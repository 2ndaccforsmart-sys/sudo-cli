"""'sudo find' command — glob file search."""

from __future__ import annotations

import os
from pathlib import Path

from sudo.utils.output import terminal_width, format_tree
from sudo.utils.constants import SOURCE_EXTS, load_gitignore_patterns


def register(subparsers) -> None:
    p = subparsers.add_parser("find", help="Search files by glob pattern")
    p.add_argument("pattern", help="Glob pattern (e.g. '*.py')")
    p.add_argument("--flat", action="store_true", help="Flat file list")
    p.add_argument("--code", action="store_true", help="Source files only")
    p.set_defaults(func=lambda args: run_find(args))


def run_find(args) -> None:
    cwd = os.getcwd()
    pattern = args.pattern
    ignored = load_gitignore_patterns(cwd)
    tw = terminal_width()
    matches = []

    try:
        for p in Path(cwd).rglob(pattern):
            if any(part.startswith(".") for part in p.parts):
                continue
            if any(part in p.parts for part in ignored):
                continue
            if not p.is_file():
                continue
            if args.code and p.suffix.lower() not in SOURCE_EXTS:
                continue
            matches.append(str(p.resolve()))
    except RecursionError:
        for root, dirs, files in os.walk(cwd):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ignored]
            for f in files:
                if f.startswith("."):
                    continue
                if Path(f).match(pattern):
                    matches.append(os.path.join(root, f))

    print(f"Found {len(matches)} match(es) for '{pattern}':")
    print()

    if args.flat:
        for m in sorted(matches):
            try:
                rel = os.path.relpath(m, cwd)
            except ValueError:
                rel = m
            print(f"  {rel[:tw-4]}")
    else:
        tree = format_tree(matches, max_depth=2)
        print(tree if tree else "  (no matches)")
