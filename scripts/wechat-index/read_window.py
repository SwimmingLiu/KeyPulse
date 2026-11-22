#!/usr/bin/env python3
"""Read 微信指数 from the WeChat desktop client's mini-program window.

Why this exists: 微信指数 has no obtainable HTTP credential path — the
mini-program mints a short-lived ``search_key`` inside the WeChat client, and
capturing it needs a MITM proxy on a phone.  But the desktop client is right
here, so the window can be read the way a person reads it: screenshot, OCR,
parse.

Requirements (macOS only):
  * WeChat.app running with the 「微信指数」 mini-program window open;
  * Accessibility permission (to locate/activate the window);
  * Screen Recording permission for the process running this script.

Exit codes: 0 read succeeded, 1 not ready (prints the blocker), 2 parse failed.
Standard library only; OCR goes through the Vision framework via swift.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OCR_SWIFT = HERE / "ocr_image.swift"
SCREENCAPTURE = "/usr/sbin/screencapture"
WINDOW_NAME = "微信指数"

#: 「1,234,567」 style index values; the mini-program groups thousands.
INDEX_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+\b")
#: 「2026年11月21日指数」-style caption above the headline number.
DATE_RE = re.compile(r"(?:\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}-\d{2}-\d{2})")
PCT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")
NO_DATA = ("暂无数据", "无数据", "暂时没有数据")
NOT_INDEXED = ("暂未收录", "未收录", "未被收录", "指数暂无收录")


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def window_rect() -> tuple[dict | None, str]:
    """Locate the 微信指数 window via Accessibility. Returns (rect, error)."""
    script = (
        'tell application "System Events" to tell process "WeChat" '
        f'to tell window "{WINDOW_NAME}" to get {{position, size}}'
    )
    proc = _run(["/usr/bin/osascript", "-e", script])
    if proc.returncode != 0 or not proc.stdout.strip():
        return None, (proc.stderr.strip() or "微信指数窗口不存在（请先在小程序里打开）")
    try:
        x, y, width, height = [int(float(v)) for v in proc.stdout.strip().split(",")]
    except ValueError:
        return None, f"无法解析窗口位置: {proc.stdout!r}"
    return {"x": x, "y": y, "width": width, "height": height}, ""


def activate() -> None:
    script = (
        'tell application "System Events" to tell process "WeChat" '
        f'to perform action "AXRaise" of window "{WINDOW_NAME}"'
    )
    _run(["/usr/bin/osascript", "-e", script])


def screen_capture_available() -> tuple[bool, str]:
    """A cheap probe: a failed capture means Screen Recording is not granted."""
    with tempfile.TemporaryDirectory(prefix="wxidx-probe-") as tmp:
        target = Path(tmp) / "probe.png"
        proc = _run([SCREENCAPTURE, "-x", str(target)])
        if proc.returncode == 0 and target.exists() and target.stat().st_size > 0:
            return True, ""
        return False, (proc.stderr.strip() or "screencapture 失败")


def ocr(image: Path) -> dict:
    swift = "/usr/bin/swift"
    proc = _run([swift, str(OCR_SWIFT), str(image)])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "OCR 失败")
    return json.loads(proc.stdout)


def parse(lines: list[dict]) -> dict:
    """Pull the headline index out of OCR lines.

    The window prints, top to bottom: the keyword, the date caption, the big
    index number, then the day-over-day figure.  Taking the largest
    comma-grouped number under a date caption is more robust than fixed
    coordinates, which move with window size and font scaling.
    """
    texts = [l.get("text", "").strip() for l in lines]
    joined = " ".join(texts)
    if any(marker in joined for marker in NOT_INDEXED):
        return {"unindexed": True, "note": "微信指数未收录该词"}
    if any(marker in joined for marker in NO_DATA):
        return {"no_data": True, "note": "微信指数暂无数据"}

    values = [int(m.replace(",", "")) for m in INDEX_RE.findall(joined)]
    values = [v for v in values if v >= 1000]  # ignore counts like "1,000 条"
    pct = PCT_RE.search(joined)
    date = DATE_RE.search(joined)
    return {
        "index": max(values) if values else None,
        "all_values": values,
        "pct": pct.group(1) + "%" if pct else None,
        "date": date.group(0) if date else None,
        "text": joined[:400],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="only emit JSON")
    parser.add_argument("--keep-image", action="store_true")
    args = parser.parse_args(argv)

    rect, error = window_rect()
    if rect is None:
        print(json.dumps({"ok": False, "blocker": "window", "error": error}, ensure_ascii=False))
        return 1

    ok, capture_error = screen_capture_available()
    if not ok:
        print(
            json.dumps(
                {
                    "ok": False,
                    "blocker": "screen_recording",
                    "error": capture_error,
                    "hint": "系统设置 → 隐私与安全性 → 屏幕录制，勾选运行本脚本的终端应用",
                },
                ensure_ascii=False,
            )
        )
        return 1

    activate()
    region = ",".join(str(rect[k]) for k in ("x", "y", "width", "height"))
    tmp = Path(tempfile.mkdtemp(prefix="wxidx-"))
    image = tmp / "window.png"
    capture = _run([SCREENCAPTURE, "-x", "-R", region, str(image)])
    if capture.returncode != 0 or not image.exists() or image.stat().st_size == 0:
        print(
            json.dumps(
                {
                    "ok": False,
                    "blocker": "capture",
                    "error": capture.stderr.strip() or "窗口截图失败",
                },
                ensure_ascii=False,
            )
        )
        return 1

    try:
        result = parse(ocr(image)["lines"])
    except Exception as exc:  # noqa: BLE001 - report, do not crash a scan
        print(json.dumps({"ok": False, "blocker": "ocr", "error": str(exc)}, ensure_ascii=False))
        return 2
    finally:
        if not args.keep_image:
            for child in tmp.iterdir():
                child.unlink(missing_ok=True)
            tmp.rmdir()

    payload = {"ok": result.get("index") is not None or result.get("unindexed") is True, **result, "image": str(image)}
    print(json.dumps(payload, ensure_ascii=False, indent=None if args.json else 2))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
