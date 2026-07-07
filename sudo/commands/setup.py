"""'sudo setup' command — interactive setup wizard."""
from __future__ import annotations
import sys
from sudo.commands.chat import run_setup_wizard

def register(subparsers) -> None:
    p = subparsers.add_parser("setup", help="Run interactive setup wizard for LLM provider and model")
    p.set_defaults(func=lambda args: run_setup(args))

def run_setup(args) -> None:
    success = run_setup_wizard()
    if not success:
        sys.exit(1)
