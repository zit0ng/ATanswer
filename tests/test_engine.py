"""点击引擎的单元测试（使用伪造的点击/匹配回调，不产生真实点击）。"""
from __future__ import annotations

import os
import queue
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine import ClickConfig, ClickEngine, validate_config  # noqa: E402


class FakeMatch:
    def __init__(self, center: tuple[int, int], score: float = 0.97) -> None:
        self.center = center
        self.score = score
        self.elapsed_ms = 3.2


class EngineTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.events: queue.Queue[dict] = queue.Queue()
        self.clicks: list[tuple] = []

    def click_fn(self, x, y, button, double, restore) -> bool:
        self.clicks.append((x, y, button, double, restore))
        return True

    def make_engine(self, match_fn=None) -> ClickEngine:
        return ClickEngine(
            click_fn=self.click_fn,
            match_fn=match_fn or (lambda: None),
            emit=self.events.put,
        )

    def drain_until(self, predicate, timeout: float = 5.0) -> list[dict]:
        collected: list[dict] = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                event = self.events.get(timeout=0.05)
            except queue.Empty:
                continue
            collected.append(event)
            if predicate(event):
                return collected
        self.fail(f"等待事件超时，已收到 {collected[-5:]}")

    def wait_until(self, predicate, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("等待条件超时")


class FixedModeTest(EngineTestBase):
    def test_clicks_exact_count(self) -> None:
        engine = self.make_engine()
        cfg = ClickConfig(
            mode="fixed",
            x=123,
            y=456,
            interval_ms=10,
            count_limit=5,
            start_delay_ms=0,
            double_click=True,
            button="right",
        )
        self.assertTrue(engine.start(cfg))
        self.drain_until(lambda e: e["kind"] == "finished", timeout=5)
        self.assertEqual(len(self.clicks), 5)
        self.assertEqual(self.clicks[0], (123, 456, "right", True, False))
        self.assertFalse(engine.is_running)

    def test_infinite_and_stop(self) -> None:
        engine = self.make_engine()
        cfg = ClickConfig(mode="fixed", x=1, y=2, interval_ms=10, count_limit=0, start_delay_ms=0)
        self.assertTrue(engine.start(cfg))
        self.wait_until(lambda: len(self.clicks) >= 3, timeout=3)
        engine.stop()
        count = len(self.clicks)
        time.sleep(0.15)
        self.assertLessEqual(len(self.clicks), count + 1)

    def test_pause_and_resume(self) -> None:
        engine = self.make_engine()
        cfg = ClickConfig(mode="fixed", x=9, y=9, interval_ms=20, count_limit=8, start_delay_ms=0)
        self.assertTrue(engine.start(cfg))
        self.wait_until(lambda: len(self.clicks) >= 2, timeout=3)
        engine.pause()
        time.sleep(0.08)
        paused_count = len(self.clicks)
        time.sleep(0.25)
        self.assertEqual(len(self.clicks), paused_count, "暂停期间不应继续点击")
        engine.resume()
        self.drain_until(lambda e: e["kind"] == "finished", timeout=5)
        self.assertEqual(len(self.clicks), 8)

    def test_click_error_stops_engine(self) -> None:
        def failing_click(*args, **kwargs):
            raise RuntimeError("模拟点击失败")

        engine = ClickEngine(failing_click, lambda: None, self.events.put)
        cfg = ClickConfig(mode="fixed", x=0, y=0, interval_ms=10, count_limit=0, start_delay_ms=0)
        self.assertTrue(engine.start(cfg))
        events = self.drain_until(lambda e: e["kind"] == "finished", timeout=8)
        finished = events[-1]
        self.assertEqual(finished["reason"], "error")
        self.assertFalse(engine.is_running)


class ImageModeTest(EngineTestBase):
    def test_click_at_match_center_with_offset(self) -> None:
        matches = [FakeMatch((100, 200)), FakeMatch((110, 210)), None]
        iterator = iter(matches)
        state = {"done": False}

        def match_fn():
            try:
                return next(iterator)
            except StopIteration:
                return None

        engine = self.make_engine(match_fn)
        cfg = ClickConfig(
            mode="image",
            count_limit=2,
            start_delay_ms=0,
            poll_ms=50,
            click_offset_x=5,
            click_offset_y=-5,
            template=object(),
        )
        self.assertTrue(engine.start(cfg))
        self.drain_until(lambda e: e["kind"] == "finished", timeout=5)
        self.assertEqual(len(self.clicks), 2)
        self.assertEqual(self.clicks[0][:2], (105, 195))
        self.assertEqual(self.clicks[1][:2], (115, 205))

    def test_no_match_then_stop(self) -> None:
        engine = self.make_engine(lambda: None)
        cfg = ClickConfig(mode="image", poll_ms=50, start_delay_ms=0, template=object())
        self.assertTrue(engine.start(cfg))
        self.wait_until(lambda: not engine.is_running or True, timeout=0.1)
        time.sleep(0.25)
        self.assertEqual(len(self.clicks), 0)
        engine.stop()
        self.assertFalse(engine.is_running)


class ValidateConfigTest(unittest.TestCase):
    def test_invalid_values(self) -> None:
        self.assertFalse(validate_config(ClickConfig(interval_ms=5))[0])
        self.assertFalse(validate_config(ClickConfig(count_limit=-1))[0])
        self.assertFalse(
            validate_config(ClickConfig(random_interval=True, interval_min_ms=100, interval_max_ms=50))[0]
        )
        self.assertFalse(validate_config(ClickConfig(mode="image"))[0])
        self.assertTrue(validate_config(ClickConfig(mode="fixed"))[0])
        self.assertTrue(
            validate_config(ClickConfig(mode="image", template=object(), threshold=0.9))[0]
        )


if __name__ == "__main__":
    unittest.main()
