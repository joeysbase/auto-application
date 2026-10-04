"""The DeepSeek runner (docs/step2-select.md §4): our tool-use loop on DeepSeek's OpenAI-compatible
Chat Completions API (`POST {base_url}/chat/completions`), with `DEEPSEEK_API_KEY`.

Used for filters whose `model` starts with `deepseek-` (e.g. deepseek-chat, deepseek-reasoner).
Tools — all run here, in our code:
  web_fetch   ours: GET a public http(s) page → plain text; linkedin.com, local and private network
              addresses refused; size and time limits
  read_file   ours: a file in the filter's own folder, nothing else
  web_search  not available (DeepSeek has no search tool) — skipped, with a note in the trace
Budget, wrap-up and trace work as in the Claude API runner. Each call is a new conversation.
"""

import ipaddress
import json
import logging
import re
import socket
import time
from urllib.parse import urlparse

import httpx

from auto_app.select.runner import SYSTEM, WRAP_UP, RunResult, _short, read_file
from auto_app.sources.common.text import html_to_text

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"
MAX_TOKENS = 4096
FETCH_TIMEOUT_S = 20
FETCH_MAX_BYTES = 2_000_000
FETCH_MAX_CHARS = 20_000  # page text handed back to the model
BLOCKED_HOSTS = ("linkedin.com",)
USER_AGENT = "Mozilla/5.0 (compatible; auto-app/0.1; job screening)"
_SCRIPTS = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.I | re.S)


def is_deepseek(model: str) -> bool:
    return model.startswith("deepseek-")


def _host_allowed(url: str) -> str | None:
    """Why this URL may not be fetched (None = allowed)."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return "only public http(s) URLs can be fetched"
    host = u.hostname.lower().rstrip(".")
    if any(host == b or host.endswith("." + b) for b in BLOCKED_HOSTS):
        return f"{host} is blocked"
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return f"can't resolve {host}"
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return f"{host} is a local or private address"
    return None


def web_fetch(url: str, http: httpx.Client, check=_host_allowed) -> str:
    """A public web page as plain text. Every redirect hop is checked again."""
    for _ in range(5):
        why = check(url)
        if why:
            raise ValueError(why)
        with http.stream("GET", url, headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT_S,
                         follow_redirects=False) as r:
            if r.is_redirect:
                url = str(r.url.join(r.headers.get("location", "")))
                continue
            if r.status_code >= 400:
                raise ValueError(f"HTTP {r.status_code}")
            body = b""
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > FETCH_MAX_BYTES:
                    break
            text = body.decode(r.encoding or "utf-8", errors="replace")
            if "html" in (r.headers.get("content-type") or "html"):
                text = html_to_text(_SCRIPTS.sub(" ", text))
            return text[:FETCH_MAX_CHARS] + ("\n[… page cut]" if len(text) > FETCH_MAX_CHARS else "")
    raise ValueError("too many redirects")


def _tools(flt) -> list[dict]:
    tools = []
    if "web_fetch" in flt.config.tools:
        tools.append({"type": "function", "function": {
            "name": "web_fetch", "description": "Fetch a public web page and return its text. linkedin.com is blocked.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}})
    if flt.files:
        tools.append({"type": "function", "function": {
            "name": "read_file",
            "description": "Read one of the user's notes in this filter's folder. Available: " + ", ".join(flt.files),
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}})
    return tools


def _post(http: httpx.Client, base_url: str, key: str, body: dict, retries: int = 4) -> dict:
    for attempt in range(retries + 1):
        try:
            r = http.post(f"{base_url.rstrip('/')}/chat/completions", json=body, timeout=180,
                          headers={"Authorization": f"Bearer {key}"})
        except httpx.TransportError:
            if attempt == retries:
                raise
            time.sleep(min(30, 2 ** attempt))
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(min(30, 2 ** attempt))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"DeepSeek API HTTP {r.status_code}: {r.text[:300]}")
        return r.json()
    raise RuntimeError("unreachable")


def run_deepseek(flt, prompt: str, key: str, base_url: str = DEFAULT_BASE_URL, http: httpx.Client | None = None,
                 fetch=web_fetch, on_event=None) -> RunResult:
    """on_event: live events (`filter run --stream`) — per turn here: its text, then each tool call and result."""

    def emit(kind, **data):
        if on_event:
            try:
                on_event(kind, data)
            except Exception:
                pass

    cfg = flt.config
    res = RunResult(answer=None, status="ok")
    own = http is None
    http = http or httpx.Client()
    tools = _tools(flt)
    if "web_search" in cfg.tools:
        res.trace.append({"turn": 0, "note": "web_search isn't available with DeepSeek; skipped"})
    messages: list[dict] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    wrap_up_sent = False
    try:
        for _ in range(cfg.max_turns + 2):
            out_of_budget = res.turns >= cfg.max_turns - 1 or res.tool_calls >= cfg.max_tool_calls
            body = {"model": cfg.model, "messages": messages, "max_tokens": MAX_TOKENS}
            if tools:
                body["tools"] = tools
                body["tool_choice"] = "none" if out_of_budget else "auto"
            data = _post(http, base_url, key, body)
            res.turns += 1
            usage = data.get("usage") or {}
            res.input_tokens += usage.get("prompt_tokens") or 0
            res.output_tokens += usage.get("completion_tokens") or 0
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            calls = msg.get("tool_calls") or []
            turn = {"turn": res.turns, "stop": choice.get("finish_reason"), "blocks": []}
            if msg.get("content"):
                turn["blocks"].append({"type": "text", "text": _short(msg["content"], 2000)})
                emit("text", text=msg["content"] + ("\n" if calls else ""))
            res.trace.append(turn)
            if not calls:
                res.answer = (msg.get("content") or "").strip() or None
                return res
            # the assistant turn goes back without reasoning_content (DeepSeek rejects it in the input)
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for c in calls:
                res.tool_calls += 1
                fn = c.get("function") or {}
                name = fn.get("name")
                emit("tool", name=name, input=fn.get("arguments"))
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                    if name == "read_file":
                        text = read_file(flt, args.get("path", ""))
                    elif name == "web_fetch" and "web_fetch" in cfg.tools:
                        res.web_fetches += 1
                        text = fetch(args.get("url", ""), http)
                    else:
                        raise ValueError(f"unknown tool {name!r}")
                    ok = True
                except Exception as e:  # tool errors go back to the model as text
                    text, ok = f"error: {e}", False
                turn["blocks"].append({"type": "tool_use", "name": name, "input": fn.get("arguments")})
                turn["blocks"].append({"type": "tool_result", "name": name, "error": not ok, "text": _short(text)})
                emit("tool_result", name=name, ok=ok, text=_short(text, 160))
                messages.append({"role": "tool", "tool_call_id": c.get("id"), "content": text})
            if (res.turns >= cfg.max_turns - 1 or res.tool_calls >= cfg.max_tool_calls) and not wrap_up_sent:
                messages.append({"role": "user", "content": WRAP_UP})
                wrap_up_sent = True
        res.status, res.error = "error", f"no answer within {res.turns} model calls"
    except Exception as e:
        res.status, res.error = "error", f"{type(e).__name__}: {e}"[:500]
        log.warning("deepseek run failed: %s", res.error)
    finally:
        if own:
            http.close()
    return res
