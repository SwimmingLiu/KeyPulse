"""百度指数 driven through the local browser over the DevTools protocol.

Why this exists: 百度指数 answers ``status 10018`` (异常访问) to its JSON API
from a script, from a cookie-carrying request, *and* from a fetch issued
inside the page.  Only the page's own XHR passes, so the reliable route is a
real browser rendering the real UI.

``opencli`` does that when it is installed and its extension is connected.
This provider is the fallback that needs neither: it launches the Chrome (or
Chromium/Brave/Edge/Vivaldi) already on the machine, injects the cookies
KeyPulse can decrypt from that browser's own store, and navigates straight to
the trend page for the keyword.

The numbers come from the 搜索指数概览 table, which is what a person reads:
整体日均值 / 移动日均值 / 整体同比.  Navigating to ``#/trend/<word>`` rather
than typing into the search box matters — typing triggers an in-app route
change that destroys the JS execution context mid-call ("Inspected target
navigated or closed"), while a hash URL loads the same page directly.
"""

from __future__ import annotations

import json
import time
import urllib.parse

from .. import browser_cookies
from .. import cdp
from ..models import Signal
from .base import Provider, normalise_region

#: ``?t=`` is a cache-buster, and it is load-bearing: with a bare ``#/trend``
#: URL a second keyword only changes the fragment, which keeps the same
#: document — ``readyState`` is already ``complete`` and the router does not
#: re-run its fetch, so the read times out on the previous keyword's page.
#: A unique query string forces a real navigation.  Measured: 0.8 s per
#: keyword with it, 25 s timeouts without.
TREND_URL = "https://index.baidu.com/v2/main/index.html?t={stamp}#/trend/{word}?words={word}"

#: Read the 搜索指数概览 row for one keyword.
#: ``__KEYWORD__`` is replaced with a JSON string literal by the caller.
READ_SCRIPT = r"""
(async () => {
  const kw = __KEYWORD__;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const esc = kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const rowRe = new RegExp(esc + '\\s+([\\d,]+)\\s+([\\d,]+)\\s+(-?\\d+)%');
  const unindexed = new RegExp(esc + '\\s*未被收录');
  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) {
    const text = (document.body.innerText || '').replace(/\s+/g, ' ');
    if (/异常访问|安全验证/.test(text)) {
      return JSON.stringify({ error: 'blocked by Baidu anti-bot' });
    }
    if (/扫码登录|登录百度账号/.test(text) && !document.querySelector('input.search-input')) {
      return JSON.stringify({ error: '需要登录 index.baidu.com（运行 keypulse login --provider baidu）' });
    }
    if (unindexed.test(text)) return JSON.stringify({ unindexed: true });
    const m = text.match(rowRe);
    if (m) {
      return JSON.stringify({
        overall: Number(m[1].replace(/,/g, '')),
        mobile: Number(m[2].replace(/,/g, '')),
        yoy: m[3] + '%',
        url: location.href,
      });
    }
    await sleep(400);
  }
  return JSON.stringify({ error: '概览表 25s 内没有渲染' });
})()
"""


def _has_replay(provider: str, settings) -> bool:
    try:
        return any(settings.cache_dir.glob(f"replay-{provider}-*.json"))
    except OSError:
        return False


class BaiduIndexCDP(Provider):
    #: Same name as the other two 百度指数 providers: they are interchangeable
    #: transports for one source, and ``--provider baidu_index``, the index
    #: tier and the scoring weights all key off this name.
    name = "baidu_index"
    label = "百度指数"
    metric = "index"
    unit = "raw"
    kind = "index"
    tier = "index"
    access = "browser"
    regions = ("CN",)
    requires_session = True

    def check(self) -> tuple[bool, str]:
        if _has_replay(self.name, self.settings):
            return True, "ok (replay)"
        ok, reason = cdp.available()
        if not ok:
            return False, reason
        try:
            cookies = browser_cookies.read("baidu.com")
        except browser_cookies.CookieError as exc:
            return False, f"读不到浏览器 Cookie：{exc}"
        if not any(cookie.name == "BDUSS" for cookie in cookies):
            return False, "浏览器没有登录 index.baidu.com（运行 keypulse login --provider baidu）"
        family = reason.split("(")[-1].rstrip(")") if "(" in reason else "chrome"
        return True, f"ok (本地 {family}，注入浏览器 Cookie)"

    def signal(self, keyword: str, region: str = "CN") -> Signal:
        recorded = self._replay(keyword)
        if recorded:
            return self._replayed_signal(keyword, region, recorded)
        return self.signals([keyword], region)[0]

    def signals(self, keywords: list[str], region: str = "CN") -> list[Signal]:
        """One browser, one navigation per keyword."""
        recorded = [self._replay(kw) for kw in keywords]
        if all(recorded):
            return [self._replayed_signal(kw, region, entry) for kw, entry in zip(keywords, recorded)]

        try:
            cookies = browser_cookies.read("baidu.com")
        except browser_cookies.CookieError as exc:
            return [self._failure(kw, str(exc), region) for kw in keywords]

        try:
            browser = cdp.session()
        except cdp.CDPError as exc:
            return [self._failure(kw, f"无法启动本地浏览器：{exc}", region) for kw in keywords]

        results: list[Signal] = []
        try:
            browser.set_cookies(cdp.cookie_params(cookies))
            # Warm the app shell once: the first load builds the SPA's runtime
            # and costs ~1.6 s, which would otherwise be charged to the first
            # keyword and eat into its render budget.
            try:
                browser.navigate("https://index.baidu.com/v2/main/index.html")
            except cdp.CDPError:
                pass
            for keyword in keywords:
                results.append(self._one(browser, keyword, region))
        finally:
            browser.close()
        return results

    def _one(self, browser, keyword: str, region: str) -> Signal:
        quoted = urllib.parse.quote(keyword)
        url = TREND_URL.format(word=quoted, stamp=int(time.time() * 1000))
        script = READ_SCRIPT.replace("__KEYWORD__", json.dumps(keyword, ensure_ascii=False))
        try:
            browser.navigate(url)
            payload = browser.evaluate(script, timeout=90)
            data = json.loads(payload) if isinstance(payload, str) else payload
        except Exception as exc:  # noqa: BLE001 - one blocked keyword must not sink the scan
            return self._failure(keyword, f"{type(exc).__name__}: {exc}", region)

        if not isinstance(data, dict):
            return self._failure(keyword, "浏览器返回了无法解析的结果", region)
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
                    "note": "百度指数未收录该词（可在百度指数后台创建后查询）",
                    "transport": "cdp",
                },
            )
        if "error" in data:
            return self._failure(keyword, str(data["error"]), region)
        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=self.metric,
            value=float(data.get("overall") or 0.0),
            unit=self.unit,
            region=normalise_region(region),
            detail={
                "mobile": data.get("mobile"),
                "yoy": data.get("yoy"),
                "window": "近 30 天日均",
                "note": "本地浏览器驱动页面读取（百度指数 JSON API 被风控拦截）",
                "transport": "cdp",
            },
        )
