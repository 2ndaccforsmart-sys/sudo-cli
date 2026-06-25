"""'sudo grep' command — ripgrep-first text search."""

from __future__ import annotations

import subprocess
import sys

from sudo.utils.output import terminal_width


def register(subparsers) -> None:
    p = subparsers.add_parser("grep", help="Search file contents with regex")
    p.add_argument("pattern", help="Regex pattern to search for")
    p.add_argument("--context", "-C", type=int, default=0, help="Lines of context (default: 0)")
    p.add_argument("--max-matches", type=int, default=5, help="Max matches per file (default: 5)")
    p.add_argument("--files", action="store_true", help="Only show matching filenames")
    p.set_defaults(func=lambda args: run_grep(args))


def _try_rg(args) -> int:
    try:
        subprocess.run(["rg", "--version"], capture_output=True, timeout=2)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 1

    cmd = ["rg", "--no-heading", "--line-number", "--color=never"]
    if args.context > 0:
        cmd.extend(["-C", str(args.context)])
    if args.files:
        cmd.append("--files-with-matches")
    else:
        cmd.extend(["--max-count", str(args.max_matches)])
    cmd.append(args.pattern)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout
        if output:
            tw = terminal_width()
            for line in output.splitlines():
                print(line[: tw - 3] + "..." if len(line) > tw else line)
            if result.stderr:
                print(f"  [stderr] {result.stderr[:200]}", file=sys.stderr)
        else:
            print(f"No matches for '{args.pattern}'")
        return 0
    except subprocess.TimeoutExpired:
        print("Search timed out.")
        return 2
    except Exception as e:
        print(f"ripgrep error: {e}", file=sys.stderr)
        return 2


def _fallback_grep(args) -> None:
    cwd = "."
    cmd = ["grep", "-rn", "--color=never"]
    if args.context > 0:
        cmd.extend(["-C", str(args.context)])
    if args.files:
        cmd.append("-l")
    else:
        cmd.extend(["--max-count", str(args.max_matches)])
    cmd.extend([args.pattern, cwd])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout
    except FileNotFoundError:
        print("grep not found. Install ripgrep for best results.")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("Search timed out.")
        sys.exit(1)

    if not output:
        print(f"No matches for '{args.pattern}'")
        return

    tw = terminal_width()
    ignored = {".git", "__pycache__", "node_modules", ".venv", "venv", ".env", "dist", "build"}
    count = 0
    for line in output.splitlines():
        if any(ig in line for ig in ignored):
            continue
        count += 1
        print(line[: tw - 3] + "..." if len(line) > tw else line)
    print(f"\n({count} matches total)")


def run_grep(args) -> None:
    if _try_rg(args) == 1:
        _fallback_grep(args)
