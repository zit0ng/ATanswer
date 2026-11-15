"""界面截图与自动化验收脚本（开发验证用，需 Pillow）。

运行：<带 Pillow 的 Python> tools/capture_screenshots.py
输出：docs/screenshot-*.png（只截取程序窗口本身）

说明：AI 预览截图使用 PIL 绘制的示例题目 + 模拟 AI 回答（非真实接口调用），
用于文档展示；真实 AI 链路由 tools/ai_integration_test.py 验证。
"""
from __future__ import annotations

import ctypes
import io
import os
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont, ImageGrab  # noqa: E402

from app import winapi  # noqa: E402
from app.ai_client import AIAnswer  # noqa: E402
from app.preview import AIResultPreview  # noqa: E402
from app.templates import save_template  # noqa: E402
from app.ui import MainApp  # noqa: E402

DOCS = ROOT / "docs"
DOCS.mkdir(exist_ok=True)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def window_rect(window) -> tuple[int, int, int, int]:
    hwnd = winapi.get_toplevel_hwnd(window)
    rect = RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def grab_window(window, filename: str) -> None:
    window.update_idletasks()
    window.update()
    try:
        # 截图时临时改成完全不透明，避免桌面/其它窗口透出来。
        window.attributes("-alpha", 1.0)
        window.attributes("-topmost", True)
        window.lift()
    except tk.TclError:
        pass
    time.sleep(0.25)
    window.update()
    left, top, right, bottom = window_rect(window)
    img = ImageGrab.grab(bbox=(left, top, right, bottom))
    path = DOCS / filename
    img.save(path)
    print("saved", path, img.size)


