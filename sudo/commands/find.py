"""'sudo find' command — glob file search."""

from __future__ import annotations

import os
from pathlib import Path

from sudo.utils.output import terminal_width


def register(subparsers) -> None:
    p = subparsers.add_parser("find", help="Search files by glob pattern")
    p.add_argument("pattern", help="Glob pattern (e.g. '*.py')")
    p.add_argument("--flat", action="store_true", help="Flat file list")
    p.add_argument("--code", action="store_true", help="Source files only")
    p.set_defaults(func=lambda args: run_find(args))


SOURCE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala", ".sh", ".bash",
    ".zsh", ".fish", ".sql", ".r", ".m", ".cs", ".hs", ".ex", ".exs",
    ".html", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
    ".yaml", ".yml", ".toml", ".json", ".xml", ".md", ".rst",
    ".zig", ".nim", ".cr", ".lua", ".clj", ".cljs", ".erl", ".hrl",
    ".fs", ".fsx", ".dart", ".asm", ".s", ".tex", ".bib",
}


def _ignored_dirs(cwd):
    ignored = {".git", "__pycache__", "node_modules", ".venv", "venv",
               ".env", "dist", "build", ".tox", ".eggs", "target"}
    try:
        gi = os.path.join(cwd, ".gitignore")
        if os.path.isfile(gi):
            with open(gi) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("!"):
                        ignored.add(line.rstrip("/"))
    except Exception:
        pass
    return ignored


def _format_tree(paths, cwd, tw):
    if not paths:
        return "(no matches)"
    tree = {}
    for p in sorted(paths):
        try:
            rel = os.path.relpath(p, cwd)
        except ValueError:
            rel = p
        parts = Path(rel).parts
        node = tree
        for i, part in enumerate(parts):
            if i >= 2:
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
                    lines.append(f"{indent}    {f}")
                continue
            if val:
                lines.append(f"{indent}  {key}/")
                _render(val, depth + 1)
            else:
                lines.append(f"{indent}  {key}")
    _render(tree)
    return "\n".join(lines)


def run_find(args) -> None:
    cwd = os.getcwd()
    pattern = args.pattern
    ignored = _ignored_dirs(cwd)
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
        tree = _format_tree(matches, cwd, tw)
        print(tree if tree else "  (no matches)")
