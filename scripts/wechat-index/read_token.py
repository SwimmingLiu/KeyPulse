#!/usr/bin/env python3
"""Read 微信指数 credentials out of the WeChat client's own webview cache.

Why this works: the 「微信指数」 mini-program logs in with ``weapplogin``
(``POST /cgi-bin/searchweb/weapplogin``), and the macOS client keeps the
response in its embedded-browser HTTP cache on disk::

    {"data": {"openid": "…", "search_key": "…"}, "errcode": 0}

``search_key`` is short-lived, so the newest cached entry is the one to use.
Reading the cache costs nothing and needs no proxy on a phone.

The cache is written by WeChat.app inside its sandbox container.  macOS grants
another process read access to it without any special permission, so this
needs neither Accessibility nor Screen Recording.

Exit codes: 0 found, 1 nothing found (prints where it looked).
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
import time
from pathlib import Path

#: ``weapplogin`` answers with this object; match it without parsing the
#: surrounding cache-entry binary framing.
SESSION_RE = re.compile(rb'\{"openid":"([^"]+)","search_key":"([^"]+)"\}')
#: Login URLs look like ``…/searchweb/weapplogin?appid=…&js_code=…``.  The
#: path identifies the exchange, so the appid never has to be written down
#: here — this repository is public, a WeChat appid trips GitHub's secret
#: scanner, and the request does not need it anyway.  Other mini-programs do
#: call the same path, so an entry must carry the request *and* the
#: ``{openid, search_key}`` answer to count as this one.
LOGIN_PATH = b"/cgi-bin/searchweb/weapplogin"
DEFAULT_CONTAINER = (
    Path.home()
    / "Library/Containers/com.tencent.xinWeChat/Data/Documents/app_data/radium/web/profiles"
)


def profile_dirs(root: Path | None = None) -> list[Path]:
    """Every webview cache directory under the WeChat container."""
    root = root or DEFAULT_CONTAINER
    if not root.exists():
        return []
    found = []
    for candidate in root.glob("*/Cache/Cache_Data"):
        if candidate.is_dir():
            found.append(candidate)
    return found


def scan(dirs: list[Path]) -> list[dict]:
    """All (mtime, openid, search_key, path) found, newest first."""
    hits: list[dict] = []
    for directory in dirs:
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_file() or entry.stat().st_size > 2_000_000:
                    continue
                blob = entry.read_bytes()
            except OSError:
                continue
            # Only the index mini-program's login; other applets run their own
            # weapplogin against the same host with a different appid.
            if LOGIN_PATH not in blob:
                continue
            match = SESSION_RE.search(blob)
            if not match:
                continue
            hits.append(
                {
                    "openid": match.group(1).decode(),
                    "search_key": match.group(2).decode(),
                    "cache_file": str(entry),
                    "mtime": entry.stat().st_mtime,
                }
            )
    hits.sort(key=lambda item: item["mtime"], reverse=True)
    return hits


def key_age_seconds(search_key: str, now: float | None = None) -> float | None:
    """Age implied by the timestamp encoded in ``search_key``.

    Keys are ``<epoch-seconds><sequence>_<digits>``: the first ten digits of
    the part before ``_`` are an epoch-seconds value, and the rest are a
    sequence number (the observed keys all agree to within a second of the
    cache file's mtime).  The server stops accepting a key long before this
    gets old, but the age tells a caller whether refreshing is worth trying
    before a request is even made.
    """
    prefix = search_key.split("_", 1)[0]
    stamp = prefix[:10]
    if not stamp.isdigit():
        return None
    now = now if now is not None else time.time()
    return now - int(stamp)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument("--container", default="", help="override the WeChat container path")
    args = parser.parse_args(argv)

    if platform.system() != "Darwin":
        print(
            json.dumps(
                {"ok": False, "error": "only macOS keeps this cache in a readable location"},
                ensure_ascii=False,
            )
        )
        return 1

    root = Path(args.container).expanduser() if args.container else None
    dirs = profile_dirs(root)
    if not dirs:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "找不到微信的 webview 缓存目录",
                    "looked_in": str(root or DEFAULT_CONTAINER),
                },
                ensure_ascii=False,
            )
        )
        return 1

    hits = scan(dirs)
    if not hits:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "缓存里没有 weapplogin 记录（先在微信里打开一次「微信指数」）",
                    "searched": [str(d) for d in dirs],
                },
                ensure_ascii=False,
            )
        )
        return 1

    newest = hits[0]
    age = key_age_seconds(newest["search_key"])
    payload = {
        "ok": True,
        "openid": newest["openid"],
        "search_key": newest["search_key"],
        "key_age_seconds": round(age) if age is not None else None,
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(newest["mtime"])),
        "alternatives": len(hits),
        "cache_file": newest["cache_file"],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
