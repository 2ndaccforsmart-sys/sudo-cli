"""Tests for the memory management system."""
from pathlib import Path
from unittest.mock import patch

from sudo.core.memory import (
    load_memories,
    save_memories,
    add_memory,
    delete_memory,
    clear_memories
)

def test_memory_crud(tmp_path):
    test_file = tmp_path / "memory.json"
    with patch("sudo.core.memory.MEMORY_FILE", test_file):
        assert load_memories() == []
        
        add_memory("prefers python")
        assert load_memories() == ["prefers python"]
        
        add_memory("prefers python")
        assert load_memories() == ["prefers python"]
        
        add_memory("prefers vscode")
        assert load_memories() == ["prefers python", "prefers vscode"]
        
        assert delete_memory(3) is False
        assert delete_memory(1) is True
        assert load_memories() == ["prefers vscode"]
        
        clear_memories()
        assert load_memories() == []
