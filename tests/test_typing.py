"""逐字录入逻辑测试：不依赖真实键盘，mock 掉 Win32 SendInput。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import typing, winapi  # noqa: E402


class UnicodeTypingTest(unittest.TestCase):
    def test_types_each_character_and_reports_progress(self) -> None:
        sent: list[tuple[int, int]] = []
        progress: list[tuple[int, int]] = []

        def fake_send(scan=0, vk=0, flags=0):
            sent.append((scan, flags))
            return 1

        with mock.patch.object(winapi, "_send_keyboard", side_effect=fake_send), \
                mock.patch.object(winapi, "_tap_key") as tap, \
                mock.patch.object(winapi.time, "sleep"):
            ok = winapi.type_unicode_text(
                "A中\n", interval=0.0,
                on_progress=lambda done, total: progress.append((done, total)),
            )
        self.assertTrue(ok)
        self.assertEqual(tap.call_count, 1)
        self.assertEqual(progress, [(1, 3), (2, 3), (3, 3)])
        # 两个普通字符各按下+抬起；换行由 _tap_key 处理
        self.assertEqual(len(sent), 4)

    def test_stop_callback_interrupts_between_characters(self) -> None:
        typed: list[int] = []
        state = {"stop": False}

        def progress(done, total):
            typed.append(done)
            if done == 1:
                state["stop"] = True

        with mock.patch.object(winapi, "_send_keyboard", return_value=1), \
                mock.patch.object(winapi, "_tap_key"), \
                mock.patch.object(winapi.time, "sleep"):
            ok = winapi.type_unicode_text(
                "abcdef", interval=0.0,
                should_stop=lambda: state["stop"],
                on_progress=progress,
            )
        self.assertFalse(ok)
        self.assertEqual(typed, [1])


class TypeTextTest(unittest.TestCase):
    def test_replace_select_all_and_type(self) -> None:
        calls: list[str] = []
        typed_text: list[str] = []

        def fake_select_all():
            calls.append("select_all")
            return True

        def fake_type(text, interval=0.008, should_stop=None, on_progress=None):
            typed_text.append(text)
            if on_progress:
                on_progress(len(text), len(text))
            return True

        with mock.patch.object(winapi, "select_all", side_effect=fake_select_all), \
                mock.patch.object(winapi, "type_unicode_text", side_effect=fake_type), \
                mock.patch.object(typing.time, "sleep"):
            result = typing.type_text(
                typing.TypingOptions(
                    text="你好\r\n世界", interval_ms=10, start_delay_ms=0,
                    replace_existing=True, press_enter=True,
                )
            )
        self.assertTrue(result.completed)
        self.assertEqual(calls, ["select_all"])
        self.assertEqual(typed_text[0], "你好\n世界")
        self.assertEqual(typed_text[-1], "\n")
        self.assertEqual(result.typed_chars, 5)
        self.assertEqual(result.total_chars, 5)

    def test_stop_before_delay_does_not_type(self) -> None:
        with mock.patch.object(winapi, "select_all") as select_all, \
                mock.patch.object(winapi, "type_unicode_text") as type_text, \
                mock.patch.object(typing.time, "sleep"):
            result = typing.type_text(
                typing.TypingOptions(text="hello", start_delay_ms=1000),
                should_stop=lambda: True,
            )
        self.assertTrue(result.stopped)
        self.assertEqual(result.typed_chars, 0)
        select_all.assert_not_called()
        type_text.assert_not_called()

    def test_empty_text_is_ignored(self) -> None:
        with mock.patch.object(winapi, "type_unicode_text") as type_text:
            result = typing.type_text(typing.TypingOptions(text=""))
        self.assertFalse(result.completed)
        self.assertEqual(result.total_chars, 0)
        type_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
