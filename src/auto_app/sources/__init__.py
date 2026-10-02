from auto_app.sources.base import SourceAdapter

JOB_SITES = ("linkedin", "jobright", "handshake")  # built-in job-site plugins
SOURCES = JOB_SITES  # backward-compatible name


def get_adapter(name: str, base_url: str | None = None) -> SourceAdapter:
    """A browser plugin instance by name (built-in or from plugins/)."""
    from auto_app.sources.registry import default_registry

    info = default_registry().get(name)
    if info.kind != "browser":
        raise ValueError(f"{name!r} is an API plugin, not a browser site")
    return default_registry().create(name, base_url)
