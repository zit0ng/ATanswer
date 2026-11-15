"""生成只包含 ATanswer 窗口的文档截图（不包含桌面/其它窗口）。"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab  # noqa: E402

from app import winapi  # noqa: E402
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


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def client_bbox(window) -> tuple[int, int, int, int]:
    hwnd = winapi.get_toplevel_hwnd(window)
    rect = RECT()
    ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect))
    point = POINT(0, 0)
    ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(point))
    return point.x, point.y, point.x + rect.right, point.y + rect.bottom


def grab(window, filename: str) -> None:
    window.update_idletasks()
    window.update()
    try:
        window.attributes("-alpha", 1.0)
        window.attributes("-topmost", True)
        window.lift()
    except Exception:
        pass
    time.sleep(0.25)
    window.update()
    bbox = client_bbox(window)
    image = ImageGrab.grab(bbox=bbox)
    path = DOCS / filename
    image.save(path)
    print("saved", path.name, image.size)


def main() -> None:
    app = MainApp()
    root = app.root
    root.attributes("-alpha", 1.0)
    root.attributes("-topmost", True)
    root.update()
    time.sleep(0.4)

    grab(root, "screenshot-main.png")
    grab(root, "screenshot-image.png")
    grab(root, "screenshot-running.png")

    app._on_text_typing()
    dialog = app._typing_dialog
    if dialog is not None and dialog.winfo_exists():
        dialog.text.insert("1.0", "这是逐字录入示例：程序会把这行字逐个输入目标文本框。")
        dialog.update()
        grab(dialog, "screenshot-typing.png")
        dialog._on_close()
    root.update()

    app._toggle_advanced()
    root.update()
    adv = app.advanced_win
    adv.attributes("-alpha", 1.0)
    adv.update()
    grab(adv, "screenshot-ai.png")
    grab(adv, "screenshot-ai-config.png")
    grab(adv, "screenshot-ai-detect.png")
    try:
        app.scroll.scroll_to_bottom()
        adv.update()
        grab(adv, "screenshot-ai-preview.png")
    except Exception:
        pass
    app._toggle_advanced()
    root.update()

    app._toggle_monitor()
    root.update()
    mon = app.monitor_win
    mon.attributes("-alpha", 1.0)
    mon.update()
    grab(mon, "screenshot-preview.png")
    grab(mon, "screenshot-region-hud.png")
    app._toggle_monitor()
    root.update()

    app._on_browser_hybrid()
    root.update()
    hybrid = app._browser_hybrid_win
    if hybrid is not None and hybrid.winfo_exists():
        grab(hybrid, "screenshot-browser-hybrid.png")
        hybrid.destroy()
    root.update()

    try:
        app._shutdown()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    main()
