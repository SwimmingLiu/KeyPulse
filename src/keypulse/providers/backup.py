"""Secondary demand signals from open APIs.

These are not search indexes, but they are free, keyless, and stable, which
makes them a useful cross-check when the big indexes are blocked or gated:
Wikipedia pageviews track topical attention, Hacker News and GitHub track
developer interest.
"""

from __future__ import annotations

import urllib.parse

from ..http import FetchError, fetch
from ..models import Signal
from .base import Provider, normalise_region


class WikipediaSearch(Provider):
    """How many Wikipedia articles match the phrase.

    A phrase with tens of thousands of matching articles is an established
    topic; a made-up string matches only a handful.  Cheaper and far more
    reliable than pageviews (which need an exact article title).
    """

    name = "wikipedia_search"
    label = "维基百科匹配条目"
    metric = "matching_articles"
    unit = "hits"
    kind = "volume"
    tier = "corpus"
    regions = ("GLOBAL", "US", "CN")

    API = "https://{lang}.wikipedia.org/w/api.php"

    def signal(self, keyword: str, region: str = "GLOBAL") -> Signal:
        lang = "zh" if normalise_region(region) == "CN" else "en"
        # Exact-phrase search: without quotes Wikipedia matches documents that
        # contain the words anywhere, which inflates counts for common tokens.
        params = urllib.parse.urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": f'"{keyword}"',
                "format": "json",
                "srlimit": "1",
            }
        )
        url = f"{self.API.format(lang=lang)}?{params}"
        try:
            payload = fetch(url, self.settings, headers={"Accept": "application/json"}).json()
        except FetchError as exc:
            return self._failure(keyword, str(exc), region)
        except ValueError as exc:
            return self._failure(keyword, f"invalid Wikipedia payload: {exc}", region)
        hits = (payload.get("query") or {}).get("searchinfo", {}).get("totalhits")
        if hits is None:
            return self._failure(keyword, "no totalhits in Wikipedia response", region)
        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=self.metric,
            value=float(hits),
            unit=self.unit,
            region=normalise_region(region),
            detail={"lang": lang},
        )


class WikipediaPageviews(Provider):
    name = "wikipedia_pageviews"
    label = "维基百科浏览量"
    metric = "monthly_pageviews"
    unit = "per_month"
    kind = "pageviews"
    tier = "corpus"
    regions = ("GLOBAL", "US", "CN")

    API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"

    def signal(self, keyword: str, region: str = "GLOBAL") -> Signal:
        lang = "zh" if normalise_region(region) == "CN" else "en"
        # The pageviews endpoint needs the exact article title.  Multi-word and
        # Chinese topics usually differ from the query, so look the title up
        # first; only fall back to guessing when the lookup fails.
        titles = self._resolve_titles(keyword, lang)
        last_error = "no article matched this keyword"
        for article_title in titles:
            article = urllib.parse.quote(article_title.replace(" ", "_"))
            url = (
                f"{self.API}/{lang}.wikipedia/all-access/user/{article}"
                "/monthly/20250101/20251031"
            )
            try:
                payload = fetch(url, self.settings, headers={"Accept": "application/json"}).json()
            except FetchError as exc:
                last_error = str(exc)
                continue
            except ValueError as exc:
                last_error = f"invalid pageviews payload: {exc}"
                continue
            items = payload.get("items") or []
            if not items:
                continue
            recent = items[-1]
            return Signal(
                keyword=keyword,
                provider=self.name,
                metric=self.metric,
                value=float(recent.get("views", 0)),
                unit=self.unit,
                region=normalise_region(region),
                detail={"article": recent.get("article"), "month": recent.get("timestamp", "")[:6]},
            )
        return self._failure(keyword, last_error, region)

    def _resolve_titles(self, keyword: str, lang: str) -> list[str]:
        """Best-guess article titles for a query, most specific first."""
        guesses = [keyword, keyword.replace(" ", ""), keyword.replace(" ", "_")]
        opensearch_url = (
            f"https://{lang}.wikipedia.org/w/api.php?"
            + urllib.parse.urlencode(
                {"action": "opensearch", "search": keyword, "limit": "1", "format": "json"}
            )
        )
        try:
            payload = fetch(
                opensearch_url, self.settings, headers={"Accept": "application/json"}
            ).json()
            if isinstance(payload, list) and len(payload) > 1 and payload[1]:
                guesses.insert(0, payload[1][0])
        except (FetchError, ValueError):
            # Title lookup is an optimisation; the literal guesses may still hit.
            pass
        return list(dict.fromkeys(g for g in guesses if g))


class HackerNewsMentions(Provider):
    name = "hn_mentions"
    label = "Hacker News 提及"
    metric = "story_mentions"
    unit = "hits"
    kind = "volume"
    tier = "audience"
    regions = ("GLOBAL", "US")

    API = "https://hn.algolia.com/api/v1/search"

    def signal(self, keyword: str, region: str = "GLOBAL") -> Signal:
        params = urllib.parse.urlencode({"query": keyword, "tags": "story"})
        try:
            payload = fetch(f"{self.API}?{params}", self.settings).json()
        except FetchError as exc:
            return self._failure(keyword, str(exc), region)
        except ValueError as exc:
            return self._failure(keyword, f"invalid HN payload: {exc}", region)
        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=self.metric,
            value=float(payload.get("nbHits", 0)),
            unit=self.unit,
            region=normalise_region(region),
        )


class GitHubRepos(Provider):
    name = "github_repos"
    label = "GitHub 仓库数"
    metric = "repo_count"
    unit = "hits"
    kind = "volume"
    tier = "audience"
    regions = ("GLOBAL", "US", "CN")

    API = "https://api.github.com/search/repositories"

    def signal(self, keyword: str, region: str = "GLOBAL") -> Signal:
        params = urllib.parse.urlencode({"q": keyword, "per_page": "1"})
        headers = {"Accept": "application/vnd.github+json"}
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        try:
            payload = fetch(f"{self.API}?{params}", self.settings, headers=headers).json()
        except FetchError as exc:
            return self._failure(keyword, str(exc), region)
        except ValueError as exc:
            return self._failure(keyword, f"invalid GitHub payload: {exc}", region)
        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=self.metric,
            value=float(payload.get("total_count", 0)),
            unit=self.unit,
            region=normalise_region(region),
        )
