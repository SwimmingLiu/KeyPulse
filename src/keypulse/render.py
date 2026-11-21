"""Rendering helpers: terminal table, JSON, and Markdown.

Kept separate from the CLI so the skill can ask for Markdown (which reads well
in a chat reply) while humans get the aligned table.
"""

from __future__ import annotations

import json
from typing import Iterable

from .scan import ScanResult
from .scoring import ScoredKeyword


MODE_LABELS = {
    "index": "搜索引擎指数（百度指数 / Google Trends）",
    "fallback": "指数不可用，改用内容量代理指标",
    "explicit": "按指定数据源",
    "blended": "多来源混合",
}

MODE_CAVEATS = {
    "fallback": "指数来源不可用：排名基于内容量代理指标，不等于搜索指数。",
    "explicit": "按你指定的数据源计算，未启用自动分层。",
}


def _fmt_int(value: float) -> str:
    if value >= 10_000:
        return f"{value/1000:.0f}k" if value >= 100_000 else f"{value:,.0f}"
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


def render_table(result: ScanResult, top: int | None = None) -> str:
    top = top or result.request.top
    rows = result.scored[:top]
    if not rows:
        return "没有可展示的关键词。"

    score_header = "指数分" if result.mode != "fallback" else "热度分"
    header = ("#", "关键词", score_header, "置信度", "机会分", "主要来源")
    body: list[tuple[str, ...]] = []
    for index, record in enumerate(rows, start=1):
        body.append(
            (
                str(index),
                _truncate(record.keyword, 26),
                f"{record.score:.1f}",
                f"{record.confidence:.2f}",
                f"{record.opportunity:.1f}",
                _truncate(_top_sources(record), 38),
            )
        )

    widths = [max(len(str(cell)) for cell in column) for column in zip(header, *body)]
    widths = [max(w, 4) for w in widths]

    def line(cells: Iterable[str]) -> str:
        return "  ".join(str(cell).ljust(width) for cell, width in zip(cells, widths)).rstrip()

    divider = "  ".join("─" * width for width in widths)
    output = [line(header), divider]
    output.extend(line(row) for row in body)

    output.append("")
    output.append(f"数据来源: {MODE_LABELS.get(result.mode, result.mode)}")
    if result.mode in MODE_CAVEATS:
        output.append(f"注意: {MODE_CAVEATS[result.mode]}")
    unavailable = {
        name: note
        for name, note in result.provider_status.items()
        if note != "ok" and not note.startswith("ok")
    }
    if unavailable:
        output.append("未启用来源:")
        for name, note in sorted(unavailable.items()):
            output.append(f"  - {name}: {note}")
    unindexed = _unindexed(result)
    if unindexed:
        output.append(f"未收录: {len(unindexed)} 个关键词在百度指数中未收录"
                      "（按 0 计入分数，非查询失败）")

    failures = result.signal_failures
    if failures:
        output.append("查询失败来源:")
        for name, note in sorted(failures.items()):
            output.append(f"  - {name}: {note}")

    low_confidence = [record for record in rows if record.confidence < 0.5]
    if low_confidence:
        output.append(
            f"提示: {len(low_confidence)} 个关键词的可用来源不足一半（置信度 < 0.5），"
            "排名仅供参考。"
        )

    output.append("")
    output.append(
        f"共扫描 {len(result.scored)} 个关键词，用时 {result.duration_s:.1f}s。"
        f"最高分: {rows[0].keyword} ({rows[0].score:.1f})"
    )
    return "\n".join(output)


