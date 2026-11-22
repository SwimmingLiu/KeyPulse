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


def _cache_files(dirs: list[Path]) -> list[Path]:
    """Every regular cache file, in no particular order."""
    files: list[Path] = []
    for directory in dirs:
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_file() and entry.stat().st_size <= 2_000_000:
                    files.append(entry)
            except OSError:
                continue
    return files


def _match(entry: Path) -> dict | None:
    """The login carried by one cache file, if it carries one."""
    try:
        blob = entry.read_bytes()
        mtime = entry.stat().st_mtime
    except OSError:
        return None
    # Only the index mini-program's login; other applets run their own
    # weapplogin against the same host with a different appid, so an entry
    # must carry the request *and* the answer.
    if LOGIN_PATH not in blob:
        return None
    match = SESSION_RE.search(blob)
    if not match:
        return None
    return {
        "openid": match.group(1).decode(),
        "search_key": match.group(2).decode(),
        "cache_file": str(entry),
        "mtime": mtime,
    }


def scan(dirs: list[Path]) -> list[dict]:
    """All (mtime, openid, search_key, path) found, newest first."""
    hits = [hit for hit in (_match(entry) for entry in _cache_files(dirs)) if hit]
    hits.sort(key=lambda item: item["mtime"], reverse=True)
    return hits


def scan_newest(dirs: list[Path], max_files: int = 0) -> dict | None:
    """The newest login, reading as few files as possible.

    WeChat keeps ~23k files in these caches and reading all of them costs
    about 8 seconds.  That is fine once, and ruinous when four parallel
    keyword workers each do it: every call blew through its timeout and the
    scan fell back to a stale saved key.  Sorting by mtime is a stat-only
    pass (~0.06 s), and because the list is newest-first the *first* login
    encountered is by definition the newest one, so the scan can stop there.

    ``max_files`` bounds the reads; ``0`` means "no bound".  With a bound and
    no hit inside it, the caller gets ``None`` rather than a slow answer.
    """
    files = _cache_files(dirs)
    try:
        files.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    except OSError:
        pass
    for index, entry in enumerate(files):
        if max_files and index >= max_files:
            return None
        hit = _match(entry)
        if hit:
            return hit
    return None


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
    parser.add_argument(
        "--fast",
        action="store_true",
        help="newest login only, stopping at the first hit (for the provider's hot path)",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=400,
        help="with --fast: how many of the newest cache files to read (default 400)",
    )
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

    # ``--fast`` exists for callers on a hot path (the provider asks before
    # every scan).  It bounds the reads instead of sweeping the whole cache;
    # the full sweep is kept for ``keypulse wechat-token``, where reporting
    # how many logins exist is part of the answer.
    if args.fast:
        newest = scan_newest(dirs, max_files=args.max_files)
        hits = [newest] if newest else []
    else:
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
