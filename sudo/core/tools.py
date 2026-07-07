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

def _get_gcs_client():
    """Resolve GCS bucket + credentials.

    Priority:
      1. Central sudo-config.json (cfg.gcs_bucket / cfg.gcs_key_file)
      2. SyncRegistry settings (legacy separate config)
      3. Environment variables GCS_BUCKET / GOOGLE_APPLICATION_CREDENTIALS
    """
    from sudo.core.sync.gcs_client import GCSClient
    from sudo.core.config import load as _load_cfg

    # 1. Central config
    cfg = _load_cfg()
    bucket = cfg.gcs_bucket
    creds = cfg.gcs_key_file

    # 2. SyncRegistry fallback
    if not bucket:
        try:
            from sudo.core.sync.registry import SyncRegistry
            registry = SyncRegistry()
            registry.load()
            settings = registry.get_settings()
            bucket = settings.gcs_bucket
            creds = creds or settings.gcs_credentials_path
        except Exception:
            pass

    # 3. Env vars fallback
    bucket = bucket or os.environ.get("GCS_BUCKET")
    creds = creds or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

    if not bucket:
        raise ValueError(
            "GCS bucket not configured. Run: /gcs-config bucket <your-bucket-name>"
        )
    return GCSClient(bucket, creds)


def _handle_gcs_list_files(prefix: str = "") -> str:
    try:
        client = _get_gcs_client()
        files = client.list_files(prefix)
        if not files:
            return f"[Tool Output: No files found in GCS under prefix '{prefix}']"
        lines = [f"Files in GCS under prefix '{prefix}':"]
        for f in files:
            size_kb = f['size'] / 1024 if f['size'] is not None else 0
            lines.append(f"  - {f['name']} ({size_kb:.1f} KB, updated: {f['updated']})")
        return "\n".join(lines)
    except Exception as e:
        return f"[Tool Error: {e}]"


def _handle_gcs_read_file(path: str) -> str:
    try:
        client = _get_gcs_client()
        content = client.read_file_text(path)
        if content is None:
            return f"[Tool Error: File '{path}' not found in GCS]"
        truncated_content = content[:5000]
        suffix = "\n... (truncated, file too large)" if len(content) > 5000 else ""
        return f"[Tool Output — gcs_read_file {path}]:\n{truncated_content}{suffix}"
    except Exception as e:
        return f"[Tool Error: {e}]"


def _handle_gcs_write_file(path: str, content: str) -> str:
    try:
        client = _get_gcs_client()
        blob = client._bucket.blob(path)
        client._retry(blob.upload_from_string, content)
        return f"[Tool Output: File '{path}' successfully written to GCS]"
    except Exception as e:
        return f"[Tool Error: {e}]"


def _handle_gcs_delete_file(path: str) -> str:
    try:
        client = _get_gcs_client()
        success = client.delete_file(path)
        if success:
            return f"[Tool Output: File '{path}' successfully deleted from GCS]"
        else:
            return f"[Tool Error: Failed to delete file '{path}' from GCS]"
    except Exception as e:
        return f"[Tool Error: {e}]"


def _handle_gcs_make_directory(path: str) -> str:
    try:
        client = _get_gcs_client()
        folder_path = path if path.endswith('/') else path + '/'
        blob = client._bucket.blob(folder_path)
        client._retry(blob.upload_from_string, "")
        return f"[Tool Output: Directory '{folder_path}' successfully created in GCS]"
    except Exception as e:
        return f"[Tool Error: {e}]"


def _handle_gcs_upload(local_path: str, gcs_dest_path: str) -> str:
    try:
        client = _get_gcs_client()
        local_abs = os.path.abspath(local_path)
        if not os.path.exists(local_abs):
            return f"[Tool Error: Local path '{local_path}' does not exist]"
            
        if os.path.isfile(local_abs):
            blob = client._bucket.blob(gcs_dest_path)
            with open(local_abs, "rb") as f:
                client._retry(blob.upload_from_file, f)
            return f"[Tool Output: File '{local_path}' successfully uploaded to 'gs://{client.bucket_name}/{gcs_dest_path}']"
        else:
            uploaded_count = 0
            for root, dirs, files in os.walk(local_abs):
                for file in files:
                    full_local = os.path.join(root, file)
                    rel_path = os.path.relpath(full_local, local_abs)
                    dest_blob_path = os.path.join(gcs_dest_path, rel_path).replace("\\", "/")
                    blob = client._bucket.blob(dest_blob_path)
                    with open(full_local, "rb") as f:
                        client._retry(blob.upload_from_file, f)
                    uploaded_count += 1
            return f"[Tool Output: Folder '{local_path}' successfully uploaded. Total {uploaded_count} files written to 'gs://{client.bucket_name}/{gcs_dest_path}']"
    except Exception as e:
        return f"[Tool Error: {e}]"


