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


class FakeClient:
    """`script(kwargs) -> response`; or a list of responses returned in order."""

    def __init__(self, script):
        self.calls: list[dict] = []
        self._script = script
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if callable(self._script):
            return self._script(kwargs)
        return self._script.pop(0)


def prompt_of(kwargs) -> str:
    first = kwargs["messages"][0]["content"]
    return first if isinstance(first, str) else str(first)
