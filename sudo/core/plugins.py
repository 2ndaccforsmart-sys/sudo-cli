"""Simple plugin/hook system for sudo CLI.

Allows extending the CLI with custom tool handlers, middleware,
and lifecycle hooks without modifying core code.

Usage:
    from sudo.core.plugins import hook, register_hook, run_hooks

    def my_startup_hook(ctx):
        print("Plugin initialized!")

    register_hook("on_chat_start", my_startup_hook)
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional


HOOKS: dict[str, list[Callable]] = {}
PLUGIN_DIRS: list[Path] = []


def register_hook(event: str, fn: Callable) -> None:
    """Register a callable for a given event name."""
    HOOKS.setdefault(event, []).append(fn)


def run_hooks(event: str, *args, **kwargs) -> list[Any]:
    """Run all hooks registered for an event. Returns list of results."""
    results = []
    for fn in HOOKS.get(event, []):
        try:
            results.append(fn(*args, **kwargs))
        except Exception as e:
            results.append(e)
    return results


def clear_hooks(event: Optional[str] = None) -> None:
    """Clear hooks for an event (or all events if None)."""
    if event:
        HOOKS.pop(event, None)
    else:
        HOOKS.clear()


def discover_plugins(plugin_dirs: Optional[list[Path]] = None) -> None:
    """Scan plugin directories and import Python files as plugin modules.

    Each plugin file should define a ``register`` function that
    takes no arguments and calls ``register_hook`` to set up hooks.
    """
    dirs = plugin_dirs or PLUGIN_DIRS
    if not dirs:
        config_plugin_dir = Path.home() / ".config" / "sudo" / "plugins"
        if config_plugin_dir.exists():
            dirs.append(config_plugin_dir)
        # Also check a local plugins dir
        local_plugin_dir = Path.cwd() / ".sudo" / "plugins"
        if local_plugin_dir.exists():
            dirs.append(local_plugin_dir)

    for d in dirs:
        if not d.is_dir():
            continue
        for pyfile in sorted(d.glob("*.py")):
            if pyfile.stem.startswith("_"):
                continue
            mod_name = f"sudoplugin_{pyfile.stem}"
            spec = importlib.util.spec_from_file_location(mod_name, pyfile)
            if spec and spec.loader:
                try:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[mod_name] = mod
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "register"):
                        mod.register()
                except Exception as e:
                    print(f"\033[33mPlugin error ({pyfile.name}): {e}\033[0m")


def add_plugin_dir(path: Path) -> None:
    """Register a directory to scan for plugins."""
    if path not in PLUGIN_DIRS:
        PLUGIN_DIRS.append(path)


# ── Built-in hook events ─────────────────────────────────────────────────────

HOOK_EVENTS = {
    "on_cli_start": "Called at CLI startup. Args: (config)",
    "on_chat_start": "Called when chat session starts. Args: (config, provider)",
    "on_chat_message": "Called on each user message. Args: (message_text, messages)",
    "on_tool_before": "Called before a tool executes. Args: (tool_name, arguments)",
    "on_tool_after": "Called after a tool executes. Args: (tool_name, arguments, result)",
    "on_chat_end": "Called when chat ends. Args: ()",
}
