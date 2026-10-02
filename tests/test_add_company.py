"""`auto-app add-company` output: the generated plugin file loads as a source, and the config stub keeps the config valid."""

from auto_app.cli import _append_source_stub, company_plugin_source
from auto_app.collect.plan import build_tasks
from auto_app.core.config import load_config
from auto_app.sources.registry import load_registry


def test_generated_plugin_is_a_company_source(tmp_path):
    (tmp_path / "companies").mkdir()
    board = "https://acme.wd1.myworkdayjobs.com/en-US/Acme_Careers"
    (tmp_path / "companies" / "acme.py").write_text(company_plugin_source("acme", "ACME \"Labs\"", "workday", board))
    reg = load_registry(tmp_path)
    assert not reg.broken
    info = reg.get("acme")
    assert info.is_company and info.company == 'ACME "Labs"' and info.platform == "workday"
    assert info.board == "https://acme.wd1.myworkdayjobs.com/Acme_Careers"


def test_source_stub_goes_into_the_sources_block(tmp_path):
    cfg_file = tmp_path / "searches.yaml"
    cfg_file.write_text(
        "defaults:\n  posted_within_days: 1\n\n"
        "sources:\n  linkedin:\n    searches:\n      - name: ml\n        keywords: [x]\n\n"
        "  # handshake: …\n\n"
        "run_window: { start: \"08:00\", end: \"23:00\" }\n"
    )
    _append_source_stub(cfg_file, "acme", load_config(cfg_file))
    text = cfg_file.read_text()
    assert text.index("  acme:") < text.index("run_window:")
    assert "#   - from: linkedin/ml" in text
    cfg = load_config(cfg_file)
    assert cfg.sources["acme"].searches == [] and cfg.run_window.start == "08:00"
    (tmp_path / "plugins" / "companies").mkdir(parents=True)
    (tmp_path / "plugins" / "companies" / "acme.py").write_text(company_plugin_source("acme", "Acme", "ashby", "acme"))
    reg = load_registry(tmp_path / "plugins")
    assert [t.source for t in build_tasks(cfg, reg)] == ["linkedin"]  # the stub doesn't run yet

    empty = tmp_path / "empty.yaml"
    empty.write_text("defaults: {}\n")
    _append_source_stub(empty, "acme", load_config(empty))
    assert load_config(empty).sources["acme"].searches == []
