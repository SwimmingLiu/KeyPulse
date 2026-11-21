"""Autocomplete providers — keyword discovery, plus a weak demand signal.

Autocomplete is excellent for *discovery* (it surfaces what people actually
type next) but a poor *volume* signal: every engine returns roughly ten
phrases regardless of popularity, so phrase position says little about demand.

What does carry signal is **affinity**: the share of the suggestion set that
is about the term itself rather than a drifting neighbour (Googling "猫粮"
returns ten cat-food phrases; Googling a made-up string returns ten unrelated
corrections).  That is what ``signal`` reports.
"""

from __future__ import annotations

import json
import urllib.parse

from ..http import JSON_HEADERS, FetchError, fetch, strip_jsonp
from ..models import Signal, Suggestion
from .base import Provider, normalise_region


class _AutocompleteProvider(Provider):
    kind = "suggest"
    metric = "suggest_affinity"
    unit = "percent"
    tier = "suggest"
    weight = 0.7

    def complete(self, seed: str) -> list[str]:
        raise NotImplementedError

    def suggest(self, seed: str, region: str = "GLOBAL", limit: int = 10) -> list[Suggestion]:
        try:
            words = self.complete(seed)
        except (FetchError, ValueError):
            return []
        return [
            Suggestion(keyword=word, source=self.name, seed=seed, rank=pos)
            for pos, word in enumerate(words[:limit], start=1)
        ]

    def signal(self, keyword: str, region: str = "GLOBAL") -> Signal:
        try:
            words = self.complete(keyword)
        except (FetchError, ValueError) as exc:
            return self._failure(keyword, str(exc), region)
        if not words:
            return self._failure(keyword, "no suggestions returned", region)
        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=self.metric,
            value=affinity(keyword, words),
            unit=self.unit,
            region=normalise_region(region),
            detail={"suggestions": len(words), "on_topic": sum(1 for w in words if _matches(keyword, w))},
        )


def affinity(keyword: str, suggestions: list[str]) -> float:
    """Percent of suggestions that mention the keyword itself."""
    if not suggestions:
        return 0.0
    hits = sum(1 for word in suggestions if _matches(keyword, word))
    return round(100.0 * hits / len(suggestions), 1)


def _matches(keyword: str, suggestion: str) -> bool:
    needle = "".join(keyword.lower().split())
    return bool(needle) and needle in "".join(suggestion.lower().split())


class GoogleSuggest(_AutocompleteProvider):
    name = "google_suggest"
    label = "Google 下拉词"
    regions = ("GLOBAL", "US", "CN", "GB", "JP", "DE", "IN", "BR")

    API = "https://suggestqueries.google.com/complete/search"

    def complete(self, seed: str) -> list[str]:
        params = urllib.parse.urlencode({"client": "firefox", "hl": "en", "q": seed})
        payload = fetch(f"{self.API}?{params}", self.settings).json()
        return [str(w) for w in payload[1]]


class BingSuggest(_AutocompleteProvider):
    name = "bing_suggest"
    label = "Bing 下拉词"
    weight = 0.5
    regions = ("GLOBAL", "US", "CN", "GB", "JP", "DE", "IN", "BR")

    API = "https://api.bing.com/qsonhs.aspx"

    def complete(self, seed: str) -> list[str]:
        params = urllib.parse.urlencode({"type": "cb", "q": seed})
        payload = fetch(f"{self.API}?{params}", self.settings, headers=JSON_HEADERS).json()
        words: list[str] = []
        for result in payload.get("AS", {}).get("Results", []):
            for item in result.get("Suggests", []):
                text = item.get("Txt")
                if text and text not in words:
                    words.append(text)
        return words


class So360Suggest(_AutocompleteProvider):
    name = "so360_suggest"
    label = "360 下拉词"
    weight = 0.4
    regions = ("CN", "GLOBAL")

    API = "https://sug.so.360.cn/suggest"

    def complete(self, seed: str) -> list[str]:
        params = urllib.parse.urlencode({"word": seed})
        payload = fetch(f"{self.API}?{params}", self.settings, headers=JSON_HEADERS).json()
        return [item["word"] for item in payload.get("result", []) if item.get("word")]


class SogouSuggest(_AutocompleteProvider):
    name = "sogou_suggest"
    label = "搜狗下拉词"
    weight = 0.4
    regions = ("CN", "GLOBAL")

    # The legacy /suggnew/ajajjson endpoint now answers with an empty body; the
    # mobile suggestion gateway still returns the same ``sogou.sug([...])``
    # JSONP array, in GB18030.
    API = "https://sor.html5.qq.com/api/getsug"

    def complete(self, seed: str) -> list[str]:
        params = urllib.parse.urlencode({"key": seed})
        text = fetch(f"{self.API}?{params}", self.settings).text
        payload = json.loads(strip_jsonp(text))
        if isinstance(payload, dict):
            return [str(w) for w in payload.get("s", [])]
        return [str(w) for w in payload[1]] if len(payload) > 1 else []
