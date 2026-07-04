"""Automated Git backup hook for sync targets.

Before syncing, if Git backup is enabled for a target:
1. Detect if the target is a Git repository.
2. Stage modified files that are in the sync selection.
3. Create a timestamped commit.
4. Push to the active remote branch.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class GitBackup:
    """Manages automated Git commits and pushes for sync targets."""

    def __init__(self, target_path: str):
        self.target_path = Path(target_path)
        self._git_root: Optional[Path] = None

    def _run(self, *args, cwd: Optional[Path] = None, timeout: int = 30) -> subprocess.CompletedProcess:
        """Run a git command safely."""
        return subprocess.run(
            ["git"] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd or self.target_path),
        )

    def is_git_repo(self) -> bool:
        """Check if the target directory is a Git repository."""
        try:
            r = self._run("rev-parse", "--git-dir")
            if r.returncode == 0:
                git_dir = Path(r.stdout.strip())
                if not git_dir.is_absolute():
                    git_dir = self.target_path / git_dir
                self._git_root = git_dir.parent
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return False

    def get_current_branch(self) -> Optional[str]:
        """Get the name of the current branch."""
        try:
            r = self._run("branch", "--show-current")
            if r.returncode == 0:
                return r.stdout.strip() or None
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return None

    def get_remote_url(self) -> Optional[str]:
        """Get the origin remote URL."""
        try:
            r = self._run("remote", "get-url", "origin")
            if r.returncode == 0:
                return r.stdout.strip() or None
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return None

    def has_changes(self) -> bool:
        """Check if there are uncommitted changes in the working tree."""
        try:
            r = self._run("status", "--porcelain")
            if r.returncode == 0:
                return bool(r.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return False

    def stage_files(self, file_patterns: list[str]) -> int:
        """Stage files matching patterns. Returns count of staged files."""
        count = 0
        for pattern in file_patterns:
            try:
                r = self._run("add", pattern)
                if r.returncode == 0:
                    # Count what was staged
                    r2 = self._run("diff", "--cached", "--name-only")
                    if r2.returncode == 0:
                        count = len(r2.stdout.strip().splitlines()) if r2.stdout.strip() else 0
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                continue
        return count

    def stage_paths(self, paths: list[Path]) -> int:
        """Stage specific file paths. Returns count of staged files."""
        if not paths:
            return 0

        str_paths = [str(p) for p in paths]
        try:
            r = self._run("add", *str_paths)
            if r.returncode == 0:
                r2 = self._run("diff", "--cached", "--name-only")
                if r2.returncode == 0:
                    return len(r2.stdout.strip().splitlines()) if r2.stdout.strip() else 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return 0

    def create_backup_commit(self, message: Optional[str] = None) -> Optional[str]:
        """Create a timestamped commit. Returns commit hash or None."""
        if not self.has_changes():
            return None

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if message is None:
            message = f"sync: auto-backup {now}\n\nAutomated backup by sudo sync system."

        try:
            r = self._run("commit", "-m", message)
            if r.returncode == 0:
                # Get the commit hash
                r2 = self._run("rev-parse", "HEAD")
                if r2.returncode == 0:
                    return r2.stdout.strip()[:8]
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return None

    def push_to_remote(self, branch: Optional[str] = None) -> bool:
        """Push the current branch to its tracking remote."""
        if branch is None:
            branch = self.get_current_branch()
        if not branch:
            return False

        try:
            r = self._run("push", "origin", branch, timeout=60)
            return r.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def run_full_backup(self, file_paths: list[Path]) -> dict:
        """Execute full backup flow: stage → commit → push.

        Returns dict with:
            success: bool
            committed: bool
            pushed: bool
            commit_hash: Optional[str]
            files_staged: int
            error: Optional[str]
        """
        result = {
            "success": False,
            "committed": False,
            "pushed": False,
            "commit_hash": None,
            "files_staged": 0,
            "error": None,
        }

        if not self.is_git_repo():
            result["error"] = "Not a Git repository"
            return result

        # Stage files
        staged = self.stage_paths(file_paths)
        result["files_staged"] = staged

        if staged == 0 and not self.has_changes():
            result["success"] = True
            return result

        # Commit
        commit_hash = self.create_backup_commit()
        if commit_hash:
            result["committed"] = True
            result["commit_hash"] = commit_hash

        # Push (best effort — don't fail sync if push fails)
        remote = self.get_remote_url()
        if remote:
            pushed = self.push_to_remote()
            result["pushed"] = pushed
            if not pushed:
                result["error"] = "Commit created but push failed"

        result["success"] = True
        return result
