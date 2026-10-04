"""A fake Claude client for Step 2 tests: scripted responses, every request recorded."""

from types import SimpleNamespace


def text(t):
    return {"type": "text", "text": t}


def tool_use(name, inp, id_="t1"):
    return {"type": "tool_use", "id": id_, "name": name, "input": inp}


def server_tool(name, inp):
    return {"type": "server_tool_use", "id": "s1", "name": name, "input": inp}


def response(*blocks, stop="end_turn", inp=100, out=20, searches=0):
    return SimpleNamespace(content=list(blocks), stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=inp, output_tokens=out,
                                                 server_tool_use=SimpleNamespace(web_search_requests=searches,
                                                                                 web_fetch_requests=0)))


def answer(recommend, reason="because"):
    return response(text(f'Thinking it over.\n{{"recommend": {str(recommend).lower()}, "reason": "{reason}"}}'))


class _FakeStream:
    """Like the SDK's message stream: text blocks arrive as a few deltas, then the final message."""

    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for b in self.resp.content:
            if b.get("type") == "text":
                t = b["text"]
                for i in range(0, len(t), 7):
                    yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=t[i:i + 7]))

    def get_final_message(self):
        return self.resp


class FakeClient:
    """`script(kwargs) -> response`; or a list of responses returned in order."""

    def __init__(self, script):
        self.calls: list[dict] = []
        self._script = script
        self.streamed = 0
        self.messages = SimpleNamespace(create=self._create, stream=self._stream)

    def _stream(self, **kwargs):
        self.streamed += 1
        return _FakeStream(self._create(**kwargs))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if callable(self._script):
            return self._script(kwargs)
        return self._script.pop(0)


def prompt_of(kwargs) -> str:
    first = kwargs["messages"][0]["content"]
    return first if isinstance(first, str) else str(first)
