"""控制器抽象层：统一 UI 与不同设备（Windows PC / Android 手机）的接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from ..engine import ClickConfig


class BaseController(ABC):
    """设备控制器基类。

    UI 只依赖本接口，通过 ``start / pause / resume / stop`` 控制执行；
    具体如何截屏、点击、找图，由各平台子类实现。
    """

    display_name = "未知设备"

    def __init__(self, emit: Callable[[dict], None]) -> None:
        self.emit = emit

    @abstractmethod
    def start(self, config: ClickConfig) -> tuple[bool, str]:
        """开始执行；返回 (是否成功, 失败原因)。"""

    @abstractmethod
    def pause(self) -> None:
        ...

    @abstractmethod
    def resume(self) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    @property
    @abstractmethod
    def is_running(self) -> bool:
        ...

    @property
    @abstractmethod
    def is_paused(self) -> bool:
        ...

    @abstractmethod
    def describe(self) -> str:
        """一句话描述当前设备能力（显示在界面底部）。"""
