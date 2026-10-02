"""Description similarity: word shingles, MinHash signatures, Jaccard and containment."""

import hashlib
import random
import re

SHINGLE_WORDS = 5
PERMUTATIONS = 128
_PRIME = (1 << 61) - 1
_rng = random.Random(20260930)  # fixed seed: signatures must be comparable across runs
_COEFFS = [(_rng.randrange(1, _PRIME), _rng.randrange(0, _PRIME)) for _ in range(PERMUTATIONS)]
_WORD = re.compile(r"[a-z0-9]+")


def shingles(text: str, k: int = SHINGLE_WORDS) -> set[str]:
    words = _WORD.findall(text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def _h(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big")


def minhash(sh: set[str]) -> list[int]:
    if not sh:
        return []
    hashes = [_h(s) for s in sh]
    return [min((a * x + b) % _PRIME for x in hashes) for a, b in _COEFFS]


def jaccard_estimate(a: list[int], b: list[int]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x == y for x, y in zip(a, b)) / len(a)


def containment_estimate(a: list[int], b: list[int], size_a: int, size_b: int) -> float:
    """|A∩B| / min(|A|,|B|) from MinHash signatures and set sizes (for when one side's text is purged)."""
    j = jaccard_estimate(a, b)
    if not size_a or not size_b:
        return 0.0
    inter = j * (size_a + size_b) / (1 + j)
    return min(1.0, inter / min(size_a, size_b))


def containment(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))
