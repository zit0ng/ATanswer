"""SplitButton（左/右双段按钮）的单元测试。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import winapi  # noqa: E402


class SplitButtonTest(unittest.TestCase):
    def setUp(self) -> None:
        import tkinter as tk

        from app.widgets import SplitButton

        winapi.enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.withdraw()
        self.hits: list[str] = []
        self.btn = SplitButton(
            self.root,
            left_text="单次答题",
            right_text="单次填空",
            left_command=lambda: self.hits.append("left"),
            right_command=lambda: self.hits.append("right"),
        )

    def tearDown(self) -> None:
        try:
            self.btn.destroy()
        finally:
            try:
                self.root.destroy()
            except Exception:
                pass

    def test_invoke_halves(self) -> None:
        self.assertTrue(self.btn.is_enabled())
        self.btn.invoke_side("left")
        self.btn.invoke_side("right")
        self.assertEqual(self.hits, ["left", "right"])

    def test_disabled_ignores_invoke(self) -> None:
        self.btn.set_enabled(False)
        self.assertFalse(self.btn.is_enabled())
        self.btn.invoke_side("left")
        self.assertEqual(self.hits, [])
        self.btn.set_enabled(True)
        self.btn.invoke_side("right")
        self.assertEqual(self.hits, ["right"])

    def test_set_texts(self) -> None:
        self.btn.set_texts("答题", "填空")
        self.assertEqual(self.btn._left_text, "答题")
        self.assertEqual(self.btn._right_text, "填空")


if __name__ == "__main__":
    unittest.main()
