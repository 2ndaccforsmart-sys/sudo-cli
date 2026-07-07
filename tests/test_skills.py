"""Tests for the skills management system."""
import json
from pathlib import Path
from unittest.mock import patch

from sudo.core.skills import (
    load_skills,
    save_skills,
    add_skill,
    get_skill,
    delete_skill,
    DEFAULT_SKILLS,
)

def test_load_skills_default(tmp_path):
    test_file = tmp_path / "skills.json"
    with patch("sudo.core.skills.SKILLS_FILE", test_file):
        skills = load_skills()
        assert "goal" in skills
        assert "schedule" in skills
        assert "grill-me" in skills
        assert test_file.exists()


def test_add_delete_skill(tmp_path):
    test_file = tmp_path / "skills.json"
    with patch("sudo.core.skills.SKILLS_FILE", test_file):
        # Add custom skill
        add_skill("refactor", "Refactor code", "You are a refactoring assistant.")
        skills = load_skills()
        assert "refactor" in skills
        assert skills["refactor"]["description"] == "Refactor code"
        
        # Get skill
        skill = get_skill("refactor")
        assert skill is not None
        assert skill["system_prompt"] == "You are a refactoring assistant."
        
        # Try deleting default skill (should fail)
        assert delete_skill("goal") is False
        
        # Delete custom skill
        assert delete_skill("refactor") is True
        assert "refactor" not in load_skills()
