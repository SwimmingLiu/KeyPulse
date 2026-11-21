#!/usr/bin/env python3
"""Assert that every eval case has a replay recording the CLI can read.

Eval cases replay recorded provider values because the eval sandbox has no
network. This check fails CI if a case ships without a recording, or if a
recording names a provider/keyword the fixture cannot materialise — which would
silently turn a real assertion into a "no data" pass.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "evals"


def main() -> int:
    problems: list[str] = []
    cases = [p for p in CASES.iterdir() if p.is_dir() and (p / "prompt.md").exists()]
    if not cases:
        problems.append("no eval cases found")

    for case in sorted(cases):
        recording = case / "recording.json"
        fixture = case / "fixture.sh"
        if not fixture.exists():
            problems.append(f"{case.name}: missing fixture.sh")
        if not recording.exists():
            problems.append(f"{case.name}: missing recording.json")
            continue
        try:
            data = json.loads(recording.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"{case.name}: recording.json is not valid JSON ({exc})")
            continue
        keywords = data.get("keywords") or {}
        if not keywords:
            problems.append(f"{case.name}: recording has no keywords")
            continue
        for keyword, providers in keywords.items():
            if not providers:
                problems.append(f"{case.name}: {keyword} has no providers")
            for provider, entry in providers.items():
                if "value" not in entry:
                    problems.append(f"{case.name}: {keyword}/{provider} has no value")

    # The scan case must be able to produce a ranking that differs between
    # keywords, otherwise the assertion is vacuous.
    scan_recording = CASES / "scan-ranks" / "recording.json"
    if scan_recording.exists():
        data = json.loads(scan_recording.read_text(encoding="utf-8"))
        values = {
            kw: {p: v.get("value") for p, v in providers.items()}
            for kw, providers in (data.get("keywords") or {}).items()
        }
        if len({json.dumps(v, sort_keys=True) for v in values.values()}) < 2:
            problems.append("scan-ranks: all keywords have identical recorded values")

    if problems:
        print("recording check FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"recording check passed for {len(cases)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
