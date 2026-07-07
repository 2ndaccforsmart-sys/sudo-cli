"""Memory system for storing key user details/preferences."""
from __future__ import annotations
import json
from pathlib import Path

MEMORY_FILE = Path.home() / ".config" / "sudo" / "memory.json"

def load_memories() -> list[str]:
    """Load memories from memory.json."""
    if not MEMORY_FILE.exists():
        return []
    try:
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_memories(memories: list[str]) -> None:
    """Save memories to memory.json."""
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_FILE.write_text(json.dumps(memories, indent=2), encoding="utf-8")
    except Exception:
        pass

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
