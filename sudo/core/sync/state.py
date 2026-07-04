"""Atomic state persistence for sync operations.

All state files are written atomically to prevent corruption
from interrupted processes or power loss.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from sudo.core.config import STATE_DIR_BASE


SYNC_STATE_DIR = STATE_DIR_BASE


def _atomic_write(path: Path, data: str) -> None:
    """Write data to a file atomically using temp file + rename.

    This ensures that if the process is killed mid-write,
    the original file remains intact.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp",
            dir=str(path.parent),
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        fd = None  # Already closed by os.fdopen
        # Atomic rename (works on NTFS and POSIX)
        os.replace(tmp_path, str(path))
        tmp_path = None
    except Exception:
        # Clean up on failure
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def _atomic_read(path: Path) -> Optional[str]:
    """Read a file atomically. Returns None if file doesn't exist."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return None


class SyncStateManager:
    """Manages per-target sync state (file hashes, timestamps)."""

    def __init__(self, target_id: str):
        self.target_id = target_id
        self._state_dir = SYNC_STATE_DIR / target_id
        self._state_file = self._state_dir / "sync_state.json"
        self._data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        """Load sync state from disk."""
        raw = _atomic_read(self._state_file)
        if raw is None:
            self._data = {"target_id": self.target_id, "files": {}}
            return self._data
        try:
            self._data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._data = {"target_id": self.target_id, "files": {}}
        return self._data

    def save(self, data: Optional[dict[str, Any]] = None) -> None:
        """Save sync state atomically."""
        if data is not None:
            self._data = data
        self._state_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(self._state_file, json.dumps(self._data, indent=2))

    def get_file_state(self, relative_path: str) -> Optional[dict]:
        """Get last-synced state for a specific file."""
        if not self._data:
            self.load()
        return self._data.get("files", {}).get(relative_path)

    def update_file_state(
        self,
        relative_path: str,
        local_hash: str,
        cloud_hash: str,
        size_bytes: int = 0,
        synced_at: str = "",
    ) -> None:
        """Update state for a successfully synced file."""
        if not self._data:
            self.load()
        files = self._data.setdefault("files", {})
        files[relative_path] = {
            "local_hash": local_hash,
            "cloud_hash": cloud_hash,
            "size_bytes": size_bytes,
            "synced_at": synced_at,
        }
        self._data["last_full_sync"] = synced_at
        self.save()

    def remove_file_state(self, relative_path: str) -> None:
        """Remove state for a deleted file."""
        if not self._data:
            self.load()
        files = self._data.get("files", {})
        files.pop(relative_path, None)
        self.save()

    def get_all_files(self) -> dict[str, dict]:
        """Get all tracked files and their states."""
        if not self._data:
            self.load()
        return self._data.get("files", {})

    def clear(self) -> None:
        """Clear all state for this target."""
        self._data = {"target_id": self.target_id, "files": {}}
        self.save()

    @property
    def last_sync(self) -> Optional[str]:
        """Get timestamp of last full sync."""
        if not self._data:
            self.load()
        return self._data.get("last_full_sync")
