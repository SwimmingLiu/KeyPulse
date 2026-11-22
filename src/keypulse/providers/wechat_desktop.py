"""微信指数 via the WeChat desktop client (macOS).

Two ways to get 微信指数 exist, and this module covers both:

1. **HTTP with captured credentials** (:mod:`wechat_index_browser`) — needs an
   ``openid`` + short-lived ``search_key`` scraped from the mini-program's
   request. Works headless, but the token expires and capturing it needs a
   proxy on a phone.
2. **The desktop client's window** (this module) — WeChat for macOS runs the
   same mini-program; its "微信指数" window shows the index. Reading it is a
   screenshot + Vision OCR, which needs no credentials and never expires, but
   needs Accessibility and Screen Recording permission.

This provider is the second path: it shells out to
``scripts/wechat-index/read_window.py``, which reports *which* permission is
missing instead of failing silently, so a scan can say "open the mini-program"
or "grant Screen Recording" rather than "unavailable".

It is opt-in by nature: the window must be open on the keyword you ask about
(use ``--set-keyword`` to type it in), and it is macOS-only.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from pathlib import Path

from ..models import Signal
from .base import Provider, normalise_region


def script_path() -> Path:
    """Locate ``scripts/wechat-index/read_window.py`` in the install tree."""
    # src/keypulse/providers/wechat_desktop.py -> repo root
    return Path(__file__).resolve().parents[3] / "scripts" / "wechat-index" / "read_window.py"


class WeChatDesktopIndex(Provider):
    name = "wechat_desktop_index"
    label = "微信指数(桌面客户端)"
    metric = "index"
    unit = "raw"
    kind = "index"
    tier = "index"
    access = "needs_cookie"
    regions = ("CN",)
    requires_session = True

    def _available(self) -> tuple[bool, str]:
        if platform.system() != "Darwin":
            return False, "仅支持 macOS（读取微信桌面客户端的窗口）"
        script = script_path()
        if not script.exists():
            return False, f"缺少 {script}"
        if shutil.which("osascript") is None:
            return False, "没有 osascript"
        return True, "ok"

    def check(self) -> tuple[bool, str]:
        try:
            if any(self.settings.cache_dir.glob(f"replay-{self.name}-*.json")):
                return True, "ok (replay)"
        except OSError:
            pass
        ok, reason = self._available()
        if not ok:
            return False, reason
        # Ask the script itself: it names the specific blocker (window closed,
        # no Screen Recording) instead of us guessing.
        probe = self._run_script()
        if probe.get("blocker") == "window":
            return False, "微信指数窗口未打开（在微信里打开小程序后重试）"
        if probe.get("blocker") == "screen_recording":
            return False, "需要屏幕录制权限（系统设置 → 隐私与安全性 → 屏幕录制）"
        if probe.get("blocker"):
            return False, str(probe.get("error") or probe["blocker"])
        return True, "ok (desktop window)"

    def _run_script(self) -> dict:
        script = script_path()
        try:
            proc = subprocess.run(
                ["python3", str(script), "--json"],
                capture_output=True,
                text=True,
                timeout=max(45.0, self.settings.timeout * 3),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "blocker": "timeout", "error": str(exc)}
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except ValueError:
                    continue
        return {"ok": False, "blocker": "bad_output", "error": (proc.stderr or proc.stdout)[:200]}

    def signal(self, keyword: str, region: str = "CN") -> Signal:
        recorded = self._replay(keyword)
        if recorded:
            return self._replayed_signal(keyword, region, recorded)

        ok, reason = self._available()
        if not ok:
            return self._failure(keyword, reason, region)

        payload = self._run_script()
        if payload.get("blocker"):
            return self._failure(keyword, str(payload.get("error") or payload["blocker"]), region)
        if payload.get("unindexed"):
            return Signal(
                keyword=keyword,
                provider=self.name,
                metric=self.metric,
                value=0.0,
                unit=self.unit,
                region=normalise_region(region),
                detail={
                    "unindexed": True,
                    "note": "微信指数未收录该词",
                    "transport": "desktop-ocr",
                },
            )
        value = payload.get("index")
        if value is None:
            return self._failure(keyword, "OCR 没有读到指数数字", region)
        return Signal(
            keyword=keyword,
            provider=self.name,
            metric=self.metric,
            value=float(value),
            unit=self.unit,
            region=normalise_region(region),
            detail={
                "pct": payload.get("pct"),
                "date": payload.get("date"),
                "text": payload.get("text"),
                "note": "从微信桌面客户端的「微信指数」窗口读取",
                "transport": "desktop-ocr",
            },
        )
