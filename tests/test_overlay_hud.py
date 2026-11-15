"""屏幕选区框（RegionHUD）的冒烟测试：显示/隐藏/销毁不应崩溃。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import winapi  # noqa: E402


@unittest.skipUnless(sys.platform.startswith("win"), "仅 Windows")
class RegionHUDTest(unittest.TestCase):
    def setUp(self) -> None:
        import tkinter as tk

        from app.overlay import RegionHUD

        winapi.enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.withdraw()
        self.hud = RegionHUD(self.root)

    def tearDown(self) -> None:
        try:
            self.hud.destroy()
        finally:
            try:
                self.root.destroy()
            except Exception:
                pass

    def test_show_hide_destroy(self) -> None:
        self.assertFalse(self.hud.is_visible())
        self.hud.show((100, 120, 500, 320), "题目区域")
        self.assertTrue(self.hud.is_visible())
        self.hud.show((200, 220, 300, 200), "题目区域")
        self.assertTrue(self.hud.is_visible())
        self.hud.hide()
        self.assertFalse(self.hud.is_visible())
        self.hud.show((0, 0, 100, 100), "搜索区域")
        self.assertTrue(self.hud.is_visible())


if __name__ == "__main__":
    unittest.main()
