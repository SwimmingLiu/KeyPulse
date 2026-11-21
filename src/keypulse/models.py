"""Value objects shared by every provider.

A provider returns ``Signal`` records: one number plus the metadata needed to
explain where it came from.  Normalisation to a 0-100 score happens later in
:mod:`keypulse.scoring`, so providers never need to know about each other's
scales (WeChat index is a 7-digit integer, Google Trends is 0-100, SERP hit
counts span nine orders of magnitude).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Signal:
    """One demand measurement for one keyword from one provider."""

    keyword: str
    provider: str
    metric: str           # e.g. "index", "result_count", "pageviews"
    value: float
    unit: str = "raw"     # raw | per_month | hits | scaled_0_100
    region: str = "global"
    captured_at: str = field(default_factory=lambda: dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "keyword": self.keyword,
            "provider": self.provider,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "region": self.region,
            "captured_at": self.captured_at,
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.error:
            payload["error"] = self.error
        return payload

    @classmethod
    def failure(cls, keyword: str, provider: str, metric: str, error: str, *, region: str = "global") -> "Signal":
        return cls(keyword=keyword, provider=provider, metric=metric, value=0.0, region=region, error=error)


@dataclass
class Suggestion:
    """A keyword a provider suggests is related to the seed."""

    keyword: str
    source: str               # provider name plus endpoint, e.g. "google_suggest"
    seed: str = ""
    rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"keyword": self.keyword, "source": self.source}
        if self.seed:
            payload["seed"] = self.seed
        if self.rank is not None:
            payload["rank"] = self.rank
        return payload
