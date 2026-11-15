"""单次答题（框选一次→AI识别→点击一次）的单元测试。

用本地 mock AI 提供答案与坐标，并替换 winapi.click_at 记录点击位置，
不依赖前台窗口，也不会真的移动鼠标。
"""
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


class AnswerOnceTest(unittest.TestCase):
    def _config(self, content: str):
        server, base_url = start_mock_ai_server(content=content)
        self.addCleanup(lambda: stop_mock_ai_server(server))
        return ai_client.AIConfig(
            base_url=base_url, api_key="sk-test", model="mock-vl", timeout_s=10
        )

    def setUp(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self._original_click = winapi.click_at

        def fake_click(x, y, button="left", double=False, restore_cursor=True):
            self.clicks.append((int(x), int(y)))
            return True

        pc_controller.winapi.click_at = fake_click

    def tearDown(self) -> None:
        pc_controller.winapi.click_at = self._original_click

    def test_single_question_multi_select(self) -> None:
        content = (
            '{"type":"multiple","answers":["A","C"],"options":['
            '{"letter":"A","x":0.10,"y":0.20},{"letter":"B","x":0.10,"y":0.40},'
            '{"letter":"C","x":0.20,"y":0.60},{"letter":"D","x":0.20,"y":0.80}],'
            '"next":{"x":0.90,"y":0.90}}'
        )
        cfg = self._config(content)
        controller = pc_controller.PCController(lambda event: None)
        result = controller.answer_once(
            (0, 0, 200, 100),
            cfg,
            page_mode=False,
            realtime_layout=True,
            auto_next=True,
            option_wait_ms=0,
            next_wait_ms=0,
        )
        self.assertEqual(result["answers"], ["A、C"])
        self.assertEqual(result["clicks"], 2)
        self.assertTrue(result["next"])
        self.assertEqual(self.clicks, [(20, 20), (40, 60), (180, 90)])

    def test_page_mode_single_and_multi(self) -> None:
        content = (
            '{"questions":['
            '{"index":1,"answers":["B"],"options":['
            '{"letter":"A","x":0.10,"y":0.10},{"letter":"B","x":0.10,"y":0.20}]},'
            '{"index":2,"answers":["A","C"],"options":['
            '{"letter":"A","x":0.10,"y":0.50},{"letter":"B","x":0.10,"y":0.60},'
            '{"letter":"C","x":0.10,"y":0.70},{"letter":"D","x":0.10,"y":0.80}]}],'
            '"next":{"x":0.90,"y":0.95}}'
        )
        cfg = self._config(content)
        controller = pc_controller.PCController(lambda event: None)
        result = controller.answer_once(
            (0, 0, 200, 100),
            cfg,
            page_mode=True,
            auto_next=True,
            option_wait_ms=0,
            next_wait_ms=0,
        )
        self.assertEqual(result["answers"], ["B", "A、C"])
        self.assertEqual(result["clicks"], 3)
        self.assertTrue(result["next"])
        self.assertEqual(self.clicks, [(20, 20), (20, 50), (20, 70), (180, 95)])

    def test_single_question_without_clickable_points_raises(self) -> None:
        content = '{"answers":["B"],"options":[{"letter":"B","x":0.1,"y":0.2}]}'
        cfg = self._config(content.replace('"x":0.1', '"x":null'))
        controller = pc_controller.PCController(lambda event: None)
        with self.assertRaises(OSError):
            controller.answer_once(
                (0, 0, 200, 100),
                cfg,
                page_mode=False,
                realtime_layout=True,
                auto_next=False,
                option_wait_ms=0,
            )


if __name__ == "__main__":
    unittest.main()
