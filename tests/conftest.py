import pytest


@pytest.fixture(autouse=True)
def no_notifications(monkeypatch):
    monkeypatch.setattr("auto_app.collect.runner.notify", lambda *a, **k: None)


@pytest.fixture(params=["files", "db"])
def store(request, tmp_path):
    """Each test using `store` runs twice: against the file store and against the database."""
    from auto_app.collect.store import FileStore
    from auto_app.core.db.store import DbStore
    from auto_app.settings import Paths

    if request.param == "files":
        return FileStore(tmp_path / "raw")
    return DbStore(tmp_path / "data" / "auto_app.db", Paths(home=tmp_path))
