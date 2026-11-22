"""微信指数 (WeChat Index) over HTTP, using the mini-program's own protocol.

This is the real 微信指数 (the search index the「微信指数」mini-program shows),
not Sogou's WeChat article count — that one lives in ``wechat_browser.py`` and
is a separate, always-available proxy.

The protocol was recovered by unpacking the mini-program itself
(``__APP__.wxapkg``, the ``V1MMWX`` container, XOR 0x33 body):

* endpoint — ``POST https://search.weixin.qq.com/cgi-bin/wxaweb/wxindex``
* body — ``{"openid": …, "search_key": …, "cgi_name": "GetDefaultIndex",
  "query": ["关键词", …], "compound_word": [], "start_ymd": "YYYYMMDD",
  "end_ymd": "YYYYMMDD", "is_beta": 1}``
* signature — header ``X-Request-Sign`` = ``md5(search_key + query.join(""))``
  (``cgi/cgi.js``: ``p([t,r].join("")).toString()`` with ``r = query.join("")``)
* response — ``{code: 0, content: {resp_list: [{query, status,
  indexes: [{ctype, cvalue, time_indexes: [{time, score}, …]}]}]}}``
  with one point per day, oldest first.

Credentials come from ``weapplogin``, which the mini-program calls on launch::

    POST /cgi-bin/searchweb/weapplogin  {appid, js_code}   (wx.login code)
    -> {"data": {"openid": "...", "search_key": "..."}, "errcode": 0}

``search_key`` is short-lived (it embeds a millisecond timestamp and the
server rejects it after roughly an hour or two), so an expired key is
reported as "需要刷新" rather than as a network failure — retrying such a key
is pointless.  ``scripts/wechat-index/` reads the key straight out of the
WeChat client's own webview cache, so refreshing is one command.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta

from ..http import FetchError, fetch
from ..models import Signal
from .base import Provider, normalise_region

HOST = "search.weixin.qq.com"
CGI = "wxindex"

#: How far back to ask for.  微信指数 serves 365 days; asking for less keeps
#: the response small while still giving a week of trend in ``detail``.
LOOKBACK_DAYS = 30


def _today() -> datetime:
    return datetime.now()


def request_sign(search_key: str, queries: list[str]) -> str:
    """``X-Request-Sign`` exactly as ``cgi/cgi.js`` builds it.

    ``md5(search_key + query.map(...).join(""))`` — the query list is
    concatenated with no separator, before any URL encoding.
    """
    joined = "".join(queries)
    return hashlib.md5((search_key + joined).encode("utf-8")).hexdigest()


def build_body(
    openid: str,
    search_key: str,
    queries: list[str],
    start_ymd: str,
    end_ymd: str,
) -> dict:
    """The request body ``cgi/index.js`` sends for ``GetDefaultIndex``."""
    return {
        "openid": openid,
        "search_key": search_key,
        "cgi_name": "GetDefaultIndex",
        "query": list(queries),
        "compound_word": [],
        "start_ymd": start_ymd,
        "end_ymd": end_ymd,
        "is_beta": 1,
    }


def parse_latest(content: dict) -> tuple[int | None, list[float], str]:
    """Pull the newest ``score`` out of a ``GetDefaultIndex`` response.

    Returns ``(latest, series, query)``.  ``latest`` is ``None`` when the
    keyword came back with no points, which is how 微信指数 says "暂未收录".

    ``resp_list`` is positional — ``resp_list[i]`` answers ``query[i]`` — so
    the caller asks about one keyword at a time and reads entry 0.  Reading
    the first non-empty entry instead would attribute one keyword's number to
    another when several were queried at once.
    """
    resp_list = (content or {}).get("resp_list") or []
    if not resp_list:
        return None, [], ""
    first = resp_list[0]
    query = str(first.get("query") or "")
    series: list[float] = []
    for index in first.get("indexes") or []:
        for point in index.get("time_indexes") or []:
            score = point.get("score")
            if isinstance(score, (int, float)):
                series.append(float(score))
    if not series:
        return None, [], query
    return int(series[-1]), series, query


class WeChatIndex(Provider):
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
                "需要 openid + search_key（运行 keypulse wechat-token 从微信客户端读取）",
            )
        return True, "ok (mini-program protocol)"

    def signal(self, keyword: str, region: str = "CN") -> Signal:
        recorded = self._replay(keyword)
        if recorded:
            return self._replayed_signal(keyword, region, recorded)
        openid, key = self._credentials()
        if not openid or not key:
            return self._failure(
                keyword,
                "缺少 openid / search_key（运行 keypulse wechat-token 从微信客户端读取）",
                region,
            )
        return self._one(keyword, region, openid, key)

    def _one(self, keyword: str, region: str, openid: str, key: str) -> Signal:
        end = _today()
        start = end - timedelta(days=LOOKBACK_DAYS)
        queries = [keyword]
        body = build_body(
            openid,
            key,
            queries,
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
        )
        headers = {
            "Content-Type": "application/json",
            "X-Request-Sign": request_sign(key, queries),
            "Accept": "application/json, text/plain, */*",
        }
        url = f"https://{HOST}/cgi-bin/wxaweb/{CGI}"
        try:
            response = fetch(
                url,
                self.settings,
                headers=headers,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                # Never cache an auth failure: the key is refreshable, so the
                # next run must actually retry rather than replay a rejection.
                use_cache=True,
            )
        except FetchError as exc:
            return self._failure(keyword, str(exc), region)

        try:
            payload = response.json()
        except ValueError:
            return self._failure(keyword, f"响应不是 JSON：{response.text[:120]}", region)

        code = payload.get("code")
        if code in (0, None) and not payload.get("content"):
            return self._failure(keyword, "响应里没有 content", region)
        if code == -10000:
            return self._failure(
                keyword,
                "微信指数凭据失效（auth failed）：运行 keypulse wechat-token 刷新 search_key",
                region,
            )
        if code == -10002:
            # The CGI answers -10002 with an empty resp_list for an
            # unindexed word, which is a result, not an error.
            return Signal(
                keyword=keyword,
                provider=self.name,
                metric=self.metric,
                value=0.0,
                unit=self.unit,
                region=normalise_region(region),
                detail={
                    "unindexed": True,
                    "note": "微信指数未收录该词",
                    "transport": "wxaweb",
                },
            )
        if code not in (0, -10002):
            return self._failure(
                keyword, f"微信指数返回 code {code}: {payload.get('msg', '')}", region
            )

        latest, series, answered = parse_latest(payload.get("content") or {})
        if latest is None:
            return Signal(
                keyword=keyword,
                provider=self.name,
                metric=self.metric,
                value=0.0,
                unit=self.unit,
                region=normalise_region(region),
                detail={
                    "unindexed": True,
                    "note": "微信指数未收录该词",
                    "transport": "wxaweb",
                },
            )
        # A positional answer for a different word means the server matched
        # something else; report nothing rather than the wrong keyword's index.
        if answered and answered != keyword:
            return self._failure(
                keyword,
                f"服务端返回的是「{answered}」的指数，不是「{keyword}」，已丢弃",
                region,
            )
        prev = series[-2] if len(series) > 1 else None
        change = None
        if prev:
            change = round((series[-1] - prev) / prev * 100, 2)
        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=self.metric,
            value=float(latest),
            unit=self.unit,
            region=normalise_region(region),
            detail={
                "latest_day": int(latest),
                "prev_day": int(prev) if prev is not None else None,
                "change_pct": change,
                "points": len(series),
                "series_tail": [int(x) for x in series[-7:]],
                "window": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")},
                "note": "微信指数小程序协议（wxaweb/wxindex）",
                "transport": "wxaweb",
            },
        )
