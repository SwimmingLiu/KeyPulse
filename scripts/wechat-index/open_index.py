#!/usr/bin/env python3
"""Open the 「微信指数」 mini-program in the macOS WeChat client.

Why this exists: ``read_token.py`` can only read a login that WeChat has
already performed, and the ``search_key`` it yields lives about 30 minutes.
So a refresh means "make the client open the mini-program again", and that is
the fragile step — WeChat's window is routinely buried under other windows and
loses focus between "activate" and a synthetic click.

How it avoids the usual failures:

* **Compiled driver.** ``wxdrive.swift`` is compiled once and cached.  Calling
  it through ``swift file.swift`` would recompile on every invocation (~1–2 s),
  which is long enough for another app to take focus back.
* **Activate-and-confirm in one process.** The driver activates WeChat, checks
  that it really is frontmost (retrying, and asking System Events as a second
  voice), and only then clicks.
* **Window-relative coordinates, resolved at click time.** The window gets
  dragged around by stray clicks, so an absolute point read a second earlier
  can be wrong.  ``clickrel`` reads the window bounds in the same process as
  the click.
* **The target is found by OCR, not by a hard-coded row.** 最近使用 reorders
  itself; the position of 「微信指数」 is whatever the screenshot says it is.

Usage::

    python3 open_index.py [--json] [--build-only]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRIVER_SWIFT = HERE / "wxdrive.swift"
WINDOW_NAME = "微信指数"
MAIN_WINDOW = "Weixin"

#: Discover (compass) icon, as a fixed offset from the window's top-left.
#: Fixed, not a fraction: the icon rail is a fixed-size column, so a fraction
#: points somewhere else as soon as the window is resized.
DISCOVER_ICON = (32, 255)
#: The search box at the top-left of the main window (same reasoning).
SEARCH_BOX = (110, 30)


class DriverError(RuntimeError):
    pass


def _cache_dir() -> Path:
    base = Path(os.environ.get("KEYPULSE_CACHE_DIR") or (Path.home() / ".cache" / "keypulse"))
    return base


def driver_path() -> Path:
    return _cache_dir() / "wxdrive"


def ensure_driver() -> Path:
    """Compile ``wxdrive.swift`` once; return the cached binary.

    Compiled rather than run through ``swift`` because interpreter start-up is
    the difference between landing a click and losing focus to another app.
    """
    binary = driver_path()
    source_mtime = DRIVER_SWIFT.stat().st_mtime if DRIVER_SWIFT.exists() else 0
    if binary.exists() and binary.stat().st_mtime >= source_mtime:
        return binary
    if shutil.which("swiftc") is None:
        raise DriverError("找不到 swiftc（需要 Xcode 命令行工具）")
    binary.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["/usr/bin/swiftc", "-O", str(DRIVER_SWIFT), "-o", str(binary)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise DriverError(f"编译 wxdrive 失败：{(proc.stderr or proc.stdout)[:300]}")
    return binary


def _drive(*args: str, timeout: float = 20.0) -> subprocess.CompletedProcess:
    binary = ensure_driver()
    return subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=timeout)


def windows() -> list[dict]:
    proc = _drive("windows")
    try:
        return json.loads(proc.stdout or "[]")
    except ValueError:
        return []


def is_open() -> bool:
    return any(w.get("name") == WINDOW_NAME for w in windows())


def main_window() -> dict | None:
    """The largest WeChat window (the main one), which is what we click in."""
    best = None
    for w in windows():
        if w.get("name") == WINDOW_NAME:
            continue
        if (w.get("w") or 0) < 200 or (w.get("h") or 0) < 200:
            continue
        if best is None or w["w"] * w["h"] > best["w"] * best["h"]:
            best = w
    return best


def activate() -> bool:
    return _drive("activate").returncode == 0


def click_fraction(fx: float, fy: float) -> bool:
    """Click at a fraction of the window — used for OCR hits, which are
    normalised to the screenshot and therefore proportional."""
    return _drive("clickrel", f"{fx:.4f}", f"{fy:.4f}").returncode == 0


def click_offset(dx: float, dy: float) -> bool:
    """Click a fixed distance from the window's top-left (icon rail, search box)."""
    return _drive("clickoff", f"{dx:.0f}", f"{dy:.0f}").returncode == 0


