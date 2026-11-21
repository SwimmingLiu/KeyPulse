#!/usr/bin/env python3
"""Record real provider values into a replay cache for the eval suite.

The eval sandbox denies all network access, so eval cases cannot query Baidu or
Google live.  This script runs each provider once with networking enabled —
including the browser bridge, which is how Baidu Index and Google Trends are
actually reached — and writes the results to ``evals/fixtures/cache.json``.

The eval fixture points ``KEYPULSE_REPLAY`` at that file; the provider layer
serves those values instead of making a request.  The agent therefore exercises
the real scan, scoring and rendering path on fixed, genuine data.

Usage:
    python3 evals/fixtures/record_cache.py --keywords 人工智能 量子计算 猫粮
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from keypulse.config import Settings  # noqa: E402
from keypulse.scan import ScanRequest, run_scan  # noqa: E402

OUT = Path(__file__).resolve().parent / "cache.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keywords", nargs="+", required=True)
    parser.add_argument("--region", default="CN")
    args = parser.parse_args()

    settings = Settings()
    settings.cache_dir = Path("/tmp/keypulse-record")
    settings.offline = False
    settings.cache_ttl = 0  # always fetch fresh while recording

    result = run_scan(
        ScanRequest(keywords=args.keywords, region=args.region, top=len(args.keywords)),
        settings,
    )

    payload: dict = {"region": args.region, "keywords": {}, "captured_at": result.started_at}
    for signal in result.signals:
        if not signal.ok:
            continue
        payload["keywords"].setdefault(signal.keyword, {})[signal.provider] = {
            "metric": signal.metric,
            "value": signal.value,
            "unit": signal.unit,
            "detail": signal.detail,
        }

    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ({len(payload['keywords'])} keywords)")
    for keyword, signals in payload["keywords"].items():
        print(f"  {keyword}: " + ", ".join(f"{p}={v['value']:g}" for p, v in signals.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
