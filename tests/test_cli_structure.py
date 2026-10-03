"""The command tree: groups and subcommands; old flat names are gone; a bare group shows its help."""

import pytest
from typer.testing import CliRunner

from auto_app.cli import app

TREE = {
    "source": ["list", "show", "add", "login", "health", "plan"],
    "collect": ["run", "probe", "stats"],
    "process": ["parse", "normalize", "dedup"],
    "job": ["list", "show", "mark"],
    "filter": ["list", "show", "run", "why", "reparse"],
    "data": ["inspect", "export", "import", "purge"],
    "schedule": ["install", "show", "remove"],
}


def test_every_subcommand_exists_and_has_help():
    runner = CliRunner()
    for group, subs in TREE.items():
        for sub in subs:
            r = runner.invoke(app, [group, sub, "--help"])
            assert r.exit_code == 0, (group, sub, r.output)
    assert runner.invoke(app, ["run", "--help"]).exit_code == 0


@pytest.mark.parametrize("group", list(TREE))
def test_a_bare_group_shows_its_help_in_workflow_order(group):
    r = CliRunner().invoke(app, [group])
    listed = [line.split()[1] for line in r.output.splitlines() if line.startswith("│ ") and len(line.split()) > 2
              and line.split()[1] in TREE[group]]
    assert listed == TREE[group] and "Usage:" in r.output


@pytest.mark.parametrize("old", ["login", "plugins", "add-company", "plan", "sources", "probe", "stats", "parse",
                                 "normalize", "dedup", "jobs", "mark", "filters", "select", "why", "inspect", "export",
                                 "import-files", "purge"])
def test_old_flat_names_are_gone(old):
    r = CliRunner().invoke(app, [old])
    assert r.exit_code != 0 and "No such command" in r.output


def test_legacy_option_aliases_are_gone():
    assert "No such option" in CliRunner().invoke(app, ["collect", "run", "-c", "nvidia"]).output
    assert "No such option" in CliRunner().invoke(app, ["source", "plan", "--site", "linkedin"]).output
