"""轻量 Chrome DevTools Protocol 客户端（不依赖 chromedriver）。

用于「浏览器混合答题」：连接本机 Chrome/Edge 的调试端口，
直接读 DOM、写入输入框、点击选项和下一题。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover
    requests = None

try:
    import websocket
except ModuleNotFoundError:  # pragma: no cover
    websocket = None


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
        if requests is None:
            return False
        try:
            requests.get(f"{self.endpoint}/json/version", timeout=1.2,
                         proxies={"http": None, "https": None})
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
        if requests is None:
            raise RuntimeError("缺少 requests，无法连接浏览器")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                requests.get(f"{self.endpoint}/json/version", timeout=1.0,
                             proxies={"http": None, "https": None})
                return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError("浏览器调试端口未就绪，请检查端口是否被占用")

    def connect(self) -> None:
        if requests is None or websocket is None:
            raise RuntimeError("缺少 requests/websocket-client，请先安装：pip install requests websocket-client")
        self.wait_ready()
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        self.ws = None
        data = requests.get(f"{self.endpoint}/json/list", timeout=3,
                            proxies={"http": None, "https": None}).json()
        pages = [
            x for x in data
            if x.get("type") == "page"
            and x.get("webSocketDebuggerUrl")
            and not str(x.get("url", "")).startswith(("chrome://", "edge://", "about:"))
        ]
        if not pages:
            raise RuntimeError("浏览器当前只有新标签页，请先在这个浏览器里打开答题页面")
        target = next((x for x in pages if x.get("url", "").startswith(("http://", "https://"))), pages[0])
        self.target_info = target
        self.ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=12, suppress_origin=True
        )
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
                        "height": min(height, 12000),
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
                    "width": max(1, int(width)), "height": max(1, int(height)),
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
