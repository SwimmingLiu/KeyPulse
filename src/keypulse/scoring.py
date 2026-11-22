"""Turn heterogeneous provider numbers into one comparable weight.

The problem: 微信指数 is a 7-figure integer, Google Trends is a 0-100 relative
series, article/hit counts span five orders of magnitude, and autocomplete
answers with the same number of phrases no matter how popular the term is.
You cannot average those raw values.

The approach:

1. **Per-provider normalisation.** Each provider's raw value maps to 0-100
   through a transform chosen for what that number *means*:
   - bounded indexes (Google Trends 0-100) → identity;
   - corpus counts (WeChat articles, Wikipedia hits, HN stories, SERP hits)
     → log scale against a provider-specific ceiling;
   - autocomplete → how much of the suggestion set is about this term, not the
     position of the term in it.
2. **Weighted blend.** Normalised values are averaged with per-provider
   weights.  Providers that failed drop out of both numerator and
   denominator, so an unavailable source lowers *confidence*, not the score.
3. **Confidence.** Share of the requested provider weight that actually
   answered, reported alongside the score so a thin result is visible.
4. **Opportunity.** Demand discounted by competition (SERP/article volume),
   which is what actually identifies keywords worth targeting.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .models import Signal
from .providers import COMPETITION_PROVIDERS

#: Weights for the demand score.  Weight is a *reliability* judgement about the
#: metric, not its magnitude: direct search indexes dominate, corpus counts
#: corroborate.
#:
#: Autocomplete providers (``*_suggest``) are deliberately absent.  They are
#: discovery sources — every engine returns ~10 phrases whether the term is a
#: national obsession or a typo, so their "affinity" is near-binary and would
#: flatten the ranking.  They still feed :mod:`keypulse.expand`.
DEFAULT_WEIGHTS: Mapping[str, float] = {
    "google_trends": 3.0,
    "baidu_index": 3.0,
    "douyin_index": 2.5,
    "wechat_index": 2.5,
    "wechat_desktop_index": 2.5,
}

#: Used only when no index provider answered.  These are proxies for demand —
#: article/pageview volume tracks attention, but it is not an index and must
#: never be presented as one.
FALLBACK_WEIGHTS: Mapping[str, float] = {
    "sogou_wechat": 2.0,
    "wikipedia_pageviews": 1.6,
    "wikipedia_search": 1.4,
    "xiaohongshu_notes": 1.5,
}

#: Developer-audience sources.  Real signal, but skewed towards technical
#: topics: 猫粮 and 量子计算 are both demand-heavy while only one has GitHub
#: repos.  Reported separately; blended only with ``--include-audience``.
AUDIENCE_WEIGHTS: Mapping[str, float] = {
    "github_repos": 1.0,
    "hn_mentions": 1.0,
}

#: Sources recorded as evidence but never blended into the score.
DISCOVERY_ONLY = {
    "baidu_suggest",
    "google_suggest",
    "bing_suggest",
    "so360_suggest",
    "sogou_suggest",
}


def mode_for(weights: Mapping[str, float]) -> str:
    """Label the score's provenance so the report can be honest about it."""
    if any(name in weights for name in (
            "baidu_index",
            "google_trends",
            "douyin_index",
            "wechat_index",
            "wechat_desktop_index",
        )):
        return "index"
    if any(name in weights for name in FALLBACK_WEIGHTS):
        return "fallback"
    return "blended"

