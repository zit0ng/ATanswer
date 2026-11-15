"""Windows 底层能力测试（抓屏、匹配、热键注册）。

注意：这里不测试真实鼠标点击，避免在自动化环境中产生副作用。
真实点击已在开发阶段用独立探针验证。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import winapi  # noqa: E402
from app.matcher import find_template  # noqa: E402


@unittest.skipUnless(sys.platform.startswith("win"), "仅 Windows")
class WinApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        winapi.enable_dpi_awareness()

    def test_virtual_screen(self) -> None:
        vx, vy, vw, vh = winapi.virtual_screen()
        self.assertGreaterEqual(vw, 800)
        self.assertGreaterEqual(vh, 600)

    def test_capture_sizes(self) -> None:
        with winapi.ScreenGrabber() as grabber:
            grabber.capture()
            gray = grabber.gray()
            rgb = grabber.rgb()
            self.assertEqual(len(gray), grabber.width * grabber.height)
            self.assertEqual(len(rgb), grabber.width * grabber.height * 3)
            crop = grabber.crop_rgb(10, 10, 64, 48)
            self.assertEqual(len(crop), 64 * 48 * 3)

    def test_real_screen_match(self) -> None:
        """在真实屏幕截图上裁剪模板，再在整屏中找回。"""
        from app.matcher import check_template

        with winapi.ScreenGrabber() as grabber:
            grabber.capture()
            gray = grabber.gray()
            width, height = grabber.width, grabber.height
            tw, th = 240, 90
            chosen = None
            for y0 in range(120, height - th - 120, 90):
                for x0 in range(120, width - tw - 120, 160):
                    candidate = b"".join(
                        gray[(y0 + r) * width + x0 : (y0 + r) * width + x0 + tw]
                        for r in range(th)
                    )
                    quality = check_template(candidate, tw, th)
                    if quality.ok and quality.edge_ratio > 0.02:
                        chosen = (x0, y0, candidate)
                        break
                if chosen:
                    break
            self.assertIsNotNone(chosen, "没有找到适合测试的屏幕区域")
            x0, y0, template = chosen
            result = find_template(gray, width, height, template, tw, th, threshold=0.9)
            self.assertIsNotNone(result)
            self.assertEqual((result.x, result.y), (x0, y0))
            self.assertGreaterEqual(result.score, 0.999)

    def test_dpi_and_admin(self) -> None:
        self.assertGreater(winapi.get_dpi_scale(), 0.5)
        self.assertIsInstance(winapi.is_admin(), bool)

    def test_hotkey_register(self) -> None:
        winapi.create_message_queue()
        vk = 0x87  # F24，几乎不会被占用
        ok = winapi.register_hotkey(99, vk)
        try:
            self.assertTrue(ok, "无法注册测试热键 F24")
        finally:
            winapi.unregister_hotkey(99)


if __name__ == "__main__":
    unittest.main()
