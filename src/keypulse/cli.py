"""``keypulse`` command line interface.

    keypulse scan   --keywords "ai note, ai notes" --region CN --format md
    keypulse scan   --scenario "面向程序员的 AI 笔记软件" --expand --top 10
    keypulse top    --keywords "ai" "llm" --format json
    keypulse expand --keywords "ai note" --limit 40
    keypulse providers

Every command accepts ``--format {table,json,md}``.  Exit code is 0 on a scan
that produced data, 2 when nothing could be measured, so scripts can branch.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import __version__
from .config import Settings
from .expand import add_modifiers, detect_language, expand, suggest_seeds
from .login import cmd_login
from .providers import build_registry
from .render import render_json, render_leaders, render_markdown, render_table
from .scan import ScanRequest, describe_provider_health, run_scan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="keypulse",
        description="KeyPulse — keyword demand research across search engines.",
    )
    parser.add_argument("--version", action="version", version=f"keypulse {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="query keyword indexes and rank by weight")
    _add_common(scan)
    scan.add_argument("--keywords", "-k", nargs="+", default=[], help="explicit keywords")
    scan.add_argument("--scenario", "-s", default="", help="describe the scenario; seeds are derived")
    scan.add_argument("--expand", action="store_true", help="grow seeds with autocomplete suggestions")
    scan.add_argument("--modifiers", action="store_true", help="add intent modifiers (怎么样/教程/...) to seeds")
    scan.add_argument("--depth", type=int, default=1, help="expansion rounds (default 1)")
    scan.add_argument("--max-keywords", type=int, default=25, help="cap on keywords to measure (default 25)")
    scan.add_argument("--top", "-n", type=int, default=10, help="rows to show (default 10)")
    scan.add_argument(
        "--include-audience", action="store_true",
        help="also weigh developer sources (GitHub/HN); suits technical keyword sets",
    )
    scan.set_defaults(func=cmd_scan)

    top = sub.add_parser("top", help="return only the highest-weight keywords")
    _add_common(top)
    top.add_argument("--keywords", "-k", nargs="+", default=[])
    top.add_argument("--scenario", "-s", default="")
    top.add_argument("--expand", action="store_true")
    top.add_argument("--top", "-n", type=int, default=5)
    top.add_argument("--max-keywords", type=int, default=25)
    top.add_argument("--include-audience", action="store_true")
    top.set_defaults(func=cmd_top)

    expand_cmd = sub.add_parser("expand", help="list keyword suggestions without measuring them")
    _add_common(expand_cmd)
    expand_cmd.add_argument("--keywords", "-k", nargs="+", default=[])
    expand_cmd.add_argument("--scenario", "-s", default="")
    expand_cmd.add_argument("--limit", type=int, default=40)
    expand_cmd.set_defaults(func=cmd_expand)

    seeds = sub.add_parser("seeds", help="derive seed keywords from a scenario description")
    seeds.add_argument("--scenario", "-s", required=True)
    seeds.add_argument("--modifiers", action="store_true")
    seeds.add_argument("--limit", type=int, default=10)
    seeds.add_argument("--format", "-f", choices=["table", "json", "md"], default="table")
    seeds.set_defaults(func=cmd_seeds)

    login = sub.add_parser("login", help="store a Baidu Index cookie")
    login.add_argument("--baidu-cookie", default="", help="the cookie string copied from index.baidu.com")
    login.add_argument("--clear", action="store_true", help="forget the stored cookie")
    login.set_defaults(func=cmd_login)

    providers = sub.add_parser("providers", help="list providers and their availability")
    providers.add_argument("--region", "-r", default="GLOBAL")
    providers.add_argument("--format", "-f", choices=["table", "json", "md"], default="table")
    providers.set_defaults(func=cmd_providers)

    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--region", "-r", default="GLOBAL", help="CN, US, GLOBAL, ... (default GLOBAL)")
    parser.add_argument(
        "--provider", "-p", action="append", default=None,
        help="provider name, repeatable (default: auto by region; 'all' for every provider)",
    )
    parser.add_argument("--format", "-f", choices=["table", "json", "md"], default="table")
    parser.add_argument("--no-cache", action="store_true", help="bypass the on-disk response cache")
    parser.add_argument("--offline", action="store_true", help="serve from cache only")


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings()
    if getattr(args, "no_cache", False):
        settings.cache_ttl = 0
    if getattr(args, "offline", False):
        settings.offline = True
    return settings


def _emit(text: str) -> None:
    sys.stdout.write(text.rstrip() + "\n")


def cmd_scan(args: argparse.Namespace) -> int:
    request = ScanRequest(
        keywords=args.keywords,
        scenario=args.scenario,
        region=args.region,
        providers=args.provider,
        expand_seeds=args.expand,
        depth=args.depth,
        max_keywords=args.max_keywords,
        top=args.top,
        modifiers=args.modifiers,
        include_audience=args.include_audience,
    )
    result = run_scan(request, _settings(args))
    if not result.scored:
        _emit(_no_keywords_message(request))
        return 2
    _emit(_render(result, args.format))
    return 0 if any(record.score > 0 for record in result.scored) else 2


def _no_keywords_message(request: ScanRequest) -> str:
    """Explain *why* there was nothing to query, not just that there wasn't."""
    if request.scenario and not request.keywords:
        return (
            "没有从场景描述中提取到关键词。请补充更具体的名词短语，"
            "或直接用 --keywords 指定关键词。"
        )
    return "没有关键词可查询：请提供 --keywords 或 --scenario。"


