"""智能点击助手 · 自定义 tkinter 控件库（深色主题）。

本模块只依赖 Python 标准库（``tkinter``），不依赖任何第三方包，
可以安全地在「没有 Tk 根窗口」的情况下 import：模块级只定义常量、函数与类，
不会创建任何窗口。

快速开始::

    import tkinter as tk
    import widgets

    root = tk.Tk()
    widgets.configure_fonts(root)          # 可选：显式解析系统中文字体
    btn = widgets.RoundedButton(root, text="开始运行", command=on_start)
    btn.pack()

约定
----
* 所有颜色均为 ``"#rrggbb"`` 字符串，配色集中在 :data:`PALETTE`。
* 控件尺寸（width / height / radius / padx / pady ...）由调用方以「像素」为单位传入，
  模块内部不做任何 DPI 缩放；需要适配 150% 缩放时由调用方统一乘系数。
* 字体 ``size`` 遵循 Tk 约定：正数表示磅（point，会随系统 DPI 自动缩放），
  负数表示像素。因此通常只需要传字号，无需手动乘以缩放系数。
* 控件默认尽量从父容器读取背景色作为画布底色，圆角外侧不会出现色块。
"""

from __future__ import annotations

import math
import sys
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Optional, Sequence

__version__ = "0.1.0"

__all__ = [
    "PALETTE",
    "FONT_UI",
    "FONT_MONO",
    "pick_font",
    "configure_fonts",
    "init_fonts",
    "font",
    "enable_dpi_awareness",
    "RoundedButton",
    "SegmentControl",
    "Card",
    "ScrollFrame",
    "LogView",
    "StatusPill",
    "StatTile",
    "LabeledEntry",
]


# ---------------------------------------------------------------------------
# 调色板
# ---------------------------------------------------------------------------

PALETTE: dict[str, str] = {
    "bg": "#0b0f16",           # 窗口底色
    "bg2": "#10151f",          # 次级底色 / 输入框、凹槽
    "card": "#141a26",         # 卡片、按钮
    "card_hover": "#19202e",   # 卡片悬停
    "border": "#232c3d",       # 分隔线、描边
    "text": "#e8eef8",         # 主文本
    "muted": "#93a0b5",        # 次要文本
    "dim": "#6b7789",          # 弱化 / 禁用文本
    "accent": "#3d8bfd",       # 主色
    "accent_hover": "#5a9eff",
    "accent_dark": "#2f6fd0",
    "cyan": "#22d3ee",
    "success": "#34d399",
    "warning": "#fbbf24",
    "danger": "#f87171",
    "danger_hover": "#ef4444",
}


# ---------------------------------------------------------------------------
# 字体解析与字号辅助
# ---------------------------------------------------------------------------

_DEFAULT_UI_FAMILY = "Microsoft YaHei UI"
_DEFAULT_MONO_FAMILY = "Consolas"

#: UI 字体候选（按优先级）
UI_FONT_CANDIDATES: tuple[str, ...] = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Segoe UI",
    "Arial",
)

#: 等宽字体候选（按优先级，优先中英文等宽兼容的现代字体）
MONO_FONT_CANDIDATES: tuple[str, ...] = (
    "Cascadia Mono",
    "Cascadia Code",
    "Consolas",
    "JetBrains Mono",
    "Source Code Pro",
    "Courier New",
    "TkFixedFont",
)

#: 解析后的 UI 字体族；在 :func:`configure_fonts` / 首次创建控件后更新。
#: 注意：``from widgets import FONT_UI`` 取到的是导入时的快照，动态读取请使用
#: ``widgets.FONT_UI``。
FONT_UI: str = _DEFAULT_UI_FAMILY

#: 解析后的等宽字体族。
FONT_MONO: str = _DEFAULT_MONO_FAMILY

_fonts_ready: bool = False


def _root_of(master=None):
    """取得一个可用的 Tk 根窗口（优先使用传入控件的 toplevel）。"""
    if master is not None:
        try:
            return master.winfo_toplevel()
        except Exception:
            pass
    try:
        return tk._default_root  # type: ignore[attr-defined]
    except Exception:
        return None


def pick_font(candidates: Sequence[str], root=None) -> str:
    """返回 ``candidates`` 中第一个在 Tk 实例中真实可用的字体族。

    参数
    ----
    candidates:
        字体族名称列表，按优先级从高到低排列。
    root:
        Tk 根窗口（可选）。省略时使用当前默认根窗口；若当前没有根窗口，
        则直接返回第一个候选名（延迟到真正创建控件后再解析）。

    返回
    ----
    字体族名称；列表为空时返回 ``"Microsoft YaHei UI"``。
    """
    candidates = list(candidates)
    if not candidates:
        return _DEFAULT_UI_FAMILY

    root = _root_of(root)
    if root is None:
        return candidates[0]

    try:
        available = {name.lower(): name for name in tkfont.families(root)}
    except Exception:
        return candidates[0]

    for name in candidates:
        hit = available.get(name.lower())
        if hit is not None:
            return hit
    return candidates[0]


def _ensure_fonts(master=None) -> None:
    """惰性解析 FONT_UI / FONT_MONO，只执行一次。"""
    global FONT_UI, FONT_MONO, _fonts_ready
    if _fonts_ready:
        return
    root = _root_of(master)
    if root is None:
        return
    try:
        tkfont.families(root)
    except Exception:
        return
    FONT_UI = pick_font(UI_FONT_CANDIDATES, root)
    FONT_MONO = pick_font(MONO_FONT_CANDIDATES, root)
    _fonts_ready = True


def configure_fonts(root=None) -> None:
    """解析并缓存字体族（建议在创建根窗口后立即调用一次）。

    若不调用，控件初始化时会自动惰性解析；显式调用可以保证在
    ``FONT_UI`` / ``FONT_MONO`` 被读取前完成解析。
    """
    global _fonts_ready
    _fonts_ready = False
    _ensure_fonts(root)


#: 兼容别名（任务书中提到过 ``init_fonts(root)`` 这一命名）。
init_fonts = configure_fonts


def font(size: int, bold: bool = False, mono: bool = False) -> tuple:
    """返回 Tk 可用的字体规格元组 ``(family, size[, "bold"])``。

    ``size`` 正数为磅（随系统 DPI 自动缩放），负数为像素。
    在没有根窗口时调用不会抛异常，会返回默认字体族的规格。
    """
    _ensure_fonts()
    family = FONT_MONO if mono else FONT_UI
    if bold:
        return (family, size, "bold")
    return (family, size)


#: 内部别名：在参数名恰好叫 ``font`` 的方法里仍能调用模块级字体辅助函数。
_font_fn = font


