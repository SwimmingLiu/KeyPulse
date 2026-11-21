"""SERP hit-count providers — how much content exists around a phrase.

Result counts are a *supply/competition* signal rather than pure demand: a high
count means the term is well-established and contested, which is useful for
ranking keyword opportunity.  Counts are read from the "N results" string the
search engines render in the results header.
"""

from __future__ import annotations

import re
import urllib.parse

from ..http import BROWSER_HEADERS, FetchError, fetch, is_block_page
from ..models import Signal
from .base import Provider, normalise_region

_COUNT_PATTERNS = {
    "en": re.compile(r"([\d,]+)\s+results", re.I),
    "zh": re.compile(r"找到约?([\d,]+)条|约([\d,]+)条结果|([\d,]+)\s*条结果"),
}


class _SerpCountProvider(Provider):
    kind = "serp_hits"
    metric = "result_count"
    unit = "hits"
    tier = "competition"
    queries = 1

    def count(self, keyword: str) -> tuple[int, dict]:
        raise NotImplementedError

    def _parse_count(self, html: str, lang: str = "en") -> int | None:
        match = _COUNT_PATTERNS[lang].search(html)
        if not match:
            return None
        value = next((g for g in match.groups() if g), None)
        return int(value.replace(",", "")) if value else None

    def signal(self, keyword: str, region: str = "GLOBAL") -> Signal:
        try:
            count, detail = self.count(keyword)
        except FetchError as exc:
            return self._failure(keyword, str(exc), region)
        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=self.metric,
            value=float(count),
            unit=self.unit,
            region=normalise_region(region),
            detail=detail,
        )


class BingWeb(_SerpCountProvider):
    name = "bing_web"
    label = "Bing 结果数"
    regions = ("GLOBAL", "US", "CN", "GB", "JP", "DE", "IN", "BR")

    API = "https://www.bing.com/search"

    def count(self, keyword: str) -> tuple[int, dict]:
        params = urllib.parse.urlencode(
            {"q": f'"{keyword}"', "count": "50", "mkt": "en-US", "setlang": "en"}
        )
        html = fetch(f"{self.API}?{params}", self.settings, headers=BROWSER_HEADERS).text
        count = self._parse_count(html, "en")
        if count is None:
            raise FetchError("Bing returned no result count (layout change or block)")
        return count, {"query": f'"{keyword}"', "market": "en-US"}


class SogouWeChat(_SerpCountProvider):
    """WeChat article volume via Sogou's WeChat vertical.

    This is the closest public proxy for 微信指数: it counts the WeChat
    articles indexed for a phrase.  The official WeChat Index lives behind a
    mini-program session token and has no public API.
    """

    name = "sogou_wechat"
    label = "微信文章数(搜狗)"
    tier = "corpus"
    regions = ("CN", "GLOBAL")

    API = "https://weixin.sogou.com/weixin"

    def count(self, keyword: str) -> tuple[int, dict]:
        params = urllib.parse.urlencode({"type": "2", "query": keyword})
        html = fetch(f"{self.API}?{params}", self.settings, headers=BROWSER_HEADERS).text
        if is_block_page(html):
            raise FetchError("搜狗微信触发反爬验证（本机 IP 被限流），稍后重试或换网络出口")
        count = self._parse_count(html, "zh")
        if count is None:
            raise FetchError("搜狗微信未返回结果数")
        return count, {"vertical": "wechat_articles"}


class So360Web(_SerpCountProvider):
    """360 result counts are rendered as "约N个" and only appear for some queries.

    So.com also caps what it reports, so this source is advisory: it contributes
    to the competition estimate but never to the demand score.
    """

    name = "so360_web"
    label = "360 结果数"
    regions = ("CN", "GLOBAL")

    API = "https://www.so.com/s"

    def count(self, keyword: str) -> tuple[int, dict]:
        params = urllib.parse.urlencode({"q": keyword})
        html = fetch(f"{self.API}?{params}", self.settings, headers=BROWSER_HEADERS).text
        match = re.search(r"约([\d,]+)个", html)
        if not match:
            raise FetchError("360 未返回结果数（页面未渲染总数）")
        return int(match.group(1).replace(",", "")), {"engine": "so.com"}