def cmd_top(args: argparse.Namespace) -> int:
    request = ScanRequest(
        keywords=args.keywords,
        scenario=args.scenario,
        region=args.region,
        providers=args.provider,
        expand_seeds=args.expand,
        max_keywords=args.max_keywords,
        top=args.top,
        include_audience=args.include_audience,
    )
    result = run_scan(request, _settings(args))
    if args.format == "json":
        payload = result.to_dict()
        _emit(json.dumps({"top": payload["summary"].get("top_n", [])[: args.top]}, ensure_ascii=False, indent=2))
    elif args.format == "md":
        _emit(render_markdown(result, top=args.top))
    else:
        _emit(render_leaders(result.scored, args.top))
    return 0 if result.scored else 2


def cmd_expand(args: argparse.Namespace) -> int:
    settings = _settings(args)
    registry = build_registry(settings)
    seeds = list(args.keywords)
    if args.scenario:
        seeds.extend(suggest_seeds(args.scenario))
    if not seeds:
        _emit("请提供 --keywords 或 --scenario。")
        return 2
    keywords, suggestions = expand(
        seeds, registry, settings, providers=args.provider, region=args.region, per_seed=10
    )
    keywords = keywords[: args.limit]
    if args.format == "json":
        payload = {
            "seeds": seeds,
            "keywords": keywords,
            "suggestions": [s.to_dict() for s in suggestions],
        }
        _emit(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.format == "md":
        _emit("\n".join(f"- {kw}" for kw in keywords))
    else:
        _emit("\n".join(f"{i:>3}. {kw}" for i, kw in enumerate(keywords, start=1)))
    return 0


def cmd_seeds(args: argparse.Namespace) -> int:
    seeds = suggest_seeds(args.scenario, limit=args.limit)
    if args.modifiers:
        seeds = add_modifiers(seeds, detect_language(args.scenario), limit=args.limit)
    if args.format == "json":
        _emit(json.dumps({"scenario": args.scenario, "seeds": seeds}, ensure_ascii=False, indent=2))
    elif args.format == "md":
        _emit("\n".join(f"- {seed}" for seed in seeds))
    else:
        _emit("\n".join(f"{i:>3}. {seed}" for i, seed in enumerate(seeds, start=1)))
    return 0


def cmd_providers(args: argparse.Namespace) -> int:
    rows = describe_provider_health(_settings(args), args.region)
    if args.format == "json":
        _emit(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if args.format == "md":
        lines = ["| provider | 名称 | 区域 | 可用 | 说明 |", "|---|---|---|---|---|"]
        for row in rows:
            lines.append(
                f"| `{row['name']}` | {row['label']} | {', '.join(row['regions'])} | "
                f"{'是' if row['available'] else '否'} | {row['note']} |"
            )
        _emit("\n".join(lines))
        return 0
    width = max(len(row["name"]) for row in rows)
    lines = []
    for row in rows:
        flag = "✓" if row["available"] else "✗"
        regions = ",".join(row["regions"])
        lines.append(f"{flag} {row['name'].ljust(width)}  {row['label']:<12} [{regions}]  {row['note']}")
    _emit("\n".join(lines))
    return 0


def _render(result, fmt: str) -> str:
    if fmt == "json":
        return render_json(result)
    if fmt == "md":
        return render_markdown(result)
    return render_table(result)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _emit("已取消。")
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
