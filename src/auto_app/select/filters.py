"""Filters (docs/step2-select.md §2): one folder each under `filters/`.

    filters/<name>/
      command.md     the prompt run for every job; `$ARGUMENTS` = the job details
      filter.yaml    job details to send, how the agent runs, which jobs it judges
      parser.py      optional: parse(answer) -> {"fit": bool, "dealbreaker": bool, "reason": str}
      <other files>  anything the command refers to; the agent may read them

A filter's version is a hash of everything that shapes the agent's answer except the job:
command.md, the other files, and filter.yaml's agent-facing settings. Not parser.py (it only re-reads
stored answers) and not applies_to (it only picks which jobs are judged).
"""

import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Callable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from auto_app.settings import PROJECT_ROOT

FILTERS_DIR = PROJECT_ROOT / "filters"
EXAMPLES_DIR = PROJECT_ROOT / "filters.example"

JobDetail = Literal["title", "company", "locations", "workplace_type", "employment_type", "salary", "posted_at",
                    "reposted", "description"]
Tool = Literal["web_search", "web_fetch"]
RESERVED = {"command.md", "filter.yaml", "parser.py"}
MAX_FILE_BYTES = 200_000  # other files are read by the agent; keep them small
CONVENTION = re.compile(r'"?(fit|dealbreaker|recommend)"?\s*"?\s*:', re.I)  # the command mentions the output convention
SPLIT = re.compile(r'"?dealbreaker"?\s*"?\s*:', re.I)  # … the current one, with fit and dealbreaker
FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)


class FilterConfig(BaseModel):
    """filter.yaml"""

    model_config = ConfigDict(extra="forbid")
    # what the agent gets for each job (part of the version)
    job_details: list[JobDetail] = ["title", "company", "locations", "description"]
    max_description_chars: Annotated[int, Field(ge=500)] = 12_000
    # how the agent runs (part of the version)
    model: str = "claude-sonnet-5"
    tools: list[Tool] = ["web_search", "web_fetch"]
    max_turns: Annotated[int, Field(ge=1, le=50)] = 8
    max_tool_calls: Annotated[int, Field(ge=0, le=100)] = 6
    # which jobs it judges (not part of the version)
    applies_to: list[str] | None = None

    def versioned(self) -> dict:
        return self.model_dump(exclude={"applies_to"})


class FilterError(ValueError):
    pass


@dataclass
class Filter:
    name: str
    path: Path
    description: str
    command: str  # the body of command.md (front matter removed)
    config: FilterConfig
    files: dict[str, bytes]  # other files, by path relative to the folder
    parser: Callable[[str], dict] | None = None
    version: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def uses_convention(self) -> bool:
        return self.parser is None


def _read_command(path: Path) -> tuple[str, str]:
    """(description, body) of command.md."""
    text = path.read_text()
    m = FRONT_MATTER.match(text)
    if not m:
        return "", text.strip()
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise FilterError(f"{path.name}: front matter isn't valid YAML ({e})") from None
    if not isinstance(meta, dict):
        raise FilterError(f"{path.name}: front matter must be `key: value` lines")
    extra = sorted(set(meta) - {"description"})
    if extra:
        raise FilterError(f"{path.name}: front matter only takes `description` (got {extra}); "
                          f"model, tools and budgets go in filter.yaml")
    return str(meta.get("description") or ""), text[m.end():].strip()


def _read_config(path: Path) -> FilterConfig:
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise FilterError(f"filter.yaml isn't valid YAML ({e})") from None
    try:
        return FilterConfig.model_validate(data)
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(map(str, x['loc'])) or '(top)'}: {x['msg']}" for x in e.errors())
        raise FilterError(f"filter.yaml: {msgs}") from None


def _load_parser(path: Path, name: str) -> Callable[[str], dict]:
    mod_name = f"auto_app_filters.{name}.parser"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:  # the file is the user's code: report, don't crash
        raise FilterError(f"parser.py doesn't load ({type(e).__name__}: {e})") from None
    fn = getattr(module, "parse", None)
    if not callable(fn):
        raise FilterError("parser.py must define parse(answer: str) -> dict")
    return fn


