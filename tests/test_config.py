"""Tests for configuration loading/saving."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from sudo.core.config import Config, ProviderConfig, _resolve_key, _parse, save


def test_parse_empty():
    cfg = _parse({})
    assert cfg.provider is None
    assert cfg.api_key is None
    assert cfg.model is None
    assert cfg.base_url is None
    assert cfg.extra_providers == []


def test_parse_full():
    raw = {
        "provider": "groq",
        "api_key": "sk-test",
        "model": "llama-3.3-70b",
        "base_url": "https://custom.api.com/v1",
        "extra_providers": [{"name": "custom", "api_key": "ck-test"}],
    }
    cfg = _parse(raw)
    assert cfg.provider == "groq"
    assert cfg.api_key == "sk-test"
    assert cfg.model == "llama-3.3-70b"
    assert cfg.base_url == "https://custom.api.com/v1"
    assert len(cfg.extra_providers) == 1
    assert cfg.extra_providers[0]["name"] == "custom"


def test_resolve_key_direct():
    assert _resolve_key("sk-key", None) == "sk-key"


def test_resolve_key_env(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "env-val")
    assert _resolve_key(None, "TEST_API_KEY") == "env-val"


def test_resolve_key_fallback():
    assert _resolve_key(None, None) is None


def test_get_provider_config_main():
    cfg = Config(provider="openai", api_key="sk-123", model="gpt-4")
    pc = cfg.get_provider_config()
    assert pc.name == "openai"
    assert pc.api_key == "sk-123"
    assert pc.model == "gpt-4"


def test_get_provider_config_extra():
    cfg = Config(
        provider="main",
        extra_providers=[{"name": "custom", "api_key": "ck-456", "model": "custom-model"}],
    )
    pc = cfg.get_provider_config("custom")
    assert pc.name == "custom"
    assert pc.api_key == "ck-456"
    assert pc.model == "custom-model"


def test_get_provider_config_not_found():
    cfg = Config(provider="openai")
    try:
        cfg.get_provider_config("nonexistent")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_get_provider_config_no_provider():
    cfg = Config()
    try:
        cfg.get_provider_config()
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_save_and_reload(monkeypatch, tmp_path):
    config_dir = tmp_path / ".config" / "sudo"
    monkeypatch.setattr("sudo.core.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("sudo.core.config.CONFIG_YAML", config_dir / "config.yaml")
    monkeypatch.setattr("sudo.core.config.CONFIG_JSON", config_dir / "config.json")

    cfg = Config(provider="groq", api_key="sk-test", model="llama-3.3-70b")
    save(cfg)

    # Should have created YAML
    yaml_path = config_dir / "config.yaml"
    assert yaml_path.exists()
    content = yaml_path.read_text()
    assert "groq" in content


def test_provider_config_dataclass():
    pc = ProviderConfig(name="test", api_key="key", env_key="ENV", model="m", base_url="url")
    assert pc.name == "test"
    assert pc.api_key == "key"
    assert pc.env_key == "ENV"
    assert pc.model == "m"
    assert pc.base_url == "url"


def test_provider_config_defaults():
    pc = ProviderConfig(name="test")
    assert pc.api_key is None
    assert pc.env_key is None
    assert pc.model is None
    assert pc.base_url is None
