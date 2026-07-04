"""sudo CLI — main argument parser and dispatcher.

Entry point: sudo <command> [options]
"""

from __future__ import annotations

import argparse
import sys

from sudo import __version__
from sudo.core.plugins import discover_plugins, run_hooks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sudo",
        description="AI coding assistant for Android Termux",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  sudo-cli provider list        List all 60+ providers
  sudo-cli provider set groq    Set active provider
  sudo-cli provider key sk-xxx  Save API key
  sudo-cli provider test        Test current provider
  sudo-cli status               Show project summary
  sudo-cli find *.py            Find Python files
  sudo-cli grep "class "        Search for class definitions
  sudo-cli --help               Show this help
        """,
    )

    parser.add_argument("--version", action="version", version=f"sudo {__version__}",
                        help="Show version and exit")
    parser.add_argument("--detail", action="store_true", help="Expand output with more detail")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress non-essential output for scripting")
    parser.add_argument("--pipe", action="store_true", help="Read input from stdin (pipe mode)")

    subparsers = parser.add_subparsers(
        title="commands", dest="command", metavar="<command>",
        help="Use 'sudo <command> --help' for subcommand help",
    )

    _register_commands(subparsers)
    return parser


def _register_commands(subparsers) -> None:
    import sudo.commands.provider as cmd_provider
    import sudo.commands.status as cmd_status
    import sudo.commands.find as cmd_find
    import sudo.commands.grep as cmd_grep
    import sudo.commands.chat as cmd_chat

    cmd_provider.register(subparsers)
    cmd_status.register(subparsers)
    cmd_find.register(subparsers)
    cmd_grep.register(subparsers)
    cmd_chat.register(subparsers)


def main(argv: list[str] | None = None) -> int:
    # Ensure 'cli' shortcut command is created in the same prefix path as sudo-cli
    try:
        import shutil
        import os
        for target_name in ["sudo-cli", "sudo"]:
            exec_path = shutil.which(target_name)
            if exec_path:
                bin_dir = os.path.dirname(exec_path)
                cli_path = os.path.join(bin_dir, "cli")
                if not os.path.exists(cli_path):
                    try:
                        if hasattr(os, "symlink"):
                            os.symlink(target_name, cli_path)
                        else:
                            shutil.copy2(exec_path, cli_path)
                    except Exception as e:
                        print(f"\033[33m⚠️  Unable to automatically create 'cli' shortcut in {bin_dir}: {e}\033[0m")
                        print(f"\033[33m   To enable it manually, please run:\033[0m")
                        print(f"   \033[1mln -s {exec_path} {cli_path}\033[0m\n")
                    break
    except Exception:
        pass

    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except Exception:
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    discover_plugins()
    run_hooks("on_cli_start", args)

    if not args.command:
        from sudo.commands.chat import run_chat

        # Pipe mode: read stdin and pass as initial input
        _pipe_input = None
        if args.pipe or not sys.stdin.isatty():
            try:
                _pipe_input = sys.stdin.read().strip()
            except Exception:
                pass

        mock_args = argparse.Namespace(
            pipe_input=_pipe_input,
            quiet=args.quiet,
            json_output=args.json,
        )
        return run_chat(mock_args)

    try:
        args.func(args)
        return 0
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as e:
        if args.json:
            import json
            print(json.dumps({"error": str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
            if args.detail:
                import traceback
                traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
