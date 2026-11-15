"""全屏分块识别：分块区域计算测试（不抓屏、不调 API）。"""
from __future__ import annotations

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import ai_client  # noqa: E402
from app.controllers import pc_controller  # noqa: E402
from app.controllers.pc_controller import PCController  # noqa: E402


class PageTilesTest(unittest.TestCase):
    def test_full_screen_splits_into_four_overlapping_tiles(self) -> None:
        region = (0, 0, 2560, 1440)
        tiles = PCController._page_tiles(region, True)
        self.assertEqual(len(tiles), 4)
        for x, y, w, h in tiles:
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + w, 2560)
            self.assertLessEqual(y + h, 1440)
            self.assertGreater(w, 0)
            self.assertGreater(h, 0)
        # 四块要覆盖全屏（右上、左下、右下都有）
        rights = [x + w for x, _y, w, _h in tiles]
        bottoms = [y + h for _x, y, _w, h in tiles]
        self.assertEqual(max(rights), 2560)
        self.assertEqual(max(bottoms), 1440)
        # 相邻块有重叠，避免题目被切断
        self.assertLess(tiles[0][0] + tiles[0][2], 2560)
        self.assertLess(tiles[0][1] + tiles[0][3], 1440)

    def test_small_region_kept_as_single_tile(self) -> None:
        region = (100, 100, 800, 600)
        self.assertEqual(PCController._page_tiles(region, True), [region])

    def test_disabled_keeps_single_tile(self) -> None:
        region = (0, 0, 2560, 1440)
        self.assertEqual(PCController._page_tiles(region, False), [region])

    def test_tiled_merge_maps_points_and_dedupes_keys(self) -> None:
        calls: list[int] = []
        original_ask = pc_controller.ai_client.ask_page
        original_png = pc_controller.png.encode_png

        def fake_ask(png_bytes, ai_config):
            index = len(calls)
            calls.append(index)
            # 前两块模拟重叠区域里的同一道题（key 相同）
            key = "重叠题" if index < 2 else f"题目{index}"
            question = ai_client.PageQuestion(
                index=index + 1,
                letter="B",
                norm_points={"B": (0.5, 0.5)},
                letters=["B"],
                key=key,
            )
            return ai_client.PageAnswer(questions=[question], raw="", elapsed_ms=1.0)

        class FakeGrabber:
            vx = 0
            vy = 0

            def crop_rgb(self, x, y, w, h):
                return b""

        pc_controller.ai_client.ask_page = fake_ask
        pc_controller.png.encode_png = lambda w, h, rgb: b"png"
        try:
            controller = PCController(lambda event: None)
            cfg = types.SimpleNamespace(ai_config=object())
            page, abs_points, next_click = controller._ai_page_tiled(
                cfg, FakeGrabber(), (0, 0, 2560, 1440)
            )
        finally:
            pc_controller.ai_client.ask_page = original_ask
            pc_controller.png.encode_png = original_png
        self.assertEqual(len(calls), 4)
        # 第一块和第二块 key 相同，第二块被去重
        self.assertEqual(len(page.questions), 3)
        self.assertEqual(len(abs_points), 3)
        self.assertTrue(all("B" in mapping for mapping in abs_points))
        self.assertIsNone(next_click)

    def test_tile_error_emits_log_without_crashing(self) -> None:
        events: list[dict] = []
        calls: list[int] = []
        original_ask = pc_controller.ai_client.ask_page
        original_png = pc_controller.png.encode_png

        def fake_ask(png_bytes, ai_config):
            index = len(calls)
            calls.append(index)
            if index == 0:
                raise OSError("模拟远端断开")
            question = ai_client.PageQuestion(
                index=index + 1,
                letter="A",
                norm_points={"A": (0.2, 0.2)},
                letters=["A"],
                key=f"题{index}",
            )
            return ai_client.PageAnswer(questions=[question], raw="", elapsed_ms=1.0)

        class FakeGrabber:
            vx = 0
            vy = 0

            def crop_rgb(self, x, y, w, h):
                return b""

        pc_controller.ai_client.ask_page = fake_ask
        pc_controller.png.encode_png = lambda w, h, rgb: b"png"
        try:
            controller = PCController(lambda event: events.append(event))
            cfg = types.SimpleNamespace(ai_config=object())
            page, _points, _next = controller._ai_page_tiled(
                cfg, FakeGrabber(), (0, 0, 2560, 1440)
            )
        finally:
            pc_controller.ai_client.ask_page = original_ask
            pc_controller.png.encode_png = original_png
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(page.questions), 3)
        self.assertTrue(any("分块识别失败" in event.get("message", "") for event in events))


if __name__ == "__main__":
    unittest.main()
