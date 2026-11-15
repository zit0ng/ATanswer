"""识别测试结果预览窗口。"""
from __future__ import annotations

import math
import tkinter as tk
from typing import Callable

from . import png, winapi
from .matcher import Match
from .paths import temp_png_path

_BG = "#0b0f16"
_CARD = "#141a26"
_BORDER = "#232c3d"
_TEXT = "#e8eef8"
_MUTED = "#93a0b5"
_DIM = "#6b7789"
_ACCENT = "#3d8bfd"
_SUCCESS = "#34d399"
_DANGER = "#f87171"
_WARNING = "#fbbf24"


class MatchPreview:
    """展示一次"抓屏 + 找图"的结果（截图 + 标记框 + 统计信息）。"""

    def __init__(
        self,
        master: tk.Misc,
        result: dict,
        template,
        on_retry: Callable[[], None] | None = None,
        title: str = "识别测试结果",
    ) -> None:
        self.window = tk.Toplevel(master)
        self.window.title(title)
        self.window.configure(bg=_BG)
        self.window.minsize(640, 420)
        try:
            self.window.attributes("-topmost", True)
            self.window.transient(master)
        except tk.TclError:  # pragma: no cover
            pass
        self._on_retry = on_retry
        self._photo = None
        self._display_photo = None

        screen_w = int(result.get("screen_width") or 0) or 1
        screen_h = int(result.get("screen_height") or 0) or 1
        win_w = min(int(screen_w * 0.66), 1500)
        win_h = min(int(screen_h * 0.72), 940)
        self.window.geometry(f"{win_w}x{win_h}")
        self._center_window(win_w, win_h)

        match = result.get("match")
        elapsed = float(result.get("elapsed_ms") or 0.0)

        # ------------------------------------------------------------------
        # 顶部状态
        # ------------------------------------------------------------------
        header = tk.Frame(self.window, bg=_BG)
        header.pack(fill="x", padx=22, pady=(18, 10))
        if isinstance(match, Match):
            status_text = (
                f"识别成功 · 相似度 {match.score * 100:.1f}% · 耗时 {match.elapsed_ms:.1f} ms"
            )
            status_color = _SUCCESS
        else:
            status_text = f"未找到目标 · 耗时 {elapsed:.1f} ms"
            status_color = _DANGER
        tk.Label(
            header,
            text=status_text,
            bg=_BG,
            fg=status_color,
            font=("Microsoft YaHei UI", 15, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Label(
            header,
            text=f"屏幕 {screen_w}×{screen_h}",
            bg=_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 10),
            anchor="e",
        ).pack(side="right")

        # ------------------------------------------------------------------
        # 截图预览
        # ------------------------------------------------------------------
        self._canvas_wrap = tk.Frame(self.window, bg=_CARD, highlightthickness=1,
                                     highlightbackground=_BORDER)
        self._canvas_wrap.pack(fill="both", expand=True, padx=22, pady=(0, 10))
        self._canvas = tk.Canvas(
            self._canvas_wrap, bg="#05070b", highlightthickness=0, bd=0
        )
        self._canvas.pack(expand=True)

        photo = self._load_photo(result)
        if photo is None:
            self._canvas.create_text(
                240, 120, text="截图预览不可用", fill=_MUTED, font=("Microsoft YaHei UI", 13)
            )
            self._canvas.configure(width=480, height=240)
        else:
            self._photo = photo
            avail_w = max(200, win_w - 80)
            avail_h = max(160, win_h - 220)
            factor = max(1, math.ceil(max(screen_w / avail_w, screen_h / avail_h)))
            # 按窗口自适应：截图的显示尺寸不超过可用区域
            while screen_w // factor > avail_w or screen_h // factor > avail_h:
                factor += 1
            display = photo if factor <= 1 else photo.subsample(factor, factor)
            self._display_photo = display
            disp_w, disp_h = display.width(), display.height()
            self._factor = factor
            self._canvas.configure(width=disp_w, height=disp_h)
            self._canvas.create_image(0, 0, image=display, anchor="nw")
            self._draw_overlay(result, match, factor, disp_w, disp_h)

        # ------------------------------------------------------------------
        # 信息行
        # ------------------------------------------------------------------
        info = tk.Frame(self.window, bg=_BG)
        info.pack(fill="x", padx=22, pady=(0, 12))
        if isinstance(match, Match):
            info_text = (
                f"匹配位置：({match.x}, {match.y})    模板尺寸：{template.width}×{template.height}    "
                f"候选：{match.candidates}"
            )
        else:
            info_text = (
                "建议：降低相似度阈值、重新框选更有特征的模板；若目标在动画中，可适当降低轮询间隔。"
            )
        tk.Label(
            info,
            text=info_text,
            bg=_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 10),
            anchor="w",
            justify="left",
        ).pack(side="left")

        # ------------------------------------------------------------------
        # 按钮
        # ------------------------------------------------------------------
        btns = tk.Frame(self.window, bg=_BG)
        btns.pack(fill="x", padx=22, pady=(0, 18))
        if on_retry is not None:
            tk.Button(
                btns,
                text="重新测试",
                command=self._retry,
                bg=_ACCENT,
                fg="#ffffff",
                activebackground="#5a9eff",
                activeforeground="#ffffff",
                relief="flat",
                bd=0,
                padx=18,
                pady=7,
                font=("Microsoft YaHei UI", 11, "bold"),
                cursor="hand2",
            ).pack(side="left")
        tk.Button(
            btns,
            text="关闭",
            command=self._close,
            bg=_CARD,
            fg=_TEXT,
            activebackground="#1d2534",
            activeforeground=_TEXT,
            relief="flat",
            bd=0,
            padx=18,
            pady=7,
            font=("Microsoft YaHei UI", 11),
            cursor="hand2",
        ).pack(side="right")
        self.window.bind("<Escape>", lambda _e: self._close())
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.lift()
        self.window.focus_force()
        try:
            self.window.update_idletasks()
            winapi.enable_dark_titlebar(winapi.get_toplevel_hwnd(self.window))
        except Exception:  # pragma: no cover
            pass

    # ------------------------------------------------------------------
    def _load_photo(self, result: dict):
        rgb = result.get("rgb")
        if not rgb:
            return None
        path = temp_png_path("preview")
        try:
            png.save_png(path, int(result["screen_width"]), int(result["screen_height"]), rgb)
            photo = tk.PhotoImage(file=str(path), master=self.window)
            return photo
        except Exception:  # pragma: no cover - 预览失败不应影响主流程
            return None
        finally:
            try:
                path.unlink(missing_ok=True)
            except (OSError, UnboundLocalError):
                pass

    def _draw_overlay(self, result: dict, match, factor: int, disp_w: int, disp_h: int) -> None:
        ox, oy = result.get("origin") or (0, 0)
        # 区域标记
        region = result.get("region")
        if region:
            rx, ry, rw, rh = region
            x0 = (rx - ox) / factor
            y0 = (ry - oy) / factor
            self._canvas.create_rectangle(
                x0,
                y0,
                x0 + rw / factor,
                y0 + rh / factor,
                outline="#fbbf24",
                width=2,
                dash=(5, 4),
            )
        if isinstance(match, Match):
            x0 = (match.x - ox) / factor
            y0 = (match.y - oy) / factor
            x1 = (match.x - ox + match.width) / factor
            y1 = (match.y - oy + match.height) / factor
            self._canvas.create_rectangle(x0, y0, x1, y1, outline=_SUCCESS, width=3)
            cx = (x0 + x1) / 2
            cy = (y0 + y1) / 2
            self._canvas.create_line(cx - 12, cy, cx + 12, cy, fill=_SUCCESS, width=2)
            self._canvas.create_line(cx, cy - 12, cx, cy + 12, fill=_SUCCESS, width=2)
            label = f"{match.score * 100:.0f}%"
            tx = min(max(x0, 4), disp_w - 80)
            ty = max(y0 - 26, 4)
            self._canvas.create_text(
                tx,
                ty,
                text=label,
                fill="#04120c",
                anchor="nw",
                font=("Consolas", 11, "bold"),
            )

    def _center_window(self, w: int, h: int) -> None:
        try:
            sw = self.window.winfo_screenwidth()
            sh = self.window.winfo_screenheight()
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 2)
            self.window.geometry(f"{w}x{h}+{x}+{y}")
        except tk.TclError:  # pragma: no cover
            pass

    def _retry(self) -> None:
        self._close()
        if callable(self._on_retry):
            self._on_retry()

    def _close(self) -> None:
        try:
            self.window.destroy()
        except tk.TclError:  # pragma: no cover
            pass


