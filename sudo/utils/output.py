"""Output formatting utilities for sudo CLI.

All output constrained to 70 chars wide with auto-pagination.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def terminal_width() -> int:
    """Detect terminal width, clamped to 70."""
    try:
        w = shutil.get_terminal_size().columns
        return min(w, 70)
    except Exception:
        return 70


def page(text: str) -> None:
    """Pipe text through $PAGER if output exceeds terminal height."""
    if not text:
        return
    lines = text.splitlines()
    _, h = shutil.get_terminal_size()
    if len(lines) <= h:
        print(text)
        return
    pager = os.environ.get("PAGER")
    if not pager:
        pager = "more" if sys.platform == "win32" else "less"
    p = None
    try:
        shell_flag = sys.platform != "win32" and " " not in pager
        p = subprocess.Popen(pager, stdin=subprocess.PIPE, shell=shell_flag)
        p.communicate(text.encode(), timeout=30)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired, BrokenPipeError):
        if p is not None:
            try:
                p.kill()
                p.wait()
            except Exception:
                pass
        print(text)


def truncate(text: str, max_lines: int = 20) -> str:
    """Truncate text to max_lines, appending a summary."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    shown = lines[:max_lines]
    remaining = len(lines) - max_lines
    shown.append(f"... (+{remaining} more line{'s' if remaining != 1 else ''})")
    return "\n".join(shown)


def format_status(data: dict[str, Any]) -> str:
    """Format key: value pairs compactly."""
    tw = terminal_width()
    lines = []
    for k, v in data.items():
        label = k.replace("_", " ").title()
        val = str(v) if v is not None else "(not set)"
        line = f"  {label}: {val}"
        if len(line) > tw:
            line = line[: tw - 3] + "..."
        lines.append(line)
    return "\n".join(lines)


def format_tree(paths: list[str], max_depth: int = 2) -> str:
    """Format a list of file paths as a compact directory tree."""
    if not paths:
        return "(no files)"

    tree: dict = {}
    for p in sorted(paths):
        parts = Path(p).parts
        node = tree
        for i, part in enumerate(parts):
            if i >= max_depth:
                node.setdefault("__files__", []).append("/".join(parts[i:]))
                break
            if part not in node:
                node[part] = {}
            node = node[part]

    lines = []

    def _render(subtree, depth=0):
        indent = "  " * depth
        for key in sorted(subtree):
            val = subtree[key]
            if key == "__files__":
                for f in val:
                    lines.append(f"{indent}  {f}")
                continue
            if val:
                lines.append(f"{indent}{key}/")
                _render(val, depth + 1)
            else:
                lines.append(f"{indent}{key}")

    _render(tree)
    return "\n".join(lines)


def format_check(text: str, label: str = "") -> str:
    return f"  [✓] {label}: {text}" if label else f"  [✓] {text}"


def format_cross(text: str, label: str = "") -> str:
    return f"  [✗] {label}: {text}" if label else f"  [✗] {text}"


def render_table(headers: list[str], rows: list[list[str]], max_width: int = 66) -> list[str]:
    """Render a table as a list of text lines."""
    if not rows:
        return ["(no data)"]
    out = []
    ncols = len(headers)
    col_widths = []
    for i, h in enumerate(headers):
        max_cell = max(len(str(r[i])) if i < len(r) else 0 for r in rows) if rows else 0
        col_widths.append(max(len(h), max_cell))
    total = sum(col_widths) + 3 * (ncols - 1) + 2
    if total > max_width and ncols > 0:
        overflow = total - max_width
        for i in range(ncols):
            if overflow <= 0:
                break
            shrink = max(0, min(col_widths[i] - 5, overflow // (ncols - i)))
            if shrink > 0:
                col_widths[i] -= shrink
                overflow -= shrink

    def _render(cells):
        parts = []
        for i, c in enumerate(cells):
            w = col_widths[i] if i < len(col_widths) else 10
            parts.append(str(c)[:w].ljust(w))
        return " " + "  ".join(parts)

    header_line = _render(headers)
    out.append(header_line)
    out.append("-" * len(header_line))
    for row in rows:
        out.append(_render(row[:ncols]))
    return out
