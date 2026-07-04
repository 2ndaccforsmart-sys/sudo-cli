"""Sync target registry — persistent storage for sync configurations.

Stores which folders/files the user has registered for syncing,
along with their preferences (include/exclude patterns, git backup, etc.).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from sudo.core.sync.state import _atomic_write, _atomic_read
from sudo.core.sync.sentinel import validate_path, get_cloud_prefix


REGISTRY_DIR = Path.home() / ".config" / "sudo" / "sync"
REGISTRY_FILE = REGISTRY_DIR / "registry.json"


@dataclass
class SyncTarget:
    """A registered sync target (folder or file)."""
    id: str
    local_path: str
    cloud_prefix: str
    enabled: bool = True
    git_backup: bool = False
    include_patterns: list[str] = field(default_factory=lambda: ["**/*"])
    exclude_patterns: list[str] = field(default_factory=list)
    last_sync: Optional[str] = None
    created: str = ""
    file_count: int = 0
    total_size_bytes: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SyncTarget:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SyncSettings:
    """Global sync settings."""
    gcs_bucket: Optional[str] = None
    gcs_credentials_path: Optional[str] = None
    enabled: bool = False
    auto_sync: bool = False
    max_concurrent_uploads: int = 4
    hash_algorithm: str = "sha256"
    user_blacklist: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SyncSettings:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class SyncRegistry:
    """Manages sync targets and global settings with atomic persistence."""

    def __init__(self):
        self._settings = SyncSettings()
        self._targets: list[SyncTarget] = []

    def load(self) -> None:
        """Load registry from disk."""
        raw = _atomic_read(REGISTRY_FILE)
        if raw is None:
            self._settings = SyncSettings()
            self._targets = []
            return
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._settings = SyncSettings()
            self._targets = []
            return

        self._settings = SyncSettings.from_dict(data.get("settings", {}))
        self._targets = [
            SyncTarget.from_dict(t) for t in data.get("targets", [])
        ]

    def save(self) -> None:
        """Save registry atomically."""
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "settings": self._settings.to_dict(),
            "targets": [t.to_dict() for t in self._targets],
        }
        _atomic_write(REGISTRY_FILE, json.dumps(data, indent=2))

    def add_target(
        self,
        local_path: str,
        include_patterns: Optional[list[str]] = None,
        exclude_patterns: Optional[list[str]] = None,
        git_backup: bool = False,
    ) -> SyncTarget:
        """Add a new sync target after validation."""
        # Validate against sentinel
        resolved = validate_path(local_path)
        path_str = str(resolved)

        # Check if already registered
        for t in self._targets:
            if os.path.normcase(os.path.normpath(t.local_path)) == os.path.normcase(os.path.normpath(path_str)):
                raise ValueError(f"Path already registered: {path_str} (ID: {t.id})")

        # Generate target
        target_id = uuid.uuid4().hex[:8]
        cloud_prefix = get_cloud_prefix(path_str)

        target = SyncTarget(
            id=target_id,
            local_path=path_str,
            cloud_prefix=cloud_prefix,
            enabled=True,
            git_backup=git_backup,
            include_patterns=include_patterns or ["**/*"],
            exclude_patterns=exclude_patterns or [],
            created=_now_iso(),
        )

        self._targets.append(target)
        self.save()
        return target

    def remove_target(self, target_id: str) -> bool:
        """Remove a sync target by ID."""
        for i, t in enumerate(self._targets):
            if t.id == target_id:
                self._targets.pop(i)
                self.save()
                return True
        return False

    def get_target(self, target_id: str) -> Optional[SyncTarget]:
        """Get a target by ID."""
        for t in self._targets:
            if t.id == target_id:
                return t
        return None

    def get_target_by_path(self, path: str) -> Optional[SyncTarget]:
        """Get a target by local path."""
        norm = os.path.normcase(os.path.normpath(path))
        for t in self._targets:
            if os.path.normcase(os.path.normpath(t.local_path)) == norm:
                return t
        return None

    def list_targets(self) -> list[SyncTarget]:
        """List all registered targets."""
        return list(self._targets)

    def list_enabled_targets(self) -> list[SyncTarget]:
        """List only enabled targets."""
        return [t for t in self._targets if t.enabled]

    def update_target(self, target_id: str, **kwargs) -> bool:
        """Update target fields."""
        target = self.get_target(target_id)
        if target is None:
            return False
        for key, value in kwargs.items():
            if hasattr(target, key):
                setattr(target, key, value)
        self.save()
        return True

    def get_settings(self) -> SyncSettings:
        """Get global sync settings."""
        return self._settings

    def update_settings(self, **kwargs) -> None:
        """Update global settings."""
        for key, value in kwargs.items():
            if hasattr(self._settings, key):
                setattr(self._settings, key, value)
        self.save()

    def is_configured(self) -> bool:
        """Check if sync has been configured (bucket + credentials set)."""
        return bool(self._settings.gcs_bucket and self._settings.gcs_credentials_path)

    def add_user_blacklist(self, path: str) -> None:
        """Add a user-defined path to the blacklist."""
        resolved = validate_path(path)
        path_str = str(resolved)
        if path_str not in self._settings.user_blacklist:
            self._settings.user_blacklist.append(path_str)
            self.save()

    def remove_user_blacklist(self, path: str) -> bool:
        """Remove a path from the user blacklist."""
        norm = os.path.normcase(os.path.normpath(path))
        for i, bl in enumerate(self._settings.user_blacklist):
            if os.path.normcase(os.path.normpath(bl)) == norm:
                self._settings.user_blacklist.pop(i)
                self.save()
                return True
        return False


def _now_iso() -> str:
    """Get current time as ISO string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
