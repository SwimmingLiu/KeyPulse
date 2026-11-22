"""微信指数 (WeChat Index) via the browser bridge, using captured credentials.

This is the real 微信指数 (the mini-program's index), not Sogou's WeChat
article count — that one lives in ``wechat_browser.py`` and is a separate,
always-available proxy.

Why it needs credentials: the index is served from
``search.weixin.qq.com/cgi-bin/searchweb/wxindex/querywxindexgroup`` and the
request carries three things that only the WeChat client can mint:

* ``openid`` — the mini-program openid of the account that opened 微信指数;
* ``search_key`` — a short-lived token (minutes to ~24h);
* a ``Referer`` naming the mini-program's appid — the browser sets this
  itself for a same-origin fetch, so nothing about it is needed here.

``index.weixin.qq.com`` does not exist (NXDOMAIN); there is no web endpoint to
fall back on.  Credentials come from capturing one request from the WeChat
mini-program (e.g. mitmproxy on a phone) — see ``keypulse login --help``.

Given credentials, the response is JSON and needs no decryption::

    data.group_list[].group_wxindex[].wxindex_str = "34572,34358,..."

one comma-separated value per day, newest last.  This provider reports the
latest day as the value and keeps the series in ``detail``.

Because the token expires, an expired credential is reported as
"需要重新获取" rather than as a network failure, so the scan can tell the user
to re-capture instead of retrying pointlessly.
"""

from __future__ import annotations

import json
import urllib.parse

from .. import opencli as bridge
from ..models import Signal
from .base import Provider, normalise_region

SESSION = "keypulse-wechat-index"
HOST = "https://search.weixin.qq.com"
PATH = "/cgi-bin/searchweb/wxindex/querywxindexgroup"
#: Fetches one keyword. ``__PARAMS__`` is a JSON object substituted below.
#: Runs inside the mini-program's own page so the Referer and any cookies the
#: client set are present.
FETCH_SCRIPT = r"""
(async () => {
  const params = __PARAMS__;
  const q = new URLSearchParams(params).toString();
  try {
    const r = await fetch('/cgi-bin/searchweb/wxindex/querywxindexgroup?' + q, {
      credentials: 'include',
      headers: { 'Accept': 'application/json' },
    });
    const text = await r.text();
    let body = null;
    try { body = JSON.parse(text); } catch (e) { body = { raw: text.slice(0, 300) }; }
    return JSON.stringify({ status: r.status, body });
  } catch (e) {
    return JSON.stringify({ error: String(e).slice(0, 120) });
  }
})()
"""


class WeChatIndexBrowser(Provider):
    name = "wechat_index"
    label = "微信指数"
    metric = "index"
    unit = "raw"
    kind = "index"
    tier = "index"
    access = "needs_cookie"
    regions = ("CN",)
    requires_session = True

    # -- credentials ------------------------------------------------------
    def _credentials(self) -> tuple[str, str]:
        settings = self.settings
        return (
            getattr(settings, "wechat_openid", "") or "",
            getattr(settings, "wechat_search_key", "") or "",
        )

    def check(self) -> tuple[bool, str]:
        try:
            if any(self.settings.cache_dir.glob(f"replay-{self.name}-*.json")):
                return True, "ok (replay)"
        except OSError:
            pass
        openid, key = self._credentials()
        if not openid or not key:
            return (
                False,
                "需要 KEYPULSE_WECHAT_OPENID 与 KEYPULSE_WECHAT_SEARCH_KEY"
                "（从微信指数小程序抓一次请求；见 keypulse login --help）",
            )
        ok, reason = bridge.available()
        if not ok:
            return False, f"需要浏览器桥接：{reason}"
        return True, "ok (browser bridge)"

    def signal(self, keyword: str, region: str = "CN") -> Signal:
        recorded = self._replay(keyword)
        if recorded:
            return self._replayed_signal(keyword, region, recorded)
        openid, key = self._credentials()
        if not openid or not key:
            return self._failure(keyword, "缺少 openid / search_key", region)
        return self._one(keyword, region, openid, key)

    def _one(self, keyword: str, region: str, openid: str, key: str) -> Signal:
        # The request must look like it came from the mini-program's web-view.
        page = f"{HOST}/cgi-bin/searchweb/clientjump?tag=wxindex"
        try:
            bridge.open_page(self.settings, page, session=SESSION)
        except Exception:  # noqa: BLE001 - the fetch may still succeed
            pass
        params = {
            "wxindex_query_list": keyword,
            "gid": "",
            "openid": openid,
            "search_key": key,
        }
        try:
            payload = bridge.eval_js(
                self.settings,
                FETCH_SCRIPT.replace("__PARAMS__", json.dumps(params, ensure_ascii=False)),
                session=SESSION,
                timeout=max(45.0, self.settings.timeout * 3),
            )
            data = json.loads(payload) if isinstance(payload, str) else payload
        except Exception as exc:  # noqa: BLE001
            return self._failure(keyword, f"{type(exc).__name__}: {exc}", region)

        if "error" in data:
            return self._failure(keyword, str(data["error"]), region)
        body = data.get("body") or {}
        errcode = body.get("errcode")
        if errcode not in (0, None):
            # 1000/1001/1008 are the auth family: the token is wrong or stale.
            if errcode in (1000, 1001, 1008):
                return self._failure(
                    keyword, f"微信指数凭据失效（errcode {errcode}）：请重新抓取 search_key", region
                )
            return self._failure(keyword, f"微信指数返回 errcode {errcode}: {body.get('msg')}", region)

        groups = ((body.get("data") or {}).get("group_list") or [])
        series: list[float] = []
        for group in groups:
            for item in group.get("group_wxindex") or []:
                raw = item.get("wxindex_str") or ""
                series = [float(x) for x in raw.split(",") if x.strip() not in ("", "-")]
                if series:
                    break
            if series:
                break
        if not series:
            return self._failure(keyword, "响应里没有 wxindex_str（该词可能未被收录）", region)
        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=self.metric,
            value=series[-1],
            unit=self.unit,
            region=normalise_region(region),
            detail={
                "latest_day": series[-1],
                "mean": round(sum(series) / len(series), 1),
                "points": len(series),
                "series_tail": series[-7:],
                "transport": "opencli",
            },
        )
