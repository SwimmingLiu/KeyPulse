"""Provider interface and registry.

Each provider subclass declares a :attr:`name`, the regions it covers, and how
it measures a keyword.  ``signal`` and ``suggest`` must never raise: they
return ``Signal.failure`` / an empty list instead, because a scan fans many
providers over many keywords and one blocked source must not sink the run.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

import json
from pathlib import Path

from ..config import Settings
from ..models import Signal, Suggestion

REGION_ALIASES = {
    "cn": "CN",
    "zh": "CN",
    "china": "CN",
    "us": "US",
    "usa": "US",
    "en": "US",
    "global": "GLOBAL",
    "world": "GLOBAL",
    "worldwide": "GLOBAL",
}


def normalise_region(region: str) -> str:
    key = (region or "GLOBAL").strip().lower()
    return REGION_ALIASES.get(key, key.upper())


class Provider:
    """Base class for a demand-data source."""

    name: str = "base"
    label: str = "Base"
    metric: str = "index"
    unit: str = "raw"
    regions: tuple[str, ...] = ("GLOBAL",)
    #: True when the provider works without any credential or browser session.
    requires_session: bool = False
    #: Rough kind of number it emits; used to decide whether it feeds the score.
    kind: str = "index"   # index | volume | serp_hits | pageviews | suggest
    #: ``index`` providers measure search demand directly.  They are what the
    #: user asked for ("搜索引擎关键词指数"); corpus/pageview providers are
    #: stand-ins used only when no index is reachable.
    tier: str = "corpus"  # index | corpus | competition | suggest
    #: How the source will behave: ``keyless`` (works out of the box),
    #: ``needs_cookie`` (configure it), ``proxied`` (usually needs a proxy).
    access: str = "keyless"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # -- replay -----------------------------------------------------------
    def _replay(self, keyword: str) -> dict | None:
        """A recorded value for this keyword/provider, when one exists.

        Two sources, checked in order:

        * ``KEYPULSE_REPLAY`` — a JSON recording produced by
          ``evals/fixtures/record_cache.py``.
        * ``<cache_dir>/replay-<provider>-<keyword>.json`` — the same recording
          materialised into the cache directory, which lets an offline scan in
          a sandbox replay data without needing an environment variable.

        Returns ``None`` when neither applies, in which case the provider does
        a real lookup as usual.
        """
        import os

        path = os.environ.get("KEYPULSE_REPLAY")
        if path:
            if not hasattr(self, "_replay_data"):
                try:
                    self._replay_data = json.loads(Path(path).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    self._replay_data = {"keywords": {}}
            entry = (self._replay_data.get("keywords") or {}).get(keyword, {}).get(self.name)
            if isinstance(entry, dict):
                return entry
        cached = self.settings.cache_dir / f"replay-{self.name}-{keyword}.json"
        try:
            entry = json.loads(cached.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return entry if isinstance(entry, dict) else None

    def _replayed_signal(self, keyword: str, region: str, entry: dict) -> Signal:
        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=entry.get("metric", self.metric),
            value=float(entry.get("value", 0.0)),
            unit=entry.get("unit", self.unit),
            region=normalise_region(region),
            detail={**(entry.get("detail") or {}), "transport": "replay"},
        )

    # -- capability -------------------------------------------------------
    def supports(self, region: str) -> bool:
        region = normalise_region(region)
        return region in self.regions or "GLOBAL" in self.regions

    def check(self) -> tuple[bool, str]:
        """Cheap health check. Returns (usable, human-readable reason)."""
        return True, "ok"

    # -- data -------------------------------------------------------------
    def signal(self, keyword: str, region: str = "GLOBAL") -> Signal:
        raise NotImplementedError

    def signals(self, keywords: list[str], region: str = "GLOBAL") -> list[Signal]:
        """Default batch: one ``signal`` per keyword.

        Browser-backed providers override this to share one page visit across
        all keywords, which is both faster and gentler on the upstream site.
        """
        return [self.signal(keyword, region) for keyword in keywords]

    def suggest(self, seed: str, region: str = "GLOBAL", limit: int = 10) -> list[Suggestion]:
        return []

    # -- helpers ----------------------------------------------------------
    def _failure(
        self,
        keyword: str,
        error: str,
        region: str = "GLOBAL",
        *,
        detail: dict[str, Any] | None = None,
    ) -> Signal:
        return Signal.failure(
            keyword, self.name, self.metric, error, region=normalise_region(region), detail=detail
        )


class Registry:
    """Ordered set of provider instances, lookup by name/alias."""

    def __init__(self, providers: Sequence[Provider]) -> None:
        self._providers = {p.name: p for p in providers}
        self._order = [p.name for p in providers]

    def __iter__(self) -> Iterable[Provider]:
        for name in self._order:
            yield self._providers[name]

    def __len__(self) -> int:
        return len(self._order)

    def names(self) -> list[str]:
        return list(self._order)

    def get(self, name: str) -> Provider:
        key = name.strip().lower()
        if key in self._providers:
            return self._providers[key]
        raise KeyError(f"unknown provider {name!r}; known: {', '.join(self._order)}")

    def select(self, wanted: Iterable[str] | None, region: str = "GLOBAL") -> list[Provider]:
        """Resolve a ``--provider`` list (supports ``all`` and ``auto``)."""
        wanted_list = [w.strip().lower() for w in (wanted or []) if w and w.strip()]
        if not wanted_list or "auto" in wanted_list:
            return [p for p in self if p.supports(region)]
        if "all" in wanted_list:
            return list(self)
        picked: list[Provider] = []
        for raw in wanted_list:
            for candidate in raw.split(","):
                candidate = candidate.strip()
                if candidate:
                    picked.append(self.get(candidate))
        return picked
