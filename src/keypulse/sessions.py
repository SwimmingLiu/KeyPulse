"""Who is logged in, and where the credential comes from.

Three of KeyPulse's index sources are gated on a session:

* 百度指数 — ``index.baidu.com`` (the cookie is also usable over plain HTTP)
* 抖音指数 — ``creator.douyin.com`` (only usable from the browser that is
  logged in; there is no copyable credential)
* 小红书 — ``www.xiaohongshu.com`` (same)
* 微信指数 — the WeChat desktop client (``openid`` + ``search_key``)

This module answers one question per source — "am I logged in?" — and knows
which of them can be turned into a stored credential.  It deliberately does
*not* guess from cookie names alone for 抖音/小红书: both sites keep a
``www`` cookie that survives while the creator/RED session is gone, and a
wrong "已登录" is worse than an honest check.  Those two are probed in the
browser the bridge will actually use at scan time.

Probes cost a few seconds each, so results are memoised twice: in-process,
and in ``<cache>/sessions.json`` for ``STATE_TTL`` seconds, which is what
lets ``keypulse providers`` stay instant.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings

STATE_TTL = 600.0

#: Login pages, and how to tell "logged in" from "login wall" in each.
#: Polls until either signal appears, instead of sampling the text once: both
#: sites render their shell first and their content (or their login form)
#: seconds later, and a single early read sees "状态不明" for both states.
_PROBE = r"""
(async () => {
  const deadline = Date.now() + 15000;
  const outRe = __OUT_RE__;
  const readyRe = __READY_RE__;
  let text = '';
  while (Date.now() < deadline) {
    text = (document.body && document.body.innerText || '').replace(/\s+/g, ' ');
    // The login wall is checked first: 小红书 renders note cards behind its
    // login modal, so a ready-selector alone would report "logged in" while
    // the scan gets nothing.
    if (__OUT_SEL__ && document.querySelector(__OUT_SEL__)) {
      return JSON.stringify({ ok: true, logged_in: false, evidence: '出现登录表单' });
    }
    if (outRe && outRe.test(text)) {
      return JSON.stringify({ ok: true, logged_in: false, evidence: text.slice(0, 40) });
    }
    if (__READY_SEL__ && document.querySelector(__READY_SEL__)) {
      return JSON.stringify({ ok: true, logged_in: true, evidence: '页面内容已就绪' });
    }
    if (readyRe && readyRe.test(text)) {
      return JSON.stringify({ ok: true, logged_in: true, evidence: text.slice(0, 40) });
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return JSON.stringify({ ok: false, reason: '页面状态不明：' + text.slice(0, 80) });
})()
"""


def _js(value: str) -> str:
    """A JS regex literal, or ``null`` when the pattern is empty."""
    if not value:
        return "null"
    return "/" + value.replace("\\", "\\\\").replace("/", "\\/") + "/"


def _sel(value: str) -> str:
    if not value:
        return "null"
    return json.dumps(value)


@dataclass(frozen=True)
class Target:
    provider: str
    label: str
    url: str
    #: "cookie" — the credential can be stored and used over plain HTTP.
    #: "browser" — only the logged-in browser can use it.
    #: "client" — comes from a desktop app, not a browser.
    kind: str
    domain: str = ""
    cookies: tuple[str, ...] = ()
    out_re: str = ""      # text that means "login wall"
    ready_re: str = ""    # text that means "the page we asked for"
    out_sel: str = ""     # DOM that only the login wall has
    ready_sel: str = ""   # DOM that only a working page has
    #: Short form accepted by ``--provider``.
    alias: str = ""


TARGETS: tuple[Target, ...] = (
    Target(
        provider="baidu_index",
        label="百度指数",
        url="https://index.baidu.com/v2/main/index.html",
        kind="cookie",
        domain="baidu.com",
        cookies=("BDUSS",),
        out_re="扫码登录|登录百度账号|请登录后使用",
        ready_sel="input.search-input",
        alias="baidu",
    ),
    Target(
        provider="douyin_index",
        label="抖音指数",
        url="https://creator.douyin.com/creator-micro/creator-count/arithmetic-index/analysis",
        kind="browser",
        domain="douyin.com",
        cookies=("sessionid", "sessionid_ss", "sid_tt"),
        out_re="扫码登录|手机号登录|验证码登录|密码登录",
        ready_re="关键词搜索指数|抖音指数|数据概览",
        alias="douyin",
    ),
    Target(
        provider="xiaohongshu_notes",
        label="小红书",
        url="https://www.xiaohongshu.com/search_result?keyword=咖啡",
        kind="browser",
        domain="xiaohongshu.com",
        cookies=("web_session",),
        out_re="手机号登录|扫码登录|请先登录|登录后查看",
        out_sel="input[name=xhs-pc-web-phone], .login-container, .login-modal",
        ready_sel="section.note-item",
        alias="xiaohongshu",
    ),
)

WECHAT_PROVIDER = "wechat_index"
WECHAT_LABEL = "微信指数"
WECHAT_URL = "https://weixin.qq.com"


@dataclass
class State:
    provider: str
    label: str
    url: str
    kind: str
    logged_in: bool | None
    detail: str
    how: str = ""
    checked_at: float = field(default_factory=time.time)

    @property
    def known(self) -> bool:
        return self.logged_in is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "label": self.label,
            "url": self.url,
            "kind": self.kind,
            "logged_in": self.logged_in,
            "detail": self.detail,
            "how": self.how,
            "checked_at": self.checked_at,
        }


def state_file(settings: Settings) -> Path:
    return settings.cache_dir / "sessions.json"


def _read_states(settings: Settings) -> dict[str, dict]:
    try:
        payload = json.loads(state_file(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def remember(settings: Settings, states: list[State]) -> None:
    payload = _read_states(settings)
    for state in states:
        payload[state.provider] = state.to_dict()
    try:
        settings.ensure_cache_dir()
        state_file(settings).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass  # a cache we cannot write is not worth failing over


def recall(provider: str, settings: Settings, *, max_age: float = STATE_TTL) -> State | None:
    """A recent answer for ``provider``, or ``None`` when it is stale."""
    entry = _read_states(settings).get(provider)
    if not isinstance(entry, dict):
        return None
    age = time.time() - float(entry.get("checked_at") or 0)
    if age > max_age:
        return None
    return State(
        provider=str(entry.get("provider") or provider),
        label=str(entry.get("label") or ""),
        url=str(entry.get("url") or ""),
        kind=str(entry.get("kind") or ""),
        logged_in=entry.get("logged_in"),
        detail=str(entry.get("detail") or ""),
        how=str(entry.get("how") or ""),
        checked_at=float(entry.get("checked_at") or 0),
    )


def _probe(settings: Settings, target: Target) -> tuple[bool | None, str]:
    """Open ``target.url`` in the bridge browser and read the login state."""
    try:
        from . import opencli as bridge

        ok, reason = bridge.available()
        if not ok:
            return None, f"浏览器桥接不可用：{reason}"
        script = (
            _PROBE.replace("__OUT_RE__", _js(target.out_re))
            .replace("__READY_RE__", _js(target.ready_re))
            .replace("__OUT_SEL__", _sel(target.out_sel))
            .replace("__READY_SEL__", _sel(target.ready_sel))
        )
        bridge.open_page(settings, target.url, session=f"keypulse-login-{target.provider}")
        payload = bridge.eval_js(
            settings,
            script,
            session=f"keypulse-login-{target.provider}",
            timeout=max(45.0, settings.timeout * 3),
        )
        data = json.loads(payload) if isinstance(payload, str) else payload
    except Exception as exc:  # noqa: BLE001 - a failed probe is "unknown", not "out"
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(data, dict) or not data.get("ok"):
        reason = (data or {}).get("reason") if isinstance(data, dict) else "探测失败"
        return None, str(reason or "无法判断登录状态")
    logged_in = bool(data.get("logged_in"))
    return logged_in, "浏览器里已登录" if logged_in else "浏览器里显示登录页"


def _browser_cookie_hint(target: Target) -> tuple[bool, str]:
    """Whether the browser holds the session cookie (evidence, not proof)."""
    from . import browser_cookies

    try:
        cookies = browser_cookies.read(target.domain)
    except browser_cookies.CookieError as exc:
        return False, str(exc)
    found = browser_cookies.find(cookies, target.cookies, target.domain)
    if found:
        return True, "浏览器里有登录 Cookie（" + "、".join(found) + "）"
    return False, "浏览器里没有找到登录 Cookie"


def wechat_state(settings: Settings) -> State:
    """Freshness of the 微信指数 credential, without driving WeChat."""
    age_min: int | None = None
    try:
        from .providers.wechat_index_browser import _newest_cached

        live = _newest_cached()
    except Exception:  # noqa: BLE001
        live = None
    key = (live[1] if live else "") or (settings.wechat_search_key or "")
    if key:
        stamp = key.split("_", 1)[0][:10]
        if stamp.isdigit():
            age_min = max(0, int((time.time() - int(stamp)) / 60))
    if not key:
        return State(
            provider=WECHAT_PROVIDER,
            label=WECHAT_LABEL,
            url=WECHAT_URL,
            kind="client",
            logged_in=False,
            detail="还没有微信指数凭据；运行 keypulse wechat-token",
            how="client",
        )
    alive = age_min is not None and age_min < 30
    detail = f"search_key 已签发 {age_min} 分钟" if age_min is not None else "已有 search_key"
    if age_min is not None and age_min >= 30:
        detail += "，服务端会拒绝；运行 keypulse wechat-open 再取一次"
    return State(
        provider=WECHAT_PROVIDER,
        label=WECHAT_LABEL,
        url=WECHAT_URL,
        kind="client",
        logged_in=alive,
        detail=detail,
        how="client",
    )


def detect(settings: Settings, target: Target, *, probe: bool = True) -> State:
    """Best available answer for one target, cheapest evidence first."""
    if target.kind == "cookie":
        stored = getattr(settings, "baidu_cookie", "") or ""
        have_stored = bool(stored) and all(name.upper() in stored.upper() for name in target.cookies)
        if have_stored:
            return State(
                provider=target.provider,
                label=target.label,
                url=target.url,
                kind=target.kind,
                logged_in=True,
                detail="已保存浏览器 Cookie（命令行会带上它；百度风控仍可能拦截）",
                how="stored",
            )
        if probe:
            logged_in, detail = _probe(settings, target)
            if logged_in is not None:
                return State(
                    provider=target.provider,
                    label=target.label,
                    url=target.url,
                    kind=target.kind,
                    logged_in=logged_in,
                    detail=detail,
                    how="browser",
                )
        has_cookie, cookie_detail = _browser_cookie_hint(target)
        detail = cookie_detail if has_cookie else f"{cookie_detail}；请在浏览器登录 index.baidu.com"
        return State(
            provider=target.provider,
            label=target.label,
            url=target.url,
            kind=target.kind,
            logged_in=True if has_cookie else False,
            detail=detail,
            how="browser-cookie" if has_cookie else "",
        )

    if target.kind == "browser":
        if probe:
            logged_in, detail = _probe(settings, target)
            if logged_in is not None:
                return State(
                    provider=target.provider,
                    label=target.label,
                    url=target.url,
                    kind=target.kind,
                    logged_in=logged_in,
                    detail=detail,
                    how="browser",
                )
        # Do not claim a login the scan will not get: these sites keep a www
        # cookie long after the creator/RED session is gone.
        return State(
            provider=target.provider,
            label=target.label,
            url=target.url,
            kind=target.kind,
            logged_in=None,
            detail="未确认；运行 keypulse login 探测（浏览器桥接不可用或探测失败）",
            how="",
        )

    return wechat_state(settings)


def detect_all(settings: Settings, *, probe: bool = True) -> list[State]:
    states = [detect(settings, target, probe=probe) for target in TARGETS]
    states.append(wechat_state(settings))
    remember(settings, states)
    return states


def import_cookie(settings: Settings, target: Target) -> tuple[bool, str]:
    """Copy the browser's cookie for ``target`` into the credential store.

    Only meaningful for ``kind == "cookie"`` sources: 抖音 and 小红书 have no
    credential that survives outside the browser, so storing one would only
    imply a capability that does not exist.
    """
    if target.kind != "cookie":
        return False, f"{target.label} 的登录态只能在浏览器里使用，无法保存为凭据"
    from . import browser_cookies
    from .login import save_cookie

    try:
        cookies = browser_cookies.read(target.domain)
    except browser_cookies.CookieError as exc:
        return False, str(exc)
    if not cookies:
        return False, f"在浏览器里没有找到 {target.domain} 的 Cookie"
    found = browser_cookies.find(cookies, target.cookies, target.domain)
    missing = [name for name in target.cookies if name not in found]
    if missing:
        return False, f"浏览器 Cookie 里缺少 {'、'.join(missing)}；请先在浏览器登录 {target.url}"
    # Send exactly what the browser would send to that host: cookies scoped to
    # a parent domain (.baidu.com) or to the host itself.  Matching on the
    # site name alone would also pick up pan.baidu.com and friends, which the
    # browser never sends to index.baidu.com.
    from urllib.parse import urlparse

    host = (urlparse(target.url).hostname or "").lower()
    relevant = []
    for cookie in cookies:
        domain = cookie.domain.lstrip(".").lower()
        if not domain:
            continue
        if host == domain or host.endswith("." + domain):
            # Chrome keeps a *_BFESS twin of some cookies for its partitioned
            # cookie store; sending both is harmless but doubles the header.
            if not cookie.name.endswith("_BFESS"):
                relevant.append(cookie)
    if not relevant:
        return False, f"没有适用于 {host} 的 Cookie"
    value = browser_cookies.header(relevant)
    save_cookie(settings, value)
    settings.baidu_cookie = value
    return True, f"已从浏览器导入 {len(relevant)} 条 Cookie（{host}）"


def open_login(settings: Settings, target: Target) -> tuple[bool, str]:
    """Put the login page in front of the user, in the bridge browser."""
    try:
        from . import opencli as bridge

        ok, reason = bridge.available()
        if not ok:
            return False, f"浏览器桥接不可用：{reason}"
        bridge.open_page(settings, target.url, session=f"keypulse-login-{target.provider}")
        return True, f"已在浏览器打开 {target.url}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def targets_for(names: list[str]) -> list[Target]:
    wanted = {name.strip().lower() for name in names if name.strip()}
    if not wanted or "all" in wanted:
        return list(TARGETS)
    alias = {
        "baidu": "baidu_index",
        "百度": "baidu_index",
        "百度指数": "baidu_index",
        "douyin": "douyin_index",
        "抖音": "douyin_index",
        "抖音指数": "douyin_index",
        "xiaohongshu": "xiaohongshu_notes",
        "xhs": "xiaohongshu_notes",
        "小红书": "xiaohongshu_notes",
    }
    resolved = {alias.get(name, name) for name in wanted}
    return [target for target in TARGETS if target.provider in resolved]