def _font(size: int, bold: bool = False):
    candidates = [
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyhbd.ttc" if bold
        else Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "simhei.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_sample_question_png() -> bytes:
    """用 PIL 画一张示例选择题截图（用于 AI 预览窗口的文档展示）。"""
    width, height = 900, 620
    img = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(img)
    draw.text((40, 26), "作业 20261103", fill="#374151", font=_font(22))
    draw.text((40, 84), "1. 单选题", fill="#6b7280", font=_font(18))
    lines = [
        "According to the text, Yuan Longping's new",
        "hybrid rice increased the annual rice yield by",
        "20%, which could feed at least how many more",
        "people each year?",
    ]
    for idx, line in enumerate(lines):
        draw.text((40, 130 + idx * 38), line, fill="#111827", font=_font(24))
    options = [
        ("A", "40-50 million"),
        ("B", "70 million"),
        ("C", "208 million"),
        ("D", "400-500 million"),
    ]
    for idx, (letter, label) in enumerate(options):
        cy = 350 + idx * 62
        draw.ellipse([50, cy - 24, 98, cy + 24], outline="#9db4d6", width=3)
        draw.text((64, cy - 15), letter, fill="#2563eb", font=_font(20, bold=True))
        draw.text((122, cy - 18), label, fill="#111827", font=_font(22))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def main() -> None:
    app = MainApp()
    root = app.root
    root.attributes("-topmost", True)
    root.update()
    time.sleep(0.5)
    root.update()
    state: dict = {"preview": None}

    def step_main() -> None:
        grab_window(root, "screenshot-main.png")
        root.after(300, step_typing_dialog)

    def step_typing_dialog() -> None:
        app._on_text_typing()
        dialog = app._typing_dialog
        if dialog is not None and dialog.winfo_exists():
            dialog.text.insert(
                "1.0",
                "这是逐字录入示例：程序会把这句话一个字一个字地打进目标文本框，"
                "不会使用剪贴板，也不会覆盖错位置。",
            )
            dialog.update_idletasks()
            dialog.update()
            grab_window(dialog, "screenshot-typing.png")
            dialog._on_close()
        root.update()
        root.after(300, step_image_mode)

    def step_image_mode() -> None:
        app._on_mode_change("image")
        app.mode_segment.set_active("image")
        root.update()
        left, top, right, bottom = window_rect(root)
        region = (
            left + int((right - left) * 0.045),
            top + int((bottom - top) * 0.060),
            430,
            90,
        )
        try:
            rgb, w, h = app.controller.capture_region(region)
            path = save_template(rgb, w, h, prefix="docshot")
            app._load_template_path(str(path))
            app.var_region_mode.set("full")
        except Exception as exc:  # noqa: BLE001
            print("template capture failed:", exc)
        root.after(500, step_image_shot)

    def step_image_shot() -> None:
        grab_window(root, "screenshot-image.png")
        root.after(300, step_test_match)

    def step_test_match() -> None:
        app._on_test_match()
        root.after(1000, step_preview_shot)

    def step_preview_shot() -> None:
        for child in root.winfo_children():
            if isinstance(child, tk.Toplevel) and child.winfo_exists():
                grab_window(child, "screenshot-preview.png")
                try:
                    child.destroy()
                except tk.TclError:
                    pass
        root.after(300, step_ai_mode)

    def step_ai_mode() -> None:
        app._on_mode_change("ai")
        app.mode_segment.set_active("ai")
        root.update()
        left, top, right, bottom = window_rect(root)
        width, height = right - left, bottom - top
        app.ai_region = (
            left + int(width * 0.07),
            top + int(height * 0.12),
            int(width * 0.52),
            int(height * 0.40),
        )
        app._update_ai_region_label()
        app.var_ai_base_url.set("https://open.bigmodel.cn/api/paas/v4")
        app.var_ai_model.set("glm-4v-flash")
        app.var_ai_api_key.set("sk-demo-key-for-screenshot")
        app.var_ai_option_wait.set("400")
        app.var_ai_next_wait.set("1500")
        app.var_ai_count.set("14")
        app.var_ai_delay.set("1.0")
        for idx, letter in enumerate(("A", "B", "C", "D")):
            var_x, var_y = app.var_ai_opt[letter]
            var_x.set(str(left + int(width * 0.12)))
            var_y.set(str(top + int(height * (0.50 + idx * 0.065))))
        app.var_ai_next_x.set(str(left + int(width * 0.86)))
        app.var_ai_next_y.set(str(top + int(height * 0.88)))
        root.update()
        root.after(500, step_ai_shot)

    def step_ai_shot() -> None:
        grab_window(root, "screenshot-ai.png")
        app.scroll.scroll_to_bottom()
        root.after(400, step_ai_config_shot)

    def step_ai_config_shot() -> None:
        grab_window(root, "screenshot-ai-config.png")
        app.scroll.scroll_to_top()
        root.update()
        answer = AIAnswer(
            letter="B",
            raw="推理：题目中 20% 的增产可多养活约 7000 万人，对应选项 B。\n答案：B",
            elapsed_ms=1234.0,
        )
        result = {
            "png": render_sample_question_png(),
            "answer": answer,
            "width": 900,
            "height": 620,
            "model": "glm-4v-flash",
            "api": "https://open.bigmodel.cn/api/paas/v4",
        }
        state["preview"] = AIResultPreview(root, result)
        root.after(900, step_ai_preview_shot)

    def step_ai_preview_shot() -> None:
        preview = state.get("preview")
        if preview is not None and preview.window.winfo_exists():
            grab_window(preview.window, "screenshot-ai-preview.png")
            try:
                preview.window.destroy()
            except tk.TclError:
                pass
        else:
            print("WARNING: AI preview window not found")
        root.after(300, step_run)

    def step_run() -> None:
        app.mode_segment.set_active("fixed")
        app._on_mode_change("fixed")
        root.update()
        left, top, right, bottom = window_rect(root)
        app.var_x.set(str(left + int((right - left) * 0.83)))
        app.var_y.set(str(top + int((bottom - top) * 0.80)))
        app.var_interval.set("500")
        app.var_random.set(False)
        app.var_count.set("0")
        app.var_delay.set("0.3")
        app.var_restore.set(True)
        app._on_start()
        root.after(1200, step_running_shot)

    def step_running_shot() -> None:
        grab_window(root, "screenshot-running.png")
        root.after(300, step_stop)

    def step_stop() -> None:
        try:
            app._on_stop()
        except Exception:  # noqa: BLE001
            pass
        root.after(1200, shutdown)

    def shutdown() -> None:
        print("clicks:", app.tile_clicks.get_value(), "| state:", app.state)
        try:
            app._shutdown()
        except Exception:  # noqa: BLE001
            pass
        root.after(100, root.destroy)

    root.after(400, step_main)
    root.mainloop()
    print("done")


if __name__ == "__main__":
    main()