def _handle_save_skill(name: str, description: str, system_prompt: str) -> str:
    try:
        from sudo.core.skills import add_skill
        add_skill(name, description, system_prompt)
        return f"[Tool Output: Skill '{name}' successfully saved! Users can now use it via '/{name} <prompt>']"
    except Exception as e:
        return f"[Tool Error: {e}]"

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


def _clean_html(html: str) -> str:
    import re
    import html as html_lib
    html = re.sub(r'<(script|style)\b[^>]*>([\s\S]*?)<\/\1>', '', html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', html)
    text = html_lib.unescape(text)
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _handle_browse(url: str) -> str:
    try:
        import httpx
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=15)
        if resp.status_code != 200:
            return f"[Tool Error: Failed to fetch {url} (status code {resp.status_code})]"
        text = _clean_html(resp.text)
        if len(text) > 8000:
            text = text[:8000] + "\n... [truncated]"
        return f"[Tool Output — browse {url}]:\n{text}"
    except Exception as e:
        return f"[Tool Error browsing URL: {e}]"


def _handle_github_push(commit_message: str, branch: str = "main") -> str:
    try:
        import subprocess
        add_res = subprocess.run("git add .", shell=True, capture_output=True, text=True, timeout=30)
        commit_res = subprocess.run(f'git commit -m "{commit_message}"', shell=True, capture_output=True, text=True, timeout=30)
        push_res = subprocess.run(f"git push origin {branch}", shell=True, capture_output=True, text=True, timeout=60)
        
        output = f"git add output: {add_res.stdout} {add_res.stderr}\n"
        output += f"git commit output: {commit_res.stdout} {commit_res.stderr}\n"
        output += f"git push output: {push_res.stdout} {push_res.stderr}\n"
        return f"[Tool Output — github_push]:\n{output}"
    except Exception as e:
        return f"[Tool Error performing github_push: {e}]"


def _handle_telegram_send(message: str) -> str:
    """Send a message to the user via Telegram."""
    try:
        from sudo.core.config import load as _load_cfg
        from sudo.core.telegram import send_telegram_message
        cfg = _load_cfg()
        if not cfg.telegram_enabled or not cfg.telegram_token or not cfg.telegram_chat_id:
            return "[Tool Error: Telegram is not configured or not enabled. Use /config telegram to set it up.]"
        send_telegram_message(cfg, message)
        preview = message[:80] + "..." if len(message) > 80 else message
        return f"[Tool Output — telegram_send]: Message sent to Telegram: {preview}"
    except Exception as e:
        return f"[Tool Error sending Telegram message: {e}]"


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
    try:
        from sudo.core.config import load
        cfg = load()
        yolo = cfg.yolo_mode
    except Exception:
        yolo = False

    if not yolo:
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

register_tool(ToolSpec(
    name="gcs_list_files",
    description="List all files in GCS under a prefix/directory path. Use to see what files exist in GCS.",
    parameters={
        "prefix": _param("string", "Filter results to files starting with this prefix (optional)", required=False),
    },
    handler=_handle_gcs_list_files,
))

register_tool(ToolSpec(
    name="gcs_read_file",
    description="Read the contents of a file directly from GCS (max 5000 chars).",
    parameters={
        "path": _param("string", "Full cloud path to the file in GCS", required=True),
    },
    handler=_handle_gcs_read_file,
))

register_tool(ToolSpec(
    name="gcs_write_file",
    description="Write/upload text content directly to a cloud file in GCS.",
    parameters={
        "path": _param("string", "Full cloud path to write the file to in GCS", required=True),
        "content": _param("string", "Text content to write to the file", required=True),
    },
    handler=_handle_gcs_write_file,
))

