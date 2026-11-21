"""``keypulse doctor`` — one read-only check that this install can actually work.

Answers "is this instance worth driving?" before any scan: the binary resolves
its package, the config chain is intact, the cache directory is writable, and
the optional browser bridge is either usable or cleanly absent.

Exit code is the contract: 0 when the core chain is healthy, 1 otherwise.
Optional transports (browser bridge, Baidu cookie) never fail the check — they
degrade the data, not correctness, and are reported as notes.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from . import __version__
from .config import Settings


def _line(ok: bool, label: str, detail: str) -> dict:
    return {"ok": ok, "label": label, "detail": detail}


def run_checks(settings: Settings) -> list[dict]:
    checks: list[dict] = []

    checks.append(_line(True, "version", __version__))

    # The package must import from the tree the binary belongs to, not from a
    # stray site-packages copy that would hide local edits.
    package = importlib.import_module("keypulse")
    package_path = Path(getattr(package, "__file__", "")).resolve().parent
    checks.append(_line(package_path.exists(), "package", str(package_path)))

    # Every provider module must import; a syntax error in one would otherwise
    # only surface as a missing source mid-scan.
    try:
        from .providers import build_registry

        registry = build_registry(settings)
        checks.append(_line(True, "providers", f"{len(registry)} registered"))
    except Exception as exc:  # noqa: BLE001 - the point is to report it
        checks.append(_line(False, "providers", f"{type(exc).__name__}: {exc}"))
        return checks

    # A scan writes into the cache; an unwritable directory means every scan
    # fails at the first response instead of here.
    directory = settings.cache_dir
    try:
        settings.ensure_cache_dir()
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".doctor-", delete=True):
            pass
        checks.append(_line(True, "cache_dir", str(directory)))
    except OSError as exc:
        checks.append(_line(False, "cache_dir", f"{directory} 不可写: {exc}"))

    checks.append(
        _line(True, "mode", "offline (replay only)" if settings.offline else "live")
    )

    if settings.offline:
        replay = sorted(settings.cache_dir.glob("replay-*.json")) if settings.cache_dir.exists() else []
        checks.append(
            _line(bool(replay), "replay", f"{len(replay)} 条录制数据" if replay else "无 replay 数据，离线扫描将没有结果")
        )

    # Optional transports: reported, never fatal.
    cookie = bool(settings.baidu_cookie)
    checks.append(_line(True, "baidu_cookie", "已配置" if cookie else "未配置（可选）"))

    if os.environ.get("KEYPULSE_BROWSER", "on").lower() not in {"0", "off", "false", "no"}:
        try:
            from .opencli import available

            ok, reason = available()
        except Exception as exc:  # noqa: BLE001
            ok, reason = False, f"{type(exc).__name__}: {exc}"
        checks.append(_line(ok, "browser_bridge", reason if ok else f"不可用：{reason}"))
    else:
        checks.append(_line(True, "browser_bridge", "已禁用 (KEYPULSE_BROWSER=off)"))

    checks.append(_line(sys.version_info >= (3, 10), "python", sys.version.split()[0]))
    return checks


def cmd_doctor(args) -> int:
    settings = Settings()
    if getattr(args, "no_cache", False):
        settings.cache_ttl = 0
    if getattr(args, "offline", False):
        settings.offline = True

    checks = run_checks(settings)
    # Fatal = the core chain; optional transports are informational.
    fatal = [c for c in checks if not c["ok"] and c["label"] in {"package", "providers", "cache_dir", "python"}]

    if args.format == "json":
        print(json.dumps({"ok": not fatal, "checks": checks}, ensure_ascii=False, indent=2))
        return 0 if not fatal else 1

    width = max(len(c["label"]) for c in checks)
    for check in checks:
        optional = check["label"] in {"baidu_cookie", "browser_bridge", "replay"}
        mark = "✓" if check["ok"] else ("·" if optional else "✗")
        print(f"{mark} {check['label'].ljust(width)}  {check['detail']}")

    if fatal:
        print(f"doctor: FAILED ({', '.join(c['label'] for c in fatal)})")
        return 1
    print("doctor: ok")
    return 0
