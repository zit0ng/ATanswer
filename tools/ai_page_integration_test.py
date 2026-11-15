"""整页多题模式端到端测试：一页 2 道题（含 1 道多选），一次 AI 请求全部答完。

验证：
1. 一页里有多道题时，只发 1 次 AI 请求；
2. 单选/多选混合：单选点 1 个选项，多选点全部正确选项；
3. 最后点一次「下一题」；
4. 全程真实鼠标注入（SendInput）。

运行：
    python tools/ai_page_integration_test.py
"""
from __future__ import annotations

import json
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import ai_client, winapi  # noqa: E402

winapi.enable_dpi_awareness()

from app.controllers import create_controller  # noqa: E402
from app.engine import ClickConfig  # noqa: E402
from tools.mock_ai_server import start_mock_ai_server, stop_mock_ai_server  # noqa: E402

CANVAS_W, CANVAS_H = 760, 760
ROWS = {
    "Q1": {"A": 120, "B": 170, "C": 220, "D": 270},
    "Q2": {"A": 400, "B": 450, "C": 500, "D": 550},
}
NEXT_RECT = (560, 690, 720, 740)
NEXT_POINT = (640, 715)


def pump(root: tk.Tk, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            root.update()
        except tk.TclError:
            return
        time.sleep(0.02)


def wait_finished(root: tk.Tk, events: list[dict], timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        pump(root, 0.05)
        for event in reversed(events):
            if event.get("kind") == "finished":
                return event
    return None


def page_content() -> str:
    def options(rows: dict[str, int]):
        return [
            {"letter": letter, "x": 72 / CANVAS_W, "y": cy / CANVAS_H}
            for letter, cy in rows.items()
        ]

    return json.dumps(
        {
            "questions": [
                {"index": 1, "type": "single", "answers": ["B"],
                 "options": options(ROWS["Q1"])},
                {"index": 2, "type": "multiple", "answers": ["A", "C"],
                 "options": options(ROWS["Q2"])},
            ],
            "next": {"x": NEXT_POINT[0] / CANVAS_W, "y": NEXT_POINT[1] / CANVAS_H},
        }
    )


def main() -> int:
    server, base_url = start_mock_ai_server(content=page_content())
    failures = 0

    root = tk.Tk()
    root.title("整页多题 · 集成测试")
    root.geometry("780x800+480+120")
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, width=780, height=800, bg="#f7f9fc", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    hits = {"options": [], "next": 0}

    canvas.create_text(40, 40, anchor="nw", text="模拟答题页：第 1、2 题同屏",
                       fill="#6b7280", font=("Microsoft YaHei UI", 13))
    for q_idx, (label, rows) in enumerate(ROWS.items(), start=1):
        canvas.create_text(
            40, rows["A"] - 60, anchor="nw", fill="#111827",
            text=f"{q_idx}. 第 {q_idx} 题（模拟）：选择正确答案。",
            font=("Microsoft YaHei UI", 15),
        )
        for letter, cy in rows.items():
            canvas.create_oval(50, cy - 22, 94, cy + 22, outline="#9db4d6", width=2)
            canvas.create_text(72, cy, text=letter, fill="#2563eb",
                               font=("Microsoft YaHei UI", 14, "bold"))
            canvas.create_text(120, cy, anchor="w", text=f"选项 {label}-{letter}",
                               fill="#111827", font=("Microsoft YaHei UI", 14))

    left, top, right, bottom = NEXT_RECT
    canvas.create_rectangle(left, top, right, bottom, fill="#e8f0fe", outline="#c7d7f5")
    canvas.create_text(NEXT_POINT[0], NEXT_POINT[1], text="下一题 >", fill="#2563eb",
                       font=("Microsoft YaHei UI", 14, "bold"))

    def on_click(event) -> None:
        local_y = event.y_root - canvas.winfo_rooty()
        local_x = event.x_root - canvas.winfo_rootx()
        if left <= local_x <= right and top <= local_y <= bottom:
            hits["next"] += 1
            return
        for rows in ROWS.values():
            for letter, cy in rows.items():
                if abs(local_y - cy) < 28:
                    hits["options"].append(letter)
                    return

    canvas.bind("<Button-1>", on_click)
    root.update()
    root.focus_force()
    pump(root, 0.8)
    root.focus_force()
    root.attributes("-topmost", True)
    pump(root, 0.3)
    region = (
        canvas.winfo_rootx(), canvas.winfo_rooty(), CANVAS_W, CANVAS_H
    )

    events: list[dict] = []
    controller = create_controller(events.append)
    config = ClickConfig(
        mode="ai",
        ai_config=ai_client.AIConfig(
            base_url=base_url, api_key="sk-test", model="mock-vl", timeout_s=15
        ),
        ai_region=region,
        ai_options={},  # 整页多题模式：不需要预先配置选项坐标
        ai_next_point=None,  # 下一题位置由 AI 返回
        ai_option_wait_ms=100,
        ai_next_wait_ms=150,
        ai_monitor=True,
        ai_auto_next=True,
        ai_poll_ms=200,
        ai_realtime_layout=True,
        ai_page_mode=True,
        count_limit=2,
        start_delay_ms=100,
    )
    ok, message = controller.start(config)
    print("start:", ok, message)
    if not ok:
        failures += 1

    finished = wait_finished(root, events, timeout=20)
    controller.stop()
    pump(root, 0.2)
    print("finished:", finished)
    print("hits:", hits, "| AI requests:", len(server.requests))
    if not finished or finished.get("reason") != "limit":
        failures += 1
    if hits["options"] != ["B", "A", "C"]:
        print("FAIL: 选项点击不正确（应为 B、A、C，其中第 2 题为多选 A+C）")
        failures += 1
    if hits["next"] != 1:
        print("FAIL: 应点击一次下一题")
        failures += 1
    if len(server.requests) != 1:
        print("FAIL: 整页多题应只请求 AI 一次")
        failures += 1
    if finished and finished.get("clicks") != 3:
        failures += 1
    if finished and finished.get("elapsed", 99) > 8:
        print("WARN: 用时偏长", finished.get("elapsed"))

    try:
        root.destroy()
    except tk.TclError:
        pass
    stop_mock_ai_server(server)
    print("RESULT:", "PASS" if failures == 0 else f"FAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
