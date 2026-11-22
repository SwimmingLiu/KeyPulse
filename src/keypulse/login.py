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
from types import SimpleNamespace

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
配置需要登录的数据源

  直接运行  keypulse login  可以查看每个来源的登录状态。

百度指数
  在浏览器登录 https://index.baidu.com 后运行

     keypulse login --provider baidu

  KeyPulse 会从浏览器的 Cookie 数据库里读出登录凭据并保存（权限 0600），
  之后命令行可以直接查询，不需要浏览器。也可以手动粘贴：

     keypulse login --baidu-cookie "BDUSS=...; STOKEN=..."

抖音指数 / 小红书
  这两个站点的接口按页面加载签名，没有可以复制出来反复使用的凭据，
  只能在已登录的浏览器里驱动页面。所以要做的是保持浏览器登录：

     keypulse login --provider douyin
     keypulse login --provider xiaohongshu

  它会告诉你当前是否已登录；加 --open 可以直接打开登录页。

微信指数
  凭据来自微信客户端：「微信指数」小程序登录时，客户端会把 openid 和
  search_key 写进自己的 webview 缓存，KeyPulse 只读那个文件。

     keypulse login --provider wechat

  等价于 keypulse wechat-open + keypulse wechat-token。search_key 约 30 分钟
  过期，但小程序遇到 -10000 会自己重新 wx.login 换新 key，所以重新打开一次
  小程序即可，不需要手动登录。

──────────────────────────────────────────────
不想自动读取时，也可以手动填：

   export KEYPULSE_BAIDU_COOKIE="BDUSS=..."
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
    if getattr(args, "force", False):
        argv.append("--force")
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
        print("「微信指数」已经开着，但凭据可能已经过期——现在读取它。")
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
        print("注意：这条 search_key 已经签发超过 30 分钟，服务端会拒绝。"
              "先 wechat-open 打开一次「微信指数」——客户端会自己重新登录换新 key——"
              "然后再运行本命令。")
    else:
        print("search_key 有效期约 30 分钟（实测）。失效后先 wechat-open 再重跑本命令；"
              "客户端在请求被拒时会自动 wx.login 换新 key，打开小程序即可。")
    return 0


def cmd_login(args) -> int:
    """Show login state, or fetch a credential for a named source.

    Three sources need a session and each gets it from somewhere different:

    * 百度指数 — the Cookie in your browser; this command imports it so the
      command line can query without the browser.
    * 抖音指数 / 小红书 — there is no copyable credential (their APIs are
      signed per page load), so the browser has to *stay* logged in.  This
      command says whether it is, and can open the login page for you.
    * 微信指数 — read from the WeChat client (``keypulse wechat-token``).
    """
    settings = Settings()
    if getattr(args, "clear", False) and not (args.baidu_cookie or getattr(args, "provider", "")):
        removed_cookie = clear_cookie(settings)
        removed_wechat = clear_wechat(settings)
        if removed_cookie or removed_wechat:
            print("已清除本地保存的凭据。")
        else:
            print("没有已保存的凭据。")
        return 0

    if args.baidu_cookie:
        cookie = args.baidu_cookie.strip()
        if "BDUSS" not in cookie.upper():
            print("警告：Cookie 中未找到 BDUSS，百度指数通常会拒绝该请求。", file=sys.stderr)
        path = save_cookie(settings, cookie)
        print(f"已保存到 {path}（权限 0600）。")
        print("注意：Cookie 会过期，失效后重新执行本命令即可。")
        return 0

    names = [name for name in (getattr(args, "provider", "") or "").split(",") if name.strip()]
    if not names:
        return _print_status(settings, as_json=getattr(args, "json", False))

    from . import sessions

    if any(name.strip().lower() in {"wechat", "wechat_index", "微信", "微信指数"} for name in names):
        return _login_wechat(restart_client=bool(getattr(args, "restart_client", False)))

    targets = sessions.targets_for(names)
    if not targets:
        print(f"未知的数据源：{', '.join(names)}", file=sys.stderr)
        return 2

    failed = False
    for target in targets:
        state = sessions.detect(settings, target, probe=True)
        if state.logged_in:
            if target.kind == "cookie":
                ok, detail = sessions.import_cookie(settings, target)
                print(f"✓ {target.label}：{detail}")
                failed = failed or not ok
            else:
                print(f"✓ {target.label}：已登录，直接用它扫描即可（凭据只存在于浏览器里，无需保存）")
            continue
        failed = True
        print(f"✗ {target.label}：{state.detail}")
        print(f"  在浏览器登录 {target.url}")
        if getattr(args, "open", False):
            opened, detail = sessions.open_login(settings, target)
            print(f"  {detail}")
            if not opened:
                print("  请手动打开上面的网址")
        print(f"  登录后重跑：keypulse login --provider {target.alias or target.provider}")
    return 1 if failed else 0


