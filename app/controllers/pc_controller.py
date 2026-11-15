"""Windows PC 控制器：抓屏 + 模板匹配 + SendInput 模拟鼠标。"""
from __future__ import annotations

import time
from typing import Callable

from .. import ai_client, png, winapi
from ..engine import ClickConfig, ClickEngine, validate_config
from ..matcher import Match, find_template
from ..templates import Template
from .base import BaseController

#: 实时监控时，判断"题目是否发生变化"的最小采样像素差异数量
_AI_MONITOR_MIN_DIFF = 12
#: 两次 AI 请求之间的最小间隔（秒），避免动画/闪烁导致频繁调用
_AI_MIN_INTERVAL = 0.7


class PCController(BaseController):
    display_name = "Windows PC"

    def __init__(self, emit: Callable[[dict], None]) -> None:
        super().__init__(emit)
        self._grabber: winapi.ScreenGrabber | None = None
        self._engine: ClickEngine | None = None
        self._config: ClickConfig | None = None
        self._template: Template | None = None
        self._ai_last_signature: bytes | None = None
        self._ai_last_call_time = 0.0
        self._ai_cooldown_until = 0.0

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self, config: ClickConfig) -> tuple[bool, str]:
        if self.is_running:
            return False, "任务已在运行中"
        ok, message = validate_config(config)
        if not ok:
            return False, message
        if config.mode == "image":
            template = config.template
            if not isinstance(template, Template):
                return False, "请先选择或截取模板图片"
            self._template = template
        elif config.mode == "ai":
            ai_ok, ai_message = ai_client.validate_config(config.ai_config)
            if not ai_ok:
                return False, ai_message
            self._template = None
            self._ai_last_signature = None
            self._ai_last_call_time = 0.0
        else:
            self._template = None

        grabber = None
        try:
            grabber = winapi.ScreenGrabber()
            grabber.capture()  # 预检：无桌面/权限不足时尽早报错
        except Exception as exc:  # noqa: BLE001
            if grabber is not None:
                try:
                    grabber.close()
                except Exception:
                    pass
            return False, f"屏幕抓取失败：{exc}"

        self._grabber = grabber
        self._config = config
        self._engine = ClickEngine(
            click_fn=self._click,
            match_fn=self._match_once,
            emit=self.emit,
            ai_fn=self._ai_step,
            ai_baseline_fn=self.mark_ai_baseline,
            scroll_fn=self._scroll_page,
        )
        if not self._engine.start(config):
            self._close_grabber()
            return False, "引擎启动失败"
        return True, ""

    def pause(self) -> None:
        if self._engine is not None:
            self._engine.pause()

    def resume(self) -> None:
        if self._engine is not None:
            self._engine.resume()

    def stop(self) -> None:
        if self._engine is not None:
            self._engine.stop()
            self._engine = None
        self._close_grabber()

    @property
    def is_running(self) -> bool:
        return self._engine is not None and self._engine.is_running

    @property
    def is_paused(self) -> bool:
        return self._engine is not None and self._engine.is_paused

    def describe(self) -> str:
        return "Windows PC 后端：GDI 抓屏 + 模板匹配 + SendInput 模拟点击"

    # ------------------------------------------------------------------
    # 引擎回调
    # ------------------------------------------------------------------
    def _click(self, x: int, y: int, button: str, double: bool, restore: bool) -> bool:
        return winapi.click_at(x, y, button=button, double=double, restore_cursor=restore)

    def _match_once(self) -> Match | None:
        grabber = self._grabber
        template = self._template
        config = self._config
        if grabber is None or template is None or config is None:
            return None
        grabber.capture()
        gray = grabber.gray()
        region = self._local_region(config.region, grabber)
        found = find_template(
            gray,
            grabber.width,
            grabber.height,
            template.gray,
            template.width,
            template.height,
            region=region,
            threshold=config.threshold,
        )
        if found is None:
            return None
        return Match(
            score=found.score,
            x=found.x + grabber.vx,
            y=found.y + grabber.vy,
            width=found.width,
            height=found.height,
            elapsed_ms=found.elapsed_ms,
            candidates=found.candidates,
        )

    # ------------------------------------------------------------------
    # 供 UI 使用的辅助能力
    # ------------------------------------------------------------------
    @staticmethod
    def _local_region(
        region: tuple[int, int, int, int] | None, grabber: winapi.ScreenGrabber
    ) -> tuple[int, int, int, int] | None:
        if region is None:
            return None
        x, y, w, h = region
        return x - grabber.vx, y - grabber.vy, w, h

    @staticmethod
    def _clamp_local_region(
        region: tuple[int, int, int, int], grabber: winapi.ScreenGrabber
    ) -> tuple[int, int, int, int]:
        x, y, w, h = (int(v) for v in region)
        x = max(0, min(x, grabber.width - 1))
        y = max(0, min(y, grabber.height - 1))
        w = max(1, min(w, grabber.width - x))
        h = max(1, min(h, grabber.height - y))
        return x, y, w, h

    # ------------------------------------------------------------------
    # AI 答题 / 实时监控
    # ------------------------------------------------------------------
    def _require_ai_ready(self) -> tuple[ClickConfig, winapi.ScreenGrabber]:
        cfg = self._config
        grabber = self._grabber
        if cfg is None or grabber is None or cfg.ai_config is None or cfg.ai_region is None:
            raise RuntimeError("AI 识别器未就绪")
        return cfg, grabber

    def _ai_local_region(self, grabber: winapi.ScreenGrabber) -> tuple[int, int, int, int]:
        cfg = self._config
        assert cfg is not None and cfg.ai_region is not None
        local = self._local_region(cfg.ai_region, grabber)
        assert local is not None
        return self._clamp_local_region(local, grabber)

    def _region_signature(
        self, grabber: winapi.ScreenGrabber, region: tuple[int, int, int, int]
    ) -> bytes:
        """对题目区域做降采样签名，用于快速判断画面是否变化。"""
        x, y, w, h = region
        gray = grabber.gray()
        sw = grabber.width
        step_x = max(1, w // 220)
        step_y = max(1, h // 160)
        chunks: list[bytes] = []
        for r in range(0, h, step_y):
            offset = (y + r) * sw + x
            chunks.append(gray[offset : offset + w : step_x])
        return b"".join(chunks)

    @staticmethod
    def _signature_changed(
        old: bytes, new: bytes, min_diff: int = _AI_MONITOR_MIN_DIFF
    ) -> bool:
        if not old or not new:
            return True
        if len(old) != len(new):
            return True
        diff = 0
        for a, b in zip(old, new):
            if a != b:
                diff += 1
                if diff >= min_diff:
                    return True
        return False

    def _attach_layout(
        self, answer, grabber: winapi.ScreenGrabber, local_region: tuple[int, int, int, int]
    ) -> None:
        """把 AI 返回的相对选项坐标换算成绝对屏幕坐标，附到 answer 上。"""
        x, y, w, h = local_region
        norm_points = getattr(answer, "norm_points", None) or {}
        click_points: dict[str, tuple[int, int]] = {}
        for letter, (nx, ny) in norm_points.items():
            ax = int(round(grabber.vx + x + nx * w))
            ay = int(round(grabber.vy + y + ny * h))
            click_points[letter] = (ax, ay)
        norm_next = getattr(answer, "norm_next", None)
        next_click = None
        if norm_next is not None:
            next_click = (
                int(round(grabber.vx + x + norm_next[0] * w)),
                int(round(grabber.vy + y + norm_next[1] * h)),
            )
        answer.click_points = click_points or None
        answer.next_click = next_click

    def _ai_answer_once(self):
        """AI 顺序答题：抓取题目区域截图 → 请求多模态模型 → 返回 AIAnswer。"""
        cfg, grabber = self._require_ai_ready()
        grabber.capture()
        local_region = self._ai_local_region(grabber)
        x, y, w, h = local_region
        rgb = grabber.crop_rgb(x, y, w, h)
        png_bytes = png.encode_png(w, h, rgb)
        if getattr(cfg, "ai_realtime_layout", True):
            answer = ai_client.ask_with_layout(png_bytes, cfg.ai_config)
            self._attach_layout(answer, grabber, local_region)
            return answer
        return ai_client.ask_question(png_bytes, cfg.ai_config)

    def _capture_stable_for_ai(self):
        """抓屏并确认画面已稳定；返回 (grabber, region, signature, png) 或 None。"""
        cfg, grabber = self._require_ai_ready()
        now = time.perf_counter()
        if now < self._ai_cooldown_until:
            return None  # 刚答完题，短暂冷却，避免滚动/选中效果触发重复作答
        if now - self._ai_last_call_time < _AI_MIN_INTERVAL:
            return None  # 冷却中，避免频繁调用 API

        grabber.capture()
        region = self._ai_local_region(grabber)
        signature = self._region_signature(grabber, region)
        if self._ai_last_signature is not None and not self._signature_changed(
            self._ai_last_signature, signature
        ):
            return None  # 页面没有变化，不重复作答

        # 等待画面停止滚动/切换：两次采样仍不同则本轮跳过，等下一轮
        time.sleep(0.1)
        grabber.capture()
        region = self._ai_local_region(grabber)
        signature2 = self._region_signature(grabber, region)
        if self._signature_changed(signature, signature2):
            return None
        x, y, w, h = region
        rgb = grabber.crop_rgb(x, y, w, h)
        png_bytes = png.encode_png(w, h, rgb)
        return grabber, region, signature2, png_bytes

    def _verify_ai_frame(self, grabber: winapi.ScreenGrabber, signature: bytes) -> bool:
        """确认 AI 请求期间画面没有变化；返回 False 表示本轮答案作废。"""
        grabber.capture()
        region_after = self._ai_local_region(grabber)
        signature_after = self._region_signature(grabber, region_after)
        if self._signature_changed(signature, signature_after):
            self.emit(
                {
                    "kind": "log",
                    "level": "warn",
                    "message": "AI 识别期间画面发生变化，已放弃本次答案（将重新识别）",
                }
            )
            self._ai_last_signature = signature_after
            return False
        self._ai_last_signature = signature
        self._ai_last_call_time = time.perf_counter()
        return True

    def _ai_monitor_once(self):
        """AI 实时监控：画面稳定变化后才请求模型，并在同一次请求里获取选项位置。"""
        cfg, _ = self._require_ai_ready()
        prepared = self._capture_stable_for_ai()
        if prepared is None:
            return None
        grabber, region, signature, png_bytes = prepared
        if getattr(cfg, "ai_realtime_layout", True):
            answer = ai_client.ask_with_layout(png_bytes, cfg.ai_config)
            self._attach_layout(answer, grabber, region)
        else:
            answer = ai_client.ask_question(png_bytes, cfg.ai_config)
        if not self._verify_ai_frame(grabber, signature):
            return None
        return answer

    @staticmethod
    def _point_looks_selected(grabber, rgb: bytes, point: tuple[int, int], radius: int = 7) -> bool:
        """在选项点击点附近采样主题蓝色，识别“已经选中”的按钮（兜底 AI selected）。"""
        lx = int(round(point[0] - grabber.vx))
        ly = int(round(point[1] - grabber.vy))
        width, height = int(grabber.width), int(grabber.height)
        if not (0 <= lx < width and 0 <= ly < height):
            return False
        x0, x1 = max(0, lx - radius), min(width, lx + radius + 1)
        y0, y1 = max(0, ly - radius), min(height, ly + radius + 1)
        total = 0
        blue = 0
        for y in range(y0, y1):
            base = y * width * 3
            for x in range(x0, x1):
                index = base + x * 3
                if index + 2 >= len(rgb):
                    continue
                r, g, b = rgb[index], rgb[index + 1], rgb[index + 2]
                total += 1
                if b >= 110 and b - r >= 25 and b - g >= 10:
                    blue += 1
        return total > 0 and blue / total >= 0.18

    def _augment_selected_from_pixels(self, grabber, page, abs_points) -> None:
        """AI 没返回 selected 时，用选项按钮的蓝色高亮做一次兜底判断。"""
        try:
            rgb = grabber.rgb()
        except Exception:  # noqa: BLE001
            return
        for position, question in enumerate(getattr(page, "questions", []) or []):
            if position >= len(abs_points):
                break
            selected = {
                str(item).upper()
                for item in (getattr(question, "selected", None) or [])
                if str(item).strip()
            }
            mapping = abs_points[position] or {}
            for letter, point in mapping.items():
                if str(letter).upper() in selected:
                    continue
                if self._point_looks_selected(grabber, rgb, point):
                    selected.add(str(letter).upper())
            question.selected = sorted(selected)

    @staticmethod
    def _page_tiles(
        region: tuple[int, int, int, int], enabled: bool
    ) -> list[tuple[int, int, int, int]]:
        """全屏/大区域分块：2x2 带重叠，避免图片被模型压缩后字太小。"""
        x, y, w, h = region
        if not enabled or w * h < 1_000_000:
            return [(x, y, w, h)]
        overlap_x = max(40, int(w * 0.06))
        overlap_y = max(40, int(h * 0.06))
        tile_w = min(w, (w + overlap_x) // 2)
        tile_h = min(h, (h + overlap_y) // 2)
        return [
            (x, y, tile_w, tile_h),
            (x + w - tile_w, y, tile_w, tile_h),
            (x, y + h - tile_h, tile_w, tile_h),
            (x + w - tile_w, y + h - tile_h, tile_w, tile_h),
        ]

    def _ai_page_tiled(self, cfg, grabber, region):
        """把大区域拆成 2x2 小块分别识别，再合并题目与绝对坐标。"""
        x, y, w, h = region
        merged_questions: list = []
        abs_points: list[dict[str, tuple[int, int]]] = []
        raw_parts: list[str] = []
        elapsed_ms = 0.0
        next_click: tuple[int, int] | None = None
        seen_keys: set[str] = set()
        errors: list[str] = []
        for tile_x, tile_y, tile_w, tile_h in self._page_tiles(region, True):
            rgb = grabber.crop_rgb(tile_x, tile_y, tile_w, tile_h)
            tile_png = png.encode_png(tile_w, tile_h, rgb)
            try:
                tile_page = ai_client.ask_page(tile_png, cfg.ai_config)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
                self.emit(
                    {
                        "kind": "log",
                        "level": "warn",
                        "message": f"分块识别失败（{tile_w}×{tile_h}）：{exc}",
                    }
                )
                continue
            raw_parts.append(tile_page.raw)
            elapsed_ms += tile_page.elapsed_ms
            for question in tile_page.questions:
                key = str(getattr(question, "key", "") or "").strip().lower()
                if key and key in seen_keys:
                    continue  # 重叠区域里的同一道题只保留一次
                if key:
                    seen_keys.add(key)
                mapping: dict[str, tuple[int, int]] = {}
                for letter, (nx, ny) in question.norm_points.items():
                    mapping[letter] = (
                        int(round(grabber.vx + tile_x + nx * tile_w)),
                        int(round(grabber.vy + tile_y + ny * tile_h)),
                    )
                merged_questions.append(question)
                abs_points.append(mapping)
            if next_click is None and tile_page.norm_next is not None:
                next_click = (
                    int(round(grabber.vx + tile_x + tile_page.norm_next[0] * tile_w)),
                    int(round(grabber.vy + tile_y + tile_page.norm_next[1] * tile_h)),
                )
        if errors and not merged_questions:
            raise OSError("分块识别全部失败：" + errors[0])
        page = ai_client.PageAnswer(
            questions=merged_questions,
            norm_next=None,
            raw="\n".join(raw_parts),
            elapsed_ms=elapsed_ms,
        )
        return page, abs_points, next_click

    def _ai_page_once(self):
        """整页多题：识别页面内所有未作答题，并换算每题的选项坐标。"""
        cfg, _ = self._require_ai_ready()
        prepared = self._capture_stable_for_ai()
        if prepared is None:
            return None
        grabber, region, signature, png_bytes = prepared
        x, y, w, h = region
        next_click: tuple[int, int] | None = None
        if getattr(cfg, "ai_tile_mode", False):
            try:
                page, abs_points, next_click = self._ai_page_tiled(cfg, grabber, region)
            except OSError as exc:
                # 分块请求全部失败（常见于网络抖动/远程断开）时，退回整图请求一次。
                self.emit(
                    {
                        "kind": "log",
                        "level": "warn",
                        "message": f"分块识别全部失败（{exc}），改用整图重试一次…",
                    }
                )
                page = ai_client.ask_page(png_bytes, cfg.ai_config)
                abs_points = []
                for question in page.questions:
                    mapping: dict[str, tuple[int, int]] = {}
                    for letter, (nx, ny) in question.norm_points.items():
                        mapping[letter] = (
                            int(round(grabber.vx + x + nx * w)),
                            int(round(grabber.vy + y + ny * h)),
                        )
                    abs_points.append(mapping)
        else:
            page = ai_client.ask_page(png_bytes, cfg.ai_config)
            abs_points = []
            for question in page.questions:
                mapping: dict[str, tuple[int, int]] = {}
                for letter, (nx, ny) in question.norm_points.items():
                    mapping[letter] = (
                        int(round(grabber.vx + x + nx * w)),
                        int(round(grabber.vy + y + ny * h)),
                    )
                abs_points.append(mapping)
        page.abs_points = abs_points
        self._augment_selected_from_pixels(grabber, page, abs_points)
        if next_click is not None:
            page.next_click = next_click
        elif page.norm_next is not None:
            page.next_click = (
                int(round(grabber.vx + x + page.norm_next[0] * w)),
                int(round(grabber.vy + y + page.norm_next[1] * h)),
            )
        if not self._verify_ai_frame(grabber, signature):
            return None
        return page

    def _ai_step(self):
        """引擎入口：按配置选择顺序答题、实时监控或整页多题。"""
        cfg = self._config
        if cfg is None:
            raise RuntimeError("AI 识别器未就绪")
        if cfg.ai_monitor:
            if cfg.ai_page_mode:
                return self._ai_page_once()
            return self._ai_monitor_once()
        return self._ai_answer_once()

    def mark_ai_baseline(self) -> None:
        """答题完成（选项已点击）后，把当前画面记为新的基线，避免重复作答。"""
        grabber = self._grabber
        if grabber is None or self._config is None or self._config.ai_region is None:
            return
        grabber.capture()
        region = self._ai_local_region(grabber)
        self._ai_last_signature = self._region_signature(grabber, region)
        self._ai_last_call_time = time.perf_counter()
        # 手动翻页模式下，给用户一点时间滚动/点击，避免重复作答同一题
        if self._config.ai_monitor and not self._config.ai_auto_next:
            self._ai_cooldown_until = time.perf_counter() + 2.0

    def answer_once(
        self,
        region: tuple[int, int, int, int],
        ai_config,
        *,
        page_mode: bool = False,
        realtime_layout: bool = True,
        auto_next: bool = True,
        option_wait_ms: int = 300,
        next_wait_ms: int = 600,
        fallback_options: dict[str, tuple[int, int]] | None = None,
        fallback_next: tuple[int, int] | None = None,
    ) -> dict:
        """单次答题：截取指定区域 → AI 识别 → 点击答案（可含多题/多选）→ 可选下一题。

        这是"框选一次、按钮点一次、答一次"的手动作答流程，不启动实时监控。
        返回结果摘要（answers/clicks/next/elapsed_ms/raw），供 UI 写日志。
        """
        ok, message = ai_client.validate_config(ai_config)
        if not ok:
            raise ValueError(message)
        fallback_options = fallback_options or {}
        started = time.perf_counter()
        result: dict = {
            "mode": "page" if page_mode else "single",
            "answers": [],
            "questions": [],
            "clicks": 0,
            "next": False,
            "elapsed_ms": 0.0,
            "raw": "",
        }
        with winapi.ScreenGrabber() as grabber:
            grabber.capture()
            local = self._local_region(region, grabber)
            assert local is not None
            x, y, w, h = self._clamp_local_region(local, grabber)
            rgb = grabber.crop_rgb(x, y, w, h)
            png_bytes = png.encode_png(w, h, rgb)
            try:
                screen_rgb = grabber.rgb()
            except Exception:  # noqa: BLE001
                screen_rgb = b""

            def to_abs(nx: float, ny: float) -> tuple[int, int]:
                return (
                    int(round(grabber.vx + x + nx * w)),
                    int(round(grabber.vy + y + ny * h)),
                )

            def click(point: tuple[int, int]) -> bool:
                return winapi.click_at(
                    int(point[0]), int(point[1]), button="left", double=False,
                    restore_cursor=True,
                )

            if page_mode:
                page = ai_client.ask_page(png_bytes, ai_config)
                result["raw"] = page.raw
                questions = list(page.questions or [])
                for position, question in enumerate(questions):
                    letters = [
                        str(item).upper()
                        for item in (getattr(question, "letters", None) or [])
                        if str(item).strip()
                    ]
                    if not letters:
                        raw_letter = str(getattr(question, "letter", "")).upper()
                        letters = [char for char in raw_letter if char in "ABCDEF"]
                    clicked: list[str] = []
                    selected = {
                        str(item).upper()
                        for item in (getattr(question, "selected", None) or [])
                        if str(item).strip()
                    }
                    for letter in letters:
                        norm = (question.norm_points or {}).get(letter)
                        if norm is not None:
                            point = to_abs(*norm)
                        else:
                            point = fallback_options.get(letter)
                        if point is None:
                            continue
                        if letter in selected or (
                            screen_rgb
                            and self._point_looks_selected(grabber, screen_rgb, point)
                        ):
                            continue
                        if not click(point):
                            raise OSError(f"点击选项 {letter} 失败（坐标 {point}）")
                        clicked.append(letter)
                        result["clicks"] += 1
                        time.sleep(max(0.0, option_wait_ms / 1000.0))
                    if clicked:
                        result["answers"].append("、".join(clicked))
                        result["questions"].append(
                            {
                                "index": int(getattr(question, "index", position + 1)),
                                "letters": clicked,
                            }
                        )
                next_point = (
                    to_abs(*page.norm_next) if page.norm_next is not None else None
                )
            else:
                if realtime_layout:
                    answer = ai_client.ask_with_layout(png_bytes, ai_config)
                    norm_points = getattr(answer, "norm_points", None) or {}
                    norm_next = getattr(answer, "norm_next", None)
                else:
                    answer = ai_client.ask_question(png_bytes, ai_config)
                    norm_points = {}
                    norm_next = None
                result["raw"] = getattr(answer, "raw", "")
                letters = [
                    str(item).upper() for item in getattr(answer, "letters", None) or []
                ] or [
                    str(getattr(answer, "letter", "")).upper()
                ]
                letters = [char for item in letters for char in item if char in "ABCDEF"]
                clicked = []
                skipped_selected = 0
                for letter in letters:
                    norm = norm_points.get(letter)
                    if norm is not None:
                        point = to_abs(*norm)
                    else:
                        point = fallback_options.get(letter)
                    if point is None:
                        continue
                    if screen_rgb and self._point_looks_selected(grabber, screen_rgb, point):
                        skipped_selected += 1
                        continue
                    if not click(point):
                        raise OSError(f"点击选项 {letter} 失败（坐标 {point}）")
                    clicked.append(letter)
                    result["clicks"] += 1
                    time.sleep(max(0.0, option_wait_ms / 1000.0))
                if not clicked and not skipped_selected:
                    raise OSError("AI 没有找到可点击的选项坐标，请重新框选题目区域")
                if clicked:
                    result["answers"].append("、".join(clicked))
                    result["questions"].append({"index": 1, "letters": clicked})
                next_point = to_abs(*norm_next) if norm_next is not None else None

            next_point = next_point or fallback_next
            if auto_next and next_point is not None:
                if not click(next_point):
                    raise OSError(f"点击「下一题」失败（坐标 {next_point}）")
                result["next"] = True
                time.sleep(max(0.0, next_wait_ms / 1000.0))
        result["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
        return result

    def fill_once(
        self,
        region: tuple[int, int, int, int],
        ai_config,
        *,
        auto_next: bool = True,
        next_wait_ms: int = 600,
        fill_wait_ms: int = 120,
        typing: bool = True,
        overwrite: bool = False,
        offset_y: int = 0,
        fallback_next: tuple[int, int] | None = None,
    ) -> dict:
        """单次填空：截取区域 → AI 给出题号/答案/输入框位置 → 自动点击并填写。

        typing=True 时用逐字键盘输入（不经过剪贴板，兼容禁止粘贴的页面）。
        返回结果摘要（items/skipped/clicks/next/elapsed_ms/raw），供 UI 写日志。
        """
        ok, message = ai_client.validate_config(ai_config)
        if not ok:
            raise ValueError(message)
        started = time.perf_counter()
        result: dict = {
            "items": [],
            "skipped": [],
            "clicks": 0,
            "next": False,
            "elapsed_ms": 0.0,
            "raw": "",
        }
        with winapi.ScreenGrabber() as grabber:
            grabber.capture()
            local = self._local_region(region, grabber)
            assert local is not None
            x, y, w, h = self._clamp_local_region(local, grabber)
            rgb = grabber.crop_rgb(x, y, w, h)
            png_bytes = png.encode_png(w, h, rgb)

            def to_abs(nx: float, ny: float) -> tuple[int, int]:
                return (
                    int(round(grabber.vx + x + nx * w)),
                    int(round(grabber.vy + y + ny * h)),
                )

            answer = ai_client.ask_fill(png_bytes, ai_config, overwrite=overwrite)
            result["raw"] = answer.raw
            for item in answer.items:
                if item.norm_point is None:
                    result["skipped"].append(
                        {"index": item.index, "answer": item.answer, "reason": "未定位到输入框"}
                    )
                    continue
                point = to_abs(*item.norm_point)
                if offset_y:
                    point = (point[0], point[1] + int(offset_y))
                if not winapi.click_at(point[0], point[1], button="left", restore_cursor=True):
                    result["skipped"].append(
                        {"index": item.index, "answer": item.answer, "reason": "点击输入框失败"}
                    )
                    continue
                time.sleep(max(0.05, fill_wait_ms / 1000.0))
                filled = (
                    winapi.fill_textbox_typing(item.answer)
                    if typing
                    else winapi.fill_textbox(item.answer)
                )
                if not filled:
                    result["skipped"].append(
                        {"index": item.index, "answer": item.answer, "reason": "输入答案失败"}
                    )
                    continue
                time.sleep(max(0.05, fill_wait_ms / 1000.0))
                result["items"].append(
                    {
                        "index": item.index,
                        "slot": getattr(item, "slot", 0),
                        "answer": item.answer,
                        "point": point,
                    }
                )
                result["clicks"] += 1
            next_point = to_abs(*answer.norm_next) if answer.norm_next is not None else None
            next_point = next_point or fallback_next
            if auto_next and next_point is not None:
                if winapi.click_at(next_point[0], next_point[1], button="left", restore_cursor=True):
                    result["next"] = True
                    time.sleep(max(0.0, next_wait_ms / 1000.0))
        result["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
        return result

    def _scroll_page(self) -> bool:
        """向下滚动一屏并等待画面稳定；返回是否发现新内容。"""
        cfg, grabber = self._require_ai_ready()
        grabber.capture()
        region = self._ai_local_region(grabber)
        before = self._region_signature(grabber, region)
        x, y, w, h = region
        center_x = grabber.vx + x + w // 2
        center_y = grabber.vy + y + h // 2
        steps = max(1, int(cfg.ai_scroll_steps))
        if not winapi.scroll_wheel(steps, center_x, center_y, restore_cursor=False):
            raise OSError("模拟滚轮失败（可能被系统/权限拦截）")
        deadline = time.perf_counter() + max(
            0.5, float(cfg.ai_scroll_wait_ms) / 1000.0 + 0.6
        )
        last = before
        final = before
        stable = 0
        while time.perf_counter() < deadline:
            time.sleep(0.08)
            grabber.capture()
            region = self._ai_local_region(grabber)
            signature = self._region_signature(grabber, region)
            final = signature
            if not self._signature_changed(last, signature):
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
            last = signature
        return self._signature_changed(before, final)

    def test_ai(self, region: tuple[int, int, int, int], ai_config) -> dict:
        """截取题目区域并请求 AI（供 UI 的"测试识别"使用）。"""
        ok, message = ai_client.validate_config(ai_config)
        if not ok:
            raise ValueError(message)
        with winapi.ScreenGrabber() as grabber:
            grabber.capture()
            local = self._local_region(region, grabber)
            assert local is not None
            x, y, w, h = self._clamp_local_region(local, grabber)
            rgb = grabber.crop_rgb(x, y, w, h)
            png_bytes = png.encode_png(w, h, rgb)
            answer = ai_client.ask_question(png_bytes, ai_config)
            return {
                "png": png_bytes,
                "answer": answer,
                "width": w,
                "height": h,
                "model": ai_config.model,
                "api": ai_config.base_url,
            }

    def test_match(
        self,
        template: Template,
        region: tuple[int, int, int, int] | None,
        threshold: float,
    ) -> dict:
        """立即执行一次"抓屏 + 找图"，返回预览数据（不点击）。"""
        with winapi.ScreenGrabber() as grabber:
            grabber.capture()
            rgb = grabber.rgb()
            gray = grabber.gray()
            local_region = self._local_region(region, grabber)
            found = find_template(
                gray,
                grabber.width,
                grabber.height,
                template.gray,
                template.width,
                template.height,
                region=local_region,
                threshold=threshold,
            )
            result = {
                "match": None,
                "rgb": rgb,
                "screen_width": grabber.width,
                "screen_height": grabber.height,
                "origin": (grabber.vx, grabber.vy),
                "elapsed_ms": 0.0,
            }
            if found is not None:
                result["match"] = Match(
                    score=found.score,
                    x=found.x + grabber.vx,
                    y=found.y + grabber.vy,
                    width=found.width,
                    height=found.height,
                    elapsed_ms=found.elapsed_ms,
                    candidates=found.candidates,
                )
                result["elapsed_ms"] = found.elapsed_ms
            return result

    def capture_region(self, region: tuple[int, int, int, int]) -> tuple[bytes, int, int]:
        """截取屏幕区域并返回 (rgb, width, height)，坐标使用虚拟桌面绝对坐标。"""
        x, y, w, h = region
        with winapi.ScreenGrabber() as grabber:
            grabber.capture()
            local = self._local_region(region, grabber)
            assert local is not None
            lx, ly, lw, lh = self._clamp_local_region(local, grabber)
            rgb = grabber.crop_rgb(lx, ly, lw, lh)
            return rgb, lw, lh

    def _close_grabber(self) -> None:
        if self._grabber is not None:
            try:
                self._grabber.close()
            except Exception:
                pass
            self._grabber = None