def _other_files(folder: Path) -> dict[str, bytes]:
    out = {}
    for f in sorted(folder.rglob("*")):
        rel = f.relative_to(folder)
        if not f.is_file() or str(rel) in RESERVED or any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        if f.stat().st_size > MAX_FILE_BYTES:
            raise FilterError(f"{rel}: {f.stat().st_size // 1000} KB; files the agent reads must be under "
                              f"{MAX_FILE_BYTES // 1000} KB")
        out[str(rel)] = f.read_bytes()
    return out


def filter_version(command_md: bytes, files: dict[str, bytes], config: FilterConfig) -> str:
    h = hashlib.sha256()
    h.update(b"command.md\0" + command_md + b"\0")
    for rel, data in sorted(files.items()):
        h.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
    h.update(json.dumps(config.versioned(), sort_keys=True).encode())
    return h.hexdigest()[:12]


def load_filter(folder: Path) -> Filter:
    name = folder.name
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise FilterError(f"folder name {name!r}: use lowercase letters, digits, '-' or '_'")
    cmd_path, cfg_path = folder / "command.md", folder / "filter.yaml"
    for p in (cmd_path, cfg_path):
        if not p.is_file():
            raise FilterError(f"missing {p.name}")
    description, command = _read_command(cmd_path)
    if not command:
        raise FilterError("command.md has no prompt")
    config = _read_config(cfg_path)
    files = _other_files(folder)
    parser = _load_parser(folder / "parser.py", name) if (folder / "parser.py").is_file() else None
    flt = Filter(name, folder, description, command, config, files, parser,
                 version=filter_version(cmd_path.read_bytes(), files, config))
    if parser is None and not CONVENTION.search(command):
        flt.warnings.append('command.md doesn\'t mention the output convention: end the answer with '
                            '{"fit": true | false, "dealbreaker": true | false, "reason": "…"} — or add a parser.py')
    elif parser is None and not SPLIT.search(command):
        flt.warnings.append('command.md uses the older {"recommend": …} answer: ask for {"fit": …, "dealbreaker": …, '
                            '"reason": …} instead, so a dealbreaker and a poor fit are told apart')
    if "$ARGUMENTS" not in command:
        flt.warnings.append("command.md has no $ARGUMENTS: the job details will be appended at the end")
    return flt


@dataclass
class FilterSet:
    filters: dict[str, Filter] = field(default_factory=dict)
    broken: dict[str, str] = field(default_factory=dict)  # folder → why it didn't load

    def get(self, name: str) -> Filter:
        if name in self.filters:
            return self.filters[name]
        if name in self.broken:
            raise KeyError(f"filter {name!r} didn't load: {self.broken[name]}")
        known = ", ".join(sorted(self.filters)) or "none"
        raise KeyError(f"no filter named {name!r} (available: {known})")


def load_filters(directory: Path | None = None) -> FilterSet:
    directory = directory or FILTERS_DIR
    out = FilterSet()
    if not directory.is_dir():
        return out
    for folder in sorted(p for p in directory.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        try:
            out.filters[folder.name] = load_filter(folder)
        except FilterError as e:
            out.broken[folder.name] = str(e)
    return out


def check_applies_to(flt: Filter, cfg, registry) -> list[str]:
    """Errors for `applies_to` entries that name no configured source or search."""
    errors = []
    for entry in flt.config.applies_to or []:
        source, _, search = entry.partition("/")
        if source not in registry.plugins:
            errors.append(f"applies_to: no source {source!r}")
            continue
        if search:
            sc = cfg.sources.get(source)
            if search not in {s.search_name for s in (sc.searches if sc else [])}:
                errors.append(f"applies_to: {source} has no search {search!r} in the config")
    return errors


def render(flt: Filter, job_details: str) -> str:
    """The text the agent is started with: the command, the job details in place of $ARGUMENTS."""
    if "$ARGUMENTS" in flt.command:
        return flt.command.replace("$ARGUMENTS", job_details)
    return f"{flt.command}\n\n{job_details}"
