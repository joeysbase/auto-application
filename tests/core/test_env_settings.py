"""Global settings from `.env` / the environment: precedence, nesting, JSON maps, the template."""

import logging
import os
from pathlib import Path

import pytest

from auto_app.core.config import AppConfig, GLOBAL_FIELDS, load_config, load_env_file

ROOT = Path(__file__).parents[2]


@pytest.fixture
def env(monkeypatch):
    """A private copy of the environment, without any AUTO_APP_ / ANTHROPIC_ variables from the shell."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("AUTO_APP_", "ANTHROPIC_"))}
    monkeypatch.setattr(os, "environ", clean)
    return clean


def test_env_overrides_yaml_with_nesting_and_json(tmp_path, env, caplog):
    yml = tmp_path / "searches.yaml"
    yml.write_text("jobs: {ttl_days: {unfiltered: 20, recommended: 40}}\nretention: {raw_days: 3}\n")
    env.update({"AUTO_APP_JOBS__TTL_DAYS__UNFILTERED": "10", "AUTO_APP_DEBUG_FILES": "true",
                "AUTO_APP_SELECT__PRICES": '{"claude-sonnet-5": {"input_per_mtok": 3, "output_per_mtok": 15}}'})
    with caplog.at_level(logging.WARNING):
        cfg = load_config(yml)
    assert cfg.jobs.ttl_days.unfiltered == 10  # environment wins
    assert cfg.jobs.ttl_days.recommended == 40 and cfg.retention.raw_days == 3  # yaml still used where env is silent
    assert cfg.debug_files is True and cfg.select.prices["claude-sonnet-5"].output_per_mtok == 15
    assert "move them to .env" in caplog.text and "retention, jobs" in caplog.text


def test_shell_variables_win_over_the_env_file(tmp_path, env):
    f = tmp_path / ".env"
    f.write_text("AUTO_APP_JOBS__TTL_DAYS__RECOMMENDED=45  # comment\nAUTO_APP_RETENTION__LOGS_DAYS=5\nANTHROPIC_API_KEY=sk-test\n")
    env["AUTO_APP_RETENTION__LOGS_DAYS"] = "9"
    assert load_env_file(f) and not load_env_file(tmp_path / "missing.env")
    cfg = load_config(None)
    assert cfg.jobs.ttl_days.recommended == 45 and cfg.retention.logs_days == 9
    assert env["ANTHROPIC_API_KEY"] == "sk-test"


def test_bad_values_are_errors(env):
    env["AUTO_APP_JOBS__TTL_DAYS__UNFILTERED"] = "0"
    with pytest.raises(Exception, match="unfiltered"):
        load_config(None)


def test_the_template_gives_the_defaults(env):
    assert load_env_file(ROOT / ".env.example")
    cfg, default = load_config(None), AppConfig()
    for name in GLOBAL_FIELDS:
        assert getattr(cfg, name) == getattr(default, name), name
    assert env.get("ANTHROPIC_API_KEY") == ""  # left for you to fill in


def test_the_cli_loads_the_projects_env_file(tmp_path, env, monkeypatch):
    from typer.testing import CliRunner

    from auto_app import cli

    env["AUTO_APP_HOME"] = str(tmp_path)
    (tmp_path / ".env").write_text("AUTO_APP_DEBUG_FILES=true\n")
    r = CliRunner().invoke(cli.app, ["data", "inspect"])
    assert r.exit_code == 0 and env.get("AUTO_APP_DEBUG_FILES") == "true"
    assert cli._store().__class__.__name__ == "FileStore"  # debug_files from .env, no searches.yaml needed
