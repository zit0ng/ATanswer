"""点击引擎：在独立线程中执行"定点连点 / 找图点击"循环。

设计要点
--------
- 开始、暂停、继续、停止都由 UI 线程调用，内部用 Event 协作，响应及时（<50ms）。
- 通过 ``emit(event: dict)`` 把状态与日志推送到 UI 队列，UI 定时消费。
- 不直接依赖 Windows API：点击与找图动作由控制器注入，方便未来接入手机端。
"""
from __future__ import annotations

import random
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ClickConfig:
    mode: str = "fixed"
    # 定点连点
    x: int = 0
    y: int = 0
    button: str = "left"
    double_click: bool = False
    interval_ms: int = 1000
    random_interval: bool = False
    interval_min_ms: int = 800
    interval_max_ms: int = 1200
    count_limit: int = 0
    start_delay_ms: int = 2000
    restore_cursor: bool = False
    # 找图点击
    template_path: str = ""
    template: Any = None  # templates.Template | None
    region: tuple[int, int, int, int] | None = None
    threshold: float = 0.90
    click_offset_x: int = 0
    click_offset_y: int = 0
    poll_ms: int = 500
    # AI 答题
    ai_config: Any = None  # ai_client.AIConfig | None
    ai_region: tuple[int, int, int, int] | None = None
    ai_options: dict[str, tuple[int, int]] = field(default_factory=dict)
    ai_next_point: tuple[int, int] | None = None
    ai_option_wait_ms: int = 400
    ai_next_wait_ms: int = 1500
    ai_monitor: bool = True
    ai_auto_next: bool = True
    ai_poll_ms: int = 300
    ai_realtime_layout: bool = True
    ai_page_mode: bool = False
    ai_fullscreen: bool = False
    ai_auto_scroll: bool = False
    ai_tile_mode: bool = True
    ai_scroll_steps: int = 3
    ai_scroll_wait_ms: int = 400
    ai_scroll_max: int = 0
    # 其它
    extra: dict = field(default_factory=dict)


def validate_config(config: ClickConfig) -> tuple[bool, str]:
    """检查配置是否合法，返回 (是否通过, 提示信息)。"""
    if config.interval_ms < 10:
        return False, "点击间隔不能小于 10 毫秒"
    if config.random_interval:
        if config.interval_min_ms < 10:
            return False, "随机间隔下限不能小于 10 毫秒"
        if config.interval_max_ms < config.interval_min_ms:
            return False, "随机间隔上限不能小于下限"
    if config.count_limit < 0:
        return False, "点击次数不能为负数（0 表示无限）"
    if config.start_delay_ms < 0:
        return False, "开始延迟不能为负数"
    if config.mode == "image":
        if config.template is None:
            return False, "请先选择或截取模板图片"
        if not (0.5 <= config.threshold <= 1.0):
            return False, "相似度需在 50% ~ 100% 之间"
        if config.poll_ms < 50:
            return False, "找图轮询间隔不能小于 50 毫秒"
    if config.mode == "ai":
        if config.ai_config is None:
            return False, "请先填写 AI 接口配置"
        if config.ai_region is None:
            return False, "请先框选题目区域"
        if config.ai_page_mode and not config.ai_monitor:
            return False, "整页多题模式需要开启实时监控"
        if not config.ai_page_mode:
            if not config.ai_options:
                return False, "请至少配置一个选项的点击坐标"
            if (not config.ai_monitor) or config.ai_auto_next:
                if config.ai_next_point is None:
                    return False, "请配置「下一题」按钮的点击坐标（或关闭自动进入下一题）"
        if config.ai_poll_ms < 100:
            return False, "实时监控间隔不能小于 100 毫秒"
        if config.ai_auto_scroll:
            if not config.ai_monitor:
                return False, "自动滚屏答题需要开启实时监控"
            if not config.ai_page_mode:
                return False, "自动滚屏答题需要开启整页多题模式"
            if config.ai_scroll_steps < 1:
                return False, "滚动格数至少为 1"
            if config.ai_scroll_wait_ms < 0:
                return False, "滚屏等待时间不能为负数"
            if config.ai_scroll_max < 0:
                return False, "最多滚动次数不能为负数（0 表示到底自动停）"
    return True, ""


