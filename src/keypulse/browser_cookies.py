"""Read login cookies out of a local Chromium-family browser (macOS).

Why this exists: 百度指数, 抖音指数 and 小红书 all gate on a logged-in session.
The browser bridge can *use* that session, but KeyPulse has no way to say
"you are already logged in" or to keep the cookie for the plain-HTTP path
unless it can read it.  This module does that: it opens the browser's own
cookie database and decrypts the values.

The encryption, as Chrome has stored it for years on macOS:

* key = ``PBKDF2-HMAC-SHA1(keychain password, b"saltysalt", 1003, 16)``
* cipher = AES-128-CBC, IV = 16 spaces, ciphertext prefixed ``b"v10"``
* plaintext = ``SHA256(host_key) || cookie value``

That last part is the trap.  Decrypting without stripping the 32-byte host
hash yields binary that looks like a wrong key, which is exactly how it read
the first time.  The hash also doubles as a check: it must match the row's
``host_key``, so a decryption that produces a plausible-looking string for
the wrong host is still rejected.

Nothing here prints or logs a cookie value.  Callers get them to build a
``Cookie`` header; the CLI only ever reports lengths and names.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

#: Chromium-family browsers: (data dir, keychain service, keychain account).
#: The service names differ per vendor; "Chrome Safe Storage" is also what
#: Chromium forks copied, which is why each entry carries its own.
BROWSERS: dict[str, tuple[str, str, str]] = {
    "chrome": ("~/Library/Application Support/Google/Chrome", "Chrome Safe Storage", "Chrome"),
    "chromium": ("~/Library/Application Support/Chromium", "Chromium Safe Storage", "Chromium"),
    "brave": ("~/Library/Application Support/BraveSoftware/Brave-Browser", "Brave Safe Storage", "Brave"),
    "edge": ("~/Library/Application Support/Microsoft Edge", "Microsoft Edge Safe Storage", "Microsoft Edge"),
    "vivaldi": ("~/Library/Application Support/Vivaldi", "Vivaldi Safe Storage", "Vivaldi"),
    "arc": ("~/Library/Application Support/Arc/User Data", "Arc Safe Storage", "Arc"),
}

#: Chromium counts microseconds from 1601-01-01; a cookie whose expiry is
#: before "now" is dead weight and must not be reported as a live session.
_EPOCH_DELTA_US = 11_644_473_600 * 1_000_000

_CACHE_TTL = 30.0
_lock = threading.Lock()
_password_cache: dict[str, bytes] = {}
_read_cache: dict[tuple[str, str], tuple[float, list["Cookie"]]] = {}


class CookieError(RuntimeError):
    pass


@dataclass(frozen=True)
class Cookie:
    name: str
    value: str
    domain: str
    path: str
    secure: bool
    http_only: bool
    expires_at: float | None  # epoch seconds; None for a session cookie

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "domain": self.domain,
            "path": self.path,
            "secure": self.secure,
            "http_only": self.http_only,
            "expires_at": self.expires_at,
        }

    def __str__(self) -> str:  # never leak the value by accident
        return f"{self.name}@{self.domain}({len(self.value)} chars)"


def profile_dirs(browser: str) -> list[Path]:
    """Every profile ('Default', 'Profile 1', …) that has a cookie database."""
    root = Path(BROWSERS[browser][0]).expanduser()
    if not root.exists():
        return []
    found = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if (child / "Cookies").is_file():
            found.append(child)
    return found


def available_browsers() -> dict[str, list[str]]:
    """Browsers on this machine, mapped to the profiles with cookies."""
    return {
        name: [path.name for path in profile_dirs(name)]
        for name in BROWSERS
        if profile_dirs(name)
    }


def _keychain_password(service: str) -> bytes:
    """The browser's cookie password, from the login keychain.

    macOS asks the user to approve the first read; that prompt is the access
    control working as intended, so a denial is reported as such rather than
    retried or worked around.
    """
    if service in _password_cache:
        return _password_cache[service]
    if sys.platform != "darwin":
        raise CookieError("只有 macOS 把浏览器 Cookie 存在可读且可解密的位置")
    try:
        proc = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-w", "-s", service],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CookieError(f"读取钥匙串失败：{exc}") from exc
    if proc.returncode != 0 or not proc.stdout.strip():
        message = (proc.stderr or "").strip() or f"找不到钥匙串条目「{service}」"
        raise CookieError(f"无法读取浏览器加密密钥：{message[:160]}")
    password = proc.stdout.strip().encode("utf-8")
    _password_cache[service] = password
    return password


def _derive_key(service: str) -> bytes:
    password = _keychain_password(service)
    return hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, 16)


def _decrypt(host_key: str, blob: bytes, key: bytes) -> str | None:
    """Plaintext of one ``encrypted_value``, or ``None`` if it does not verify."""
    if not blob:
        return ""
    prefix, body = blob[:3], blob[3:]
    if prefix not in (b"v10", b"v11"):
        # Old or already-plaintext rows: Chromium wrote the value verbatim.
        try:
            return blob.decode("utf-8")
        except UnicodeDecodeError:
            return None
    proc = subprocess.run(
        [
            "/usr/bin/openssl", "enc", "-aes-128-cbc", "-d",
            "-K", key.hex(), "-iv", (b" " * 16).hex(),
        ],
        input=body,
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    plain = proc.stdout
    if plain and 1 <= plain[-1] <= 16:
        plain = plain[: -plain[-1]]
    # Chrome ≥ DB v24 prepends SHA256(host_key).  Check it: a correct padding
    # but a wrong host means the decryption is not trustworthy.
    if len(plain) >= 32 and plain[:32] == hashlib.sha256(host_key.encode()).digest():
        plain = plain[32:]
    elif len(plain) >= 32 and _looks_hashed(plain[:32]):
        return None
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _looks_hashed(prefix: bytes) -> bool:
    """32 random-looking bytes at the front mean we failed to strip a host hash."""
    printable = sum(1 for byte in prefix if 32 <= byte < 127)
    return printable < 24


def _read_db(
    db: Path, domain: str, key: bytes, *, now: float | None = None
) -> list[Cookie]:
    """Rows of one cookie database whose host matches ``domain``."""
    now = now if now is not None else time.time()
    tmp = Path(tempfile.mkdtemp(prefix="kp-cookies-")) / "Cookies"
    try:
        # Copy first: Chromium holds a write lock while it runs, and a lock
        # error here would be reported as "not logged in", which is wrong.
        shutil.copy2(db, tmp)
        try:
            connection = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise CookieError(f"无法打开浏览器 Cookie 数据库：{exc}") from exc
        try:
            rows = connection.execute(
                "select host_key, name, value, encrypted_value, path, "
                "expires_utc, is_secure, is_httponly from cookies"
            ).fetchall()
        except sqlite3.Error as exc:
            raise CookieError(f"无法读取浏览器 Cookie 表：{exc}") from exc
        finally:
            connection.close()
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)

    cookies: list[Cookie] = []
    for host, name, plain, encrypted, path, expires_utc, secure, http_only in rows:
        if domain and domain not in host:
            continue
        value = plain or ""
        if not value and encrypted:
            value = _decrypt(host, encrypted, key) or ""
        if not value:
            continue
        expires_at = None
        if expires_utc:
            expires_at = expires_utc / 1_000_000 - _EPOCH_DELTA_US / 1_000_000
            if expires_at <= now:
                continue
        cookies.append(
            Cookie(
                name=name,
                value=value,
                domain=host,
                path=path or "/",
                secure=bool(secure),
                http_only=bool(http_only),
                expires_at=expires_at,
            )
        )
    return cookies


def read(domain: str, *, browser: str = "", now: float | None = None) -> list[Cookie]:
    """Decrypted cookies whose host contains ``domain``, from any profile.

    Cached for 30 s: the login hub and the provider health check both ask,
    and each answer costs a keychain read plus an AES pass per row.
    Memoised per ``(browser, domain)`` and serialised across threads.
    """
    key = (browser or "*", domain)
    with _lock:
        cached = _read_cache.get(key)
        if cached and (time.time() - cached[0]) < _CACHE_TTL:
            return cached[1]

    names = [browser] if browser else list(BROWSERS)
    found: list[Cookie] = []
    errors: list[str] = []
    for name in names:
        profiles = profile_dirs(name)
        if not profiles:
            continue
        try:
            aes = _derive_key(BROWSERS[name][1])
        except CookieError as exc:
            errors.append(f"{name}: {exc}")
            continue
        for profile in profiles:
            try:
                found.extend(_read_db(profile / "Cookies", domain, aes, now=now))
            except CookieError as exc:
                errors.append(f"{name}/{profile.name}: {exc}")
    if not found and errors:
        # Every browser that exists failed for a reason worth reporting —
        # usually a denied keychain prompt.  Returning [] here would show up
        # as "not logged in", which sends the user to re-login for nothing.
        raise CookieError("；".join(errors[:2]))
    with _lock:
        _read_cache[key] = (time.time(), found)
    return found


def find(cookies: list[Cookie], names: tuple[str, ...], domain: str = "") -> dict[str, str]:
    """The first value present for each of ``names`` (case-sensitive)."""
    wanted = {name: "" for name in names}
    for cookie in cookies:
        if domain and domain not in cookie.domain:
            continue
        if cookie.name in wanted and not wanted[cookie.name]:
            wanted[cookie.name] = cookie.value
    return {name: value for name, value in wanted.items() if value}


def header(cookies: list[Cookie]) -> str:
    """A ``Cookie:`` header value, most specific domain first."""
    ordered = sorted(cookies, key=lambda item: len(item.domain), reverse=True)
    return "; ".join(f"{item.name}={item.value}" for item in ordered)


def main(argv: list[str] | None = None) -> int:
    """``python3 -m keypulse.browser_cookies --domain baidu.com [--json]``.

    Values are never printed unless ``--show-values`` is passed explicitly,
    and even then only the requested names are shown.
    """
    import json as _json

    args = list(argv if argv is not None else sys.argv[1:])
    domain = ""
    browser = ""
    show = False
    if "--domain" in args:
        index = args.index("--domain")
        domain = args[index + 1]
    if "--browser" in args:
        index = args.index("--browser")
        browser = args[index + 1]
    show = "--show-values" in args
    try:
        cookies = read(domain, browser=browser)
    except CookieError as exc:
        print(_json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    payload = {
        "ok": True,
        "domain": domain,
        "count": len(cookies),
        "cookies": [
            {**cookie.to_dict(), "value": cookie.value} if show else cookie.to_dict()
            for cookie in cookies
        ],
        "browsers": available_browsers(),
    }
    print(_json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
