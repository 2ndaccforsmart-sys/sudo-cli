"""Sync engine — orchestrates push, pull, and bidirectional sync.

Coordinates the sentinel, scanner, GCS client, conflict resolver,
and Git backup to perform complete sync operations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sudo.core.sync.sentinel import validate_path, PathBoundaryViolation, PathBlacklistViolation
from sudo.core.sync.scanner import scan_target_files, detect_changes, FileSnapshot
from sudo.core.sync.state import SyncStateManager
from sudo.core.sync.registry import SyncTarget, SyncRegistry
from sudo.core.sync.conflict import ConflictResolver
from sudo.core.sync.gcs_client import GCSClient, GCSOfflineError


@dataclass
class SyncResult:
    """Result of a sync operation for a single target."""
    target_id: str
    target_path: str
    files_uploaded: int = 0
    files_downloaded: int = 0
    files_skipped: int = 0
    files_deleted: int = 0
    local_files: int = 0
    conflicts: int = 0
    errors: list[str] = field(default_factory=list)
    git_committed: bool = False
    git_pushed: bool = False
    git_commit_hash: Optional[str] = None
    duration_seconds: float = 0
    success: bool = True
    aborted: bool = False


@dataclass
class TargetStatus:
    """Status summary for a sync target."""
    target_id: str
    local_path: str
    enabled: bool
    last_sync: Optional[str]
    pending_push: int = 0
    pending_pull: int = 0
    pending_conflicts: int = 0
    cloud_files: int = 0
    local_files: int = 0
    git_backup_enabled: bool = False
    git_branch: Optional[str] = None


class SyncEngine:
    """Orchestrates sync operations across all registered targets."""

    def __init__(self, registry: SyncRegistry, gcs: GCSClient):
        self.registry = registry
        self.gcs = gcs

    def push_target(
        self,
        target_id: str,
        force: bool = False,
        quiet: bool = False,
    ) -> SyncResult:
        """Push all changes for a single target to GCS."""
        target = self.registry.get_target(target_id)
        if target is None:
            return SyncResult(
                target_id=target_id,
                target_path="",
                success=False,
                errors=[f"Target not found: {target_id}"],
            )

        return self._sync_single(target, mode="push", force=force, quiet=quiet)

    def pull_target(
        self,
        target_id: str,
        force: bool = False,
        quiet: bool = False,
    ) -> SyncResult:
        """Pull all changes for a single target from GCS."""
        target = self.registry.get_target(target_id)
        if target is None:
            return SyncResult(
                target_id=target_id,
                target_path="",
                success=False,
                errors=[f"Target not found: {target_id}"],
            )

        return self._sync_single(target, mode="pull", force=force, quiet=quiet)

    def sync_target(
        self,
        target_id: str,
        force_local: bool = False,
        force_cloud: bool = False,
        quiet: bool = False,
    ) -> SyncResult:
        """Bidirectional sync for a single target."""
        target = self.registry.get_target(target_id)
        if target is None:
            return SyncResult(
                target_id=target_id,
                target_path="",
                success=False,
                errors=[f"Target not found: {target_id}"],
            )

        return self._sync_single(
            target, mode="sync",
            force_local=force_local,
            force_cloud=force_cloud,
            quiet=quiet,
        )

    def push_all(self, force: bool = False, quiet: bool = False) -> list[SyncResult]:
        """Push all enabled targets."""
        results = []
        for target in self.registry.list_enabled_targets():
            results.append(self.push_target(target.id, force=force, quiet=quiet))
        return results

    def pull_all(self, force: bool = False, quiet: bool = False) -> list[SyncResult]:
        """Pull all enabled targets."""
        results = []
        for target in self.registry.list_enabled_targets():
            results.append(self.pull_target(target.id, force=force, quiet=quiet))
        return results

    def sync_all(self, quiet: bool = False, **kwargs) -> list[SyncResult]:
        """Sync all enabled targets."""
        results = []
        for target in self.registry.list_enabled_targets():
            results.append(self.sync_target(target.id, quiet=quiet, **kwargs))
        return results

    def status(self, target_id: Optional[str] = None) -> list[TargetStatus]:
        """Get sync status for one or all targets."""
        targets = []
        if target_id:
            t = self.registry.get_target(target_id)
            if t:
                targets = [t]
        else:
            targets = self.registry.list_targets()

        results = []
        for target in targets:
            state = SyncStateManager(target.id)
            prev_state = state.load().get("files", {})

            # Scan local files
            try:
                local_files = scan_target_files(
                    target.local_path,
                    target.include_patterns,
                    target.exclude_patterns,
                    use_fast_hash=True,
                )
            except (PathBoundaryViolation, PathBlacklistViolation):
                local_files = []

            # List cloud files
            cloud_files = self.gcs.list_files(target.cloud_prefix)

            # Detect pending changes
            pending_push = 0
            pending_pull = 0
            pending_conflicts = 0

            cloud_map = {f["name"].replace(target.cloud_prefix + "/", ""): f for f in cloud_files}

            for snap in local_files:
                cloud = cloud_map.get(snap.relative_path)
                prev = prev_state.get(snap.relative_path)

                if cloud is None:
                    pending_push += 1
                elif prev:
                    local_changed = snap.local_hash != prev.get("local_hash", "")
                    cloud_changed = cloud.get("md5", "") != prev.get("cloud_hash", "")
                    if local_changed and cloud_changed:
                        pending_conflicts += 1
                    elif local_changed:
                        pending_push += 1
                    elif cloud_changed:
                        pending_pull += 1

            # Check for cloud-only files
            local_map = {s.relative_path: s for s in local_files}
            for rel_path in cloud_map:
                if rel_path not in local_map and rel_path in prev_state:
                    pending_pull += 1

            results.append(TargetStatus(
                target_id=target.id,
                local_path=target.local_path,
                enabled=target.enabled,
                last_sync=target.last_sync,
                pending_push=pending_push,
                pending_pull=pending_pull,
                pending_conflicts=pending_conflicts,
                cloud_files=len(cloud_files),
                local_files=len(local_files),
                git_backup_enabled=target.git_backup,
            ))

        return results

    def _sync_single(
        self,
        target: SyncTarget,
        mode: str = "sync",
        force: bool = False,
        force_local: bool = False,
        force_cloud: bool = False,
        quiet: bool = False,
    ) -> SyncResult:
        """Core sync logic for a single target."""
        start_time = time.time()
        result = SyncResult(
            target_id=target.id,
            target_path=target.local_path,
        )

        # Validate path
        try:
            resolved = validate_path(target.local_path)
        except (PathBoundaryViolation, PathBlacklistViolation) as e:
            result.success = False
            result.errors.append(str(e))
            return result

        # Check offline
        if not self.gcs.is_online():
            result.success = False
            result.errors.append("Offline — sync deferred")
            return result

        # Git backup (before push/sync, not pull)
        if target.git_backup and mode in ("push", "sync"):
            if not quiet:
                print(f"  \033[36mBacking up Git...\033[0m")
            git_result = self._run_git_backup(target, quiet=quiet)
            result.git_committed = git_result.get("committed", False)
            result.git_pushed = git_result.get("pushed", False)
            result.git_commit_hash = git_result.get("commit_hash")

        # Scan local files
        if not quiet:
            print(f"  Scanning {target.local_path}...")

        try:
            local_files = scan_target_files(
                target.local_path,
                target.include_patterns,
                target.exclude_patterns,
                use_fast_hash=(mode != "sync"),
            )
        except (PathBoundaryViolation, PathBlacklistViolation) as e:
            result.success = False
            result.errors.append(str(e))
            return result

        result.local_files = len(local_files)

        # Load state
        state = SyncStateManager(target.id)
        prev_data = state.load()
        prev_state = prev_data.get("files", {})

        # List cloud files
        cloud_files = self.gcs.list_files(target.cloud_prefix)
        cloud_map = {}
        for cf in cloud_files:
            rel = cf["name"].replace(target.cloud_prefix + "/", "")
            cloud_map[rel] = cf

        # Detect changes
        snapshots = detect_changes(local_files, cloud_map, prev_state)

        # Build resolver
        resolver = ConflictResolver(
            force_local=force_local or force,
            force_cloud=force_cloud or force,
        )

        now_str = datetime.now(timezone.utc).isoformat()

        # Process each file
        for snap in snapshots:
            if snap.status == "unchanged":
                result.files_skipped += 1
                continue

            if snap.status == "new" or snap.status == "modified":
                if mode in ("push", "sync"):
                    success = self._upload_file(snap, target, quiet=quiet)
                    if success:
                        result.files_uploaded += 1
                        state.update_file_state(
                            snap.relative_path,
                            snap.local_hash,
                            snap.local_hash,
                            snap.size_bytes,
                            now_str,
                        )
                    else:
                        result.errors.append(f"Failed to upload: {snap.relative_path}")
                else:
                    result.files_skipped += 1

            elif snap.status == "remote_modified":
                if mode in ("pull", "sync"):
                    success = self._download_file(snap, target, quiet=quiet)
                    if success:
                        result.files_downloaded += 1
                        new_hash = self.gcs.compute_local_md5(Path(snap.absolute_path))
                        state.update_file_state(
                            snap.relative_path,
                            new_hash,
                            snap.cloud_hash or "",
                            snap.size_bytes,
                            now_str,
                        )
                    else:
                        result.errors.append(f"Failed to download: {snap.relative_path}")
                else:
                    result.files_skipped += 1

            elif snap.status == "conflict":
                result.conflicts += 1
                if mode == "sync":
                    cloud_content = self._read_cloud_file(snap, target)
                    decision = resolver.resolve(
                        Path(snap.absolute_path),
                        snap.relative_path,
                        snap.local_hash,
                        snap.cloud_hash or "",
                        snap.local_mtime,
                    )

                    if decision == "abort":
                        result.aborted = True
                        break
                    elif decision == "local":
                        success = self._upload_file(snap, target, quiet=quiet)
                        if success:
                            result.files_uploaded += 1
                            state.update_file_state(
                                snap.relative_path,
                                snap.local_hash,
                                snap.local_hash,
                                snap.size_bytes,
                                now_str,
                            )
                    elif decision == "cloud":
                        success = self._download_file(snap, target, quiet=quiet)
                        if success:
                            result.files_downloaded += 1
                            new_hash = self.gcs.compute_local_md5(Path(snap.absolute_path))
                            state.update_file_state(
                                snap.relative_path,
                                new_hash,
                                snap.cloud_hash or "",
                                snap.size_bytes,
                                now_str,
                            )
                    # skip → do nothing
                else:
                    result.files_skipped += 1

            elif snap.status == "deleted":
                if mode in ("sync", "pull"):
                    # Re-download if cloud still has it
                    if cloud_map.get(snap.relative_path):
                        success = self._download_file(snap, target, quiet=quiet)
                        if success:
                            result.files_downloaded += 1
                else:
                    result.files_skipped += 1

        # Update target last_sync
        if not result.aborted:
            self.registry.update_target(target.id, last_sync=now_str)

        result.duration_seconds = time.time() - start_time
        return result

    def _upload_file(self, snap: FileSnapshot, target: SyncTarget, quiet: bool = False) -> bool:
        """Upload a single file to GCS."""
        if not quiet:
            size_kb = snap.size_bytes / 1024
            print(f"  \033[32m[push]\033[0m {snap.relative_path:<40} {size_kb:.1f} KB")

        return self.gcs.upload_file(
            Path(snap.absolute_path),
            target.cloud_prefix,
            metadata={"local_hash": snap.local_hash},
        )

    def _download_file(self, snap: FileSnapshot, target: SyncTarget, quiet: bool = False) -> bool:
        """Download a single file from GCS."""
        cloud_path = f"{target.cloud_prefix}/{snap.relative_path}"
        local_path = Path(snap.absolute_path)

        if not quiet:
            print(f"  \033[34m[pull]\033[0m {snap.relative_path}")

        return self.gcs.download_file(cloud_path, local_path)

    def _read_cloud_file(self, snap: FileSnapshot, target: SyncTarget) -> Optional[str]:
        """Read cloud file content for diff display."""
        cloud_path = f"{target.cloud_prefix}/{snap.relative_path}"
        return self.gcs.read_file_text(cloud_path)

    def _run_git_backup(self, target: SyncTarget, quiet: bool = False) -> dict:
        """Run Git backup for a target."""
        from sudo.core.sync.git_backup import GitBackup

        backup = GitBackup(target.local_path)
        if not backup.is_git_repo():
            return {"success": False, "error": "Not a Git repo"}

        # Scan which files to stage
        try:
            local_files = scan_target_files(
                target.local_path,
                target.include_patterns,
                target.exclude_patterns,
                use_fast_hash=True,
            )
            file_paths = [Path(f.absolute_path) for f in local_files if f.status in ("new", "modified")]
        except Exception:
            file_paths = []

        result = backup.run_full_backup(file_paths)

        if not quiet:
            if result.get("committed"):
                print(f"  \033[32mGit commit:\033[0m {result.get('commit_hash', '?')}")
            if result.get("pushed"):
                print(f"  \033[32mGit push:\033[0m  OK")
            elif result.get("error"):
                print(f"  \033[33mGit warning:\033[0m {result['error']}")

        return result