def _login_wechat(restart_client: bool = False) -> int:
    """Get a usable 微信指数 key: reuse, reopen, or cold-start, then read it.

    The mini-program renews the key itself (``-10000`` → ``wx.login`` →
    ``weapplogin``), but only when it actually sends a request.  An already
    open window is therefore not proof of a fresh key, which is why the age
    is checked first.

    Restarting the whole client is the reliable last resort (it always runs
    the login flow), but it closes the user's chat app — so it only happens
    when they asked for it with ``--restart-client``.
    """
    from . import sessions

    settings = Settings()
    state = sessions.wechat_state(settings)
    if state.logged_in:
        print(f"✓ 微信指数：{state.detail}，直接读取。")
        return cmd_wechat_token(SimpleNamespace(clear=False, container=""))

    print(f"微信指数凭据不可用（{state.detail}），打开小程序让客户端换一条新 key…")
    cmd_wechat_open(SimpleNamespace(json=False, force=True))
    code = cmd_wechat_token(SimpleNamespace(clear=False, container=""))
    state = sessions.wechat_state(settings)
    if state.logged_in:
        return code

    if restart_client:
        print("自动打开失败，重启微信客户端（会关掉当前微信窗口）…")
        _restart_wechat_client()
        cmd_wechat_open(SimpleNamespace(json=False, force=True))
        code = cmd_wechat_token(SimpleNamespace(clear=False, container=""))
        state = sessions.wechat_state(settings)
        if state.logged_in:
            return code

    print("仍然没有拿到新 key。请手动在微信里打开「微信指数」小程序并搜索一次，"
          "然后运行 keypulse wechat-token。")
    if not restart_client:
        print("（如果小程序打不开，可以加 --restart-client 让 KeyPulse 重启微信客户端——注意它会关掉微信窗口。）")
    return 1


def _restart_wechat_client() -> None:
    """Quit and relaunch WeChat.

    A full restart is what guarantees a new ``search_key``: the mini-program
    only calls ``wx.login`` when its stored ``third_session`` is gone or the
    server rejects it, and a restart clears the renderer state outright.
    """
    script = Path(__file__).resolve().parents[2] / "scripts" / "wechat-index" / "wxdrive.swift"
    binary = Path.home() / ".cache" / "keypulse" / "wxdrive"
    if not binary.exists() and script.exists():
        subprocess.run(
            ["/usr/bin/swiftc", "-O", str(script), "-o", str(binary)],
            capture_output=True,
        )
    if binary.exists():
        subprocess.run([str(binary), "pastequit"], capture_output=True)
    else:
        subprocess.run(["/usr/bin/osascript", "-e", 'tell application "WeChat" to quit'],
                       capture_output=True)
    import time

    time.sleep(3)
    subprocess.run(["/usr/bin/open", "-a", "WeChat"], capture_output=True)
    time.sleep(8)


def _print_status(settings: Settings, *, as_json: bool = False) -> int:
    """The 'what do I need to log in to' table."""
    from . import sessions

    states = sessions.detect_all(settings, probe=True)
    if as_json:
        print(json.dumps([state.to_dict() for state in states], ensure_ascii=False, indent=2))
        return 0

    rows = []
    for state in states:
        if state.logged_in is True:
            mark, note = "✓ 已登录", state.detail
        elif state.logged_in is False:
            mark, note = "✗ 未登录", state.detail
        else:
            mark, note = "? 未确认", state.detail
        rows.append((state.label, mark, state.url, note))

    width_label = max(len("数据源"), *(len(row[0]) for row in rows))
    width_mark = max(len("状态"), *(len(row[1]) for row in rows))
    print(f"{'数据源'.ljust(width_label)}  {'状态'.ljust(width_mark)}  说明")
    print("─" * (width_label + width_mark + 40))
    for label, mark, url, note in rows:
        print(f"{label.ljust(width_label)}  {mark.ljust(width_mark)}  {note}")

    missing = [state for state in states if state.logged_in is False]
    if missing:
        print("\n需要登录的来源：")
        for state in missing:
            if state.kind == "client":
                print(f"  {state.label}: 运行 keypulse login --provider wechat"
                      "（打开小程序后客户端会自动换一条新 key）")
            else:
                alias = next(
                    (t.alias or t.provider for t in sessions.TARGETS if t.provider == state.provider),
                    state.provider,
                )
                print(f"  {state.label}: 打开 {state.url} 登录，"
                      f"然后重跑 keypulse login --provider {alias}")
        if any(state.kind != "client" for state in missing):
            print("  也可以加 --open 让 KeyPulse 直接把登录页打开。")
    else:
        print("\n所有需要登录的来源都可用。")
    return 0