register_tool(ToolSpec(
    name="gcs_delete_file",
    description="Delete a file from GCS.",
    parameters={
        "path": _param("string", "Full cloud path to the file in GCS to delete", required=True),
    },
    handler=_handle_gcs_delete_file,
))

register_tool(ToolSpec(
    name="gcs_make_directory",
    description="Create a virtual directory/folder path in GCS (creates a trailing slash placeholder).",
    parameters={
        "path": _param("string", "Directory path to create (e.g. folder/subfolder)", required=True),
    },
    handler=_handle_gcs_make_directory,
))

register_tool(ToolSpec(
    name="gcs_upload",
    description="Upload a local file or folder recursively from your PC to Google Cloud Storage.",
    parameters={
        "local_path": _param("string", "Local file or directory path to upload", required=True),
        "gcs_dest_path": _param("string", "Destination GCS path (e.g. folder/subfolder or folder/file.txt)", required=True),
    },
    handler=_handle_gcs_upload,
))

register_tool(ToolSpec(
    name="save_skill",
    description="Save a new custom skill (behavior/system prompt) for the assistant. The skill will be available as a slash command in the chat.",
    parameters={
        "name": _param("string", "Name of the skill (alphanumeric, e.g. 'refactor')", required=True),
        "description": _param("string", "Brief description of what the skill does", required=True),
        "system_prompt": _param("string", "The detailed system prompt/instructions that define the assistant's behavior when this skill is active", required=True),
    },
    handler=_handle_save_skill,
))

register_tool(ToolSpec(
    name="browse",
    description="Download and read the text content of any website URL.",
    parameters={
        "url": _param("string", "URL of the page to browse", required=True),
    },
    handler=_handle_browse,
))

register_tool(ToolSpec(
    name="telegram_send",
    description=(
        "Send a message to the user via their configured Telegram bot. Use this whenever the user asks to be notified, "
        "or when they want file contents, summaries, results, or any other information delivered to Telegram. "
        "Always send a final notification when a long task completes if the user asked for it."
    ),
    parameters={
        "message": _param("string", "The text message to send to the user on Telegram", required=True),
    },
    handler=_handle_telegram_send,
))

register_tool(ToolSpec(
    name="github_push",
    description="Automatically run git add, commit, and push to GitHub repository.",
    parameters={
        "commit_message": _param("string", "Commit message", required=True),
        "branch": _param("string", "Branch to push to (default: main)", required=False),
    },
    handler=_handle_github_push,
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
        "gcs_list_files": (r'<tool:gcs_list_files(?:\s+prefix=["\'](.*?)["\'])?\s*/>', ["prefix"]),
        "gcs_read_file": (r'<tool:gcs_read_file\s+path=["\'](.*?)["\']\s*/>', ["path"]),
        "gcs_write_file": (r'<tool:gcs_write_file\s+path=["\'](.*?)["\']\s*>(.*?)</tool:gcs_write_file>', ["path", "content"]),
        "gcs_delete_file": (r'<tool:gcs_delete_file\s+path=["\'](.*?)["\']\s*/>', ["path"]),
        "gcs_make_directory": (r'<tool:gcs_make_directory\s+path=["\'](.*?)["\']\s*/>', ["path"]),
        "gcs_upload": (r'<tool:gcs_upload\s+local_path=["\'](.*?)["\']\s+gcs_dest_path=["\'](.*?)["\']\s*/>', ["local_path", "gcs_dest_path"]),
        "save_skill": (r'<tool:save_skill\s+name=["\'](.*?)["\']\s+description=["\'](.*?)["\']\s*>(.*?)</tool:save_skill>', ["name", "description", "system_prompt"]),
        "browse": (r'<tool:browse\s+url=["\'](.*?)["\']\s*/>', ["url"]),
        "github_push": (r'<tool:github_push\s+commit_message=["\'](.*?)["\'](?:\s+branch=["\'](.*?)["\'])?\s*/>', ["commit_message", "branch"]),
        "telegram_send": (r'<tool:telegram_send\s+message=["\'](.*?)["\']\s*/>', ["message"]),
    }

    for name, (pattern, arg_names) in xml_handlers.items():
        for match in re.finditer(pattern, text, re.DOTALL):
            args = {}
            for idx_grp, aname in enumerate(arg_names):
                group_val = match.group(idx_grp + 1)
                val = group_val.strip() if group_val is not None else ""
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
