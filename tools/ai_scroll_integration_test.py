"""AI 实时定位选项端到端测试：模拟滚动后选项位置变化，AI 返回新坐标，验证不会点错。

流程：
1. 模拟答题窗口，选项在初始位置；
2. mock AI 第一次返回答案 C + 初始坐标；
3. 模拟"向下滚动"，把选项整体下移 100px、题目变化；
4. mock AI 第二次返回答案 C + 滚动后的新坐标；
5. 校验两次都点中了 C（而不是旧坐标对应的错行）。

运行：
    python tools/ai_scroll_integration_test.py
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

CANVAS_W, CANVAS_H = 760, 560
REGION = (0, 0, CANVAS_W, CANVAS_H)
ROWS_1 = {"A": 220, "B": 282, "C": 344, "D": 406}
ROWS_2 = {"A": 320, "B": 382, "C": 444, "D": 506}
NEXT_POINT = (640, 505)


def pump(root: tk.Tk, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            root.update()
        except tk.TclError:
            return
        time.sleep(0.02)


def wait_until(root: tk.Tk, predicate, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        pump(root, 0.05)
        if predicate():
            return True
    return False


def wait_finished(root: tk.Tk, events: list[dict], timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        pump(root, 0.05)
        for event in reversed(events):
            if event.get("kind") == "finished":
                return event
    return None


def layout_json(rows: dict[str, int]) -> str:
    return json.dumps(
        {
            "answer": "C",
            "options": [
                {"letter": letter, "x": 72 / CANVAS_W, "y": cy / CANVAS_H}
                for letter, cy in rows.items()
            ],
            "next": {"x": NEXT_POINT[0] / CANVAS_W, "y": NEXT_POINT[1] / CANVAS_H},
        }
    )


def main() -> int:
    layouts = [layout_json(ROWS_1), layout_json(ROWS_2)]
    server, base_url = start_mock_ai_server()
    server.content = lambda: layouts[min(len(server.requests) - 1, len(layouts) - 1)]  # type: ignore[assignment]
    failures = 0

    root = tk.Tk()
    root.title("AI 滚动定位 · 集成测试")
    root.geometry("780x600+460+240")
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, width=780, height=600, bg="#f7f9fc", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(40, 40, anchor="nw", text="模拟答题平台（滚动测试）",
                       fill="#6b7280", font=("Microsoft YaHei UI", 13))
    state = {"rows": dict(ROWS_1), "q": 1, "option_items": []}
    hits = {"options": [], "next": 0}

    def draw_question() -> None:
        canvas.delete("question")
        canvas.create_text(
            40, 90, anchor="nw", width=700, fill="#111827", tags="question",
            text=f"第 {state['q']} 题：滚动后选项位置发生变化，AI 应点中新的 C 选项。",
            font=("Microsoft YaHei UI", 15),
        )

    def draw_options() -> None:
        for item in state["option_items"]:
            canvas.delete(item)
        state["option_items"] = []
        for letter, cy in state["rows"].items():
            state["option_items"].append(
                canvas.create_oval(50, cy - 22, 94, cy + 22, outline="#9db4d6", width=2)
            )
            state["option_items"].append(
                canvas.create_text(72, cy, text=letter, fill="#2563eb",
                                   font=("Microsoft YaHei UI", 14, "bold"))
            )
            state["option_items"].append(
                canvas.create_text(120, cy, anchor="w", text=f"选项 {letter}",
                                   fill="#111", font=("Microsoft YaHei UI", 15))
            )

    draw_question()
    draw_options()
    canvas.create_rectangle(560, 480, 720, 530, fill="#e8f0fe", outline="#c7d7f5")
    canvas.create_text(640, 505, text="下一题 >", fill="#2563eb",
                       font=("Microsoft YaHei UI", 14, "bold"))

    def on_click(event) -> None:
        local_y = event.y_root - canvas.winfo_rooty()
        local_x = event.x_root - canvas.winfo_rootx()
        if 480 <= local_y <= 530 and 560 <= local_x <= 720:
            hits["next"] += 1
            return
        for letter, cy in state["rows"].items():
            if abs(local_y - cy) < 32:
                hits["options"].append(letter)
                return

    canvas.bind("<Button-1>", on_click)
    root.update()
    root.focus_force()
    pump(root, 0.4)
    left, top = canvas.winfo_rootx(), canvas.winfo_rooty()
    absolute_region = (left, top, CANVAS_W, CANVAS_H)

    events: list[dict] = []
    controller = create_controller(events.append)
    ai_config = ai_client.AIConfig(
        base_url=base_url, api_key="sk-test", model="mock-vl", timeout_s=15
    )
    config = ClickConfig(
        mode="ai",
        ai_config=ai_config,
        ai_region=absolute_region,
        # 故意配置"滚动前"的固定坐标：实时定位应优先使用 AI 返回的新坐标
        ai_options={letter: (left + 72, top + cy) for letter, cy in ROWS_1.items()},
        ai_next_point=(left + NEXT_POINT[0], top + NEXT_POINT[1]),
        ai_option_wait_ms=100,
        ai_next_wait_ms=150,
        ai_monitor=True,
        ai_auto_next=False,
        ai_poll_ms=200,
        ai_realtime_layout=True,
        count_limit=2,
        start_delay_ms=100,
    )
    ok, message = controller.start(config)
    print("start:", ok, message)
    if not ok:
        failures += 1

    if not wait_until(root, lambda: len(hits["options"]) >= 1, timeout=15):
        print("FAIL: 第一题没有作答")
        failures += 1
    # 等按钮点击后的基线/冷却稳定下来，再模拟用户滚动
    pump(root, 0.8)

    # 模拟滚动：选项整体下移 100px，题目变化
    state["q"] += 1
    state["rows"] = dict(ROWS_2)
    state["option_items"] = state["option_items"]
    canvas.delete("all")
    draw_question()
    draw_options()
    canvas.create_rectangle(560, 480, 720, 530, fill="#e8f0fe", outline="#c7d7f5")
    canvas.create_text(640, 505, text="下一题 >", fill="#2563eb",
                       font=("Microsoft YaHei UI", 14, "bold"))
    root.update()
    print("scrolled, waiting for second answer...")

    if not wait_until(root, lambda: len(hits["options"]) >= 2, timeout=20):
        print("FAIL: 滚动后没有作答")
        failures += 1
    finished = wait_finished(root, events, timeout=10)
    controller.stop()
    pump(root, 0.2)

    print("finished:", finished)
    print("hits:", hits, "| AI requests:", len(server.requests))
    if hits["options"] != ["C", "C"]:
        print("FAIL: 点错了选项（可能没有使用滚动后的新坐标）")
        failures += 1
    if len(server.requests) != 2:
        failures += 1
    if not finished or finished.get("clicks") != 2:
        failures += 1

    try:
        root.destroy()
    except tk.TclError:
        pass
    stop_mock_ai_server(server)
    print("RESULT:", "PASS" if failures == 0 else f"FAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
