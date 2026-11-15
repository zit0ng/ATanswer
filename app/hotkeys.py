"""全局热键监听（RegisterHotKey + 独立消息循环线程）。

默认绑定：
    F6 -> 开始
    F7 -> 暂停 / 继续
    F8 -> 停止
"""
from __future__ import annotations

import threading
from typing import Callable

from . import winapi

HK_START = 1
HK_PAUSE = 2
HK_STOP = 3
HK_PICK = 4

HOTKEY_LABELS = {
    HK_START: "F6 单次答题",
    HK_PAUSE: "F7 单次填空",
    HK_STOP: "F8 AI看答案/再次识别",
    HK_PICK: "F9 拾取坐标",
}

DEFAULT_BINDINGS: dict[int, int] = {
    HK_START: winapi.VK_F6,
    HK_PAUSE: winapi.VK_F7,
    HK_STOP: winapi.VK_F8,
    HK_PICK: winapi.VK_F9,
}


class HotkeyManager(threading.Thread):
    """在后台线程注册全局热键，并把热键事件回调给 UI。"""

    def __init__(self, on_hotkey: Callable[[int], None]) -> None:
        super().__init__(name="HotkeyManager", daemon=True)
        self._on_hotkey = on_hotkey
        self._thread_id = 0
        self._stop_requested = False
        self._ready = threading.Event()
        self.registered: dict[int, bool] = {}
        self.errors: list[str] = []

    # -- 生命周期 ---------------------------------------------------------
    def start_and_wait(self, timeout: float = 1.5) -> bool:
        self.start()
        return self._ready.wait(timeout)

    def stop(self) -> None:
        self._stop_requested = True
        try:
            if self.registered:
                for hotkey_id in list(self.registered):
                    winapi.unregister_hotkey(hotkey_id)
                self.registered.clear()
            if self._thread_id:
                winapi.post_thread_quit(self._thread_id)
        except Exception:
            pass
        if self.is_alive():
            self.join(timeout=1.5)

    # -- 线程体 -----------------------------------------------------------
    def run(self) -> None:
        try:
            winapi.create_message_queue()
            self._thread_id = winapi.get_current_thread_id()
            for hotkey_id, vk in DEFAULT_BINDINGS.items():
                ok = winapi.register_hotkey(hotkey_id, vk)
                self.registered[hotkey_id] = ok
                if not ok:
                    self.errors.append(
                        f"全局热键 {HOTKEY_LABELS.get(hotkey_id, str(hotkey_id))} 注册失败"
                        "（可能被其它软件占用），请使用界面按钮操作。"
                    )
            self._ready.set()
            msg = winapi.MSG()
            while not self._stop_requested:
                ret = winapi.get_message(msg)
                if ret in (0, -1):
                    break
                if msg.message == winapi.WM_HOTKEY:
                    try:
                        self._on_hotkey(int(msg.wParam))
                    except Exception:
                        pass
        finally:
            self._ready.set()
            for hotkey_id in list(self.registered):
                if self.registered.get(hotkey_id):
                    winapi.unregister_hotkey(hotkey_id)
            self.registered.clear()
