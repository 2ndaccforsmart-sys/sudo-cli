"""Session state persistence for sudo CLI.

Saves/loads session JSON per-project-hash under ~/.config/sudo/state/.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from sudo.core.config import STATE_DIR_BASE


# Cache project hash to avoid repeated subprocess calls
_PROJECT_HASH_CACHE: dict[Optional[str], str] = {}


def _project_hash(path: Optional[str] = None) -> str:
    """Compute a short hash for project identity (git remote URL or cwd).
    
    Results are cached per path to avoid repeated subprocess calls.
    """
    if path in _PROJECT_HASH_CACHE:
        return _PROJECT_HASH_CACHE[path]

    target = path or os.getcwd()
    try:
        import subprocess
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3, cwd=target,
        )
        if r.returncode == 0:
            git_root = r.stdout.strip()
            rem = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=3, cwd=git_root,
            )
            if rem.returncode == 0:
                identifier = rem.stdout.strip()
            else:
                identifier = git_root
        else:
            identifier = os.path.abspath(target)
    except Exception:
        identifier = os.path.abspath(target)
    
    h = hashlib.sha256(identifier.encode()).hexdigest()[:16]
    _PROJECT_HASH_CACHE[path] = h
    return h


def _state_dir(path: Optional[str] = None) -> Path:
    h = _project_hash(path)
    d = STATE_DIR_BASE / h
    d.mkdir(parents=True, exist_ok=True)
    return d


class SessionManager:
    """Manages per-project session state persisted as JSON."""

    def __init__(self, path: Optional[str] = None):
        self.state_dir = _state_dir(path)
        self.session_file = self.state_dir / "session.json"
        self._data: dict[str, Any] = {}
        self._dirty: bool = False  # Track if unsaved changes exist

    def load(self) -> dict[str, Any]:
        try:
            if self.session_file.exists():
                with open(self.session_file) as f:
                    self._data = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._data = {}
        self._dirty = False
        return self._data

    def save(self, data: Optional[dict[str, Any]] = None) -> None:
        if data is not None:
            self._data = data
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.session_file, "w") as f:
            json.dump(self._data, f, indent=2)
        self._dirty = False

    def mark_dirty(self) -> None:
        """Mark that data has changed and needs saving."""
        self._dirty = True

    def flush(self) -> None:
        """Save only if there are pending changes."""
        if self._dirty:
            self.save()

    @property
    def plan(self) -> Optional[str]:
        return self._data.get("plan")

    @plan.setter
    def plan(self, value: Optional[str]) -> None:
        self._data["plan"] = value
        self.mark_dirty()

    @property
    def last_command(self) -> Optional[str]:
        return self._data.get("last_command")

    @last_command.setter
    def last_command(self, value: Optional[str]) -> None:
        self._data["last_command"] = value
        self.mark_dirty()

    @property
    def conversation_summary(self) -> Optional[str]:
        return self._data.get("conversation_summary")

    @conversation_summary.setter
    def conversation_summary(self, value: Optional[str]) -> None:
        self._data["conversation_summary"] = value
        self.mark_dirty()

    @property
    def undo_stack(self) -> list[dict]:
        return self._data.get("undo_stack", [])

    @undo_stack.setter
    def undo_stack(self, value: list[dict]) -> None:
        self._data["undo_stack"] = value
        self.mark_dirty()

    @property
    def memory(self) -> dict[str, Any]:
        return self._data.get("memory", {})

    @memory.setter
    def memory(self, value: dict[str, Any]) -> None:
        self._data["memory"] = value
        self.mark_dirty()

    def clear(self) -> None:
        self._data = {}
        self.save()
