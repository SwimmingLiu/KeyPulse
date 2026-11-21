"""OpenCLI browser bridge: run provider logic inside the user's real browser.

Why this exists: Google Trends returns HTTP 429 to datacenter/shared IPs, and
Baidu/Sogou gate their endpoints behind anti-bot checks that plain HTTP cannot
pass.  ``opencli`` drives an already-logged-in Chrome via a local bridge, so
requests issued *from that page context* carry the user's real cookies, TLS
fingerprint and residential IP.  The same Google Trends endpoint that answers
429 to urllib answers 200 from inside the browser.

Design: opencli exposes ``browser eval --session <name> "<js>"`` (returns JSON)
and ``browser open --session <name> <url>``.  We use ``eval`` to run a small
async JavaScript program that fetches the upstream API and extracts only the
number we need, so no HTML parsing happens outside the browser.

Availability is judged at scan time via ``opencli doctor``; when the bridge is
down or opencli is not installed, every provider here reports itself
unavailable and the scan falls back to plain HTTP exactly as before.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from typing import Any

from .config import Settings

#: Session name shared by all KeyPulse browser work; opencli keeps tabs per
#: session so repeated queries reuse the same page and its cookies.
SESSION = "keypulse"

_lock = threading.Lock()


class OpenCliError(RuntimeError):
    pass


def available() -> tuple[bool, str]:
    """Cheap, cached-per-process health check of the browser bridge."""
    binary = shutil.which("opencli")
    if not binary:
        return False, "opencli 未安装（https://github.com/...）"
    try:
        proc = subprocess.run(
            [binary, "doctor"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"opencli doctor 失败: {exc}"
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return False, "opencli doctor 返回非 JSON"
    data = payload.get("data") or {}
    if not payload.get("ok"):
        return False, "opencli doctor 报告异常"
    if not data.get("daemonRunning"):
        return False, "opencli 浏览器桥接未运行"
    if not data.get("extensionConnected"):
        return False, "opencli 浏览器扩展未连接"
    return True, "ok"


def eval_js(settings: Settings, script: str, *, session: str = SESSION, timeout: float | None = None) -> Any:
    """Evaluate JavaScript in the bridged browser and return parsed JSON.

    Callers that batch several keywords should pass an explicit ``timeout``:
    the script waits up to 20s per keyword for its page to render, so a fixed
    deadline would kill the call while it is still making progress.
    """
    binary = shutil.which("opencli")
    if not binary:
        raise OpenCliError("opencli 未安装")
    if timeout is None:
        timeout = max(60.0, settings.timeout * 4)
    # One eval at a time: opencli serialises per session anyway, and this keeps
    # parallel workers from racing on a half-loaded page.
    with _lock:
        try:
            proc = subprocess.run(
                [binary, "browser", "eval", "--session", session, script],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            # Never surface the command line: it contains the whole script.
            raise OpenCliError(
                f"opencli 浏览器调用超时（{timeout:.0f}s）：页面未在预算内返回"
            ) from exc
    if proc.returncode != 0:
        raise OpenCliError(_clean_stderr(proc.stderr) or f"opencli exited {proc.returncode}")
    try:
        envelope = json.loads(proc.stdout)
    except ValueError as exc:
        raise OpenCliError(f"opencli 输出非 JSON: {proc.stdout[:200]}") from exc
    if not envelope.get("ok"):
        error = envelope.get("error") or {}
        raise OpenCliError(str(error.get("message") or error)[:300])
    return envelope.get("data")


def _clean_stderr(stderr: str) -> str:
    """First meaningful stderr line, without echoing any command arguments."""
    for line in (stderr or "").splitlines():
        line = line.strip()
        if line and not line.startswith(("Command", "[", "+")):
            return line[:200]
    return ""


def open_page(settings: Settings, url: str, *, session: str = SESSION) -> None:
    """Navigate the session's tab so relative fetches resolve on that origin."""
    binary = shutil.which("opencli")
    if not binary:
        raise OpenCliError("opencli 未安装")
    with _lock:
        try:
            proc = subprocess.run(
                [binary, "browser", "open", "--session", session, url],
                capture_output=True,
                text=True,
                timeout=max(30.0, settings.timeout * 4),
            )
        except subprocess.TimeoutExpired as exc:
            raise OpenCliError("opencli 打开页面超时") from exc
    if proc.returncode != 0:
        raise OpenCliError(proc.stderr.strip()[:300] or f"opencli exited {proc.returncode}")


#: JS helper injected into every script: strips Google's ``)]}'`` prefix.
JSONP_STRIP = """
const _json = (t) => JSON.parse(t.replace(/^\\)\\]\\}',?\\n?/, ''));
"""
