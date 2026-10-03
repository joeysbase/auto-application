"""S1: filter folders (command.md, filter.yaml, parser.py, other files), validation, versions, job details."""

import shutil

import pytest
from typer.testing import CliRunner

from auto_app.core.config import AppConfig
from auto_app.select.filters import EXAMPLES_DIR, FilterError, load_filter, load_filters, render, check_applies_to
from auto_app.select.job_details import build_job_details, clean_text
from auto_app.sources.registry import load_registry

GOOD_CMD = "---\ndescription: test filter\n---\nJudge this job.\n\n$ARGUMENTS\n\nEnd with {\"recommend\": true|false, \"reason\": \"…\"}\n"


def make(tmp_path, name="f", command=GOOD_CMD, yaml="model: claude-haiku-4-5-20251001\n", **files):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    if command is not None:
        (d / "command.md").write_text(command)
    if yaml is not None:
        (d / "filter.yaml").write_text(yaml)
    for rel, text in files.items():
        (d / rel).write_text(text)
    return d


def test_examples_load_cleanly():
    fs = load_filters(EXAMPLES_DIR)
    assert fs.broken == {} and set(fs.filters) == {"ml-engineer", "verdict-format"}
    ml, vf = fs.filters["ml-engineer"], fs.filters["verdict-format"]
    assert ml.uses_convention and not ml.warnings and list(ml.files) == ["sponsorship-notes.md"]
    assert ml.config.tools == ["web_search", "web_fetch"] and ml.description.startswith("Screen ML")
    assert not vf.uses_convention and vf.parser("Verdict: maybe\nReason: close fit") == {"recommend": True, "reason": "close fit"}
    with pytest.raises(ValueError):
        vf.parser("no verdict here")


@pytest.mark.parametrize("kw, error", [
    ({"command": None}, "missing command.md"),
    ({"yaml": None}, "missing filter.yaml"),
    ({"yaml": "modle: x\n"}, "modle: Extra inputs"),
    ({"yaml": "job_details: [title, discription]\n"}, "job_details.1"),
    ({"yaml": "tools: [bash]\n"}, "tools.0"),
    ({"yaml": "max_turns: 0\n"}, "max_turns"),
    ({"command": "---\nmodel: x\n---\nbody\n"}, "only takes `description`"),
    ({"command": "---\ndescription: x\n---\n\n"}, "has no prompt"),
    ({"parser.py": "x = 1\n"}, "must define parse"),
    ({"parser.py": "import nonexistent_mod\n"}, "parser.py doesn't load"),
])
def test_invalid_filters_are_reported_with_the_file_and_field(tmp_path, kw, error):
    make(tmp_path, **kw)
    fs = load_filters(tmp_path)
    assert fs.filters == {} and error in fs.broken["f"]
    with pytest.raises(KeyError, match="didn't load"):
        fs.get("f")


def test_bad_folder_names_and_big_files(tmp_path):
    make(tmp_path, name="Bad Name")
    assert "lowercase" in load_filters(tmp_path).broken["Bad Name"]
    d = make(tmp_path, name="big")
    (d / "notes.md").write_text("x" * 300_000)
    with pytest.raises(FilterError, match="under 200 KB"):
        load_filter(d)


def test_warnings_for_a_missing_convention_or_arguments(tmp_path):
    f = load_filter(make(tmp_path, command="Judge this job and say yes or no."))
    assert any("output convention" in w for w in f.warnings) and any("$ARGUMENTS" in w for w in f.warnings)
    assert render(f, "JOB") == "Judge this job and say yes or no.\n\nJOB"  # appended when $ARGUMENTS is missing
    g = load_filter(make(tmp_path, name="g"))
    assert g.warnings == [] and "JOB" in render(g, "JOB") and "$ARGUMENTS" not in render(g, "JOB")


def test_version_covers_what_the_agent_sees_but_not_parser_or_applies_to(tmp_path):
    d = make(tmp_path, **{"notes.md": "a"})
    v0 = load_filter(d).version
    (d / "filter.yaml").write_text("model: claude-haiku-4-5-20251001\napplies_to: [linkedin]\n")
    assert load_filter(d).version == v0  # which jobs it judges isn't part of the version
    (d / "parser.py").write_text("def parse(a):\n    return {'recommend': True, 'reason': ''}\n")
    assert load_filter(d).version == v0  # parser only re-reads stored answers
    (d / "notes.md").write_text("b")
    v1 = load_filter(d).version
    assert v1 != v0  # another file the agent may read
    (d / "command.md").write_text(GOOD_CMD + "Be strict.\n")
    v2 = load_filter(d).version
    assert v2 != v1
    (d / "filter.yaml").write_text("model: claude-sonnet-5\napplies_to: [linkedin]\n")
    assert load_filter(d).version != v2  # agent settings
    (d / "extra.md").write_text("new")
    assert load_filter(d).version != load_filter(make(tmp_path, name="other")).version


