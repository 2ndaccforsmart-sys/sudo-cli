"""Tests for the plugin/hook system."""

import tempfile
from pathlib import Path

from sudo.core.plugins import (
    register_hook,
    run_hooks,
    clear_hooks,
    HOOKS,
    add_plugin_dir,
)


def setup_function():
    clear_hooks()


def test_register_and_run_hook():
    results = []

    def my_hook(msg):
        results.append(msg)
        return f"processed: {msg}"

    register_hook("test_event", my_hook)
    out = run_hooks("test_event", "hello")
    assert results == ["hello"]
    assert out == ["processed: hello"]


def test_multiple_hooks_same_event():
    out = []

    register_hook("multi", lambda: out.append("a"))
    register_hook("multi", lambda: out.append("b"))

    run_hooks("multi")
    assert out == ["a", "b"]


def test_hook_exception_isolation():
    def bad_hook():
        raise ValueError("oops")

    def good_hook():
        return "ok"

    register_hook("isolated", bad_hook)
    register_hook("isolated", good_hook)

    results = run_hooks("isolated")
    assert len(results) == 2
    assert isinstance(results[0], ValueError)
    assert results[1] == "ok"


def test_run_unknown_event():
    results = run_hooks("nonexistent_event")
    assert results == []


def test_clear_specific_event():
    register_hook("ev1", lambda: 1)
    register_hook("ev2", lambda: 2)
    clear_hooks("ev1")
    assert "ev1" not in HOOKS
    assert "ev2" in HOOKS


def test_clear_all_events():
    register_hook("a", lambda: 1)
    register_hook("b", lambda: 2)
    clear_hooks()
    assert HOOKS == {}


def test_add_plugin_dir():
    with tempfile.TemporaryDirectory() as d:
        plugin_dir = Path(d) / "plugins"
        plugin_dir.mkdir()
        add_plugin_dir(plugin_dir)
        from sudo.core.plugins import PLUGIN_DIRS
        assert plugin_dir in PLUGIN_DIRS


def test_hook_events_defined():
    from sudo.core.plugins import HOOK_EVENTS
    expected_events = {
        "on_cli_start", "on_chat_start", "on_chat_message",
        "on_tool_before", "on_tool_after", "on_chat_end",
    }
    assert expected_events.issubset(HOOK_EVENTS.keys())
    for event, desc in HOOK_EVENTS.items():
        assert isinstance(event, str)
        assert isinstance(desc, str)
