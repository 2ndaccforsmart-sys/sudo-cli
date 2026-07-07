"""Memory system for storing key user details/preferences."""
from __future__ import annotations
import json
from pathlib import Path

MEMORY_FILE = Path.home() / ".config" / "sudo" / "memory.json"

from sudo.core.config import load, save

def load_memories() -> list[str]:
    """Load memories from config."""
    cfg = load()
    return cfg.memories

def save_memories(memories: list[str]) -> None:
    """Save memories to config."""
    cfg = load()
    cfg.memories = memories
    save(cfg)

def add_memory(text: str) -> None:
    """Add a new memory."""
    memories = load_memories()
    if text.strip() and text.strip() not in memories:
        memories.append(text.strip())
        save_memories(memories)

def delete_memory(index: int) -> bool:
    """Delete a memory by 1-based index."""
    memories = load_memories()
    if 1 <= index <= len(memories):
        memories.pop(index - 1)
        save_memories(memories)
        return True
    return False

def clear_memories() -> None:
    """Clear all memories."""
    save_memories([])
