"""设备识别：根据运行平台创建对应的控制器。"""
from __future__ import annotations

import sys
from typing import Callable

from ..engine import ClickConfig
from .base import BaseController
from .mobile_controller import MobileController


class UnsupportedController(BaseController):
    display_name = "不支持的平台"

    def start(self, config: ClickConfig) -> tuple[bool, str]:
        return False, f"当前平台（{sys.platform}）暂不支持，本程序目前仅支持 Windows。"

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
        return "仅支持 Windows"


def detect_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    return sys.platform


def create_controller(emit: Callable[[dict], None]) -> BaseController:
    """按平台创建控制器。当前 Windows 为完整实现，手机端为占位。"""
    platform = detect_platform()
    if platform == "windows":
        from .pc_controller import PCController

        return PCController(emit)
    if platform in ("linux", "macos"):
        return MobileController(emit)  # 预留：后续可新增桌面端控制器
    return UnsupportedController(emit)