def enable_dpi_awareness(per_monitor: bool = True) -> bool:
    """在创建 Tk 根窗口之前调用，声明进程 DPI 感知，避免界面发虚。

    仅 Windows 生效；其他平台或系统调用失败时静默返回 ``False``。
    已经声明过 DPI 感知时重复调用是安全的（返回 False）。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        if per_monitor:
            try:
                # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4
                if ctypes.windll.user32.SetProcessDpiAwarenessContext(-4):
                    return True
            except Exception:
                pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2 if per_monitor else 1)
            return True
        except Exception:
            pass
        try:
            return bool(ctypes.windll.user32.SetProcessDPIAware())
        except Exception:
            return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 颜色工具
# ---------------------------------------------------------------------------


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = str(color).strip().lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    if len(color) != 6:
        return (0, 0, 0)
    try:
        return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))
    except ValueError:
        return (0, 0, 0)


def _mix(color_a: str, color_b: str, t: float) -> str:
    """把颜色 A 向颜色 B 线性混合 ``t``（0.0~1.0），返回 ``#rrggbb``。"""
    t = max(0.0, min(1.0, float(t)))
    ra, ga, ba = _hex_to_rgb(color_a)
    rb, gb, bb = _hex_to_rgb(color_b)
    return "#{:02x}{:02x}{:02x}".format(
        int(round(ra + (rb - ra) * t)),
        int(round(ga + (gb - ga) * t)),
        int(round(ba + (bb - ba) * t)),
    )


def _lighten(color: str, amount: float) -> str:
    """向白色混合，得到更亮的颜色。"""
    return _mix(color, "#ffffff", amount)


def _darken(color: str, amount: float) -> str:
    """向黑色混合，得到更暗的颜色。"""
    return _mix(color, "#000000", amount)


def _parent_bg(master, fallback: str = PALETTE["bg"]) -> str:
    """尽力读取父容器的背景色，用于圆角/画布外缘融合。"""
    try:
        value = master.cget("bg")
        if value:
            return str(value)
    except Exception:
        pass
    try:
        value = master.cget("background")
        if value:
            return str(value)
    except Exception:
        pass
    return fallback


# ---------------------------------------------------------------------------
# 画布绘制工具
# ---------------------------------------------------------------------------


