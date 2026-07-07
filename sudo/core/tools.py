"""Structured tool definitions and execution engine for sudo CLI.

Replaces brittle XML regex parsing with JSON schema-based function calling.
Each tool has a name, description, JSON input schema, and handler function.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

@dataclass
class ToolSpec:
    """Specification for a single tool with JSON schema."""
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]
    disabled: bool = False


TOOL_REGISTRY: dict[str, ToolSpec] = {}

def _param(t: str, desc: str, **kw) -> dict[str, Any]:
    return {"type": t, "description": desc, **kw}


def register_tool(spec: ToolSpec) -> None:
    TOOL_REGISTRY[spec.name] = spec


def get_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": {
                    "type": "object",
                    "properties": spec.parameters,
                    "required": [k for k, v in spec.parameters.items() if v.get("required", False)],
                },
            },
        }
        for spec in TOOL_REGISTRY.values() if not spec.disabled
    ]


def get_system_prompt_tools() -> str:
    lines = ["Available tools:"]
    for spec in TOOL_REGISTRY.values():
        if spec.disabled:
            continue
        lines.append(f"\n{spec.name}:")
        lines.append(f"  Description: {spec.description}")
        for pname, pschema in spec.parameters.items():
            req = " (required)" if pschema.get("required") else ""
            lines.append(f"  - {pname}: {pschema.get('description', '')}{req}")
    return "\n".join(lines)


# ── Tool Handlers ────────────────────────────────────────────────────────

def _handle_read_file(path: str) -> str:
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return f"[Tool Error: File {path} does not exist]"
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(5000)
        return f"[Tool Output — read_file {path}]:\n{content}"
    except Exception as e:
        return f"[Tool Error reading file: {e}]"


def _handle_write_file(path: str, content: str) -> str:
    try:
        abs_path = os.path.abspath(path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"[Tool Output: File written to {path}]"
    except Exception as e:
        return f"[Tool Error writing file: {e}]"


def _handle_delete_file(path: str) -> str:
    try:
        abs_path = os.path.abspath(path)
        if os.path.isdir(abs_path):
            shutil.rmtree(abs_path)
            return f"[Tool Output: Directory {path} deleted]"
        elif os.path.exists(abs_path):
            os.remove(abs_path)
            return f"[Tool Output: File {path} deleted]"
        else:
            return f"[Tool Error: Path {path} does not exist]"
    except Exception as e:
        return f"[Tool Error deleting path: {e}]"


# Extended dangerous command patterns — covers common destructive patterns
DANGEROUS_CMD_PATTERNS = (
    # Recursive delete
    "rm -rf", "rm -fr", "rm -r ", "rm -f ",
    "rmdir", "rd /s",
    # Windows delete
    "del /s", "del /q",
    # Filesystem destruction
    "mkfs", "format", "fdisk", "parted",
    # Device overwrite
    "> /dev/", "dd if=",
    # Permission escalation
    "chmod -R 777", "chmod -R 000", "chown -R",
    # Fork bomb
    ":(){ :|:& };:",
    # Package manager destruction
    "apt remove --purge", "apt autoremove --purge",
    "yum remove", "dnf remove",
    # Variable expansion tricks
    "$(", "`",
    # Piping to shell
    "| sh", "| bash", "| zsh",
    # Stdin overwrite
    "mv /etc/", "mv /*",
)


def _handle_run_command(cmd: str, timeout: int = 60) -> str:
    cmd_lower = cmd.lower().strip()
    for pattern in DANGEROUS_CMD_PATTERNS:
        if pattern in cmd_lower:
            try:
                confirm = input(
                    f"\033[33m⚠️  Potentially dangerous command detected.\033[0m\n"
                    f"  Command: {cmd}\n"
                    f"  Pattern: {pattern}\n"
                    f"  Type 'yes' to confirm, anything else to cancel: "
                ).strip().lower()
            except (KeyboardInterrupt, EOFError):
                return "[Tool Error: Command cancelled by user]"
            if confirm != "yes":
                return "[Tool Error: Command cancelled by user — dangerous command not confirmed]"
            break

    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        output = ""
        if res.stdout:
            output += f"stdout:\n{res.stdout}"
        if res.stderr:
            output += f"stderr:\n{res.stderr}"
        return (
            f"[Tool Output — run_command] (exit code: {res.returncode})\n{output}"
        )
    except subprocess.TimeoutExpired:
        return "[Tool Error: Command timed out]"
    except Exception as e:
        return f"[Tool Error executing command: {e}]"


def _handle_list_dir(path: str) -> str:
    return "[Tool Error: list_dir is disabled by user security policy]"


# ── Register Tools ───────────────────────────────────────────────────────

register_tool(ToolSpec(
    name="read_file",
    description="Read the contents of a file (max 5000 chars). Use this to examine source code, config files, or any text file.",
    parameters={
        "path": _param("string", "Absolute or relative path to the file", required=True),
    },
    handler=_handle_read_file,
))

register_tool(ToolSpec(
    name="write_file",
    description="Write or overwrite a file with new content. Creates parent directories if needed.",
    parameters={
        "path": _param("string", "Absolute or relative path to the file", required=True),
        "content": _param("string", "Full content to write to the file", required=True),
    },
    handler=_handle_write_file,
))

register_tool(ToolSpec(
    name="delete_file",
    description="Delete a file or empty directory. Use with caution.",
    parameters={
        "path": _param("string", "Absolute or relative path to the file or directory", required=True),
    },
    handler=_handle_delete_file,
))

register_tool(ToolSpec(
    name="run_command",
    description="Run a shell command and capture its output. Use for compilation, tests, git operations, or any CLI tool.",
    parameters={
        "cmd": _param("string", "Shell command to execute", required=True),
        "timeout": _param("integer", "Timeout in seconds (default 60)", required=False),
    },
    handler=_handle_run_command,
))

register_tool(ToolSpec(
    name="list_dir",
    description="List files and directories in a path. DISABLED for security.",
    parameters={
        "path": _param("string", "Path to list", required=True),
    },
    handler=_handle_list_dir,
    disabled=True,
))


# ── Tool Call Parsing (backward-compatible XML + JSON for future use) ─────────

def parse_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse tool calls from model output. Supports XML tags and JSON function calls."""
    calls = []

    # Try JSON-style function calls first: {"function": {"name": "...", "arguments": {...}}}
    json_pattern = r'<function_calls>\s*(.*?)\s*</function_calls>'
    for match in re.finditer(json_pattern, text, re.DOTALL):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, list):
                calls.extend(data)
            else:
                calls.append(data)
        except json.JSONDecodeError:
            pass

    # Fallback: legacy XML tag parsing
    xml_handlers = {
        "read_file": (r'<tool:read_file\s+path=["\'](.*?)["\']\s*/>', ["path"]),
        "write_file": (r'<tool:write_file\s+path=["\'](.*?)["\']\s*>(.*?)</tool:write_file>', ["path", "content"]),
        "delete_file": (r'<tool:delete_file\s+path=["\'](.*?)["\']\s*/>', ["path"]),
        "run_command": (r'<tool:run_command\s+cmd=["\'](.*?)["\']\s*/>', ["cmd"]),
    }

    for name, (pattern, arg_names) in xml_handlers.items():
        for match in re.finditer(pattern, text, re.DOTALL):
            args = {}
            for i, aname in enumerate(arg_names):
                val = match.group(i + 1).strip()
                if aname == "timeout":
                    try:
                        val = int(val)
                    except (ValueError, TypeError):
                        val = 60
                args[aname] = val
            if name == "run_command" and "timeout" not in args:
                args["timeout"] = 60
            calls.append({"name": name, "arguments": args})

    return calls


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool by name with the given arguments."""
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        return f"[Tool Error: Unknown tool '{name}']"
    if spec.disabled:
        return "[Tool Error: This tool is disabled by user security policy]"
    try:
        return spec.handler(**arguments)
    except TypeError as e:
        return f"[Tool Error: Invalid arguments for {name}: {e}]"
    except Exception as e:
        return f"[Tool Error: {e}]"


def parse_and_execute_tools(response_text: str) -> tuple[bool, str]:
    """Parse and execute all tool calls from model output.

    Returns (had_tool_call, combined_result).
    """
    calls = parse_tool_calls(response_text)
    if not calls:
        return False, ""

    outputs = []
    for call in calls:
        name = call.get("name", "")
        args = call.get("arguments", {})
        output = execute_tool(name, args)
        outputs.append((name, output))

    # For backward compat: return single string if only one call
    if len(outputs) == 1:
        return True, outputs[0][1]

    combined = "\n".join(f"[{name}]: {out}" for name, out in outputs)
    return True, combined
