"""自动滚屏答题 / 滚轮注入的单元测试。"""
from __future__ import annotations

import os
import queue
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import winapi  # noqa: E402
from app.engine import ClickConfig, ClickEngine  # noqa: E402


class FakeQuestion:
    def __init__(self, index: int, letters: list[str], key: str = "", answered: bool = False) -> None:
        self.index = index
        self.letters = letters
        self.letter = letters[0] if letters else ""
        self.key = key
        self.answered = answered


class FakePage:
    def __init__(self, questions, abs_points) -> None:
        self.questions = questions
        self.abs_points = abs_points
        self.raw = "{}"
        self.elapsed_ms = 1.0
        self.next_click = None


class AutoScrollEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.events: queue.Queue[dict] = queue.Queue()
        self.clicks: list[tuple[int, int]] = []
        self.scroll_results: list[bool] = []
        self.scroll_calls = 0

    def click_fn(self, x, y, button, double, restore) -> bool:
        self.clicks.append((int(x), int(y)))
        return True

    def baseline_fn(self) -> None:
        pass

    def scroll_fn(self) -> bool:
        self.scroll_calls += 1
        result = self.scroll_results.pop(0) if self.scroll_results else False
        return result

    def config(self, **kwargs) -> ClickConfig:
        base = dict(
            mode="ai",
            ai_config=object(),
            ai_region=(0, 0, 800, 600),
            ai_options={},
            ai_next_point=None,
            ai_option_wait_ms=5,
            ai_next_wait_ms=50,
            ai_monitor=True,
            ai_auto_next=False,
            ai_poll_ms=100,
            ai_page_mode=True,
            ai_auto_scroll=True,
            ai_scroll_steps=2,
            ai_scroll_wait_ms=0,
            ai_scroll_max=0,
            count_limit=0,
            start_delay_ms=0,
        )
        base.update(kwargs)
        return ClickConfig(**base)

    def drain_finished(self, timeout: float = 8.0) -> list[dict]:
        collected: list[dict] = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                event = self.events.get(timeout=0.05)
            except queue.Empty:
                continue
            collected.append(event)
            if event.get("kind") == "finished":
                return collected
        self.fail(f"等待 finished 超时，已收到：{collected[-3:]}")

    def test_scrolls_and_stops_at_bottom(self) -> None:
        pages = [
            FakePage([FakeQuestion(1, ["B"])], [{"B": (10, 20)}]),
            FakePage([FakeQuestion(2, ["A", "C"])], [{"A": (30, 40), "C": (50, 60)}]),
            FakePage([FakeQuestion(3, ["D"])], [{"D": (70, 80)}]),
        ]
        state = {"i": 0}

        def ai_fn():
            if state["i"] < len(pages):
                page = pages[state["i"]]
                state["i"] += 1
                return page
            return None

        self.scroll_results = [True, True, False, False]
        engine = ClickEngine(
            self.click_fn, lambda: None, self.events.put, ai_fn=ai_fn,
            ai_baseline_fn=self.baseline_fn, scroll_fn=self.scroll_fn,
        )
        self.assertTrue(engine.start(self.config()))
        events = self.drain_finished()
        finished = events[-1]
        self.assertEqual(finished["reason"], "scroll_end")
        self.assertEqual(self.clicks, [(10, 20), (30, 40), (50, 60), (70, 80)])
        self.assertEqual(finished["clicks"], 4)
        self.assertEqual(self.scroll_calls, 4)

    def test_skips_answered_and_duplicate_questions(self) -> None:
        pages = [
            FakePage(
                [
                    FakeQuestion(1, ["B"], key="题目一"),
                    FakeQuestion(2, ["A"], key="题目二", answered=True),
                ],
                [{"B": (10, 20), "A": (30, 40)}],
            ),
            # 滚动重叠：上一屏刚答过的题又出现了（同一题干 key）
            FakePage([FakeQuestion(1, ["B"], key="题目一")], [{"B": (10, 20)}]),
            FakePage([FakeQuestion(3, ["C"], key="题目三")], [{"C": (70, 80)}]),
        ]
        state = {"i": 0}

        def ai_fn():
            if state["i"] < len(pages):
                page = pages[state["i"]]
                state["i"] += 1
                return page
            return None

        self.scroll_results = [True, True, False, False]
        engine = ClickEngine(
            self.click_fn, lambda: None, self.events.put, ai_fn=ai_fn,
            ai_baseline_fn=self.baseline_fn, scroll_fn=self.scroll_fn,
        )
        self.assertTrue(engine.start(self.config()))
        events = self.drain_finished()
        self.assertEqual(events[-1]["reason"], "scroll_end")
        # 题目二已答（AI 标记）跳过；第二次出现的题目一按 key 去重跳过；只点题目一和题目三
        self.assertEqual(self.clicks, [(10, 20), (70, 80)])
        self.assertEqual(events[-1]["clicks"], 2)

    def test_stops_at_scroll_limit(self) -> None:
        page = FakePage([FakeQuestion(1, ["B"])], [{"B": (10, 20)}])
        calls = {"n": 0}

        def ai_fn():
            calls["n"] += 1
            return page

        self.scroll_results = [True, True, True]
        engine = ClickEngine(
            self.click_fn, lambda: None, self.events.put, ai_fn=ai_fn,
            ai_baseline_fn=self.baseline_fn, scroll_fn=self.scroll_fn,
        )
        self.assertTrue(engine.start(self.config(ai_scroll_max=1, count_limit=0)))
        events = self.drain_finished()
        self.assertEqual(events[-1]["reason"], "scroll_end")
        self.assertEqual(self.scroll_calls, 1)


class ScrollWheelTest(unittest.TestCase):
    def test_wheel_flags_and_direction(self) -> None:
        original_send = winapi._send_mouse
        original_move = winapi.move_cursor
        events: list[tuple[int, int]] = []
        winapi._send_mouse = lambda flags, dx=0, dy=0, data=0: events.append((flags, data)) or 1
        winapi.move_cursor = lambda *a, **k: True
        try:
            self.assertTrue(winapi.scroll_wheel(3, 100, 200))
            self.assertEqual(len(events), 3)
            self.assertTrue(all(flags & winapi.MOUSEEVENTF_WHEEL for flags, _ in events))
            self.assertTrue(all(data == -winapi.WHEEL_DELTA for _, data in events))
            events.clear()
            self.assertTrue(winapi.scroll_wheel(-2))
            self.assertEqual(len(events), 2)
            self.assertTrue(all(data == winapi.WHEEL_DELTA for _, data in events))
        finally:
            winapi._send_mouse = original_send
            winapi.move_cursor = original_move


if __name__ == "__main__":
    unittest.main()
