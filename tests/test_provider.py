"""Tests for provider registry, definitions, and factory."""

import os

from sudo.core.provider import (
    PROVIDER_REGISTRY,
    ProviderDef,
    ProviderFactory,
    BaseProvider,
    TIER_ORDER,
    TIER_LABELS,
)


def test_registry_has_60_plus_providers():
    assert len(PROVIDER_REGISTRY) >= 60


def test_well_known_providers_present():
    for name in ("openai", "anthropic", "groq", "ollama", "deepseek"):
        assert name in PROVIDER_REGISTRY, f"Missing provider: {name}"


def test_all_tiers_present():
    tiers_seen = set()
    for defn in PROVIDER_REGISTRY.values():
        tiers_seen.add(defn.tier)
    for t in TIER_ORDER:
        assert t in tiers_seen, f"Missing tier {t}"


def test_tier_labels_all_present():
    for t in TIER_ORDER:
        assert t in TIER_LABELS


def test_provider_has_required_fields():
    for name, defn in PROVIDER_REGISTRY.items():
        assert defn.name
        assert defn.display
        assert defn.api_type in ("openai", "anthropic", "google")
        assert defn.base_url
        assert defn.env_key
        assert defn.docs_url
        assert defn.website
        assert defn.default_model
        assert defn.tier in TIER_ORDER


def test_provider_def_api_type():
    assert PROVIDER_REGISTRY["openai"].api_type == "openai"
    assert PROVIDER_REGISTRY["anthropic"].api_type == "anthropic"
    assert PROVIDER_REGISTRY["google/gemini"].api_type == "google"


def test_factory_creates_openai_provider():
    defn = PROVIDER_REGISTRY["groq"]
    provider = ProviderFactory.create("groq", api_key="test-key")
    assert provider.defn.name == "groq"
    assert provider.api_key == "test-key"
    assert provider.model == defn.default_model
    from sudo.core.provider import OpenAICompatibleProvider
    assert isinstance(provider, OpenAICompatibleProvider)


def test_factory_creates_anthropic_provider():
    provider = ProviderFactory.create("anthropic", api_key="test-key")
    from sudo.core.provider import AnthropicProvider
    assert isinstance(provider, AnthropicProvider)


def test_factory_creates_gemini_provider():
    provider = ProviderFactory.create("google/gemini", api_key="test-key")
    from sudo.core.provider import GeminiProvider
    assert isinstance(provider, GeminiProvider)


def test_factory_raises_on_unknown_provider():
    try:
        ProviderFactory.create("nonexistent_provider", api_key="test")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_factory_raises_on_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        ProviderFactory.create("openai")
        assert False, "Should have raised ValueError for missing key"
    except ValueError:
        pass


def test_factory_uses_env_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "env-groq-key")
    provider = ProviderFactory.create("groq")
    assert provider.api_key == "env-groq-key"


def test_provider_uses_custom_model():
    provider = ProviderFactory.create("groq", api_key="test", model="custom-model")
    assert provider.model == "custom-model"


def test_provider_uses_custom_base_url():
    provider = ProviderFactory.create("groq", api_key="test", base_url="https://custom.api.com/v1")
    assert provider.base_url == "https://custom.api.com/v1"


def test_base_provider_is_abstract():
    try:
        BaseProvider(PROVIDER_REGISTRY["groq"], "test")
        assert False, "Should not instantiate abstract class"
    except TypeError:
        pass


def test_free_tier_providers_have_free_flag():
    for name, defn in PROVIDER_REGISTRY.items():
        if defn.tier == "S":
            assert defn.free_tier is True, f"{name} should be free tier"


def test_ollama_default_model():
    assert PROVIDER_REGISTRY["ollama"].default_model == "llama3.2"


def test_tier_z_providers():
    z_providers = [n for n, d in PROVIDER_REGISTRY.items() if d.tier == "Z"]
    assert len(z_providers) >= 8, f"Expected at least 8 tier Z providers, got {len(z_providers)}"


def test_tier_l_providers():
    l_providers = [n for n, d in PROVIDER_REGISTRY.items() if d.tier == "L"]
    assert len(l_providers) >= 8, f"Expected at least 8 local providers, got {len(l_providers)}"