def render_json(result: ScanResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


def render_markdown(result: ScanResult, top: int | None = None) -> str:
    top = top or result.request.top
    rows = result.scored[:top]
    summary = result.summary
    score_header = "指数分" if result.mode != "fallback" else "热度分"
    lines = [
        f"## KeyPulse 关键词指数报告 · {result.request.region.upper()}",
        "",
        f"- 扫描关键词: **{len(result.scored)}**",
        f"- 用时: {result.duration_s:.1f}s",
        f"- 数据来源: {MODE_LABELS.get(result.mode, result.mode)}",
    ]
    if result.mode in MODE_CAVEATS:
        lines.append(f"- ⚠️ {MODE_CAVEATS[result.mode]}")
    if summary.get("top"):
        lines.append(
            f"- 最高权重关键词: **{summary['top']['keyword']}** "
            f"({score_header} {summary['top']['score']}, 置信度 {summary['top']['confidence']})"
        )
    lines.append("")
    lines.append(f"| # | 关键词 | {score_header} | 置信度 | 机会分 | 竞争度 | 主要来源 |")
    lines.append("|---|---|---|---|---|---|---|")
    for index, record in enumerate(rows, start=1):
        competition = "—" if record.competition is None else f"{record.competition:.0f}"
        lines.append(
            f"| {index} | {record.keyword} | {record.score:.1f} | {record.confidence:.2f} | "
            f"{record.opportunity:.1f} | {competition} | {_top_sources(record)} |"
        )

    unavailable = {
        name: note
        for name, note in result.provider_status.items()
        if note != "ok" and not note.startswith("ok")
    }
    if unavailable:
        lines.append("")
        lines.append("**未启用来源**")
        for name, note in sorted(unavailable.items()):
            lines.append(f"- `{name}`: {note}")
    unindexed = _unindexed(result)
    if unindexed:
        lines.append("")
        lines.append(
            f"**未收录**: {'、'.join(unindexed)} 在百度指数中未收录（按 0 计入，非查询失败）"
        )
    failures = result.signal_failures
    if failures:
        lines.append("")
        lines.append("**查询失败来源**")
        for name, note in sorted(failures.items()):
            lines.append(f"- `{name}`: {note}")

    raw_sources = _raw_table(rows)
    if raw_sources:
        lines.append("")
        lines.append("<details><summary>各来源原始数值</summary>")
        lines.append("")
        lines.append(raw_sources)
        lines.append("")
        lines.append("</details>")
    return "\n".join(lines)


def _raw_table(rows: list[ScoredKeyword]) -> str:
    providers = sorted({name for record in rows for name in record.raw})
    if not providers:
        return ""
    header = ["关键词", *providers]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for record in rows:
        cells = [record.keyword]
        for provider in providers:
            value = record.raw.get(provider)
            cells.append("—" if value is None else _fmt_int(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render_leaders(scored: list[ScoredKeyword], top: int) -> str:
    """Compact list used by ``keypulse top``."""
    if not scored:
        return "没有数据：所有来源都不可用。"
    lines = []
    for index, record in enumerate(scored[:top], start=1):
        components = ", ".join(
            f"{name} {value:.0f}" for name, value in sorted(record.demand_evidence.items())
        )
        lines.append(f"{index:>2}. {record.keyword}")
        lines.append(
            f"    指数分 {record.score:.1f} · 置信度 {record.confidence:.2f} · 机会分 {record.opportunity:.1f}"
            + (f" · 竞争度 {record.competition:.0f}" if record.competition is not None else "")
        )
        if components:
            lines.append(f"    {components}")
    return "\n".join(lines)


def _unindexed(result) -> list[str]:
    """Keywords a provider explicitly reported as not indexed."""
    return sorted(
        {
            signal.keyword
            for signal in result.signals
            if signal.ok and signal.detail.get("unindexed")
        }
    )


def _top_sources(record: ScoredKeyword) -> str:
    # Prefer sources that actually drove the score over discovery-only evidence.
    ranked = sorted(record.demand_evidence.items(), key=lambda item: item[1], reverse=True)
    return ", ".join(f"{name}:{value:.0f}" for name, value in ranked[:3]) or "—"


def _truncate(text: str, width: int) -> str:
    text = text.replace("\n", " ")
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"
