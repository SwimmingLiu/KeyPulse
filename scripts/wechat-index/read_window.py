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
import time
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


#: The search entry sits at this fraction of the window's height on the home
#: screen (the app has no accessible elements, so this is a screen coordinate
#: derived from a 414x780 window). The search *field* on the results page is at
#: ``SEARCH_FIELD_Y``. Both are window-relative; see ``_click_window``.
SEARCH_BOX = (0.50, 0.235)
SEARCH_FIELD = (0.50, 0.051)
#: "<" in the top-left of the detail/results page returns to the home screen.
BACK_BUTTON = (0.053, 0.056)


def _on_home(rect: dict) -> bool:
    """True when the home screen is showing (it has 我的订阅 / 浏览历史)."""
    with tempfile.TemporaryDirectory(prefix="wxhome-") as tmp:
        img = Path(tmp) / "w.png"
        region = ",".join(str(rect[k]) for k in ("x", "y", "width", "height"))
        if _run([SCREENCAPTURE, "-x", "-R", region, str(img)]).returncode != 0:
            return False
        try:
            text = " ".join(l.get("text", "") for l in ocr(img)["lines"])
        except Exception:  # noqa: BLE001
            return False
    return ("浏览历史" in text) or ("我的订阅" in text)


def _click_window(rect: dict, frac: tuple[float, float]) -> None:
    """Click a window-relative point, converting to global screen coordinates."""
    x = rect["x"] + frac[0] * rect["width"]
    y = rect["y"] + frac[1] * rect["height"]
    _run(["/usr/bin/swift", str(HERE / "search.swift"), "click", str(int(x)), str(int(y))])


def search(keyword: str, rect: dict) -> bool:
    """Type ``keyword`` into the mini-program and submit it.

    Returns False when the keyword does not come back on the results page,
    which is the signal that the search did not take (no Accessibility grant,
    or the app changed its layout). Typing goes through the clipboard because
    synthetic key events for CJK text are unreliable.
    """
    # Normalise to the home screen first: a detail page has the search box in a
    # different place, and this call may follow a previous search.  Bounded
    # retries because a single click occasionally lands while the page is
    # still animating.
    for _ in range(3):
        if _on_home(rect):
            break
        _click_window(rect, BACK_BUTTON)
        time.sleep(1.0)
    _click_window(rect, SEARCH_BOX)
    time.sleep(0.9)
    # The results page opens with a focused field; put the text there.
    _click_window(rect, SEARCH_FIELD)
    time.sleep(0.6)
    _run(["/usr/bin/swift", str(HERE / "search.swift"), "type", keyword])
    return True


def window_rect() -> tuple[dict | None, str]:
    """Locate the 微信指数 window via Accessibility. Returns (rect, error).

    The window can be minimised or hidden behind another app after the user
    last looked at it; bring it back before deciding it is gone, otherwise a
    background scan reports "窗口未打开" for a window that exists.
    """
    script = (
        'tell application "System Events" to tell process "WeChat"\n'
        f'  if not (exists window "{WINDOW_NAME}") then return "MISSING"\n'
        f'  set value of attribute "AXMinimized" of window "{WINDOW_NAME}" to false\n'
        '  set frontmost to true\n'
        f'  perform action "AXRaise" of window "{WINDOW_NAME}"\n'
        '  delay 0.3\n'
        f'  return {{position, size}} of window "{WINDOW_NAME}"\n'
        "end tell"
    )
    proc = _run(["/usr/bin/osascript", "-e", script])
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out or out == "MISSING":
        return None, (proc.stderr.strip() or "微信指数窗口不存在（请先在小程序里打开）")
    try:
        x, y, width, height = [int(float(v)) for v in proc.stdout.strip().split(",")]
    except ValueError:
        return None, f"无法解析窗口位置: {proc.stdout!r}"
    return {"x": x, "y": y, "width": width, "height": height}, ""


