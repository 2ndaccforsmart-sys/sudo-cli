"""sudo.core.sync — Global Zero-Trust Cloud Sync system.

Modules:
    sentinel   — path boundary enforcement (first gate)
    state      — atomic local state persistence
    registry   — sync target registry
    scanner    — file scanning and hashing
    gcs_client — GCS API wrapper
    conflict   — interactive conflict resolution
    git_backup — automated Git commit/push
    engine     — sync orchestration
"""

from sudo.core.sync.sentinel import validate_path
from sudo.core.sync.scanner import FileSnapshot, detect_changes
from sudo.core.sync.registry import SyncTarget, SyncRegistry
from sudo.core.sync.state import SyncStateManager
from sudo.core.sync.gcs_client import GCSClient
from sudo.core.sync.conflict import ConflictResolver
from sudo.core.sync.git_backup import GitBackup
from sudo.core.sync.engine import SyncEngine

__all__ = [
    "validate_path",
    "FileSnapshot",
    "detect_changes",
    "SyncTarget",
    "SyncRegistry",
    "SyncStateManager",
    "GCSClient",
    "ConflictResolver",
    "GitBackup",
    "SyncEngine",
]
