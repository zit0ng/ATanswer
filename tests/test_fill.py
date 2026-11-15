"""填空题：AI 解析 + 单次填空点击/输入流程的测试（不真实敲键盘）。"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import ai_client, winapi  # noqa: E402
from app.controllers import pc_controller  # noqa: E402
from tools.mock_ai_server import start_mock_ai_server, stop_mock_ai_server  # noqa: E402

winapi.enable_dpi_awareness()


class FillClientTest(unittest.TestCase):
    def _config(self, content: str):
        server, base_url = start_mock_ai_server(content=content)
        self.addCleanup(lambda: stop_mock_ai_server(server))
        return ai_client.AIConfig(
            base_url=base_url, api_key="sk-test", model="mock-vl", timeout_s=10
        )

    def test_ask_fill_valid(self) -> None:
        content = (
            '{"items":[{"index":1,"slot":1,"answer":"北京","x":0.40,"y":0.25},'
            '{"index":1,"slot":2,"answer":"上海","x":0.42,"y":0.35},'
            '{"index":2,"answer":"4","x":0.42,"y":0.55}],'
            '"next":{"x":0.90,"y":0.94}}'
        )
        answer = ai_client.ask_fill(b"fake-png", self._config(content))
        self.assertEqual(len(answer.items), 3)
        self.assertEqual(answer.items[0].index, 1)
        self.assertEqual(answer.items[0].slot, 1)
        self.assertEqual(answer.items[0].answer, "北京")
        self.assertEqual(answer.items[1].slot, 2)
        self.assertEqual(answer.items[0].norm_point, (0.40, 0.25))
        self.assertEqual(answer.items[2].answer, "4")
        self.assertEqual(answer.norm_next, (0.90, 0.94))

    def test_ask_fill_overwrite_prompt(self) -> None:
        server, base_url = start_mock_ai_server(content='{"items":[]}')
        self.addCleanup(lambda: stop_mock_ai_server(server))
        config = ai_client.AIConfig(
            base_url=base_url, api_key="sk-test", model="mock-vl", timeout_s=10
        )
        ai_client.ask_fill(b"fake-png", config, overwrite=True)
        body = server.requests[0]["body"].decode("utf-8")
        self.assertIn("即使某个空已经有内容", body)

    def test_ask_fill_skips_empty_answer(self) -> None:
        content = (
            '{"items":[{"index":1,"answer":"","x":0.1,"y":0.2},'
            '{"index":2,"answer":"答案二"}]}'
        )
        answer = ai_client.ask_fill(b"fake-png", self._config(content))
        self.assertEqual(len(answer.items), 1)
        self.assertEqual(answer.items[0].index, 2)
        self.assertIsNone(answer.items[0].norm_point)

    def test_ask_fill_invalid_json(self) -> None:
        with self.assertRaises(ai_client.AIError):
            ai_client.ask_fill(b"fake-png", self._config("不是 JSON"))


class FillOnceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self.filled: list[str] = []
        self.typed: list[str] = []
        self._original_click = winapi.click_at
        self._original_fill = winapi.fill_textbox
        self._original_typing = winapi.fill_textbox_typing

        def fake_click(x, y, button="left", double=False, restore_cursor=True):
            self.clicks.append((int(x), int(y)))
            return True

        def fake_fill(text):
            self.filled.append(str(text))
            return True

        def fake_typing(text):
            self.typed.append(str(text))
            return True

        pc_controller.winapi.click_at = fake_click
        pc_controller.winapi.fill_textbox = fake_fill
        pc_controller.winapi.fill_textbox_typing = fake_typing

    def tearDown(self) -> None:
        pc_controller.winapi.click_at = self._original_click
        pc_controller.winapi.fill_textbox = self._original_fill
        pc_controller.winapi.fill_textbox_typing = self._original_typing

    def _config(self, content: str):
        server, base_url = start_mock_ai_server(content=content)
        self.addCleanup(lambda: stop_mock_ai_server(server))
        return ai_client.AIConfig(
            base_url=base_url, api_key="sk-test", model="mock-vl", timeout_s=10
        )

    def test_fill_once_clicks_and_types(self) -> None:
        content = (
            '{"items":[{"index":1,"answer":"北京","x":0.10,"y":0.20},'
            '{"index":2,"answer":"4","x":0.20,"y":0.60},'
            '{"index":3,"answer":"没有输入框"}],'
            '"next":{"x":0.90,"y":0.90}}'
        )
        controller = pc_controller.PCController(lambda event: None)
        result = controller.fill_once(
            (0, 0, 200, 100),
            self._config(content),
            auto_next=True,
            fill_wait_ms=0,
            next_wait_ms=0,
        )
        self.assertEqual(self.typed, ["北京", "4"])
        self.assertEqual(self.filled, [])
        self.assertEqual(self.clicks, [(20, 20), (40, 60), (180, 90)])
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["items"][0]["index"], 1)
        self.assertTrue(result["next"])

    def test_fill_once_no_auto_next(self) -> None:
        content = '{"items":[{"index":1,"answer":"测试","x":0.20,"y":0.40}]}'
        controller = pc_controller.PCController(lambda event: None)
        result = controller.fill_once(
            (0, 0, 200, 100),
            self._config(content),
            auto_next=False,
            fill_wait_ms=0,
        )
        self.assertEqual(self.typed, ["测试"])
        self.assertEqual(self.clicks, [(40, 40)])
        self.assertFalse(result["next"])

    def test_fill_click_offset(self) -> None:
        content = '{"items":[{"index":1,"answer":"测试","x":0.50,"y":0.50}]}'
        controller = pc_controller.PCController(lambda event: None)
        controller.fill_once(
            (0, 0, 200, 100),
            self._config(content),
            auto_next=False,
            fill_wait_ms=0,
            offset_y=-12,
        )
        self.assertEqual(self.typed, ["测试"])
        self.assertEqual(self.clicks, [(100, 38)])


if __name__ == "__main__":
    unittest.main()
