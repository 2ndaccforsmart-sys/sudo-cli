"""Tests for plugin dedup — calling discover_plugins twice should not double-load."""

import tempfile
from pathlib import Path

from sudo.core.plugins import (
    clear_hooks,
    discover_plugins,
    HOOKS,
    _LOADED_PLUGINS,
)


def setup_function():
    clear_hooks()
    _LOADED_PLUGINS.clear()


def test_discover_plugins_dedup():
    """Calling discover_plugins twice should not load plugins twice."""
    with tempfile.TemporaryDirectory() as d:
        plugin_dir = Path(d) / "plugins"
        plugin_dir.mkdir()

        # Create a test plugin
        plugin_file = plugin_dir / "test_dedup.py"
        plugin_file.write_text("""
counter = 0

def register():
    global counter
    counter += 1
    from sudo.core.plugins import register_hook
    register_hook("dedup_test", lambda: "ok")
""")

        # Discover once
        discover_plugins([plugin_dir])
        assert "dedup_test" in HOOKS
        assert len(HOOKS["dedup_test"]) == 1

        # Discover again — should NOT add another hook
        discover_plugins([plugin_dir])
        assert len(HOOKS["dedup_test"]) == 1


def test_discover_plugins_loads_once():
    """Plugin register() should only be called once."""
    with tempfile.TemporaryDirectory() as d:
        plugin_dir = Path(d) / "plugins"
        plugin_dir.mkdir()

        counter_file = Path(d) / "counter.txt"
        counter_file.write_text("0")

        plugin_file = plugin_dir / "test_counter.py"
        # Use raw string and repr to avoid Windows backslash unicode issues
        counter_path = repr(str(counter_file))
        plugin_file.write_text(f"""
def register():
    path = {counter_path}
    count = int(open(path).read().strip()) + 1
    open(path, "w").write(str(count))
""")

        discover_plugins([plugin_dir])
        discover_plugins([plugin_dir])

        count = int(counter_file.read_text().strip())
        assert count == 1  # Only loaded once


def test_discover_plugins_different_dirs():
    """Different plugin directories should load independently."""
    with tempfile.TemporaryDirectory() as d:
        dir1 = Path(d) / "plugins1"
        dir2 = Path(d) / "plugins2"
        dir1.mkdir()
        dir2.mkdir()

        (dir1 / "plugin_a.py").write_text("""
def register():
    from sudo.core.plugins import register_hook
    register_hook("multi_dir", lambda: "a")
""")

        (dir2 / "plugin_b.py").write_text("""
def register():
    from sudo.core.plugins import register_hook
    register_hook("multi_dir", lambda: "b")
""")

        discover_plugins([dir1, dir2])
        assert "multi_dir" in HOOKS
        assert len(HOOKS["multi_dir"]) == 2
