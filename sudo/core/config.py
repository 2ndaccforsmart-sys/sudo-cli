"""Configuration loader for sudo CLI.

Loads ~/.config/sudo/config.yaml with fallback to config.json.
"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None

CONFIG_FILE = Path.home() / "sudo-config.json"
STATE_DIR_BASE = Path.home() / ".config" / "sudo" / "state"


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""
    name: str
    api_key: Optional[str] = None
    env_key: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None


@dataclass
class Config:
    """Global sudo configuration."""
    provider: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    extra_providers: list[dict] = field(default_factory=list)
    personality: Optional[str] = None
    always_on_skills: list[str] = field(default_factory=list)
    show_reasoning: bool = False
    yolo_mode: bool = False
    gcs_bucket: Optional[str] = None
    gcs_key_file: Optional[str] = None
    mcp_servers: dict = field(default_factory=dict)
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_enabled: bool = False
    skills: dict = field(default_factory=dict)
    memories: list[str] = field(default_factory=list)

    def get_provider_config(self, name: Optional[str] = None) -> ProviderConfig:
        """Resolve provider config, consulting env vars for API keys.

        Priority:
          1. Extra providers list (for custom endpoints)
          2. Main provider fields in config
          3. Environment variable fallback
        """
        target = name or self.provider
        if not target:
            raise ValueError("No provider configured. Use 'sudo provider set <name>'")

        # Check extra providers first
        for ep in self.extra_providers:
            if ep.get("name") == target:
                return ProviderConfig(
                    name=target,
                    api_key=_resolve_key(ep.get("api_key"), ep.get("env_key")),
                    model=ep.get("model"),
                    base_url=ep.get("base_url"),
                    env_key=ep.get("env_key"),
                )

        # Main config
        if target == self.provider or name is None:
            return ProviderConfig(
                name=target,
                api_key=_resolve_key(self.api_key, None),
                model=self.model,
                base_url=self.base_url,
            )

        raise ValueError(f"Provider '{target}' not found in config")


def _resolve_key(api_key: Optional[str], env_key: Optional[str]) -> Optional[str]:
    """Resolve API key: direct value > env var > None."""
    if api_key:
        return api_key
    if env_key:
        return os.environ.get(env_key)
    return None


def migrate_old_config():
    old_dir = Path.home() / ".config" / "sudo"
    if CONFIG_FILE.exists():
        return
        
    if old_dir.exists():
        import json
        
        data = {
            "provider": None,
            "api_key": None,
            "model": None,
            "base_url": None,
            "extra_providers": [],
            "personality": None,
            "always_on_skills": [],
            "show_reasoning": False,
            "yolo_mode": False,
            "gcs_bucket": None,
            "gcs_key_file": None,
            "telegram_token": None,
            "telegram_chat_id": None,
            "telegram_enabled": False,
            "mcp_servers": {},
            "skills": {},
            "memories": []
        }
        
        old_yaml = old_dir / "config.yaml"
        old_json = old_dir / "config.json"
        
        if old_yaml.exists():
            try:
                if yaml is not None:
                    raw = yaml.safe_load(old_yaml.read_text()) or {}
                    for k, v in raw.items():
                        if k in data:
                            data[k] = v
            except Exception:
                pass
        elif old_json.exists():
            try:
                raw = json.loads(old_json.read_text())
                for k, v in raw.items():
                    if k in data:
                        data[k] = v
            except Exception:
                pass
                
        old_mcp = old_dir / "mcp.json"
        if old_mcp.exists():
            try:
                raw = json.loads(old_mcp.read_text())
                data["mcp_servers"] = raw.get("mcpServers", {})
            except Exception:
                pass
                
        old_skills = old_dir / "skills.json"
        if old_skills.exists():
            try:
                raw = json.loads(old_skills.read_text())
                data["skills"] = raw
            except Exception:
                pass
                
        old_mem = old_dir / "memory.json"
        if old_mem.exists():
            try:
                raw = json.loads(old_mem.read_text())
                data["memories"] = raw
            except Exception:
                pass
                
        CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load() -> Config:
    """Load config from ~/sudo-config.json, with backward-compatible migration."""
    migrate_old_config()
    if not CONFIG_FILE.exists():
        return Config()
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return _parse(raw)
    except Exception:
        return Config()


def _parse(raw: dict) -> Config:
    return Config(
        provider=raw.get("provider"),
        api_key=raw.get("api_key"),
        model=raw.get("model"),
        base_url=raw.get("base_url"),
        extra_providers=raw.get("extra_providers", []),
        personality=raw.get("personality"),
        always_on_skills=raw.get("always_on_skills", []),
        show_reasoning=raw.get("show_reasoning", False),
        yolo_mode=raw.get("yolo_mode", False),
        gcs_bucket=raw.get("gcs_bucket"),
        gcs_key_file=raw.get("gcs_key_file"),
        mcp_servers=raw.get("mcp_servers", {}),
        telegram_token=raw.get("telegram_token"),
        telegram_chat_id=raw.get("telegram_chat_id"),
        telegram_enabled=raw.get("telegram_enabled", False),
        skills=raw.get("skills", {}),
        memories=raw.get("memories", []),
    )


def save(cfg: Config) -> None:
    """Persist config to ~/sudo-config.json."""
    data = {
        "provider": cfg.provider,
        "api_key": cfg.api_key,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "extra_providers": cfg.extra_providers,
        "personality": cfg.personality,
        "always_on_skills": cfg.always_on_skills,
        "show_reasoning": cfg.show_reasoning,
        "yolo_mode": cfg.yolo_mode,
        "gcs_bucket": cfg.gcs_bucket,
        "gcs_key_file": cfg.gcs_key_file,
        "mcp_servers": cfg.mcp_servers,
        "telegram_token": cfg.telegram_token,
        "telegram_chat_id": cfg.telegram_chat_id,
        "telegram_enabled": cfg.telegram_enabled,
        "skills": cfg.skills,
        "memories": cfg.memories,
    }
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
