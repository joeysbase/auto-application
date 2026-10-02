"""Shared test setup: a registry with the built-in job sites and the test company plugins."""

from pathlib import Path

from auto_app.sources.registry import Registry, load_registry

TEST_PLUGINS = Path(__file__).parent / "plugins"


def test_registry() -> Registry:
    return load_registry(TEST_PLUGINS)


test_registry.__test__ = False  # not a test
