"""模板匹配模块的单元测试（纯合成图像，不依赖真实屏幕）。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.matcher import check_template, find_template, quantize  # noqa: E402


def make_screen(w: int = 640, h: int = 480) -> bytes:
    """生成一张带纹理的确定性灰度图。"""
    buf = bytearray(w * h)
    for y in range(h):
        base = (y * 3) % 64
        for x in range(w):
            buf[y * w + x] = (base + (x * 7) % 96 + ((x * y) % 23)) & 0xFF
    # 添加一些"UI 元素"
    def rect(x0, y0, rw, rh, value):
        for yy in range(y0, y0 + rh):
            if 0 <= yy < h:
                start = yy * w + max(0, x0)
                end = yy * w + min(w, x0 + rw)
                for xx in range(start, end):
                    buf[xx] = value

    rect(60, 50, 180, 60, 15)
    rect(64, 54, 172, 52, 230)
    rect(64, 54, 172, 8, 15)
    rect(300, 120, 240, 90, 10)
    rect(310, 130, 60, 60, 200)
    rect(400, 140, 120, 30, 90)
    rect(120, 300, 400, 100, 40)
    for i in range(120, 520, 40):
        rect(i, 320, 30, 60, 220)
    return bytes(buf)


class MatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sw, self.sh = 640, 480
        self.screen = make_screen(self.sw, self.sh)
        # 裁剪一块带明显边缘/形状的区域作为模板
        self.tx, self.ty, self.tw, self.th = 120, 300, 400, 100
        self.tpl = b"".join(
            self.screen[(self.ty + r) * self.sw + self.tx : (self.ty + r) * self.sw + self.tx + self.tw]
            for r in range(self.th)
        )

    def crop_screen(self, x: int, y: int, w: int, h: int) -> bytes:
        return b"".join(
            self.screen[(y + r) * self.sw + x : (y + r) * self.sw + x + w] for r in range(h)
        )

    def test_exact_match(self) -> None:
        result = find_template(
            self.screen, self.sw, self.sh, self.tpl, self.tw, self.th, threshold=0.9
        )
        self.assertIsNotNone(result)
        self.assertEqual((result.x, result.y), (self.tx, self.ty))
        self.assertGreaterEqual(result.score, 0.999)

    def test_not_found(self) -> None:
        other = self.crop_screen(300, 120, 240, 90)
        result = find_template(
            self.screen, self.sw, self.sh, other, 240, 90, region=(0, 0, 200, 200), threshold=0.95
        )
        self.assertIsNone(result)

    def test_region_limits_search(self) -> None:
        region = (100, 280, 440, 240)
        result = find_template(
            self.screen, self.sw, self.sh, self.tpl, self.tw, self.th, region=region, threshold=0.9
        )
        self.assertIsNotNone(result)
        self.assertEqual((result.x, result.y), (self.tx, self.ty))

        result2 = find_template(
            self.screen, self.sw, self.sh, self.tpl, self.tw, self.th, region=(0, 0, 300, 250), threshold=0.9
        )
        self.assertIsNone(result2)

    def test_noisy_template_still_matches(self) -> None:
        noisy = bytearray(self.tpl)
        for i in range(0, len(noisy), 97):
            noisy[i] = (noisy[i] + 6) & 0xFF
        result = find_template(
            self.screen, self.sw, self.sh, bytes(noisy), self.tw, self.th, threshold=0.85
        )
        self.assertIsNotNone(result)
        self.assertEqual((result.x, result.y), (self.tx, self.ty))

    def test_screen_change_outside_template(self) -> None:
        screen = bytearray(self.screen)
        for y in range(400, 460):
            for x in range(0, 640):
                screen[y * self.sw + x] = (screen[y * self.sw + x] + 99) & 0xFF
        result = find_template(
            bytes(screen), self.sw, self.sh, self.tpl, self.tw, self.th, threshold=0.9
        )
        self.assertIsNotNone(result)
        self.assertEqual((result.x, result.y), (self.tx, self.ty))

    def test_flat_template_rejected(self) -> None:
        flat = bytes([128]) * (80 * 40)
        quality = check_template(flat, 80, 40)
        self.assertFalse(quality.ok)
        self.assertIsNone(find_template(self.screen, self.sw, self.sh, flat, 80, 40))

    def test_quantize(self) -> None:
        self.assertEqual(quantize(b"\x00\x0f\x10\xff"), b"\x00\x00\x10\xf0")

    def test_quality_of_good_template(self) -> None:
        quality = check_template(self.tpl, self.tw, self.th)
        self.assertTrue(quality.ok, quality.message)
        self.assertGreaterEqual(quality.levels, 2)


if __name__ == "__main__":
    unittest.main()