#: Log-scale ceilings: a value at or above the ceiling normalises to ~100.
#: Calibrated against real lookups, not guessed: 人工智能 has ~4.8k exact-phrase
#: Wikipedia articles and 猫粮 ~19, so the ceiling sits where "established topic"
#: begins rather than where "enormous topic" begins.
LOG_CEILINGS: Mapping[str, float] = {
    "wikipedia_pageviews": 300_000,     # monthly views of one article
    "wikipedia_search": 5_000,          # exact-phrase article matches
    "sogou_wechat": 30_000,             # indexed WeChat articles
    "xiaohongshu_notes": 3_000,         # mean likes on the first page of RED notes
    # 抖音指数 is an absolute 30-day average in the millions: 露营 68.4万,
    # 人工智能 8.4万, a niche term in the low thousands. Ceiling at 200万 keeps
    # the top of the real range off 100 so the scale still discriminates.
    "douyin_index": 2_000_000,
    # 微信指数 runs to the tens of millions for national topics and a few
    # thousand for niche ones; 3000万 keeps mainstream keywords off the 100
    # ceiling so the scale still separates them.
    "wechat_index": 30_000_000,
    "wechat_desktop_index": 30_000_000,
    "hn_mentions": 10_000,              # matching HN stories
    "github_repos": 100_000,            # matching repos
    "bing_web": 20_000_000,             # SERP result count
    "so360_web": 5_000_000,             # SERP result count
}


def normalise(signal: Signal) -> float | None:
    """Map one provider's raw value onto 0-100. ``None`` when unusable."""
    if not signal.ok:
        return None
    if signal.metric == "suggest_affinity":
        # Provider already emits a percentage of on-topic suggestions.
        return max(0.0, min(100.0, signal.value))
    if signal.unit == "scaled_0_100":
        return max(0.0, min(100.0, signal.value))
    value = signal.value
    if value <= 0:
        return 0.0
    ceiling = LOG_CEILINGS.get(signal.provider)
    if ceiling is None:
        ceiling = 1_000_000  # conservative default for an unknown integer index
    return max(0.0, min(100.0, 100.0 * math.log1p(value) / math.log1p(ceiling)))


@dataclass
class ScoredKeyword:
    keyword: str
    score: float                                  # weighted 0-100 demand score
    confidence: float                             # 0-1, share of requested weight answered
    competition: float | None = None              # 0-100, higher = harder to rank
    components: dict[str, float] = field(default_factory=dict)  # provider -> normalised
    raw: dict[str, float] = field(default_factory=dict)         # provider -> raw value
    errors: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    @property
    def opportunity(self) -> float:
        """Demand discounted by competition.

        A keyword with the same demand but twice the competing content is a
        worse target; with no competition data we cannot discount, so the
        demand score stands unchanged.
        """
        if self.competition is None:
            return self.score
        return max(0.0, self.score * (1.0 - 0.45 * (self.competition / 100.0)))

    def to_dict(self) -> dict:
        payload = {
            "keyword": self.keyword,
            "score": round(self.score, 1),
            "confidence": round(self.confidence, 2),
            "opportunity": round(self.opportunity, 1),
            "components": {k: round(v, 1) for k, v in sorted(self.components.items())},
            "raw": {k: v for k, v in sorted(self.raw.items())},
            "sources": sorted(self.sources),
        }
        if self.competition is not None:
            payload["competition"] = round(self.competition, 1)
        if self.errors:
            payload["errors"] = self.errors
        return payload

    @property
    def demand_evidence(self) -> dict[str, float]:
        """Components that actually drove the score (excludes discovery/competition)."""
        return {k: v for k, v in self.components.items() if k not in DISCOVERY_ONLY}


