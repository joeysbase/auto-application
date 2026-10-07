"""`auto-app web` (docs/web-frontend.md): the JSON API over the TUI's data side, and the localhost guard."""

import pytest
from fastapi.testclient import TestClient

from auto_app.web.app import create_app
from tests.select.test_review import env  # noqa: F401 — env is a fixture

HOST = "127.0.0.1:8765"


@pytest.fixture
def web(env, tmp_path):  # noqa: F811
    db, browser, jobs, _ = env
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "app.js").write_text("js")
    client = TestClient(create_app(browser, dist, port=8765, db_name="auto_app.db"), base_url=f"http://{HOST}")
    return db, jobs, client


def test_meta_has_tabs_with_counts_sources_and_filters(web):
    db, jobs, c = web
    m = c.get("/api/meta").json()
    tabs = {t["id"]: t for t in m["tabs"]}
    assert tabs["recommended"]["count"] == 2 and tabs["recommended"]["title"] == "New recommended"
    assert tabs["un_recommended"]["count"] == 1 and "archived" in tabs
    assert m["sources"] == ["linkedin", "nvidia"] and {f["name"] for f in m["filters"]} == {"ml", "research"}


def test_job_lists_and_their_narrowing(web):
    db, jobs, c = web
    ids = lambda r: {j["job_id"] for j in r.json()}  # noqa: E731
    assert ids(c.get("/api/jobs", params={"tab": "recommended"})) == {jobs["ml"], jobs["analyst"]}
    assert ids(c.get("/api/jobs", params={"tab": "all", "q": "acme"})) == {jobs["analyst"]}
    assert ids(c.get("/api/jobs", params={"tab": "all", "source": "linkedin"})) == {jobs["analyst"]}
    assert ids(c.get("/api/jobs", params={"tab": "all", "company": "acm"})) == {jobs["analyst"]}
    yes = ids(c.get("/api/jobs", params={"tab": "all", "filter": "ml", "verdict": "yes"}))
    no = ids(c.get("/api/jobs", params={"tab": "all", "filter": "ml", "verdict": "no"}))
    assert jobs["ml"] in yes and jobs["sales"] in no and not yes & no
    assert ids(c.get("/api/jobs", params={"tab": "all", "filter": "ml"})) == yes | no  # every job ml judged
    assert ids(c.get("/api/jobs", params={"tab": "all", "posted_within": 3650})) == set(jobs.values())
    row = c.get("/api/jobs", params={"tab": "un_recommended"}).json()[0]
    assert row["title"] == "Sales Lead" and row["state_label"] == "un-recommended" and row["expires"].startswith("in ")
    assert c.get("/api/jobs", params={"tab": "nope"}).status_code == 400


def test_one_job_with_every_verdict(web):
    db, jobs, c = web
    j = c.get(f"/api/jobs/{jobs['ml']}").json()
    assert j["title"] == "ML Engineer" and j["postings"][0]["source"] == "nvidia" and j["description"]
    v = {x["filter"]: x for x in j["verdicts"]}
    assert v["ml"]["recommend"] is True and v["ml"]["reason"] == "strong ML fit" and v["ml"]["lookups"]
    assert v["research"]["recommend"] is False and v["research"]["label"] is None
    assert c.get("/api/jobs/99999").status_code == 404


def test_only_this_mac(web):
    db, jobs, c = web
    assert c.get("/api/meta", headers={"host": "evil.example:8765"}).status_code == 403  # DNS rebinding
    assert c.post("/api/undo", headers={"origin": "https://evil.example"}).status_code == 403  # another site
    assert c.get("/api/meta", headers={"host": "localhost:8765"}).status_code == 200


def test_serves_the_built_app(web):
    db, jobs, c = web
    assert c.get("/").text == "<html>app</html>" and c.get("/jobs/12").text == "<html>app</html>"  # its routes
    assert c.get("/app.js").text == "js"
    assert c.get("/../../etc/passwd").text == "<html>app</html>"  # never outside dist
    assert c.get("/api/nothing").status_code == 404


def test_the_page_in_a_browser(env):  # noqa: F811 — needs `auto-app web --build` (web/dist); skipped otherwise
    import socket
    import threading
    import time

    import uvicorn

    from auto_app.web.build import DIST, built

    if not built():
        pytest.skip("web/dist isn't built (auto-app web --build)")
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    db, browser, jobs, _ = env
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(browser, DIST, port=port), host="127.0.0.1", port=port,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/?tab=recommended")
            page.wait_for_selector(".detail h1")
            assert page.locator("tbody tr").count() == 2
            assert page.inner_text(".tabs .on").startswith("New recommended")
            page.keyboard.press("/")
            page.keyboard.type("acme")
            page.wait_for_function("document.querySelectorAll('tbody tr').length === 1")
            page.wait_for_function("document.querySelector('.detail h1').innerText === 'Data Analyst'")
            assert f"job={jobs['analyst']}" in page.url  # the view lives in the URL
            page.keyboard.press("Escape")
            page.keyboard.press("]")  # the next tab: All recommended
            page.wait_for_function("document.querySelector('.tabs .on').innerText.startsWith('All recommended')")
            assert not errors
            b.close()
    finally:
        server.should_exit = True
