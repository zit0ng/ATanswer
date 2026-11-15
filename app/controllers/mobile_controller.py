"""Android 手机控制器占位实现。

当前版本只做 Windows 端；手机端计划通过 ADB + uiautomator2（或 AutoX.js）
接入，届时只需实现同样的 BaseController 接口，UI 无需改动。
"""
from __future__ import annotations

from typing import Callable

from ..engine import ClickConfig
from .base import BaseController


class MobileController(BaseController):
    display_name = "Android 手机（尚未接入）"

    def __init__(self, emit: Callable[[dict], None]) -> None:
        super().__init__(emit)

    def start(self, config: ClickConfig) -> tuple[bool, str]:
        return (
            False,
            "当前版本仅支持 Windows 电脑端。手机端控制器将在后续版本接入"
            "（需要 ADB + uiautomator2，或使用 AutoX.js）。",
        )

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def stop(self) -> None:
        pass

    @property
    def is_running(self) -> bool:
        return False

    @property
    def is_paused(self) -> bool:
        return False

    def describe(self) -> str:
        return "手机端尚未接入"