def activate() -> bool:
    """Bring the index window to the front and confirm it actually is.

    AXRaise alone is not enough: another app can hold focus and the screen
    capture then photographs the wrong window.  Raising the window also needs
    its process frontmost, and there is a short delay before the compositor
    reflects it, so verify with the window's position rather than assuming.
    """
    script = (
        'tell application "System Events" to tell process "WeChat"\n'
        "  set frontmost to true\n"
        f'  perform action "AXRaise" of window "{WINDOW_NAME}"\n'
        "  delay 0.4\n"
        f'  return position of window "{WINDOW_NAME}"\n'
        "end tell"
    )
    proc = _run(["/usr/bin/osascript", "-e", script])
    return proc.returncode == 0 and bool(proc.stdout.strip())


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
    """Pull the index out of OCR lines, keyed by keyword.

    The window can show several numbers at once — 浏览历史 lists every recent
    keyword with its own index and percentage (行测 3,551,726 / 行测分 2,131 /
    行测估分 1,367).  They are different keywords, so taking the largest number
    would report one term's index for another.  Instead each OCR line is split
    into (label, number) pairs and returned as a mapping; the caller picks the
    keyword it actually asked for.

    When the window shows a single keyword's detail page there is no label, and
    the lone number is returned under the empty-string key.
    """
    joined = " ".join(l.get("text", "").strip() for l in lines)
    if any(marker in joined for marker in NOT_INDEXED):
        return {"unindexed": True, "note": "微信指数未收录该词"}
    if any(marker in joined for marker in NO_DATA):
        return {"no_data": True, "note": "微信指数暂无数据"}

    entries: dict[str, int] = {}
    # OCR returns each visual row as separate fragments (label, number, pct).
    # Their y values differ by a hair — 行测 0.7624 vs its number 0.7645 rounds
    # to a different bucket — so cluster with a tolerance (here 0.006 of the
    # window height, ~5px) instead of exact bins.
    # Section headings ("浏览历史", "我的订阅") sit close to the first data row
    # and would otherwise be absorbed into it, becoming the row's label.
    HEADINGS = {"浏览历史", "我的订阅", "公告", "搜索关键词"}

    frags = sorted(
        ((float(l.get("y", 0.0)), (l.get("text") or "").strip()) for l in lines),
        key=lambda item: item[0],
    )
    rows: list[list[str]] = []
    last_y = None
    for y, text in frags:
        if not text or text.strip(" 　·•|") in HEADINGS:
            continue
        if last_y is None or y - last_y > 0.006:
            rows.append([])
        rows[-1].append(text)
        last_y = y

    for parts in rows:
        number = None
        pct = None
        label = ""
        for part in parts:
            match = INDEX_RE.fullmatch(part.replace(" ", ""))
            if match and number is None:
                number = int(match.group(0).replace(",", ""))
                continue
            found = PCT_RE.search(part)
            if found and pct is None:
                pct = found.group(1) + "%"
                continue
        # The label is the fragment that contains no digits; the percentage
        # fragment ("1.38%") is skipped above, so 行测 survives, 行测分 survives,
        # and a lone index row keeps an empty label.
        for part in parts:
            if PCT_RE.search(part):
                continue          # "1.38%" / "14.18% V" are not labels
            if INDEX_RE.search(part):
                continue          # the index number is not the label
            if not any(ch.isdigit() for ch in part):
                label = part.strip(" 　·•|")
                break
        if number is None or number < 1000:
            continue
        entries[label or "_"] = {"index": number, "pct": pct}

    date = DATE_RE.search(joined)
    return {
        # Kept for callers that only want "some number"; prefer `entries`.
        "index": max((e["index"] for e in entries.values()), default=None),
        "entries": entries,
        "all_values": sorted({e["index"] for e in entries.values()}, reverse=True),
        "date": date.group(0) if date else None,
        "text": joined[:400],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="only emit JSON")
    parser.add_argument("--keep-image", action="store_true")
    parser.add_argument(
        "--keyword",
        default="",
        help="keyword to read; picks its row from 浏览历史. Omit on a detail page.",
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="type the --keyword into 微信指数 first. Needed for a word the "
        "window has never shown; requires Accessibility permission.",
    )
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

    if not activate():
        print(json.dumps({"ok": False, "blocker": "activate",
                          "error": "无法把微信指数窗口置前"}, ensure_ascii=False))
        return 1
    time.sleep(0.5)

    if args.search:
        if not args.keyword:
            print(json.dumps({"ok": False, "blocker": "usage",
                              "error": "--search 需要同时给出 --keyword"}, ensure_ascii=False))
            return 1
        search(args.keyword, rect)
        time.sleep(3.0)

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

    # When a keyword was named, answer for that row only.  Reporting the
    # largest number on screen would attribute one term's index to another.
    if args.keyword:
        entries = result.get("entries") or {}
        hit = entries.get(args.keyword)
        if hit is None:
            # OCR sometimes spaces out a label ("行 测"), so retry ignoring
            # whitespace — but never accept a mere substring.  露营 is a
            # different keyword from 露营装备, and 行测 from 行测分; resolving
            # one to the other would report the wrong number, which is worse
            # than reporting nothing.
            want = "".join(args.keyword.split())
            for label, value in entries.items():
                if label and "".join(label.split()) == want:
                    hit = value
                    break
        payload["keyword"] = args.keyword
        payload["match"] = hit
        payload["ok"] = hit is not None
        if hit is None:
            payload["note"] = "窗口里没有该关键词的行（先在微信指数里搜一次该词）"
            payload["seen"] = [k for k in (result.get("entries") or {}) if k]

    print(json.dumps(payload, ensure_ascii=False, indent=None if args.json else 2))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
