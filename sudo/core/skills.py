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
    },
    "obsidian": {
        "description": "Take notes in an Obsidian vault style",
        "system_prompt": "You are an expert Obsidian note-taking assistant. Format all output using Markdown notes with frontmatter metadata, wiki-links [[like this]], and tag structures. Organize the thoughts neatly into headers and callouts."
    },
    "design": {
        "description": "Art and visual layout design assistant",
        "system_prompt": "You are a professional design and art assistant. You specialize in ASCII art, terminal UI layout aesthetics, styling typography, and visual structure design. Help the user create beautiful layouts, arts, and designs."
    }
}


from sudo.core.config import load, save

def load_skills() -> dict[str, dict]:
    """Load skills from config, fallback to defaults."""
    cfg = load()
    merged = dict(DEFAULT_SKILLS)
    merged.update(cfg.skills)
    return merged


def save_skills(skills: dict[str, dict]) -> None:
    """Save custom skills to config."""
    custom_skills = {k: v for k, v in skills.items() if k not in DEFAULT_SKILLS}
    cfg = load()
    cfg.skills = custom_skills
    save(cfg)


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