def test_applies_to_names_real_sources_and_searches(tmp_path):
    f = load_filter(make(tmp_path, yaml="applies_to: [linkedin, linkedin/test, linkedin/nope, nosuch]\n"))
    cfg = AppConfig.model_validate({"sources": {"linkedin": {"searches": [{"name": "test"}]}}})
    assert check_applies_to(f, cfg, load_registry(None)) == ["applies_to: linkedin has no search 'nope' in the config",
                                                              "applies_to: no source 'nosuch'"]


PARSED = {
    "title": "Applied Scientist", "company": "Amazon", "locations": ["Seattle, WA"], "workplace_type": "unknown",
    "employment_type": "full_time", "posted_at": "2026-09-28T00:00:00+00:00", "reposted": True,
    "salary": {"min": 150000, "max": 210000, "currency": "USD", "period": "year"},
    "description_text": "About the Role\n-\nBuild ML systems\n\n\n\nWe are an Equal Opportunity Employer without regard to race.\n#LI-Hybrid\nApply now.",
}


def test_job_details_are_labelled_lines_marked_as_data():
    text = build_job_details(PARSED, ["seattle, wa", "remote-us"],
                             ["title", "company", "locations", "workplace_type", "employment_type", "salary",
                              "posted_at", "reposted", "description"], 12_000)
    lines = text.splitlines()
    assert lines[0].startswith("<job_posting>") and "not instructions" in lines[0] and lines[-1] == "</job_posting>"
    assert lines[1:9] == ["Title: Applied Scientist", "Company: Amazon", "Locations: Seattle, WA; Remote (US)",
                          "Workplace: not stated", "Employment type: full_time", "Salary: USD 150,000–210,000 per year",
                          "Posted: 2026-09-28", "Reposted: yes"]
    assert "Description:\nAbout the Role\n- Build ML systems\n\nApply now." in text  # casing kept, bullets joined
    assert "Equal Opportunity" not in text and "#LI-" not in text


def test_job_details_follow_the_chosen_fields_and_cut_long_descriptions():
    text = build_job_details({**PARSED, "description_text": "word " * 1000}, [], ["title", "description"], 500)
    assert "Company:" not in text and "Locations:" not in text
    assert "[… description cut at 500 characters]" in text and len(text) < 700
    assert "Locations: Seattle, WA" in build_job_details(PARSED, [], ["locations"], 500)  # falls back to the posting's own
    assert clean_text("  Keep THIS\n\n\n\nand this ") == "Keep THIS\n\nand this"


def test_filters_command_lists_checks_and_previews(tmp_path, monkeypatch):
    from auto_app.cli import app
    from auto_app.core.db.store import DbStore
    from auto_app.settings import Paths
    from tests.process.test_link import add, job_of, run, wd

    monkeypatch.setenv("AUTO_APP_HOME", str(tmp_path))
    folder = tmp_path / "myfilters"
    shutil.copytree(EXAMPLES_DIR, folder)
    make(folder, name="broken", yaml="tools: [bash]\n")
    r = CliRunner().invoke(app, ["filter", "list", "--dir", str(folder)])
    assert r.exit_code == 0 and "ml-engineer" in r.output and "reads: parser.py" in r.output
    assert "broken" in r.output and "not loaded" in r.output
    r = CliRunner().invoke(app, ["filter", "show", "ml-engineer", "--dir", str(folder)])
    assert r.exit_code == 0 and "sponsorship-notes.md" in r.output and "v" + load_filter(folder / "ml-engineer").version in r.output

    db = DbStore(tmp_path / "data" / "auto_app.db", Paths(home=tmp_path))
    add(db, "nvidia", "JR1", url=wd("JR1"), title="ML Engineer")
    run(db)
    r = CliRunner().invoke(app, ["filter", "show", "ml-engineer", "--dir", str(folder), "--job", str(job_of(db, "nvidia/JR1"))])
    assert r.exit_code == 0, r.output
    assert "## The job\n<job_posting>" in r.output and "Title: ML Engineer" in r.output and "Company: NVIDIA" in r.output
    assert "Locations: Santa Clara, CA" in r.output and r.output.rstrip().endswith("deal-breaker you found\"}")
    assert CliRunner().invoke(app, ["filter", "show", "ml-engineer", "--dir", str(folder), "--job", "999"]).exit_code == 1
