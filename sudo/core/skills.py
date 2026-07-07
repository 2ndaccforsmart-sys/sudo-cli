"""Skills management system for custom agent behaviors."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

SKILLS_FILE = Path.home() / ".config" / "sudo" / "skills.json"

DEFAULT_SKILLS = {
    "goal": {
        "description": "Run until the specified goal is completely finished",
        "system_prompt": "You are an autonomous agent focused on completing the user's goal. Break down the goal into steps, execute them methodically, verify results, and do not stop until the goal is fully accomplished."
    },
    "schedule": {
        "description": "Run an instruction on a recurring schedule or as a one-time timer",
        "system_prompt": "You are an assistant specialized in scheduling background tasks. Formulate standard cron expressions or timer intervals as requested by the user, and write helper scripts if needed to execute them."
    },
    "grill-me": {
        "description": "Interview me to align on a plan",
        "system_prompt": "You are an expert interviewer. Do not solve the user's problem immediately. Instead, ask clarifying questions, present design options, and grill the user to completely align on a comprehensive plan before writing any code."
    }
}


def load_skills() -> dict[str, dict]:
    """Load skills from skills.json, fallback to defaults."""
    if not SKILLS_FILE.exists():
        SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        save_skills(DEFAULT_SKILLS)
        return dict(DEFAULT_SKILLS)
    try:
        data = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_SKILLS)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULT_SKILLS)


def save_skills(skills: dict[str, dict]) -> None:
    """Save skills to skills.json."""
    try:
        SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SKILLS_FILE.write_text(json.dumps(skills, indent=2), encoding="utf-8")
    except Exception:
        pass


def add_skill(name: str, description: str, system_prompt: str) -> None:
    """Add or overwrite a skill."""
    skills = load_skills()
    skills[name.strip().lower()] = {
        "description": description.strip(),
        "system_prompt": system_prompt.strip()
    }
    save_skills(skills)


def get_skill(name: str) -> Optional[dict]:
    """Get a skill definition by name."""
    skills = load_skills()
    return skills.get(name.strip().lower())


def delete_skill(name: str) -> bool:
    """Delete a skill by name. Built-in default skills cannot be deleted."""
    skills = load_skills()
    key = name.strip().lower()
    if key in DEFAULT_SKILLS:
        return False
    if key in skills:
        del skills[key]
        save_skills(skills)
        return True
    return False
