"""轻量 Chrome DevTools Protocol 客户端（不依赖 chromedriver）。

用于「浏览器混合答题」：连接本机 Chrome/Edge 的调试端口，
直接读 DOM、写入输入框、点击选项和下一题。
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener


def _http_json(url: str, timeout: float = 3.0):
    """只用标准库访问本机 CDP HTTP 接口，避免 requests 依赖。"""
    request = Request(url, headers={"User-Agent": "ATanswer"})
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class _MiniWebSocket:
    """给 Chrome CDP 用的最小 WebSocket 客户端（ws://，无 TLS）。"""

    def __init__(self, url: str, timeout: float = 12.0) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "ws" or not parsed.hostname:
            raise RuntimeError(f"不支持的 CDP WebSocket 地址：{url}")
        self._sock = socket.create_connection(
            (parsed.hostname, parsed.port or 80), timeout=timeout
        )
        self._sock.settimeout(timeout)
        self._buffer = b""
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port or 80}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(request.encode("ascii"))
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise RuntimeError("CDP WebSocket 握手失败")
            header += chunk
            if len(header) > 65536:
                raise RuntimeError("CDP WebSocket 握手响应过长")
        head, _, rest = header.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise RuntimeError(head.decode("latin-1", "replace")[:300])
        self._buffer = rest

    def settimeout(self, timeout: float) -> None:
        self._sock.settimeout(timeout)

    def _recv_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("CDP WebSocket 连接已关闭")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    @staticmethod
    def _frame(opcode: int, payload: bytes) -> bytes:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return bytes(header) + masked

    def send(self, text: str) -> None:
        self._sock.sendall(self._frame(0x1, text.encode("utf-8")))

    def _send_control(self, opcode: int, payload: bytes = b"") -> None:
        self._sock.sendall(self._frame(opcode, payload))

    def recv(self) -> str:
        chunks: list[bytes] = []
        while True:
            first, second = self._recv_exact(2)
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length)
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise ConnectionError("CDP WebSocket 已关闭")
            if opcode == 0x9:
                self._send_control(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x0):
                chunks.append(payload)
                if fin:
                    return b"".join(chunks).decode("utf-8", "replace")
                continue

    def close(self) -> None:
        try:
            self._send_control(0x8)
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass



CHROME_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str:
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    return ""


