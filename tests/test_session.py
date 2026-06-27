"""Tests for session state persistence."""

import json
from pathlib import Path

from sudo.core.session import SessionManager


def test_session_load_empty(tmp_path):
    sm = SessionManager()
    sm.state_dir = tmp_path
    sm.session_file = tmp_path / "session.json"
    data = sm.load()
    assert data == {}


def test_session_save_and_load(tmp_path):
    sm = SessionManager()
    sm.state_dir = tmp_path
    sm.session_file = tmp_path / "session.json"
    sm.save({"key": "value"})
    assert sm.session_file.exists()
    data = json.loads(sm.session_file.read_text())
    assert data == {"key": "value"}

    sm2 = SessionManager()
    sm2.state_dir = tmp_path
    sm2.session_file = tmp_path / "session.json"
    loaded = sm2.load()
    assert loaded == {"key": "value"}


def test_session_plan_property(tmp_path):
    sm = SessionManager()
    sm.state_dir = tmp_path
    sm.session_file = tmp_path / "session.json"
    sm.plan = "test plan"
    assert sm.plan == "test plan"
    data = json.loads(sm.session_file.read_text())
    assert data["plan"] == "test plan"


def test_session_last_command(tmp_path):
    sm = SessionManager()
    sm.state_dir = tmp_path
    sm.session_file = tmp_path / "session.json"
    sm.last_command = "echo hello"
    assert sm.last_command == "echo hello"


def test_session_conversation_summary(tmp_path):
    sm = SessionManager()
    sm.state_dir = tmp_path
    sm.session_file = tmp_path / "session.json"
    sm.conversation_summary = "summary text"
    assert sm.conversation_summary == "summary text"


def test_session_undo_stack(tmp_path):
    sm = SessionManager()
    sm.state_dir = tmp_path
    sm.session_file = tmp_path / "session.json"
    assert sm.undo_stack == []
    sm.undo_stack = [{"action": "write"}]
    assert sm.undo_stack == [{"action": "write"}]


def test_session_memory(tmp_path):
    sm = SessionManager()
    sm.state_dir = tmp_path
    sm.session_file = tmp_path / "session.json"
    assert sm.memory == {}
    sm.memory = {"key": "val"}
    assert sm.memory == {"key": "val"}


def test_session_clear(tmp_path):
    sm = SessionManager()
    sm.state_dir = tmp_path
    sm.session_file = tmp_path / "session.json"
    sm.plan = "test"
    sm.clear()
    assert sm.plan is None
    assert sm._data == {}


def test_session_load_corrupted_json(tmp_path):
    sm = SessionManager()
    sm.state_dir = tmp_path
    sm.session_file = tmp_path / "session.json"
    sm.session_file.write_text("{invalid json}")
    data = sm.load()
    assert data == {}


def test_session_data_passed_to_save(tmp_path):
    sm = SessionManager()
    sm.state_dir = tmp_path
    sm.session_file = tmp_path / "session.json"
    sm.save({"explicit": "data"})
    assert sm._data == {"explicit": "data"}
