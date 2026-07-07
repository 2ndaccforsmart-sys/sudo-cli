"""Tests for session state — lazy hash caching and dirty tracking."""

import json
import time
from pathlib import Path

from sudo.core.session import SessionManager, _PROJECT_HASH_CACHE, _project_hash


class TestProjectHash:
    def setup_method(self):
        _PROJECT_HASH_CACHE.clear()

    def test_hash_is_cached(self):
        h1 = _project_hash("/tmp")
        h2 = _project_hash("/tmp")
        assert h1 == h2
        assert "/tmp" in _PROJECT_HASH_CACHE

    def test_different_paths_different_hashes(self):
        h1 = _project_hash("/tmp")
        h2 = _project_hash("/var")
        assert h1 != h2

    def test_hash_is_16_chars(self):
        h = _project_hash("/tmp")
        assert len(h) == 16


class TestSessionManagerDirtyTracking:
    def test_no_save_on_property_set(self, tmp_path):
        sm = SessionManager()
        sm.state_dir = tmp_path
        sm.session_file = tmp_path / "session.json"

        # Set properties — should NOT write to disk
        sm.plan = "test plan"
        sm.last_command = "echo hello"
        sm.conversation_summary = "summary"
        sm.undo_stack = [{"action": "write"}]
        sm.memory = {"key": "val"}

        # File should NOT exist yet (dirty but not saved)
        assert not sm.session_file.exists()
        assert sm._dirty is True

    def test_flush_writes_to_disk(self, tmp_path):
        sm = SessionManager()
        sm.state_dir = tmp_path
        sm.session_file = tmp_path / "session.json"

        sm.plan = "test plan"
        assert sm._dirty is True
        assert not sm.session_file.exists()

        sm.flush()
        assert sm._dirty is False
        assert sm.session_file.exists()
        data = json.loads(sm.session_file.read_text())
        assert data["plan"] == "test plan"

    def test_flush_noop_when_clean(self, tmp_path):
        sm = SessionManager()
        sm.state_dir = tmp_path
        sm.session_file = tmp_path / "session.json"
        sm._dirty = False

        sm.flush()  # Should not write
        assert not sm.session_file.exists()

    def test_save_clears_dirty(self, tmp_path):
        sm = SessionManager()
        sm.state_dir = tmp_path
        sm.session_file = tmp_path / "session.json"

        sm.plan = "test"
        assert sm._dirty is True

        sm.save({"plan": "saved"})
        assert sm._dirty is False

    def test_load_clears_dirty(self, tmp_path):
        sm = SessionManager()
        sm.state_dir = tmp_path
        sm.session_file = tmp_path / "session.json"
        sm.session_file.write_text(json.dumps({"plan": "loaded"}))

        sm._dirty = True
        sm.load()
        assert sm._dirty is False
        assert sm.plan == "loaded"

    def test_property_roundtrip_after_flush(self, tmp_path):
        sm = SessionManager()
        sm.state_dir = tmp_path
        sm.session_file = tmp_path / "session.json"

        sm.plan = "my plan"
        sm.flush()

        sm2 = SessionManager()
        sm2.state_dir = tmp_path
        sm2.session_file = tmp_path / "session.json"
        sm2.load()
        assert sm2.plan == "my plan"
