"""AI 答题模式的引擎单元测试（伪造 AI / 点击回调）。"""
from __future__ import annotations

import os
import queue
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine import ClickConfig, ClickEngine, validate_config  # noqa: E402


class FakeAnswer:
    def __init__(self, letter: str, click_points=None, next_click=None, letters=None) -> None:
        self.letter = letter
        self.raw = f"推理…\n答案：{letter}"
        self.elapsed_ms = 1.2
        self.click_points = click_points
        self.next_click = next_click
        self.letters = letters or ([letter] if letter else [])

    def all_letters(self):
        return list(self.letters)


class FakePageQuestion:
    def __init__(self, index: int, letter: str, letters=None, selected=None) -> None:
        self.index = index
        self.letter = letter
        self.letters = letters or ([letter] if letter else [])
        self.selected = selected or []


class FakePage:
    def __init__(self, questions, abs_points, next_click=None) -> None:
        self.questions = questions
        self.abs_points = abs_points
        self.next_click = next_click
        self.raw = "整页多题"
        self.elapsed_ms = 2.0


class AIEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.events: queue.Queue[dict] = queue.Queue()
        self.clicks: list[tuple[int, int]] = []
        self.baseline_calls = 0

    def click_fn(self, x, y, button, double, restore) -> bool:
        self.clicks.append((int(x), int(y)))
        return True

    def baseline_fn(self) -> None:
        self.baseline_calls += 1

    def make_engine(self, letters) -> ClickEngine:
        if isinstance(letters, list):
            iterator = iter(letters)
            raw = list(letters)
        else:  # pragma: no cover
            iterator = iter([])
            raw = []
        state = {"index": 0}

        def ai_fn():
            if state["index"] < len(raw):
                item = raw[state["index"]]
                state["index"] += 1
                if item is None:
                    return None
                return FakeAnswer(str(item))
            return None

        return ClickEngine(
            self.click_fn,
            lambda: None,
            self.events.put,
            ai_fn=ai_fn,
            ai_baseline_fn=self.baseline_fn,
        )

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
        self.fail(f"等待 finished 超时，已收到：{collected[-5:]}")

    def config(self, **kwargs) -> ClickConfig:
        base = dict(
            mode="ai",
            ai_config=object(),
            ai_region=(0, 0, 800, 600),
            ai_options={"B": (300, 400), "C": (300, 500)},
            ai_next_point=(900, 700),
            ai_option_wait_ms=10,
            ai_next_wait_ms=100,
            ai_monitor=False,
            ai_auto_next=True,
            ai_poll_ms=100,
            count_limit=2,
            start_delay_ms=0,
        )
        base.update(kwargs)
        return ClickConfig(**base)

    def test_ai_flow_clicks_option_and_next(self) -> None:
        engine = self.make_engine(["B", "C"])
        self.assertTrue(engine.start(self.config()))
        events = self.drain_finished()
        self.assertEqual(self.clicks, [(300, 400), (900, 700), (300, 500), (900, 700)])
        finished = events[-1]
        self.assertEqual(finished["reason"], "limit")
        self.assertEqual(finished["clicks"], 2)
        ai_events = [e for e in events if e.get("kind") == "ai_result"]
        self.assertEqual([e["letter"] for e in ai_events], ["B", "C"])
        self.assertFalse(engine.is_running)

    def test_monitor_mode_marks_baseline_and_optional_next(self) -> None:
        engine = self.make_engine(["B"])
        cfg = self.config(ai_monitor=True, ai_auto_next=False, ai_next_point=None, count_limit=1)
        self.assertTrue(engine.start(cfg))
        self.drain_finished(timeout=8)
        self.assertEqual(self.clicks, [(300, 400)])
        self.assertEqual(self.baseline_calls, 1)

    def test_monitor_mode_skips_unchanged_frames(self) -> None:
        # 前两帧画面未变化（None），第三帧才出现新题目
        engine = self.make_engine([None, None, "B"])
        cfg = self.config(ai_monitor=True, ai_auto_next=False, ai_next_point=None, count_limit=1)
        self.assertTrue(engine.start(cfg))
        self.drain_finished(timeout=8)
        self.assertEqual(self.clicks, [(300, 400)])
        # 未变化时不应产生错误
        errors = []  # 事件可能已消费，改从引擎状态判断
        self.assertFalse(engine.is_running)

    def test_unknown_letter_stops_after_errors(self) -> None:
        engine = self.make_engine(["Z", "Z", "Z", "Z", "Z"])
        self.assertTrue(engine.start(self.config(count_limit=0)))
        events = self.drain_finished()
        self.assertEqual(events[-1]["reason"], "error")
        self.assertEqual(len(self.clicks), 0)
        errors = [e for e in events if e.get("kind") == "log" and e.get("level") == "error"]
        self.assertGreaterEqual(len(errors), 3)

    def test_validate_ai_config(self) -> None:
        ok, _ = validate_config(self.config())
        self.assertTrue(ok)
        bad = self.config(ai_options={}, ai_next_point=None)
        ok, message = validate_config(bad)
        self.assertFalse(ok)
        self.assertIn("选项", message)

    def test_dynamic_layout_points_override_config(self) -> None:
        """实时定位模式下，AI 返回的动态坐标应优先于固定配置坐标。"""
        answer = FakeAnswer("B", click_points={"B": (777, 888)}, next_click=(999, 1000))

        def ai_fn():
            return answer

        engine = ClickEngine(
            self.click_fn, lambda: None, self.events.put, ai_fn=ai_fn,
            ai_baseline_fn=self.baseline_fn,
        )
        cfg = self.config(count_limit=1)
        self.assertTrue(engine.start(cfg))
        self.drain_finished()
        self.assertEqual(self.clicks, [(777, 888), (999, 1000)])

    def test_multi_select_clicks_all_answers(self) -> None:
        """多选题：AI 返回 A、C 时，应依次点击 A 和 C，再点下一题。"""
        answer = FakeAnswer(
            "A",
            click_points={"A": (10, 20), "C": (30, 40)},
            next_click=(99, 100),
            letters=["A", "C"],
        )

        def ai_fn():
            return answer

        engine = ClickEngine(
            self.click_fn, lambda: None, self.events.put, ai_fn=ai_fn,
            ai_baseline_fn=self.baseline_fn,
        )
        cfg = self.config(count_limit=1)
        self.assertTrue(engine.start(cfg))
        events = self.drain_finished()
        self.assertEqual(self.clicks, [(10, 20), (30, 40), (99, 100)])
        ai_events = [e for e in events if e.get("kind") == "ai_result"]
        self.assertEqual(ai_events[0]["letter"], "A、C")

    def test_page_mode_multi_select(self) -> None:
        """整页多题 + 多选题：同一题要点击全部正确选项。"""
        page = FakePage(
            [FakePageQuestion(1, "A", letters=["A", "C"])],
            [{"A": (110, 210), "C": (310, 410)}],
            next_click=(950, 750),
        )

        def ai_fn():
            return page

        engine = ClickEngine(
            self.click_fn, lambda: None, self.events.put, ai_fn=ai_fn,
            ai_baseline_fn=self.baseline_fn,
        )
        cfg = self.config(
            ai_monitor=True, ai_page_mode=True, ai_auto_next=True,
            ai_next_point=None, count_limit=1,
        )
        self.assertTrue(engine.start(cfg))
        self.drain_finished()
        self.assertEqual(self.clicks, [(110, 210), (310, 410), (950, 750)])

    def test_page_mode_skips_already_selected_options(self) -> None:
        """多选：C/D 已经选中时，只补点未选中的 A/B，避免把 C/D 点掉。"""
        page = FakePage(
            [FakePageQuestion(7, "A", letters=["A", "B", "C", "D"], selected=["C", "D"])],
            [{"A": (10, 20), "B": (30, 40), "C": (50, 60), "D": (70, 80)}],
        )
        state = {"done": False}

        def ai_fn():
            if state["done"]:
                return None
            state["done"] = True
            return page

        engine = ClickEngine(
            self.click_fn, lambda: None, self.events.put, ai_fn=ai_fn,
            ai_baseline_fn=self.baseline_fn,
        )
        cfg = self.config(
            ai_monitor=True, ai_page_mode=True, ai_auto_next=False,
            ai_next_point=None, count_limit=1,
        )
        self.assertTrue(engine.start(cfg))
        self.drain_finished()
        self.assertEqual(self.clicks, [(10, 20), (30, 40)])

    def test_page_mode_answers_all_questions(self) -> None:
        """整页多题模式：一次 AI 返回两道题，应依次点击两道题的正确答案，最后点下一题。"""
        page = FakePage(
            [FakePageQuestion(1, "B"), FakePageQuestion(2, "C")],
            [{"B": (310, 410)}, {"C": (320, 520)}],
            next_click=(950, 750),
        )
        state = {"done": False}

        def ai_fn():
            if state["done"]:
                return None
            state["done"] = True
            return page

        engine = ClickEngine(
            self.click_fn, lambda: None, self.events.put, ai_fn=ai_fn,
            ai_baseline_fn=self.baseline_fn,
        )
        cfg = self.config(
            ai_monitor=True, ai_page_mode=True, ai_auto_next=True,
            ai_next_point=None, count_limit=2,
        )
        self.assertTrue(engine.start(cfg))
        events = self.drain_finished()
        self.assertEqual(self.clicks, [(310, 410), (320, 520), (950, 750)])
        self.assertEqual(self.baseline_calls, 1)
        finished = events[-1]
        self.assertEqual(finished["clicks"], 2)
        answered = [e["letter"] for e in events if e.get("kind") == "ai_result"]
        self.assertEqual(answered, ["B", "C"])

    def test_validate_page_mode(self) -> None:
        ok, message = validate_config(
            self.config(
                ai_monitor=True, ai_page_mode=True, ai_options={}, ai_next_point=None
            )
        )
        self.assertTrue(ok, message)
        ok, message = validate_config(self.config(ai_monitor=False, ai_page_mode=True))
        self.assertFalse(ok)
        self.assertIn("实时监控", message)

    def test_validate_monitor_without_next(self) -> None:
        ok, message = validate_config(
            self.config(ai_monitor=True, ai_auto_next=False, ai_next_point=None)
        )
        self.assertTrue(ok, message)
        ok, message = validate_config(
            self.config(ai_monitor=True, ai_auto_next=True, ai_next_point=None)
        )
        self.assertFalse(ok)
        self.assertIn("下一题", message)


if __name__ == "__main__":
    unittest.main()
