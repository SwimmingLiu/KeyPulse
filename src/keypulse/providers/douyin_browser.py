"""抖音指数 (Douyin Index) via the browser bridge.

``creator.douyin.com`` has a "抖音指数" module: a keyword page showing
「关键词搜索指数」and 「关键词综合指数」 as daily series, with an average, a
year-over-year and a month-over-month figure.

There is no public JSON endpoint.  The page fetches its data with signed
requests (the usual ``a_bogus``/``msToken`` family), so a copied request does
not keep working.  What *does* work is driving the page the way a person does:
open the analysis URL for the keyword, let the chart render, and read the
numbers out of the page text.

The URL form is stable and shareable::

    /creator-micro/creator-count/arithmetic-index/analysis
        ?source=creator&keyword=<url-encoded>&appName=aweme

Keywords Douyin has not indexed answer 「关键词 “X” 尚未收录」, which is data
(not a failure) — the provider reports it the same way Baidu Index reports
「未被收录」.
"""

from __future__ import annotations

import json
import urllib.parse

from .. import opencli as bridge
from ..models import Signal
from .base import Provider, normalise_region

SESSION = "keypulse-douyin"
BASE = "https://creator.douyin.com/creator-micro/creator-count/arithmetic-index/analysis"

#: Read the rendered page. ``__KEYWORD__`` is substituted by the caller.
#:
#: The page prints each metric block as
#:   「关键词搜索指数 … 平均值 68.4万」
#:   「关键词综合指数 … 平均值 30.9万」
#: so the capture is label -> nearest 平均值.  A "尚未收录" notice means Douyin
#: has no index for the word.
READ_SCRIPT = r"""
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const num = (s) => {
    if (s == null) return null;
    const m = String(s).match(/([\d.]+)\s*(万|亿)?/);
    if (!m) return null;
    const v = Number(m[1]);
    return m[2] === '亿' ? v * 1e8 : m[2] === '万' ? v * 1e4 : v;
  };
  const deadline = Date.now() + 25000;
  let text = '';
  while (Date.now() < deadline) {
    text = (document.body.innerText || '').replace(/\s+/g, ' ');
    if (/尚未收录|暂未收录/.test(text)) return JSON.stringify({ unindexed: true });
    if (/关键词搜索指数/.test(text) && /平均值/.test(text)) break;
    if (/请登录|登录后查看/.test(text)) {
      return JSON.stringify({ error: '需要登录 creator.douyin.com' });
    }
    await sleep(600);
  }
  if (!/关键词搜索指数/.test(text)) {
    return JSON.stringify({ error: '指数面板未在 25s 内渲染' });
  }
  const grab = (label) => {
    const i = text.indexOf(label);
    if (i < 0) return null;
    const seg = text.slice(i, i + 400);
    const m = seg.match(/平均值\s*([\d.]+\s*[万亿]?)/);
    return m ? num(m[1]) : null;
  };
  const search = grab('关键词搜索指数');
  const comprehensive = grab('关键词综合指数');
  if (search == null && comprehensive == null) {
    return JSON.stringify({ error: '面板已渲染但没有读到平均值' });
  }
  const yoy = (text.match(/同比\s*([+-]?[\d.]+%)/) || [])[1] || null;
  const mom = (text.match(/环比\s*([+-]?[\d.]+%)/) || [])[1] || null;
  const window = (text.match(/本周期[：:]\s*([\d-]+)\s*~\s*([\d-]+)/) || []);
  return JSON.stringify({
    search, comprehensive, yoy, mom,
    window: window[1] ? window[1] + ' ~ ' + window[2] : null,
  });
})()
"""


def _url(keyword: str) -> str:
    return f"{BASE}?source=creator&keyword={urllib.parse.quote(keyword)}&appName=aweme"


class DouyinIndexBrowser(Provider):
    name = "douyin_index"
    label = "抖音指数"
    metric = "index"
    unit = "raw"
    kind = "index"
    tier = "index"
    access = "browser"
    regions = ("CN",)
    requires_session = True

    def check(self) -> tuple[bool, str]:
        try:
            if any(self.settings.cache_dir.glob(f"replay-{self.name}-*.json")):
                return True, "ok (replay)"
        except OSError:
            pass
        ok, reason = bridge.available()
        if not ok:
            return False, f"需要浏览器桥接：{reason}"
        return True, "ok (browser bridge)"

    def signal(self, keyword: str, region: str = "CN") -> Signal:
        recorded = self._replay(keyword)
        if recorded:
            return self._replayed_signal(keyword, region, recorded)
        return self.signals([keyword], region)[0]

    def signals(self, keywords: list[str], region: str = "CN") -> list[Signal]:
        """One navigation per keyword; the panel is rendered per page load."""
        recorded = [self._replay(kw) for kw in keywords]
        if all(recorded):
            return [self._replayed_signal(kw, region, e) for kw, e in zip(keywords, recorded)]
        results: list[Signal] = []
        for keyword in keywords:
            results.append(self._one(keyword, region))
        return results

    def _one(self, keyword: str, region: str) -> Signal:
        try:
            bridge.open_page(self.settings, _url(keyword), session=SESSION)
            payload = bridge.eval_js(
                self.settings,
                READ_SCRIPT,
                session=SESSION,
                timeout=max(60.0, self.settings.timeout * 4),
            )
            data = json.loads(payload) if isinstance(payload, str) else payload
        except Exception as exc:  # noqa: BLE001 - a scan must not die on one source
            return self._failure(keyword, f"{type(exc).__name__}: {exc}", region)

        if data.get("unindexed"):
            return Signal(
                keyword=keyword,
                provider=self.name,
                metric=self.metric,
                value=0.0,
                unit=self.unit,
                region=normalise_region(region),
                detail={
                    "unindexed": True,
                    "note": "抖音指数未收录该词",
                    "transport": "opencli",
                },
            )
        if "error" in data:
            return self._failure(keyword, str(data["error"]), region)

        value = data.get("search")
        if value is None:
            value = data.get("comprehensive")
        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=self.metric,
            value=float(value or 0.0),
            unit=self.unit,
            region=normalise_region(region),
            detail={
                "search_index": data.get("search"),
                "comprehensive_index": data.get("comprehensive"),
                "yoy": data.get("yoy"),
                "mom": data.get("mom"),
                "window": data.get("window") or "近 30 天日均",
                "transport": "opencli",
            },
        )