class BrowserSession:
    def __init__(self, port: int = 18765, browser_path: str = "", profile_dir: str = "") -> None:
        self.port = int(port)
        self.browser_path = browser_path or find_browser()
        self.profile_dir = profile_dir or str(Path.home() / ".smart-clicker-browser")
        self.process: subprocess.Popen | None = None
        self.ws = None
        self._msg_id = 0
        self.target_info: dict = {}

    # -- lifecycle -----------------------------------------------------
    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def is_running(self) -> bool:
        try:
            _http_json(f"{self.endpoint}/json/version", timeout=1.2)
            return True
        except Exception:
            return False

    def launch(self, url: str = "") -> None:
        if not self.browser_path or not Path(self.browser_path).exists():
            raise RuntimeError("未找到 Chrome/Edge，请填写浏览器 exe 路径")
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        if not self.is_running():
            args = [
                self.browser_path,
                f"--remote-debugging-port={self.port}",
                f"--user-data-dir={self.profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-popup-blocking",
                "--new-window",
            ]
            if url:
                args.append(url)
            self.process = subprocess.Popen(args, close_fds=True)
        self.wait_ready()

    def wait_ready(self, timeout: float = 15.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                _http_json(f"{self.endpoint}/json/version", timeout=1.0)
                return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError("浏览器调试端口未就绪，请检查端口是否被占用")

    def connect(self, preferred_url: str = "") -> None:
        self.wait_ready()
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        self.ws = None
        data = _http_json(f"{self.endpoint}/json/list", timeout=3)
        default_hosts = (
            "discovery.lenovo.com.cn",
            "microsoft.com",
            "msn.com",
            "bing.com",
            "baidu.com",
            "google.com",
        )

        def is_default_page(url: str) -> bool:
            text = str(url or "").lower()
            return any(host in text for host in default_hosts)

        pages = [
            x for x in data
            if x.get("type") == "page"
            and x.get("webSocketDebuggerUrl")
            and not str(x.get("url", "")).startswith(("chrome://", "edge://", "about:"))
            and not is_default_page(x.get("url", ""))
        ]
        if not pages:
            raise RuntimeError(
                "浏览器当前只有新标签页或浏览器默认主页，请先在这个浏览器里打开答题页面；"
                "再点击「启动/连接浏览器」按钮，显示已连接后点击「开始」即可。"
            )
        target = None
        if preferred_url:
            try:
                from urllib.parse import urlparse

                wanted = urlparse(preferred_url).netloc.lower()
                if wanted:
                    target = next(
                        (x for x in pages if wanted in str(x.get("url", "")).lower()),
                        None,
                    )
            except Exception:
                target = None
        target = target or next(
            (x for x in pages if x.get("url", "").startswith(("http://", "https://"))),
            pages[0],
        )
        self.target_info = target
        self.ws = _MiniWebSocket(target["webSocketDebuggerUrl"], timeout=12)
        self.call("Runtime.enable")
        self.call("Page.enable")

    def close(self) -> None:
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        self.ws = None

    def kill(self) -> None:
        self.close()
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
        except Exception:
            pass
        self.process = None

    # -- CDP -----------------------------------------------------------
    def call(self, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
        if self.ws is None:
            raise RuntimeError("尚未连接浏览器")
        self._msg_id += 1
        msg_id = self._msg_id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.settimeout(max(0.2, deadline - time.time()))
            raw = self.ws.recv()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if data.get("id") != msg_id:
                continue
            if "error" in data:
                raise RuntimeError(str(data["error"]))
            return data.get("result", {})
        raise TimeoutError(f"CDP 命令超时：{method}")

    def eval(self, expression: str, await_promise: bool = True):
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            exception = details.get("exception") or {}
            raise RuntimeError(
                str(exception.get("description") or details.get("text") or "页面脚本执行失败")
            )
        return result.get("result", {}).get("value")

    def screenshot(self, full_page: bool = False) -> bytes:
        import base64

        params = {
            "format": "png",
            "fromSurface": True,
            "captureBeyondViewport": bool(full_page),
        }
        if full_page:
            try:
                metrics = self.call("Page.getLayoutMetrics")
                size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
                width = int(size.get("width", 0))
                height = int(size.get("height", 0))
                if width > 0 and height > 0:
                    # 限制超大页面，避免图片请求过大
                    params["clip"] = {
                        "x": 0, "y": 0,
                        "width": min(width, 2400),
                        "height": min(height, 8000),
                        "scale": 1,
                    }
            except Exception:  # noqa: BLE001
                pass
        result = self.call("Page.captureScreenshot", params)
        return base64.b64decode(result.get("data", ""))

    def page_size(self) -> tuple[int, int]:
        metrics = self.call("Page.getLayoutMetrics")
        size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
        return int(size.get("width", 0)), int(size.get("height", 0))

    def screenshot_clip(self, x: int, y: int, width: int, height: int) -> bytes:
        import base64

        result = self.call(
            "Page.captureScreenshot",
            {
                "format": "png",
                "fromSurface": True,
                "captureBeyondViewport": True,
                "clip": {
                    "x": int(x), "y": int(y),
                    "width": min(8000, max(1, int(width))),
                    "height": min(8000, max(1, int(height))),
                    "scale": 1,
                },
            },
        )
        return base64.b64decode(result.get("data", ""))

    def navigate(self, url: str) -> None:
        if not url:
            return
        self.call("Page.navigate", {"url": url})
        time.sleep(1.0)

    def insert_text(self, text: str) -> None:
        self.call("Input.insertText", {"text": str(text)})

    def clear_focused(self) -> None:
        """Ctrl+A + Backspace，用于覆盖编辑器已有内容。"""
        self.call(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "modifiers": 2, "key": "a", "code": "KeyA",
             "windowsVirtualKeyCode": 65},
        )
        self.call(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", "modifiers": 2, "key": "a", "code": "KeyA",
             "windowsVirtualKeyCode": 65},
        )
        self.call(
            "Input.dispatchKeyEvent",
            {"type": "keyDown", "key": "Backspace", "code": "Backspace",
             "windowsVirtualKeyCode": 8},
        )
        self.call(
            "Input.dispatchKeyEvent",
            {"type": "keyUp", "key": "Backspace", "code": "Backspace",
             "windowsVirtualKeyCode": 8},
        )
