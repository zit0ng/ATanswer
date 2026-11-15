"""设置持久化测试。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.settings import Settings, load_settings, save_settings  # noqa: E402


class SettingsTest(unittest.TestCase):
    def test_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            settings = load_settings(path)
            self.assertEqual(settings.mode, "fixed")
            self.assertEqual(settings.interval_ms, 1000)

    def test_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            settings = Settings(mode="image", threshold=85, region=[10, 20, 300, 200])
            self.assertTrue(save_settings(settings, path))
            loaded = load_settings(path)
            self.assertEqual(loaded.mode, "image")
            self.assertEqual(loaded.threshold, 85)
            self.assertEqual(loaded.region, [10, 20, 300, 200])

    def test_ai_fields_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            settings = Settings(
                mode="ai",
                ai_base_url="https://example.com/v1",
                ai_api_key="sk-test",
                ai_model="mock-vl",
                ai_timeout_s=30,
                ai_region=[100, 200, 800, 600],
                ai_options={"A": [10, 20], "B": [30, 40]},
                ai_next_point=[500, 700],
                ai_option_wait_ms=300,
                ai_next_wait_ms=1200,
                ai_delay_ms=800,
                ai_count_limit=14,
            )
            self.assertTrue(save_settings(settings, path))
            loaded = load_settings(path)
            self.assertEqual(loaded.mode, "ai")
            self.assertEqual(loaded.ai_api_key, "sk-test")
            self.assertEqual(loaded.ai_options["B"], [30, 40])
            self.assertEqual(loaded.ai_region, [100, 200, 800, 600])
            self.assertEqual(loaded.ai_next_point, [500, 700])
            self.assertEqual(loaded.ai_count_limit, 14)

    def test_ai_fields_bad_types(self) -> None:
        settings = Settings.from_dict(
            {"ai_options": {"a": [1, 2], "Z": [3, 4], "B": [5]}, "ai_next_point": [1, 2, 3]}
        )
        self.assertEqual(settings.ai_options, {"A": [1, 2]})
        self.assertEqual(settings.ai_next_point, [0, 0])

    def test_normalize(self) -> None:
        settings = Settings(mode="bad", button="x", interval_ms=-5, threshold=200, poll_ms=1)
        settings.normalize()
        self.assertEqual(settings.mode, "fixed")
        self.assertEqual(settings.button, "left")
        self.assertGreaterEqual(settings.interval_ms, 10)
        self.assertEqual(settings.threshold, 100)
        self.assertGreaterEqual(settings.poll_ms, 50)

    def test_bad_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            with open(path, "w", encoding="utf-8") as fp:
                fp.write("{not json")
            settings = load_settings(path)
            self.assertEqual(settings.mode, "fixed")


if __name__ == "__main__":
    unittest.main()