class ClickEngine:
    """点击引擎。"""

    def __init__(
        self,
        click_fn: Callable[[int, int, str, bool, bool], bool],
        match_fn: Callable[[], Any],
        emit: Callable[[dict], None],
        ai_fn: Callable[[], Any] | None = None,
        ai_baseline_fn: Callable[[], None] | None = None,
        scroll_fn: Callable[[], bool] | None = None,
    ) -> None:
        self._click_fn = click_fn
        self._match_fn = match_fn
        self._emit = emit
        self._ai_fn = ai_fn
        self._ai_baseline_fn = ai_baseline_fn
        self._scroll_fn = scroll_fn
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._state = "idle"
        self._clicks = 0
        self._misses = 0
        self._errors = 0
        self._last_score: float | None = None
        self._last_match_ms: float | None = None
        self._started_at = 0.0
        self._config: ClickConfig | None = None
        self._log_throttle = 0.0
        self._scrolls = 0
        self._scroll_nochange = 0
        self._scroll_end = False
        self._answered_keys: set[str] = set()

    # -- 对外属性 ---------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    # -- 控制 -------------------------------------------------------------
    def start(self, config: ClickConfig) -> bool:
        if self.is_running:
            return False
        ok, _ = validate_config(config)
        if not ok:
            return False
        self._stop_event.clear()
        self._pause_event.clear()
        self._config = config
        self._state = "starting"
        self._clicks = 0
        self._misses = 0
        self._errors = 0
        self._last_score = None
        self._last_match_ms = None
        self._started_at = time.perf_counter()
        self._scrolls = 0
        self._scroll_nochange = 0
        self._scroll_end = False
        self._answered_keys.clear()
        self._thread = threading.Thread(target=self._run, name="ClickEngine", daemon=True)
        self._thread.start()
        return True

    def pause(self) -> None:
        if self.is_running and self._state in ("starting", "running"):
            self._pause_event.set()
            self._state = "paused"
            self._emit_status()
            self._emit({"kind": "log", "level": "warn", "message": "已暂停（按 F7 或点击“继续”恢复）"})

    def resume(self) -> None:
        if self.is_running and self._state == "paused":
            self._pause_event.clear()
            self._state = "running"
            self._emit_status()
            self._emit({"kind": "log", "level": "accent", "message": "已继续运行"})

    def stop(self, timeout: float = 2.5) -> None:
        if not self.is_running:
            self._state = "idle"
            return
        self._stop_event.set()
        self._pause_event.clear()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._state = "idle"
        self._thread = None
        self._emit_status()

    # -- 内部 -------------------------------------------------------------
    def _emit(self, event: dict) -> None:
        try:
            self._emit(event)
        except Exception:  # pragma: no cover - UI 队列不应阻塞引擎
            pass

    def _emit_status(self) -> None:
        self._emit(
            {
                "kind": "status",
                "state": self._state,
                "clicks": self._clicks,
                "misses": self._misses,
                "elapsed": max(0.0, time.perf_counter() - self._started_at),
                "score": self._last_score,
            }
        )

    def _interval_seconds(self) -> float:
        cfg = self._config
        assert cfg is not None
        if cfg.random_interval:
            low = min(cfg.interval_min_ms, cfg.interval_max_ms)
            high = max(cfg.interval_min_ms, cfg.interval_max_ms)
            return max(10, random.randint(low, high)) / 1000.0
        return max(10, cfg.interval_ms) / 1000.0

    def _wait(self, seconds: float) -> bool:
        """可被停止/暂停打断的等待。返回 False 表示收到停止信号。"""
        deadline = time.perf_counter() + seconds
        while not self._stop_event.is_set():
            if self._pause_event.is_set():
                pause_start = time.perf_counter()
                while self._pause_event.is_set() and not self._stop_event.is_set():
                    self._stop_event.wait(0.03)
                deadline += time.perf_counter() - pause_start
                continue
            remain = deadline - time.perf_counter()
            if remain <= 0:
                return True
            self._stop_event.wait(min(remain, 0.03))
        return False

    def _do_click(self, x: int, y: int) -> None:
        cfg = self._config
        assert cfg is not None
        ok = self._click_fn(x, y, cfg.button, cfg.double_click, cfg.restore_cursor)
        if not ok:
            raise OSError("模拟点击失败（可能被系统/权限拦截）")

    def _run(self) -> None:
        cfg = self._config
        assert cfg is not None
        reason = "stopped"
        self._emit({"kind": "log", "level": "info", "message": "引擎已启动"})
        self._emit_status()

        # 开始延迟（可中止）
        if cfg.start_delay_ms > 0:
            self._emit(
                {
                    "kind": "log",
                    "level": "muted",
                    "message": f"开始延迟 {cfg.start_delay_ms / 1000:.1f} 秒…",
                }
            )
            delay_done = time.perf_counter() + cfg.start_delay_ms / 1000.0
            while not self._stop_event.is_set() and time.perf_counter() < delay_done:
                self._stop_event.wait(0.03)

        if not self._stop_event.is_set():
            self._state = "running"
            self._emit_status()
            mode_text = {"image": "找图点击", "ai": "AI 答题"}.get(cfg.mode, "定点连点")
            self._emit({"kind": "log", "level": "success", "message": f"开始执行：{mode_text}"})

        last_tick = 0.0
        try:
            while not self._stop_event.is_set():
                if self._pause_event.is_set():
                    self._effortless_wait_while_paused()
                    continue
                try:
                    if cfg.mode == "image":
                        match = self._match_fn()
                        if match is not None:
                            self._last_score = float(getattr(match, "score", 0.0))
                            self._last_match_ms = float(getattr(match, "elapsed_ms", 0.0))
                            mx = match.center[0] + cfg.click_offset_x
                            my = match.center[1] + cfg.click_offset_y
                            self._do_click(mx, my)
                            self._clicks += 1
                            now = time.perf_counter()
                            if now - self._log_throttle > 1.0:
                                self._log_throttle = now
                                self._emit(
                                    {
                                        "kind": "log",
                                        "level": "success",
                                        "message": (
                                            f"识别成功，相似度 {self._last_score * 100:.1f}%"
                                            f"，点击 ({mx}, {my})"
                                        ),
                                    }
                                )
                        else:
                            self._misses += 1
                            self._last_score = None
                    elif cfg.mode == "ai":
                        self._do_ai_step()
                    else:
                        self._do_click(cfg.x, cfg.y)
                        self._clicks += 1
                except Exception as exc:  # noqa: BLE001 - 引擎需要容错
                    if self._stop_event.is_set():
                        break
                    self._errors += 1
                    self._emit(
                        {
                            "kind": "log",
                            "level": "error",
                            "message": f"执行出错：{exc}",
                        }
                    )
                    max_errors = 3 if cfg.mode == "ai" else 10
                    if self._errors >= max_errors:
                        reason = "error"
                        break
                else:
                    self._errors = 0  # 连续成功则重置错误计数

                if self._scroll_end:
                    reason = "scroll_end"
                    break

                now = time.perf_counter()
                if now - last_tick >= 0.1:
                    last_tick = now
                    self._emit(
                        {
                            "kind": "tick",
                            "clicks": self._clicks,
                            "misses": self._misses,
                            "elapsed": now - self._started_at,
                            "score": self._last_score,
                        }
                    )
                self._emit_status_sometimes()

                if cfg.count_limit > 0 and self._clicks >= cfg.count_limit:
                    reason = "limit"
                    break

                if cfg.mode == "fixed":
                    wait_s = self._interval_seconds()
                elif cfg.mode == "image":
                    wait_s = max(0.05, cfg.poll_ms / 1000.0)
                elif cfg.mode == "ai":
                    wait_s = max(0.1, cfg.ai_poll_ms / 1000.0) if cfg.ai_monitor else 0.1
                else:
                    wait_s = 0.1
                if not self._wait(wait_s):
                    reason = "stopped"
                    break
        finally:
            self._state = "idle"
            elapsed = max(0.0, time.perf_counter() - self._started_at)
            self._emit_status()
            self._emit(
                {
                    "kind": "finished",
                    "reason": reason,
                    "clicks": self._clicks,
                    "misses": self._misses,
                    "elapsed": elapsed,
                }
            )

    def _effortless_wait_while_paused(self) -> None:
        while self._pause_event.is_set() and not self._stop_event.is_set():
            self._stop_event.wait(0.03)

    def _do_ai_step(self) -> None:
        """AI 答题一步：根据配置走"实时监控"或"顺序答题"。"""
        cfg = self._config
        assert cfg is not None
        if cfg.ai_monitor:
            if cfg.ai_auto_scroll:
                self._do_ai_scroll_page()
            elif cfg.ai_page_mode:
                self._do_ai_page()
            else:
                self._do_ai_monitor()
        else:
            self._do_ai_question()

    def _wait_pause(self) -> bool:
        """若处于暂停则等待；返回 False 表示需要中止当前步骤。"""
        if self._stop_event.is_set():
            return False
        while self._pause_event.is_set() and not self._stop_event.is_set():
            self._stop_event.wait(0.05)
        return not self._stop_event.is_set()

    def _apply_ai_answer(self, answer, mark_baseline: bool, label: str) -> bool:
        """点击 AI 选中的选项（单选一个 / 多选多个）；返回 False 表示步骤被中止。"""
        cfg = self._config
        assert cfg is not None
        if not self._wait_pause():
            return False
        letters = [
            str(item).upper()
            for item in (getattr(answer, "letters", None) or [])
            if str(item).strip()
        ]
        if not letters:
            raw_letter = str(getattr(answer, "letter", "")).upper()
            letters = [char for char in raw_letter if char in "ABCDEF"]
        if not letters:
            raise OSError("AI 没有返回答案字母")
        display = "、".join(letters)
        raw = str(getattr(answer, "raw", ""))
        elapsed_ms = float(getattr(answer, "elapsed_ms", 0.0))
        self._emit(
            {
                "kind": "ai_result",
                "letter": display,
                "letters": letters,
                "raw": raw,
                "elapsed_ms": elapsed_ms,
            }
        )
        dynamic_points = getattr(answer, "click_points", None) or {}
        clicked: list[str] = []
        missing: list[str] = []
        for letter in letters:
            point = dynamic_points.get(letter)
            if point is None:
                point = cfg.ai_options.get(letter)
            if point is None:
                missing.append(letter)
                continue
            self._do_click(int(point[0]), int(point[1]))
            self._clicks += 1
            clicked.append(letter)
            if not self._wait(max(0.0, cfg.ai_option_wait_ms / 1000.0)):
                return False
        if not clicked:
            raise OSError(
                f"AI 选择 {display}，但既没有实时位置也没有配置该选项的点击坐标"
            )
        if missing:
            self._emit(
                {
                    "kind": "log",
                    "level": "warn",
                    "message": f"选项 {'、'.join(missing)} 没有可用坐标，已跳过",
                }
            )
        multi_note = "（多选）" if len(clicked) > 1 else ""
        self._emit(
            {
                "kind": "log",
                "level": "success",
                "message": (
                    f"{label} AI 答案：{'、'.join(clicked)}{multi_note}"
                    f"（耗时 {elapsed_ms / 1000:.1f} 秒），已点击选项"
                ),
            }
        )
        if mark_baseline and self._ai_baseline_fn is not None:
            try:
                self._ai_baseline_fn()
            except Exception:  # noqa: BLE001 - 基线失败不应中断任务
                pass
        return True

    def _click_next_question(self, dynamic_point: tuple[int, int] | None = None) -> None:
        cfg = self._config
        assert cfg is not None
        point = dynamic_point or cfg.ai_next_point
        if point is None:
            return
        nx, ny = point
        self._do_click(int(nx), int(ny))
        self._emit({"kind": "log", "level": "muted", "message": "已点击「下一题」，等待加载…"})
        self._wait(max(0.1, cfg.ai_next_wait_ms / 1000.0))

    def _do_ai_question(self) -> None:
        """顺序答题：识别 → 点选项 → 点下一题。"""
        cfg = self._config
        assert cfg is not None
        if self._ai_fn is None:
            raise OSError("AI 识别器未初始化")
        answer = self._ai_fn()
        if answer is None:
            raise OSError("AI 未返回有效答案")
        if not self._apply_ai_answer(answer, mark_baseline=False, label="顺序答题 ·"):
            return
        self._click_next_question(getattr(answer, "next_click", None))

    def _do_ai_monitor(self) -> None:
        """实时监控：画面变化后才请求 AI；答题后更新基线，避免重复作答。"""
        cfg = self._config
        assert cfg is not None
        if self._ai_fn is None:
            raise OSError("AI 识别器未初始化")
        answer = self._ai_fn()
        if answer is None:
            # 画面未变化 / 仍在滚动：本轮不处理，继续等待
            return
        if not self._apply_ai_answer(answer, mark_baseline=True, label="实时监控 ·"):
            return
        if cfg.ai_auto_next:
            self._click_next_question(getattr(answer, "next_click", None))

    def _question_key(self, question) -> str:
        """题目去重 key：题干摘要归一化后取前 40 个字符。"""
        key = str(getattr(question, "key", "") or "")
        if not key:
            key = str(getattr(question, "stem", "") or "")
        key = re.sub(r"\s+", "", key).lower()
        key = re.sub(r"[，。！？、,.!?;；:：\"'“”‘’（）()\[\]【】<>《》]", "", key)
        return key[:40]

    def _apply_page_questions(self, page) -> int:
        """点击整页多题结果中的全部题目，返回作答的题目数。"""
        cfg = self._config
        assert cfg is not None
        questions = list(getattr(page, "questions", None) or [])
        abs_points = list(getattr(page, "abs_points", None) or [])
        if not questions:
            self._emit(
                {
                    "kind": "log",
                    "level": "muted",
                    "message": "整页多题：当前画面没有需要作答的新题目",
                }
            )
            return 0
        answered = 0
        for position, question in enumerate(questions):
            if not self._wait_pause():
                return answered
            display_index = getattr(question, "index", position + 1)
            letters = [
                str(item).upper()
                for item in (getattr(question, "letters", None) or [])
                if str(item).strip()
            ]
            if not letters:
                raw_letter = str(getattr(question, "letter", "")).upper()
                letters = [char for char in raw_letter if char in "ABCDEF"]
            if not letters:
                continue
            selected = {
                str(item).upper()
                for item in (getattr(question, "selected", None) or [])
                if str(item).strip()
            }
            key = self._question_key(question)
            if key and key in self._answered_keys:
                self._emit(
                    {
                        "kind": "log",
                        "level": "muted",
                        "message": (
                            f"整页多题 · 第 {display_index} 题与本轮已答题重复（滚动重叠），跳过"
                        ),
                    }
                )
                continue
            if selected and all(letter in selected for letter in letters):
                if key:
                    self._answered_keys.add(key)
                self._emit(
                    {
                        "kind": "log",
                        "level": "muted",
                        "message": (
                            f"整页多题 · 第 {display_index} 题正确答案已全部处于选中状态"
                            f"（{'、'.join(sorted(selected))}），跳过不重复点击"
                        ),
                    }
                )
                continue
            if bool(getattr(question, "answered", False)) and not selected:
                self._emit(
                    {
                        "kind": "log",
                        "level": "muted",
                        "message": (
                            f"整页多题 · 第 {display_index} 题已有选中答案（高亮/勾选），跳过不点"
                        ),
                    }
                )
                continue
            mapping = abs_points[position] if position < len(abs_points) else {}
            clicked: list[str] = []
            skipped_selected: list[str] = []
            missing: list[str] = []
            for letter in letters:
                if letter in selected:
                    skipped_selected.append(letter)
                    continue
                point = mapping.get(letter) if isinstance(mapping, dict) else None
                if point is None:
                    point = cfg.ai_options.get(letter)
                if point is None:
                    missing.append(letter)
                    continue
                self._do_click(int(point[0]), int(point[1]))
                self._clicks += 1
                clicked.append(letter)
                if not self._wait(max(0.0, cfg.ai_option_wait_ms / 1000.0)):
                    return answered
            if skipped_selected:
                self._emit(
                    {
                        "kind": "log",
                        "level": "muted",
                        "message": (
                            f"整页多题 · 第 {display_index} 题选项 "
                            f"{'、'.join(skipped_selected)} 已经是选中状态，跳过不点（避免取消选择）"
                        ),
                    }
                )
            if not clicked:
                self._emit(
                    {
                        "kind": "log",
                        "level": "warn",
                        "message": (
                            f"整页多题：第 {display_index} 题"
                            f"缺少选项 {'、'.join(letters)} 的位置，已跳过"
                        ),
                    }
                )
                continue
            if missing:
                self._emit(
                    {
                        "kind": "log",
                        "level": "warn",
                        "message": (
                            f"整页多题：第 {getattr(question, 'index', position + 1)} 题"
                            f"选项 {'、'.join(missing)} 缺少位置，已跳过这些选项"
                        ),
                    }
                )
            answered += 1
            if key:
                self._answered_keys.add(key)
            display = "、".join(clicked)
            self._emit(
                {
                    "kind": "ai_result",
                    "letter": display,
                    "letters": clicked,
                    "raw": str(getattr(page, "raw", "")),
                    "elapsed_ms": float(getattr(page, "elapsed_ms", 0.0)),
                }
            )
            multi_note = "（多选）" if len(clicked) > 1 else ""
            self._emit(
                {
                    "kind": "log",
                    "level": "success",
                    "message": (
                        f"整页多题 · 第 {getattr(question, 'index', position + 1)} 题"
                        f" AI 答案：{display}{multi_note}，已点击选项"
                    ),
                }
            )
        return answered

    def _do_ai_page(self) -> None:
        """整页多题：一次 AI 请求答完框内所有未作答题，再可选地点「下一题」。"""
        cfg = self._config
        assert cfg is not None
        if self._ai_fn is None:
            raise OSError("AI 识别器未初始化")
        page = self._ai_fn()
        if page is None:
            return  # 画面未变化 / 未稳定，等下一轮
        answered = self._apply_page_questions(page)
        has_questions = bool(getattr(page, "questions", None) or [])
        if answered or not has_questions:
            if self._ai_baseline_fn is not None:
                try:
                    self._ai_baseline_fn()
                except Exception:  # noqa: BLE001
                    pass
            if cfg.ai_auto_next:
                self._click_next_question(getattr(page, "next_click", None))

    def _do_ai_scroll_page(self) -> None:
        """自动滚屏答题：答完当前屏可见题目 → 向下滚动 → 等待下一屏。"""
        cfg = self._config
        assert cfg is not None
        if self._ai_fn is None:
            raise OSError("AI 识别器未初始化")
        page = self._ai_fn()
        answered = 0
        if page is not None:
            answered = self._apply_page_questions(page)
            if answered and self._ai_baseline_fn is not None:
                try:
                    self._ai_baseline_fn()
                except Exception:  # noqa: BLE001
                    pass
        if cfg.ai_scroll_max > 0 and self._scrolls >= cfg.ai_scroll_max:
            self._emit(
                {
                    "kind": "log",
                    "level": "info",
                    "message": f"已达到最多滚动 {cfg.ai_scroll_max} 次，任务结束",
                }
            )
            self._scroll_end = True
            return
        if self._scroll_fn is None:
            raise OSError("自动滚屏回调未初始化")
        self._scrolls += 1
        changed = bool(self._scroll_fn())
        if changed:
            self._scroll_nochange = 0
            self._emit(
                {
                    "kind": "log",
                    "level": "muted",
                    "message": f"已自动向下滚动 {self._scrolls} 次，继续查找题目…",
                }
            )
        else:
            self._scroll_nochange += 1
            if self._scroll_nochange >= 2:
                self._emit(
                    {
                        "kind": "log",
                        "level": "success",
                        "message": "连续两次滚动没有发现新内容（已到页面底部），自动停止",
                    }
                )
                self._scroll_end = True
                return
        if not self._wait(max(0.0, cfg.ai_scroll_wait_ms / 1000.0)):
            return

    def _emit_status_sometimes(self) -> None:
        # 节流：最多每 100ms 推送一次状态，避免 UI 队列堆积
        now = time.perf_counter()
        last = getattr(self, "_last_status_push", 0.0)
        if now - last >= 0.1:
            self._last_status_push = now
            self._emit_status()
