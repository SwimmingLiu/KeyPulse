"""Scan orchestration: keywords x providers, concurrently, with partial failure.

The scan is the unit of work behind ``keypulse scan`` and ``keypulse top``.
It fans queries out over a thread pool, then hands every ``Signal`` to the
scoring layer.  A provider that is unavailable (no cookie, blocked, rate
limited) is recorded once in ``provider_status`` so the report can explain
missing data instead of silently under-scoring a keyword.
"""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .config import Settings
from .expand import add_modifiers, expand, suggest_seeds
from .http import reset_runtime_state
from .models import Signal, Suggestion
from .providers import (
    AUDIENCE_PROVIDERS,
    COMPETITION_PROVIDERS,
    FALLBACK_PROVIDERS,
    INDEX_PROVIDERS,
    build_registry,
)
from .providers.base import Provider, normalise_region
from .scoring import (
    AUDIENCE_WEIGHTS,
    DEFAULT_WEIGHTS,
    FALLBACK_WEIGHTS,
    ScoredKeyword,
    rank_summary,
    score_keywords,
)


@dataclass
class ScanRequest:
    keywords: list[str] = field(default_factory=list)
    scenario: str = ""
    region: str = "GLOBAL"
    providers: list[str] | None = None
    expand_seeds: bool = False
    depth: int = 1
    max_keywords: int = 25
    top: int = 10
    modifiers: bool = False
    #: Fold developer-audience sources (GitHub, HN) into the score.  Off by
    #: default because those sources skew every ranking towards tech keywords.
    include_audience: bool = False


@dataclass
class ScanResult:
    request: ScanRequest
    keywords: list[str]
    scored: list[ScoredKeyword]
    signals: list[Signal] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    provider_status: dict[str, str] = field(default_factory=dict)
    started_at: str = ""
    duration_s: float = 0.0
    #: Where the numbers came from: ``index`` (Baidu/Google index answered),
    #: ``fallback`` (corpus proxies only), ``explicit`` (user chose providers)
    #: or ``blended``.
    mode: str = "index"

    @property
    def summary(self) -> dict:
        return rank_summary(self.scored, top=self.request.top)

    @property
    def signal_failures(self) -> dict[str, str]:
        """Providers that passed their health check but failed on real queries.

        Distinct from :attr:`provider_status`: a source can be configured and
        reachable, then still get blocked per-query (Sogou rate-limits by IP).
        Reporting both keeps the "why is this number thin" answer honest.
        """
        failures: dict[str, str] = {}
        for signal in self.signals:
            if signal.ok:
                failures.pop(signal.provider, None)
            elif signal.provider not in failures:
                failures[signal.provider] = signal.error or "failed"
        return failures

    def to_dict(self) -> dict:
        return {
            "generated_at": self.started_at,
            "duration_s": round(self.duration_s, 2),
            "region": normalise_region(self.request.region),
            "scenario": self.request.scenario or None,
            "mode": self.mode,
            "keyword_count": len(self.keywords),
            "providers": self.provider_status,
            "provider_failures": self.signal_failures,
            "summary": self.summary,
            "ranked": [k.to_dict() for k in self.scored],
            "signals": [s.to_dict() for s in self.signals],
        }


def _check_providers(
    providers: list[Provider], settings: Settings, status: dict[str, str]
) -> list[Provider]:
    """Run health checks concurrently and record each verdict in ``status``."""
    usable: list[Provider] = []
    if not providers:
        return usable
    with ThreadPoolExecutor(max_workers=max(1, min(settings.workers, 16))) as pool:
        checks = {pool.submit(provider.check): provider for provider in providers}
        for future in as_completed(checks):
            provider = checks[future]
            try:
                ok, reason = future.result()
            except Exception as exc:  # noqa: BLE001 - a check must never break the scan
                ok, reason = False, f"health check failed: {type(exc).__name__}: {exc}"
            status[provider.name] = reason
            if ok:
                usable.append(provider)
    return usable


