"""``keypulse login`` — store credentials for the providers that need them.

Two sources need one:

* 百度指数 — a cookie copied out of the browser;
* 微信指数 — ``openid`` + ``search_key`` from the mini-program's ``weapplogin``.

Rather than asking users to export an environment variable by hand, this
writes both to ``<cache>/credentials.json`` with file mode 0600, and prints
exactly what to paste and where to find it.  For 微信指数 the key is read
straight out of the WeChat client's own cache, so refreshing is one command.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .config import Settings

CREDENTIALS_FILE = "credentials.json"


def credentials_path(settings: Settings) -> Path:
    return settings.cache_dir / CREDENTIALS_FILE


def _read_payload(settings: Settings) -> dict:
    path = credentials_path(settings)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_payload(settings: Settings, payload: dict) -> Path:
    path = settings.ensure_cache_dir() / CREDENTIALS_FILE
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows and some filesystems have no meaningful mode bits
    return path


def load_stored_cookie(settings: Settings) -> str:
    return str(_read_payload(settings).get("baidu_cookie") or "")


def load_stored_wechat(settings: Settings) -> tuple[str, str]:
    """``(openid, search_key)`` saved by ``keypulse wechat-token``."""
    payload = _read_payload(settings)
    return (
        str(payload.get("wechat_openid") or ""),
        str(payload.get("wechat_search_key") or ""),
    )


def save_cookie(settings: Settings, cookie: str) -> Path:
    payload = _read_payload(settings)
    payload["baidu_cookie"] = cookie.strip()
    return _write_payload(settings, payload)


def save_wechat(settings: Settings, openid: str, search_key: str, captured_at: str = "") -> Path:
    payload = _read_payload(settings)
    payload["wechat_openid"] = openid.strip()
    payload["wechat_search_key"] = search_key.strip()
    if captured_at:
        payload["wechat_captured_at"] = captured_at
    return _write_payload(settings, payload)


def clear_cookie(settings: Settings) -> bool:
    path = credentials_path(settings)
    if not path.exists():
        return False
    payload = _read_payload(settings)
    payload.pop("baidu_cookie", None)
    _write_payload(settings, payload)
    return True


def clear_wechat(settings: Settings) -> bool:
    path = credentials_path(settings)
    if not path.exists():
        return False
    payload = _read_payload(settings)
    removed = bool(payload.pop("wechat_openid", "") or payload.pop("wechat_search_key", ""))
    _write_payload(settings, payload)
    return removed


def token_script() -> Path:
    """``scripts/wechat-index/read_token.py`` in the install tree."""
    return Path(__file__).resolve().parents[2] / "scripts" / "wechat-index" / "read_token.py"


INSTRUCTIONS = """\
配置百度指数 Cookie：

1. 浏览器登录 https://index.baidu.com
2. 打开开发者工具 → Network → 任意请求 → Request Headers → Cookie
3. 复制包含 BDUSS=... 的完整 Cookie 字符串
4. 写入配置：

   keypulse login --baidu-cookie "BDUSS=xxxx; ..."

也可以直接用环境变量（优先级更高）：

   export KEYPULSE_BAIDU_COOKIE="BDUSS=xxxx; ..."

查看当前状态：keypulse providers -r CN

──────────────────────────────────────────────
配置微信指数

第一步（推荐）：一条命令刷新凭据
  keypulse wechat-token

它会从微信客户端自己的 webview 缓存里读出 weapplogin 的
openid + search_key 并存下来（只读本地文件，不需要抓包、不需要 token）。
search_key 有效期约 30 分钟（实测），失效后重新执行同一条命令即可。

小程序本身怎么跑：在微信里打开「微信指数」小程序，搜一次词，
扫一次登录缓存就会刷新。

──────────────────────────────────────────────
微信指数的备用通道：桌面窗口 OCR

  - 在微信里打开「微信指数」小程序，搜一个词
  - 授予「屏幕录制」权限：系统设置 → 隐私与安全性 → 屏幕录制
  - 直接运行：python3 scripts/wechat-index/read_window.py
  - KeyPulse 会自动使用它（provider: wechat_desktop_index）
  - 不需要任何 token，也不会过期；但需要窗口保持打开

