"""The claude-cli runner: the lockdown flags, the environment, and reading Claude Code's JSON (fake process)."""

import json
import os
import subprocess
from types import SimpleNamespace

from auto_app.select.engine import SelectOptions, run_select
from auto_app.select.filters import EXAMPLES_DIR, load_filter
from auto_app.select.parse import parse_answer
from auto_app.select.runner_cli import cli_args, run_claude_cli

ML = load_filter(EXAMPLES_DIR / "ml-engineer")  # web tools + sponsorship-notes.md
VF = load_filter(EXAMPLES_DIR / "verdict-format")  # no tools, no files


def claude_json(result, subtype="success", is_error=False, turns=2, cost=0.0123, events=()):
    """Claude Code's stream-json output: events, then the result line."""
    lines = [json.dumps(e) for e in events]
    lines.append(json.dumps({"type": "result", "subtype": subtype, "is_error": is_error, "result": result,
                             "num_turns": turns, "total_cost_usd": cost, "permission_denials": [],
                             "usage": {"input_tokens": 900, "cache_read_input_tokens": 100, "output_tokens": 50,
                                       "server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 0}}}))
    return "\n".join(lines) + "\n"


LOOKUP_EVENTS = [
    {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Checking "}}},
    {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "notes."}}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "u1", "name": "Read",
                                                   "input": {"file_path": "/tmp/x/sponsorship-notes.md"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "u1",
                                              "content": [{"type": "text", "text": "1\tChecking visa sponsorship"}]}]}},
]


class FakeProc:
    """Stands in for subprocess.Popen: records the call and the folder contents, replays stdout lines."""

    def __init__(self, stdout="", returncode=0, stderr="", raise_=None):
        self.calls = []
        self.lines, self.returncode_, self.stderr_text, self.raise_ = stdout, returncode, stderr, raise_

    def __call__(self, args, **kw):
        if self.raise_:
            raise self.raise_
        kw["files_present"] = sorted(os.listdir(kw["cwd"]))  # what the process could see in its folder
        self.calls.append((args, kw))
        fake = SimpleNamespace(returncode=self.returncode_, kill=lambda: None, wait=lambda: self.returncode_)
        fake.stdin = SimpleNamespace(written=[], write=lambda t: fake.stdin.written.append(t), close=lambda: None)
        fake.stdout = iter(self.lines.splitlines(keepends=True))
        fake.stderr = SimpleNamespace(read=lambda: self.stderr_text)
        kw["proc"] = fake
        return fake


def test_lockdown_flags():
    a = cli_args(ML, "claude")
    assert a[:6] == ["claude", "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    assert a[a.index("--tools") + 1] == "WebSearch,WebFetch,Read" and a[a.index("--permission-mode") + 1] == "dontAsk"
    assert a[a.index("--setting-sources") + 1] == "" and {"--strict-mcp-config", "--disable-slash-commands",
                                                         "--no-session-persistence"} <= set(a)
    allow = a[a.index("--allowedTools") + 1:a.index("--disallowedTools")]
    assert allow == ["WebSearch", "WebFetch", "Read(./**)"]
    deny = a[a.index("--disallowedTools") + 1:]
    assert "WebFetch(domain:www.linkedin.com)" in deny and "Read(./parser.py)" in deny
    assert a[a.index("--max-turns") + 1] == "8" and a[a.index("--model") + 1] == "claude-sonnet-5"
    v = cli_args(VF)
    assert v[v.index("--tools") + 1] == "" and "--allowedTools" not in v  # no tools at all


def test_runs_in_the_filter_folder_without_the_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-passed")
    proc = FakeProc(claude_json('Fits.\n{"recommend": true, "reason": "ML systems"}'))
    res = run_claude_cli(ML, "THE PROMPT", popen=proc)
    args, kw = proc.calls[0]
    assert kw["proc"].stdin.written == ["THE PROMPT"] and "ANTHROPIC_API_KEY" not in kw["env"]
    # a fresh temporary folder outside the project, with only the filter's own files — gone afterwards
    assert not kw["cwd"].startswith(str(ML.path.parents[1])) and kw["files_present"] == ["sponsorship-notes.md"]
    assert not os.path.exists(kw["cwd"])
    assert res.status == "ok" and parse_answer(ML, res.answer).output.recommend is True
    assert (res.input_tokens, res.output_tokens, res.web_searches, res.turns, res.cost_usd) == (1000, 50, 1, 2, 0.0123)
    assert res.trace[0]["runner"] == "claude-cli"


def test_stream_events_become_live_events_and_a_full_trace():
    events = []
    proc = FakeProc(claude_json('{"recommend": true, "reason": "sponsors"}', events=LOOKUP_EVENTS))
    res = run_claude_cli(ML, "P", popen=proc, on_event=lambda k, d: events.append((k, d)))
    assert [k for k, _ in events] == ["text", "text", "tool", "tool_result"]
    assert "".join(d["text"] for k, d in events if k == "text") == "Checking notes."
    assert events[2][1]["name"] == "Read" and events[3][1]["ok"]
    lookups = [b for t in res.trace for b in t.get("blocks", []) if b["type"] == "tool_use"]
    assert lookups == [{"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/x/sponsorship-notes.md"}}]
    assert res.tool_calls == 1 and parse_answer(ML, res.answer).output.recommend is True


def test_every_call_is_a_new_session():
    proc = FakeProc(claude_json('{"recommend": true, "reason": "x"}'))
    run_claude_cli(ML, "A", popen=proc)
    run_claude_cli(ML, "B", popen=proc)
    ids = [a[a.index("--session-id") + 1] for a, _ in proc.calls]
    assert len(set(ids)) == 2 and all(len(i) == 36 for i in ids)  # a new UUID each time
    for a, kw in proc.calls:
        assert "--no-session-persistence" in a and "--continue" not in a and "--resume" not in a
    assert proc.calls[0][1]["cwd"] != proc.calls[1][1]["cwd"]  # a new folder each time


def test_failures_are_errors_and_running_out_of_turns_is_unreadable():
    assert run_claude_cli(ML, "P", popen=FakeProc(claude_json("boom", subtype="error_during_execution", is_error=True))).status == "error"
    bad = run_claude_cli(ML, "P", popen=FakeProc("not json", returncode=1, stderr="Not logged in"))
    assert bad.status == "error" and "Not logged in" in bad.error
    assert "not found" in run_claude_cli(ML, "P", popen=FakeProc(raise_=FileNotFoundError())).error
    assert "timed out" in run_claude_cli(ML, "P", popen=FakeProc(raise_=subprocess.TimeoutExpired("claude", 1))).error
    out = run_claude_cli(ML, "P", popen=FakeProc(claude_json(None, subtype="error_max_turns")))
    assert out.status == "ok" and out.answer is None  # → parsed as "couldn't read" → shown to you


def test_select_uses_the_cli_cost(tmp_path):
    from auto_app.core.config import AppConfig
    from auto_app.core.db.store import DbStore
    from auto_app.settings import Paths
    from tests.process.test_link import add, job_of, run, wd

    db = DbStore(tmp_path / "data" / "auto_app.db", Paths(home=tmp_path))
    add(db, "nvidia", "JR1", url=wd("JR1"), title="ML Engineer")
    run(db)
    proc = FakeProc(claude_json('{"recommend": false, "reason": "needs 5 years"}', cost=0.02))
    rep = run_select(db, AppConfig(), [ML], lambda f, p, **kw: run_claude_cli(f, p, popen=proc, **kw), SelectOptions())
    assert rep.judged == 1 and rep.cost == 0.02 and rep.cost_unknown == 0  # no price config needed
    assert db.job(job_of(db, "nvidia/JR1"))["step2"] == "un_recommended"