def _weights_for(mode: str, include_audience: bool, providers: list[str] | None = None):
    """Weights for the tier that answered.

    An explicit ``--provider`` list means "score what I asked for", so every
    selected provider gets a weight even if it is normally discovery-only or
    audience-only.
    """
    if mode == "explicit":
        known = {**DEFAULT_WEIGHTS, **FALLBACK_WEIGHTS, **AUDIENCE_WEIGHTS}
        selected = providers or []
        return {name: known.get(name, 1.0) for name in selected if name not in COMPETITION_PROVIDERS}
    weights = dict(FALLBACK_WEIGHTS) if mode == "fallback" else dict(DEFAULT_WEIGHTS)
    if include_audience:
        weights.update(AUDIENCE_WEIGHTS)
    return weights


def resolve_keywords(request: ScanRequest, settings: Settings) -> tuple[list[str], list[Suggestion]]:
    """Turn the request (explicit keywords and/or scenario) into a keyword list."""
    registry = build_registry(settings)
    seeds: list[str] = [k.strip() for k in request.keywords if k and k.strip()]

    if request.scenario:
        seeds.extend(suggest_seeds(request.scenario, limit=8))

    if request.modifiers and seeds:
        seeds = add_modifiers(sorted(set(seeds)), limit=request.max_keywords)

    suggestions: list[Suggestion] = []
    if request.expand_seeds and seeds:
        expanded, suggestions = expand(
            seeds,
            registry,
            settings,
            providers=request.providers,
            region=request.region,
            per_seed=10,
            depth=request.depth,
        )
        seeds = expanded

    deduped: list[str] = []
    seen: set[str] = set()
    for seed in seeds:
        key = seed.lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(seed)
    limit = max(1, request.max_keywords)
    return deduped[:limit], suggestions


def run_scan(request: ScanRequest, settings: Settings | None = None) -> ScanResult:
    settings = settings or Settings()
    reset_runtime_state()
    registry = build_registry(settings)
    keywords, suggestions = resolve_keywords(request, settings)

    # A scan is index-first: when Baidu Index or Google Trends answers, the
    # score is built from indexes alone and the corpus proxies are skipped.
    # Only when every index is unreachable do we fall back to proxies, and the
    # report says so.  An explicit --provider list bypasses the tiering.
    explicit = bool(request.providers) and "auto" not in {
        p.strip().lower() for p in request.providers or []
    }
    if explicit:
        tiers: list[tuple[str, list[Provider]]] = [
            ("explicit", registry.select(request.providers, request.region))
        ]
    else:
        tiers = [
            ("index", [p for p in registry if p.name in INDEX_PROVIDERS]),
            ("fallback", [p for p in registry if p.name in FALLBACK_PROVIDERS]),
        ]
    augments = [
        p for p in registry
        if p.name not in INDEX_PROVIDERS
        and p.name not in FALLBACK_PROVIDERS
        and p.name not in AUDIENCE_PROVIDERS
    ]
    if request.include_audience:
        augments.extend(p for p in registry if p.name in AUDIENCE_PROVIDERS)

    provider_status: dict[str, str] = {}
    started = dt.datetime.now()

    def attempt(tier_providers: list[Provider]) -> list[Signal]:
        return _collect_signals(tier_providers, keywords, request.region, settings)

    # Walk the tiers until one produces usable evidence.  A source can pass its
    # health check and still fail every real query (Sogou throttles per query),
    # so "available" is judged on results, not on the check alone.
    mode = tiers[0][0]
    usable: list[Provider] = []
    signals: list[Signal] = []
    scored: list[ScoredKeyword] = []
    for label, tier_providers in tiers:
        tier_usable = _check_providers(tier_providers, settings, provider_status)
        if not tier_usable:
            mode = label
            continue
        tier_signals = attempt(tier_usable)
        tier_weights = _weights_for(
            label, request.include_audience, [p.name for p in tier_usable]
        )
        tier_scored = score_keywords(
            tier_signals, tier_weights, expected_providers=[p.name for p in tier_usable]
        )
        usable, signals, scored, mode = tier_usable, tier_signals, tier_scored, label
        if any(record.components for record in tier_scored):
            break
        if label == tiers[-1][0]:
            break
        # Nothing usable from this tier: fall through to the next one.

    pool_targets = [p for p in augments if p.name not in provider_status]
    usable.extend(_check_providers(pool_targets, settings, provider_status))
    order = {name: index for index, name in enumerate(registry.names())}
    usable.sort(key=lambda p: order[p.name])

    # Keywords with no usable data still deserve a row so the report shows them.
    covered = {record.keyword.lower() for record in scored}
    for keyword in keywords:
        if keyword.lower() not in covered:
            scored.append(ScoredKeyword(keyword=keyword, score=0.0, confidence=0.0))
    scored.sort(key=lambda k: (k.score, k.confidence), reverse=True)

    duration = (dt.datetime.now() - started).total_seconds()
    return ScanResult(
        request=request,
        keywords=keywords,
        scored=scored,
        signals=signals,
        suggestions=suggestions,
        provider_status=provider_status,
        started_at=started.astimezone().isoformat(timespec="seconds"),
        duration_s=duration,
        mode=mode,
    )


