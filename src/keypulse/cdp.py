"""Drive a local Chromium-family browser over the DevTools protocol.

Why this exists: 百度指数 answers ``status 10018`` (异常访问) to its JSON API
even when the request carries a valid cookie and even when it is issued from
inside the page.  The only path that works is the rendered UI — the page's own
XHR passes Baidu's checks, a copied one does not.  So something has to drive a
real browser.

``opencli`` does that when it is installed, but it needs its extension
connected, which is a dependency KeyPulse should not *require* for 百度指数.
This module is the dependency-free route: launch the browser that is already
on the machine with ``--remote-debugging-port``, inject the cookies KeyPulse
already knows how to decrypt, and evaluate the same page-driving script.

The protocol is plain JSON over a WebSocket, so the client below is a small
RFC 6455 implementation on top of ``socket`` — the project has no third-party
dependencies and this keeps it that way.

Nothing here prints a cookie value; cookies go in as CDP parameters only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

#: Where a browser might be, most preferred first.  The same list the cookie
#: reader uses, so a machine with only Edge still works end to end.
CANDIDATES: tuple[tuple[str, str], ...] = (
    ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ("chromium", "/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ("brave", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
    ("edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ("vivaldi", "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi"),
)

_WHICH = {
    "chrome": ("google-chrome", "google-chrome-stable", "chrome"),
    "chromium": ("chromium", "chromium-browser"),
    "brave": ("brave-browser", "brave"),
    "edge": ("microsoft-edge", "msedge"),
    "vivaldi": ("vivaldi", "vivaldi-stable"),
}


class CDPError(RuntimeError):
    pass


def binary() -> tuple[str, Path] | None:
    """``(family, path)`` of the first usable browser, or ``None``."""
    for family, path in CANDIDATES:
        candidate = Path(path)
        if candidate.exists():
            return family, candidate
    for family, names in _WHICH.items():
        for name in names:
            found = shutil.which(name)
            if found:
                return family, Path(found)
    return None


def available() -> tuple[bool, str]:
    found = binary()
    if not found:
        return False, "没有找到本机 Chrome/Chromium（CDP 通道不可用）"
    return True, f"ok ({found[0]})"


def _user_agent(browser: Path) -> str:
    """A normal UA for this browser, not the ``HeadlessChrome`` one.

    Headless Chrome advertises itself in the UA string, and 百度指数 treats
    that as a bot.  Deriving the real version keeps the header honest and the
    page rendering.
    """
    version = "126.0.0.0"
    try:
        proc = subprocess.run([str(browser), "--version"], capture_output=True, text=True, timeout=15)
        for token in (proc.stdout or "").replace("/", " ").split():
            if token[:1].isdigit() and "." in token:
                version = token
                break
    except (OSError, subprocess.SubprocessError):
        pass
    major = version.split(".")[0]
    if "Edge" in str(browser):
        return (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36 Edg/{major}.0.0.0"
        )
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )


class WebSocket:
    """The minimum RFC 6455 client CDP needs: text frames, ping, close."""

    def __init__(self, url: str, timeout: float = 60.0) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "ws":
            raise CDPError(f"只支持 ws:// 调试端点，收到 {parsed.scheme}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        target = parsed.path + (("?" + parsed.query) if parsed.query else "")
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("调试端点关闭了连接")
            buffer += chunk
        head, _, rest = buffer.partition(b"\r\n\r\n")
        status = head.split(b"\r\n")[0]
        if b"101" not in status:
            raise CDPError(f"WebSocket 握手失败：{status[:80].decode('latin-1')}")
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        )
        if expected not in head:
            raise CDPError("WebSocket 握手校验失败")
        self._buffer = rest

    def _read(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self.sock.recv(max(4096, count - len(self._buffer)))
            if not chunk:
                raise CDPError("调试端点已关闭")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _send(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        mask = os.urandom(4)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        header += mask
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def send_text(self, text: str) -> None:
        self._send(0x1, text.encode("utf-8"))

    def receive(self) -> str:
        while True:
            first, second = self._read(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if second & 0x80 else None
            payload = self._read(length)
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x8:
                raise CDPError("调试端点主动关闭")
            if opcode == 0x9:
                self._send(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                return payload.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self._send(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class Session:
    """One browser, one page target, driven over CDP."""

    def __init__(self, *, profile: Path, headless: bool = True, timeout: float = 60.0) -> None:
        found = binary()
        if not found:
            raise CDPError("没有找到本机 Chrome/Chromium")
        self.family, self.binary_path = found
        self.profile = profile
        self.headless = headless
        self.timeout = timeout
        self.process: subprocess.Popen | None = None
        self.port = 0
        self.ws: WebSocket | None = None
        self.target_id = ""
        self._next_id = 0
        self._owns_browser = False

    # -- lifecycle --------------------------------------------------------
    def start(self) -> "Session":
        self.profile.mkdir(parents=True, exist_ok=True)
        if self._reuse_running():
            try:
                self._connect()
                return self
            except CDPError:
                # The port file outlived its browser.  Fall through to a fresh
                # launch rather than failing the scan on a stale artifact.
                self.ws = None
        self._launch()
        self._connect()
        return self

    def _reuse_running(self) -> bool:
        """A previous scan may have left a browser on this profile running."""
        port_file = self.profile / "DevToolsActivePort"
        try:
            port = int(port_file.read_text().splitlines()[0])
        except (OSError, ValueError, IndexError):
            return False
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            opener.open(f"http://127.0.0.1:{port}/json/version", timeout=2).read()
        except OSError:
            return False
        self.port = port
        return True

    def _launch(self) -> None:
        # The port file from a previous run is still there and still says a
        # port; reading it before the new browser writes its own would connect
        # to nothing.  Delete it, then wait for it to reappear.
        try:
            (self.profile / "DevToolsActivePort").unlink()
        except OSError:
            pass
        args = [
            str(self.binary_path),
            "--remote-debugging-port=0",
            f"--user-data-dir={self.profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--window-size=1280,900",
            f"--user-agent={_user_agent(self.binary_path)}",
            "about:blank",
        ]
        if self.headless:
            args.insert(1, "--headless=new")
        self.process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        self._owns_browser = True
        port_file = self.profile / "DevToolsActivePort"
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                self.port = int(port_file.read_text().splitlines()[0])
                # The file appears before the endpoint is listening; confirm
                # it answers, or the next call fails with ECONNREFUSED.
                self._http("/json/version")
                return
            except (OSError, ValueError, IndexError, CDPError):
                pass
            except Exception:  # noqa: BLE001 - any probe failure means "not yet"
                pass
            if self.process.poll() is not None:
                raise CDPError("浏览器启动后立即退出（可能缺少 --headless 支持）")
            time.sleep(0.2)
        raise CDPError("等待浏览器调试端口超时")

    def _http(self, path: str, method: str = "GET") -> Any:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", method=method
        )
        # Bypass any *_proxy setting: this is a loopback debug endpoint, and
        # routing it through a proxy turns a working browser into a 502.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
        return json.loads(body) if body.strip().startswith(("{", "[")) else body

    def _connect(self) -> None:
        targets = self._http("/json/list")
        page = next(
            (item for item in targets if item.get("type") == "page"),
            None,
        )
        if page is None:
            page = self._http("/json/new?about:blank", method="PUT")
        url = page.get("webSocketDebuggerUrl")
        if not url:
            raise CDPError("调试端点没有给出 page 的 WebSocket 地址")
        self.target_id = str(page.get("id") or "")
        self.ws = WebSocket(url, timeout=self.timeout)
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Network.enable")

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
            self.ws = None
        if not self._owns_browser or self.process is None:
            return
        # Ask the browser to shut down over CDP first: that is the only way to
        # take the renderer/GPU children with it.  SIGTERM on the parent alone
        # leaves orphaned helpers holding the profile lock, and the next scan
        # then launches a second browser on top of a half-dead one.
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            opener.open(
                f"http://127.0.0.1:{self.port}/json/close/{self.target_id}", timeout=3
            ).read()
        except OSError:
            pass
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process = None
        self._reap_children()

    def _reap_children(self) -> None:
        """Kill helpers still holding this profile.

        Chrome forks zygotes and renderers that outlive a terminated parent
        when it is killed rather than asked to quit.  They keep the
        ``--user-data-dir`` locked, so a later run either fails to start or
        silently reuses a zombie.  Matching on the profile path is safe: it is
        a directory unique to KeyPulse.
        """
        marker = str(self.profile)
        try:
            proc = subprocess.run(
                ["/usr/bin/pgrep", "-f", marker], capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            return
        for token in (proc.stdout or "").split():
            if not token.isdigit():
                continue
            try:
                os.kill(int(token), 15)
            except OSError:
                pass

    def __enter__(self) -> "Session":
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- protocol ---------------------------------------------------------
    def call(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        if self.ws is None:
            raise CDPError("会话尚未连接")
        self._next_id += 1
        message_id = self._next_id
        self.ws.send_text(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            raw = self.ws.receive()
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            if payload.get("id") != message_id:
                continue  # an event, not our answer
            if "error" in payload:
                raise CDPError(str(payload["error"].get("message") or payload["error"])[:200])
            return payload.get("result") or {}
        raise CDPError(f"{method} 超时（{timeout or self.timeout:.0f}s）")

    def set_cookies(self, cookies: list[dict]) -> int:
        """Install cookies into the browser's jar.

        ``cookies`` are ``{name, value, domain, path, secure, httpOnly}``.
        Returns how many were accepted; a rejection here means the page will
        render its login wall, which the caller reports rather than retries.
        """
        if not cookies:
            return 0
        self.call("Network.setCookies", {"cookies": cookies}, timeout=30)
        return len(cookies)

    def navigate(self, url: str) -> None:
        self.call("Page.navigate", {"url": url}, timeout=45)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                state = self.evaluate("document.readyState", await_promise=False)
            except CDPError:
                time.sleep(0.3)
                continue
            if state == "complete":
                return
            time.sleep(0.3)

    def evaluate(self, expression: str, *, await_promise: bool = True, timeout: float = 90.0) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            text = (detail.get("exception") or {}).get("description") or detail.get("text")
            raise CDPError(str(text)[:200])
        return (result.get("result") or {}).get("value")


def session(profile: Path | None = None, *, headless: bool = True, timeout: float = 60.0) -> Session:
    """A started session on ``profile`` (default: the KeyPulse cache)."""
    if profile is None:
        base = Path(os.environ.get("KEYPULSE_CACHE_DIR") or (Path.home() / ".cache" / "keypulse"))
        profile = base / "chrome-cdp"
    return Session(profile=profile, headless=headless, timeout=timeout).start()


def cookie_params(cookies) -> list[dict]:
    """``browser_cookies.Cookie`` records → CDP ``Network.setCookies`` params."""
    out = []
    for cookie in cookies:
        item = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path or "/",
            "secure": bool(cookie.secure),
            "httpOnly": bool(cookie.http_only),
        }
        if cookie.expires_at:
            item["expires"] = float(cookie.expires_at)
        out.append(item)
    return out
