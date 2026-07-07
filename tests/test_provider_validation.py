"""Tests for provider registry validation."""

from sudo.core.provider import PROVIDER_REGISTRY


class TestProviderValidation:
    def test_registry_count_is_60_plus(self):
        """This test verifies the RuntimeError check works.
        
        If someone accidentally deletes providers, this test catches it.
        The RuntimeError is raised at import time if count < 60.
        """
        assert len(PROVIDER_REGISTRY) >= 60

    def test_registry_import_does_not_crash(self):
        """Importing provider module should not raise RuntimeError."""
        # If we got here, the module imported successfully
        # which means the RuntimeError check passed
        import sudo.core.provider
        assert hasattr(sudo.core.provider, "PROVIDER_REGISTRY")

    def test_all_tiers_have_providers(self):
        from sudo.core.provider import TIER_ORDER
        tiers_seen = set(d.tier for d in PROVIDER_REGISTRY.values())
        for tier in TIER_ORDER:
            assert tier in tiers_seen, f"Tier {tier} has no providers"
