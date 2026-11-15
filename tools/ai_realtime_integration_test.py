"""AI 实时监控模式端到端集成测试（本地模拟 AI + 真实鼠标点击 + 题目切换）。

验证：
1. 实时监控 + 自动下一题：2 道题各请求一次 AI、点击 C、点击下一题；
2. 实时监控 + 手动翻页：答完不重复请求；用户/平台翻页后自动识别新题。
"""
from __future__ import annotations

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


def pump(root: tk.Tk, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            root.update()
        except tk.TclError:
            return
        time.sleep(0.02)


def wait_until(root: tk.Tk, predicate, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        pump(root, 0.05)
        if predicate():
            return True
    return False


def wait_finished(root: tk.Tk, events: list[dict], timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        pump(root, 0.05)
        for event in reversed(events):
            if event.get("kind") == "finished":
                return event
    return None


def make_quiz_window():
    root = tk.Tk()
    root.title("AI 实时监控 · 集成测试")
    root.geometry("780x600+460+240")
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, width=780, height=600, bg="#f7f9fc", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    state = {"question": 1}
    question_text = canvas.create_text(
        40, 120, anchor="nw", width=700, fill="#111827",
        text="第 1 题：Which option is correct?",
        font=("Microsoft YaHei UI", 17),
    )
    canvas.create_text(40, 40, anchor="nw", text="模拟答题平台（实时监控测试）",
                       fill="#6b7280", font=("Microsoft YaHei UI", 13))
    rows = {}
    for idx, letter in enumerate("ABCD"):
        cy = 250 + idx * 66
        rows[letter] = cy
        canvas.create_oval(50, cy - 24, 98, cy + 24, outline="#9db4d6", width=2)
        canvas.create_text(74, cy, text=letter, fill="#2563eb",
                           font=("Microsoft YaHei UI", 14, "bold"))
        canvas.create_text(120, cy, anchor="w", text=f"选项 {letter}",
                           fill="#111827", font=("Microsoft YaHei UI", 15))
    canvas.create_rectangle(560, 520, 740, 570, fill="#e8f0fe", outline="#c7d7f5")
    canvas.create_text(650, 545, text="下一题 >", fill="#2563eb",
                       font=("Microsoft YaHei UI", 14, "bold"))

    hits = {"options": [], "next": 0}

    def advance() -> None:
        state["question"] += 1
        canvas.itemconfigure(
            question_text, text=f"第 {state['question']} 题：Which option is correct?"
        )

    def on_click(event) -> None:
        local_y = event.y_root - canvas.winfo_rooty()
        local_x = event.x_root - canvas.winfo_rootx()
        if 520 <= local_y <= 570 and 560 <= local_x <= 740:
            hits["next"] += 1
            advance()
            return
        for letter, cy in rows.items():
            if abs(local_y - cy) < 34:
                hits["options"].append(letter)
                return

    canvas.bind("<Button-1>", on_click)
    state["advance"] = advance
    root.update()
    root.focus_force()
    pump(root, 0.4)
    return root, canvas, hits, rows, state


def main() -> int:
    server, base_url = start_mock_ai_server(answer="C")
    root, canvas, hits, rows, state = make_quiz_window()
    left, top = canvas.winfo_rootx(), canvas.winfo_rooty()
    region = (left + 20, top + 50, 700, 420)
    next_point = (left + 650, top + 545)
    options = {letter: (left + 74, top + cy) for letter, cy in rows.items()}

    events: list[dict] = []
    controller = create_controller(events.append)
    ai_config = ai_client.AIConfig(
        base_url=base_url, api_key="sk-test", model="mock-vl", timeout_s=15
    )
    failures = 0

    def base_config(**kwargs) -> ClickConfig:
        values = dict(
            mode="ai",
            ai_config=ai_config,
            ai_region=region,
            ai_options=options,
            ai_next_point=next_point,
            ai_option_wait_ms=120,
            ai_next_wait_ms=250,
            ai_monitor=True,
            ai_auto_next=True,
            ai_poll_ms=200,
            count_limit=2,
            start_delay_ms=100,
            button="left",
        )
        values.update(kwargs)
        return ClickConfig(**values)

    # ---- 1. 实时监控 + 自动下一题 ----
    before = len(server.requests)
    ok, message = controller.start(base_config())
    print("monitor+auto start:", ok, message)
    if not ok:
        failures += 1
    finished = wait_finished(root, events)
    controller.stop()
    pump(root, 0.2)
    requests = len(server.requests) - before
    print("auto finished:", finished)
    print("auto requests:", requests, "| options:", hits["options"], "| next:", hits["next"],
          "| question:", state["question"])
    if not finished or finished.get("clicks") != 2:
        failures += 1
    if requests != 2:
        failures += 1
    if hits["options"] != ["C", "C"]:
        failures += 1
    if hits["next"] != 2:
        failures += 1

    # ---- 2. 实时监控 + 手动翻页（用户自己点下一题）----
    hits["options"].clear()
    hits["next"] = 0
    events.clear()
    before = len(server.requests)
    ok, message = controller.start(base_config(ai_auto_next=False, count_limit=0))
    print("monitor+manual start:", ok, message)
    if not ok:
        failures += 1
    if not wait_until(root, lambda: len(hits["options"]) >= 1, timeout=15):
        print("manual: first question not answered")
        failures += 1
    pump(root, 1.5)
    mid_requests = len(server.requests) - before
    print("manual after first answer requests:", mid_requests, "| options:", hits["options"])
    if mid_requests != 1:
        failures += 1  # 同一画面不应重复请求
    # 模拟用户手动翻页（直接触发页面切换；真实使用时由用户点击下一题）
    state["advance"]()
    pump(root, 0.6)
    if not wait_until(root, lambda: len(hits["options"]) >= 2, timeout=15):
        print("manual: second question not answered")
        failures += 1
    pump(root, 0.5)
    total_requests = len(server.requests) - before
    print("manual total requests:", total_requests, "| options:", hits["options"],
          "| next:", hits["next"], "| question:", state["question"])
    if total_requests != 2:
        failures += 1
    if hits["options"] != ["C", "C"]:
        failures += 1
    controller.stop()
    pump(root, 0.2)

    try:
        root.destroy()
    except tk.TclError:
        pass
    stop_mock_ai_server(server)
    print("RESULT:", "PASS" if failures == 0 else f"FAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
