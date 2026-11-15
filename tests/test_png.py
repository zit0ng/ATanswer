"""PNG 编解码测试。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import png  # noqa: E402


class PngTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        width, height = 37, 23
        rgb = bytes((i * 7 + 3) % 256 for i in range(width * height * 3))
        data = png.encode_png(width, height, rgb)
        w, h, decoded = png.decode_png(data)
        self.assertEqual((w, h), (width, height))
        self.assertEqual(decoded, rgb)

    def test_save_and_load(self) -> None:
        width, height = 16, 16
        rgb = bytes([10, 20, 30] * (width * height))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.png")
            png.save_png(path, width, height, rgb)
            with open(path, "rb") as fp:
                w, h, decoded = png.decode_png(fp.read())
        self.assertEqual((w, h), (width, height))
        self.assertEqual(decoded, rgb)

    def test_invalid_length(self) -> None:
        with self.assertRaises(ValueError):
            png.encode_png(10, 10, b"\x00" * 10)

    def test_decode_garbage(self) -> None:
        with self.assertRaises(ValueError):
            png.decode_png(b"not a png")


if __name__ == "__main__":
    unittest.main()
