"""sudo CLI — main argument parser and dispatcher.

Entry point: sudo <command> [options]
"""

from __future__ import annotations

import argparse
import sys

from sudo import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sudo",
        description="AI coding assistant for Android Termux",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  sudo provider list           List all 60+ providers
  sudo provider set groq       Set active provider
  sudo provider key sk-xxx     Save API key
  sudo provider test           Test current provider
  sudo status                  Show project summary
  sudo find *.py               Find Python files
  sudo find --code "def "      Find matches in source files
  sudo grep "class "           Search for class definitions
  sudo --help                  Show this help
        """,
    )

    parser.add_argument("--version", action="version", version=f"sudoc {__version__}")
    parser.add_argument("--detail", action="store_true", help="Expand output with more detail")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")

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

    cmd_provider.register(subparsers)
    cmd_status.register(subparsers)
    cmd_find.register(subparsers)
    cmd_grep.register(subparsers)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

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
