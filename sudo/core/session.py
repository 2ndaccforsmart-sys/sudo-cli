"""Session state persistence for sudo CLI.

Saves/loads session JSON per-project-hash under ~/.config/sudo/state/.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from sudo.core.config import STATE_DIR_BASE

# Optional tiktoken for accurate token counting
try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    tiktoken = None
    TIKTOKEN_AVAILABLE = False


# Cache project hash to avoid repeated subprocess calls
_PROJECT_HASH_CACHE: dict[Optional[str], str] = {}

# Context window sizes for known models
CONTEXT_WINDOWS = {
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 16384,
    "claude-sonnet-4-20250514": 200000,
    "claude-3-5-sonnet-latest": 200000,
    "claude-3-5-haiku-latest": 200000,
    "claude-3-opus-latest": 200000,
    "gemini-2.0-flash": 1048576,
    "gemini-2.0-pro-exp": 1048576,
    "gemini-1.5-flash": 1048576,
    "gemini-1.5-pro": 1048576,
    "deepseek-chat": 64000,
    "deepseek-coder": 64000,
    "llama-3.3-70b-versatile": 128000,
    "llama-3.1-8b-instant": 128000,
    "llama-3.1-70b-versatile": 128000,
    "llama-3.2": 4096,
    "llama3.2": 4096,
    "llama3.1": 8192,
    "mistral-large-latest": 128000,
    "mixtral-8x22b-instruct-v0.1": 64000,
    "mixtral-8x7b-32768": 32768,
    "gemma2-9b-it": 8192,
}

DEFAULT_CONTEXT_WINDOW = 32000


class TokenCounter:
    """Token counter with tiktoken for accurate counting."""
    
    def __init__(self, model: str = ""):
        self.model = model.lower()
        self._encoding = None
        self._init_encoding()
    
    def _init_encoding(self) -> None:
        """Initialize tiktoken encoding for the model."""
        try:
            if "gpt-4o" in self.model or "gpt-4-turbo" in self.model:
                self._encoding = tiktoken.encoding_for_model("gpt-4o")
            elif "gpt-4" in self.model:
                self._encoding = tiktoken.encoding_for_model("gpt-4")
            elif "gpt-3.5" in self.model:
                self._encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
            elif "claude" in self.model:
                # Claude uses similar tokenization to GPT
                self._encoding = tiktoken.get_encoding("cl100k_base")
            elif "gemini" in self.model:
                # Gemini uses similar tokenization
                self._encoding = tiktoken.get_encoding("cl100k_base")
            else:
                # Default to cl100k_base (GPT-4/3.5 tokenizer)
                self._encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._encoding = tiktoken.get_encoding("cl100k_base")
    
    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        if self._encoding:
            return len(self._encoding.encode(text))
        # Fallback: rough estimation (chars / 4)
        return len(text) // 4
    
    def count_messages_tokens(self, messages: list[dict]) -> int:
        """Count total tokens in message list."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self.count_tokens(content)
            elif isinstance(content, list):
                # Handle multimodal content
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += self.count_tokens(part.get("text", ""))
            # Add overhead for message structure (~4 tokens per message)
            total += 4
        return total
    
    def get_context_limit(self) -> int:
        """Get context window limit for the model."""
        for key, limit in CONTEXT_WINDOWS.items():
            if key in self.model:
                return limit
        return DEFAULT_CONTEXT_WINDOW
    
    def should_summarize(self, messages: list[dict], threshold: float = 0.75) -> bool:
        """Check if conversation should be summarized."""
        total = self.count_messages_tokens(messages)
        limit = self.get_context_limit()
        return total / limit >= threshold if limit > 0 else False


def _project_hash(path: Optional[str] = None) -> str:
    """Compute a short hash for project identity (git remote URL or cwd).
    
    Results are cached per path to avoid repeated subprocess calls.
    """
    if path in _PROJECT_HASH_CACHE:
        return _PROJECT_HASH_CACHE[path]

    target = path or os.getcwd()
    try:
        import subprocess
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3, cwd=target,
        )
        if r.returncode == 0:
            git_root = r.stdout.strip()
            rem = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=3, cwd=git_root,
            )
            if rem.returncode == 0:
                identifier = rem.stdout.strip()
            else:
                identifier = git_root
        else:
            identifier = os.path.abspath(target)
    except Exception:
        identifier = os.path.abspath(target)
    
    h = hashlib.sha256(identifier.encode()).hexdigest()[:16]
    _PROJECT_HASH_CACHE[path] = h
    return h


