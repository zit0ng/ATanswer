"""本地模拟 AI 接口（开发/测试用，只监听 127.0.0.1）。

用于在没有真实 API Key / 外网的情况下验证「AI 答题」流程。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server 约定
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)
        self.server.requests.append(  # type: ignore[attr-defined]
            {"path": self.path, "headers": dict(self.headers), "body": raw}
        )
        delay = float(getattr(self.server, "delay_s", 0.0) or 0.0)
        if delay > 0:
            time.sleep(delay)
        content = getattr(self.server, "content", None)  # type: ignore[attr-defined]
        if callable(content):
            try:
                content = content()
            except Exception:
                content = None
        if content is None:
            content = f"推理：根据题目内容判断，正确选项是 {self.server.answer}。\n答案：{self.server.answer}"  # type: ignore[attr-defined]
        payload = {
            "id": "mock-1",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # 静默
        pass


def start_mock_ai_server(
    answer: str = "B",
    host: str = "127.0.0.1",
    port: int = 0,
    delay_s: float = 0.0,
    content: str | None = None,
):
    """启动模拟 AI 服务，返回 (server, base_url)。

    - answer: 默认回答（生成"...答案：X"内容）
    - content: 若提供，则直接返回该文本内容（可用来模拟 JSON 等）
    调用方结束时需执行 ``server.shutdown(); server.server_close()``。
    """
    server = ThreadingHTTPServer((host, port), _Handler)
    server.answer = answer  # type: ignore[attr-defined]
    server.delay_s = delay_s  # type: ignore[attr-defined]
    server.content = content  # type: ignore[attr-defined]
    server.requests = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{host}:{server.server_address[1]}/v1"
    return server, base_url


def stop_mock_ai_server(server) -> None:
    try:
        server.shutdown()
    finally:
        server.server_close()
