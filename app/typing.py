"""逐字录入：把一段文本用键盘逐字符键入当前焦点控件（不经过剪贴板）。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from . import winapi


@dataclass
class TypingOptions:
    text: str
    interval_ms: int = 40
    start_delay_ms: int = 3000
    replace_existing: bool = True
    press_enter: bool = False


@dataclass
class TypingResult:
    completed: bool
    typed_chars: int
    total_chars: int
    stopped: bool = False

    @property
    def message(self) -> str:
        if self.stopped:
            return f"已停止：已输入 {self.typed_chars}/{self.total_chars} 字"
        if self.completed:
            return f"完成：已输入 {self.typed_chars} 字"
        return f"输入中断：已输入 {self.typed_chars}/{self.total_chars} 字"


def normalize_text(text: str) -> str:
    """统一换行符；保留用户输入的空行。"""
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def type_text(
    options: TypingOptions,
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> TypingResult:
    """等待用户聚焦目标框，然后逐字输入。

    该函数在后台线程中调用；``should_stop`` 用于 F8/停止按钮中断。
    """
    text = normalize_text(options.text)
    total = len(text)
    if not text:
        return TypingResult(False, 0, 0, stopped=False)

    def stopped() -> bool:
        return bool(should_stop and should_stop())

    if on_status:
        on_status("等待目标文本框…")
    remaining = max(0, int(options.start_delay_ms))
    while remaining > 0:
        if stopped():
            return TypingResult(False, 0, total, stopped=True)
        step = min(50, remaining)
        time.sleep(step / 1000.0)
        remaining -= step

    if stopped():
        return TypingResult(False, 0, total, stopped=True)

    if options.replace_existing:
        if on_status:
            on_status("全选目标框原有内容…")
        winapi.select_all()
        time.sleep(0.05)
        if stopped():
            return TypingResult(False, 0, total, stopped=True)

    if on_status:
        on_status("正在逐字输入…")

    typed_count = 0

    def progress(done: int, count: int) -> None:
        nonlocal typed_count
        typed_count = done
        if on_progress is not None:
            on_progress(done, count)

    completed = winapi.type_unicode_text(
        text,
        interval=max(0, int(options.interval_ms)) / 1000.0,
        should_stop=should_stop,
        on_progress=progress,
    )
    if completed and options.press_enter and not stopped():
        winapi.type_unicode_text("\n", interval=0.0)
    return TypingResult(bool(completed), typed_count, total, stopped=stopped())