def _state_dir(path: Optional[str] = None) -> Path:
    h = _project_hash(path)
    d = STATE_DIR_BASE / h
    d.mkdir(parents=True, exist_ok=True)
    return d


class SessionManager:
    """Manages per-project session state persisted as JSON."""

    def __init__(self, path: Optional[str] = None):
        self.state_dir = _state_dir(path)
        self.session_file = self.state_dir / "session.json"
        self._data: dict[str, Any] = {}
        self._dirty: bool = False  # Track if unsaved changes exist
        self._token_counter: Optional[TokenCounter] = None

    def _get_token_counter(self, model: str) -> TokenCounter:
        """Get or create token counter for model."""
        if self._token_counter is None or self._token_counter.model != model.lower():
            self._token_counter = TokenCounter(model)
        return self._token_counter

    def load(self) -> dict[str, Any]:
        try:
            if self.session_file.exists():
                with open(self.session_file) as f:
                    self._data = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._data = {}
        self._dirty = False
        return self._data

    def save(self, data: Optional[dict[str, Any]] = None) -> None:
        if data is not None:
            self._data = data
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.session_file, "w") as f:
            json.dump(self._data, f, indent=2)
        self._dirty = False

    def mark_dirty(self) -> None:
        """Mark that data has changed and needs saving."""
        self._dirty = True

    def flush(self) -> None:
        """Save only if there are pending changes."""
        if self._dirty:
            self.save()

    def get_token_usage(self, model: str, messages: list[dict]) -> dict:
        """Get token usage stats for current conversation."""
        counter = self._get_token_counter(model)
        total = counter.count_messages_tokens(messages)
        limit = counter.get_context_limit()
        return {
            "used": total,
            "limit": limit,
            "ratio": total / limit if limit > 0 else 0,
            "remaining": max(0, limit - total),
        }

    def should_summarize(self, model: str, messages: list[dict], threshold: float = 0.75) -> bool:
        """Check if conversation should be summarized."""
        counter = self._get_token_counter(model)
        return counter.should_summarize(messages, threshold)

    def summarize_conversation(self, messages: list[dict], model: str, max_summary_tokens: int = 2000) -> str:
        """Generate a summary of the conversation (placeholder - would use LLM)."""
        # This is a placeholder - in practice, you'd call the LLM to generate a summary
        # For now, return a simple summary
        user_msgs = [m for m in messages if m.get("role") == "user"]
        assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
        
        summary = f"Conversation summary: {len(user_msgs)} user messages, {len(assistant_msgs)} assistant responses."
        if user_msgs:
            summary += f" Last user query: {user_msgs[-1].get('content', '')[:100]}..."
        return summary

    def trim_context(self, messages: list[dict], model: str, reserved_ratio: float = 0.85) -> list[dict]:
        """Trim message list to fit within context window."""
        counter = self._get_token_counter(model)
        ctx_limit = counter.get_context_limit()
        target_max = int(ctx_limit * reserved_ratio)
        
        total = counter.count_messages_tokens(messages)
        if total <= target_max:
            return messages
        
        # Always keep system prompt (index 0) and last 3 exchanges (6 messages)
        trimmed = messages[:1]  # system prompt
        if len(messages) > 7:
            trimmed.append({
                "role": "user", 
                "content": "[Earlier conversation history was trimmed to fit context window. Key context retained below.]"
            })
        trimmed.extend(messages[-6:] if len(messages) > 1 else messages[1:])
        return trimmed

    @property
    def plan(self) -> Optional[str]:
        return self._data.get("plan")

    @plan.setter
    def plan(self, value: Optional[str]) -> None:
        self._data["plan"] = value
        self.mark_dirty()

    @property
    def last_command(self) -> Optional[str]:
        return self._data.get("last_command")

    @last_command.setter
    def last_command(self, value: Optional[str]) -> None:
        self._data["last_command"] = value
        self.mark_dirty()

    @property
    def conversation_summary(self) -> Optional[str]:
        return self._data.get("conversation_summary")

    @conversation_summary.setter
    def conversation_summary(self, value: Optional[str]) -> None:
        self._data["conversation_summary"] = value
        self.mark_dirty()

    @property
    def undo_stack(self) -> list[dict]:
        return self._data.get("undo_stack", [])

    @undo_stack.setter
    def undo_stack(self, value: list[dict]) -> None:
        self._data["undo_stack"] = value
        self.mark_dirty()

    @property
    def memory(self) -> dict[str, Any]:
        return self._data.get("memory", {})

    @memory.setter
    def memory(self, value: dict[str, Any]) -> None:
        self._data["memory"] = value
        self.mark_dirty()

    def clear(self) -> None:
        self._data = {}
        self.save()
