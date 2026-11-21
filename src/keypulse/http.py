"""HTTP fetch layer: disk cache, per-host throttling, retries, browser-ish headers.

Providers must go through :func:`fetch` so that a scan never hammers a host
and repeated runs are near-instant.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .config import Settings

#: A plain desktop Chrome UA.  Deliberately the short form: Sogou's WeChat
#: vertical serves its anti-spider page to the verbose "… (KHTML, like Gecko)
#: Chrome/124.0.0.0 …" variant while accepting this one.
CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Real browsers send these on top-level navigations; several Chinese SERPs
# return a challenge page without them.
BROWSER_HEADERS: Mapping[str, str] = {
    "User-Agent": CHROME_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Upgrade-Insecure-Requests": "1",
}

JSON_HEADERS: Mapping[str, str] = {
    "User-Agent": CHROME_UA,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
}


class FetchError(RuntimeError):
    """Upstream refused or failed the request in a way retrying will not fix."""

    def __init__(self, message: str, *, status: int | None = None, url: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.url = url


#: Providers that answer in GBK/GB18030 regardless of the declared charset.
_FORCE_GBK_HOSTS = {"sor.html5.qq.com", "suggestion.baidu.com"}


@dataclass
class Response:
    text: str
    status: int
    url: str
    from_cache: bool = False

    def json(self):
        return json.loads(self.text)


class _HostThrottle:
    """Serialises requests per host, spaces them out, and remembers failures.

    Two extra jobs beyond pacing:

    * **Negative cache** — a host that just failed is not retried again within
      the same run.  A scan asks one provider about N keywords; without this a
      blocked host costs N timeouts.
    * **Circuit breaker** — after ``breaker_threshold`` consecutive failures a
      host is skipped entirely, so the scan finishes fast and the report shows
      one clear reason instead of dozens of errors.
    """

    def __init__(self, interval: float, breaker_threshold: int = 2) -> None:
        self.interval = interval
        self.breaker_threshold = breaker_threshold
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._dead: dict[str, str] = {}
        self._guard = threading.Lock()

    def _lock_for(self, host: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(host, threading.Lock())

    def dead_reason(self, host: str) -> str | None:
        with self._guard:
            return self._dead.get(host)

    def record_failure(self, host: str, reason: str) -> None:
        with self._guard:
            count = self._failures.get(host, 0) + 1
            self._failures[host] = count
            if count >= self.breaker_threshold:
                self._dead[host] = reason

    def record_success(self, host: str) -> None:
        with self._guard:
            self._failures.pop(host, None)

    def wait(self, host: str) -> None:
        with self._lock_for(host):
            delay = self._last.get(host, 0.0) + self.interval - time.monotonic()
            if delay > 0:
                # Jitter keeps parallel workers from re-synchronising.
                time.sleep(delay + random.uniform(0, self.interval * 0.3))
            self._last[host] = time.monotonic()


class _Sessions:
    """Per-host cookie jars.

    Several Chinese sites (Sogou's WeChat vertical in particular) hand out a
    short-lived cookie on the homepage and reject deep links without it.  A jar
    per host plus a one-time warm-up request is enough to be treated as a
    returning visitor.
    """

    def __init__(self) -> None:
        self._jars: dict[str, http.cookiejar.CookieJar] = {}
        self._warmed: set[str] = set()
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, host: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(host, threading.Lock())

    def open(
        self,
        host: str,
        url: str,
        headers: Mapping[str, str],
        timeout: float,
        warmup: str | None,
        opener_factory=None,
    ) -> str:
        with self._lock(host):
            jar = self._jars.setdefault(host, http.cookiejar.CookieJar())
            if opener_factory is not None:
                opener = opener_factory(jar)
            else:
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            if warmup and host not in self._warmed:
                self._warmed.add(host)
                try:
                    opener.open(urllib.request.Request(warmup, headers=dict(headers)), timeout=timeout).read()
                except Exception:  # noqa: BLE001 - warm-up is best effort
                    pass
            with opener.open(urllib.request.Request(url, headers=dict(headers)), timeout=timeout) as resp:
                return resp.read().decode(_charset_for(host, resp), "replace")


_sessions = _Sessions()
_throttle: _HostThrottle | None = None


def _opener(settings: Settings, jar: http.cookiejar.CookieJar | None = None):
    """urllib opener honouring ``KEYPULSE_PROXY``.

    Built per call rather than cached: proxy settings can differ between a
    probe and a scan in the same process, and constructing it is cheap.
    """
    handlers = []
    if jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    if settings.proxy:
        handlers.append(
            urllib.request.ProxyHandler({"http": settings.proxy, "https": settings.proxy})
        )
    return urllib.request.build_opener(*handlers)

#: Hosts that need a homepage visit first to receive their session cookie.
_WARMUP_URLS: Mapping[str, str] = {
    "weixin.sogou.com": "https://weixin.sogou.com/",
}


def _charset_for(host: str, resp) -> str:
    if host in _FORCE_GBK_HOSTS:
        return "gb18030"
    charset = resp.headers.get_content_charset()
    return charset or "utf-8"


def _get_throttle(settings: Settings) -> _HostThrottle:
    global _throttle
    if _throttle is None:
        _throttle = _HostThrottle(settings.host_interval)
    return _throttle


def reset_runtime_state() -> None:
    """Forget throttles, circuit breakers and session cookies between runs."""
    global _throttle, _sessions
    _throttle = None
    _sessions = _Sessions()


def cache_key(url: str, headers: Mapping[str, str] | None, body: str | None) -> str:
    digest = hashlib.sha256()
    digest.update(url.encode("utf-8"))
    if headers:
        for key in sorted(headers):
            if key.lower() in {"cookie", "authorization"}:
                continue  # never key a cache entry on credentials
            digest.update(f"\x00{key}:{headers[key]}".encode("utf-8"))
    if body:
        digest.update(b"\x00" + body.encode("utf-8"))
    return digest.hexdigest()[:32]


def fetch(
    url: str,
    settings: Settings,
    *,
    headers: Mapping[str, str] | None = None,
    data: Mapping[str, str] | bytes | None = None,
    use_cache: bool = True,
    cache_ttl: int | None = None,
) -> Response:
    """GET/POST ``url`` with caching and retries.

    Raises :class:`FetchError` when the upstream is unreachable or blocked.
    """
    merged = dict(BROWSER_HEADERS)
    if headers:
        merged.update(headers)

    body: bytes | None = None
    if isinstance(data, Mapping):
        body = urllib.parse.urlencode(data).encode("utf-8")
        merged.setdefault("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    elif isinstance(data, bytes):
        body = data

    ttl = settings.cache_ttl if cache_ttl is None else cache_ttl
    key = cache_key(url, merged, body.decode("utf-8", "ignore") if body else None)
    cache_path: Path | None = None
    if use_cache and (settings.offline or ttl > 0):
        cache_path = settings.ensure_cache_dir() / f"{key}.json"
        # Offline mode accepts any age: the point is to use what was fetched
        # before, not to enforce freshness.
        cached = _read_cache(cache_path, math.inf if settings.offline else ttl)
        if cached is not None:
            return cached
    if settings.offline:
        raise FetchError("offline mode: no cached response for this request", url=url)

    host = urllib.parse.urlsplit(url).netloc
    throttle = _get_throttle(settings)
    dead = throttle.dead_reason(host)
    if dead:
        raise FetchError(f"{host} skipped after repeated failures in this run ({dead})", url=url)

    last_error: Exception | None = None
    for attempt in range(settings.retries + 1):
        throttle.wait(host)
        try:
            text = _request(url, merged, body, settings)
        except urllib.error.HTTPError as exc:
            last_error = exc
            # 429/5xx are worth a retry with backoff; 4xx others are terminal.
            if exc.code in (429, 500, 502, 503, 504) and attempt < settings.retries:
                time.sleep(1.5 * (attempt + 1) + random.random())
                continue
            reason = f"HTTP {exc.code}"
            throttle.record_failure(host, reason)
            raise FetchError(f"{reason} from {host}", status=exc.code, url=url) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < settings.retries:
                time.sleep(1.0 * (attempt + 1) + random.random())
                continue
            throttle.record_failure(host, _short_error(exc))
            raise FetchError(f"network error talking to {host}: {exc}", url=url) from exc

        throttle.record_success(host)
        response = Response(text=text, status=200, url=url)
        if cache_path is not None and not is_block_page(text):
            # Never persist a challenge/anti-spider page: caching it would turn
            # a transient block into six hours of confidently wrong data.
            _write_cache(cache_path, response)
        return response

    raise FetchError(f"request to {host} failed: {last_error}", url=url)


def _short_error(exc: BaseException) -> str:
    """Trim provider-facing error text to the part that identifies the cause."""
    text = str(exc)
    if "SSLEOFError" in text or "UNEXPECTED_EOF" in text:
        return "TLS connection dropped (proxy/network flake)"
    if "timed out" in text:
        return "timeout"
    if "Name or service not known" in text:
        return "DNS failure"
    return type(exc).__name__


def _request(url: str, headers: Mapping[str, str], body: bytes | None, settings: Settings) -> str:
    host = urllib.parse.urlsplit(url).netloc
    if body is None and host in _WARMUP_URLS:
        return _sessions.open(
            host,
            url,
            headers,
            settings.timeout,
            _WARMUP_URLS[host],
            opener_factory=lambda jar: _opener(settings, jar),
        )
    request = urllib.request.Request(url, data=body, headers=dict(headers))
    with _opener(settings).open(request, timeout=settings.timeout) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset()
    if host in _FORCE_GBK_HOSTS:
        charset = "gb18030"
    if not charset:
        charset = _guess_charset(raw)
    try:
        return raw.decode(charset, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def _guess_charset(raw: bytes) -> str:
    head = raw[:2048].lower()
    if b"charset=gbk" in head or b"charset=gb2312" in head or b"charset=gb18030" in head:
        return "gb18030"
    return "utf-8"


def _read_cache(path: Path, ttl: int) -> Response | None:
    try:
        age = time.time() - path.stat().st_mtime
        if age > ttl:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return Response(text=payload["text"], status=payload["status"], url=payload["url"], from_cache=True)


def _write_cache(path: Path, response: Response) -> None:
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps({"text": response.text, "status": response.status, "url": response.url}),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)


#: Markers that a 200 response is really an anti-bot challenge page.
_BLOCK_MARKERS = (
    "antispider",
    "请输入验证码",
    "百度安全验证",
    "检测到存在异常访问行为",
    "unusual traffic",
    "are you a robot",
)


def is_block_page(text: str) -> bool:
    """True when the body looks like a captcha/anti-bot interstitial."""
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _BLOCK_MARKERS)


def strip_jsonp(text: str) -> str:
    """Return the JSON payload of a JSONP/``)]}',`` prefixed response."""
    text = text.lstrip()
    if text.startswith(")]}'"):
        newline = text.find("\n")
        text = text[newline + 1 :] if newline != -1 else ""
    if not text.startswith(("{", "[")):
        start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
        end = max(text.rfind("}"), text.rfind("]"))
        if start != -1 and end > start:
            text = text[start : end + 1]
    return text
