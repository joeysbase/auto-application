"""Reading an agent's answer (docs/step2-select.md §3): raw answer → parser → ParsedOutput.

The default parser reads our output convention — the answer ends with a JSON object
`{"fit": true | false, "dealbreaker": true | false, "reason": "…"}`:
- fit: does the job meet your requirements (what you want, your background, preferences)?
- dealbreaker: does a hard rule from your command's deal-breakers apply (clearly shown by the posting or a lookup)?
The filter recommends the job when it fits and has no dealbreaker. The older `{"recommend": …, "reason": …}`
is still read (fit = recommend, dealbreaker unknown). A filter with a `parser.py` reads its own format and returns
either shape. Anything that can't be read gives `recommend = None` ("couldn't read"), never "no".
"""

import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel


class ParsedOutput(BaseModel):
    recommend: bool | None  # None = the answer couldn't be read
    reason: str = ""
    fit: bool | None = None  # meets your requirements? (None: not given / couldn't read)
    dealbreaker: bool | None = None  # a deal-breaker applies? (None: not given — treated as no)

    @classmethod
    def of(cls, fit: bool | None, dealbreaker: bool | None, reason: str = "") -> "ParsedOutput":
        """recommend = fit and no dealbreaker; a dealbreaker alone is enough to say no."""
        recommend = False if dealbreaker else fit
        return cls(recommend=recommend, reason=reason, fit=fit, dealbreaker=dealbreaker)


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


KEYS = ("fit", "dealbreaker", "recommend")


def from_dict(obj: dict) -> ParsedOutput:
    """{"fit", "dealbreaker", "reason"} — or the older {"recommend", "reason"}. Values must be true / false."""
    for k in KEYS:
        if k in obj and obj[k] is not None and not isinstance(obj[k], bool):
            raise ValueError(f'"{k}" must be true or false, got {obj[k]!r}')
    reason = obj.get("reason", "")
    reason = reason if isinstance(reason, str) else json.dumps(reason)
    if "fit" in obj or "dealbreaker" in obj:
        if obj.get("fit") is None and not obj.get("dealbreaker"):
            raise ValueError('"fit" is missing (and no dealbreaker was found)')
        return ParsedOutput.of(obj.get("fit"), obj.get("dealbreaker"), reason)
    if isinstance(obj.get("recommend"), bool):
        return ParsedOutput.of(obj["recommend"], None, reason)
    raise ValueError('needs "fit" and "dealbreaker" (or "recommend")')


def parse_convention(answer: str) -> ParsedOutput:
    """The last JSON object with a "fit" / "dealbreaker" (or older "recommend") key."""
    found = [o for o in _json_objects(answer or "") if any(k in o for k in KEYS)]
    if not found:
        raise ValueError('no {"fit": …, "dealbreaker": …} JSON object in the answer')
    return from_dict(found[-1])


def verdict_text(recommend: bool | None, fit: bool | None = None, dealbreaker: bool | None = None) -> str:
    """recommend / no — dealbreaker / no — doesn't fit / couldn't read (older answers: plain "no")."""
    if dealbreaker:
        return "no — dealbreaker" if fit is not False else "no — dealbreaker, doesn't fit"
    if recommend is None:
        return "couldn't read"
    if recommend:
        return "recommend"
    return "no — doesn't fit" if dealbreaker is False else "no"


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
            if not isinstance(raw, dict):
                raise ValueError(f'parser.py must return {{"fit": bool, "dealbreaker": bool, "reason": str}} '
                                 f'(or {{"recommend": bool, "reason": str}}), got {raw!r}'[:300])
            out = from_dict({**raw, "reason": str(raw.get("reason") or "")})
    except Exception as e:  # the user's parser, or a malformed answer: "couldn't read", never "no"
        return ParseResult(ParsedOutput(recommend=None), pid, f"{type(e).__name__}: {e}"[:500])
    return ParseResult(out, pid)
