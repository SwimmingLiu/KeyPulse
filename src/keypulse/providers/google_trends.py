"""Google Trends interest-over-time provider.

Google publishes no public API.  The endpoint used here is the same one the
Trends web client calls; it returns a relative 0-100 interest series, which we
reduce to a mean.

Two transports, tried in order:

1. **Browser bridge** (``opencli``) — requests are issued from the user's own
   logged-in Chrome, so they carry a residential IP and real cookies.  This is
   the reliable path; Google rate-limits the plain endpoint by IP.
2. **Plain HTTP** — works from a clean IP or a proxy, otherwise 429s.

Environment: ``KEYPULSE_BROWSER=off`` disables the bridge, ``KEYPULSE_PROXY``
configures the HTTP fallback.
"""

from __future__ import annotations

import json
import os
import urllib.parse

from ..http import BROWSER_HEADERS, FetchError, fetch
from ..models import Signal, Suggestion
from .base import Provider, normalise_region


class GoogleTrends(Provider):
    name = "google_trends"
    label = "Google Trends"
    metric = "interest"
    unit = "scaled_0_100"
    kind = "index"
    regions = ("GLOBAL", "US", "CN", "GB", "JP", "DE", "IN", "BR")
    tier = "index"
    access = "browser"

    EXPLORE = "https://trends.google.com/trends/api/explore"
    MULTILINE = "https://trends.google.com/trends/api/widgetdata/multiline"

    def check(self) -> tuple[bool, str]:
        # A replay entry means this provider can answer without any network.
        if self._has_replay():
            return True, "ok (replay)"
        if self._browser_enabled():
            ok, reason = self._browser_ready()
            if ok:
                return True, "ok (browser bridge)"
        try:
            fetch(
                self.EXPLORE
                + "?hl=en-US&tz=0&req="
                + urllib.parse.quote(json.dumps({"comparisonItem": [_item("test", "")], "category": 0, "property": ""})),
                self.settings,
                headers={"Referer": "https://trends.google.com/"},
                cache_ttl=600,
            )
            return True, "ok (HTTP)"
        except FetchError as exc:
            if exc.status == 429:
                hint = "启动 opencli 浏览器桥接，或配置 KEYPULSE_PROXY"
                return False, f"Google Trends 对本机 IP 限流 (HTTP 429)；{hint}"
            return False, str(exc)

    def signal(self, keyword: str, region: str = "GLOBAL") -> Signal:
        recorded = self._replay(keyword)
        if recorded:
            return self._replayed_signal(keyword, region, recorded)
        if self._browser_enabled():
            via_browser = self._signal_via_browser(keyword, region)
            if via_browser is not None:
                return via_browser
        return self._signal_via_http(keyword, region)

    # -- transports -------------------------------------------------------
    def _signal_via_browser(self, keyword: str, region: str) -> Signal | None:
        """Return a Signal, or ``None`` to let the HTTP transport try."""
        from . import trends_browser

        try:
            payload = trends_browser.fetch_interest(self.settings, [keyword], region)
        except Exception as exc:  # noqa: BLE001 - bridge failure is not fatal
            self._browser_error = f"{type(exc).__name__}: {exc}"
            return None
        entry = payload.get(keyword) or {}
        if "value" not in entry:
            self._browser_error = str(entry.get("error") or "empty batch response")
            return None
        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=self.metric,
            value=float(entry["value"]),
            unit=self.unit,
            region=normalise_region(region),
            detail={"points": entry.get("points"), "window": "today 12-m", "transport": "opencli"},
        )

    def _signal_via_http(self, keyword: str, region: str) -> Signal:
        geo = "" if normalise_region(region) in ("GLOBAL", "CN") else normalise_region(region)
        try:
            interest = self._interest(keyword, geo)
        except FetchError as exc:
            reason = str(exc)
            if self._browser_enabled() and getattr(self, "_browser_error", None):
                reason = f"{reason}（浏览器桥接失败: {self._browser_error}）"
            return self._failure(keyword, reason, region)
        except (ValueError, KeyError) as exc:
            return self._failure(keyword, f"unexpected Trends payload: {exc}", region)
        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=self.metric,
            value=interest,
            unit=self.unit,
            region=normalise_region(region),
            detail={"geo": geo or "worldwide", "window": "today 12-m"},
        )

    def suggest(self, seed: str, region: str = "GLOBAL", limit: int = 10) -> list[Suggestion]:
        # Trends autocomplete returns topics, not query completions; still useful.
        url = "https://trends.google.com/trends/api/autocomplete/" + urllib.parse.quote(seed)
        try:
            payload = fetch(url, self.settings, headers={"Referer": "https://trends.google.com/"}).json()
        except (FetchError, ValueError):
            return []
        out: list[Suggestion] = []
        for rank, topic in enumerate(payload.get("default", {}).get("topics", [])[:limit], start=1):
            title = topic.get("title")
            if title:
                out.append(Suggestion(keyword=title, source="google_trends", seed=seed, rank=rank))
        return out

    # -- helpers ----------------------------------------------------------
    @property
    def access(self) -> str:  # type: ignore[override]
        # A browser-backed Trends request opens/uses one shared tab, so the
        # scan must serialise keywords for this provider.
        if self.settings.offline:
            return "proxied"
        return "browser" if self._browser_enabled() and self._browser_ready()[0] else "proxied"

    def _has_replay(self) -> bool:
        cache_dir = self.settings.cache_dir
        try:
            return any(cache_dir.glob(f"replay-{self.name}-*.json"))
        except OSError:
            return False

    def _browser_enabled(self) -> bool:
        import os

        if os.environ.get("KEYPULSE_REPLAY"):
            return True
        if self.settings.offline:
            return False
        return os.environ.get("KEYPULSE_BROWSER", "on").lower() not in {"0", "off", "false", "no"}

    def _browser_ready(self) -> tuple[bool, str]:
        from .. import opencli

        if not hasattr(self, "_browser_status"):
            self._browser_status = opencli.available()
        return self._browser_status

    def _interest(self, keyword: str, geo: str) -> float:
        request = json.dumps(
            {"comparisonItem": [_item(keyword, geo)], "category": 0, "property": ""},
            separators=(",", ":"),
        )
        explore_url = (
            f"{self.EXPLORE}?hl=en-US&tz=0&req={urllib.parse.quote(request)}"
        )
        explore = fetch(
            explore_url,
            self.settings,
            headers={"Referer": "https://trends.google.com/"},
        ).json()
        widgets = explore.get("widgets") or []
        multiline = next((w for w in widgets if w.get("id") == "TIMESERIES"), None)
        if not multiline:
            raise ValueError("no TIMESERIES widget in explore response")
        token = multiline["token"]
        req = multiline["request"]
        params = urllib.parse.urlencode(
            {
                "hl": "en-US",
                "tz": "0",
                "req": json.dumps(req, separators=(",", ":")),
                "token": token,
            }
        )
        series = fetch(
            f"{self.MULTILINE}?{params}",
            self.settings,
            headers={"Referer": "https://trends.google.com/", **BROWSER_HEADERS},
        ).json()
        points = (
            series.get("default", {})
            .get("timelineData", [])
        )
        values = [p.get("value", [0])[0] for p in points if p.get("value")]
        if not values:
            return 0.0
        return round(sum(values) / len(values), 1)


def _item(keyword: str, geo: str) -> dict[str, str]:
    return {
        "keyword": keyword,
        "geo": geo,
        "time": "today 12-m",
    }
