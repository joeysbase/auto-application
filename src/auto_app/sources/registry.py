"""Plugin discovery. Each plugin is one source (docs/step1-collect.md §2.2):

- built-in job sites (LinkedIn, Handshake, Jobright);
- every source class in `plugins/**/*.py` — e.g. `plugins/companies/nvidia.py`, a subclass of a
  platform base (auto_app/platforms) that sets `name`, `company` and `board`.

A class counts as a source when it defines its own `name` (browser plugins: a `SourceSpec`); classes
without one are helpers or bases and are skipped. Plugins are checked when loaded — a plugin that
fails (import error, bad board, duplicate name) is listed in `broken` instead of stopping the app.
"""

import importlib.util
import inspect
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from auto_app.settings import PROJECT_ROOT
from auto_app.sources.api import ApiSource
from auto_app.sources.base import SourceAdapter

log = logging.getLogger(__name__)

PLUGIN_DIR = PROJECT_ROOT / "plugins"
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass
class PluginInfo:
    name: str
    kind: str  # browser | api
    cls: type
    origin: str  # "built-in" or the plugin file path

    @property
    def company(self) -> str | None:
        """The employer's display name for a company source; None for a job site."""
        return getattr(self.cls, "company", None)

    @property
    def is_company(self) -> bool:
        return bool(self.company)

    @property
    def platform(self) -> str | None:
        return getattr(self.cls, "platform", None)

    @property
    def board(self) -> str | None:
        return getattr(self.cls, "board", None)


@dataclass
class Registry:
    plugins: dict[str, PluginInfo] = field(default_factory=dict)
    broken: dict[str, str] = field(default_factory=dict)  # plugin file (or class) → why it wasn't loaded

    def add(self, info: PluginInfo) -> None:
        if not NAME.match(info.name or ""):
            raise ValueError(f"plugin name {info.name!r} must be lowercase letters, digits, '-' or '_'")
        if info.name in self.plugins:
            other = self.plugins[info.name]
            raise ValueError(f"plugin name {info.name!r} defined twice: {other.origin} and {info.origin}")
        if info.kind == "api" and info.platform:
            cls = info.cls
            if not info.company:
                raise ValueError(f"{info.name}: set `company` (the employer's display name, e.g. \"NVIDIA\")")
            cls.board = cls().validate_board(cls.board)  # normalized once, e.g. a Workday careers URL
        self.plugins[info.name] = info

    def add_class(self, cls: type, origin: str = "test") -> PluginInfo:
        info = _info(cls, origin)
        self.add(info)
        return info

    def get(self, name: str) -> PluginInfo:
        if name not in self.plugins:
            known = ", ".join(sorted(self.plugins)) or "none"
            msg = f"no plugin named {name!r} (available: {known})"
            broken = [f"{k}: {v}" for k, v in self.broken.items() if name in Path(k).stem or name in v]
            if broken:
                msg += "\n  plugins that failed to load: " + "; ".join(broken)
            raise KeyError(msg)
        return self.plugins[name]

    def create(self, name: str, base_url: str | None = None):
        info = self.get(name)
        obj = info.cls()
        if info.kind == "browser":
            obj.with_base_url(base_url)
        return obj

    def companies(self) -> dict[str, PluginInfo]:
        """Company (single-employer) sources by name."""
        return {n: i for n, i in self.plugins.items() if i.is_company}


def _info(cls: type, origin: str) -> PluginInfo:
    if issubclass(cls, ApiSource):
        return PluginInfo(cls.__dict__.get("name"), "api", cls, origin)
    return PluginInfo(cls().spec.name, "browser", cls, origin)


def _builtins() -> list[PluginInfo]:
    from auto_app.sources.handshake.adapter import HandshakeAdapter
    from auto_app.sources.jobright.adapter import JobrightAdapter
    from auto_app.sources.linkedin.adapter import LinkedInAdapter

    return [_info(cls, "built-in") for cls in (LinkedInAdapter, HandshakeAdapter, JobrightAdapter)]


def _is_source(obj, mod_name: str) -> bool:
    if not inspect.isclass(obj) or obj.__module__ != mod_name:
        return False  # imported, not defined here
    if issubclass(obj, ApiSource):
        return "name" in obj.__dict__  # bases and helpers have no name of their own
    return issubclass(obj, SourceAdapter) and obj is not SourceAdapter


def _plugin_classes(path: Path, root: Path):
    rel = path.relative_to(root).with_suffix("")
    mod_name = "auto_app_plugins." + ".".join(rel.parts)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return [obj for _, obj in inspect.getmembers(module) if _is_source(obj, mod_name)]


def load_registry(plugin_dir: Path | None = PLUGIN_DIR) -> Registry:
    reg = Registry()
    for info in _builtins():
        reg.add(info)
    if plugin_dir and plugin_dir.exists():
        for path in sorted(plugin_dir.rglob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                classes = _plugin_classes(path, plugin_dir)
            except Exception as e:  # a broken plugin file must not stop the other sources
                reg.broken[str(path)] = f"{type(e).__name__}: {e}"
                log.warning("plugin %s not loaded: %s", path, e)
                continue
            for cls in classes:
                try:
                    reg.add(_info(cls, str(path)))
                except Exception as e:
                    reg.broken[f"{path} ({cls.__name__})"] = str(e)
                    log.warning("plugin %s (%s) not loaded: %s", path, cls.__name__, e)
    return reg


_default: Registry | None = None


def default_registry() -> Registry:
    global _default
    if _default is None:
        _default = load_registry()
    return _default
