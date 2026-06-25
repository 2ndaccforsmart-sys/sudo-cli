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

CONFIG_DIR = Path.home() / ".config" / "sudo"
CONFIG_YAML = CONFIG_DIR / "config.yaml"
CONFIG_JSON = CONFIG_DIR / "config.json"
STATE_DIR_BASE = CONFIG_DIR / "state"


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


def load() -> Config:
    """Load config from YAML (preferred) or JSON."""
    if CONFIG_YAML.exists():
        return _load_yaml(CONFIG_YAML)
    if CONFIG_JSON.exists():
        return _load_json(CONFIG_JSON)
    return Config()


def _load_yaml(path: Path) -> Config:
    if yaml is None:
        raise ImportError("PyYAML is required. Run: pip install pyyaml")
    raw = yaml.safe_load(path.read_text()) or {}
    return _parse(raw)


def _load_json(path: Path) -> Config:
    raw = json.loads(path.read_text())
    return _parse(raw)


def _parse(raw: dict) -> Config:
    return Config(
        provider=raw.get("provider"),
        api_key=raw.get("api_key"),
        model=raw.get("model"),
        base_url=raw.get("base_url"),
        extra_providers=raw.get("extra_providers", []),
    )


def save(cfg: Config) -> None:
    """Persist config as YAML."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "provider": cfg.provider,
        "api_key": cfg.api_key,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "extra_providers": cfg.extra_providers,
    }
    if yaml is not None:
        CONFIG_YAML.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False))
    else:
        CONFIG_JSON.write_text(json.dumps(data, indent=2))
