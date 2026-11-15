"""端到端集成测试（开发验证用，非单元测试）。

验证真实的 Windows 鼠标注入链路：
    1. 打开一个置顶测试窗口，用 PCController 定点点击 2 次；
    2. 从窗口中截取模板，用 PCController 找图并点击 1 次。

运行：
    python tools/integration_test.py
注意：运行过程中鼠标会被程序移动并点击（目标仅限本脚本创建的窗口）。
"""
from __future__ import annotations

import random
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import winapi  # noqa: E402

winapi.enable_dpi_awareness()

from app.controllers import create_controller  # noqa: E402
from app.engine import ClickConfig  # noqa: E402
from app.templates import save_template, template_from_rgb  # noqa: E402


def pump(root: tk.Tk, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            root.update()
        except tk.TclError:
            return
        time.sleep(0.02)


def wait_finished(root: tk.Tk, events: list[dict], timeout: float = 10.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        pump(root, 0.05)
        for event in reversed(events):
            if event.get("kind") == "finished":
                return event
    return None


def make_window() -> tuple[tk.Tk, tk.Canvas, list[tuple[int, int]]]:
    """创建一个置顶的测试目标窗口，返回 (root, canvas, 点击记录)。"""
    root = tk.Tk()
    root.title("智能点击助手 · 集成测试目标")
    root.geometry("560x360+620+360")
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, width=560, height=360, bg="#0f1722", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    random.seed(20261106)
    for _ in range(30):
        x0 = random.randint(16, 480)
        y0 = random.randint(16, 300)
        w = random.randint(20, 60)
        h = random.randint(12, 32)
        color = random.choice(["#1d2b3d", "#22384f", "#2e4a66", "#16324a"])
        canvas.create_rectangle(x0, y0, x0 + w, y0 + h, fill=color, outline="")
    target = canvas.create_rectangle(180, 120, 380, 250, fill="#2b5cff", outline="#22d3ee", width=5)
    canvas.create_text(280, 185, text="CLICK TARGET", fill="#ffffff",
                       font=("Microsoft YaHei UI", 20, "bold"))
    hits: list[tuple[int, int]] = []

    def on_click(event) -> None:
        hits.append((event.x_root, event.y_root))

    canvas.tag_bind(target, "<Button-1>", on_click)
    canvas.bind("<Button-1>", on_click)
    root.update()
    root.focus_force()
    pump(root, 0.4)
    return root, canvas, hits


def main() -> int:
    root, canvas, hits = make_window()
    rootx, rooty = canvas.winfo_rootx(), canvas.winfo_rooty()
    target_center = (rootx + 280, rooty + 185)
    print(f"目标窗口位置：({rootx}, {rooty})，点击目标：{target_center}")

    events: list[dict] = []
    controller = create_controller(events.append)
    failures = 0

    # ---- 1. 定点连点 ----
    hits.clear()
    ok, message = controller.start(
        ClickConfig(
            mode="fixed",
            x=target_center[0],
            y=target_center[1],
            button="left",
            interval_ms=260,
            count_limit=2,
            start_delay_ms=150,
        )
    )
    if not ok:
        print("固定点击启动失败：", message)
        failures += 1
    finished = wait_finished(root, events)
    controller.stop()
    pump(root, 0.2)
    print("定点连点：", "PASS" if finished and finished.get("clicks") == 2 and hits else "FAIL",
          f"(clicks={finished.get('clicks') if finished else None}, 收到点击={len(hits)})")
    if not (finished and finished.get("clicks") == 2 and hits):
        failures += 1

    # ---- 2. 找图点击 ----
    region = (rootx + 180, rooty + 120, 200, 130)
    rgb, width, height = controller.capture_region(region)
    tpl_path = save_template(rgb, width, height, prefix="itest")
    template = template_from_rgb(str(tpl_path), rgb, width, height)
    test_result = controller.test_match(template, None, 0.9)
    found = test_result.get("match")
    print("识别测试：", "PASS" if found else "FAIL",
          f"(score={found.score:.3f}, pos=({found.x}, {found.y}), {test_result['elapsed_ms']:.1f}ms)" if found else "")
    if not found:
        failures += 1

    hits.clear()
    events.clear()
    ok, message = controller.start(
        ClickConfig(
            mode="image",
            template=template,
            template_path=str(tpl_path),
            threshold=0.9,
            poll_ms=250,
            count_limit=1,
            start_delay_ms=150,
        )
    )
    if not ok:
        print("找图点击启动失败：", message)
        failures += 1
    finished = wait_finished(root, events)
    controller.stop()
    pump(root, 0.2)
    print("找图点击：", "PASS" if finished and finished.get("clicks") == 1 and hits else "FAIL",
          f"(clicks={finished.get('clicks') if finished else None}, 收到点击={len(hits)})")
    if not (finished and finished.get("clicks") == 1 and hits):
        failures += 1

    try:
        root.destroy()
    except tk.TclError:
        pass
    print("RESULT:", "PASS" if failures == 0 else f"FAIL ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
