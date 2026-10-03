"""The DeepSeek runner: OpenAI-style tool loop, our own web_fetch (LinkedIn / private addresses refused), routing."""

import json
import shutil

import httpx
import pytest
from typer.testing import CliRunner

from auto_app.select import runner_deepseek as R
from auto_app.select.filters import EXAMPLES_DIR, load_filter
from auto_app.select.parse import parse_answer


@pytest.fixture
def ds(tmp_path):
    """The ml-engineer example switched to deepseek-chat (tools: web_search, web_fetch; sponsorship-notes.md)."""
    d = tmp_path / "ds"
    shutil.copytree(EXAMPLES_DIR / "ml-engineer", d)
    y = d / "filter.yaml"
    y.write_text(y.read_text().replace("model: claude-sonnet-5", "model: deepseek-chat"))
    return load_filter(d)


def chat(content=None, tool_calls=None, finish="stop", pt=200, ct=30):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
        msg["reasoning_content"] = "internal thoughts"  # must not be sent back
    return {"choices": [{"message": msg, "finish_reason": finish}], "usage": {"prompt_tokens": pt, "completion_tokens": ct}}


def call(id_, name, args):
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class API:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        reply = self.replies.pop(0)
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)


def test_request_shape_and_answer(ds):
    api = API([chat('Good fit.\n{"recommend": true, "reason": "ML systems"}')])
    res = R.run_deepseek(ds, "PROMPT", "sk-ds", http=httpx.Client(transport=httpx.MockTransport(api)))
    req = api.requests[0]
    body = json.loads(req.content)
    assert str(req.url) == "https://api.deepseek.com/chat/completions" and req.headers["authorization"] == "Bearer sk-ds"
    assert body["model"] == "deepseek-chat" and [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][1]["content"] == "PROMPT" and body["tool_choice"] == "auto"
    assert {t["function"]["name"] for t in body["tools"]} == {"web_fetch", "read_file"}  # no web_search
    assert res.status == "ok" and parse_answer(ds, res.answer).output.recommend is True
    assert (res.input_tokens, res.output_tokens, res.turns) == (200, 30, 1)
    assert "web_search isn't available" in res.trace[0]["note"]


def test_tool_loop_read_file_and_web_fetch(ds):
    api = API([chat(tool_calls=[call("a", "read_file", {"path": "sponsorship-notes.md"}),
                                call("b", "web_fetch", {"url": "https://h1b.example/acme"}),
                                call("c", "read_file", {"path": "../../.env"})], finish="tool_calls"),
               chat('{"recommend": false, "reason": "no sponsorship history"}')])
    fetched = []
    res = R.run_deepseek(ds, "P", "k", http=httpx.Client(transport=httpx.MockTransport(api)),
                         fetch=lambda url, http: fetched.append(url) or "Acme filed 0 H-1B petitions")
    second = json.loads(api.requests[1].content)["messages"]
    assistant = second[2]
    assert assistant["role"] == "assistant" and "reasoning_content" not in assistant and len(assistant["tool_calls"]) == 3
    tools = {m["tool_call_id"]: m["content"] for m in second if m["role"] == "tool"}
    assert "Checking visa sponsorship" in tools["a"] and tools["b"] == "Acme filed 0 H-1B petitions"
    assert tools["c"].startswith("error: no such file")  # outside the folder
    assert fetched == ["https://h1b.example/acme"] and res.tool_calls == 3 and res.web_fetches == 1
    assert parse_answer(ds, res.answer).output.recommend is False


def test_budget_wraps_up_without_tools(ds):
    looping = chat(tool_calls=[call("x", "read_file", {"path": "sponsorship-notes.md"})], finish="tool_calls")

    def reply(request):
        body = json.loads(request.content)
        if body.get("tool_choice") == "none":
            assert body["messages"][-1]["content"].startswith("You've reached the limit")
            return httpx.Response(200, json=chat('{"recommend": true, "reason": "ran out of lookups"}'))
        return httpx.Response(200, json=looping)

    res = R.run_deepseek(ds, "P", "k", http=httpx.Client(transport=httpx.MockTransport(reply)))
    assert res.tool_calls == 6 and res.answer and "ran out" in res.answer  # max_tool_calls 6


def test_retries_then_errors(ds, monkeypatch):
    monkeypatch.setattr(R.time, "sleep", lambda s: None)
    api = API([httpx.Response(429), httpx.Response(503), chat('{"recommend": true, "reason": "ok"}')])
    assert R.run_deepseek(ds, "P", "k", http=httpx.Client(transport=httpx.MockTransport(api))).status == "ok"
    bad = R.run_deepseek(ds, "P", "k", http=httpx.Client(transport=httpx.MockTransport(API([httpx.Response(401, text="bad key")]))))
    assert bad.status == "error" and "401" in bad.error and bad.answer is None


def test_web_fetch_refuses_linkedin_and_private_addresses():
    for url in ("https://www.linkedin.com/jobs/1", "https://linkedin.com", "ftp://x.example/a", "http://127.0.0.1/",
                "http://localhost:8080/", "http://10.0.0.5/admin", "http://169.254.169.254/latest/meta-data"):
        assert R._host_allowed(url), url

    def site(request):
        if request.url.path == "/move":
            return httpx.Response(302, headers={"location": "https://www.linkedin.com/in/someone"})
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<html><script>var x=1</script><h1>Acme careers</h1><p>We sponsor visas.</p></html>")

    http = httpx.Client(transport=httpx.MockTransport(site))
    allow_public = lambda url: R._host_allowed(url) if "linkedin" in url else None  # noqa: E731  (no DNS in tests)
    text = R.web_fetch("https://acme.example/jobs", http, check=allow_public)
    assert "Acme careers" in text and "We sponsor visas." in text and "var x" not in text
    with pytest.raises(ValueError, match="linkedin.com is blocked"):
        R.web_fetch("https://acme.example/move", http, check=allow_public)  # a redirect hop is checked too


def test_select_routes_deepseek_filters_and_needs_their_key(ds, tmp_path, monkeypatch):
    from auto_app import cli

    monkeypatch.setenv("AUTO_APP_HOME", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    folder = ds.path.parent
    r = CliRunner().invoke(cli.app, ["filter", "run", "--dir", str(folder)])
    assert r.exit_code == 2 and "DEEPSEEK_API_KEY" in r.output
    r = CliRunner().invoke(cli.app, ["filter", "run", "--dir", str(folder), "--dry-run"])
    assert r.exit_code == 0 and "web_search isn't available with DeepSeek" in r.output
    r = CliRunner().invoke(cli.app, ["filter", "run", "--dir", str(folder), "--max-cost", "1"])
    assert r.exit_code == 2 and "deepseek-chat" in r.output and "needs a price" in r.output