class AIResultPreview:
    """展示一次 AI 题目识别结果：题目截图 + AI 答案 + 模型原始输出。"""

    def __init__(
        self,
        master: tk.Misc,
        result: dict,
        on_retry: Callable[[], None] | None = None,
        title: str = "AI 识别结果",
    ) -> None:
        self.window = tk.Toplevel(master)
        self.window.title(title)
        self.window.configure(bg=_BG)
        self.window.minsize(680, 480)
        try:
            self.window.attributes("-topmost", True)
            self.window.transient(master)
        except tk.TclError:  # pragma: no cover
            pass
        self._on_retry = on_retry
        self._photo = None
        self._display_photo = None

        answer = result.get("answer")
        image_w = int(result.get("width") or 0)
        image_h = int(result.get("height") or 0)
        try:
            sw = self.window.winfo_screenwidth()
            sh = self.window.winfo_screenheight()
        except tk.TclError:  # pragma: no cover
            sw, sh = 1920, 1080
        win_w = min(int(sw * 0.62), 1280)
        win_h = min(int(sh * 0.78), 960)
        x = max(0, (sw - win_w) // 2)
        y = max(0, (sh - win_h) // 2)
        self.window.geometry(f"{win_w}x{win_h}+{x}+{y}")

        # 顶部状态
        header = tk.Frame(self.window, bg=_BG)
        header.pack(fill="x", padx=22, pady=(16, 8))
        elapsed = float(getattr(answer, "elapsed_ms", 0.0)) / 1000.0 if answer else 0.0
        if answer is not None:
            status_text = f"AI 答案：{'、'.join(answer.all_letters())}    ·    耗时 {elapsed:.1f} 秒"
            status_color = _SUCCESS
        else:
            status_text = "AI 识别失败"
            status_color = _DANGER
        tk.Label(
            header,
            text=status_text,
            bg=_BG,
            fg=status_color,
            font=("Microsoft YaHei UI", 16, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Label(
            header,
            text=f"模型：{result.get('model', '-')}",
            bg=_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 10),
            anchor="e",
        ).pack(side="right")

        # 题目截图
        wrap = tk.Frame(self.window, bg=_CARD, highlightthickness=1, highlightbackground=_BORDER)
        wrap.pack(fill="both", expand=True, padx=22, pady=(0, 8))
        canvas = tk.Canvas(wrap, bg="#05070b", highlightthickness=0, bd=0)
        canvas.pack(expand=True)
        photo = self._load_photo(result)
        if photo is None:
            canvas.configure(width=520, height=180)
            canvas.create_text(260, 90, text="题目截图不可用", fill=_MUTED,
                               font=("Microsoft YaHei UI", 13))
        else:
            self._photo = photo
            avail_w = max(240, win_w - 80)
            avail_h = max(180, win_h - 360)
            factor = max(1, math.ceil(max(image_w / avail_w, image_h / avail_h)))
            while image_w // factor > avail_w or image_h // factor > avail_h:
                factor += 1
            display = photo if factor <= 1 else photo.subsample(factor, factor)
            self._display_photo = display
            canvas.configure(width=display.width(), height=display.height())
            canvas.create_image(0, 0, image=display, anchor="nw")

        # 模型原始输出
        raw_wrap = tk.Frame(self.window, bg=_BG)
        raw_wrap.pack(fill="x", padx=22, pady=(0, 8))
        tk.Label(
            raw_wrap, text="模型输出", bg=_BG, fg=_MUTED,
            font=("Microsoft YaHei UI", 10), anchor="w",
        ).pack(fill="x")
        text_frame = tk.Frame(raw_wrap, bg=_BG)
        text_frame.pack(fill="x", pady=(4, 0))
        raw_text = tk.Text(
            text_frame,
            height=7,
            wrap="word",
            bg="#05070b",
            fg=_TEXT,
            insertbackground=_TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=_BORDER,
            font=("Consolas", 10),
            padx=10,
            pady=8,
            state="normal",
        )
        scroll = tk.Scrollbar(
            text_frame, command=raw_text.yview, bg=_CARD, troughcolor=_BG,
            activebackground=_BORDER, bd=0, width=10,
        )
        raw_text.configure(yscrollcommand=scroll.set)
        raw_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        raw_output = str(getattr(answer, "raw", "") if answer else result.get("error", "无返回内容"))
        raw_text.insert("1.0", raw_output or "（空）")
        raw_text.configure(state="disabled")

        # 底部按钮 / 信息
        footer = tk.Frame(self.window, bg=_BG)
        footer.pack(fill="x", padx=22, pady=(0, 16))
        info = f"题目区域 {image_w}×{image_h} 像素"
        if result.get("api"):
            info += f"    ·    {result['api']}"
        tk.Label(footer, text=info, bg=_BG, fg=_DIM, font=("Microsoft YaHei UI", 9),
                 anchor="w").pack(side="left")
        if on_retry is not None:
            tk.Button(
                footer, text="重新测试", command=self._retry,
                bg=_ACCENT, fg="#ffffff", activebackground="#5a9eff",
                activeforeground="#ffffff", relief="flat", bd=0, padx=18, pady=7,
                font=("Microsoft YaHei UI", 11, "bold"), cursor="hand2",
            ).pack(side="right")
            tk.Button(
                footer, text="关闭", command=self._close,
                bg=_CARD, fg=_TEXT, activebackground="#1d2534", activeforeground=_TEXT,
                relief="flat", bd=0, padx=18, pady=7,
                font=("Microsoft YaHei UI", 11), cursor="hand2",
            ).pack(side="right", padx=(0, 10))
        else:
            tk.Button(
                footer, text="关闭", command=self._close,
                bg=_CARD, fg=_TEXT, activebackground="#1d2534", activeforeground=_TEXT,
                relief="flat", bd=0, padx=18, pady=7,
                font=("Microsoft YaHei UI", 11), cursor="hand2",
            ).pack(side="right")

        self.window.bind("<Escape>", lambda _e: self._close())
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.lift()
        self.window.focus_force()
        try:
            self.window.update_idletasks()
            winapi.enable_dark_titlebar(winapi.get_toplevel_hwnd(self.window))
        except Exception:  # pragma: no cover
            pass

    def _load_photo(self, result: dict):
        data = result.get("png")
        if not data:
            return None
        path = temp_png_path("ai")
        try:
            path.write_bytes(data)
            return tk.PhotoImage(file=str(path), master=self.window)
        except Exception:  # pragma: no cover
            return None
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _retry(self) -> None:
        self._close()
        if callable(self._on_retry):
            self._on_retry()

    def _close(self) -> None:
        try:
            self.window.destroy()
        except tk.TclError:  # pragma: no cover
            pass


class OptionLayoutPreview:
    """展示 AI 自动定位出的选项 / 下一题按钮位置（带标记的题目截图）。"""

    def __init__(
        self,
        master: tk.Misc,
        result: dict,
        on_retry: Callable[[], None] | None = None,
    ) -> None:
        self.window = tk.Toplevel(master)
        self.window.title("选项定位结果")
        self.window.configure(bg=_BG)
        self.window.minsize(640, 460)
        try:
            self.window.attributes("-topmost", True)
            self.window.transient(master)
        except tk.TclError:  # pragma: no cover
            pass
        self._on_retry = on_retry
        self._photo = None
        self._display_photo = None

        layout = result.get("layout")
        points = dict(getattr(layout, "points", {}) or {})
        next_point = getattr(layout, "next_point", None)
        image_w = int(result.get("width") or 0)
        image_h = int(result.get("height") or 0)
        try:
            sw = self.window.winfo_screenwidth()
            sh = self.window.winfo_screenheight()
        except tk.TclError:  # pragma: no cover
            sw, sh = 1920, 1080
        win_w = min(int(sw * 0.60), 1220)
        win_h = min(int(sh * 0.76), 920)
        x = max(0, (sw - win_w) // 2)
        y = max(0, (sh - win_h) // 2)
        self.window.geometry(f"{win_w}x{win_h}+{x}+{y}")

        header = tk.Frame(self.window, bg=_BG)
        header.pack(fill="x", padx=22, pady=(16, 6))
        letters = "、".join(points.keys()) if points else "无"
        next_text = " · 已定位「下一题」" if next_point else ""
        tk.Label(
            header,
            text=f"已识别选项位置：{letters}{next_text}",
            bg=_BG,
            fg=_SUCCESS,
            font=("Microsoft YaHei UI", 15, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Label(
            header,
            text="请核对标记是否与选项对齐",
            bg=_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 10),
            anchor="e",
        ).pack(side="right")

        wrap = tk.Frame(self.window, bg=_CARD, highlightthickness=1, highlightbackground=_BORDER)
        wrap.pack(fill="both", expand=True, padx=22, pady=(0, 8))
        canvas = tk.Canvas(wrap, bg="#05070b", highlightthickness=0, bd=0)
        canvas.pack(expand=True)

        photo = self._load_photo(result)
        if photo is None:
            canvas.configure(width=520, height=180)
            canvas.create_text(260, 90, text="题目截图不可用", fill=_MUTED,
                               font=("Microsoft YaHei UI", 13))
        else:
            self._photo = photo
            avail_w = max(240, win_w - 80)
            avail_h = max(180, win_h - 300)
            factor = max(1, math.ceil(max(image_w / avail_w, image_h / avail_h)))
            while image_w // factor > avail_w or image_h // factor > avail_h:
                factor += 1
            display = photo if factor <= 1 else photo.subsample(factor, factor)
            self._display_photo = display
            disp_w, disp_h = display.width(), display.height()
            canvas.configure(width=disp_w, height=disp_h)
            canvas.create_image(0, 0, image=display, anchor="nw")
            for letter, (nx, ny) in points.items():
                cx = max(10.0, min(disp_w - 10.0, nx * disp_w))
                cy = max(10.0, min(disp_h - 10.0, ny * disp_h))
                canvas.create_oval(cx - 13, cy - 13, cx + 13, cy + 13,
                                   outline=_SUCCESS, width=2)
                canvas.create_text(cx, cy, text=letter, fill=_SUCCESS,
                                   font=("Microsoft YaHei UI", 10, "bold"))
            if next_point:
                nx, ny = next_point
                cx = max(10.0, min(disp_w - 10.0, nx * disp_w))
                cy = max(10.0, min(disp_h - 10.0, ny * disp_h))
                canvas.create_rectangle(cx - 30, cy - 14, cx + 30, cy + 14,
                                        outline=_WARNING, width=2)
                canvas.create_text(cx, cy, text="下一题", fill=_WARNING,
                                   font=("Microsoft YaHei UI", 9, "bold"))

        footer = tk.Frame(self.window, bg=_BG)
        footer.pack(fill="x", padx=22, pady=(0, 16))
        tk.Label(
            footer,
            text=f"题目区域 {image_w}×{image_h} 像素 · 已自动填入左侧坐标，可手动微调",
            bg=_BG, fg=_DIM, font=("Microsoft YaHei UI", 9), anchor="w",
        ).pack(side="left")
        tk.Button(
            footer, text="关闭", command=self._close,
            bg=_CARD, fg=_TEXT, activebackground="#1d2534", activeforeground=_TEXT,
            relief="flat", bd=0, padx=18, pady=7,
            font=("Microsoft YaHei UI", 11), cursor="hand2",
        ).pack(side="right")
        if on_retry is not None:
            tk.Button(
                footer, text="重新识别", command=self._retry,
                bg=_ACCENT, fg="#ffffff", activebackground="#5a9eff",
                activeforeground="#ffffff", relief="flat", bd=0, padx=18, pady=7,
                font=("Microsoft YaHei UI", 11, "bold"), cursor="hand2",
            ).pack(side="right", padx=(0, 10))

        self.window.bind("<Escape>", lambda _e: self._close())
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.lift()
        self.window.focus_force()
        try:
            self.window.update_idletasks()
            winapi.enable_dark_titlebar(winapi.get_toplevel_hwnd(self.window))
        except Exception:  # pragma: no cover
            pass

    def _load_photo(self, result: dict):
        data = result.get("png")
        if not data:
            return None
        path = temp_png_path("layout")
        try:
            path.write_bytes(data)
            return tk.PhotoImage(file=str(path), master=self.window)
        except Exception:  # pragma: no cover
            return None
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _retry(self) -> None:
        self._close()
        if callable(self._on_retry):
            self._on_retry()

    def _close(self) -> None:
        try:
            self.window.destroy()
        except tk.TclError:  # pragma: no cover
            pass
