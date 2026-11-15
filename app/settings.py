"""用户设置与持久化。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .paths import SETTINGS_FILE


@dataclass
class Settings:
    # 模式：fixed = 定点连点，image = 找图点击
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
    region_mode: str = "full"  # full / custom
    region: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    threshold: int = 90
    click_offset_x: int = 0
    click_offset_y: int = 0
    poll_ms: int = 500
    # AI 答题
    ai_base_url: str = "https://api.deepseek.com"
    ai_api_key: str = ""
    ai_model: str = "deepseek-flash"
    ai_proxy: str = ""
    ai_timeout_s: int = 60
    ai_enable_thinking: bool = False
    ai_region: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    ai_options: dict[str, list[int]] = field(default_factory=dict)
    ai_next_point: list[int] = field(default_factory=lambda: [0, 0])
    ai_option_wait_ms: int = 400
    ai_next_wait_ms: int = 1500
    ai_delay_ms: int = 1000
    ai_count_limit: int = 0
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
    ai_fill_typing: bool = True
    ai_fill_overwrite: bool = False
    ai_fill_offset_y: int = 0
    ai_show_region: bool = True
    show_region_box: bool = True
    # 其它
    topmost: bool = False
    hotkeys_enabled: bool = True
    window_geometry: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        settings = cls()
        if not isinstance(data, dict):
            return settings
        for f in fields(cls):
            if f.name not in data:
                continue
            value = data[f.name]
            try:
                if f.name in ("region", "ai_region"):
                    numbers = [int(v) for v in value]
                    if len(numbers) == 4:
                        setattr(settings, f.name, numbers)
                elif f.name == "ai_next_point":
                    numbers = [int(v) for v in value]
                    if len(numbers) == 2:
                        settings.ai_next_point = numbers
                elif f.name == "ai_options":
                    if isinstance(value, dict):
                        options: dict[str, list[int]] = {}
                        for key, point in value.items():
                            letter = str(key).strip().upper()
                            if letter not in "ABCDEF" or not isinstance(point, (list, tuple)):
                                continue
                            numbers = [int(v) for v in point]
                            if len(numbers) == 2:
                                options[letter] = numbers
                        settings.ai_options = options
                elif isinstance(getattr(settings, f.name), bool):
                    setattr(settings, f.name, bool(value))
                elif isinstance(getattr(settings, f.name), int):
                    setattr(settings, f.name, int(value))
                elif isinstance(getattr(settings, f.name), str):
                    setattr(settings, f.name, str(value))
            except (TypeError, ValueError):
                continue
        return settings

    def normalize(self) -> None:
        """纠正明显不合法的字段，避免界面崩溃。"""
        if self.mode not in ("fixed", "image", "ai"):
            self.mode = "fixed"
        if self.button not in ("left", "right", "middle"):
            self.button = "left"
        if self.region_mode not in ("full", "custom"):
            self.region_mode = "full"
        self.interval_ms = max(10, min(3_600_000, int(self.interval_ms)))
        self.interval_min_ms = max(10, min(3_600_000, int(self.interval_min_ms)))
        self.interval_max_ms = max(self.interval_min_ms, min(3_600_000, int(self.interval_max_ms)))
        self.count_limit = max(0, int(self.count_limit))
        self.start_delay_ms = max(0, min(600_000, int(self.start_delay_ms)))
        self.threshold = max(50, min(100, int(self.threshold)))
        self.poll_ms = max(50, min(60_000, int(self.poll_ms)))
        self.click_offset_x = int(self.click_offset_x)
        self.click_offset_y = int(self.click_offset_y)
        # AI 答题
        self.ai_timeout_s = max(5, min(300, int(self.ai_timeout_s)))
        self.ai_enable_thinking = bool(self.ai_enable_thinking)
        self.ai_option_wait_ms = max(0, min(10_000, int(self.ai_option_wait_ms)))
        self.ai_next_wait_ms = max(100, min(60_000, int(self.ai_next_wait_ms)))
        self.ai_delay_ms = max(0, min(60_000, int(self.ai_delay_ms)))
        self.ai_count_limit = max(0, int(self.ai_count_limit))
        self.ai_monitor = bool(self.ai_monitor)
        self.ai_auto_next = bool(self.ai_auto_next)
        self.ai_poll_ms = max(100, min(10_000, int(self.ai_poll_ms)))
        self.ai_realtime_layout = bool(self.ai_realtime_layout)
        self.ai_page_mode = bool(self.ai_page_mode)
        self.ai_fullscreen = bool(self.ai_fullscreen)
        self.ai_auto_scroll = bool(self.ai_auto_scroll)
        self.ai_tile_mode = bool(self.ai_tile_mode)
        self.ai_scroll_steps = max(1, min(50, int(self.ai_scroll_steps)))
        self.ai_scroll_wait_ms = max(0, min(10_000, int(self.ai_scroll_wait_ms)))
        self.ai_scroll_max = max(0, min(10_000, int(self.ai_scroll_max)))
        self.ai_fill_typing = bool(self.ai_fill_typing)
        self.ai_fill_overwrite = bool(self.ai_fill_overwrite)
        self.ai_fill_offset_y = max(-400, min(400, int(self.ai_fill_offset_y)))
        if self.ai_base_url is None:
            self.ai_base_url = ""
        if self.ai_model is None:
            self.ai_model = ""
        if self.ai_api_key is None:
            self.ai_api_key = ""


def load_settings(path: str | Path = SETTINGS_FILE) -> Settings:
    file = Path(path)
    if not file.exists():
        settings = Settings()
        settings.normalize()
        return settings
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        settings = Settings.from_dict(data)
    except (OSError, ValueError):
        settings = Settings()
    settings.normalize()
    return settings


def save_settings(settings: Settings, path: str | Path = SETTINGS_FILE) -> bool:
    file = Path(path)
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, file)
        return True
    except OSError:
        return False
