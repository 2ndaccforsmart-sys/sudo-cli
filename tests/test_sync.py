"""Tests for sync module components."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from sudo.core.sync.sentinel import (
    validate_path,
    is_within_boundaries,
    get_cloud_prefix,
    PathBoundaryViolation,
    PathBlacklistViolation,
    ALLOWED_ROOTS,
    BLACKLISTED_DIRS,
    BLACKLISTED_KEYWORDS,
    HARDCODED_EXCLUSIONS,
)
from sudo.core.sync.scanner import (
    compute_file_hash,
    compute_file_hash_fast,
    filter_files,
    scan_target_files,
    detect_changes,
    FileSnapshot,
)
from sudo.core.sync.state import (
    SyncStateManager,
    _atomic_write,
    _atomic_read,
)
from sudo.core.sync.conflict import ConflictResolver
from sudo.core.sync.registry import (
    SyncTarget,
    SyncSettings,
    SyncRegistry,
    REGISTRY_FILE,
)
from sudo.core.sync.engine import (
    SyncEngine,
    SyncResult,
    TargetStatus,
)
from sudo.core.sync.gcs_client import GCSClient, GCSOfflineError, GCSCredentialsError


class TestSentinel:
    """Tests for path boundary sentinel."""

    def test_validate_allowed_path(self):
        """Test that allowed paths pass validation."""
        # Use a path within allowed roots
        result = validate_path("E:/Projects/test")
        assert result == Path("E:/Projects/test").resolve()

    def test_validate_blocked_path(self):
        """Test that blocked paths raise PathBoundaryViolation."""
        with pytest.raises(PathBoundaryViolation):
            validate_path("C:/Windows")

    def test_validate_blacklisted_dir(self):
        """Test that blacklisted directories raise PathBlacklistViolation."""
        with pytest.raises(PathBlacklistViolation):
            validate_path("D:/Daksh/Coding/someproject")

    def test_validate_blacklisted_keyword(self):
        """Test that blacklisted keywords raise PathBlacklistViolation."""
        with pytest.raises(PathBlacklistViolation):
            validate_path("E:/Projects/jarvis-app")

    def test_is_within_boundaries(self):
        """Test non-raising boundary check."""
        assert is_within_boundaries("E:/Projects/test") is True
        assert is_within_boundaries("C:/Windows") is False

    def test_get_cloud_prefix(self):
        """Test cloud prefix generation."""
        prefix = get_cloud_prefix("E:/Projects/MyApp")
        # get_cloud_prefix returns forward slashes with drive letter
        assert prefix == "E:/Projects/MyApp"

    def test_hardcoded_exclusions(self):
        """Test that hardcoded exclusions are defined."""
        assert ".git" in HARDCODED_EXCLUSIONS
        assert "__pycache__" in HARDCODED_EXCLUSIONS
        assert "node_modules" in HARDCODED_EXCLUSIONS


class TestScanner:
    """Tests for file scanner."""

    def test_compute_file_hash(self, tmp_path):
        """Test SHA-256 hash computation."""
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        hash_val = compute_file_hash(f)
        assert len(hash_val) == 64  # SHA-256 hex length

    def test_compute_file_hash_fast(self, tmp_path):
        """Test fast hash computation."""
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        hash_val = compute_file_hash_fast(f)
        assert hash_val.startswith("fast:")

    def test_filter_files_include(self, tmp_path):
        """Test include pattern filtering."""
        files = [
            tmp_path / "test.py",
            tmp_path / "test.txt",
            tmp_path / "other.js",
        ]
        for f in files:
            f.write_text("x")
        
        result = filter_files(files, include=["*.py"], exclude=[])
        assert len(result) == 1
        assert result[0].name == "test.py"

    def test_filter_files_exclude(self, tmp_path):
        """Test exclude pattern filtering."""
        files = [
            tmp_path / "test.py",
            tmp_path / "test.txt",
            tmp_path / "other.js",
        ]
        for f in files:
            f.write_text("x")
        
        result = filter_files(files, include=["*"], exclude=["*.txt"])
        assert len(result) == 2
        names = {f.name for f in result}
        assert "test.txt" not in names

    def test_scan_target_files(self, tmp_path):
        """Test scanning a target directory."""
        # Use an allowed path for testing
        import tempfile
        with tempfile.TemporaryDirectory(dir="E:/") as tmp_dir:
            tmp_path_allowed = Path(tmp_dir)
            # Create test files
            (tmp_path_allowed / "test.py").write_text("print('hello')")
            (tmp_path_allowed / "test.txt").write_text("hello")
            (tmp_path_allowed / ".hidden").write_text("secret")
            
            # Create subdirectory
            subdir = tmp_path_allowed / "subdir"
            subdir.mkdir()
            (subdir / "nested.py").write_text("nested")
            
            # Scan
            snapshots = scan_target_files(
                str(tmp_path_allowed),
                include_patterns=["**/*.py"],
                exclude_patterns=[],
                use_fast_hash=True,
            )
            
            # Should find test.py and subdir/nested.py
            assert len(snapshots) == 2
            names = {s.relative_path.replace("\\", "/") for s in snapshots}
            assert "test.py" in names
            assert "subdir/nested.py" in names

    def test_detect_changes_new_file(self, tmp_path):
        """Test detecting new files."""
        local = [
            FileSnapshot(
                relative_path="new.txt",
                absolute_path=str(tmp_path / "new.txt"),
                size_bytes=10,
                local_mtime=1234567890.0,
                local_hash="abc123",
            )
        ]
        cloud_state = {}
        prev_state = {}
        
        result = detect_changes(local, cloud_state, prev_state)
        assert len(result) == 1
        assert result[0].status == "new"

    def test_detect_changes_modified_local(self, tmp_path):
        """Test detecting locally modified files."""
        local = [
            FileSnapshot(
                relative_path="existing.txt",
                absolute_path=str(tmp_path / "existing.txt"),
                size_bytes=10,
                local_mtime=1234567890.0,
                local_hash="newhash",
            )
        ]
        cloud_state = {"existing.txt": {"md5": "cloudhash", "size": 10, "updated": "2024-01-01"}}
        prev_state = {"existing.txt": {"local_hash": "oldhash", "cloud_hash": "cloudhash"}}
        
        result = detect_changes(local, cloud_state, prev_state)
        assert len(result) == 1
        assert result[0].status == "modified"

    def test_detect_changes_conflict(self, tmp_path):
        """Test detecting conflicts."""
        local = [
            FileSnapshot(
                relative_path="conflict.txt",
                absolute_path=str(tmp_path / "conflict.txt"),
                size_bytes=10,
                local_mtime=1234567890.0,
                local_hash="localhash",
            )
        ]
        cloud_state = {"conflict.txt": {"md5": "cloudhash", "size": 10, "updated": "2024-01-01"}}
        prev_state = {"conflict.txt": {"local_hash": "oldhash", "cloud_hash": "olderhash"}}
        
        result = detect_changes(local, cloud_state, prev_state)
        assert len(result) == 1
        assert result[0].status == "conflict"

    def test_detect_changes_deleted(self, tmp_path):
        """Test detecting deleted files."""
        local = []
        cloud_state = {"deleted.txt": {"md5": "cloudhash", "size": 10, "updated": "2024-01-01"}}
        prev_state = {"deleted.txt": {"local_hash": "localhash", "cloud_hash": "cloudhash"}}
        
        result = detect_changes(local, cloud_state, prev_state)
        assert len(result) == 1
        assert result[0].status == "deleted"


class TestStateManager:
    """Tests for sync state manager."""

    def test_sync_state_manager(self, tmp_path):
        """Test state manager basic operations."""
        manager = SyncStateManager("test-target")
        # Override state dir to use temp path
        manager._state_dir = tmp_path
        manager._state_file = tmp_path / "sync_state.json"
        
        # Test load empty
        data = manager.load()
        assert data == {"target_id": "test-target", "files": {}}
        
        # Test update
        manager.update_file_state("file1.txt", "local_hash", "cloud_hash", 100, "2024-01-01")
        
        data = manager.load()
        assert "file1.txt" in data["files"]
        assert data["files"]["file1.txt"]["local_hash"] == "local_hash"
        assert data["files"]["file1.txt"]["cloud_hash"] == "cloud_hash"
        
        # Test remove
        manager.remove_file_state("file1.txt")
        data = manager.load()
        assert "file1.txt" not in data["files"]


class TestConflictResolver:
    """Tests for conflict resolver."""

    def test_force_local(self, tmp_path):
        """Test force_local policy."""
        resolver = ConflictResolver(force_local=True)
        result = resolver.resolve(
            tmp_path / "test.txt", "test.txt",
            "local_hash", "cloud_hash", 1234567890.0
        )
        assert result == "local"

    def test_force_cloud(self, tmp_path):
        """Test force_cloud policy."""
        resolver = ConflictResolver(force_cloud=True)
        result = resolver.resolve(
            tmp_path / "test.txt", "test.txt",
            "local_hash", "cloud_hash", 1234567890.0
        )
        assert result == "cloud"

    def test_newest_wins_local_newer(self, tmp_path):
        """Test newest_wins with local newer."""
        resolver = ConflictResolver(newest_wins=True)
        result = resolver.resolve(
            tmp_path / "test.txt", "test.txt",
            "local_hash", "cloud_hash", 
            1234567890.0,  # local newer
            1234567800.0   # cloud older
        )
        assert result == "local"

    def test_newest_wins_cloud_newer(self, tmp_path):
        """Test newest_wins with cloud newer."""
        resolver = ConflictResolver(newest_wins=True)
        result = resolver.resolve(
            tmp_path / "test.txt", "test.txt",
            "local_hash", "cloud_hash",
            1234567800.0,  # local older
            1234567890.0   # cloud newer
        )
        assert result == "cloud"


class TestRegistry:
    """Tests for sync registry."""

    def test_add_target(self, tmp_path):
        """Test adding a sync target."""
        import sudo.core.sync.registry as reg_module
        original_file = reg_module.REGISTRY_FILE
        reg_module.REGISTRY_FILE = tmp_path / "registry.json"
        
        registry = reg_module.SyncRegistry()
        
        target = registry.add_target(
            "E:/Projects/test_folder",
            include_patterns=["**/*.py"],
            exclude_patterns=["*.pyc"],
        )
        
        assert target.id is not None
        assert target.local_path == "E:\\Projects\\test_folder"
        assert target.include_patterns == ["**/*.py"]
        assert target.exclude_patterns == ["*.pyc"]
        
        reg_module.REGISTRY_FILE = original_file

    def test_duplicate_target_raises(self, tmp_path):
        """Test that adding duplicate path raises ValueError."""
        import sudo.core.sync.registry as reg_module
        original_file = reg_module.REGISTRY_FILE
        reg_module.REGISTRY_FILE = tmp_path / "registry.json"
        
        registry = reg_module.SyncRegistry()
        
        registry.add_target("E:/Projects/test_folder")
        
        with pytest.raises(ValueError, match="already registered"):
            registry.add_target("E:/Projects/test_folder")
        
        reg_module.REGISTRY_FILE = original_file

    def test_remove_target(self, tmp_path):
        """Test removing a sync target."""
        import sudo.core.sync.registry as reg_module
        original_file = reg_module.REGISTRY_FILE
        reg_module.REGISTRY_FILE = tmp_path / "registry.json"
        
        registry = reg_module.SyncRegistry()
        
        target = registry.add_target("E:/Projects/test_folder")
        assert registry.remove_target(target.id) is True
        assert registry.get_target(target.id) is None
        
        reg_module.REGISTRY_FILE = original_file

    def test_remove_nonexistent_target(self, tmp_path):
        """Test removing nonexistent target returns False."""
        import sudo.core.sync.registry as reg_module
        original_file = reg_module.REGISTRY_FILE
        reg_module.REGISTRY_FILE = tmp_path / "registry.json"
        
        registry = reg_module.SyncRegistry()
        assert registry.remove_target("nonexistent") is False
        
        reg_module.REGISTRY_FILE = original_file

    def test_update_target(self, tmp_path):
        """Test updating target fields."""
        import sudo.core.sync.registry as reg_module
        original_file = reg_module.REGISTRY_FILE
        reg_module.REGISTRY_FILE = tmp_path / "registry.json"
        
        registry = reg_module.SyncRegistry()
        
        target = registry.add_target("E:/Projects/test_folder")
        assert registry.update_target(target.id, enabled=False, git_backup=True) is True
        
        updated = registry.get_target(target.id)
        assert updated.enabled is False
        assert updated.git_backup is True
        
        reg_module.REGISTRY_FILE = original_file

    def test_list_targets(self, tmp_path):
        """Test listing targets."""
        import sudo.core.sync.registry as reg_module
        original_file = reg_module.REGISTRY_FILE
        reg_module.REGISTRY_FILE = tmp_path / "registry.json"
        
        registry = reg_module.SyncRegistry()
        
        t1 = registry.add_target("E:/Projects/folder1")
        t2 = registry.add_target("E:/Projects/folder2")
        
        targets = registry.list_targets()
        assert len(targets) == 2
        
        enabled = registry.list_enabled_targets()
        assert len(enabled) == 2
        
        reg_module.REGISTRY_FILE = original_file

    def test_update_settings(self, tmp_path):
        """Test updating global settings."""
        import sudo.core.sync.registry as reg_module
        original_file = reg_module.REGISTRY_FILE
        reg_module.REGISTRY_FILE = tmp_path / "registry.json"
        
        registry = reg_module.SyncRegistry()
        
        registry.update_settings(gcs_bucket="my-bucket", auto_sync=True)
        settings = registry.get_settings()
        
        assert settings.gcs_bucket == "my-bucket"
        assert settings.auto_sync is True
        
        reg_module.REGISTRY_FILE = original_file

    def test_is_configured(self, tmp_path):
        """Test is_configured check."""
        import sudo.core.sync.registry as reg_module
        original_file = reg_module.REGISTRY_FILE
        reg_module.REGISTRY_FILE = tmp_path / "registry.json"
        
        registry = reg_module.SyncRegistry()
        
        assert registry.is_configured() is False
        
        registry.update_settings(gcs_bucket="bucket", gcs_credentials_path="/path/to/creds")
        assert registry.is_configured() is True
        
        reg_module.REGISTRY_FILE = original_file


class TestEngine:
    """Tests for sync engine."""

    def test_sync_result(self):
        """Test SyncResult dataclass."""
        result = SyncResult(
            target_id="test",
            target_path="/test",
            files_uploaded=5,
            files_downloaded=3,
            files_skipped=2,
            success=True,
        )
        assert result.target_id == "test"
        assert result.files_uploaded == 5
        assert result.success is True

    def test_target_status(self):
        """Test TargetStatus dataclass."""
        status = TargetStatus(
            target_id="test",
            local_path="/test",
            enabled=True,
            last_sync="2024-01-01",
            pending_push=2,
            pending_pull=1,
        )
        assert status.target_id == "test"
        assert status.pending_push == 2


class TestGCSClient:
    """Tests for GCS client."""

    def test_gcs_offline_error(self):
        """Test GCSOfflineError."""
        with pytest.raises(GCSOfflineError):
            raise GCSOfflineError("offline")

    def test_gcs_credentials_error(self):
        """Test GCSCredentialsError."""
        with pytest.raises(GCSCredentialsError):
            raise GCSCredentialsError("bad creds")


# Note: Full integration tests for SyncEngine, GCSClient require
# actual GCS credentials and are skipped in unit tests.
# These would be run as integration tests separately.