#!/usr/bin/env python3
"""Open the 「微信指数」 mini-program in the macOS WeChat client.

Why this exists: ``read_token.py`` can only read a login that WeChat has
already performed.  When the ``search_key`` has aged out, someone has to make
the client open the mini-program again — and doing that by hand (or by
guessing coordinates) is the fragile part.

Two routes, tried in order:

1. **Already open** — the window titled 微信指数 exists.  Nothing to do; the
   login is already fresh (or, if it is not, WeChat refreshes it on show).
2. **Search** — click the search box in the WeChat main window and type
   微信指数.  This is the reliable route: the 最近使用 list shifts as other
   mini-programs are used, so it cannot be trusted to still contain it.

The Mini Programs grid is deliberately NOT used as a third route: it is
several clicks deep, reorders itself, and each click is a chance to land on
the wrong thing.

Focus is the real hazard when driving another app: another process can take
focus between ``activate`` and the synthetic click, and the click then lands
in the wrong window.  Every step here re-checks that WeChat is frontmost and
bails out instead of clicking blind.

Exit codes: 0 the mini-program is open, 1 could not open it (prints why).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEARCH_SWIFT = HERE / "search.swift"
WINDOW_NAME = "微信指数"
MAIN_WINDOW = "Weixin"

#: Search box in the main window, as a fraction of its width/height.
SEARCH_BOX = (0.186, 0.040)


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def frontmost() -> str:
    proc = _run(
        ["/usr/bin/osascript", "-e",
         'tell application "System Events" to get name of first process whose frontmost is true']
    )
    return proc.stdout.strip()


def windows() -> list[str]:
    proc = _run(
        ["/usr/bin/osascript", "-e",
         'tell application "System Events" to tell process "WeChat" to get name of every window']
    )
    if proc.returncode != 0:
        return []
    return [part.strip() for part in proc.stdout.strip().split(",") if part.strip()]


def main_rect() -> dict | None:
    script = (
        'tell application "System Events" to tell process "WeChat"\n'
        f'  if not (exists window "{MAIN_WINDOW}") then return "MISSING"\n'
        f'  return {{position, size}} of window "{MAIN_WINDOW}"\n'
        "end tell"
    )
    proc = _run(["/usr/bin/osascript", "-e", script])
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out or out == "MISSING":
        return None
    try:
        x, y, width, height = [int(float(v)) for v in out.split(",")]
    except ValueError:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def focus_wechat() -> bool:
    """Bring WeChat forward and confirm it actually got focus."""
    _run(["/usr/bin/osascript", "-e", 'tell application "WeChat" to activate'])
    _run(
        ["/usr/bin/osascript", "-e",
         'tell application "System Events" to tell process "WeChat" to set frontmost to true']
    )
    for _ in range(6):
        if frontmost() == "WeChat":
            return True
        time.sleep(0.4)
    return False


def click(x: int, y: int) -> None:
    _run(["/usr/bin/swift", str(SEARCH_SWIFT), "click", str(int(x)), str(int(y))])


def type_text(text: str) -> None:
    _run(["/usr/bin/swift", str(SEARCH_SWIFT), "type", text])


def open_by_search() -> tuple[bool, str]:
    """Search WeChat for the mini-program and open it from the results."""
    rect = main_rect()
    if rect is None:
        return False, "找不到微信主窗口（Weixin）"
    if not focus_wechat():
        return False, "无法把微信切到前台（可能有其他应用在抢焦点）"
    x = rect["x"] + SEARCH_BOX[0] * rect["width"]
    y = rect["y"] + SEARCH_BOX[1] * rect["height"]
    click(x, y)
    time.sleep(0.8)
    # A stolen focus after the click types into the wrong app, so re-check.
    if frontmost() != "WeChat":
        return False, "点击后微信失去焦点，已中止（避免把文字打进别的应用）"
    type_text(WINDOW_NAME)
    time.sleep(2.5)
    return True, "已输入搜索词"


def wait_for_window(timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if WINDOW_NAME in windows():
            return True
        time.sleep(1.0)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args(argv)

    if sys.platform != "darwin":
        print(json.dumps({"ok": False, "error": "仅支持 macOS"}, ensure_ascii=False))
        return 1

    if WINDOW_NAME in windows():
        payload = {"ok": True, "already_open": True, "window": WINDOW_NAME}
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    ok, detail = open_by_search()
    if not ok:
        # Focus was lost, or the main window is missing.  Say what to do by
        # hand — this is the one step that cannot be automated safely.
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": detail,
                    "note": (
                        "自动操作没有完成。请手动打开：点微信左上角搜索框，"
                        "输入「微信指数」，在搜索结果里点开它。"
                        "然后运行 keypulse wechat-token。"
                    ),
                },
                ensure_ascii=False,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "ok": False,
                "searched": True,
                "note": (
                    "已在微信搜索框输入「微信指数」。剩下的一步是在搜索结果里点击它——"
                    "搜索结果的位置不固定，自动点击容易点错，留给用户确认更安全。"
                ),
                "detail": detail,
            },
            ensure_ascii=False,
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