def type_text(text: str) -> bool:
    """Paste ``text`` into the focused field, then press Return."""
    try:
        subprocess.run(["/usr/bin/pbcopy"], input=text.encode("utf-8"), check=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return _drive("type").returncode == 0


def capture(window_id: int) -> Path | None:
    """Screenshot one window by id, even when it is buried.

    ``-o`` omits the drop shadow; with the shadow the image is larger than the
    window, so fractions computed from it do not map back to the window.
    """
    tmp = Path(tempfile.mkdtemp(prefix="wxshot-")) / "window.png"
    proc = subprocess.run(
        ["/usr/sbin/screencapture", "-x", "-o", "-l", str(window_id), str(tmp)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        return None
    return tmp


def ocr(image: Path) -> list[dict]:
    """Recognise text in ``image``; each line carries normalised x and y."""
    binary = _cache_dir() / "wxocr"
    if not binary.exists() or binary.stat().st_mtime < (HERE / "ocr_image.swift").stat().st_mtime:
        binary.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["/usr/bin/swiftc", "-O", str(HERE / "ocr_image.swift"), "-o", str(binary)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise DriverError(f"编译 wxocr 失败：{(proc.stderr or proc.stdout)[:300]}")
    proc = subprocess.run([str(binary), str(image)], capture_output=True, text=True)
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return []
    return payload.get("lines") or []


def find_text(lines: list[dict], needle: str) -> dict | None:
    """The OCR line whose text is exactly ``needle`` (or contains it)."""
    for line in lines:
        if (line.get("text") or "").strip() == needle:
            return line
    for line in lines:
        if needle in (line.get("text") or ""):
            return line
    return None


def weapp_running() -> bool:
    """Is the mini-program runtime alive?

    The window can outlive its process: after ``WeApp`` is killed the window
    stays on screen as a ghost that never responds.  Checking the process is
    what tells a live mini-program from that ghost.
    """
    return subprocess.run(
        ["/usr/bin/pgrep", "-f", "MacOS/WeApp"], capture_output=True
    ).returncode == 0


def close_window(name: str) -> bool:
    """Close a WeChat window by clicking into it and pressing ⌘W."""
    for w in windows():
        if w.get("name") != name:
            continue
        cx = w["x"] + w["w"] / 2
        cy = w["y"] + w["h"] / 2
        if _drive("click", f"{cx:.0f}", f"{cy:.0f}").returncode != 0:
            return False
        time.sleep(0.5)
        _drive("key", "13", "--cmd")
        time.sleep(1.0)
        return True
    return False


def force_cold_start() -> str:
    """Kill the runtime and clear the ghost window.

    Why: WeChat mints a new ``search_key`` when the mini-program *starts*, not
    when its window is merely shown again — observed directly, a reopen with
    the runtime alive produced no ``weapplogin`` at all.  So a refresh has to
    be a cold start.
    """
    subprocess.run(["/usr/bin/pkill", "-f", "MacOS/WeApp"], capture_output=True)
    time.sleep(1.5)
    if any(w.get("name") == WINDOW_NAME for w in windows()):
        close_window(WINDOW_NAME)
    return "restarted the mini-program runtime"


def wait_until_open(timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_open():
            return True
        time.sleep(1.0)
    return False


def _click_row(label: str, *, exact: bool = True) -> tuple[bool, str]:
    """Screenshot the main window, OCR it, and click ``label``.

    OCR-driven rather than coordinate-driven: the Discover panel's rows and
    最近使用 both reorder themselves, so a remembered position is a guess.
    """
    win = main_window()
    if win is None:
        return False, "找不到微信主窗口"
    shot = capture(win["id"])
    if shot is None:
        return False, "无法截取微信窗口"
    lines = ocr(shot)
    hit = None
    if exact:
        hit = next((l for l in lines if (l.get("text") or "").strip() == label), None)
    if hit is None:
        hit = find_text(lines, label)
    if hit is None:
        return False, f"界面上没有「{label}」"
    if not click_fraction(hit["x"], hit["y"]):
        return False, f"无法点击「{label}」"
    return True, label


def open_via_mini_program_panel() -> tuple[bool, str]:
    """Discover → Mini Programs → the 微信指数 row, each step located by OCR.

    Three clicks, not two: the Discover panel opens at its root, and the
    最近使用 list only appears after picking the Mini Programs row.  Skipping
    that step is what made an earlier version silently find nothing.
    """
    if not activate():
        return False, "无法把微信激活到前台"
    # Clicking the compass toggles the panel: if Discover is already open the
    # first click closes it.  Verify by looking for a row we expect, and click
    # once more if it is not there.
    for attempt in range(2):
        if not click_offset(*DISCOVER_ICON):
            return False, "无法点击「发现」面板"
        time.sleep(1.2)
        win = main_window()
        shot = capture(win["id"]) if win else None
        if shot is not None and find_text(ocr(shot), "Mini Programs") is not None:
            break
        if attempt == 1:
            return False, "没能打开「发现」面板"

    ok, detail = _click_row("Mini Programs")
    if not ok:
        return False, detail
    time.sleep(1.4)

    ok, detail = _click_row(WINDOW_NAME)
    if not ok:
        return False, f"「最近使用」里没有「{WINDOW_NAME}」"
    return True, "点击了「最近使用」里的「微信指数」"


def open_via_search() -> tuple[bool, str]:
    """Fallback: type the name into WeChat's search box and click the result."""
    if not activate():
        return False, "无法把微信激活到前台"
    if not click_offset(*SEARCH_BOX):
        return False, "无法点击微信搜索框"
    time.sleep(0.6)
    if not type_text(WINDOW_NAME):
        return False, "无法输入搜索词"
    time.sleep(2.0)

    win = main_window()
    if win is None:
        return False, "找不到微信主窗口"
    shot = capture(win["id"])
    if shot is None:
        return False, "无法截取微信窗口"
    hit = find_text(ocr(shot), WINDOW_NAME)
    if hit is None:
        return False, "搜索结果里没有「微信指数」"
    if not click_fraction(hit["x"], hit["y"]):
        return False, "无法点击搜索结果"
    return True, "点击了搜索结果里的「微信指数」"


MANUAL_HINT = (
    "请手动打开：点微信左上角搜索框，输入「微信指数」，在结果里点开它。"
    "然后运行 keypulse wechat-token。"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument("--build-only", action="store_true", help="compile the driver and exit")
    parser.add_argument(
        "--force",
        action="store_true",
        help="restart the mini-program even if it is open, so WeChat mints a new key",
    )
    args = parser.parse_args(argv)

    if sys.platform != "darwin":
        print(json.dumps({"ok": False, "error": "仅支持 macOS"}, ensure_ascii=False))
        return 1

    try:
        ensure_driver()
    except DriverError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    if args.build_only:
        print(json.dumps({"ok": True, "driver": str(driver_path())}, ensure_ascii=False))
        return 0

    # Already open AND alive: nothing to do, the key is already current.
    if is_open() and weapp_running() and not args.force:
        print(json.dumps({"ok": True, "already_open": True, "window": WINDOW_NAME}, ensure_ascii=False))
        return 0

    restarted = ""
    if args.force or not weapp_running():
        restarted = force_cold_start()

    attempts: list[str] = []
    for opener in (open_via_mini_program_panel, open_via_search):
        ok, detail = opener()
        attempts.append(detail)
        if ok and wait_until_open():
            print(
                json.dumps(
                    {
                        "ok": True,
                        "opened": True,
                        "how": detail,
                        "attempts": attempts,
                        "restart": restarted,
                    },
                    ensure_ascii=False,
                )
            )
            return 0

    print(
        json.dumps(
            {"ok": False, "error": "；".join(attempts), "note": MANUAL_HINT},
            ensure_ascii=False,
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
