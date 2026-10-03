"""--debug-files: files under data/raw/ *instead of* the database."""

from typer.testing import CliRunner

from auto_app.cli import app
from auto_app.collect.plan import Selection
from auto_app.collect.runner import CollectOptions
from auto_app.collect.store import FileStore
from auto_app.core.config import AppConfig, Defaults, SourceConfig, SourceSearch
from auto_app.pipeline import format_pipeline, run_pipeline
from auto_app.settings import Paths
from auto_app.sources.registry import PluginInfo, load_registry
from tests.fakesite.site import FakeAdapter, FakeSite


def test_store_choice(tmp_path, monkeypatch):
    import auto_app.cli as cli

    monkeypatch.setenv("AUTO_APP_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_debug_files", True)
    s = cli._store()
    assert isinstance(s, FileStore) and s.root == tmp_path / "data" / "raw"
    monkeypatch.setattr(cli, "_debug_files", False)
    assert not isinstance(cli._store(), FileStore)  # the database
    assert isinstance(cli._store(cfg=AppConfig(debug_files=True)), FileStore)  # config setting


async def test_run_in_file_mode_writes_no_database(tmp_path):
    site = FakeSite()
    cfg = AppConfig(defaults=Defaults(early_stop_known_streak=2),
                    sources={"fake": SourceConfig(searches=[SourceSearch(name="ml", keywords=["ml"])])})
    reg = load_registry()
    reg.add(PluginInfo("fake", "browser", FakeAdapter, "test"))
    paths = Paths(home=tmp_path)
    files = FileStore(paths.raw)
    opts = CollectOptions(headless=True, fast=True, context_hook=site.install)
    res = await run_pipeline(cfg, paths, files, opts, Selection(), reg, do_purge=True)
    assert len(list(files.iter_postings())) == 5
    assert files.get_normalized(next(files.iter_postings()))  # parsed + normalized as files
    assert res.created == {} and len(res.plan.new_jobs) == 5  # dedup previewed, nothing written
    assert res.purge is None
    assert not paths.db.exists()  # nothing written to the database
    assert "debug files only" in format_pipeline(res, files)


def test_commands_that_need_the_database_refuse_clearly(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_APP_HOME", str(tmp_path))
    r = CliRunner()
    for args in (["job", "list"], ["process", "dedup"], ["data", "purge"], ["data", "import"], ["data", "export", "x", "1"]):
        out = r.invoke(app, ["--debug-files", *args])
        assert out.exit_code == 2 and "needs the database" in out.output, args
    assert r.invoke(app, ["--debug-files", "process", "dedup", "--preview"]).exit_code == 0
    assert not (tmp_path / "data" / "auto_app.db").exists()
