"""File scanner — walks directories, computes hashes, detects changes.

Works with the sentinel to ensure only permitted files are scanned.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sudo.core.sync.sentinel import (
    validate_path,
    PathBoundaryViolation,
    PathBlacklistViolation,
    HARDCODED_EXCLUSIONS,
)


@dataclass
class FileSnapshot:
    """A snapshot of a single file's state."""
    relative_path: str
    absolute_path: str
    size_bytes: int
    local_mtime: float
    local_hash: str
    cloud_hash: Optional[str] = None
    cloud_mtime: Optional[float] = None
    status: str = "unchanged"  # new, modified, deleted, unchanged, conflict


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hash of a file (streaming, memory-efficient)."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
    except (OSError, PermissionError):
        return "error:unreadable"
    return h.hexdigest()


def compute_file_hash_fast(path: Path) -> str:
    """Fast hash using size + mtime + first/last 4KB.

    Good for quick scans, not for conflict detection.
    """
    try:
        stat = path.stat()
        size = stat.st_size
        mtime = stat.st_mtime
    except (OSError, PermissionError):
        return "error:stat"

    h = hashlib.md5()
    h.update(f"{size}:{mtime}".encode())

    try:
        with open(path, "rb") as f:
            # First 4KB
            first = f.read(4096)
            h.update(first)
            # Last 4KB (if file > 4KB)
            if size > 4096:
                f.seek(max(0, size - 4096))
                last = f.read(4096)
                h.update(last)
    except (OSError, PermissionError):
        pass

    return f"fast:{h.hexdigest()}"


def filter_files(
    files: list[Path],
    include: list[str],
    exclude: list[str],
) -> list[Path]:
    """Apply glob include/exclude patterns to a list of files."""
    result = []
    for f in files:
        name = f.name
        rel = str(f)

        # Check include patterns (at least one must match)
        if include and not any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) for p in include):
            # Also check if parent dirs match
            if not any(fnmatch.fnmatch(rel, p) for p in include):
                continue

        # Check exclude patterns (none should match)
        if exclude and any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p) for p in exclude):
            continue

        result.append(f)
    return result


def scan_target_files(
    target_path: str,
    include_patterns: list[str],
    exclude_patterns: list[str],
    use_fast_hash: bool = False,
) -> list[FileSnapshot]:
    """Scan a sync target and return file snapshots.

    Only files passing the sentinel and pattern filters are included.
    """
    resolved = validate_path(target_path)
    all_files: list[Path] = []
    snapshots: list[FileSnapshot] = []

    # Walk the directory
    for root, dirs, files in os.walk(resolved):
        root_path = Path(root)

        # Filter out excluded directories in-place
        dirs[:] = [
            d for d in dirs
            if d not in HARDCODED_EXCLUSIONS
            and not d.startswith(".")
            and not any(fnmatch.fnmatch(d, p) for p in exclude_patterns)
        ]

        for fname in files:
            # Skip hidden files and exclusions
            if fname.startswith("."):
                continue
            if fname in HARDCODED_EXCLUSIONS:
                continue
            if any(fnmatch.fnmatch(fname, p) for p in exclude_patterns):
                continue

            fpath = root_path / fname

            # Run sentinel on each file
            try:
                validate_path(str(fpath))
            except (PathBoundaryViolation, PathBlacklistViolation):
                continue

            all_files.append(fpath)

    # Apply include/exclude filters
    filtered = filter_files(all_files, include_patterns, exclude_patterns)

    # Build snapshots
    for fpath in filtered:
        try:
            stat = fpath.stat()
        except (OSError, PermissionError):
            continue

        rel = str(fpath.relative_to(resolved))
        hasher = compute_file_hash_fast if use_fast_hash else compute_file_hash

        snapshot = FileSnapshot(
            relative_path=rel,
            absolute_path=str(fpath),
            size_bytes=stat.st_size,
            local_mtime=stat.st_mtime,
            local_hash=hasher(fpath),
            status="new",
        )
        snapshots.append(snapshot)

    return snapshots


def detect_changes(
    local_files: list[FileSnapshot],
    cloud_state: dict[str, dict],
    prev_state: dict[str, dict],
) -> list[FileSnapshot]:
    """Compare local files against cloud state to determine sync status.

    Args:
        local_files: Current local file snapshots.
        cloud_state: Dict of cloud file paths -> {md5, size, updated}.
        prev_state: Dict of previously synced file paths -> {local_hash, cloud_hash}.

    Returns:
        Updated FileSnapshot list with status field set.
    """
    local_map = {f.relative_path: f for f in local_files}
    result = []

    # Check each local file against previous and cloud state
    for snap in local_files:
        rel = snap.relative_path
        prev = prev_state.get(rel)
        cloud = cloud_state.get(rel)

        if cloud is None:
            # File exists locally but not in cloud → new
            snap.status = "new"
        elif prev is None:
            # Never synced before → new
            snap.status = "new"
        else:
            local_changed = snap.local_hash != prev.get("local_hash", "")
            cloud_changed = cloud.get("md5", "") != prev.get("cloud_hash", "")

            if local_changed and cloud_changed:
                snap.status = "conflict"
                snap.cloud_hash = cloud.get("md5")
                snap.cloud_mtime = cloud.get("updated")
            elif local_changed:
                snap.status = "modified"
            elif cloud_changed:
                snap.status = "remote_modified"
                snap.cloud_hash = cloud.get("md5")
                snap.cloud_mtime = cloud.get("updated")
            else:
                snap.status = "unchanged"

        result.append(snap)

    # Check for files in cloud but not locally (deleted locally)
    for rel_path, cloud_info in cloud_state.items():
        if rel_path not in local_map:
            prev = prev_state.get(rel_path)
            if prev:
                # Was synced before, now gone locally → deleted
                snap = FileSnapshot(
                    relative_path=rel_path,
                    absolute_path="",
                    size_bytes=0,
                    local_mtime=0,
                    local_hash="",
                    cloud_hash=cloud_info.get("md5", ""),
                    status="deleted",
                )
                result.append(snap)

    return result
