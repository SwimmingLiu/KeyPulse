"""Keyword expansion from a scenario or seed list.

Two entry points:

* ``expand`` — grow explicit seeds into a candidate pool using every available
  autocomplete source (the same trick SEO tools use; it is free and reflects
  real query volume ordering).
* ``suggest_seeds`` — turn a free-form scenario description ("做一款面向程序员
  的 AI 笔记软件") into seed keywords.  Purely lexical and offline: it mines
  n-grams from the scenario and stamps them with intent modifiers.  A model
  in the loop (the ``keypulse`` skill) can do much better, so this is the
  fallback used by the bare CLI.
"""

from __future__ import annotations

import itertools
import re
from collections import Counter

from .config import Settings
from .models import Suggestion
from .providers.base import Registry

_CJK = re.compile(r"[一-鿿]+")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#.]*")

#: Modifiers appended to a scenario theme to form long-tail seeds.
INTENT_MODIFIERS_ZH = ["怎么样", "多少钱", "哪个好", "推荐", "教程", "对比", "替代", "开源"]
INTENT_MODIFIERS_EN = ["tutorial", "vs", "pricing", "best", "open source", "alternative", "how to"]

#: Punctuation and conjunctions that separate noun phrases in a scenario.
_SENTENCE_MARKERS = ("，", "。", "；", "、", "！", "？", ",", ".", ";", "!", "?", "\n")
_SPLIT_CHARS = "的和与或及并想要支持需要可以能够通过实现进行以及对这个那个我你他她它们是为把让给作用于等"
#: Leading verbs/measure words to strip from a phrase head.
_LEADING_FILLER = (
    "做一款", "做一款面向", "做一款", "做一个", "做一种", "打造", "开发", "建设",
    "一款", "一个", "一种", "面向", "针对", "为了", "想要", "希望",
)

_STOPWORDS_EN = {
    "a", "an", "the", "for", "and", "or", "to", "of", "in", "on", "with", "my", "our",
    "want", "wants", "need", "needs", "build", "building", "make", "making", "app",
    "tool", "product", "users", "user", "feature", "features", "that", "this", "is",
    "are", "be", "can", "should", "help", "helps", "support", "using", "use", "which",
    "i", "we", "it", "as", "at", "by", "so", "if", "do", "does", "from",
}


