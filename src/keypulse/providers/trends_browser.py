"""Google Trends, fetched from inside a real browser tab.

The plain-HTTP provider (:mod:`keypulse.providers.google_trends`) works but is
the one source most likely to answer 429, because Google rate-limits by IP and
datacenter/shared egress is usually already burned.  Issued from the user's own
browser — via ``opencli`` — the identical endpoint returns 200.

This module keeps that logic in one place and is used by the browser-backed
provider; the HTTP provider remains for machines without a browser bridge.
"""

from __future__ import annotations

from ..config import Settings
from ..models import Signal
from .base import normalise_region

EXPLORE_URL = "https://trends.google.com/trends/explore?hl=en-US"

#: Fetches explore + multiline for several keywords in one browser round-trip.
BATCH_SCRIPT = """
(async () => {
  const enc = encodeURIComponent;
  const strip = (t) => JSON.parse(t.replace(/^\\)\\]\\}',?\\n?/, ''));
  const kws = __KEYWORDS__;
  const geo = __GEO__;
  const out = {};
  for (const kw of kws) {
    try {
      const req = enc(JSON.stringify({
        comparisonItem: [{ keyword: kw, geo: geo, time: 'today 12-m' }],
        category: 0, property: ''
      }));
      const explore = await fetch('/trends/api/explore?hl=en-US&tz=0&req=' + req);
      const ej = strip(await explore.text());
      const widget = (ej.widgets || []).find((w) => w.id === 'TIMESERIES');
      if (!widget) { out[kw] = { error: 'no TIMESERIES widget' }; continue; }
      const params = 'hl=en-US&tz=0&req=' + enc(JSON.stringify(widget.request))
                   + '&token=' + enc(widget.token);
      const series = await fetch('/trends/api/widgetdata/multiline?' + params);
      const sj = strip(await series.text());
      const vals = ((sj.default || {}).timelineData || [])
        .map((p) => (p.value || [])[0]).filter((v) => typeof v === 'number');
      if (!vals.length) { out[kw] = { error: 'empty series' }; continue; }
      const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
      out[kw] = { value: Math.round(mean * 10) / 10, points: vals.length };
    } catch (e) {
      out[kw] = { error: String(e).slice(0, 120) };
    }
  }
  return JSON.stringify(out);
})()
"""


def _geo(region: str) -> str:
    normalised = normalise_region(region)
    return "" if normalised in ("GLOBAL", "CN") else normalised


def fetch_interest(settings: Settings, keywords: list[str], region: str = "GLOBAL") -> dict[str, dict]:
    """Interest-over-time mean per keyword, via the browser bridge.

    Returns ``{keyword: {"value": float, "points": int}}`` or, per keyword,
    ``{"error": str}``.  Raises ``OpenCliError``/``RuntimeError`` when the
    bridge itself is unavailable.
    """
    from ..opencli import SESSION, eval_js, open_page

    script = (
        BATCH_SCRIPT.replace("__KEYWORDS__", _js_literal(keywords))
        .replace("__GEO__", _js_literal(_geo(region)))
    )
    # Make sure the tab is on trends.google.com so relative fetches and the
    # site's cookies apply.
    open_page(settings, EXPLORE_URL, session=SESSION)
    raw = eval_js(settings, script, session=SESSION)
    import json as _json

    payload = _json.loads(raw) if isinstance(raw, str) else raw
    return payload or {}


def _js_literal(value) -> str:
    import json as _json

    return _json.dumps(value, ensure_ascii=False)


def signals_from_payload(
    keywords: list[str], payload: dict[str, dict], region: str
) -> list[Signal]:
    """Turn a batch payload into ``Signal`` records for the scoring layer."""
    from ..models import Signal as _Signal

    results: list[Signal] = []
    for keyword in keywords:
        entry = payload.get(keyword) or {}
        if "error" in entry:
            results.append(
                Signal.failure(keyword, "google_trends", "interest", entry["error"], region=normalise_region(region))
            )
            continue
        results.append(
            _Signal(
                keyword=keyword,
                provider="google_trends",
                metric="interest",
                value=float(entry.get("value", 0.0)),
                unit="scaled_0_100",
                region=normalise_region(region),
                detail={"points": entry.get("points"), "transport": "opencli"},
            )
        )
    return results
