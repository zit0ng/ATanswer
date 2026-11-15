"""AI 答题端到端集成测试（本地模拟 AI + 真实鼠标点击）。

流程：
1. 打开一个置顶的"模拟答题窗口"；
2. 启动本地 mock AI 服务（返回固定答案 C）；
3. 用 PCController 的 AI 模式执行 2 道题；
4. 校验：AI 请求收到 2 次、选项 C 被点击 2 次、「下一题」被点击 2 次。

运行：
    python tools/ai_integration_test.py
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


def wait_finished(root: tk.Tk, events: list[dict], timeout: float = 15.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        pump(root, 0.05)
        for event in reversed(events):
            if event.get("kind") == "finished":
                return event
    return None


def make_quiz_window():
    """创建模拟答题窗口，返回 (root, canvas, hit 信息 dict, 关键坐标)。"""
    root = tk.Tk()
    root.title("AI 答题 · 集成测试")
    root.geometry("760x580+480+260")
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, width=760, height=580, bg="#f7f9fc", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(40, 30, anchor="nw", text="1. 单选题（模拟）",
                       fill="#6b7789", font=("Microsoft YaHei UI", 12))
    canvas.create_text(40, 70, anchor="nw", width=680, fill="#111827",
                       text="According to the text, Yuan Longping's new hybrid rice increased the annual "
                            "rice yield by 20%. Which option is correct?",
                       font=("Microsoft YaHei UI", 14))
    letters = ["A", "B", "C", "D"]
    option_labels = ["40-50 million", "70 million", "208 million", "400-500 million"]
    option_rows: dict[str, int] = {}
    for idx, (letter, label) in enumerate(zip(letters, option_labels)):
        cy = 200 + idx * 70
        option_rows[letter] = cy
        canvas.create_oval(50, cy - 24, 98, cy + 24, outline="#9db4d6", width=2)
        canvas.create_text(74, cy, text=letter, fill="#2563eb",
                           font=("Microsoft YaHei UI", 14, "bold"))
        canvas.create_text(120, cy, anchor="w", text=label, fill="#111827",
                           font=("Microsoft YaHei UI", 14))
    # 下一题按钮
    canvas.create_rectangle(540, 500, 720, 552, fill="#e8f0fe", outline="#c7d7f5")
    canvas.create_text(630, 526, text="下一题 >", fill="#2563eb",
                       font=("Microsoft YaHei UI", 14, "bold"))

    hits = {"options": [], "next": 0}

    def on_click(event) -> None:
        x_root, y_root = event.x_root, event.y_root
        left, top = canvas.winfo_rootx(), canvas.winfo_rooty()
        local_y = y_root - top
        if 500 <= local_y <= 552 and 540 <= (x_root - left) <= 720:
            hits["next"] += 1
            return
        best = None
        for letter, cy in option_rows.items():
            distance = abs(local_y - cy)
            if distance < 35 and best is None or (distance < 35 and distance < abs(local_y - option_rows[best])):
                best = letter
        if best is not None:
            hits["options"].append(best)

    canvas.bind("<Button-1>", on_click)
    root.update()
    root.focus_force()
    pump(root, 0.4)
    return root, canvas, hits, option_rows


def main() -> int:
    server, base_url = start_mock_ai_server(answer="C")
    root, canvas, hits, option_rows = make_quiz_window()
    left, top = canvas.winfo_rootx(), canvas.winfo_rooty()
    failures = 0

    print("mock AI:", base_url)
    events: list[dict] = []
    controller = create_controller(events.append)
    ai_config = ai_client.AIConfig(
        base_url=base_url, api_key="sk-test", model="mock-vl", timeout_s=15
    )

    # ---- 单次 AI 识别测试 ----
    region = (left + 20, top + 40, 700, 320)
    try:
        result = controller.test_ai(region, ai_config)
        answer = result.get("answer")
        print("test_ai:", "PASS" if answer and answer.letter == "C" else "FAIL",
              f"(letter={getattr(answer, 'letter', None)}, png={len(result.get('png', b''))} bytes)")
        if not answer or answer.letter != "C":
            failures += 1
    except Exception as exc:  # noqa: BLE001
        print("test_ai FAIL:", exc)
        failures += 1

    # ---- AI 自动答题 2 道 ----
    options = {letter: (left + 74, top + cy) for letter, cy in option_rows.items()}
    next_point = (left + 630, top + 526)
    config = ClickConfig(
        mode="ai",
        ai_config=ai_config,
        ai_region=region,
        ai_options=options,
        ai_next_point=next_point,
        button="left",
        count_limit=2,
        start_delay_ms=100,
        ai_option_wait_ms=120,
        ai_next_wait_ms=200,
        ai_monitor=False,
        ai_auto_next=True,
    )
    ok, message = controller.start(config)
    print("engine start:", ok, message)
    if not ok:
        failures += 1
    requests_before_engine = len(server.requests)
    finished = wait_finished(root, events)
    controller.stop()
    pump(root, 0.2)

    request_count = len(server.requests) - requests_before_engine
    print("finished:", finished)
    print("AI 请求次数:", request_count, "| 选项点击:", hits["options"], "| 下一题点击:", hits["next"])
    if not finished or finished.get("clicks") != 2:
        failures += 1
    if request_count != 2:
        failures += 1
    if hits["options"] != ["C", "C"]:
        failures += 1
    if hits["next"] != 2:
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
