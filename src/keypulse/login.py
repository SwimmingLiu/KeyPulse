"""``keypulse login`` — store a Baidu Index cookie for later scans.

百度指数 is the one source that both measures demand directly and needs a
credential.  Rather than asking users to export an environment variable by
hand, this writes the cookie to ``<cache>/credentials.json`` with file mode
0600, and prints exactly what to paste and where to find it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .config import Settings

CREDENTIALS_FILE = "credentials.json"


def credentials_path(settings: Settings) -> Path:
    return settings.cache_dir / CREDENTIALS_FILE


def load_stored_cookie(settings: Settings) -> str:
    path = credentials_path(settings)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(payload.get("baidu_cookie") or "")


def save_cookie(settings: Settings, cookie: str) -> Path:
    directory = settings.ensure_cache_dir()
    path = credentials_path(settings)
    payload: dict[str, str] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
    payload["baidu_cookie"] = cookie.strip()
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows and some filesystems have no meaningful mode bits
    return path


def clear_cookie(settings: Settings) -> bool:
    path = credentials_path(settings)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    payload.pop("baidu_cookie", None)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return True


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
配置微信指数（两种方式，任选其一）

方式 A：读取微信桌面客户端窗口（macOS，推荐）
  - 在微信里打开「微信指数」小程序，搜一个词
  - 授予「屏幕录制」权限：系统设置 → 隐私与安全性 → 屏幕录制
  - 直接运行：python3 scripts/wechat-index/read_window.py
  - KeyPulse 会自动使用它（provider: wechat_desktop_index）
  - 不需要任何 token，也不会过期；但需要窗口处于打开状态

方式 B：抓取小程序请求里的 openid + search_key
  - 在手机上用 mitmproxy/Charles 抓微信指数小程序的请求
  - 从 URL 里取 openid=... 与 search_key=...
  - 写入环境变量：
      export KEYPULSE_WECHAT_OPENID="..."
      export KEYPULSE_WECHAT_SEARCH_KEY="..."
  - search_key 会过期（几分钟到 24 小时），失效后重新抓
"""


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