def _collect_signals(
    providers: list[Provider], keywords: list[str], region: str, settings: Settings
) -> list[Signal]:
    """Query every (provider, keyword) pair concurrently.

    Browser-backed providers share one browser tab per provider, so their
    keywords are serialised inside the provider while different providers run
    in parallel — otherwise N browser navigations would queue behind each other.
    """
    signals: list[Signal] = []
    if not keywords or not providers:
        return signals
    workers = max(1, min(settings.workers, 16))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        if any(_is_browser_provider(p) for p in providers):
            # One task per provider: inside it, keywords run in order.
            futures = [
                pool.submit(_run_provider_batch, provider, keywords, region)
                for provider in providers
            ]
            for future in as_completed(futures):
                signals.extend(future.result())
            return signals
        futures = [
            pool.submit(_safe_signal, provider, keyword, region)
            for provider in providers
            for keyword in keywords
        ]
        for future in as_completed(futures):
            signals.append(future.result())
    return signals


def _is_browser_provider(provider: Provider) -> bool:
    return getattr(provider, "access", "") == "browser"


def _run_provider_batch(provider: Provider, keywords: list[str], region: str) -> list[Signal]:
    try:
        return provider.signals(keywords, region)
    except Exception as exc:  # noqa: BLE001 - a provider must never break a scan
        return [
            Signal.failure(
                keyword, provider.name, provider.metric,
                f"{type(exc).__name__}: {exc}", region=region,
            )
            for keyword in keywords
        ]



def _safe_signal(provider: Provider, keyword: str, region: str) -> Signal:
    try:
        return provider.signal(keyword, region)
    except Exception as exc:  # noqa: BLE001 - a provider must never break a scan
        return Signal.failure(keyword, provider.name, provider.metric, f"{type(exc).__name__}: {exc}", region=region)


def top_keyword(result: ScanResult) -> ScoredKeyword | None:
    return result.scored[0] if result.scored else None


def describe_provider_health(settings: Settings | None = None, region: str = "GLOBAL") -> list[dict]:
    """Health table for ``keypulse providers``."""
    settings = settings or Settings()
    registry = build_registry(settings)
    rows: list[dict] = []
    for provider in registry.select(["all"]):
        ok, reason = provider.check()
        rows.append(
            {
                "name": provider.name,
                "label": provider.label,
                "regions": list(provider.regions),
                "kind": provider.kind,
                "supports_region": provider.supports(region),
                "available": ok,
                "note": reason,
            }
        )
    return rows


def keyword_report_lines(scored: Sequence[ScoredKeyword], limit: int = 10) -> Iterable[str]:
    """Plain-text rows used by the CLI table renderer."""
    for index, record in enumerate(scored[:limit], start=1):
        comps = ", ".join(f"{k}={v:.0f}" for k, v in sorted(record.components.items()))
        yield (
            f"{index:>2}. {record.keyword}  score={record.score:.1f} "
            f"conf={record.confidence:.2f} opp={record.opportunity:.1f} [{comps}]"
        )