def score_keywords(
    signals: Iterable[Signal],
    weights: Mapping[str, float] | None = None,
    expected_providers: Iterable[str] | None = None,
    include_audience: bool = False,
) -> list[ScoredKeyword]:
    """Aggregate per-provider signals into ranked :class:`ScoredKeyword` records.

    ``expected_providers`` is the set the scan asked for; confidence is measured
    against it, so a source that fails to answer lowers confidence instead of
    silently leaving the denominator.  ``include_audience`` folds the
    developer-audience sources into the blend for technical keyword sets.
    """
    weights = dict(weights or DEFAULT_WEIGHTS)
    if include_audience:
        weights.update(AUDIENCE_WEIGHTS)
    grouped: dict[str, list[Signal]] = {}
    for signal in signals:
        grouped.setdefault(signal.keyword, []).append(signal)

    if expected_providers is None:
        expected = sum(w for name, w in weights.items() if not _is_competition(name))
    else:
        expected = sum(
            weights.get(name, 0.5)
            for name in expected_providers
            if name in weights and not _is_competition(name) and name not in DISCOVERY_ONLY
        )

    scored: list[ScoredKeyword] = []
    for keyword, items in grouped.items():
        total_weight = 0.0
        weighted_sum = 0.0
        competition_values: list[float] = []
        record = ScoredKeyword(keyword=keyword, score=0.0, confidence=0.0)

        for signal in items:
            normalised = normalise(signal)
            record.sources.append(signal.provider)
            if normalised is None:
                record.errors[signal.provider] = signal.error or "unavailable"
                continue
            record.raw[signal.provider] = signal.value

            if signal.provider not in weights and signal.provider not in COMPETITION_PROVIDERS:
                # Collected by the scan but not part of this scoring mode
                # (e.g. developer sources without --include-audience).
                continue

            weight = weights.get(signal.provider, 0.5)
            if signal.provider in COMPETITION_PROVIDERS:
                # Competition never drives demand, so it is kept out of the blend
                # even though it carries no weight.
                competition_values.append(normalised)
                continue
            record.components[signal.provider] = normalised
            if signal.provider in DISCOVERY_ONLY:
                # Discovery sources prove the term exists, not how big it is.
                continue
            total_weight += weight
            weighted_sum += weight * normalised

        record.score = weighted_sum / total_weight if total_weight else 0.0
        record.confidence = min(1.0, total_weight / expected) if expected else 0.0
        if competition_values:
            record.competition = sum(competition_values) / len(competition_values)
        scored.append(record)

    _apply_rank_fallback(scored)
    # Rank by demand; break ties by confidence, then by keyword so the order is
    # stable across runs (a single-source scan can produce exact ties).
    scored.sort(key=lambda k: (-k.score, -k.confidence, k.keyword))
    return scored


def _apply_rank_fallback(scored: list[ScoredKeyword]) -> None:
    """Re-score by rank when the evidence is too thin to be comparable.

    Two cases lose information under absolute normalisation:

    * **One source** — a log curve squeezes every value into a narrow band, and
      a bare autocomplete signal collapses to ties.
    * **One keyword** — with nothing to compare against, the absolute number is
      the only thing there is, and it is meaningful.

    So when several keywords share the same single evidence source, rank them
    against each other instead.
    """
    if len(scored) < 2:
        return
    source_sets = {frozenset(record.components) for record in scored}
    if len(source_sets) != 1:
        return
    only = next(iter(source_sets))
    if len(only) != 1 or not only:
        return
    source = next(iter(only))
    raw_values = [record.raw.get(source) for record in scored]
    if any(value is None for value in raw_values):
        return
    ordered = sorted(raw_values, reverse=True)
    span = ordered[0] - ordered[-1]
    if span <= 0:
        return
    for record in scored:
        value = record.raw.get(source, 0.0)
        record.score = round(100.0 * (value - ordered[-1]) / span, 1)


def _is_competition(name: str) -> bool:
    return name in COMPETITION_PROVIDERS


def rank_summary(scored: list[ScoredKeyword], top: int = 10) -> dict:
    """Headline numbers for the report: winner plus distribution context."""
    if not scored:
        return {"total": 0, "top": None, "median_score": 0.0}
    scores = sorted((k.score for k in scored), reverse=True)
    mid = scores[len(scores) // 2]
    best = scored[0]
    return {
        "total": len(scored),
        "top": best.to_dict(),
        "median_score": round(mid, 1),
        "leader_gap": round(best.score - mid, 1),
        "top_n": [k.to_dict() for k in scored[:top]],
    }