──────────────────────────────────────────────
不想自动读取时，也可以手动填：

   export KEYPULSE_WECHAT_OPENID="..."
   export KEYPULSE_WECHAT_SEARCH_KEY="..."
"""


def open_script() -> Path:
    """``scripts/wechat-index/open_index.py`` in the install tree."""
    return Path(__file__).resolve().parents[2] / "scripts" / "wechat-index" / "open_index.py"


def cmd_wechat_open(args) -> int:
    """Bring the 微信指数 mini-program up so WeChat mints a fresh key."""
    script = open_script()
    if not script.exists():
        print(f"缺少 {script}", file=sys.stderr)
        return 1
    argv = [sys.executable, str(script), "--json"]
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    payload: dict = {}
    for line in (proc.stdout or "").splitlines():
        if line.strip().startswith("{"):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
    if not payload:
        print((proc.stderr or proc.stdout or "打开小程序失败").strip())
        return 1
    if payload.get("already_open"):
        print("「微信指数」已经开着——凭据就是最新的，直接扫描即可。")
        return 0
    print(str(payload.get("note") or payload.get("error") or "已完成"))
    return 0 if payload.get("ok") else 1


def cmd_wechat_token(args) -> int:
    """Read (or clear) the 微信指数 credentials from the WeChat client."""
    settings = Settings()
    if args.clear:
        removed = clear_wechat(settings)
        print("已清除本地保存的微信指数凭据。" if removed else "没有已保存的微信指数凭据。")
        return 0
    script = token_script()
    if not script.exists():
        print(f"缺少 {script}", file=sys.stderr)
        return 1
    argv = [sys.executable, str(script), "--json"]
    if getattr(args, "container", ""):
        argv += ["--container", args.container]
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    payload: dict = {}
    for line in (proc.stdout or "").splitlines():
        if line.strip().startswith("{"):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
    if not payload.get("ok"):
        print(payload.get("error") or (proc.stderr or proc.stdout or "读取失败").strip())
        return 1
    path = save_wechat(
        settings,
        payload["openid"],
        payload["search_key"],
        captured_at=str(payload.get("captured_at") or ""),
    )
    age = payload.get("key_age_seconds")
    age_text = f"，已签发 {age // 60} 分钟" if isinstance(age, int) else ""
    print(f"已读取微信指数凭据并保存到 {path}（权限 0600{age_text}）。")
    # A key is good for roughly half an hour; past that it is already dead,
    # so say so instead of letting the next scan fail on a stale copy.
    if isinstance(age, int) and age >= 1800:
        print("注意：这个 search_key 已经超过 30 分钟，服务端会拒绝。"
              "先在微信里打开一次「微信指数」（keypulse wechat-open），再运行本命令。")
    else:
        print("search_key 有效期约 30 分钟（实测），失效后先 wechat-open 再重跑本命令。")
    return 0


def cmd_login(args) -> int:
    settings = Settings()
    if args.clear:
        removed = clear_cookie(settings)
        print("已清除本地保存的百度指数 Cookie。" if removed else "没有已保存的 Cookie。")
        return 0
    if not args.baidu_cookie:
        print(INSTRUCTIONS)
        stored = load_stored_cookie(settings)
        env = settings.baidu_cookie
        if env:
            print("当前状态：已通过环境变量 KEYPULSE_BAIDU_COOKIE 配置（长度 %d）。" % len(env))
        elif stored:
            print("当前状态：已保存本地 Cookie（长度 %d）。" % len(stored))
        else:
            print("当前状态：未配置，百度指数不可用。")
        return 0

    cookie = args.baidu_cookie.strip()
    if "BDUSS" not in cookie.upper():
        print("警告：Cookie 中未找到 BDUSS，百度指数通常会拒绝该请求。", file=sys.stderr)
    path = save_cookie(settings, cookie)
    print(f"已保存到 {path}（权限 0600）。")
    print("注意：Cookie 会过期，失效后重新执行本命令即可。")
    return 0
