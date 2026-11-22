"""Provider registry construction.

``build_registry`` is the single place that knows the concrete provider set;
the CLI, the scoring layer and the plugin skill all go through it.
"""

from __future__ import annotations

from ..config import Settings
from .backup import GitHubRepos, HackerNewsMentions, WikipediaPageviews, WikipediaSearch
from .baidu import BaiduIndex, BaiduSuggest
from .baidu_browser import BaiduIndexBrowser
from .baidu_cdp import BaiduIndexCDP
from .douyin_browser import DouyinIndexBrowser
from .wechat_browser import SogouWeChatBrowser
from .wechat_desktop import WeChatDesktopIndex
from .wechat_index_browser import WeChatIndex
from .xiaohongshu_browser import XiaohongshuBrowser
from .base import Provider, Registry
from .google_trends import GoogleTrends
from .serp import BingWeb, So360Web, SogouWeChat
from .suggest import BingSuggest, GoogleSuggest, So360Suggest, SogouSuggest

#: Providers whose value measures competition/supply instead of demand.
#: 360's count is a rendered page estimate that plateaus, so it is advisory
#: only and never enters the competition estimate.
COMPETITION_PROVIDERS = {"bing_web"}

#: Providers that report a search-demand index directly — what the user means
#: by "搜索引擎关键词指数".  When at least one answers, the score is index-only.
INDEX_PROVIDERS = (
    "baidu_index",
    "google_trends",
    "douyin_index",
    "wechat_index",
    "wechat_desktop_index",
)
#: Stand-ins used only when no index provider is reachable.
FALLBACK_PROVIDERS = (
    "sogou_wechat",
    "wikipedia_pageviews",
    "wikipedia_search",
    "xiaohongshu_notes",
)
#: Developer-audience sources; excluded from the main score by default.
AUDIENCE_PROVIDERS = ("github_repos", "hn_mentions")


def build_registry(settings: Settings) -> Registry:
    providers: list[Provider] = [
        _baidu_chain(settings)(settings),
        GoogleTrends(settings),
        BaiduSuggest(settings),
        GoogleSuggest(settings),
        BingSuggest(settings),
        So360Suggest(settings),
        SogouSuggest(settings),
        WikipediaPageviews(settings),
        WikipediaSearch(settings),
        HackerNewsMentions(settings),
        GitHubRepos(settings),
        BingWeb(settings),
        _prefer_browser(SogouWeChatBrowser, SogouWeChat, settings)(settings),
        DouyinIndexBrowser(settings),
        WeChatIndex(settings),
        WeChatDesktopIndex(settings),
        XiaohongshuBrowser(settings),
        So360Web(settings),
    ]
    return Registry(providers)


def _prefer_browser(browser_cls, http_cls, settings: Settings):
    """Pick the browser-backed provider when a bridge is available.

    Both expose the same provider name, so only one can be registered; the
    browser variant is strictly better when it works (Google/Baidu gate the HTTP
    endpoints), and the HTTP variant is the no-browser fallback.

    ``--offline`` disables the bridge entirely: offline means "answer from the
    on-disk cache", and a browser request is still a network request.
    """
    import os

    # Replay mode serves recorded values, so it needs neither a browser nor a
    # session — pick the browser class (where the replay check lives) whenever
    # the bridge exists OR a recording is configured.
    replay_available = bool(os.environ.get("KEYPULSE_REPLAY")) or any(
        settings.cache_dir.glob("replay-*.json")
    ) if settings.cache_dir.exists() else bool(os.environ.get("KEYPULSE_REPLAY"))
    if settings.offline and not replay_available:
        return http_cls
    from ..opencli import available

    ok, _ = available()
    if ok or os.environ.get("KEYPULSE_REPLAY"):
        return browser_cls
    return http_cls


def _baidu_chain(settings: Settings) -> type:
    """百度指数: local browser over CDP → opencli bridge → plain HTTP cookie.

    Baidu's JSON API answers ``status 10018`` to everything that is not the
    page's own XHR, so the cookie-only path is the last resort rather than
    the first.

    The CDP provider comes first because it needs no external tool and no
    browser extension — just the Chrome-family browser already installed —
    and it is much faster (one navigation per keyword against a page that is
    already logged in, ~0.5 s, versus ~7 s through the bridge).  opencli
    stays as the next step for machines where launching a browser is blocked.
    """
    import os

    from .. import cdp

    replay = bool(os.environ.get("KEYPULSE_REPLAY"))
    if replay:
        return BaiduIndexBrowser
    if settings.offline:
        return BaiduIndex
    if cdp.available()[0]:
        return BaiduIndexCDP
    from ..opencli import available

    if available()[0]:
        return BaiduIndexBrowser
    return BaiduIndex


def registry_without(settings: Settings, excluded: set[str]) -> Registry:
    """Registry with ``excluded`` providers removed (used for fallback tiers)."""
    full = build_registry(settings)
    return Registry([p for p in full if p.name not in excluded])


__all__ = [
    "build_registry",
    "registry_without",
    "COMPETITION_PROVIDERS",
    "INDEX_PROVIDERS",
    "FALLBACK_PROVIDERS",
    "AUDIENCE_PROVIDERS",
]