def _round_rect_points(x1: float, y1: float, x2: float, y2: float,
                       radius: float, segments: int = 8) -> list[float]:
    """沿真实圆弧采样圆角矩形轮廓点（顺时针，扁平坐标列表）。"""
    radius = max(0.0, min(float(radius), (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    points: list[float] = []

    def arc(cx: float, cy: float, start_deg: float, end_deg: float) -> None:
        for index in range(segments + 1):
            angle = math.radians(start_deg + (end_deg - start_deg) * index / segments)
            points.append(cx + radius * math.cos(angle))
            points.append(cy + radius * math.sin(angle))

    arc(x1 + radius, y1 + radius, 180, 270)   # 左上
    arc(x2 - radius, y1 + radius, 270, 360)   # 右上
    arc(x2 - radius, y2 - radius, 0, 90)      # 右下
    arc(x1 + radius, y2 - radius, 90, 180)    # 左下
    return points


def _round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                radius: float, **kwargs) -> int:
    """在画布上绘制一个圆角矩形，返回 item id。

    Tk Canvas 没有原生圆角矩形，这里沿真实圆弧采样后用平滑样条连接；
    对「纯填充、无描边」的形状，再用填充色与画布底色的中间色在边缘画一圈
    0.5px 外扩光晕，模拟抗锯齿，让圆角在深色背景上看起来更柔和。
    """
    radius = max(0.0, min(float(radius), (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    if radius < 1.0:
        return int(canvas.create_rectangle(x1, y1, x2, y2, **kwargs))

    fill = kwargs.get("fill")
    outline = kwargs.get("outline")
    if fill and not outline:
        try:
            halo = _mix(str(fill), str(canvas.cget("bg")), 0.5)
            canvas.create_polygon(
                _round_rect_points(x1 - 0.5, y1 - 0.5, x2 + 0.5, y2 + 0.5, radius + 0.5),
                smooth=True, fill=halo, outline="",
            )
        except tk.TclError:
            pass

    points = _round_rect_points(x1, y1, x2, y2, radius)
    return int(canvas.create_polygon(points, smooth=True, **kwargs))


def _make_font(widget: tk.Misc, spec=None, fallback_size: int = 11) -> tkfont.Font:
    """把字体规格统一转换成可测量的 :class:`tkinter.font.Font`。"""
    if spec is None:
        spec = font(fallback_size)
    if isinstance(spec, tkfont.Font):
        return spec
    try:
        return tkfont.Font(root=widget, font=spec)
    except Exception:
        try:
            return tkfont.Font(root=widget, font=font(fallback_size))
        except Exception:
            return tkfont.Font(root=widget, font=("TkDefaultFont", fallback_size))


# ---------------------------------------------------------------------------
# 1. RoundedButton —— 圆角按钮
# ---------------------------------------------------------------------------


class RoundedButton(tk.Canvas):
    """圆角按钮（Canvas 绘制，支持悬停 / 按下 / 禁用 / 键盘触发）。

    参数
    ----
    master:
        父容器。
    text:
        按钮文字（支持中文）。
    command:
        点击（在按钮内按下并抬起）时调用一次的回调。
    width / height:
        像素尺寸，由调用方传入（不做 DPI 换算）。
    radius:
        圆角半径（像素）。
    bg / fg:
        常态背景色 / 文字颜色。
    hover_bg / press_bg:
        悬停 / 按下背景色；省略时自动从 ``bg`` 派生。
    border_color:
        1px 描边颜色；省略则无描边。
    font:
        字体规格；省略时使用 ``font(11)``。
    disabled_bg / disabled_fg:
        禁用态背景 / 文字颜色；省略时自动派生。
    cursor:
        鼠标样式，默认 ``"hand2"``。
    """

    def __init__(
        self,
        master: tk.Misc,
        text: str = "",
        command: Optional[Callable[[], None]] = None,
        width: int = 160,
        height: int = 44,
        radius: int = 10,
        bg: str = PALETTE["card"],
        fg: str = PALETTE["text"],
        hover_bg: Optional[str] = None,
        press_bg: Optional[str] = None,
        border_color: Optional[str] = None,
        font=None,
        disabled_bg: Optional[str] = None,
        disabled_fg: Optional[str] = None,
        cursor: str = "hand2",
        **kwargs,
    ) -> None:
        _ensure_fonts(master)
        self._canvas_bg = kwargs.pop("canvas_bg", None) or _parent_bg(master, PALETTE["bg"])
        super().__init__(
            master,
            width=width,
            height=height,
            bg=self._canvas_bg,
            highlightthickness=0,
            bd=0,
            takefocus=1,
            cursor=cursor,
            **kwargs,
        )

        self._text = str(text)
        self._command = command
        self._bg = bg
        self._fg = fg
        self._hover_bg = hover_bg or _lighten(bg, 0.10)
        self._press_bg = press_bg or _darken(bg, 0.14)
        self._border_color = border_color
        self._disabled_bg = disabled_bg or _mix(bg, self._canvas_bg, 0.55)
        self._disabled_fg = disabled_fg or PALETTE["dim"]
        self._radius = max(0, int(radius))
        self._font = _make_font(self, font, 11)
        self._cursor = cursor

        self._canvas_w = int(width)
        self._canvas_h = int(height)
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._focused = False

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", self._on_configure)
        self.bind("<FocusIn>", self._on_focus)
        self.bind("<FocusOut>", self._on_focus)
        self.bind("<space>", self._on_key)
        self.bind("<Return>", self._on_key)

        self._redraw()

    # ---- 公开 API -----------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        """启用 / 禁用按钮（禁用时不再响应鼠标与键盘）。"""
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self._hover = False
        self._pressed = False
        try:
            self.configure(cursor=self._cursor if enabled else "arrow")
        except tk.TclError:
            pass
        self._redraw()

    def is_enabled(self) -> bool:
        """按钮当前是否可用。"""
        return self._enabled

    def set_text(self, text: str) -> None:
        """更新按钮文字（尺寸保持不变，过长会被画布裁剪）。"""
        text = str(text)
        if text == self._text:
            return
        self._text = text
        self._redraw()

    def get_text(self) -> str:
        """返回当前按钮文字。"""
        return self._text

    def invoke(self) -> None:
        """以编程方式触发一次点击（会检查禁用状态）。"""
        if self._enabled and callable(self._command):
            self._command()

    # ---- 内部实现 -----------------------------------------------------

    def _current_bg(self) -> str:
        if not self._enabled:
            return self._disabled_bg
        if self._pressed:
            return self._press_bg
        if self._hover:
            return self._hover_bg
        return self._bg

    def _current_fg(self) -> str:
        return self._fg if self._enabled else self._disabled_fg

    def _focus_outline(self) -> Optional[str]:
        if not (self._focused and self._enabled):
            return self._border_color
        if self._bg.lower() == PALETTE["accent"].lower():
            return PALETTE["accent_hover"]
        return PALETTE["accent"]

    def _redraw(self) -> None:
        self.delete("all")
        w = max(2, int(self._canvas_w))
        h = max(2, int(self._canvas_h))
        radius = max(0, min(self._radius, w // 2, h // 2))
        outline = self._focus_outline()
        if outline:
            _round_rect(self, 1, 1, w - 1, h - 1, radius,
                        fill=self._current_bg(), outline=outline, width=1)
        else:
            _round_rect(self, 1, 1, w - 1, h - 1, radius,
                        fill=self._current_bg(), outline="")
        self.create_text(
            w / 2.0,
            h / 2.0,
            text=self._text,
            fill=self._current_fg(),
            font=self._font,
            anchor="center",
        )

    # ---- 事件 ---------------------------------------------------------

    def _on_enter(self, _event=None) -> None:
        if not self._enabled or self._hover:
            return
        self._hover = True
        self._redraw()

    def _on_leave(self, _event=None) -> None:
        if not self._hover:
            return
        self._hover = False
        self._pressed = False
        self._redraw()

    def _on_press(self, _event=None) -> None:
        if not self._enabled:
            return
        self._pressed = True
        try:
            self.focus_set()
        except tk.TclError:
            pass
        self._redraw()

    def _on_release(self, event) -> None:
        if not self._enabled:
            return
        was_pressed = self._pressed
        self._pressed = False
        inside = 0 <= int(event.x) < int(self._canvas_w) and 0 <= int(event.y) < int(self._canvas_h)
        self._redraw()
        if was_pressed and inside:
            self.invoke()

    def _on_configure(self, event) -> None:
        if event.width <= 1 or event.height <= 1:
            return
        self._canvas_w = int(event.width)
        self._canvas_h = int(event.height)
        self._redraw()

    def _on_focus(self, _event=None) -> None:
        self._focused = bool(self.focus_get() == self)
        self._redraw()

    def _on_key(self, _event=None):
        if not self._enabled:
            return "break"
        self._pressed = True
        self._redraw()
        self.after(90, self._key_release)
        return "break"

    def _key_release(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        self._pressed = False
        self._redraw()
        self.invoke()


class SplitButton(tk.Canvas):
    """一个按钮尺寸、中间一分为二的双段按钮（左/右各一个点击区域）。

    常用于把"单次答题 / 单次填空"这类并列的一次性操作合并成一个按钮。
    """

    def __init__(
        self,
        master: tk.Misc,
        left_text: str = "",
        right_text: str = "",
        left_command: Optional[Callable[[], None]] = None,
        right_command: Optional[Callable[[], None]] = None,
        width: int = 320,
        height: int = 42,
        radius: int = 10,
        bg: str = PALETTE["bg2"],
        fg: str = PALETTE["text"],
        hover_bg: Optional[str] = None,
        press_bg: Optional[str] = None,
        border_color: Optional[str] = None,
        font=None,
        disabled_bg: Optional[str] = None,
        disabled_fg: Optional[str] = None,
        cursor: str = "hand2",
        **kwargs,
    ) -> None:
        _ensure_fonts(master)
        self._canvas_bg = kwargs.pop("canvas_bg", None) or _parent_bg(master, PALETTE["bg"])
        super().__init__(
            master,
            width=width,
            height=height,
            bg=self._canvas_bg,
            highlightthickness=0,
            bd=0,
            takefocus=1,
            cursor=cursor,
            **kwargs,
        )
        self._left_text = str(left_text)
        self._right_text = str(right_text)
        self._left_command = left_command
        self._right_command = right_command
        self._bg = bg
        self._fg = fg
        self._hover_bg = hover_bg or _lighten(bg, 0.10)
        self._press_bg = press_bg or _darken(bg, 0.14)
        self._border_color = border_color or _mix(bg, PALETTE["border"], 0.65)
        self._disabled_bg = disabled_bg or _mix(bg, self._canvas_bg, 0.55)
        self._disabled_fg = disabled_fg or PALETTE["dim"]
        self._radius = max(0, int(radius))
        self._font = _make_font(self, font, 11)
        self._cursor = cursor
        self._canvas_w = int(width)
        self._canvas_h = int(height)
        self._enabled = True
        self._hover_side: str | None = None
        self._pressed_side: str | None = None

        self.bind("<Motion>", self._on_motion)
        self.bind("<Enter>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", self._on_configure)
        self._redraw()

    # ---- 公开 API -----------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self._hover_side = None
        self._pressed_side = None
        try:
            self.configure(cursor=self._cursor if enabled else "arrow")
        except tk.TclError:  # pragma: no cover
            pass
        self._redraw()

    def is_enabled(self) -> bool:
        return self._enabled

    def set_texts(self, left_text: str, right_text: str) -> None:
        self._left_text = str(left_text)
        self._right_text = str(right_text)
        self._redraw()

    def invoke_side(self, side: str) -> None:
        """触发左/右半边的回调（程序化调用）。"""
        if not self._enabled:
            return
        command = self._left_command if side == "left" else self._right_command
        if callable(command):
            command()

    # ---- 内部 ---------------------------------------------------------
    def _side_at(self, x: float) -> str:
        return "left" if x < self._canvas_w / 2.0 else "right"

    def _redraw(self) -> None:
        self.delete("all")
        w, h = self._canvas_w, self._canvas_h
        _round_rect(
            self, 1, 1, w - 1, h - 1, self._radius,
            fill=self._bg if self._enabled else self._disabled_bg,
            outline=self._border_color, width=1,
        )
        active_side = self._pressed_side or self._hover_side
        if self._enabled and active_side is not None:
            fill = self._press_bg if self._pressed_side else self._hover_bg
            mid = w / 2.0
            if active_side == "left":
                self.create_rectangle(3, 3, mid - 1, h - 3, fill=fill, outline="")
            else:
                self.create_rectangle(mid + 1, 3, w - 3, h - 3, fill=fill, outline="")
        self.create_line(w / 2.0, 6, w / 2.0, h - 6, fill=self._border_color)
        color = self._fg if self._enabled else self._disabled_fg
        self.create_text(
            w / 4.0, h / 2.0, text=self._left_text, fill=color,
            font=self._font, anchor="center",
        )
        self.create_text(
            w * 3 / 4.0, h / 2.0, text=self._right_text, fill=color,
            font=self._font, anchor="center",
        )

    def _on_motion(self, event) -> None:
        if not self._enabled:
            return
        side = self._side_at(event.x)
        if side != self._hover_side:
            self._hover_side = side
            self._redraw()

    def _on_leave(self, _event=None) -> None:
        if self._hover_side is None and self._pressed_side is None:
            return
        self._hover_side = None
        self._pressed_side = None
        self._redraw()

    def _on_press(self, event) -> None:
        if not self._enabled:
            return
        self._pressed_side = self._side_at(event.x)
        try:
            self.focus_set()
        except tk.TclError:  # pragma: no cover
            pass
        self._redraw()

    def _on_release(self, event) -> None:
        if not self._enabled:
            return
        side = self._side_at(event.x)
        pressed = self._pressed_side
        self._pressed_side = None
        self._redraw()
        inside = (
            0 <= int(event.x) < int(self._canvas_w)
            and 0 <= int(event.y) < int(self._canvas_h)
        )
        if inside and pressed is not None and pressed == side:
            self.invoke_side(side)

    def _on_configure(self, event) -> None:
        if event.width <= 1 or event.height <= 1:
            return
        self._canvas_w = int(event.width)
        self._canvas_h = int(event.height)
        self._redraw()


# ---------------------------------------------------------------------------
# 2. SegmentControl —— 分段切换控件
# ---------------------------------------------------------------------------


class SegmentControl(tk.Canvas):
    """分段控制器（类似 iOS 分段条）：圆角轨道 + 主色胶囊滑块。

    ``options`` 是 ``[(key, label), ...]``，点击不同分段时调用 ``command(key)``，
    仅在「选项真正改变」时回调。程序化调用 :meth:`set_active` 不会触发回调。

    参数
    ----
    master: 父容器。
    options: 选项列表，元素为 ``(key, label)``。
    command: 选择变化时调用 ``command(key)``。
    width / height: 像素尺寸。
    font: 字体规格；省略时使用 ``font(11)``。
    """

    def __init__(
        self,
        master: tk.Misc,
        options: Sequence[tuple[str, str]],
        command: Optional[Callable[[str], None]] = None,
        width: int = 420,
        height: int = 42,
        font=None,
        **kwargs,
    ) -> None:
        _ensure_fonts(master)
        self._canvas_bg = kwargs.pop("canvas_bg", None) or _parent_bg(master, PALETTE["bg"])
        super().__init__(
            master,
            width=width,
            height=height,
            bg=self._canvas_bg,
            highlightthickness=0,
            bd=0,
            takefocus=1,
            cursor="hand2",
            **kwargs,
        )

        self._segments: list[tuple[str, str]] = [(str(k), str(v)) for k, v in options]
        if not self._segments:
            raise ValueError("SegmentControl 至少需要一个选项")
        self._keys = [k for k, _ in self._segments]
        self._command = command
        self._font = _make_font(self, font, 11)
        self._active_key: Optional[str] = self._keys[0]
        self._hover_index = -1
        self._canvas_w = int(width)
        self._canvas_h = int(height)

        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<FocusIn>", lambda _e: self._redraw())
        self.bind("<FocusOut>", lambda _e: self._redraw())
        self.bind("<Left>", lambda _e: self._step(-1))
        self.bind("<Right>", lambda _e: self._step(1))

        self._redraw()

    # ---- 公开 API -----------------------------------------------------

    def set_active(self, key: str) -> None:
        """设置当前选中项（不触发 ``command`` 回调）。"""
        if key not in self._keys:
            raise ValueError(f"未知的分段选项: {key!r}")
        if key == self._active_key:
            self._redraw()
            return
        self._active_key = key
        self._redraw()

    def get_active(self) -> Optional[str]:
        """返回当前选中项的 key；无选中时为 ``None``。"""
        return self._active_key

    def set_options(self, options: Sequence[tuple[str, str]]) -> None:
        """替换选项列表（尽量保留原选中项）。"""
        options = [(str(k), str(v)) for k, v in options]
        if not options:
            raise ValueError("SegmentControl 至少需要一个选项")
        self._segments = options
        self._keys = [k for k, _ in options]
        if self._active_key not in self._keys:
            self._active_key = self._keys[0]
        self._hover_index = -1
        self._redraw()

    # ---- 内部实现 -----------------------------------------------------

    def _segment_bounds(self) -> tuple[int, float]:
        """返回 (内边距, 单个分段宽度)。"""
        pad = max(2, int(round(self._canvas_h * 0.07)))
        count = max(1, len(self._segments))
        seg_w = max(1.0, (self._canvas_w - 2 * pad) / count)
        return pad, seg_w

    def _index_at(self, x: float) -> int:
        pad, seg_w = self._segment_bounds()
        idx = int((x - pad) // seg_w)
        return max(0, min(len(self._segments) - 1, idx))

    def _redraw(self) -> None:
        self.delete("all")
        w = max(2, int(self._canvas_w))
        h = max(2, int(self._canvas_h))
        pad, seg_w = self._segment_bounds()
        radius = max(2.0, (h - 2 * pad) / 2.0)

        # 轨道
        _round_rect(self, 1, 1, w - 1, h - 1, h / 2.0 - 1,
                    fill=PALETTE["bg2"], outline=PALETTE["border"], width=1)

        for i, (key, label) in enumerate(self._segments):
            x1 = pad + i * seg_w
            x2 = x1 + seg_w
            if key == self._active_key:
                _round_rect(self, x1 + 1, 1 + pad, x2 - 1, h - 1 - pad, radius,
                            fill=PALETTE["accent"], outline="")
                text_fill = "#ffffff"
            else:
                if i == self._hover_index:
                    _round_rect(self, x1 + 1, 1 + pad, x2 - 1, h - 1 - pad, radius,
                                fill=PALETTE["card_hover"], outline="")
                    text_fill = PALETTE["text"]
                else:
                    text_fill = PALETTE["muted"]
            self.create_text(
                (x1 + x2) / 2.0,
                h / 2.0,
                text=label,
                fill=text_fill,
                font=self._font,
                anchor="center",
            )

        if self.focus_get() == self:
            _round_rect(self, 1, 1, w - 1, h - 1, h / 2.0 - 1,
                        fill="", outline=PALETTE["accent"], width=1)

    def _select_index(self, index: int) -> None:
        index = max(0, min(len(self._segments) - 1, int(index)))
        key = self._keys[index]
        if key == self._active_key:
            return
        self._active_key = key
        self._redraw()
        if callable(self._command):
            self._command(key)

    def _step(self, delta: int):
        try:
            index = self._keys.index(self._active_key) if self._active_key else 0
        except ValueError:
            index = 0
        self._select_index(index + delta)
        return "break"

    # ---- 事件 ---------------------------------------------------------

    def _on_configure(self, event) -> None:
        if event.width <= 1 or event.height <= 1:
            return
        self._canvas_w = int(event.width)
        self._canvas_h = int(event.height)
        self._redraw()

    def _on_click(self, event) -> None:
        try:
            self.focus_set()
        except tk.TclError:
            pass
        self._select_index(self._index_at(float(event.x)))

    def _on_motion(self, event) -> None:
        index = self._index_at(float(event.x))
        if index != self._hover_index:
            self._hover_index = index
            self._redraw()

    def _on_leave(self, _event=None) -> None:
        if self._hover_index != -1:
            self._hover_index = -1
            self._redraw()


# ---------------------------------------------------------------------------
# 3. Card —— 卡片容器
# ---------------------------------------------------------------------------


class Card(tk.Frame):
    """卡片容器：深色背景 + 1px 描边，可选标题/副标题与右侧扩展区。

    参数
    ----
    master: 父容器。
    title: 标题（粗体）；``None`` 表示不显示标题栏。
    subtitle: 副标题（弱化色），显示在标题下方。
    padx / pady: 卡片内边距（像素）。
    bg: 卡片背景色，默认 ``PALETTE["card"]``。
    border_color: 描边颜色，默认 ``PALETTE["border"]``。

    属性
    ----
    body:
        内容容器 ``tk.Frame``（与卡片同色），业务控件都放进这里。
    header_right:
        标题栏右侧容器 ``tk.Frame``，可放状态胶囊、按钮等。
    title_label / subtitle_label:
        标题 / 副标题的 ``tk.Label``（无则为 ``None``）。
    """

    def __init__(
        self,
        master: tk.Misc,
        title: Optional[str] = None,
        subtitle: Optional[str] = None,
        padx: int = 18,
        pady: int = 16,
        bg: str = PALETTE["card"],
        border_color: str = PALETTE["border"],
        title_font=None,
        subtitle_font=None,
        **kwargs,
    ) -> None:
        _ensure_fonts(master)
        super().__init__(
            master,
            bg=bg,
            bd=0,
            highlightthickness=1,
            highlightbackground=border_color,
            highlightcolor=border_color,
            **kwargs,
        )
        self._bg = bg
        self._padx = int(padx)
        self._pady = int(pady)

        self._header = tk.Frame(self, bg=bg)
        self.header_right = tk.Frame(self._header, bg=bg)
        self.title_label: Optional[tk.Label] = None
        self.subtitle_label: Optional[tk.Label] = None

        has_header = bool(title) or bool(subtitle)
        if has_header:
            self._header.pack(fill="x", padx=self._padx, pady=(self._pady, 0))
            left = tk.Frame(self._header, bg=bg)
            left.pack(side="left", fill="x", expand=True)
            if title:
                self.title_label = tk.Label(
                    left,
                    text=str(title),
                    bg=bg,
                    fg=PALETTE["text"],
                    font=title_font or font(13, bold=True),
                    anchor="w",
                    justify="left",
                )
                self.title_label.pack(side="top", anchor="w")
            if subtitle:
                self.subtitle_label = tk.Label(
                    left,
                    text=str(subtitle),
                    bg=bg,
                    fg=PALETTE["muted"],
                    font=subtitle_font or font(10),
                    anchor="w",
                    justify="left",
                )
                self.subtitle_label.pack(side="top", anchor="w", pady=(2, 0))
            self.header_right.pack(side="right", anchor="e", padx=(self._padx, 0))

        self.body = tk.Frame(self, bg=bg)
        if has_header:
            self.body.pack(fill="both", expand=True, padx=self._padx,
                           pady=(max(8, self._pady // 2), self._pady))
        else:
            self.body.pack(fill="both", expand=True, padx=self._padx, pady=self._pady)

    def set_title(self, text: str) -> None:
        """更新标题文字（若创建时没有标题则不会自动创建标签）。"""
        if self.title_label is not None:
            self.title_label.configure(text=str(text))

    def set_subtitle(self, text: str) -> None:
        """更新副标题文字（若创建时没有副标题则不会自动创建标签）。"""
        if self.subtitle_label is not None:
            self.subtitle_label.configure(text=str(text))


# ---------------------------------------------------------------------------
# 4. 细长滚动条（内部复用）
# ---------------------------------------------------------------------------


class _SlimScrollbar(tk.Canvas):
    """内部使用的细长深色滚动条（Canvas 绘制，支持拖拽与整页点击）。"""

    def __init__(
        self,
        master: tk.Misc,
        command: Callable,
        width: int = 12,
        bg: str = PALETTE["bg"],
        thumb_color: Optional[str] = None,
        hover_color: Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            width=width,
            bg=bg,
            highlightthickness=0,
            bd=0,
            cursor="arrow",
            **kwargs,
        )
        self._command = command
        self._thumb_color = thumb_color or _mix(PALETTE["border"], PALETTE["dim"], 0.45)
        self._hover_color = hover_color or PALETTE["dim"]
        self._first = 0.0
        self._last = 1.0
        self._hover = False
        self._dragging = False
        self._drag_start_y = 0
        self._drag_start_first = 0.0

        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    # ---- 公开 API -----------------------------------------------------

    def set(self, first, last) -> None:
        """由滚动容器的 yscrollcommand 调用，更新滑块比例。"""
        try:
            first_f = float(first)
            last_f = float(last)
        except (TypeError, ValueError):
            return
        self._first = max(0.0, min(1.0, first_f))
        self._last = max(self._first, min(1.0, last_f))
        self._redraw()

    # ---- 内部实现 -----------------------------------------------------

    def _thumb_span(self) -> tuple[float, float]:
        h = max(1.0, float(self.winfo_height()))
        if self._last - self._first >= 0.999:
            return (0.0, 0.0)
        y1 = self._first * h
        y2 = self._last * h
        min_h = max(18.0, min(h * 0.5, h * 0.12))
        if y2 - y1 < min_h:
            y2 = y1 + min_h
            if y2 > h:
                y2 = h
                y1 = max(0.0, h - min_h)
        return (max(0.0, y1), min(h, y2))

    def _redraw(self) -> None:
        self.delete("all")
        w = float(self.winfo_width())
        h = float(self.winfo_height())
        if w <= 1 or h <= 1:
            return
        if self._last - self._first >= 0.999:
            return  # 内容不足以滚动：隐藏滑块（保留占位宽度）
        y1, y2 = self._thumb_span()
        if y2 - y1 < 2:
            return
        color = self._hover_color if (self._hover or self._dragging) else self._thumb_color
        x1 = 2.0
        x2 = max(x1 + 2.0, w - 2.0)
        radius = (x2 - x1) / 2.0
        _round_rect(self, x1, y1 + 1, x2, y2 - 1, radius, fill=color, outline="")

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._redraw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._redraw()

    def _on_press(self, event) -> None:
        y1, y2 = self._thumb_span()
        h = max(1.0, float(self.winfo_height()))
        if y1 <= float(event.y) <= y2 and y2 > y1:
            self._dragging = True
            self._drag_start_y = int(event.y)
            self._drag_start_first = self._first
        else:
            try:
                self._command("scroll", -1 if float(event.y) < y1 else 1, "pages")
            except Exception:
                pass
        self._redraw()

    def _on_drag(self, event) -> None:
        if not self._dragging:
            return
        h = max(1.0, float(self.winfo_height()))
        span = max(0.0, self._last - self._first)
        max_first = max(0.0, 1.0 - span)
        delta = (int(event.y) - self._drag_start_y) / h
        first = max(0.0, min(max_first, self._drag_start_first + delta))
        try:
            self._command("moveto", first)
        except Exception:
            pass
        self._redraw()

    def _on_release(self, _event=None) -> None:
        if self._dragging:
            self._dragging = False
            self._redraw()


# ---------------------------------------------------------------------------
# 5. ScrollFrame —— 可滚动容器
# ---------------------------------------------------------------------------

#: 默认的滚轮步进（像素）；创建控件时按默认字体的行高重新计算。
_WHEEL_STEP_FALLBACK = 60


def _dispatch_wheel(event):
    """全局滚轮分发：只让「鼠标正下方的 ScrollFrame」滚动，并跳过自身可滚动的控件。"""
    try:
        pointer = event.widget.winfo_containing(event.x_root, event.y_root)
    except (tk.TclError, AttributeError):
        return None
    if pointer is None:
        return None
    # Text / Listbox / Treeview 自己有滚轮行为，交给它们处理，避免套娃双滚动。
    try:
        if pointer.winfo_class() in ("Text", "Listbox", "Treeview"):
            return None
    except tk.TclError:
        return None

    node = pointer
    while node is not None:
        if isinstance(node, ScrollFrame):
            try:
                return node._handle_wheel(event)
            except tk.TclError:
                return None
        node = getattr(node, "master", None)
    return None


def _ensure_wheel_binding(widget: tk.Misc) -> None:
    """每个 Tk 顶层窗口只安装一次全局滚轮监听。"""
    try:
        top = widget.winfo_toplevel()
    except tk.TclError:
        return
    if getattr(top, "_widgets_wheel_bound", False):
        return
    try:
        top.bind_all("<MouseWheel>", _dispatch_wheel, add="+")
        top.bind_all("<Button-4>", _dispatch_wheel, add="+")
        top.bind_all("<Button-5>", _dispatch_wheel, add="+")
        top._widgets_wheel_bound = True  # type: ignore[attr-defined]
    except tk.TclError:
        pass


class ScrollFrame(tk.Frame):
    """纵向滚动容器：内容区 + 右侧细滚动条，滚轮在指针进入内容区时生效。

    用法::

        sc = ScrollFrame(parent, bg=PALETTE["bg"])
        sc.pack(fill="both", expand=True)
        for item in items:
            tk.Label(sc.inner, ...).pack(...)

    属性
    ----
    inner: 内容容器 ``tk.Frame``，把需要滚动的控件放进去。
    canvas: 内部 ``tk.Canvas``（一般不需要直接使用）。
    vbar: 内部细滚动条（一般不需要直接使用）。
    """

    def __init__(self, master: tk.Misc, bg: str = PALETTE["bg"],
                 height: Optional[int] = None, **kwargs) -> None:
        _ensure_fonts(master)
        super().__init__(master, bg=bg, **kwargs)
        self._bg = bg

        self.canvas = tk.Canvas(
            self,
            bg=bg,
            highlightthickness=0,
            bd=0,
            yscrollcommand=self._on_yscroll,
        )
        if height is not None:
            self.canvas.configure(height=int(height))
        else:
            self.canvas.configure(height=200)
        self.vbar = _SlimScrollbar(self, command=self.canvas.yview, bg=bg)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._content_window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        try:
            self._wheel_step = max(
                20, int(3 * tkfont.Font(root=self, font=font(10)).metrics("linespace"))
            )
        except Exception:
            self._wheel_step = _WHEEL_STEP_FALLBACK

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.vbar.set(0.0, 1.0)
        _ensure_wheel_binding(self)

    # ---- 公开 API -----------------------------------------------------

    def scroll_to_top(self) -> None:
        """滚动到内容顶部。"""
        self.canvas.yview_moveto(0.0)

    def scroll_to_bottom(self) -> None:
        """滚动到内容底部。"""
        self.canvas.yview_moveto(1.0)

    # ---- 内部实现 -----------------------------------------------------

    def _on_yscroll(self, first, last) -> None:
        self.vbar.set(first, last)

    def _on_inner_configure(self, _event=None) -> None:
        self._sync_scrollregion()

    def _on_canvas_configure(self, event) -> None:
        try:
            self.canvas.itemconfigure(self._content_window, width=max(1, int(event.width)))
        except tk.TclError:
            return
        self._sync_scrollregion()

    def _sync_scrollregion(self) -> None:
        try:
            bbox = self.canvas.bbox("all")
        except tk.TclError:
            return
        if bbox is not None:
            self.canvas.configure(scrollregion=bbox)
        self.vbar.set(*self.canvas.yview())

    def _handle_wheel(self, event):
        num = getattr(event, "num", None)
        if num == 4:
            notches = 1
        elif num == 5:
            notches = -1
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return None
            notches = int(delta / 120) or (1 if delta > 0 else -1)
        try:
            bbox = self.canvas.bbox("all")
        except tk.TclError:
            return None
        if bbox is None:
            return None
        total = float(bbox[3] - bbox[1])
        view_h = float(self.canvas.winfo_height())
        if total <= view_h or view_h <= 1:
            return None
        offset = self.canvas.yview()[0] * total - notches * self._wheel_step
        offset = max(0.0, min(total - view_h, offset))
        self.canvas.yview_moveto(offset / total)
        return "break"


# ---------------------------------------------------------------------------
# 6. LogView —— 分级日志视图
# ---------------------------------------------------------------------------


class LogView(tk.Frame):
    """只读分级日志视图（中文友好、等宽字体、自动限行、贴底时自动滚动）。

    参数
    ----
    master: 父容器。
    height: 显示行数（tk.Text 的 height 语义）。
    font: 字体规格；省略时使用 ``font(10, mono=True)``。

    属性
    ----
    text: 内部 ``tk.Text``（只读，一般不需要直接操作）。
    """

    #: 日志级别 -> 颜色
    LEVEL_COLORS: dict[str, str] = {
        "info": PALETTE["text"],
        "success": PALETTE["success"],
        "warn": PALETTE["warning"],
        "error": PALETTE["danger"],
        "accent": PALETTE["accent"],
        "muted": PALETTE["muted"],
    }

    MAX_LINES = 800

    def __init__(self, master: tk.Misc, height: int = 10, font=None, **kwargs) -> None:
        _ensure_fonts(master)
        super().__init__(
            master,
            bg=PALETTE["bg"],
            bd=0,
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            highlightcolor=PALETTE["border"],
            **kwargs,
        )
        if font is None:
            font = default_log_font()
        self._font = _make_font(self, font, 10)
        linespace = int(self._font.metrics("linespace") or 16)
        pad_x = max(8, linespace // 2)
        pad_y = max(6, linespace // 3)
        spacing = max(1, linespace // 8)

        self.text = tk.Text(
            self,
            height=int(height),
            width=40,
            wrap="word",
            bg=PALETTE["bg"],
            fg=PALETTE["text"],
            insertbackground=PALETTE["text"],
            selectbackground=PALETTE["accent_dark"],
            selectforeground=PALETTE["text"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=pad_x,
            pady=pad_y,
            spacing1=spacing,
            spacing3=spacing,
            font=self._font,
            state="disabled",
            cursor="arrow",
            yscrollcommand=self._on_yscroll,
        )
        self.vbar = _SlimScrollbar(self, command=self.text.yview, bg=PALETTE["bg"])

        self.text.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        for level, color in self.LEVEL_COLORS.items():
            self.text.tag_configure(level, foreground=color)
        self._line_count = 0

    # ---- 公开 API -----------------------------------------------------

    def append(self, text: str, level: str = "info") -> None:
        """追加一行日志；``level`` 取 ``info/success/warn/error/accent/muted``。

        若用户已经向上滚动（不在底部），不会强制拉回底部；日志总量超过
        :attr:`MAX_LINES` 时自动删除最旧的行。未知 level 按 ``info`` 处理。
        """
        level = level if level in self.LEVEL_COLORS else "info"
        line = str(text).rstrip("\n")
        # 占位行数：内容中的换行也会换行显示
        added_lines = line.count("\n") + 1
        at_bottom = self._at_bottom()

        self.text.configure(state="normal")
        try:
            self.text.insert("end", line + "\n", (level,))
        finally:
            self.text.configure(state="disabled")
        self._line_count += added_lines

        if self._line_count > self.MAX_LINES:
            excess = self._line_count - self.MAX_LINES
            self.text.configure(state="normal")
            try:
                self.text.delete("1.0", f"{excess + 1}.0")
            finally:
                self.text.configure(state="disabled")
            self._line_count = self.MAX_LINES

        if at_bottom:
            try:
                self.text.see("end")
            except tk.TclError:
                pass

    def clear(self) -> None:
        """清空全部日志。"""
        self.text.configure(state="normal")
        try:
            self.text.delete("1.0", "end")
        finally:
            self.text.configure(state="disabled")
        self._line_count = 0

    # ---- 内部实现 -----------------------------------------------------

    def _at_bottom(self) -> bool:
        try:
            _first, last = self.text.yview()
        except tk.TclError:
            return True
        return last >= 0.999

    def _on_yscroll(self, first, last) -> None:
        self.vbar.set(first, last)


def default_log_font() -> tuple:
    """LogView 默认字体：等宽（无调用方字体时使用）。"""
    return font(10, mono=True)


# ---------------------------------------------------------------------------
# 7. StatusPill —— 状态胶囊
# ---------------------------------------------------------------------------


class StatusPill(tk.Canvas):
    """状态胶囊：彩色圆点 + 文案，背景为对应颜色的暗色蒙层。

    参数
    ----
    master: 父容器。
    width: 初始宽度（像素）；调用 :meth:`set` 后默认按文字自动调整，
        也可以随时用 :meth:`set_width` 固定宽度。
    height: 高度（像素）。
    font: 字体规格；省略时使用 ``font(10)``。
    """

    def __init__(
        self,
        master: tk.Misc,
        width: int = 150,
        height: int = 30,
        font=None,
        **kwargs,
    ) -> None:
        _ensure_fonts(master)
        self._canvas_bg = kwargs.pop("canvas_bg", None) or _parent_bg(master, PALETTE["bg"])
        super().__init__(
            master,
            width=width,
            height=height,
            bg=self._canvas_bg,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self._font = _make_font(self, font, 10)
        self._height = int(height)
        self._text = ""
        self._color = PALETTE["muted"]
        self._fixed_width: Optional[int] = None
        self._canvas_w = int(width)
        self._canvas_h = int(height)
        self.bind("<Configure>", self._on_configure)

    # ---- 公开 API -----------------------------------------------------

    def set(self, text: str, color: str = PALETTE["muted"]) -> None:
        """更新文案与颜色；宽度按文字自动重新计算（若未固定宽度）。"""
        self._text = str(text)
        self._color = str(color) if color else PALETTE["muted"]
        if self._fixed_width is None:
            self._apply_auto_width()
        else:
            self._canvas_w = self._fixed_width
        self._redraw()

    def set_width(self, width: Optional[int]) -> None:
        """固定胶囊宽度（像素）；传 ``None`` 恢复按文字自适应。"""
        self._fixed_width = int(width) if width is not None else None
        if self._fixed_width is not None:
            self._canvas_w = self._fixed_width
            try:
                self.configure(width=self._fixed_width)
            except tk.TclError:
                return
        else:
            self._apply_auto_width()
        self._redraw()

    def get_text(self) -> str:
        """返回当前文案。"""
        return self._text

    # ---- 内部实现 -----------------------------------------------------

    def _metrics(self) -> tuple[float, float, float, float]:
        """返回 (内容宽度, 圆点半径, 间距, 内边距)。"""
        h = float(self._height or 30)
        dot_r = max(2.0, h * 0.13)
        gap = max(4.0, h * 0.20)
        pad = max(7.0, h * 0.30)
        text_w = float(self._font.measure(self._text)) if self._text else 0.0
        content_w = dot_r * 2 + (gap + text_w if self._text else 0.0)
        return content_w, dot_r, gap, pad

    def _apply_auto_width(self) -> None:
        content_w, _dot_r, _gap, pad = self._metrics()
        width = int(round(content_w + pad * 2))
        width = max(24, width)
        self._canvas_w = width
        try:
            self.configure(width=width)
        except tk.TclError:
            pass

    def _on_configure(self, event) -> None:
        if event.width <= 1 or event.height <= 1:
            return
        self._canvas_w = int(event.width)
        self._canvas_h = int(event.height)
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w = float(self._canvas_w)
        h = float(self._height or self._canvas_h or 30)
        if w <= 1 or h <= 1:
            return
        fill = _mix(PALETTE["bg2"], self._color, 0.16)
        outline = _mix(PALETTE["bg2"], self._color, 0.40)
        _round_rect(self, 0.5, 0.5, w - 0.5, h - 0.5, h / 2.0 - 0.5,
                    fill=fill, outline=outline, width=1)
        if not self._text:
            return
        content_w, dot_r, gap, _pad = self._metrics()
        x_start = (w - content_w) / 2.0
        dot_cx = x_start + dot_r
        self.create_oval(
            dot_cx - dot_r, h / 2.0 - dot_r, dot_cx + dot_r, h / 2.0 + dot_r,
            fill=self._color, outline="",
        )
        self.create_text(
            x_start + dot_r * 2 + gap,
            h / 2.0,
            text=self._text,
            fill=_mix(self._color, "#ffffff", 0.28),
            font=self._font,
            anchor="w",
        )


# ---------------------------------------------------------------------------
# 8. StatTile —— 指标卡
# ---------------------------------------------------------------------------


class StatTile(tk.Frame):
    """指标卡：小号弱化标题 + 大号粗体数值，可选强调色。

    参数
    ----
    master: 父容器。
    title: 指标标题（如「累计点击」）。
    value: 初始数值文本。
    width / height: 像素尺寸（作为初始尺寸，被布局管理器拉伸时仍显示完整卡片）。
    accent: 数值强调色；省略使用主文本色。
    """

    def __init__(
        self,
        master: tk.Misc,
        title: str,
        value: str = "-",
        width: int = 120,
        height: int = 64,
        accent: Optional[str] = None,
        **kwargs,
    ) -> None:
        _ensure_fonts(master)
        super().__init__(
            master,
            bg=PALETTE["card"],
            width=int(width),
            height=int(height),
            bd=0,
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            highlightcolor=PALETTE["border"],
            **kwargs,
        )
        self._accent = accent
        self._width = int(width)
        self._height = int(height)
        self.pack_propagate(False)
        self.grid_propagate(False)

        pad_x = max(8, self._width // 9)
        pad_y = max(6, self._height // 10)

        self.title_label = tk.Label(
            self,
            text=str(title),
            bg=PALETTE["card"],
            fg=PALETTE["muted"],
            font=font(9),
            anchor="w",
            justify="left",
        )
        self.title_label.pack(anchor="w", padx=pad_x, pady=(pad_y, 0))

        self.value_label = tk.Label(
            self,
            text=str(value),
            bg=PALETTE["card"],
            fg=accent or PALETTE["text"],
            font=font(15, bold=True),
            anchor="w",
            justify="left",
        )
        self.value_label.pack(anchor="w", padx=pad_x, pady=(0, pad_y))

    # ---- 公开 API -----------------------------------------------------

    def set_value(self, value) -> None:
        """更新数值文本。"""
        self.value_label.configure(text=str(value))

    def get_value(self) -> str:
        """返回当前数值文本。"""
        return str(self.value_label.cget("text"))

    def set_accent(self, color: Optional[str]) -> None:
        """设置数值强调色；传 ``None`` 恢复主文本色。"""
        self._accent = color
        self.value_label.configure(fg=color or PALETTE["text"])

    def set_title(self, title: str) -> None:
        """更新标题文本。"""
        self.title_label.configure(text=str(title))


# ---------------------------------------------------------------------------
# 9. LabeledEntry —— 标签 + 输入框
# ---------------------------------------------------------------------------


class LabeledEntry(tk.Frame):
    """深色输入行：左侧标签 + 右侧 ``tk.Entry``。

    参数
    ----
    master: 父容器。
    label: 左侧标签文字（可为空）。
    var: 绑定的 ``tk.StringVar``；省略时自动创建，可通过 ``.var`` 访问。
    width: Entry 的字符宽度（Tk 语义），默认 18。
    label_width: 标签列的最小像素宽度（可选）。
    font: 输入框字体规格；省略时使用 ``font(11)``。
    show: 同 ``tk.Entry`` 的 ``show`` 参数（密码框）。
    """

    def __init__(
        self,
        master: tk.Misc,
        label: str = "",
        var: Optional[tk.StringVar] = None,
        width: int = 18,
        label_width: Optional[int] = None,
        font=None,
        show: Optional[str] = None,
        **kwargs,
    ) -> None:
        _ensure_fonts(master)
        bg = kwargs.pop("bg", None) or _parent_bg(master, PALETTE["bg"])
        super().__init__(master, bg=bg, **kwargs)
        self._bg = bg
        self._label_text = str(label)

        self.label = tk.Label(
            self,
            text=self._label_text,
            bg=bg,
            fg=PALETTE["muted"],
            font=_font_fn(10),
            anchor="w",
            justify="left",
        )
        self.label.grid(row=0, column=0, sticky="w", padx=(0, 10))

        entry_font = _make_font(self, font, 11)
        linespace = int(entry_font.metrics("linespace") or 20)
        pad_x = max(8, linespace // 2)
        pad_y = max(3, linespace // 8)
        self.var = var if var is not None else tk.StringVar(master=self, value="")

        # Entry 自身不画边框/内边距；由外层 _box 负责 1px 描边与内边距，
        # 这样输入文字不会贴着边框，更接近现代桌面 App 的输入框观感。
        self._box = tk.Frame(
            self,
            bg=PALETTE["bg2"],
            bd=0,
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            highlightcolor=PALETTE["border"],
        )
        self.entry = tk.Entry(
            self._box,
            textvariable=self.var,
            width=int(width),
            bg=PALETTE["bg2"],
            fg=PALETTE["text"],
            insertbackground=PALETTE["text"],
            selectbackground=PALETTE["accent_dark"],
            selectforeground=PALETTE["text"],
            disabledbackground=PALETTE["bg2"],
            disabledforeground=PALETTE["dim"],
            readonlybackground=PALETTE["bg2"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=entry_font,
        )
        if show is not None:
            self.entry.configure(show=show)
        self.entry.pack(fill="both", expand=True, padx=pad_x, pady=pad_y)
        self._box.grid(row=0, column=1, sticky="ew")
        self.columnconfigure(1, weight=1)
        if label_width is not None:
            self.columnconfigure(0, minsize=int(label_width))

        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)

    # ---- 公开 API -----------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        """启用 / 禁用输入框。"""
        enabled = bool(enabled)
        self.entry.configure(state="normal" if enabled else "disabled")
        if not enabled:
            self._set_border(False)

    def is_enabled(self) -> bool:
        """输入框当前是否可编辑。"""
        return str(self.entry.cget("state")) != "disabled"

    def get_value(self) -> str:
        """返回当前输入内容。"""
        return self.var.get()

    def set_value(self, value) -> None:
        """设置输入内容。"""
        self.var.set(str(value))

    # ---- 内部实现 -----------------------------------------------------

    def _set_border(self, focused: bool) -> None:
        color = PALETTE["accent"] if focused else PALETTE["border"]
        try:
            self._box.configure(highlightbackground=color, highlightcolor=color)
        except tk.TclError:
            pass

    def _on_focus_in(self, _event=None) -> None:
        if self.is_enabled():
            self._set_border(True)

    def _on_focus_out(self, _event=None) -> None:
        self._set_border(False)
