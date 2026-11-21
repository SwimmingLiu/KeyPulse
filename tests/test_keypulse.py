"""Offline unit tests — no network, no cache, no credentials.

Run with:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
import urllib.request
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from keypulse.config import Settings
from keypulse.expand import add_modifiers, detect_language, expand_grid, suggest_seeds
from keypulse.models import Signal
from keypulse.providers import (
    AUDIENCE_PROVIDERS,
    FALLBACK_PROVIDERS,
    INDEX_PROVIDERS,
    build_registry,
)
from keypulse.providers.base import normalise_region
from keypulse.scoring import (
    AUDIENCE_WEIGHTS,
    DEFAULT_WEIGHTS,
    DISCOVERY_ONLY,
    FALLBACK_WEIGHTS,
    mode_for,
    normalise,
    score_keywords,
)


def settings(**overrides) -> Settings:
    base = Settings(offline=False, cache_ttl=0, retries=0)
    if not any(k in overrides for k in ("offline", "cache_dir")):
        # Keep file-based config out of the way unless a test opts in.
        base.cache_dir = Path("/tmp/keypulse-test-cache")
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def signal(provider: str, keyword: str, value: float, *, unit: str = "hits", metric: str | None = None) -> Signal:
    return Signal(
        keyword=keyword,
        provider=provider,
        metric=metric or "test_metric",
        value=value,
        unit=unit,
    )


class NormalisationTests(unittest.TestCase):
    def test_bounded_index_passes_through(self):
        self.assertEqual(normalise(signal("google_trends", "ai", 42, unit="scaled_0_100")), 42)

    def test_bounded_index_clamps(self):
        self.assertEqual(normalise(signal("google_trends", "ai", 250, unit="scaled_0_100")), 100)

    def test_counts_are_log_scaled_and_ordered(self):
        low = normalise(signal("wikipedia_search", "a", 10))
        high = normalise(signal("wikipedia_search", "b", 1000))
        self.assertLess(low, high)
        self.assertLess(high, 100)
        self.assertGreater(low, 0)

    def test_zero_value_normalises_to_zero(self):
        self.assertEqual(normalise(signal("wikipedia_search", "a", 0)), 0)

    def test_failed_signal_is_unusable(self):
        failed = Signal.failure("a", "baidu_index", "index", "blocked")
        self.assertIsNone(normalise(failed))

    def test_unknown_provider_is_bounded(self):
        value = normalise(signal("mystery", "a", 10**9))
        self.assertLessEqual(value, 100)
        self.assertGreaterEqual(value, 0)


class ScoringTests(unittest.TestCase):
    def test_failed_source_lowers_confidence_not_score(self):
        full = score_keywords(
            [
                signal("baidu_index", "ai", 50, unit="scaled_0_100", metric="index"),
                signal("google_trends", "ai", 50, unit="scaled_0_100"),
            ],
            expected_providers=list(DEFAULT_WEIGHTS),
        )[0]
        partial = score_keywords(
            [
                signal("baidu_index", "ai", 50, unit="scaled_0_100", metric="index"),
                Signal.failure("ai", "google_trends", "interest", "429"),
            ],
            expected_providers=list(DEFAULT_WEIGHTS),
        )[0]

        self.assertAlmostEqual(full.score, partial.score, places=6)
        self.assertGreater(full.confidence, partial.confidence)
        self.assertIn("google_trends", partial.errors)

    def test_discovery_only_providers_do_not_move_the_score(self):
        without = score_keywords(
            [signal("wikipedia_search", "ai", 500)],
            weights=FALLBACK_WEIGHTS,
            expected_providers=["wikipedia_search"],
        )[0]
        with_suggest = score_keywords(
            [
                signal("wikipedia_search", "ai", 500),
                signal("google_suggest", "ai", 100, unit="percent", metric="suggest_affinity"),
            ],
            weights=FALLBACK_WEIGHTS,
            expected_providers=["wikipedia_search", "google_suggest"],
        )[0]
        self.assertAlmostEqual(without.score, with_suggest.score, places=6)

    def test_audience_sources_excluded_by_default(self):
        signals = [
            signal("wikipedia_search", "cat food", 500),
            signal("github_repos", "cat food", 900_000),
        ]
        default = score_keywords(
            signals, weights=FALLBACK_WEIGHTS, expected_providers=["wikipedia_search"]
        )[0]
        audience = score_keywords(
            signals,
            weights={**FALLBACK_WEIGHTS, **AUDIENCE_WEIGHTS},
            expected_providers=["wikipedia_search", *AUDIENCE_PROVIDERS],
        )[0]
        self.assertNotIn("github_repos", default.components)
        self.assertIn("github_repos", audience.components)

    def test_competition_gaps_opportunity(self):
        signals = [
            signal("wikipedia_search", "kw", 500),
            signal("bing_web", "kw", 20_000_000),
        ]
        record = score_keywords(
            signals, weights=FALLBACK_WEIGHTS, expected_providers=["wikipedia_search"]
        )[0]
        self.assertIsNotNone(record.competition)
        self.assertLess(record.opportunity, record.score)

    def test_ranking_puts_highest_demand_first(self):
        # A ranking needs relative demand, which one source cannot express on
        # its own: the log curve compresses any single source to the same place.
        ranked = score_keywords(
            [
                signal("wikipedia_search", "low", 5),
                signal("wikipedia_search", "high", 5000),
                signal("wikipedia_search", "mid", 300),
                signal("wikipedia_pageviews", "low", 10),
                signal("wikipedia_pageviews", "high", 200_000),
                signal("wikipedia_pageviews", "mid", 20_000),
            ],
            weights=FALLBACK_WEIGHTS,
            expected_providers=["wikipedia_search", "wikipedia_pageviews"],
        )
        self.assertEqual([r.keyword for r in ranked], ["high", "mid", "low"])

    def test_no_signals_means_zero_not_crash(self):
        record = score_keywords([], expected_providers=["wikipedia_search"])
        self.assertEqual(record, [])

    def test_mode_detection(self):
        self.assertEqual(mode_for(DEFAULT_WEIGHTS), "index")
        self.assertEqual(mode_for(FALLBACK_WEIGHTS), "fallback")


class SeedTests(unittest.TestCase):
    def test_detects_chinese_and_english(self):
        self.assertEqual(detect_language("面向程序员的 AI 笔记软件"), "zh")
        self.assertEqual(detect_language("an AI note taking app"), "en")

    def test_chinese_scenario_yields_whole_phrases(self):
        seeds = suggest_seeds("我想做一款面向程序员的 AI 编程笔记软件，支持代码片段管理和知识库检索")
        self.assertIn("编程笔记软件", seeds)
        self.assertIn("代码片段管理", seeds)
        # Fragments from a sliding n-gram window must not appear.
        for seed in seeds:
            self.assertFalse(seed.startswith("款面"), seed)

    def test_english_scenario_drops_stopwords(self):
        seeds = suggest_seeds("I want to build an AI note taking app for developers")
        self.assertIn("developers", seeds)
        self.assertNotIn("want", seeds)
        self.assertNotIn("the", seeds)

    def test_empty_scenario(self):
        self.assertEqual(suggest_seeds("   "), [])

    def test_modifiers_append_in_language(self):
        zh = add_modifiers(["AI笔记"], language="zh")
        self.assertIn("AI笔记哪个好", zh)
        en = add_modifiers(["ai notes"], language="en")
        self.assertIn("ai notes tutorial", en)

    def test_expand_grid_cross_product(self):
        pairs = expand_grid(["ai notes"], ["教程", "推荐"])
        # ASCII seeds get a space before a CJK intent suffix.
        self.assertIn("ai notes 教程", pairs)
        self.assertIn("ai notes 推荐", pairs)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.settings = settings()
        self.registry = build_registry(self.settings)

    def test_all_provider_names_unique(self):
        names = self.registry.names()
        self.assertEqual(len(names), len(set(names)))

    def test_tiers_are_disjoint_and_known(self):
        known = set(self.registry.names())
        for group in (INDEX_PROVIDERS, FALLBACK_PROVIDERS, AUDIENCE_PROVIDERS):
            self.assertTrue(set(group) <= known)
        self.assertFalse(set(INDEX_PROVIDERS) & set(FALLBACK_PROVIDERS))
        self.assertFalse(set(FALLBACK_PROVIDERS) & set(AUDIENCE_PROVIDERS))

    def test_discovery_only_matches_suggest_providers(self):
        suggest_names = {p.name for p in self.registry if p.kind == "suggest"}
        self.assertEqual(suggest_names, set(DISCOVERY_ONLY))

    def test_region_normalisation(self):
        self.assertEqual(normalise_region("cn"), "CN")
        self.assertEqual(normalise_region("china"), "CN")
        self.assertEqual(normalise_region("worldwide"), "GLOBAL")
        self.assertEqual(normalise_region(""), "GLOBAL")

    def test_cn_only_sources_do_not_answer_for_us(self):
        baidu = self.registry.get("baidu_index")
        self.assertTrue(baidu.supports("CN"))
        self.assertFalse(baidu.supports("US"))

    def test_auto_selection_respects_region(self):
        cn_names = {p.name for p in self.registry.select(["auto"], "CN")}
        us_names = {p.name for p in self.registry.select(["auto"], "US")}
        self.assertIn("baidu_index", cn_names)
        self.assertNotIn("baidu_index", us_names)
        self.assertGreater(len(cn_names), 0)

    def test_unknown_provider_raises(self):
        with self.assertRaises(KeyError):
            self.registry.get("nope")

    def test_baidu_index_is_honest_about_its_requirements(self):
        # Either transport must say plainly what it needs: the browser bridge,
        # or a logged-in cookie. It must never claim to be ready when it isn't.
        provider = self.registry.get("baidu_index")
        ok, reason = provider.check()
        if ok:
            self.assertIn("ok", reason.lower())
        else:
            self.assertTrue("COOKIE" in reason.upper() or "浏览器" in reason, reason)

    def test_unconfigured_providers_fail_cleanly(self):
        # With no browser bridge and no cookie, Baidu must report a reason
        # rather than a misleading zero — verified offline via the HTTP class,
        # which is what the registry falls back to.
        from keypulse.providers.baidu import BaiduIndex

        result = BaiduIndex(self.settings).signal("测试", "CN")
        self.assertFalse(result.ok)
        self.assertTrue(result.error)


if __name__ == "__main__":
    unittest.main()


class EndToEndTests(unittest.TestCase):
    """Runs the real scan pipeline with every network call stubbed out."""

    def test_scan_with_stubbed_providers_ranks_without_network(self):
        from keypulse.providers.base import Provider
        from keypulse.scan import ScanRequest, run_scan

        class Stub(Provider):
            name = "wikipedia_search"
            label = "stub"
            tier = "corpus"
            regions = ("GLOBAL",)

            def signal(self, keyword, region="GLOBAL"):
                values = {"high": 4000.0, "mid": 400.0}
                return Signal(
                    keyword=keyword,
                    provider=self.name,
                    metric="matching_articles",
                    value=values.get(keyword, 1.0),
                    unit="hits",
                )

        import keypulse.scan as scan_module

        original = scan_module.build_registry
        try:
            scan_module.build_registry = lambda settings: __import__(
                "keypulse.providers.base", fromlist=["Registry"]
            ).Registry([Stub(settings)])
            result = run_scan(
                ScanRequest(keywords=["mid", "high"], region="CN", top=2),
                settings(offline=True),
            )
        finally:
            scan_module.build_registry = original

        self.assertEqual([r.keyword for r in result.scored], ["high", "mid"])
        payload = result.to_dict()
        self.assertEqual(payload["ranked"][0]["keyword"], "high")
        self.assertIn("mode", payload)

    def test_cli_parser_rejects_unknown_provider(self):
        from keypulse.cli import build_parser

        args = build_parser().parse_args(["scan", "-k", "a"])
        self.assertEqual(args.command, "scan")
        self.assertEqual(args.max_keywords, 25)


class CredentialTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.settings = settings(cache_dir=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_load_round_trip(self):
        from keypulse.login import load_stored_cookie, save_cookie

        path = save_cookie(self.settings, "BDUSS=abc; STOKEN=def")
        self.assertTrue(path.exists())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(load_stored_cookie(self.settings), "BDUSS=abc; STOKEN=def")

    def test_clear_removes_only_the_cookie(self):
        from keypulse.login import clear_cookie, credentials_path, load_stored_cookie, save_cookie

        save_cookie(self.settings, "BDUSS=abc")
        self.assertTrue(clear_cookie(self.settings))
        self.assertEqual(load_stored_cookie(self.settings), "")
        # The file survives (it may hold other settings); only the key is gone.
        self.assertTrue(credentials_path(self.settings).exists())
        self.assertFalse(clear_cookie(self.settings) and load_stored_cookie(self.settings))

    def test_missing_file_is_empty_not_an_error(self):
        from keypulse.login import load_stored_cookie

        self.assertEqual(load_stored_cookie(self.settings), "")

    def test_corrupt_file_is_ignored(self):
        from keypulse.login import credentials_path, load_stored_cookie

        path = credentials_path(self.settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(load_stored_cookie(self.settings), "")


class BaiduDecryptTests(unittest.TestCase):
    def test_ptbk_mapping_decrypts_series(self):
        # The published scheme: key's first half maps to its second half, and
        # each ciphertext character stands for one plaintext character.
        from keypulse.providers.baidu import decode_series

        # Cipher alphabet first, then the plaintext alphabet it maps to:
        # '1'->'2', 'X'->',', 'Y'->'7', 'Z'->'0'.
        key = "1XYZ2,70"
        # Cipher "1XY" decrypts to "2,7" -> two day values.
        self.assertEqual(decode_series(key, "1XY"), [2.0, 7.0])
        # A ciphertext character absent from the key maps to nothing, so the
        # resulting empty slot decodes as 0 rather than corrupting the list.
        self.assertEqual(decode_series(key, "1Q1X2"), [22.0, 0.0])

    def test_empty_ciphertext_yields_empty(self):
        from keypulse.providers.baidu import decode_series

        self.assertEqual(decode_series("anything", ""), [])

    def test_missing_key_is_an_error_not_a_zero(self):
        from keypulse.providers.baidu import decode_series

        with self.assertRaises(ValueError):
            decode_series("", "1X25")


class GoogleTrendsParsingTests(unittest.TestCase):
    def test_two_call_flow_is_averaged(self):
        import json

        import keypulse.providers.google_trends as gt

        explore = {
            "widgets": [
                {"id": "GEO_MAP", "token": "x", "request": {}},
                {
                    "id": "TIMESERIES",
                    "token": "tok",
                    "request": {"time": "today 12-m", "resolution": "WEEK"},
                },
            ]
        }
        series = {"default": {"timelineData": [{"value": [10]}, {"value": [40]}, {"value": [100]}]}}

        payload = json.dumps(explore)
        series_payload = json.dumps(series)

        def fake_fetch(url, settings, **kwargs):
            body = payload if "/explore" in url else series_payload
            return SimpleNamespace(json=lambda: __import__("json").loads(body))

        original = gt.fetch
        gt.fetch = fake_fetch
        try:
            # Force the HTTP transport; the browser path is tested separately.
            sig = gt.GoogleTrends(settings())._signal_via_http("pizza", "US")
        finally:
            gt.fetch = original

        self.assertTrue(sig.ok)
        self.assertEqual(sig.value, 50.0)   # mean of 10, 40, 100
        self.assertEqual(sig.unit, "scaled_0_100")

    def test_missing_timeseries_widget_is_an_error(self):
        import json

        import keypulse.providers.google_trends as gt

        empty = json.dumps({"widgets": []})

        def fake_fetch(url, settings, **kwargs):
            return SimpleNamespace(json=lambda: __import__("json").loads(empty))

        original = gt.fetch
        gt.fetch = fake_fetch
        try:
            sig = gt.GoogleTrends(settings())._signal_via_http("pizza", "US")
        finally:
            gt.fetch = original

        self.assertFalse(sig.ok)
        self.assertIsNone(normalise(sig))


class SingleSourceFallbackTests(unittest.TestCase):
    def test_single_source_ranks_by_raw_value(self):
        values = {"high": 5000, "mid": 300, "low": 5}
        signals = [
            signal("wikipedia_search", keyword, value) for keyword, value in values.items()
        ]
        ranked = score_keywords(
            signals, weights=FALLBACK_WEIGHTS, expected_providers=["wikipedia_search"]
        )
        self.assertEqual([r.keyword for r in ranked], ["high", "mid", "low"])
        self.assertEqual(ranked[0].score, 100.0)
        self.assertEqual(ranked[-1].score, 0.0)

    def test_rank_fallback_does_not_apply_to_multi_source(self):
        signals = [
            signal("wikipedia_search", "a", 1000),
            signal("wikipedia_search", "b", 10),
            signal("wikipedia_pageviews", "a", 10),
            signal("wikipedia_pageviews", "b", 10_000),
        ]
        ranked = score_keywords(
            signals,
            weights=FALLBACK_WEIGHTS,
            expected_providers=["wikipedia_search", "wikipedia_pageviews"],
        )
        # b wins on pageviews, so the blended score must not simply mirror search.
        self.assertEqual(ranked[0].keyword, "b")

    def test_rank_fallback_is_noop_for_single_keyword(self):
        ranked = score_keywords(
            [signal("wikipedia_search", "only", 42)],
            weights=FALLBACK_WEIGHTS,
            expected_providers=["wikipedia_search"],
        )
        self.assertLess(ranked[0].score, 100.0)


class TieringTests(unittest.TestCase):
    """The tier walk is the part most likely to regress silently."""

    def _run_with(self, providers, request_kwargs, keywords=("mid", "high")):
        from keypulse.providers.base import Provider, Registry
        from keypulse.scan import ScanRequest, run_scan
        import keypulse.scan as scan_module

        values = {"high": 4000.0, "mid": 400.0}

        class Stub(Provider):
            tier = "corpus"

            def __init__(self, settings, name):
                super().__init__(settings)
                self.name = name
                self.label = name

            def check(self):
                if self.name == "baidu_index":
                    return False, "no cookie"
                return True, "ok"

            def signal(self, keyword, region="GLOBAL"):
                return Signal(
                    keyword=keyword,
                    provider=self.name,
                    metric="matching_articles",
                    value=values.get(keyword, 1.0),
                    unit="hits",
                )

        registry = Registry([Stub(settings(), name) for name in providers])
        original = scan_module.build_registry
        try:
            scan_module.build_registry = lambda _settings: registry
            return run_scan(
                ScanRequest(keywords=list(keywords), region="CN", **request_kwargs),
                settings(offline=True),
            )
        finally:
            scan_module.build_registry = original

    def test_explicit_providers_score_even_when_discovery_only(self):
        result = self._run_with(
            ["wikipedia_search"], {"providers": ["wikipedia_search"], "top": 2}
        )
        self.assertEqual(result.mode, "explicit")
        top = result.scored[0]
        self.assertEqual(top.keyword, "high")
        self.assertIn("wikipedia_search", top.components)
        self.assertGreater(top.score, 0)

    def test_explicit_provider_missing_from_weights_still_scores(self):
        # A provider name the weight tables do not know must still be scored
        # when the user asked for it by name.
        result = self._run_with(["mystery_corpus"], {"providers": ["mystery_corpus"]})
        self.assertEqual(result.mode, "explicit")
        self.assertGreater(result.scored[0].score, 0)

    def test_index_tier_falls_through_to_fallback(self):
        result = self._run_with(
            ["baidu_index", "wikipedia_search"],
            {"top": 2},
        )
        # baidu_index has no cookie, so the fallback tier (stub wikipedia_search)
        # must produce the ranking rather than an empty result.
        self.assertEqual(result.mode, "fallback")
        self.assertEqual(result.scored[0].keyword, "high")


class ParserFixtureTests(unittest.TestCase):
    """Parsers are the brittle part; pin them against trimmed real markup."""

    def test_bing_result_count(self):
        from keypulse.providers.serp import BingWeb

        html = '<div id="b_results"><span class="sb_count">About 161,000 results</span></div>'
        self.assertEqual(BingWeb(settings())._parse_count(html, "en"), 161000)

    def test_chinese_result_count_variants(self):
        from keypulse.providers.serp import _SerpCountProvider

        class Probe(_SerpCountProvider):
            pass

        probe = Probe(settings())
        self.assertEqual(probe._parse_count("找到约12,345条", "zh"), 12345)
        self.assertEqual(probe._parse_count("约 8,000 条结果", "zh"), 8000)
        self.assertIsNone(probe._parse_count("没有结果数", "zh"))

    def test_block_page_detection(self):
        from keypulse.http import is_block_page

        self.assertTrue(is_block_page("<html>请输入验证码</html>"))
        self.assertTrue(is_block_page("<html>百度安全验证</html>"))
        self.assertTrue(is_block_page("<p>Our systems have detected unusual traffic</p>"))
        self.assertFalse(is_block_page("<html>找到约1,000条</html>"))
        self.assertFalse(is_block_page(""))

    def test_jsonp_stripping(self):
        from keypulse.http import strip_jsonp

        self.assertIn('"a"', strip_jsonp(')]}\'\n{"a":1}'))
        self.assertIn('"a"', strip_jsonp('window.sogou.sug(["a",[]]);'))
        self.assertEqual(strip_jsonp('{"a": 1}'), '{"a": 1}')

    def test_sogou_wechat_counts_articles(self):
        from keypulse.providers import serp

        original = serp.fetch

        class Fake:
            text = "<html><body>找到约12,345条</body></html>"

        serp.fetch = lambda *args, **kwargs: Fake()
        try:
            sig = serp.SogouWeChat(settings()).signal("量子计算", "CN")
        finally:
            serp.fetch = original
        self.assertTrue(sig.ok)
        self.assertEqual(sig.value, 12345)

    def test_sogou_wechat_block_is_an_error_not_zero(self):
        from keypulse.providers import serp

        original = serp.fetch

        class Fake:
            text = "<html><body>antispider 验证码</body></html>"

        serp.fetch = lambda *args, **kwargs: Fake()
        try:
            sig = serp.SogouWeChat(settings()).signal("量子计算", "CN")
        finally:
            serp.fetch = original
        self.assertFalse(sig.ok)
        self.assertIsNotNone(sig.error)
        self.assertEqual(sig.value, 0.0)

    def test_autocomplete_affinity(self):
        from keypulse.providers.suggest import affinity

        words = ["ai编程工具", "ai编程入门", "ai 编程排名", "airpods"]
        self.assertEqual(affinity("AI编程", words), 75.0)
        self.assertEqual(affinity("AI编程", []), 0.0)
        self.assertEqual(affinity("AI编程", words[:1]), 100.0)


class CacheTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.settings = settings(cache_dir=Path(self.tmp.name))
        self.settings.cache_ttl = 3600

    def tearDown(self):
        self.tmp.cleanup()

    def test_second_fetch_is_served_from_cache(self):
        from keypulse import http

        calls = []
        original = http._request

        def counting_request(url, headers, body, timeout):
            calls.append(url)
            return '{"ok": true}'

        http._request = counting_request
        try:
            first = http.fetch("https://example.test/a", self.settings)
            second = http.fetch("https://example.test/a", self.settings)
        finally:
            http._request = original

        self.assertEqual(len(calls), 1)
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)

    def test_offline_serves_cached_response_of_any_age(self):
        from keypulse import http

        original = http._request
        http._request = lambda *args: '{"ok": true}'
        try:
            http.fetch("https://example.test/b", self.settings)
        finally:
            http._request = original

        offline = settings(cache_dir=Path(self.tmp.name))
        offline.cache_ttl = 1  # already expired
        offline.offline = True
        response = http.fetch("https://example.test/b", offline)
        self.assertTrue(response.from_cache)

    def test_offline_without_cache_raises(self):
        from keypulse import http

        offline = settings(cache_dir=Path(self.tmp.name))
        offline.offline = True
        with self.assertRaises(http.FetchError):
            http.fetch("https://example.test/never", offline)

    def test_block_pages_are_not_cached(self):
        from keypulse import http

        original = http._request
        http._request = lambda *args: "<html>请输入验证码</html>"
        try:
            http.fetch("https://example.test/blocked", self.settings)
        finally:
            http._request = original
        self.assertEqual(list(Path(self.tmp.name).glob("*.json")), [])


class CliSurfaceTests(unittest.TestCase):
    """Every documented flag must actually parse."""

    def test_format_flag_on_all_subcommands(self):
        from keypulse.cli import build_parser

        parser = build_parser()
        for sub in ("seeds", "providers"):
            args = parser.parse_args([sub, "--format", "md"]) if sub == "providers" else parser.parse_args(
                [sub, "-s", "test", "-f", "md"]
            )
            self.assertEqual(args.format, "md")

    def test_scan_flags(self):
        from keypulse.cli import build_parser

        args = build_parser().parse_args(
            ["scan", "-k", "a b", "c", "-r", "CN", "-f", "json", "--expand", "--include-audience"]
        )
        self.assertEqual(args.keywords, ["a b", "c"])
        self.assertEqual(args.region, "CN")
        self.assertTrue(args.expand)
        self.assertTrue(args.include_audience)

    def test_login_flags(self):
        from keypulse.cli import build_parser

        args = build_parser().parse_args(["login", "--baidu-cookie", "BDUSS=x"])
        self.assertEqual(args.baidu_cookie, "BDUSS=x")


class ProxyTests(unittest.TestCase):
    def test_explicit_proxy_is_configured(self):
        from keypulse import http

        opener = http._opener(settings(proxy="http://127.0.0.1:9"))
        proxies = next(h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler))
        self.assertEqual(proxies.proxies.get("https"), "http://127.0.0.1:9")

    def test_empty_proxy_does_not_inject_our_mapping(self):
        # Whether urllib installs a ProxyHandler at all depends on the platform
        # and on *_proxy env vars (absent on CI runners), so only assert that we
        # never inject KEYPULSE's proxy when it is unset.
        from keypulse import http

        opener = http._opener(settings(proxy=""))
        proxies = [
            h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)
        ]
        for handler in proxies:
            self.assertNotEqual(handler.proxies.get("https"), "http://127.0.0.1:9")

    def test_unreachable_proxy_is_a_fetch_error(self):
        from keypulse import http

        dead = settings(cache_dir=Path("/tmp/kp-proxy-test"), proxy="http://127.0.0.1:1")
        dead.cache_ttl = 0
        dead.retries = 0
        with self.assertRaises(http.FetchError):
            http.fetch("https://example.test/x", dead)


class BrowserBridgeTests(unittest.TestCase):
    """The browser transport must degrade cleanly when opencli is absent."""

    def test_opencli_availability_is_a_tuple(self):
        from keypulse import opencli

        ok, reason = opencli.available()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(reason, str)

    def test_eval_js_without_binary_raises_opencli_error(self):
        from keypulse import opencli

        original = opencli.shutil.which
        opencli.shutil.which = lambda _name: None
        try:
            with self.assertRaises(opencli.OpenCliError):
                opencli.eval_js(settings(), "1+1")
        finally:
            opencli.shutil.which = original

    def test_eval_js_parses_envelope(self):
        from types import SimpleNamespace

        from keypulse import opencli

        original_which, original_run = opencli.shutil.which, opencli.subprocess.run
        opencli.shutil.which = lambda _name: "/usr/bin/opencli"
        opencli.subprocess.run = lambda *a, **k: SimpleNamespace(
            returncode=0, stdout='{"ok": true, "data": "{\\"a\\": 1}"}', stderr=""
        )
        try:
            self.assertEqual(opencli.eval_js(settings(), "x"), '{"a": 1}')
        finally:
            opencli.shutil.which, opencli.subprocess.run = original_which, original_run

    def test_eval_js_surfaces_bridge_errors(self):
        from types import SimpleNamespace

        from keypulse import opencli

        original_which, original_run = opencli.shutil.which, opencli.subprocess.run
        opencli.shutil.which = lambda _name: "/usr/bin/opencli"
        opencli.subprocess.run = lambda *a, **k: SimpleNamespace(
            returncode=0,
            stdout='{"ok": false, "error": {"message": "no session"}}',
            stderr="",
        )
        try:
            with self.assertRaises(opencli.OpenCliError):
                opencli.eval_js(settings(), "x")
        finally:
            opencli.shutil.which, opencli.subprocess.run = original_which, original_run

    def test_baidu_batch_script_has_placeholders(self):
        from keypulse.providers import baidu_browser

        self.assertIn("__KEYWORDS__", baidu_browser.BATCH_SCRIPT)
        self.assertIn("search-input", baidu_browser.BATCH_SCRIPT)

    def test_trends_browser_script_has_placeholders(self):
        from keypulse.providers import trends_browser

        self.assertIn("__KEYWORDS__", trends_browser.BATCH_SCRIPT)
        self.assertIn("__GEO__", trends_browser.BATCH_SCRIPT)

    def test_wechat_browser_extracts_count(self):
        from keypulse.providers import wechat_browser

        provider = wechat_browser.SogouWeChatBrowser(settings())
        original = wechat_browser.bridge.open_page, wechat_browser.bridge.eval_js
        wechat_browser.bridge.open_page = lambda *a, **k: None
        wechat_browser.bridge.eval_js = lambda *a, **k: '{"count": 3957}'
        try:
            sig = provider.signal("量子计算", "CN")
        finally:
            wechat_browser.bridge.open_page, wechat_browser.bridge.eval_js = original
        self.assertTrue(sig.ok)
        self.assertEqual(sig.value, 3957.0)
        self.assertEqual(sig.detail["transport"], "opencli")

    def test_wechat_browser_reports_block_after_retry(self):
        from keypulse.providers import wechat_browser

        provider = wechat_browser.SogouWeChatBrowser(settings())
        original = wechat_browser.bridge.open_page, wechat_browser.bridge.eval_js
        calls = []
        wechat_browser.bridge.open_page = lambda *a, **k: calls.append(1)
        wechat_browser.bridge.eval_js = lambda *a, **k: '{"error": "搜狗反爬验证"}'
        try:
            sig = provider.signal("量子计算", "CN")
        finally:
            wechat_browser.bridge.open_page, wechat_browser.bridge.eval_js = original
        self.assertFalse(sig.ok)
        self.assertEqual(len(calls), 2)  # retried once

    def test_baidu_browser_reads_batch(self):
        from keypulse.providers import baidu_browser

        provider = baidu_browser.BaiduIndexBrowser(settings())
        original = baidu_browser.bridge.open_page, baidu_browser.bridge.eval_js
        baidu_browser.bridge.open_page = lambda *a, **k: None
        baidu_browser.bridge.eval_js = lambda *a, **k: (
            '{"a": {"overall": 1728, "mobile": 886, "yoy": "-48%"},'
            ' "b": {"error": "overview table did not render within 20s"}}'
        )
        try:
            sigs = provider.signals(["a", "b"], "CN")
        finally:
            baidu_browser.bridge.open_page, baidu_browser.bridge.eval_js = original
        self.assertEqual(sigs[0].value, 1728.0)
        self.assertTrue(sigs[0].ok)
        self.assertFalse(sigs[1].ok)

    def test_browser_providers_advertise_browser_access(self):
        from keypulse.providers.baidu_browser import BaiduIndexBrowser
        from keypulse.providers.wechat_browser import SogouWeChatBrowser

        self.assertEqual(BaiduIndexBrowser(settings()).access, "browser")
        self.assertEqual(SogouWeChatBrowser(settings()).access, "browser")


class EvalFixtureTests(unittest.TestCase):
    """The eval harness depends on these invariants; a break makes evals vacuous."""

    ROOT = Path(__file__).resolve().parent.parent

    def test_every_case_has_prompt_fixture_and_recording(self):
        cases = [
            p for p in (self.ROOT / "evals").iterdir()
            if p.is_dir() and (p / "prompt.md").exists()
        ]
        self.assertGreaterEqual(len(cases), 4)
        for case in cases:
            with self.subTest(case=case.name):
                self.assertTrue((case / "fixture.sh").exists())
                self.assertTrue((case / "recording.json").exists())
                self.assertTrue(any((case / "graders").glob("*.md")))

    def test_recording_verifier_passes(self):
        import subprocess

        result = subprocess.run(
            [sys.executable, str(self.ROOT / "evals/fixtures/verify_recording.py")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fixture_materialises_a_replay_cache(self):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            shutil = __import__("shutil")
            shutil.copy(self.ROOT / "evals/scan-ranks/fixture.sh", workspace / "fixture.sh")
            shutil.copy(self.ROOT / "evals/scan-ranks/recording.json", workspace / "recording.json")
            result = subprocess.run(
                ["bash", str(workspace / "fixture.sh")],
                cwd=workspace,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            replay_files = list((workspace / ".kp-cache").glob("replay-*.json"))
            self.assertTrue(replay_files, result.stdout)
            config = json.loads((workspace / ".keypulse.json").read_text())
            self.assertTrue(config["offline"])

    def test_replay_serves_recorded_value_not_a_request(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "replay-google_trends-人工智能.json").write_text(
                json.dumps({"value": 47.3, "metric": "interest", "unit": "scaled_0_100"}),
                encoding="utf-8",
            )
            from keypulse.providers.google_trends import GoogleTrends

            provider = GoogleTrends(settings(cache_dir=cache))
            sig = provider.signal("人工智能", "CN")
            self.assertTrue(sig.ok)
            self.assertEqual(sig.value, 47.3)
            self.assertEqual(sig.detail["transport"], "replay")


class ConfigFileTests(unittest.TestCase):
    def test_missing_config_file_is_ignored(self):
        from keypulse import config

        original = config._FILE_CONFIG
        config._FILE_CONFIG = {}
        try:
            self.assertFalse(config.Settings(offline=False).offline)
        finally:
            config._FILE_CONFIG = original

    def test_env_overrides_file(self):
        import os

        from keypulse import config

        original = config._FILE_CONFIG
        config._FILE_CONFIG = {"offline": True}
        os.environ["KEYPULSE_OFFLINE"] = "0"
        try:
            self.assertFalse(config.Settings().offline)
        finally:
            os.environ.pop("KEYPULSE_OFFLINE", None)
            config._FILE_CONFIG = original

    def test_bool_parsing(self):
        from keypulse.config import _as_bool

        for truthy in (True, "1", "true", "YES", "on"):
            self.assertTrue(_as_bool(truthy))
        for falsy in (False, "0", "", "off", None):
            self.assertFalse(_as_bool(falsy))


class BrowserFailureReportingTests(unittest.TestCase):
    """Problems found by running against the real sites."""

    def test_timeout_reports_without_echoing_the_script(self):
        from types import SimpleNamespace

        from keypulse import opencli

        original_which, original_run = opencli.shutil.which, opencli.subprocess.run
        opencli.shutil.which = lambda _name: "/usr/bin/opencli"

        def boom(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=["opencli", "browser", "eval", "SECRET_JS"], timeout=60)

        opencli.subprocess.run = boom
        try:
            with self.assertRaises(opencli.OpenCliError) as ctx:
                opencli.eval_js(settings(), "SECRET_JS", timeout=5)
        finally:
            opencli.shutil.which, opencli.subprocess.run = original_which, original_run
        message = str(ctx.exception)
        self.assertIn("超时", message)
        self.assertNotIn("SECRET_JS", message)

    def test_stderr_cleaner_drops_command_echo(self):
        from keypulse.opencli import _clean_stderr

        noisy = "Command ['opencli', 'browser', 'eval', 'LONG_SCRIPT'] failed\nreal error: no session"
        self.assertEqual(_clean_stderr(noisy), "real error: no session")
        self.assertEqual(_clean_stderr(""), "")

    def test_unindexed_keyword_is_a_zero_not_an_error(self):
        from keypulse.providers import baidu_browser

        provider = baidu_browser.BaiduIndexBrowser(settings())
        original = baidu_browser.bridge.open_page, baidu_browser.bridge.eval_js
        baidu_browser.bridge.open_page = lambda *a, **k: None
        baidu_browser.bridge.eval_js = lambda *a, **k: '{"电商卖家": {"unindexed": true}}'
        try:
            sig = provider.signals(["电商卖家"], "CN")[0]
        finally:
            baidu_browser.bridge.open_page, baidu_browser.bridge.eval_js = original
        self.assertTrue(sig.ok)
        self.assertEqual(sig.value, 0.0)
        self.assertTrue(sig.detail["unindexed"])
        self.assertIn("未收录", sig.detail["note"])

    def test_batch_timeout_scales_with_keyword_count(self):
        from keypulse.providers import baidu_browser

        provider = baidu_browser.BaiduIndexBrowser(settings())
        seen = {}
        original = baidu_browser.bridge.open_page, baidu_browser.bridge.eval_js

        def record_timeout(*args, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return '{"a": {"overall": 1.0, "mobile": 1.0, "yoy": "0%"}}'

        baidu_browser.bridge.open_page = lambda *a, **k: None
        baidu_browser.bridge.eval_js = record_timeout
        try:
            provider.signals(["a", "b", "c", "d"], "CN")
        finally:
            baidu_browser.bridge.open_page, baidu_browser.bridge.eval_js = original
        self.assertGreaterEqual(seen["timeout"], 30 + 25 * 4)

    def test_unindexed_keywords_are_listed_in_the_report(self):
        from keypulse.models import Signal
        from keypulse.render import _unindexed
        from keypulse.scan import ScanRequest, ScanResult

        result = ScanResult(
            request=ScanRequest(),
            keywords=["a", "b"],
            scored=[],
            signals=[
                Signal(keyword="a", provider="baidu_index", metric="index", value=0.0,
                       detail={"unindexed": True}),
                Signal(keyword="b", provider="baidu_index", metric="index", value=5.0),
                Signal.failure("c", "baidu_index", "index", "timeout"),
            ],
        )
        self.assertEqual(_unindexed(result), ["a"])


class DoctorTests(unittest.TestCase):
    def test_healthy_install_passes(self):
        from keypulse.doctor import run_checks

        checks = run_checks(settings(cache_dir=Path("/tmp/keypulse-doctor-ok")))
        labels = {c["label"]: c for c in checks}
        self.assertTrue(labels["package"]["ok"])
        self.assertTrue(labels["providers"]["ok"])
        self.assertTrue(labels["cache_dir"]["ok"])
        self.assertNotIn("traceback", " ".join(c["detail"] for c in checks).lower())

    def test_unwritable_cache_fails_without_crashing(self):
        from keypulse.doctor import run_checks

        checks = run_checks(settings(cache_dir=Path("/proc/nope/keypulse")))
        cache = next(c for c in checks if c["label"] == "cache_dir")
        self.assertFalse(cache["ok"])
        self.assertIn("/proc/nope/keypulse", cache["detail"])

    def test_optional_transports_never_fail(self):
        from keypulse.doctor import run_checks

        checks = run_checks(settings(cache_dir=Path("/tmp/keypulse-doctor-opt")))
        for label in ("baidu_cookie", "browser_bridge"):
            check = next(c for c in checks if c["label"] == label)
            self.assertTrue(check["ok"], f"{label} must be informational only")

    def test_doctor_cli_exit_codes(self):
        from types import SimpleNamespace

        from keypulse.doctor import cmd_doctor

        ok_args = SimpleNamespace(format="json", no_cache=False, offline=False)
        self.assertEqual(cmd_doctor(ok_args), 0)
