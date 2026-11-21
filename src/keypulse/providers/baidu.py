"""百度指数 with the published ptbk decryption scheme.

Baidu returns the series as a ciphertext string and a same-response key
(``ptbk``) whose first half maps to its second half.  Requires a logged-in
cookie (``KEYPULSE_BAIDU_COOKIE``).
"""

from __future__ import annotations

import json
import urllib.parse

from ..http import JSON_HEADERS, FetchError, fetch
from ..models import Signal
from .base import Provider, normalise_region
from .suggest import _AutocompleteProvider


class BaiduIndex(Provider):
    name = "baidu_index"
    label = "百度指数"
    metric = "index"
    unit = "raw"
    kind = "index"
    regions = ("CN",)
    requires_session = True
    tier = "index"
    access = "needs_cookie"

    API = "https://index.baidu.com/api/SearchApi/index"
    PTBK = "https://index.baidu.com/Interface/ptbk"

    def check(self) -> tuple[bool, str]:
        if not self.settings.baidu_cookie:
            return False, "需要 KEYPULSE_BAIDU_COOKIE（index.baidu.com 登录后的 BDUSS）"
        return True, "ok"

    def signal(self, keyword: str, region: str = "CN") -> Signal:
        if not self.settings.baidu_cookie:
            return self._failure(keyword, "未配置百度指数 Cookie", region)
        headers = {
            **JSON_HEADERS,
            "Referer": "https://index.baidu.com/v2/main/index.html",
            "Cookie": self.settings.baidu_cookie,
        }
        words = urllib.parse.quote(json.dumps([[{"name": keyword, "wordType": 1}]], ensure_ascii=False))
        try:
            payload = fetch(f"{self.API}?area=0&word={words}&days=30", self.settings, headers=headers).json()
        except FetchError as exc:
            return self._failure(keyword, str(exc), region)
        except ValueError as exc:
            return self._failure(keyword, f"百度指数返回非 JSON: {exc}", region)

        if payload.get("status") == 10018:
            return self._failure(keyword, "百度风控拦截（建议降低频率或提供有效 Cookie）", region)
        if payload.get("status") != 0:
            return self._failure(keyword, f"百度指数错误: {payload.get('message') or payload.get('status')}", region)

        data = payload.get("data") or {}
        uniqid = data.get("uniqid")
        try:
            entry = data["userIndexes"][0]
            ciphertext = entry["all"]["data"]
        except (KeyError, IndexError, TypeError) as exc:
            return self._failure(keyword, f"百度指数结构异常: {exc}", region)

        try:
            key = _fetch_ptbk(uniqid, self.settings, self.PTBK, headers) if uniqid else ""
            series = decode_series(key, ciphertext)
        except (FetchError, ValueError, KeyError) as exc:
            return self._failure(keyword, f"百度指数解密失败: {exc}", region)
        if not series:
            return self._failure(keyword, "百度指数返回空序列", region)

        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=self.metric,
            value=round(sum(series) / len(series), 1),
            unit=self.unit,
            region=normalise_region(region),
            detail={"window": "近 30 天", "peak": max(series), "days": len(series)},
        )


class BaiduSuggest(_AutocompleteProvider):
    name = "baidu_suggest"
    label = "百度下拉词"
    regions = ("CN", "GLOBAL")

    API = "https://www.baidu.com/sugrec"

    def complete(self, seed: str) -> list[str]:
        url = f"{self.API}?prod=pc&wd={urllib.parse.quote(seed)}&json=1"
        payload = fetch(url, self.settings, headers=JSON_HEADERS).json()
        return [item["q"] for item in (payload.get("g") or []) if item.get("q")]


def _fetch_ptbk(uniqid: str, settings, ptbk_url: str, headers) -> str:
    """Fetch the per-response decryption key Baidu hands out with the series."""
    payload = fetch(
        f"{ptbk_url}?uniqid={urllib.parse.quote(uniqid)}", settings, headers=headers
    ).json()
    key = payload.get("data")
    if not key:
        raise ValueError("百度未返回解密密钥 (ptbk)")
    return key


def decode_series(key: str, ciphertext: str) -> list[float]:
    """Decode a Baidu Index ciphertext with its ptbk key.

    The key is two halves: character *i* of the first half maps to character
    *i* of the second, and each ciphertext character stands for one plaintext
    character.  The plaintext is a comma-separated list of day values; empty
    slots mean "no data that day" and decode to 0.
    """
    if not ciphertext:
        return []
    if not key:
        raise ValueError("缺少解密密钥 (ptbk)")
    half = len(key) // 2
    mapping = dict(zip(key[:half], key[half:]))
    plain = "".join(mapping.get(ch, "") for ch in ciphertext)
    return [float(token) if token.strip() else 0.0 for token in plain.split(",")]
