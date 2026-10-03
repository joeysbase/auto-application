"""Reading an agent's answer (docs/step2-select.md §3): raw answer → parser → ParsedOutput.

The default parser reads our output convention — the answer ends with a JSON object
`{"recommend": true | false, "reason": "…"}`. A filter with a `parser.py` reads its own format.
Anything that can't be read gives `recommend = None` ("couldn't read"), never "no".
"""

import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel


class ParsedOutput(BaseModel):
    recommend: bool | None  # None = the answer couldn't be read
    reason: str = ""


@dataclass
class ParseResult:
    output: ParsedOutput
    parser: str  # "convention" or "parser.py:<hash>"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.output.recommend is not None


def _json_objects(text: str):
    """Every top-level JSON object in the text, in order (string-aware brace matching)."""
    dec = json.JSONDecoder()
    i = 0
    while True:
        i = text.find("{", i)
        if i < 0:
            return
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            yield obj
        i = end


def parse_convention(answer: str) -> ParsedOutput:
    """The last JSON object with a "recommend" key; it must be true or false."""
    found = [o for o in _json_objects(answer or "") if "recommend" in o]
    if not found:
        raise ValueError('no {"recommend": …} JSON object in the answer')
    obj = found[-1]
    if not isinstance(obj["recommend"], bool):
        raise ValueError(f'"recommend" must be true or false, got {obj["recommend"]!r}')
    reason = obj.get("reason", "")
    return ParsedOutput(recommend=obj["recommend"], reason=reason if isinstance(reason, str) else json.dumps(reason))


def parser_id(flt) -> str:
    if flt.parser is None:
        return "convention"
    return "parser.py:" + hashlib.sha256((flt.path / "parser.py").read_bytes()).hexdigest()[:8]


def parse_answer(flt, answer: str | None) -> ParseResult:
    pid = parser_id(flt)
    if not answer:
        return ParseResult(ParsedOutput(recommend=None), pid, "the agent gave no answer")
    try:
        if flt.parser is None:
            out = parse_convention(answer)
        else:
            raw = flt.parser(answer)
            if not isinstance(raw, dict) or not isinstance(raw.get("recommend"), bool):
                raise ValueError(f'parser.py must return {{"recommend": bool, "reason": str}}, got {raw!r}'[:300])
            out = ParsedOutput(recommend=raw["recommend"], reason=str(raw.get("reason") or ""))
    except Exception as e:  # the user's parser, or a malformed answer: "couldn't read", never "no"
        return ParseResult(ParsedOutput(recommend=None), pid, f"{type(e).__name__}: {e}"[:500])
    return ParseResult(out, pid)