def detect_language(text: str) -> str:
    """``zh`` when CJK characters dominate, else ``en``."""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    return "zh" if cjk >= max(1, latin // 3) else "en"


def suggest_seeds(scenario: str, limit: int = 8) -> list[str]:
    """Derive seed keywords from a scenario description, offline.

    Chinese text is split into 2-4 character runs (no dictionary available in
    the stdlib, so bi/tri-grams over the scenario text are the practical unit);
    English into capitalised/lowercase words and short phrases.  Terms are
    ranked by frequency and length.
    """
    scenario = scenario.strip()
    if not scenario:
        return []

    if detect_language(scenario) == "zh":
        candidates = _chinese_terms(scenario)
    else:
        candidates = _english_terms(scenario)

    if not candidates:
        return _dedupe([scenario])[:limit]
    return [term for term, _ in candidates[:limit]]


def _chinese_terms(text: str) -> list[tuple[str, float]]:
    """Split a Chinese scenario into candidate keyword phrases.

    Chinese has no spaces, so there is nothing to tokenise on.  Instead we
    exploit how scenarios are written: they are a series of noun phrases
    separated by punctuation, conjunctions and other function words.  Cutting
    on those markers yields whole phrases ("编程笔记软件") rather than the
    sliding-window fragments ("款面向程") an n-gram pass produces.
    """
    counts: Counter[str] = Counter()
    for segment in _split_prose(text):
        for phrase in _phrases(segment):
            counts[phrase] += len(phrase)
    return counts.most_common()


def _split_prose(text: str) -> list[str]:
    """Cut the scenario on punctuation and function-word boundaries."""
    cleaned = text
    for marker in _SENTENCE_MARKERS:
        cleaned = cleaned.replace(marker, "\n")
    segments: list[str] = []
    for chunk in cleaned.split("\n"):
        for part in _CJK.findall(chunk):
            # Function words glued to a phrase ("的", "和") mark where one
            # noun phrase ends and the next begins.
            for piece in re.split("[" + "".join(_SPLIT_CHARS) + "]", part):
                piece = piece.strip()
                # Trim leading verbs/measure words that survived the split
                # ("做一款面向程序员" → "面向程序员").
                piece = _trim_lead(piece)
                if 2 <= len(piece) <= 8:
                    segments.append(piece)
    return segments


def _trim_lead(piece: str) -> str:
    for prefix in _LEADING_FILLER:
        while piece.startswith(prefix) and len(piece) - len(prefix) >= 2:
            piece = piece[len(prefix) :]
    return piece


def _phrases(segment: str) -> list[str]:
    """Return the segment plus its meaningful trailing sub-phrases.

    "编程笔记软件" also yields "笔记软件" — both are plausible search terms.
    A short tail cutoff ("向程序员" from "面向程序员") is a fragment, so tails
    shorter than four characters are dropped.
    """
    out = [segment]
    if len(segment) > 4:
        out.append(segment[-4:])
    return out


def _english_terms(text: str) -> list[tuple[str, float]]:
    counts: Counter[str] = Counter()
    words = [w.lower() for w in _WORD.findall(text) if w.lower() not in _STOPWORDS_EN]
    for word in words:
        counts[word] += len(word)
    for size in (2, 3):
        for start in range(0, len(words) - size + 1):
            phrase_words = words[start : start + size]
            # Skip phrases made only of very short tokens; they read as noise.
            if sum(len(w) <= 2 for w in phrase_words) == size:
                continue
            counts[" ".join(phrase_words)] += 2 * size
    return counts.most_common()


def expand(
    seeds: list[str],
    registry: Registry,
    settings: Settings,
    *,
    providers: list[str] | None = None,
    region: str = "GLOBAL",
    per_seed: int = 10,
    depth: int = 1,
) -> tuple[list[str], list[Suggestion]]:
    """Grow seeds with autocomplete suggestions, up to ``depth`` rounds.

    Returns the deduplicated keyword list (seeds first, then discoveries by
    how often they were suggested) plus the raw suggestion records.
    """
    suggesters = [
        p for p in registry.select(providers, region)
        if hasattr(p, "suggest") and p.name not in {"google_trends"}
    ]
    queue = _dedupe(seeds)
    discovered: list[Suggestion] = []
    seen = set(q.lower() for q in queue)

    frontier = list(queue)
    for round_no in range(max(1, depth)):
        next_frontier: list[str] = []
        for seed in frontier:
            for provider in suggesters:
                for suggestion in provider.suggest(seed, region, limit=per_seed):
                    discovered.append(suggestion)
                    lowered = suggestion.keyword.strip().lower()
                    if lowered and lowered not in seen:
                        seen.add(lowered)
                        next_frontier.append(suggestion.keyword.strip())
        frontier = next_frontier[: max(per_seed * 4, 20)]
        if not frontier:
            break

    # Order discoveries by how many sources agreed on them.
    agreement: Counter[str] = Counter()
    display: dict[str, str] = {}
    for suggestion in discovered:
        key = suggestion.keyword.strip().lower()
        if not key:
            continue
        agreement[key] += 1
        display.setdefault(key, suggestion.keyword.strip())
    ordered = sorted(agreement, key=lambda k: agreement[k], reverse=True)
    expanded = _dedupe(seeds) + [display[k] for k in ordered if k not in {s.lower() for s in seeds}]
    return expanded, discovered


def add_modifiers(seeds: list[str], language: str | None = None, limit: int = 60) -> list[str]:
    """Attach intent modifiers to seeds to widen the long tail offline."""
    language = language or detect_language(" ".join(seeds))
    modifiers = INTENT_MODIFIERS_ZH if language == "zh" else INTENT_MODIFIERS_EN
    joiner = "" if language == "zh" else " "
    out = list(seeds)
    for seed in seeds:
        for modifier in modifiers:
            out.append(f"{seed}{joiner}{modifier}")
    return _dedupe(out)[:limit]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        item = item.strip()
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def expand_grid(themes: list[str], intents: list[str]) -> list[str]:
    """Cross-product helper for explicit scenario → seed construction."""
    return _dedupe([f"{a} {b}".strip() if a.isascii() else f"{a}{b}" for a, b in itertools.product(themes, intents)])
