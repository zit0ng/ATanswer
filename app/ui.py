"""主界面（tkinter + 自定义深色组件）。

界面结构
--------
顶部：品牌区 + 运行状态胶囊 + 窗口置顶
左侧：工作模式 / 定点连点参数 / 找图点击参数 / 热键说明（可滚动）
右侧：开始-暂停-停止、运行统计、运行日志
底部：屏幕信息、权限、版本
"""
from __future__ import annotations

import math
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import filedialog, font as tkfont, messagebox, ttk

from . import APP_NAME, APP_SUBTITLE, VERSION, ai_client, paths, png, winapi
from . import widgets
from .ai_client import AIConfig
from .answer_popup import AnswerPopup, choose_answer_scope
from .browser_hybrid import BrowserHybridWindow
from .controllers import create_controller
from .engine import ClickConfig, validate_config
from .hotkeys import HK_PAUSE, HK_PICK, HK_START, HK_STOP, HOTKEY_LABELS, HotkeyManager
from .overlay import CoordinatePicker, EditableRegionFrame, RegionHUD, RegionPicker
from .preview import AIResultPreview, MatchPreview, OptionLayoutPreview
from .settings import Settings, load_settings, save_settings
from .templates import Template, load_template, save_template
from .typing_dialog import TextTypingDialog
from .widgets import (
    PALETTE,
    Card,
    LabeledEntry,
    LogView,
    RoundedButton,
    ScrollFrame,
    SegmentControl,
    SplitButton,
    StatTile,
    StatusPill,
    font,
)

_BUTTON_LABELS = {"左键": "left", "右键": "right", "中键": "middle"}
_BUTTON_LABELS_REV = {v: k for k, v in _BUTTON_LABELS.items()}
_CLICK_TYPES = ["单击", "双击"]
_AI_LETTERS = ["A", "B", "C", "D", "E", "F"]
_STATE_STYLE = {
    "idle": ("待机", PALETTE["muted"]),
    "starting": ("启动中", PALETTE["warning"]),
    "running": ("运行中", PALETTE["success"]),
    "paused": ("已暂停", PALETTE["warning"]),
    "stopping": ("停止中", PALETTE["muted"]),
}
_LOG_REASON = {
    "limit": "已达到设定次数/题目数，任务结束",
    "stopped": "任务已停止",
    "error": "连续执行出错，任务已自动停止",
    "scroll_end": "已滚动到页面底部（或达到滚动上限），任务结束",
}


class _CheckRow(tk.Canvas):
    """自绘深色复选框（替代系统原生 Checkbutton，避免白色方块破坏主题）。"""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        variable: tk.BooleanVar,
        command=None,
        box_size: int = 18,
        gap: int = 8,
        font_spec=None,
        bg: str | None = None,
    ) -> None:
        self._var = variable
        self._text = str(text)
        self._command = command
        self._box = max(12, int(box_size))
        self._gap = max(4, int(gap))
        self._font_spec = font_spec or font(10)
        self._bg = bg or master.cget("bg")
        self._hover = False
        self._enabled = True

        measurer = tkfont.Font(root=master, font=self._font_spec)
        line_height = measurer.metrics("linespace")
        text_w = measurer.measure(self._text)
        height = max(self._box, line_height) + 4
        width = self._box + self._gap + text_w + 4
        super().__init__(
            master,
            width=width,
            height=height,
            bg=self._bg,
            highlightthickness=0,
            bd=0,
            takefocus=1,
            cursor="hand2",
        )
        self._line_height = line_height
        self._text_w = text_w
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<space>", self._toggle)
        self.bind("<Return>", self._toggle)
        try:
            self._trace = self._var.trace_add("write", lambda *_: self._redraw())
        except tk.TclError:  # pragma: no cover
            self._trace = ""
        self._redraw()

    # ------------------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self._redraw()

    def _toggle(self, _event=None) -> str:
        if not self._enabled:
            return "break"
        self._var.set(not bool(self._var.get()))
        if callable(self._command):
            self._command()
        return "break"

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._redraw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._redraw()

    def _redraw(self) -> None:
        try:
            self.delete("all")
        except tk.TclError:  # pragma: no cover
            return
        box = self._box
        y0 = (int(self.cget("height")) - box) // 2
        x0 = 1
        checked = bool(self._var.get())
        fill = PALETTE["accent"] if checked else PALETTE["bg2"]
        outline = PALETTE["accent"] if checked else PALETTE["border"]
        if self._hover and self._enabled:
            outline = PALETTE["accent_hover"]
        r = max(3, box // 5)
        self.create_polygon(
            [
                x0 + r, y0,
                x0 + box - r, y0,
                x0 + box, y0 + r,
                x0 + box, y0 + box - r,
                x0 + box - r, y0 + box,
                x0 + r, y0 + box,
                x0, y0 + box - r,
                x0, y0 + r,
            ],
            fill=fill,
            outline=outline,
            width=1,
            smooth=True,
        )
        if checked:
            self.create_line(
                x0 + box * 0.24, y0 + box * 0.52,
                x0 + box * 0.44, y0 + box * 0.72,
                x0 + box * 0.78, y0 + box * 0.30,
                fill="#ffffff",
                width=max(2, box // 8),
                capstyle="round",
                joinstyle="round",
            )
        self.create_text(
            x0 + box + self._gap,
            self._line_height // 2 + 1,
            text=self._text,
            fill=PALETTE["text"] if self._enabled else PALETTE["dim"],
            font=self._font_spec,
            anchor="w",
        )



class MainApp:
    def __init__(self) -> None:
        self.scale = winapi.enable_dpi_awareness()
        self.settings: Settings = load_settings()
        self._queue: queue.Queue[dict] = queue.Queue()
        self.controller = create_controller(self._emit_event)
        self.hotkey_manager: HotkeyManager | None = None
        self.template: Template | None = None
        self.region: tuple[int, int, int, int] | None = None
        self.ai_region: tuple[int, int, int, int] | None = None
        self.state = "idle"
        self._thumb_photo = None
        self._logo_photo = None
        self._last_score: float | None = None
        self._started_at = 0.0
        self._last_event_time = 0.0
        self._picker_active = False
        self._ai_testing = False
        self._one_shot_busy = False
        self._fill_busy = False
        self._answer_scan_busy = False
        self._last_answer_region: tuple[int, int, int, int] | None = None
        self._last_answer_scope = ""
        self._auto_answer_running = False
        self._auto_answer_stop = threading.Event()
        self._browser_hybrid_win: BrowserHybridWindow | None = None
        self._answer_popup: AnswerPopup | None = None
        self._answer_frame: EditableRegionFrame | None = None
        self._answer_frame_visible = True
        self._typing_dialog: TextTypingDialog | None = None
        self._picker_root_was_visible = False
        self._single_shot_was_visible = False
        self.advanced_visible = False
        self.more_options_visible = False
        self._restore_after_run = False
        self._ai_detecting = False
        self._last_ai_raw = ""
        self._hud_ai: RegionHUD | None = None
        self._hud_search: RegionHUD | None = None

        self.root = tk.Tk()
        self.root.title("")
        self.root.configure(bg=PALETTE["bg"])
        try:
            self.root.attributes("-alpha", 0.90)
        except tk.TclError:  # pragma: no cover
            pass
        self.root.minsize(self.px(430), self.px(300))
        self._setup_window()
        try:
            widgets.configure_fonts(self.root)
        except Exception:
            pass
        self._setup_styles()

        try:
            if paths.ICON_FILE.exists():
                self.root.iconbitmap(default=str(paths.ICON_FILE))
        except tk.TclError:
            pass

        self.root.report_callback_exception = self._on_tk_exception

        self._build_ui()
        self._load_settings_into_ui()
        self._apply_advanced_visibility()
        self._apply_mode_visibility()
        self._fit_compact_window()
        self._refresh_template_thumb()
        self._update_screen_info()
        self._set_controls_running(False)
        self._update_stats(0, 0.0, None)
        self._log("程序已启动。F6 单次答题 · F7 单次填空 · F8 AI看答案/再次识别", "accent")
        self._start_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self._shutdown)
        self.root.after(80, self._drain_events)
        self.root.after(120, self._apply_titlebar_theme)
        self.root.after(500, self._update_clock)

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------
    def px(self, value: float) -> int:
        return max(1, int(round(value * self.scale)))

    def _emit_event(self, event: dict) -> None:
        try:
            self._queue.put(event)
        except Exception:
            pass

    def _setup_window(self) -> None:
        width, height = self.px(460), self.px(340)
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x = max(0, (sw - width) // 2)
            y = max(0, (sh - height) // 2 - self.px(10))
        except tk.TclError:  # pragma: no cover
            x = y = 0
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _fit_compact_window(self) -> None:
        try:
            self.root.update_idletasks()
            width = max(self.px(430), min(self.px(560), self.root.winfo_reqwidth()))
            height = max(self.px(300), min(self.px(430), self.root.winfo_reqheight()))
            self.root.geometry(f"{width}x{height}")
        except tk.TclError:  # pragma: no cover
            pass

    def _setup_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:  # pragma: no cover
            pass
        style.configure(
            "Dark.TCombobox",
            fieldbackground=PALETTE["bg2"],
            background=PALETTE["bg2"],
            foreground=PALETTE["text"],
            arrowcolor=PALETTE["muted"],
            bordercolor=PALETTE["border"],
            lightcolor=PALETTE["border"],
            darkcolor=PALETTE["border"],
            insertcolor=PALETTE["text"],
            padding=(8, 5),
            font=font(11),
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", PALETTE["bg2"]), ("disabled", PALETTE["bg2"])],
            foreground=[("readonly", PALETTE["text"]), ("disabled", PALETTE["dim"])],
            selectbackground=[("readonly", PALETTE["bg2"])],
            selectforeground=[("readonly", PALETTE["text"])],
            arrowcolor=[("disabled", PALETTE["dim"])],
        )
        self.root.option_add("*TCombobox*Listbox.background", PALETTE["bg2"])
        self.root.option_add("*TCombobox*Listbox.foreground", PALETTE["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", PALETTE["accent_dark"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", PALETTE["text"])
        self.root.option_add("*TCombobox*Listbox.font", font(11))

    # ------------------------------------------------------------------
    # 界面构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.body = tk.Frame(self.root, bg=PALETTE["bg"])
        self.body.pack(fill="both", expand=True, padx=self.px(14), pady=self.px(14))
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        self._build_left_panel()
        self._build_advanced_window()
        self._build_monitor_window()

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=PALETTE["bg"], height=self.px(84))
        header.pack(fill="x", padx=self.px(24), pady=(self.px(14), self.px(6)))
        header.pack_propagate(False)

        brand = tk.Frame(header, bg=PALETTE["bg"])
        brand.pack(side="left", fill="y")

        icon_box = tk.Frame(brand, bg=PALETTE["bg"])
        icon_box.pack(side="left", padx=(0, self.px(12)))
        logo = paths.ASSETS_DIR / "logo.png"
        if logo.exists():
            try:
                photo = tk.PhotoImage(file=str(logo), master=self.root)
                target = self.px(56)
                if photo.width() > target:
                    factor = max(1, round(photo.width() / target))
                    photo = photo.subsample(factor, factor)
                self._logo_photo = photo
                tk.Label(icon_box, image=self._logo_photo, bg=PALETTE["bg"], bd=0).pack()
            except tk.TclError:  # pragma: no cover
                self._draw_fallback_icon(icon_box)
        else:
            self._draw_fallback_icon(icon_box)

        texts = tk.Frame(brand, bg=PALETTE["bg"])
        texts.pack(side="left", fill="y")
        tk.Label(
            texts,
            text=APP_NAME,
            bg=PALETTE["bg"],
            fg=PALETTE["text"],
            font=font(20, bold=True),
            anchor="w",
        ).pack(side="top", anchor="w")
        tk.Label(
            texts,
            text=APP_SUBTITLE,
            bg=PALETTE["bg"],
            fg=PALETTE["muted"],
            font=font(10),
            anchor="w",
        ).pack(side="top", anchor="w", pady=(self.px(2), 0))

        right = tk.Frame(header, bg=PALETTE["bg"])
        right.pack(side="right", fill="y")
        self.status_pill = StatusPill(
            right, width=self.px(120), height=self.px(32), font=font(10), canvas_bg=PALETTE["bg"]
        )
        self.status_pill.pack(side="right", anchor="e")
        self.status_pill.set("待机", PALETTE["muted"])

        self.var_topmost = tk.BooleanVar(value=self.settings.topmost)
        top_check = self._make_check(right, "窗口置顶", self.var_topmost)
        top_check.configure(bg=PALETTE["bg"])
        top_check.pack(side="right", padx=(0, self.px(18)))
        self._topmost_check = top_check
        top_check._command = self._on_topmost_toggle

    def _draw_fallback_icon(self, parent: tk.Frame) -> None:
        size = self.px(44)
        canvas = tk.Canvas(parent, width=size, height=size, bg=PALETTE["bg"],
                           highlightthickness=0, bd=0)
        canvas.pack()
        pad = size * 0.08
        canvas.create_oval(pad, pad, size - pad, size - pad, outline=PALETTE["accent"], width=2)
        c = size / 2
        canvas.create_oval(c - size * 0.18, c - size * 0.18, c + size * 0.18,
                           c + size * 0.18, outline=PALETTE["cyan"], width=2)
        canvas.create_oval(c - size * 0.06, c - size * 0.06, c + size * 0.06,
                           c + size * 0.06, fill=PALETTE["cyan"], outline="")
        canvas.create_line(c, 0, c, size * 0.14, fill=PALETTE["accent"], width=2)
        canvas.create_line(c, size * 0.86, c, size, fill=PALETTE["accent"], width=2)
        canvas.create_line(0, c, size * 0.14, c, fill=PALETTE["accent"], width=2)
        canvas.create_line(size * 0.86, c, size, c, fill=PALETTE["accent"], width=2)

    # -- 左侧设置面板 ---------------------------------------------------
    def _build_left_panel(self) -> None:
        wrap = tk.Frame(self.body, bg=PALETTE["bg"])
        wrap.grid(row=0, column=0, sticky="nsew")
        inner = wrap

        # 默认窗口：只保留「常用功能」
        self.simple_card = Card(
            inner, title="常用功能", subtitle="点「⚙ 高级设置」展开全部参数",
        )
        self.simple_card.pack(fill="x", pady=(0, self.px(12)))
        self.var_topmost = tk.BooleanVar(value=self.settings.topmost)
        top_check = self._make_check(
            self.simple_card.header_right, "窗口置顶", self.var_topmost,
            command=self._on_topmost_toggle,
        )
        top_check.configure(bg=PALETTE["card"])
        top_check.pack(side="right", padx=(self.px(8), 0))
        self.status_pill = StatusPill(
            self.simple_card.header_right, width=self.px(88), height=self.px(28),
            font=font(9), canvas_bg=PALETTE["card"],
        )
        self.status_pill.pack(side="right")
        self.status_pill.set("待机", PALETTE["muted"])
        simple = self.simple_card.body
        self.btn_simple_quick = SplitButton(
            simple,
            left_text="单次填空",
            right_text="单次选择",
            left_command=self._on_simple_fill,
            right_command=self._on_simple_select,
            width=self.px(360),
            height=self.px(46),
            radius=self.px(10),
            bg=PALETTE["bg2"],
            hover_bg=PALETTE["card_hover"],
            press_bg=PALETTE["border"],
            border_color=PALETTE["border"],
            fg=PALETTE["text"],
            font=font(11, bold=True),
        )
        self.btn_simple_quick.pack(fill="x", pady=(0, self.px(8)))
        self.btn_simple_answers = RoundedButton(
            simple, text="AI 看答案（不自动作答）", command=self._on_ai_show_answers,
            width=self.px(170), height=self.px(48), radius=self.px(11),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], fg=PALETTE["text"], font=font(12, bold=True),
        )
        self.btn_simple_answers.pack(fill="x", pady=(0, self.px(8)))
        self.btn_auto_answer = SplitButton(
            simple,
            left_text="开始自动答题",
            right_text="停止",
            left_command=self._on_auto_answer_start,
            right_command=self._on_auto_answer_stop,
            width=self.px(360),
            height=self.px(40),
            radius=self.px(9),
            bg=PALETTE["bg2"],
            hover_bg=PALETTE["card_hover"],
            press_bg=PALETTE["border"],
            border_color=PALETTE["border"],
            fg=PALETTE["text"],
            font=font(10, bold=True),
        )
        self.btn_auto_answer.pack(fill="x", pady=(0, self.px(8)))
        tk.Label(
            simple,
            text="只针对有“下一题”选项的功能",
            bg=PALETTE["card"], fg=PALETTE["dim"], font=font(9),
            anchor="w",
        ).pack(fill="x", pady=(0, self.px(8)))
        simple_row = tk.Frame(simple, bg=PALETTE["card"])
        simple_row.pack(fill="x", pady=(0, self.px(8)))
        self.btn_simple_userscript = RoundedButton(
            simple_row, text="油猴脚本", command=self._on_open_userscript,
            width=self.px(150), height=self.px(34), radius=self.px(8),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], font=font(10),
        )
        self.btn_simple_userscript.pack(side="left")
        self.btn_simple_typing = RoundedButton(
            simple_row, text="逐字录入", command=self._on_text_typing,
            width=self.px(120), height=self.px(34), radius=self.px(8),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], font=font(10),
        )
        self.btn_simple_typing.pack(side="left", padx=(self.px(8), 0))
        gear_row = tk.Frame(simple, bg=PALETTE["card"])
        gear_row.pack(fill="x", pady=(self.px(4), 0))
        self.btn_advanced = RoundedButton(
            gear_row, text="⚙ 高级设置", command=self._toggle_advanced,
            width=self.px(150), height=self.px(30), radius=self.px(8),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], fg=PALETTE["cyan"], font=font(10),
        )
        self.btn_advanced.pack(side="left")
        self.btn_monitor = RoundedButton(
            gear_row, text="运行监测", command=self._toggle_monitor,
            width=self.px(100), height=self.px(30), radius=self.px(8),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], font=font(10),
        )
        self.btn_monitor.pack(side="left", padx=(self.px(8), 0))
        self.btn_browser_hybrid = RoundedButton(
            simple, text="浏览器混合答题（DOM 定位 + 视觉读题）",
            command=self._on_browser_hybrid,
            width=self.px(360), height=self.px(34), radius=self.px(8),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], fg=PALETTE["cyan"], font=font(9),
        )
        self.btn_browser_hybrid.pack(fill="x", pady=(self.px(8), 0))

    def _build_advanced_window(self) -> None:
        win = tk.Toplevel(self.root)
        self.advanced_win = win
        win.title("高级设置")
        win.configure(bg=PALETTE["bg"])
        try:
            win.attributes("-alpha", 1.0)
        except tk.TclError:  # pragma: no cover
            pass
        win.geometry(f"{self.px(760)}x{self.px(820)}")
        win.protocol("WM_DELETE_WINDOW", self._hide_advanced_window)
        win.withdraw()

        wrap = tk.Frame(win, bg=PALETTE["bg"], padx=self.px(14), pady=self.px(14))
        wrap.pack(fill="both", expand=True)
        self.scroll = ScrollFrame(wrap, bg=PALETTE["bg"])
        self.scroll.pack(fill="both", expand=True)
        inner = self.scroll.inner

        self.mode_card = Card(inner, title="工作模式", subtitle="选择本次要执行的任务类型")
        self.mode_card.pack(fill="x", pady=(0, self.px(12)))
        self.mode_segment = SegmentControl(
            self.mode_card.body,
            options=[("fixed", "定点连点"), ("image", "找图点击"), ("ai", "AI 答题")],
            command=self._on_mode_change,
            width=self.px(560),
            height=self.px(44),
            font=font(12),
        )
        self.mode_segment.pack(fill="x")

        self._build_fixed_card(inner)
        self._build_image_card(inner)
        self._build_ai_card(inner)
        self._build_ai_config_card(inner)

        self.help_card = Card(inner, title="全局热键", subtitle="窗口不在前台时同样生效")
        self.help_card.pack(fill="x", pady=(0, self.px(6)))
        help_body = self.help_card.body
        for hotkey_id in (HK_START, HK_PAUSE, HK_STOP):
            row = tk.Frame(help_body, bg=PALETTE["card"])
            row.pack(fill="x", pady=self.px(2))
            label = HOTKEY_LABELS.get(hotkey_id, "")
            key_text, action_text = (label.split(" ", 1) + ["", ""])[:2]
            tk.Label(
                row, text=key_text, bg=PALETTE["card"], fg=PALETTE["cyan"],
                font=font(10, bold=True), width=4, anchor="w",
            ).pack(side="left")
            tk.Label(
                row, text=action_text, bg=PALETTE["card"], fg=PALETTE["muted"],
                font=font(10), anchor="w",
            ).pack(side="left")
        tk.Label(
            help_body,
            text="提示：若目标程序以管理员身份运行，请使用管理员权限启动本程序。",
            bg=PALETTE["card"],
            fg=PALETTE["dim"],
            font=font(9),
            anchor="w",
            justify="left",
            wraplength=self.px(540),
        ).pack(fill="x", pady=(self.px(8), 0))

    def _row_label(self, parent: tk.Frame, text: str) -> None:
        tk.Label(
            parent,
            text=text,
            bg=PALETTE["card"],
            fg=PALETTE["muted"],
            font=font(10),
            anchor="w",
            width=8,
        ).pack(side="left", padx=(0, self.px(6)))

    def _make_entry(self, parent: tk.Misc, var: tk.StringVar, width: int = 8) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=var,
            width=width,
            bg=PALETTE["bg2"],
            fg=PALETTE["text"],
            insertbackground=PALETTE["text"],
            selectbackground=PALETTE["accent_dark"],
            selectforeground=PALETTE["text"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=PALETTE["border"],
            highlightcolor=PALETTE["accent"],
            font=font(11),
        )

    def _make_combo(self, parent: tk.Misc, var: tk.StringVar, values, width: int = 8
                    ) -> ttk.Combobox:
        combo = ttk.Combobox(
            parent,
            textvariable=var,
            values=values,
            state="readonly",
            width=width,
            style="Dark.TCombobox",
            font=font(11),
        )
        return combo

    def _unit_label(self, parent: tk.Misc, text: str) -> None:
        tk.Label(
            parent, text=text, bg=PALETTE["card"], fg=PALETTE["dim"], font=font(9)
        ).pack(side="left", padx=(self.px(5), self.px(12)))

    def _make_check(
        self, parent: tk.Misc, text: str, var: tk.BooleanVar, command=None
    ) -> "_CheckRow":
        try:
            parent_bg = parent.cget("bg")
        except (tk.TclError, AttributeError):  # pragma: no cover
            parent_bg = PALETTE["card"]
        return _CheckRow(
            parent,
            text,
            var,
            command=command,
            box_size=self.px(16),
            gap=self.px(7),
            font_spec=font(10),
            bg=parent_bg,
        )

    def _build_fixed_card(self, parent: tk.Misc) -> None:
        self.fixed_card = Card(
            parent, title="定点连点", subtitle="在固定坐标按设定间隔重复点击"
        )
        body = self.fixed_card.body

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(10)))
        self._row_label(row, "目标坐标")
        self.var_x = tk.StringVar(value="0")
        self.var_y = tk.StringVar(value="0")
        tk.Label(row, text="X", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self._make_entry(row, self.var_x, 7).pack(side="left", padx=(self.px(4), self.px(10)))
        tk.Label(row, text="Y", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self._make_entry(row, self.var_y, 7).pack(side="left", padx=(self.px(4), self.px(10)))
        self.btn_pick = RoundedButton(
            row, text="拾取坐标", command=self._on_pick_coord,
            width=self.px(108), height=self.px(32), radius=self.px(8),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], font=font(10),
        )
        self.btn_pick.pack(side="left")
        tk.Label(row, text="F9 快捷拾取", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left", padx=(self.px(8), 0))

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(10)))
        self._row_label(row, "鼠标按键")
        self.var_button = tk.StringVar(value=_BUTTON_LABELS_REV["left"])
        self._make_combo(row, self.var_button, list(_BUTTON_LABELS.keys()), 7).pack(side="left")
        self._unit_label(row, "")
        tk.Label(row, text="点击方式", bg=PALETTE["card"], fg=PALETTE["muted"],
                 font=font(10)).pack(side="left", padx=(0, self.px(8)))
        self.var_click_type = tk.StringVar(value=_CLICK_TYPES[0])
        self._make_combo(row, self.var_click_type, _CLICK_TYPES, 7).pack(side="left")

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(10)))
        self._row_label(row, "点击间隔")
        self.var_interval = tk.StringVar(value="1000")
        self._make_entry(row, self.var_interval, 8).pack(side="left")
        self._unit_label(row, "毫秒")
        self.var_random = tk.BooleanVar(value=False)
        self._make_check(row, "随机浮动", self.var_random).pack(side="left")
        self.var_min = tk.StringVar(value="800")
        self.var_max = tk.StringVar(value="1200")
        self.entry_min = self._make_entry(row, self.var_min, 6)
        self.entry_min.pack(side="left", padx=(self.px(6), 0))
        tk.Label(row, text="~", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(10)).pack(side="left", padx=self.px(3))
        self.entry_max = self._make_entry(row, self.var_max, 6)
        self.entry_max.pack(side="left")
        self.var_random.trace_add("write", lambda *_: self._on_random_toggle())

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(10)))
        self._row_label(row, "执行控制")
        tk.Label(row, text="次数", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self.var_count = tk.StringVar(value="0")
        self._make_entry(row, self.var_count, 6).pack(side="left", padx=(self.px(4), self.px(4)))
        tk.Label(row, text="次（0=无限）", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left", padx=(0, self.px(12)))
        tk.Label(row, text="延迟", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self.var_delay = tk.StringVar(value="2.0")
        self._make_entry(row, self.var_delay, 6).pack(side="left", padx=(self.px(4), self.px(4)))
        tk.Label(row, text="秒", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x")
        self.var_restore = tk.BooleanVar(value=False)
        self._make_check(row, "点击后把鼠标移回原位", self.var_restore).pack(side="left")

    def _build_image_card(self, parent: tk.Misc) -> None:
        self.image_card = Card(
            parent, title="找图点击", subtitle="识别屏幕上的图标/文字/按钮，自动点击其中心"
        )
        body = self.image_card.body

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(8)))
        self._row_label(row, "模板图片")
        self.var_template = tk.StringVar(value="")
        self.template_path_label = tk.Label(
            row, textvariable=self.var_template, bg=PALETTE["card"], fg=PALETTE["muted"],
            font=font(10), anchor="w", justify="left", wraplength=self.px(380),
        )
        self.template_path_label.pack(side="left", fill="x", expand=True)

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(10)))
        self.btn_choose_template = RoundedButton(
            row, text="选择图片", command=self._on_choose_template,
            width=self.px(110), height=self.px(32), radius=self.px(8),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], font=font(10),
        )
        self.btn_choose_template.pack(side="left")
        self.btn_capture_template = RoundedButton(
            row, text="从屏幕截取", command=self._on_capture_template,
            width=self.px(120), height=self.px(32), radius=self.px(8),
            bg=PALETTE["accent_dark"], hover_bg=PALETTE["accent"],
            font=font(10),
        )
        self.btn_capture_template.pack(side="left", padx=(self.px(8), self.px(12)))
        self.thumb_label = tk.Label(
            row, text="暂无模板", bg=PALETTE["bg2"], fg=PALETTE["dim"],
            font=font(9), width=16, height=3,
        )
        self.thumb_label.pack(side="left")

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(10)))
        self._row_label(row, "搜索区域")
        self.var_region_mode = tk.StringVar(value="full")
        for text, value in (("全屏", "full"), ("自定义区域", "custom")):
            tk.Radiobutton(
                row, text=text, value=value, variable=self.var_region_mode,
                command=self._on_region_mode_change,
                bg=PALETTE["card"], fg=PALETTE["muted"],
                activebackground=PALETTE["card"], activeforeground=PALETTE["text"],
                selectcolor=PALETTE["bg2"], font=font(10), bd=0, highlightthickness=0,
                cursor="hand2",
            ).pack(side="left", padx=(0, self.px(10)))
        self.btn_region = RoundedButton(
            row, text="框选区域", command=self._on_pick_region,
            width=self.px(100), height=self.px(30), radius=self.px(8),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], font=font(10),
        )
        self.btn_region.pack(side="left")
        self.region_label = tk.Label(
            row, text="全屏", bg=PALETTE["card"], fg=PALETTE["dim"], font=font(9)
        )
        self.region_label.pack(side="left", padx=(self.px(8), 0))
        self.var_show_search_region = tk.BooleanVar(value=True)
        self._make_check(
            row, "显示框", self.var_show_search_region, command=self._on_toggle_region_huds
        ).pack(side="left", padx=(self.px(8), 0))

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(10)))
        self._row_label(row, "识别参数")
        tk.Label(row, text="相似度", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self.var_threshold = tk.StringVar(value="90")
        self._make_entry(row, self.var_threshold, 5).pack(side="left", padx=(self.px(4), self.px(3)))
        tk.Label(row, text="%", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        tk.Label(row, text="轮询", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left", padx=(self.px(14), self.px(4)))
        self.var_poll = tk.StringVar(value="500")
        self._make_entry(row, self.var_poll, 6).pack(side="left")
        tk.Label(row, text="毫秒", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left", padx=(self.px(4), 0))

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(10)))
        self._row_label(row, "点击偏移")
        tk.Label(row, text="X", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self.var_offset_x = tk.StringVar(value="0")
        self._make_entry(row, self.var_offset_x, 5).pack(side="left", padx=(self.px(4), self.px(8)))
        tk.Label(row, text="Y", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self.var_offset_y = tk.StringVar(value="0")
        self._make_entry(row, self.var_offset_y, 5).pack(side="left", padx=(self.px(4), self.px(10)))
        self.btn_test_match = RoundedButton(
            row, text="测试识别", command=self._on_test_match,
            width=self.px(104), height=self.px(30), radius=self.px(8),
            bg=PALETTE["accent_dark"], hover_bg=PALETTE["accent"], font=font(10),
        )
        self.btn_test_match.pack(side="left")

        self.image_hint = tk.Label(
            body,
            text="建议：框选包含文字/图标/边框、且画面稳定的区域；模板需与当前屏幕分辨率一致。",
            bg=PALETTE["card"], fg=PALETTE["dim"], font=font(9),
            anchor="w", justify="left", wraplength=self.px(560),
        )
        self.image_hint.pack(fill="x", pady=(self.px(2), 0))

    def _build_ai_card(self, parent: tk.Misc) -> None:
        self.ai_card = Card(
            parent,
            title="AI 答题",
            subtitle="实时监控题目变化 → 多模态模型识别 → 自动选择答案",
        )
        body = self.ai_card.body

        # ---- 题目区域 ----
        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(8)))
        self._row_label(row, "题目区域")
        self.ai_region_label = tk.Label(
            row, text="未选择", bg=PALETTE["card"], fg=PALETTE["dim"], font=font(9)
        )
        self.ai_region_label.pack(side="left", padx=(0, self.px(10)))
        self.btn_ai_region = RoundedButton(
            row, text="框选题目区域", command=self._on_pick_ai_region,
            width=self.px(124), height=self.px(30), radius=self.px(8),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], font=font(10),
        )
        self.btn_ai_region.pack(side="left")
        self.var_show_ai_region = tk.BooleanVar(value=True)
        self._make_check(
            row, "显示框", self.var_show_ai_region, command=self._on_toggle_region_huds
        ).pack(side="left", padx=(self.px(10), 0))
        self.var_ai_fullscreen = tk.BooleanVar(value=False)

        # ---- 选项点击坐标 ----
        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(4)))
        self._row_label(row, "选项坐标")
        tk.Label(
            row, text="可点右侧一键识别，或手动拾取选项中心位置", bg=PALETTE["card"],
            fg=PALETTE["dim"], font=font(9),
        ).pack(side="left")
        self.btn_ai_detect = RoundedButton(
            row, text="实时扫描选项（A~F）", command=self._on_ai_detect,
            width=self.px(160), height=self.px(28), radius=self.px(8),
            bg=PALETTE["accent_dark"], hover_bg=PALETTE["accent"], font=font(9),
        )
        self.btn_ai_detect.pack(side="right")

        grid = tk.Frame(body, bg=PALETTE["card"])
        grid.pack(fill="x", pady=(0, self.px(8)))
        self.var_ai_opt: dict[str, tuple[tk.StringVar, tk.StringVar]] = {}
        for idx, letter in enumerate(_AI_LETTERS):
            cell = tk.Frame(grid, bg=PALETTE["card"])
            cell.grid(row=idx % 3, column=idx // 3, sticky="w",
                      padx=(0, self.px(18)), pady=(0, self.px(5)))
            tk.Label(cell, text=letter, bg=PALETTE["card"], fg=PALETTE["cyan"],
                     font=font(10, bold=True), width=2).pack(side="left")
            var_x = tk.StringVar(value="")
            var_y = tk.StringVar(value="")
            self.var_ai_opt[letter] = (var_x, var_y)
            tk.Label(cell, text="X", bg=PALETTE["card"], fg=PALETTE["dim"],
                     font=font(9)).pack(side="left")
            self._make_entry(cell, var_x, 4).pack(side="left", padx=(self.px(3), self.px(6)))
            tk.Label(cell, text="Y", bg=PALETTE["card"], fg=PALETTE["dim"],
                     font=font(9)).pack(side="left")
            self._make_entry(cell, var_y, 4).pack(side="left", padx=(self.px(3), self.px(6)))
            RoundedButton(
                cell, text="拾取", command=lambda l=letter: self._pick_ai_option(l),
                width=self.px(52), height=self.px(26), radius=self.px(7),
                bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
                border_color=PALETTE["border"], font=font(9),
            ).pack(side="left")

        # ---- 下一题坐标 ----
        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(8)))
        self._row_label(row, "下一题")
        tk.Label(row, text="X", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self.var_ai_next_x = tk.StringVar(value="")
        self._make_entry(row, self.var_ai_next_x, 6).pack(
            side="left", padx=(self.px(3), self.px(8))
        )
        tk.Label(row, text="Y", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self.var_ai_next_y = tk.StringVar(value="")
        self._make_entry(row, self.var_ai_next_y, 6).pack(
            side="left", padx=(self.px(3), self.px(8))
        )
        RoundedButton(
            row, text="拾取下一题", command=self._pick_ai_next,
            width=self.px(100), height=self.px(28), radius=self.px(7),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], font=font(9),
        ).pack(side="left")

        # ---- 实时监控选项 ----
        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(8)))
        self.var_ai_monitor = tk.BooleanVar(value=True)
        self._make_check(row, "实时监控（画面变化后自动作答）", self.var_ai_monitor).pack(side="left")
        self.var_ai_auto_next = tk.BooleanVar(value=True)
        self.check_ai_auto_next = self._make_check(
            row, "答完自动点下一题", self.var_ai_auto_next,
            command=self._on_auto_next_toggle,
        )
        self.check_ai_auto_next.pack(side="left", padx=(self.px(18), 0))
        tk.Label(
            row, text="不勾选 = 你自己翻页", bg=PALETTE["card"],
            fg=PALETTE["dim"], font=font(9),
        ).pack(side="left", padx=(self.px(8), 0))

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(8)))
        self.var_ai_realtime_layout = tk.BooleanVar(value=True)
        self._make_check(
            row, "实时定位选项坐标（滚动/布局变化时自动校正点击位置）",
            self.var_ai_realtime_layout,
        ).pack(side="left")

        self.var_ai_page_mode = tk.BooleanVar(value=True)
        self.var_ai_auto_scroll = tk.BooleanVar(value=False)
        self.var_ai_scroll_steps = tk.StringVar(value="3")
        self.var_ai_scroll_wait = tk.StringVar(value="400")
        self.var_ai_scroll_max = tk.StringVar(value="0")
        self.var_ai_fill_overwrite = tk.BooleanVar(value=True)

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(8)))
        self.var_ai_tile_mode = tk.BooleanVar(value=True)
        self._make_check(
            row, "大区域分块识别（更准，慢一些）", self.var_ai_tile_mode,
        ).pack(side="left")
        tk.Label(
            row, text="全屏/大区域切成 2×2 小块分别识别，避免字太小漏选；默认开启，关闭更快",
            bg=PALETTE["card"], fg=PALETTE["dim"], font=font(9),
        ).pack(side="left", padx=(self.px(8), 0))

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(8)))
        self.var_ai_fill_typing = tk.BooleanVar(value=True)
        self._make_check(
            row, "填空题用键盘逐字输入（不粘贴，兼容禁止粘贴的页面）",
            self.var_ai_fill_typing,
        ).pack(side="left")
        tk.Label(
            row, text="单次填空默认逐字输入；普通输入框也可用",
            bg=PALETTE["card"], fg=PALETTE["dim"], font=font(9),
        ).pack(side="left", padx=(self.px(8), 0))

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(8)))
        self._row_label(row, "填空偏移")
        tk.Label(row, text="Y", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self.var_ai_fill_offset_y = tk.StringVar(value="0")
        self._make_entry(row, self.var_ai_fill_offset_y, 5).pack(
            side="left", padx=(self.px(4), self.px(4))
        )
        tk.Label(row, text="像素", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        tk.Label(
            row, text="点击位置整体偏一行时填 -40 ~ -60 左右微调",
            bg=PALETTE["card"], fg=PALETTE["dim"], font=font(9),
        ).pack(side="left", padx=(self.px(10), 0))

        # ---- 节奏参数 ----
        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(8)))
        self._row_label(row, "节奏")
        tk.Label(row, text="选项后等待", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self.var_ai_option_wait = tk.StringVar(value="400")
        self._make_entry(row, self.var_ai_option_wait, 5).pack(
            side="left", padx=(self.px(4), self.px(4))
        )
        tk.Label(row, text="毫秒", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left", padx=(0, self.px(10)))
        tk.Label(row, text="下一题后等待", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self.var_ai_next_wait = tk.StringVar(value="1500")
        self._make_entry(row, self.var_ai_next_wait, 5).pack(
            side="left", padx=(self.px(4), self.px(4))
        )
        tk.Label(row, text="毫秒", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left", padx=(0, self.px(10)))
        tk.Label(row, text="监控间隔", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self.var_ai_poll = tk.StringVar(value="300")
        self._make_entry(row, self.var_ai_poll, 5).pack(
            side="left", padx=(self.px(4), self.px(4))
        )
        tk.Label(row, text="毫秒", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(10)))
        self._row_label(row, "执行")
        tk.Label(row, text="最多题目数", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self.var_ai_count = tk.StringVar(value="0")
        self._make_entry(row, self.var_ai_count, 5).pack(
            side="left", padx=(self.px(4), self.px(4))
        )
        tk.Label(row, text="道（0=无限）", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left", padx=(0, self.px(12)))
        tk.Label(row, text="开始前等待", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self.var_ai_delay = tk.StringVar(value="1.0")
        self._make_entry(row, self.var_ai_delay, 5).pack(
            side="left", padx=(self.px(4), self.px(4))
        )
        tk.Label(row, text="秒", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")

        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(6)))
        self.btn_ai_test = RoundedButton(
            row, text="测试识别", command=self._on_test_ai,
            width=self.px(104), height=self.px(30), radius=self.px(8),
            bg=PALETTE["accent_dark"], hover_bg=PALETTE["accent"], font=font(10),
        )
        self.btn_ai_test.pack(side="left")
        tk.Label(
            row, text="只识别并预览答案，不会点击屏幕", bg=PALETTE["card"],
            fg=PALETTE["dim"], font=font(9),
        ).pack(side="left", padx=(self.px(8), 0))
        self.btn_userscript = RoundedButton(
            row, text="油猴脚本（读网页文字，更快）", command=self._on_open_userscript,
            width=self.px(210), height=self.px(30), radius=self.px(8),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], font=font(10),
        )
        self.btn_userscript.pack(side="right")

        tk.Label(
            body,
            text=(
                "使用前请先配置下方「AI 接口设置」，框选题目区域后点「实时扫描选项（A~F）」"
                "一键填入选项/下一题坐标（也可手动拾取）。"
                " 支持单选/多选自动判断，多选会依次点选全部正确选项。"
                " 如果平台是普通网页，优先用「油猴脚本」直接读页面文字，速度比截图识别快很多。"
                " 单次选择和单次填空已内置整页多题识别；需要连续答选择题时用主界面的「开始答题」。"
                " 实时监控模式下，只有题目画面变化才会请求 AI，答题后也不会重复作答。"
                " 仅用于你自己的平台或学习/测试环境。"
            ),
            bg=PALETTE["card"], fg=PALETTE["dim"], font=font(9),
            anchor="w", justify="left", wraplength=self.px(560),
        ).pack(fill="x")

    def _ai_form_row(self, parent: tk.Frame, label: str, variable: tk.StringVar,
                     width: int = 20, show: str | None = None) -> tk.Entry:
        row = tk.Frame(parent, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(7)))
        self._row_label(row, label)
        entry = self._make_entry(row, variable, width)
        if show:
            entry.configure(show=show)
        entry.pack(side="left", fill="x", expand=True)
        return entry

    def _build_ai_config_card(self, parent: tk.Misc) -> None:
        self.ai_config_card = Card(
            parent,
            title="AI 接口设置",
            subtitle="OpenAI 兼容的多模态接口：智谱 GLM-4V / 阿里 Qwen-VL / OpenAI GPT-4o 等",
        )
        body = self.ai_config_card.body
        self.var_ai_base_url = tk.StringVar(value=ai_client.DEFAULT_BASE_URL)
        self.var_ai_model = tk.StringVar(value=ai_client.DEFAULT_MODEL)
        self.var_ai_api_key = tk.StringVar(value="")
        self.var_ai_timeout = tk.StringVar(value="60")
        self.var_ai_proxy = tk.StringVar(value="")
        self._ai_form_row(body, "API 地址", self.var_ai_base_url, 48)
        self._ai_form_row(body, "模型", self.var_ai_model, 24)
        key_row = tk.Frame(body, bg=PALETTE["card"])
        key_row.pack(fill="x", pady=(0, self.px(7)))
        self._row_label(key_row, "API Key")
        self.ai_key_entry = self._make_entry(key_row, self.var_ai_api_key, 36)
        self.ai_key_entry.configure(show="*")
        self.ai_key_entry.pack(side="left", fill="x", expand=True)
        self.var_ai_show_key = tk.BooleanVar(value=False)
        show_check = self._make_check(key_row, "显示", self.var_ai_show_key)
        show_check.pack(side="left", padx=(self.px(8), 0))
        self.var_ai_show_key.trace_add(
            "write",
            lambda *_: self.ai_key_entry.configure(
                show="" if self.var_ai_show_key.get() else "*"
            ),
        )
        self.var_ai_enable_thinking = tk.BooleanVar(value=False)
        thinking_row = tk.Frame(body, bg=PALETTE["card"])
        thinking_row.pack(fill="x", pady=(0, self.px(7)))
        self._make_check(
            thinking_row,
            "允许模型思考（不要勾选此选项）",
            self.var_ai_enable_thinking,
        ).pack(side="left")
        tk.Label(
            thinking_row,
            text="默认关闭；出现请求超时/空回复时请保持关闭",
            bg=PALETTE["card"], fg=PALETTE["dim"], font=font(9),
        ).pack(side="left", padx=(self.px(8), 0))
        row = tk.Frame(body, bg=PALETTE["card"])
        row.pack(fill="x", pady=(0, self.px(7)))
        self._row_label(row, "超时")
        self._make_entry(row, self.var_ai_timeout, 5).pack(side="left")
        tk.Label(row, text="秒", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left", padx=(self.px(4), self.px(16)))
        tk.Label(row, text="代理（可选）", bg=PALETTE["card"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")
        self._make_entry(row, self.var_ai_proxy, 24).pack(
            side="left", fill="x", expand=True, padx=(self.px(4), 0)
        )
        tk.Label(
            body,
            text=(
                "默认智谱 GLM-4V-Flash（bigmodel.cn 申请 Key）。也可填阿里云 DashScope 兼容地址"
                "（qwen-vl-max）、DeepSeek（https://api.deepseek.com + deepseek-flash）"
                "或 OpenAI（gpt-4o-mini，需要代理）。"
                " DeepSeek 只支持模型 deepseek-flash / deepseek-v4-pro，不要填 deepseek-v4-flash。"
                " 使用 DeepSeek 时默认关闭思考模式，避免长思考超时；API Key 以明文保存在本机 data/settings.json。"
            ),
            bg=PALETTE["card"], fg=PALETTE["dim"], font=font(9),
            anchor="w", justify="left", wraplength=self.px(560),
        ).pack(fill="x", pady=(self.px(4), 0))

    # -- 右侧控制面板 ---------------------------------------------------
    def _build_monitor_window(self) -> None:
        self._build_right_panel()

    def _build_right_panel(self) -> None:
        win = tk.Toplevel(self.root)
        self.monitor_win = win
        win.title("运行监测")
        win.configure(bg=PALETTE["bg"])
        try:
            win.attributes("-alpha", 1.0)
        except tk.TclError:  # pragma: no cover
            pass
        win.geometry(f"{self.px(760)}x{self.px(520)}")
        win.protocol("WM_DELETE_WINDOW", self._hide_monitor)
        win.withdraw()
        right = tk.Frame(win, bg=PALETTE["bg"], padx=self.px(14), pady=self.px(14))
        right.pack(fill="both", expand=True)
        right.grid_rowconfigure(0, weight=0)
        right.grid_rowconfigure(1, weight=0)
        right.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)

        action = Card(right, title="执行控制", subtitle="F6 开始 · F7 暂停/继续 · F8 停止")
        self.action_card = action
        action.grid(row=0, column=0, sticky="ew", pady=(0, self.px(12)))
        action.grid_remove()
        btn_width = self.px(360)
        self.btn_start = RoundedButton(
            action.body, text="开始执行  (F6)", command=self._on_start,
            width=btn_width, height=self.px(52), radius=self.px(12),
            bg=PALETTE["accent"], hover_bg=PALETTE["accent_hover"],
            press_bg=PALETTE["accent_dark"], fg="#ffffff", font=font(14, bold=True),
        )
        self.btn_start.pack(fill="x", pady=(0, self.px(8)))
        self.btn_pause = RoundedButton(
            action.body, text="暂停  (F7)", command=self._on_pause_resume,
            width=btn_width, height=self.px(42), radius=self.px(10),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], font=font(11),
        )
        self.btn_pause.pack(fill="x", pady=(0, self.px(8)))
        self.btn_stop = RoundedButton(
            action.body, text="停止  (F8)", command=self._on_stop,
            width=btn_width, height=self.px(42), radius=self.px(10),
            bg=PALETTE["danger"], hover_bg=PALETTE["danger_hover"],
            press_bg="#c24343", fg="#ffffff", font=font(11, bold=True),
        )
        self.btn_stop.pack(fill="x")
        self.btn_quick = SplitButton(
            action.body, left_text="单次答题", right_text="单次填空",
            left_command=self._on_one_shot_answer,
            right_command=self._on_one_shot_fill,
            width=btn_width, height=self.px(42), radius=self.px(10),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            press_bg=PALETTE["border"], border_color=PALETTE["border"],
            fg=PALETTE["text"], font=font(11),
        )
        self.btn_quick.pack(fill="x", pady=(self.px(8), 0))
        self.btn_typing = RoundedButton(
            action.body, text="逐字录入…", command=self._on_text_typing,
            width=btn_width, height=self.px(36), radius=self.px(9),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], font=font(10),
        )
        self.btn_typing.pack(fill="x", pady=(self.px(8), 0))
        tk.Label(
            action.body,
            text="左：框选后自动点选答案   ·   右：框选后自动填写答案\n"
                 "逐字录入：输入一句话，自动逐字打进你点中的文本框",
            bg=PALETTE["card"], fg=PALETTE["dim"], font=font(9),
            justify="left", anchor="w",
        ).pack(fill="x", pady=(self.px(6), 0))

        stats = Card(right, title="运行统计")
        stats.grid(row=1, column=0, sticky="ew", pady=(0, self.px(12)))
        stats_body = stats.body
        stats_body.grid_columnconfigure(0, weight=1)
        stats_body.grid_columnconfigure(1, weight=1)
        stats_body.grid_columnconfigure(2, weight=1)
        self.tile_clicks = StatTile(stats_body, title="点击次数", value="0",
                                    width=self.px(110), height=self.px(62),
                                    accent=PALETTE["accent"])
        self.tile_clicks.grid(row=0, column=0, sticky="nsew", padx=(0, self.px(6)))
        self.tile_time = StatTile(stats_body, title="运行时长", value="00:00",
                                   width=self.px(110), height=self.px(62),
                                   accent=PALETTE["cyan"])
        self.tile_time.grid(row=0, column=1, sticky="nsew", padx=self.px(6))
        self.tile_score = StatTile(stats_body, title="匹配相似度", value="-",
                                    width=self.px(110), height=self.px(62),
                                    accent=PALETTE["success"])
        self.tile_score.grid(row=0, column=2, sticky="nsew", padx=(self.px(6), 0))

        log_card = Card(right, title="运行日志")
        log_card.grid(row=2, column=0, sticky="nsew")
        self.btn_clear_log = RoundedButton(
            log_card.header_right, text="清空", command=self._on_clear_log,
            width=self.px(64), height=self.px(26), radius=self.px(6),
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], font=font(9),
        )
        self.btn_clear_log.pack(side="right", pady=(self.px(2), 0))
        self.log_view = LogView(log_card.body, height=12, font=font(9, mono=True))
        self.log_view.pack(fill="both", expand=True)

    def _build_footer(self) -> None:
        footer = tk.Frame(self.root, bg=PALETTE["bg"], height=self.px(34))
        footer.pack(side="bottom", fill="x", padx=self.px(24), pady=(0, self.px(8)))
        footer.pack_propagate(False)
        self.screen_info = tk.Label(
            footer, text="", bg=PALETTE["bg"], fg=PALETTE["dim"], font=font(9), anchor="w"
        )
        self.screen_info.pack(side="left")
        self.version_label = tk.Label(
            footer, text=f"v{VERSION} · Windows 专用版", bg=PALETTE["bg"],
            fg=PALETTE["dim"], font=font(9),
        )
        self.version_label.pack(side="right")
        if not winapi.is_admin():
            self.btn_admin = RoundedButton(
                footer, text="以管理员身份重启", command=self._on_restart_admin,
                width=self.px(150), height=self.px(28), radius=self.px(7),
                bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
                border_color=PALETTE["border"], font=font(9),
            )
            self.btn_admin.pack(side="right", padx=(0, self.px(12)))
            self.admin_label = tk.Label(
                footer, text="权限：普通用户", bg=PALETTE["bg"], fg=PALETTE["warning"],
                font=font(9),
            )
            self.admin_label.pack(side="right", padx=(0, self.px(10)))
        else:
            self.admin_label = tk.Label(
                footer, text="权限：管理员", bg=PALETTE["bg"], fg=PALETTE["success"],
                font=font(9),
            )
            self.admin_label.pack(side="right", padx=(0, self.px(12)))

    # ------------------------------------------------------------------
    # 设置 <-> 界面
    # ------------------------------------------------------------------
    @staticmethod
    def _short_path(path: str, max_len: int = 64) -> str:
        """把过长的模板路径缩短为适合界面显示的形式。"""
        if not path:
            return ""
        if len(path) <= max_len:
            return path
        from pathlib import Path

        p = Path(path)
        try:
            rel = f"…\\{p.parent.name}\\{p.name}"
        except Exception:  # pragma: no cover
            rel = path
        return rel if len(rel) <= max_len else f"…\\{p.name}"

    def _load_settings_into_ui(self) -> None:
        s = self.settings
        s.normalize()
        self.mode = s.mode
        self.region = tuple(s.region) if s.region_mode == "custom" and any(s.region) else None
        self.mode_segment.set_active(s.mode)
        self.var_x.set(str(s.x))
        self.var_y.set(str(s.y))
        self.var_button.set(_BUTTON_LABELS_REV.get(s.button, "左键"))
        self.var_click_type.set(_CLICK_TYPES[1] if s.double_click else _CLICK_TYPES[0])
        self.var_interval.set(str(s.interval_ms))
        self.var_random.set(bool(s.random_interval))
        self.var_min.set(str(s.interval_min_ms))
        self.var_max.set(str(s.interval_max_ms))
        self.var_count.set(str(s.count_limit))
        self.var_delay.set(f"{s.start_delay_ms / 1000:.1f}")
        self.var_restore.set(bool(s.restore_cursor))
        self.var_template.set(self._short_path(s.template_path) if s.template_path else "")
        self.var_region_mode.set(s.region_mode if s.region_mode in ("full", "custom") else "full")
        self.var_threshold.set(str(s.threshold))
        self.var_poll.set(str(s.poll_ms))
        self.var_offset_x.set(str(s.click_offset_x))
        self.var_offset_y.set(str(s.click_offset_y))
        # AI 答题
        self.ai_region = tuple(s.ai_region) if any(s.ai_region) else None
        self.var_ai_base_url.set(s.ai_base_url)
        self.var_ai_model.set(s.ai_model)
        self.var_ai_api_key.set(s.ai_api_key)
        self.var_ai_proxy.set(s.ai_proxy)
        self.var_ai_timeout.set(str(s.ai_timeout_s))
        self.var_ai_enable_thinking.set(bool(s.ai_enable_thinking))
        self.var_ai_option_wait.set(str(s.ai_option_wait_ms))
        self.var_ai_next_wait.set(str(s.ai_next_wait_ms))
        self.var_ai_count.set(str(s.ai_count_limit))
        self.var_ai_delay.set(f"{s.ai_delay_ms / 1000:.1f}")
        self.var_ai_monitor.set(bool(s.ai_monitor))
        self.var_ai_auto_next.set(bool(s.ai_auto_next))
        self.var_ai_realtime_layout.set(bool(s.ai_realtime_layout))
        self.var_ai_page_mode.set(True)
        self.var_ai_fullscreen.set(False)
        self.var_ai_auto_scroll.set(False)
        self.var_ai_tile_mode.set(bool(s.ai_tile_mode))
        self.var_ai_scroll_steps.set(str(s.ai_scroll_steps))
        self.var_ai_scroll_wait.set(str(s.ai_scroll_wait_ms))
        self.var_ai_scroll_max.set(str(s.ai_scroll_max))
        self.var_ai_fill_typing.set(bool(s.ai_fill_typing))
        self.var_ai_fill_overwrite.set(True)
        self.var_ai_fill_offset_y.set(str(s.ai_fill_offset_y))
        self.var_ai_poll.set(str(s.ai_poll_ms))
        self.var_show_ai_region.set(bool(s.ai_show_region))
        self.var_show_search_region.set(bool(s.show_region_box))
        for letter, (var_x, var_y) in self.var_ai_opt.items():
            point = s.ai_options.get(letter)
            if point:
                var_x.set(str(point[0]))
                var_y.set(str(point[1]))
            else:
                var_x.set("")
                var_y.set("")
        self._sync_auto_next_dependencies()
        if any(s.ai_next_point):
            self.var_ai_next_x.set(str(s.ai_next_point[0]))
            self.var_ai_next_y.set(str(s.ai_next_point[1]))
        else:
            self.var_ai_next_x.set("")
            self.var_ai_next_y.set("")
        self._update_ai_region_label()
        self.var_topmost.set(bool(s.topmost))
        self.root.attributes("-topmost", bool(s.topmost))
        self._update_region_label()
        self._on_random_toggle()
        self._sync_region_huds()
        if not getattr(self, "advanced_visible", False):
            # 简洁窗口只提供 AI 相关入口，高级设置默认展示 AI 答题参数
            self.mode = "ai"
            try:
                self.mode_segment.set_active("ai")
            except tk.TclError:  # pragma: no cover
                pass
        if s.ai_fullscreen:
            self.btn_ai_region.set_enabled(False)
        if s.template_path:
            self.root.after(120, lambda: self._try_load_saved_template(s.template_path))

    def _try_load_saved_template(self, path: str) -> None:
        if not path:
            return
        try:
            template = load_template(path, self.root)
        except Exception as exc:  # noqa: BLE001
            self._log(f"上次使用的模板加载失败：{exc}", "warn")
            self.var_template.set("")
            return
        self.template = template
        self._refresh_template_thumb()
        self._log(f"已加载上次使用的模板：{template.display_name()}", "muted")

    def _collect_settings(self) -> Settings:
        s = self.settings
        s.mode = getattr(self, "mode", s.mode)
        s.x = self._safe_int(self.var_x.get(), s.x)
        s.y = self._safe_int(self.var_y.get(), s.y)
        s.button = _BUTTON_LABELS.get(self.var_button.get(), s.button)
        s.double_click = self.var_click_type.get() == _CLICK_TYPES[1]
        s.interval_ms = self._safe_int(self.var_interval.get(), s.interval_ms)
        s.random_interval = bool(self.var_random.get())
        s.interval_min_ms = self._safe_int(self.var_min.get(), s.interval_min_ms)
        s.interval_max_ms = self._safe_int(self.var_max.get(), s.interval_max_ms)
        s.count_limit = self._safe_int(self.var_count.get(), s.count_limit)
        try:
            s.start_delay_ms = int(round(float(self.var_delay.get().strip()) * 1000))
        except (TypeError, ValueError):
            pass
        s.restore_cursor = bool(self.var_restore.get())
        s.template_path = self.template.path if self.template is not None else s.template_path
        s.region_mode = self.var_region_mode.get() if self.region else "full"
        s.region = list(self.region) if self.region else [0, 0, 0, 0]
        s.threshold = self._safe_int(self.var_threshold.get(), s.threshold, 50, 100)
        s.poll_ms = self._safe_int(self.var_poll.get(), s.poll_ms, 50)
        s.click_offset_x = self._safe_int(self.var_offset_x.get(), s.click_offset_x)
        s.click_offset_y = self._safe_int(self.var_offset_y.get(), s.click_offset_y)
        # AI 答题
        s.ai_base_url = self.var_ai_base_url.get().strip()
        s.ai_api_key = self.var_ai_api_key.get().strip()
        s.ai_model = self.var_ai_model.get().strip()
        s.ai_proxy = self.var_ai_proxy.get().strip()
        s.ai_timeout_s = self._safe_int(self.var_ai_timeout.get(), s.ai_timeout_s, 5, 300)
        s.ai_enable_thinking = bool(self.var_ai_enable_thinking.get())
        s.ai_option_wait_ms = self._safe_int(
            self.var_ai_option_wait.get(), s.ai_option_wait_ms, 0
        )
        s.ai_next_wait_ms = self._safe_int(
            self.var_ai_next_wait.get(), s.ai_next_wait_ms, 100
        )
        s.ai_count_limit = self._safe_int(self.var_ai_count.get(), s.ai_count_limit, 0)
        s.ai_monitor = bool(self.var_ai_monitor.get())
        s.ai_auto_next = bool(self.var_ai_auto_next.get())
        s.ai_realtime_layout = bool(self.var_ai_realtime_layout.get())
        s.ai_page_mode = bool(self.var_ai_page_mode.get())
        s.ai_fullscreen = False
        s.ai_auto_scroll = bool(self.var_ai_auto_scroll.get())
        s.ai_tile_mode = bool(self.var_ai_tile_mode.get())
        s.ai_scroll_steps = self._safe_int(self.var_ai_scroll_steps.get(), 3, 1)
        s.ai_scroll_wait_ms = self._safe_int(self.var_ai_scroll_wait.get(), 400, 0)
        s.ai_scroll_max = self._safe_int(self.var_ai_scroll_max.get(), 0, 0)
        s.ai_fill_typing = bool(self.var_ai_fill_typing.get())
        s.ai_fill_overwrite = bool(self.var_ai_fill_overwrite.get())
        s.ai_fill_offset_y = self._safe_int(self.var_ai_fill_offset_y.get(), 0)
        s.ai_poll_ms = self._safe_int(self.var_ai_poll.get(), s.ai_poll_ms, 100)
        s.ai_show_region = bool(self.var_show_ai_region.get())
        s.show_region_box = bool(self.var_show_search_region.get())
        try:
            s.ai_delay_ms = int(round(float(self.var_ai_delay.get().strip()) * 1000))
        except (TypeError, ValueError):
            pass
        s.ai_region = list(self.ai_region) if self.ai_region else [0, 0, 0, 0]
        ai_options: dict[str, list[int]] = {}
        for letter, (var_x, var_y) in getattr(self, "var_ai_opt", {}).items():
            try:
                x = int(float(var_x.get().strip()))
                y = int(float(var_y.get().strip()))
            except (TypeError, ValueError):
                continue
            ai_options[letter] = [x, y]
        s.ai_options = ai_options
        try:
            nx = int(float(self.var_ai_next_x.get().strip()))
            ny = int(float(self.var_ai_next_y.get().strip()))
            s.ai_next_point = [nx, ny]
        except (TypeError, ValueError):
            s.ai_next_point = [0, 0]
        s.topmost = bool(self.var_topmost.get())
        s.window_geometry = ""
        s.normalize()
        return s

    @staticmethod
    def _safe_int(text: str, default: int, minimum: int | None = None,
                  maximum: int | None = None) -> int:
        try:
            value = int(float(str(text).strip()))
        except (TypeError, ValueError):
            return default
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    def _save_settings(self, quiet: bool = True) -> None:
        try:
            self._collect_settings()
        except Exception:
            pass
        if save_settings(self.settings):
            if not quiet:
                self._log("设置已保存", "muted")
        elif not quiet:
            self._log("设置保存失败（目录可能没有写入权限）", "warn")

    # ------------------------------------------------------------------
    # 状态与统计
    # ------------------------------------------------------------------
    def _update_screen_info(self) -> None:
        try:
            vx, vy, vw, vh = winapi.virtual_screen()
            scale_text = f"{self.scale * 100:.0f}%"
            device = self.controller.display_name
            label = getattr(self, "screen_info", None)
            if label is not None:
                label.configure(
                    text=f"屏幕 {vw}×{vh} · 缩放 {scale_text} · 后端 {device}"
                )
        except Exception:  # pragma: no cover
            label = getattr(self, "screen_info", None)
            if label is not None:
                label.configure(text="")

    def _set_status(self, key: str) -> None:
        text, color = _STATE_STYLE.get(key, ("待机", PALETTE["muted"]))
        self.state = key
        try:
            self.status_pill.set(text, color)
        except tk.TclError:  # pragma: no cover
            pass

    def _set_controls_running(self, running: bool) -> None:
        self.btn_start.set_enabled(not running)
        self.btn_pause.set_enabled(running)
        self.btn_stop.set_enabled(running)
        for btn in (
            getattr(self, "btn_pick", None),
            getattr(self, "btn_choose_template", None),
            getattr(self, "btn_capture_template", None),
            getattr(self, "btn_region", None),
            getattr(self, "btn_test_match", None),
            getattr(self, "btn_ai_region", None),
            getattr(self, "btn_ai_test", None),
            getattr(self, "btn_ai_detect", None),
            getattr(self, "btn_quick", None),
            getattr(self, "btn_userscript", None),
            getattr(self, "btn_simple_quick", None),
            getattr(self, "btn_simple_answers", None),
            getattr(self, "btn_simple_userscript", None),
            getattr(self, "btn_simple_typing", None),
        ):
            if btn is not None:
                btn.set_enabled(not running)
        if (
            getattr(self, "var_ai_fullscreen", None) is not None
            and self.var_ai_fullscreen.get()
        ):
            self.btn_ai_region.set_enabled(False)

    def _update_stats(self, clicks: int, elapsed: float, score: float | None) -> None:
        self.tile_clicks.set_value(str(int(clicks)))
        total = int(elapsed)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        text = f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        self.tile_time.set_value(text)
        if score is None and getattr(self, "mode", "fixed") == "ai":
            # AI 模式下保留最近一次 AI 答案，不被状态刷新覆盖
            return
        if score is None:
            self.tile_score.set_value("-")
            self.tile_score.set_accent(PALETTE["dim"])
        else:
            self.tile_score.set_value(f"{score * 100:.1f}%")
            self.tile_score.set_accent(
                PALETTE["success"] if score >= 0.9 else PALETTE["warning"]
            )

    def _update_clock(self) -> None:
        if self.controller.is_running and self._started_at:
            elapsed = time.perf_counter() - self._started_at
            self.tile_time.set_value(self._format_elapsed(elapsed))
        self.root.after(500, self._update_clock)

    @staticmethod
    def _format_elapsed(elapsed: float) -> str:
        total = max(0, int(elapsed))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    # ------------------------------------------------------------------
    # 日志与事件
    # ------------------------------------------------------------------
    def _log(self, message: str, level: str = "info") -> None:
        timestamp = time.strftime("%H:%M:%S")
        try:
            self.log_view.append(f"[{timestamp}] {message}", level)
        except Exception:  # pragma: no cover
            print(f"[{timestamp}] {message}")

    def _show_actionable_error(self, title: str, error: str, context: str = "") -> None:
        from . import error_hints

        error_hints.show_error(self.root, title, str(error), context)

    def _on_clear_log(self) -> None:
        try:
            self.log_view.clear()
        except Exception:  # pragma: no cover
            pass

    def _drain_events(self) -> None:
        try:
            for _ in range(300):
                event = self._queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        except Exception:  # pragma: no cover
            pass
        finally:
            try:
                self.root.after(60, self._drain_events)
            except tk.TclError:
                pass

    def _handle_event(self, event: dict) -> None:
        kind = event.get("kind")
        if kind == "log":
            self._log(str(event.get("message", "")), str(event.get("level", "info")))
        elif kind == "status":
            self._apply_status_event(event)
        elif kind == "tick":
            self._last_score = event.get("score")
            self._update_stats(
                int(event.get("clicks", 0)),
                float(event.get("elapsed", 0.0)),
                self._last_score,
            )
        elif kind == "finished":
            self._on_engine_finished(event)
        elif kind == "hotkey":
            self._on_hotkey(int(event.get("id", 0)))
        elif kind == "ai_result":
            letter = str(event.get("letter", "") or "-")
            self._last_ai_raw = str(event.get("raw", ""))
            try:
                self.tile_score.set_value(letter)
                self.tile_score.set_accent(PALETTE["success"])
            except tk.TclError:  # pragma: no cover
                pass
        elif kind == "ai_test_done":
            self._on_ai_test_done(event)
        elif kind == "ai_detect_done":
            self._on_ai_detect_done(event)
        elif kind == "one_shot_done":
            self._on_one_shot_done(event)
        elif kind == "one_shot_error":
            self._on_one_shot_error(event)
        elif kind == "fill_done":
            self._on_fill_done(event)
        elif kind == "fill_error":
            self._on_fill_error(event)
        elif kind == "answer_scan_done":
            self._on_answer_scan_done(event)
        elif kind == "answer_scan_error":
            self._on_answer_scan_error(event)
        elif kind == "auto_answer_done":
            self._on_auto_answer_done(event)

    def _apply_status_event(self, event: dict) -> None:
        state = str(event.get("state", "idle"))
        if state in ("starting", "running", "paused", "idle"):
            self._set_status(state)
        if state in ("starting", "running"):
            self._set_controls_running(True)
            if state == "running":
                self._started_at = time.perf_counter() - float(event.get("elapsed", 0.0))
            self.btn_pause.set_text("暂停  (F7)")
        elif state == "paused":
            self._set_controls_running(True)
            self.btn_pause.set_text("继续  (F7)")
        else:
            self._set_controls_running(False)
            self.btn_pause.set_text("暂停  (F7)")
        clicks = int(event.get("clicks", 0))
        elapsed = float(event.get("elapsed", 0.0))
        score = event.get("score")
        self._last_score = score
        self._update_stats(clicks, elapsed, score)

    def _on_engine_finished(self, event: dict) -> None:
        # 连续任务运行期间窗口保持最小化，不自动弹出；需要查看日志时手动点任务栏。
        self._restore_after_run = False
        reason = str(event.get("reason", "stopped"))
        clicks = int(event.get("clicks", 0))
        elapsed = float(event.get("elapsed", 0.0))
        self._started_at = 0.0
        try:
            self.controller.stop()
        except Exception:  # pragma: no cover
            pass
        self._set_controls_running(False)
        self._set_status("idle")
        self.btn_pause.set_text("暂停  (F7)")
        self._update_stats(clicks, elapsed, self._last_score)
        message = _LOG_REASON.get(reason, "任务结束")
        level = "success" if reason in ("limit", "scroll_end") else (
            "error" if reason == "error" else "muted"
        )
        self._log(f"{message}（点击 {clicks} 次，用时 {self._format_elapsed(elapsed)}）", level)

    def _restore_window_after_run(self) -> None:
        if getattr(self, "_restore_after_run", False):
            self._restore_after_run = False
            try:
                self.root.deiconify()
                self.root.lift()
                self.root.attributes("-topmost", bool(self.var_topmost.get()))
            except tk.TclError:  # pragma: no cover
                pass
            self._log("已恢复主窗口", "muted")

    def _hide_main_for_picker(self) -> None:
        """框选区域时暂时隐藏主窗口，避免主窗口挡住所选页面。"""
        try:
            state = self.root.state()
        except tk.TclError:  # pragma: no cover
            state = "normal"
        self._picker_root_was_visible = state not in ("withdrawn", "iconic")
        if self._picker_root_was_visible:
            try:
                self.root.withdraw()
                # 给 DWM 一点时间把窗口真正从桌面移除；否则框选器马上截屏，
                # 会把刚才的主窗口残影一起截进去，看起来像“窗口又弹回来”。
                self.root.update_idletasks()
                self.root.update()
                time.sleep(0.22)
                self.root.update_idletasks()
                self.root.update()
            except tk.TclError:  # pragma: no cover
                pass

    def _restore_main_after_picker(self) -> None:
        if getattr(self, "_picker_root_was_visible", False):
            self._picker_root_was_visible = False
            try:
                self.root.deiconify()
                self.root.lift()
            except tk.TclError:  # pragma: no cover
                pass

    def _minimize_for_single_shot(self) -> None:
        """单次答题/填空执行期间最小化，完成后自动恢复。"""
        try:
            state = self.root.state()
        except tk.TclError:  # pragma: no cover
            state = "normal"
        self._single_shot_was_visible = state not in ("withdrawn", "iconic")
        try:
            self.root.iconify()
        except tk.TclError:  # pragma: no cover
            pass

    def _restore_after_single_shot(self) -> None:
        if getattr(self, "_single_shot_was_visible", False):
            self._single_shot_was_visible = False
            try:
                self.root.deiconify()
                self.root.lift()
            except tk.TclError:  # pragma: no cover
                pass
            self._log("单次任务完成，已恢复主窗口", "muted")

    def _minimize_for_run(self) -> None:
        """连续任务开始后最小化，运行/结束都不自动弹出。"""
        try:
            self.root.iconify()
        except tk.TclError:  # pragma: no cover
            pass

    def _restore_main_window(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", bool(self.var_topmost.get()))
        except tk.TclError:  # pragma: no cover
            pass

    def _on_hotkey(self, hotkey_id: int) -> None:
        if hotkey_id == HK_START:
            self._log("收到热键 F6：单次答题", "accent")
            self._on_one_shot_answer()
        elif hotkey_id == HK_PAUSE:
            self._log("收到热键 F7：单次填空", "accent")
            self._on_one_shot_fill()
        elif hotkey_id == HK_STOP:
            popup = self._answer_popup
            if popup is not None and popup.winfo_exists() and popup.winfo_viewable():
                self._log("收到热键 F8：再次识别", "accent")
                self._on_rescan_answer()
            else:
                self._log("收到热键 F8：AI 看答案", "accent")
                self._on_ai_show_answers()
        elif hotkey_id == HK_PICK:
            if not self.controller.is_running:
                self._log("收到热键 F9：拾取坐标", "accent")
                self._on_pick_coord()

    def _start_hotkeys(self) -> None:
        if not self.settings.hotkeys_enabled:
            return
        try:
            self.hotkey_manager = HotkeyManager(
                lambda hotkey_id: self._emit_event({"kind": "hotkey", "id": hotkey_id})
            )
            self.hotkey_manager.start_and_wait(1.2)
            for error in self.hotkey_manager.errors:
                self._log(error, "warn")
        except Exception as exc:  # noqa: BLE001
            self._log(f"全局热键不可用：{exc}", "warn")
            self.hotkey_manager = None

    # ------------------------------------------------------------------
    # 模式 / 区域 / 模板
    # ------------------------------------------------------------------
    def _on_mode_change(self, key: str) -> None:
        if self.controller.is_running:
            self._log("运行中不能切换模式，请先停止任务", "warn")
            self.mode_segment.set_active(getattr(self, "mode", "fixed"))
            return
        self.mode = key
        self.settings.mode = key
        self._apply_mode_visibility()
        name = {"fixed": "定点连点", "image": "找图点击", "ai": "AI 答题"}.get(key, key)
        self._log(f"已切换到{name}模式", "muted")

    def _refresh_mode_tile(self) -> None:
        mode = getattr(self, "mode", "fixed")
        try:
            if mode == "ai":
                self.tile_score.set_title("AI 答案")
                self.tile_score.set_value("-")
                self.tile_score.set_accent(PALETTE["dim"])
            else:
                self.tile_score.set_title("匹配相似度")
                self.tile_score.set_value("-")
                self.tile_score.set_accent(PALETTE["dim"])
        except (tk.TclError, AttributeError):  # pragma: no cover
            pass

    def _apply_mode_visibility(self) -> None:
        mode = getattr(self, "mode", "fixed")
        for card in (self.fixed_card, self.image_card, self.ai_card, self.ai_config_card):
            card.pack_forget()
        if not getattr(self, "advanced_visible", False):
            self._refresh_mode_tile()
            self._sync_region_huds()
            return
        if mode == "image":
            self.image_card.pack(fill="x", pady=(0, self.px(12)))
        elif mode == "ai":
            self.ai_config_card.pack(fill="x", pady=(0, self.px(12)))
            if getattr(self, "more_options_visible", False):
                self.ai_card.pack(fill="x", pady=(0, self.px(12)))
        else:
            self.fixed_card.pack(fill="x", pady=(0, self.px(12)))
        self.scroll.scroll_to_top()
        self._refresh_mode_tile()
        self._sync_region_huds()

    def _sync_auto_next_dependencies(self, log: bool = False) -> None:
        """整页多题已内置；自动滚屏已删除，保持自动点下一题可用。"""
        self.var_ai_page_mode.set(True)
        self.var_ai_auto_scroll.set(False)

    def _on_auto_next_toggle(self) -> None:
        self._sync_auto_next_dependencies(log=True)

    def _on_page_mode_toggle(self) -> None:
        """勾选整页多题时自动打开实时监控（该模式依赖监控画面变化）。"""
        if self.var_ai_page_mode.get() and not self.var_ai_monitor.get():
            self.var_ai_monitor.set(True)
        if self.var_ai_page_mode.get() and self.var_ai_auto_next.get():
            self.var_ai_auto_next.set(False)
            self._sync_auto_next_dependencies()
        self._sync_auto_next_dependencies()

    def _on_auto_scroll_toggle(self) -> None:
        """自动滚屏依赖整页多题 + 实时监控。"""
        if self.var_ai_auto_scroll.get():
            self.var_ai_monitor.set(True)
            self.var_ai_page_mode.set(True)
            self.var_ai_auto_next.set(False)
            self._log(
                "自动滚屏答题已开启：从当前页开始向下滚动，逐屏识别并作答；"
                "滚到底部会自动停止",                "info",
            )
        else:
            self._log("已关闭自动滚屏答题", "muted")
        self._sync_auto_next_dependencies()

    def _on_fullscreen_toggle(self) -> None:
        fullscreen = bool(self.var_ai_fullscreen.get())
        try:
            if fullscreen:
                vx, vy, vw, vh = winapi.virtual_screen()
                self.ai_region_label.configure(
                    text=f"全屏 {vw}×{vh}", fg=PALETTE["cyan"]
                )
                self.btn_ai_region.set_enabled(False)
                self._log("已开启全屏识别：截取范围改为整个屏幕", "info")
            else:
                self.btn_ai_region.set_enabled(True)
                self._update_ai_region_label()
                self._log("已关闭全屏识别：使用框选的题目区域", "muted")
        except tk.TclError:  # pragma: no cover
            pass
        self._sync_region_huds()

    def _on_random_toggle(self) -> None:
        enabled = bool(self.var_random.get())
        for entry in (getattr(self, "entry_min", None), getattr(self, "entry_max", None)):
            if entry is None:
                continue
            try:
                entry.configure(
                    state="normal" if enabled else "disabled",
                    disabledbackground=PALETTE["bg2"],
                    disabledforeground=PALETTE["dim"],
                )
            except tk.TclError:
                pass

    def _on_region_mode_change(self) -> None:
        mode = self.var_region_mode.get()
        if mode == "full":
            self.region = None
        self._update_region_label()
        self._sync_region_huds()

    def _on_toggle_region_huds(self) -> None:
        mode = getattr(self, "mode", "fixed")
        if mode == "ai" and self.var_show_ai_region.get() and self.ai_region is None:
            self._log("请先框选题目区域，再勾选「显示框」", "warn")
        if (
            mode == "image"
            and self.var_show_search_region.get()
            and (self.region is None or self.var_region_mode.get() != "custom")
        ):
            self._log("请先框选自定义搜索区域，再勾选「显示框」", "warn")
        self._sync_region_huds()

    def _hide_ai_hud_after_once(self) -> None:
        """单次答题/填空完成后隐藏选区框，避免残留在屏幕上。"""
        try:
            self.var_show_ai_region.set(False)
        except tk.TclError:  # pragma: no cover
            pass
        try:
            if self._hud_ai is not None:
                self._hud_ai.hide()
        except (tk.TclError, AttributeError):  # pragma: no cover
            pass

    def _sync_region_huds(self) -> None:
        """按当前模式与勾选状态显示/隐藏屏幕上的选区框。"""
        mode = getattr(self, "mode", "fixed")
        try:
            want_ai = (
                mode == "ai"
                and bool(self.var_show_ai_region.get())
                and not bool(self.var_ai_fullscreen.get())
                and self.ai_region is not None
            )
            if want_ai:
                if self._hud_ai is None:
                    self._hud_ai = RegionHUD(self.root)
                self._hud_ai.show(self.ai_region, "AI 题目区域", PALETTE["cyan"])
            elif self._hud_ai is not None:
                self._hud_ai.hide()

            want_search = (
                mode == "image"
                and bool(self.var_show_search_region.get())
                and self.region is not None
                and self.var_region_mode.get() == "custom"
            )
            if want_search:
                if self._hud_search is None:
                    self._hud_search = RegionHUD(self.root)
                self._hud_search.show(self.region, "搜索区域", PALETTE["accent"])
            elif self._hud_search is not None:
                self._hud_search.hide()
        except Exception as exc:  # noqa: BLE001
            self._log(f"显示选区框失败：{exc}", "warn")

    def _update_region_label(self) -> None:
        if self.region:
            x, y, w, h = self.region
            self.region_label.configure(text=f"({x}, {y})  {w}×{h}")
        else:
            self.region_label.configure(text="全屏")

    def _on_pick_coord(self) -> None:
        if self.controller.is_running or self._picker_active:
            return
        self._picker_active = True

        def on_done(x: int, y: int) -> None:
            self._picker_active = False
            self._apply_picked_coord(x, y)

        def on_cancel() -> None:
            self._picker_active = False
            self._log("已取消坐标拾取", "muted")

        try:
            CoordinatePicker(self.root, on_done=on_done, on_cancel=on_cancel)
        except Exception as exc:  # noqa: BLE001
            self._picker_active = False
            self._log(f"打开坐标拾取器失败：{exc}", "error")

    def _apply_picked_coord(self, x: int, y: int) -> None:
        self.var_x.set(str(x))
        self.var_y.set(str(y))
        self._log(f"已拾取坐标：({x}, {y})", "success")

    def _on_pick_region(self) -> None:
        if self.controller.is_running or self._picker_active:
            return
        self._picker_active = True
        self._log("请在屏幕上拖动鼠标框选搜索区域…", "info")

        def on_done(region: tuple[int, int, int, int]) -> None:
            self._picker_active = False
            self._apply_picked_region(region)

        def on_cancel() -> None:
            self._picker_active = False
            self._log("已取消区域框选", "muted")

        try:
            RegionPicker(
                self.root,
                on_done=on_done,
                on_cancel=on_cancel,
                hint="拖动框选找图点击的搜索区域",
            )
        except Exception as exc:  # noqa: BLE001
            self._picker_active = False
            self._log(f"打开区域框选器失败：{exc}", "error")

    def _apply_picked_region(self, region: tuple[int, int, int, int]) -> None:
        self.region = region
        self.var_region_mode.set("custom")
        self._update_region_label()
        self._sync_region_huds()
        self._log(
            f"已设置搜索区域：({region[0]}, {region[1]})  {region[2]}×{region[3]}", "success"
        )

    # ------------------------------------------------------------------
    # AI 答题相关
    # ------------------------------------------------------------------
    def _update_ai_region_label(self) -> None:
        if getattr(self, "var_ai_fullscreen", None) is not None and self.var_ai_fullscreen.get():
            try:
                _vx, _vy, vw, vh = winapi.virtual_screen()
                self.ai_region_label.configure(text=f"全屏 {vw}×{vh}", fg=PALETTE["cyan"])
            except tk.TclError:  # pragma: no cover
                pass
            return
        if self.ai_region:
            x, y, w, h = self.ai_region
            self.ai_region_label.configure(text=f"({x}, {y})  {w}×{h}", fg=PALETTE["cyan"])
        else:
            self.ai_region_label.configure(text="未选择", fg=PALETTE["dim"])

    def _on_pick_ai_region(self) -> None:
        if self.controller.is_running or self._picker_active:
            return
        if self.var_ai_fullscreen.get():
            self.var_ai_fullscreen.set(False)
            self.btn_ai_region.set_enabled(True)
        self._picker_active = True
        self._log("请在屏幕上框选题目区域（包含题干和全部选项）…", "info")

        def on_done(region: tuple[int, int, int, int]) -> None:
            self._picker_active = False
            self._restore_main_after_picker()
            self.ai_region = region
            self._update_ai_region_label()
            self._sync_region_huds()
            self._log(
                f"已设置题目区域：({region[0]}, {region[1]})  {region[2]}×{region[3]}",
                "success",
            )

        def on_cancel() -> None:
            self._picker_active = False
            self._restore_main_after_picker()
            self._log("已取消题目区域框选", "muted")

        try:
            self._hide_main_for_picker()
            RegionPicker(
                self.root,
                on_done=on_done,
                on_cancel=on_cancel,
                hint="拖动框选题目区域（题干 + 全部选项）",
            )
        except Exception as exc:  # noqa: BLE001
            self._picker_active = False
            self._restore_main_after_picker()
            self._log(f"打开题目区域框选失败：{exc}", "error")

    def _pick_ai_option(self, letter: str) -> None:
        if self.controller.is_running or self._picker_active:
            return
        self._picker_active = True
        self._log(f"请把鼠标移到选项 {letter} 的中心位置后点击确认…", "info")

        def on_done(x: int, y: int) -> None:
            self._picker_active = False
            var_x, var_y = self.var_ai_opt[letter]
            var_x.set(str(x))
            var_y.set(str(y))
            self._log(f"选项 {letter} 坐标：({x}, {y})", "success")

        def on_cancel() -> None:
            self._picker_active = False
            self._log(f"已取消选项 {letter} 坐标拾取", "muted")

        try:
            CoordinatePicker(self.root, on_done=on_done, on_cancel=on_cancel)
        except Exception as exc:  # noqa: BLE001
            self._picker_active = False
            self._log(f"打开坐标拾取器失败：{exc}", "error")

    def _pick_ai_next(self) -> None:
        if self.controller.is_running or self._picker_active:
            return
        self._picker_active = True
        self._log("请把鼠标移到「下一题」按钮中心位置后点击确认…", "info")

        def on_done(x: int, y: int) -> None:
            self._picker_active = False
            self.var_ai_next_x.set(str(x))
            self.var_ai_next_y.set(str(y))
            self._log(f"下一题坐标：({x}, {y})", "success")

        def on_cancel() -> None:
            self._picker_active = False
            self._log("已取消下一题坐标拾取", "muted")

        try:
            CoordinatePicker(self.root, on_done=on_done, on_cancel=on_cancel)
        except Exception as exc:  # noqa: BLE001
            self._picker_active = False
            self._log(f"打开坐标拾取器失败：{exc}", "error")

    def _build_ai_config(self) -> tuple[AIConfig | None, str]:
        config = AIConfig(
            base_url=self.var_ai_base_url.get().strip(),
            api_key=self.var_ai_api_key.get().strip(),
            model=self.var_ai_model.get().strip(),
            timeout_s=float(self._safe_int(self.var_ai_timeout.get(), 60, 5, 300)),
            temperature=0.0,
            proxy=self.var_ai_proxy.get().strip(),
            enable_thinking=bool(self.var_ai_enable_thinking.get()),
        )
        ok, message = ai_client.validate_config(config)
        if not ok:
            return None, message
        return config, ""

    def _collect_ai_option_points(self) -> dict[str, tuple[int, int]]:
        points: dict[str, tuple[int, int]] = {}
        for letter, (var_x, var_y) in self.var_ai_opt.items():
            sx = var_x.get().strip()
            sy = var_y.get().strip()
            if not sx or not sy:
                continue
            try:
                points[letter] = (int(float(sx)), int(float(sy)))
            except (TypeError, ValueError):
                continue
        return points

    def _collect_ai_next_point(self) -> tuple[int, int] | None:
        sx = self.var_ai_next_x.get().strip()
        sy = self.var_ai_next_y.get().strip()
        if not sx or not sy:
            return None
        try:
            return int(float(sx)), int(float(sy))
        except (TypeError, ValueError):
            return None

    def _on_one_shot_answer(self) -> None:
        """单次答题：框选一次 → 自动识别并点击，不启动实时监控。"""
        if self._auto_answer_running:
            self._log("开始答题正在运行，请先点「停止」", "warn")
            return
        if self.controller.is_running:
            self._log("请先停止任务再做单次答题", "warn")
            return
        if self._one_shot_busy or self._fill_busy or self._picker_active or self._ai_testing:
            return
        self._picker_active = True
        self._log("单次答题：请在屏幕上拖出矩形框，选中要作答的题目区域…", "info")

        def on_done(region: tuple[int, int, int, int]) -> None:
            self._picker_active = False
            self._restore_main_after_picker()
            self.ai_region = region
            self._update_ai_region_label()
            self._sync_region_huds()
            self._hide_ai_hud_after_once()
            self._run_one_shot(region)

        def on_cancel() -> None:
            self._picker_active = False
            self._restore_main_after_picker()
            self._log("已取消单次答题框选", "muted")

        try:
            self._hide_main_for_picker()
            RegionPicker(
                self.root,
                on_done=on_done,
                on_cancel=on_cancel,
                hint="拖出矩形框选题目区域；松开鼠标后会自动识别并点击答案",
            )
        except Exception as exc:  # noqa: BLE001
            self._picker_active = False
            self._restore_main_after_picker()
            self._log(f"打开框选器失败：{exc}", "error")

    def _run_one_shot(self, region: tuple[int, int, int, int]) -> None:
        ai_config, ai_error = self._build_ai_config()
        if ai_config is None:
            self._log(ai_error, "warn")
            messagebox.showwarning("AI 配置不完整", ai_error, parent=self.root)
            return
        page_mode = bool(self.var_ai_page_mode.get())
        realtime_layout = bool(self.var_ai_realtime_layout.get())
        options = self._collect_ai_option_points()
        next_point = self._collect_ai_next_point()
        if not page_mode and not realtime_layout and not options:
            messagebox.showwarning(
                "缺少选项坐标",
                "未开启「实时定位选项坐标」时需要先拾取选项坐标；建议保持实时定位开启。",
                parent=self.root,
            )
            return
        self._one_shot_busy = True
        self.btn_quick.set_enabled(False)
        self.btn_start.set_enabled(False)
        self.root.configure(cursor="watch")
        mode_text = "整页多题" if page_mode else "单题"
        self._log(f"单次答题（{mode_text}）：正在请求 AI 识别并自动点击…", "accent")
        self._minimize_for_single_shot()

        def worker() -> None:
            try:
                result = self.controller.answer_once(
                    region,
                    ai_config,
                    page_mode=page_mode,
                    realtime_layout=realtime_layout,
                    auto_next=bool(self.var_ai_auto_next.get()),
                    option_wait_ms=self._safe_int(self.var_ai_option_wait.get(), 400, 0),
                    next_wait_ms=self._safe_int(self.var_ai_next_wait.get(), 1500, 100),
                    fallback_options=options,
                    fallback_next=next_point,
                )
                self._emit_event({"kind": "one_shot_done", "result": result})
            except Exception as exc:  # noqa: BLE001
                self._emit_event({"kind": "one_shot_error", "error": str(exc)})

        threading.Thread(target=worker, daemon=True, name="one-shot-answer").start()

    def _finish_one_shot(self) -> None:
        self._one_shot_busy = False
        self.btn_quick.set_enabled(True)
        self.btn_start.set_enabled(True)
        try:
            self.root.configure(cursor="")
        except tk.TclError:  # pragma: no cover
            pass
        self._restore_after_single_shot()

    def _on_one_shot_done(self, event: dict) -> None:
        self._finish_one_shot()
        result = event.get("result") or {}
        questions = result.get("questions") or []
        answers = result.get("answers") or []
        for item in questions:
            letters = "、".join(item.get("letters") or [])
            multi = "（多选）" if len(item.get("letters") or []) > 1 else ""
            self._log(
                f"单次答题 · 第 {item.get('index', 1)} 题 AI 答案：{letters}{multi}，已点击选项",
                "success",
            )
        if not questions and answers:
            self._log(f"单次答题 AI 答案：{'、'.join(answers)}，已点击选项", "success")
        if answers:
            try:
                self.tile_score.set_value("、".join(answers))
                self.tile_score.set_accent(PALETTE["success"])
            except tk.TclError:  # pragma: no cover
                pass
        elapsed = float(result.get("elapsed_ms", 0.0)) / 1000.0
        next_text = "已点下一题" if result.get("next") else "未点下一题"
        self._log(
            f"单次答题完成：作答 {len(questions)} 道题，点击 {int(result.get('clicks', 0))} 次，"
            f"{next_text}（耗时 {elapsed:.1f} 秒）",
            "success",
        )
        self._log("需要再答一题：重新拖框，再点一次「单次答题」即可", "muted")

    def _on_one_shot_fill(self) -> None:
        """单次填空：框选一次 → AI 读取题号和答案 → 自动填入对应文本框。"""
        if self._auto_answer_running:
            self._log("开始答题正在运行，请先点「停止」", "warn")
            return
        if self.controller.is_running:
            self._log("请先停止任务再做单次填空", "warn")
            return
        if self._fill_busy or self._one_shot_busy or self._picker_active or self._ai_testing:
            return
        self._picker_active = True
        self._log("单次填空：请在屏幕上拖出矩形框，选中要填写的题目区域…", "info")

        def on_done(region: tuple[int, int, int, int]) -> None:
            self._picker_active = False
            self._restore_main_after_picker()
            self.ai_region = region
            self._update_ai_region_label()
            self._sync_region_huds()
            self._hide_ai_hud_after_once()
            self._run_fill(region)

        def on_cancel() -> None:
            self._picker_active = False
            self._restore_main_after_picker()
            self._log("已取消单次填空框选", "muted")

        try:
            self._hide_main_for_picker()
            RegionPicker(
                self.root,
                on_done=on_done,
                on_cancel=on_cancel,
                hint="拖出矩形框选填空题区域；松开鼠标后自动识别并填入答案",
            )
        except Exception as exc:  # noqa: BLE001
            self._picker_active = False
            self._restore_main_after_picker()
            self._log(f"打开框选器失败：{exc}", "error")

    def _run_fill(self, region: tuple[int, int, int, int]) -> None:
        ai_config, ai_error = self._build_ai_config()
        if ai_config is None:
            self._log(ai_error, "warn")
            messagebox.showwarning("AI 配置不完整", ai_error, parent=self.root)
            return
        next_point = self._collect_ai_next_point()
        self._fill_busy = True
        self.btn_quick.set_enabled(False)
        self.btn_start.set_enabled(False)
        self.root.configure(cursor="watch")
        self._log("单次填空：正在请求 AI 读取题目和答案…", "accent")
        self._minimize_for_single_shot()

        def worker() -> None:
            try:
                result = self.controller.fill_once(
                    region,
                    ai_config,
                    auto_next=bool(self.var_ai_auto_next.get()),
                    next_wait_ms=self._safe_int(self.var_ai_next_wait.get(), 1500, 100),
                    fill_wait_ms=max(
                        80, self._safe_int(self.var_ai_option_wait.get(), 400, 0)
                    ),
                    typing=bool(self.var_ai_fill_typing.get()),
                    overwrite=bool(self.var_ai_fill_overwrite.get()),
                    offset_y=self._safe_int(self.var_ai_fill_offset_y.get(), 0),
                    fallback_next=next_point,
                )
                self._emit_event({"kind": "fill_done", "result": result})
            except Exception as exc:  # noqa: BLE001
                self._emit_event({"kind": "fill_error", "error": str(exc)})

        threading.Thread(target=worker, daemon=True, name="one-shot-fill").start()

    def _finish_fill(self) -> None:
        self._fill_busy = False
        self.btn_quick.set_enabled(True)
        self.btn_start.set_enabled(True)
        try:
            self.root.configure(cursor="")
        except tk.TclError:  # pragma: no cover
            pass
        self._restore_after_single_shot()

    def _on_fill_done(self, event: dict) -> None:
        self._finish_fill()
        result = event.get("result") or {}
        items = result.get("items") or []
        skipped = result.get("skipped") or []
        for item in items:
            slot = int(item.get("slot") or 0)
            slot_text = f" 第 {slot} 空" if slot else ""
            self._log(
                f"单次填空 · 第 {item.get('index', 1)} 题{slot_text}："
                f"{item.get('answer', '')}（已填入文本框）",
                "success",
            )
        for item in skipped:
            slot = int(item.get("slot") or 0)
            slot_text = f" 第 {slot} 空" if slot else ""
            self._log(
                f"单次填空 · 第 {item.get('index', 1)} 题{slot_text}未填写："
                f"{item.get('reason', '')}",
                "warn",
            )
        elapsed = float(result.get("elapsed_ms", 0.0)) / 1000.0
        next_text = "已点下一题" if result.get("next") else "未点下一题"
        self._log(
            f"单次填空完成：填写 {len(items)} 处，跳过 {len(skipped)} 处，"
            f"{next_text}（耗时 {elapsed:.1f} 秒）",
            "success",
        )
        self._log("需要再填一页：重新拖框，再点一次「单次填空」即可", "muted")

    def _on_fill_error(self, event: dict) -> None:
        self._finish_fill()
        error = str(event.get("error", "未知错误"))
        self._log(f"单次填空失败：{error}", "error")
        self._show_actionable_error("单次填空失败", error, "单次填空")

    def _on_one_shot_error(self, event: dict) -> None:
        self._finish_one_shot()
        error = str(event.get("error", "未知错误"))
        self._log(f"单次答题失败：{error}", "error")
        self._show_actionable_error("单次答题失败", error, "单次答题")

    def _on_open_userscript(self) -> None:
        """打开油猴脚本文件，并提示安装/修改配置步骤。"""
        from pathlib import Path

        script = Path(__file__).resolve().parent.parent / "tools" / "tampermonkey_quiz.user.js"
        if not script.exists():
            self._log(f"未找到油猴脚本：{script}", "warn")
            messagebox.showwarning(
                "未找到脚本",
                f"未找到 {script}\n请确认解压后的 tools 文件夹完整。",
                parent=self.root,
            )
            return
        try:
            subprocess.Popen(["explorer", "/select,", str(script)])
        except Exception as exc:  # noqa: BLE001
            self._log(f"打开脚本文件失败：{exc}", "warn")
        self._log(
            "油猴网页文字答题：改脚本顶部 CONFIG（apiBase/apiKey/model），"
            "在 Tampermonkey 里安装后打开答题页，点右下角「文本答题」",
            "info",
        )
        messagebox.showinfo(
            "油猴网页文字答题（更快）",
            "已打开脚本文件：tools/tampermonkey_quiz.user.js\n\n"
            "1. 浏览器安装 Tampermonkey 扩展；\n"
            "2. 在 Tampermonkey 里新建脚本，把该文件内容粘贴进去（或直接拖入安装）；\n"
            "3. 修改脚本顶部 CONFIG 的 apiBase / apiKey / model（和软件里一致）；\n"
            "4. 打开你的答题页面，右下角点「文本答题」即可直接读网页文字作答。\n\n"
            "提示：如果识别不到题，请按 README 修改 CONFIG.selectors 里你平台的 class。",
            parent=self.root,
        )

    def _on_test_ai(self) -> None:
        if self.controller.is_running:
            self._log("请先停止任务再做 AI 识别测试", "warn")
            return
        if self._ai_testing:
            return
        if self.ai_region is None:
            messagebox.showwarning("缺少题目区域", "请先点击「框选题目区域」选择题目区域", parent=self.root)
            return
        ai_config, ai_error = self._build_ai_config()
        if ai_config is None:
            self._log(ai_error, "warn")
            messagebox.showwarning("AI 配置不完整", ai_error, parent=self.root)
            return
        self._ai_testing = True
        self.btn_ai_test.set_enabled(False)
        self.root.configure(cursor="watch")
        if self.var_ai_page_mode.get():
            self._log(
                "整页多题模式下，「测试识别」只验证接口连通性；整页作答请直接开始执行查看日志",
                "warn",
            )
        self._log(f"正在请求 AI 模型（{ai_config.model}）识别题目…", "accent")
        region = self.ai_region

        def worker() -> None:
            try:
                result = self.controller.test_ai(region, ai_config)
                self._emit_event({"kind": "ai_test_done", "result": result})
            except Exception as exc:  # noqa: BLE001
                self._emit_event({"kind": "ai_test_done", "result": None, "error": str(exc)})

        threading.Thread(target=worker, name="AITest", daemon=True).start()

    def _on_ai_test_done(self, event: dict) -> None:
        self._ai_testing = False
        self.btn_ai_test.set_enabled(True)
        try:
            self.root.configure(cursor="")
        except tk.TclError:  # pragma: no cover
            pass
        error = event.get("error")
        result = event.get("result")
        if error or not result:
            self._log(f"AI 识别失败：{error or '未知错误'}", "error")
            self._show_actionable_error("AI 识别失败", str(error or "未知错误"), "AI 识别")
            return
        answer = result.get("answer")
        if answer is None:
            self._log("AI 没有返回答案", "warn")
            return
        self._last_ai_raw = answer.raw
        display = "、".join(answer.all_letters())
        self._log(
            f"AI 答案：{display}（耗时 {answer.elapsed_ms / 1000:.1f} 秒）",
            "success",
        )
        if getattr(self, "mode", "") == "ai":
            try:
                self.tile_score.set_value(display)
                self.tile_score.set_accent(PALETTE["success"])
            except tk.TclError:  # pragma: no cover
                pass
        AIResultPreview(self.root, result, on_retry=self._on_test_ai)

    def _on_ai_detect(self) -> None:
        """让 AI 视觉模型自动识别选项与「下一题」的位置，自动填入坐标。"""
        if self.controller.is_running or self._ai_detecting or self._picker_active:
            return
        if self.ai_region is None:
            messagebox.showwarning(
                "缺少题目区域", "请先点击「框选题目区域」选择题目区域", parent=self.root
            )
            return
        ai_config, ai_error = self._build_ai_config()
        if ai_config is None:
            self._log(ai_error, "warn")
            messagebox.showwarning("AI 配置不完整", ai_error, parent=self.root)
            return
        self._ai_detecting = True
        self.btn_ai_detect.set_enabled(False)
        self.root.configure(cursor="watch")
        self._log("正在让 AI 识别选项位置（约需几秒）…", "accent")
        region = self.ai_region

        def worker() -> None:
            try:
                rgb, width, height = self.controller.capture_region(region)
                png_bytes = png.encode_png(width, height, rgb)
                layout = ai_client.detect_option_layout(png_bytes, ai_config)
                self._emit_event(
                    {
                        "kind": "ai_detect_done",
                        "result": {
                            "png": png_bytes,
                            "width": width,
                            "height": height,
                            "region": region,
                            "layout": layout,
                        },
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self._emit_event({"kind": "ai_detect_done", "result": None, "error": str(exc)})

        threading.Thread(target=worker, name="AIDetectLayout", daemon=True).start()

    def _on_ai_detect_done(self, event: dict) -> None:
        self._ai_detecting = False
        self.btn_ai_detect.set_enabled(True)
        try:
            self.root.configure(cursor="")
        except tk.TclError:  # pragma: no cover
            pass
        error = event.get("error")
        result = event.get("result")
        if error or not result:
            self._log(f"AI 识别选项位置失败：{error or '未知错误'}", "error")
            self._show_actionable_error(
                "AI 识别选项位置失败", str(error or "未知错误"), "AI 识别选项位置"
            )
            return
        layout = result["layout"]
        region = result["region"]
        x0, y0, width, height = region
        filled: list[str] = []
        for letter, (nx, ny) in layout.points.items():
            pair = self.var_ai_opt.get(letter)
            if pair is None:
                continue
            ax = int(round(x0 + nx * width))
            ay = int(round(y0 + ny * height))
            pair[0].set(str(ax))
            pair[1].set(str(ay))
            filled.append(letter)
        next_filled = False
        if layout.next_point is not None:
            nx, ny = layout.next_point
            self.var_ai_next_x.set(str(int(round(x0 + nx * width))))
            self.var_ai_next_y.set(str(int(round(y0 + ny * height))))
            next_filled = True
        desc = "、".join(filled) + (" · 下一题" if next_filled else "")
        self._log(
            f"实时扫描结果：{desc}，共 {len(filled)} 个选项"
            f"（耗时 {layout.elapsed_ms / 1000:.1f} 秒），请核对预览；"
            "如果页面有 E/F 却只扫到 A~D，请把选项区域框完整后重扫",
            "success",
        )
        OptionLayoutPreview(self.root, result, on_retry=self._on_ai_detect)

    def _on_choose_template(self) -> None:
        if self.controller.is_running:
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择模板图片（推荐 PNG）",
            filetypes=[("PNG 图片", "*.png"), ("BMP 图片", "*.bmp"), ("所有文件", "*.*")],
        )
        if not path:
            return
        if self._load_template_path(path):
            self.settings.template_path = path

    def _load_template_path(self, path: str) -> bool:
        try:
            template = load_template(path, self.root)
        except Exception as exc:  # noqa: BLE001
            self._log(f"模板加载失败：{exc}", "error")
            self._show_actionable_error("模板加载失败", str(exc), "模板加载")
            return False
        if template.width > 1600 or template.height > 1200:
            self._log("模板尺寸较大，匹配速度可能变慢", "warn")
        self.template = template
        self.var_template.set(self._short_path(str(path)))
        self._refresh_template_thumb()
        quality = template.quality
        if quality.ok:
            self._log(
                f"模板已加载：{template.display_name()}（{template.width}×{template.height}）", "success"
            )
        else:
            self._log(f"模板已加载，但可能不稳定：{quality.message}", "warn")
        return True

    def _on_capture_template(self) -> None:
        if self.controller.is_running or self._picker_active:
            return
        self._picker_active = True
        self._log("请在屏幕上框选要作为模板的区域…", "info")

        def on_done(region: tuple[int, int, int, int]) -> None:
            self._picker_active = False
            self._apply_captured_template(region)

        def on_cancel() -> None:
            self._picker_active = False
            self._log("已取消模板截取", "muted")

        try:
            RegionPicker(
                self.root,
                on_done=on_done,
                on_cancel=on_cancel,
                hint="拖动框选要识别的图标 / 文字 / 按钮",
            )
        except Exception as exc:  # noqa: BLE001
            self._picker_active = False
            self._log(f"打开模板截取器失败：{exc}", "error")

    def _apply_captured_template(self, region: tuple[int, int, int, int]) -> None:
        try:
            rgb, width, height = self.controller.capture_region(region)
            if width < 8 or height < 8:
                self._log("截取区域太小，已取消", "warn")
                return
            path = save_template(rgb, width, height)
            if not self._load_template_path(str(path)):
                return
            self.settings.template_path = str(path)
            self._log(
                f"已从屏幕截取模板：{width}×{height}，保存至 data/templates/{path.name}", "success"
            )
        except Exception as exc:  # noqa: BLE001
            self._log(f"模板截取失败：{exc}", "error")
            self._show_actionable_error("模板截取失败", str(exc), "模板截取")

    def _refresh_template_thumb(self) -> None:
        template = self.template
        if template is None:
            self._thumb_photo = None
            self.thumb_label.configure(image="", text="暂无模板", width=16, height=3)
            if hasattr(self, "var_template"):
                self.var_template.set("")
            return
        try:
            factor = max(1, math.ceil(template.width / self.px(150)))
            path = paths.temp_png_path("thumb")
            png.save_png(path, template.width, template.height, template.rgb)
            photo = tk.PhotoImage(file=str(path), master=self.root)
            if factor > 1:
                photo = photo.subsample(factor, factor)
            path.unlink(missing_ok=True)
            self._thumb_photo = photo
            self.thumb_label.configure(image=photo, text="", width=0, height=0)
        except Exception:  # noqa: BLE001
            self._thumb_photo = None
            name = template.display_name()
            self.thumb_label.configure(image="", text=name[:18], width=16, height=3)
        if not self.var_template.get():
            self.var_template.set(self._short_path(template.path))

    def _current_region(self) -> tuple[int, int, int, int] | None:
        if self.var_region_mode.get() == "custom" and self.region:
            return self.region
        return None

    def _current_threshold(self) -> float:
        self.settings.threshold = self._safe_int(
            self.var_threshold.get(), self.settings.threshold, 50, 100
        )
        return self.settings.threshold / 100.0

    # ------------------------------------------------------------------
    # 识别测试
    # ------------------------------------------------------------------
    def _on_test_match(self) -> None:
        if self.controller.is_running:
            self._log("请先停止任务再做识别测试", "warn")
            return
        if self.template is None:
            saved = self.settings.template_path.strip()
            if not saved:
                self._log("请先选择或截取模板图片", "warn")
                return
            if not self._load_template_path(saved):
                return
        assert self.template is not None
        region = self._current_region()
        threshold = self._current_threshold()
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            result = self.controller.test_match(self.template, region, threshold)
        except Exception as exc:  # noqa: BLE001
            self._log(f"识别测试失败：{exc}", "error")
            return
        finally:
            self.root.configure(cursor="")
        if region:
            result["region"] = region
        match = result.get("match")
        if match is not None:
            self._last_score = match.score
            self._update_stats(0, 0.0, match.score)
            self._log(
                f"识别成功：相似度 {match.score * 100:.1f}% · 耗时 {result['elapsed_ms']:.1f} ms"
                f" · 位置 ({match.x}, {match.y})",
                "success",
            )
        else:
            self._log(
                f"未找到目标（耗时 {result['elapsed_ms']:.1f} ms），请检查模板/区域/相似度",
                "warn",
            )
        MatchPreview(self.root, result, self.template, on_retry=self._on_test_match)

    # ------------------------------------------------------------------
    # 启停控制
    # ------------------------------------------------------------------
    def _build_click_config(self) -> tuple[ClickConfig | None, str]:
        mode = getattr(self, "mode", "fixed")
        if mode == "ai":
            ai_config, ai_error = self._build_ai_config()
            if ai_config is None:
                return None, ai_error
            fullscreen = False
            auto_scroll = bool(self.var_ai_auto_scroll.get())
            monitor = True if auto_scroll else bool(self.var_ai_monitor.get())
            page_mode = bool(self.var_ai_page_mode.get()) or auto_scroll
            if page_mode and not monitor:
                return None, "整页多题模式需要开启「实时监控」"
            if self.ai_region is None:
                return None, "请先框选题目区域"
            region = self.ai_region
            options = self._collect_ai_option_points()
            next_point = self._collect_ai_next_point()
            auto_next = False if auto_scroll else (
                True if not monitor else bool(self.var_ai_auto_next.get())
            )
            if not page_mode:
                if not options:
                    return None, "请至少拾取一个选项（A~F）的点击坐标"
                if (not monitor or auto_next) and next_point is None:
                    return None, "请拾取「下一题」按钮坐标（或关闭「答完自动点下一题」）"
            config = ClickConfig(
                mode="ai",
                ai_config=ai_config,
                ai_region=region,
                ai_options=options,
                ai_next_point=next_point,
                button="left",
                count_limit=max(0, self._safe_int(self.var_ai_count.get(), 0)),
                start_delay_ms=max(
                    0, int(round(self._safe_float(self.var_ai_delay.get(), 1.0) * 1000))
                ),
                ai_option_wait_ms=self._safe_int(self.var_ai_option_wait.get(), 400, 0),
                ai_next_wait_ms=self._safe_int(self.var_ai_next_wait.get(), 1500, 100),
                ai_monitor=monitor,
                ai_auto_next=auto_next,
                ai_poll_ms=self._safe_int(self.var_ai_poll.get(), 300, 100),
                ai_realtime_layout=bool(self.var_ai_realtime_layout.get()),
                ai_page_mode=page_mode,
                ai_fullscreen=fullscreen,
                ai_auto_scroll=auto_scroll,
                ai_tile_mode=bool(self.var_ai_tile_mode.get()),
                ai_scroll_steps=self._safe_int(self.var_ai_scroll_steps.get(), 3, 1),
                ai_scroll_wait_ms=self._safe_int(self.var_ai_scroll_wait.get(), 400, 0),
                ai_scroll_max=self._safe_int(self.var_ai_scroll_max.get(), 0, 0),
            )
            return config, ""
        if mode == "image":
            if self.template is None:
                saved = self.settings.template_path.strip()
                if not saved:
                    return None, "请先选择或截取模板图片"
                if not self._load_template_path(saved):
                    return None, "模板加载失败"
            assert self.template is not None
            threshold = self._current_threshold()
            poll = self._safe_int(self.var_poll.get(), 500, 50)
            config = ClickConfig(
                mode="image",
                template_path=self.template.path,
                template=self.template,
                region=self._current_region(),
                threshold=threshold,
                click_offset_x=self._safe_int(self.var_offset_x.get(), 0),
                click_offset_y=self._safe_int(self.var_offset_y.get(), 0),
                poll_ms=poll,
                count_limit=max(0, self._safe_int(self.var_count.get(), 0)),
                start_delay_ms=max(0, int(round(self._safe_float(self.var_delay.get(), 2.0) * 1000))),
                restore_cursor=bool(self.var_restore.get()),
                double_click=self.var_click_type.get() == _CLICK_TYPES[1],
                button=_BUTTON_LABELS.get(self.var_button.get(), "left"),
            )
            return config, ""
        x = self._safe_int(self.var_x.get(), 0)
        y = self._safe_int(self.var_y.get(), 0)
        config = ClickConfig(
            mode="fixed",
            x=x,
            y=y,
            button=_BUTTON_LABELS.get(self.var_button.get(), "left"),
            double_click=self.var_click_type.get() == _CLICK_TYPES[1],
            interval_ms=self._safe_int(self.var_interval.get(), 1000, 10),
            random_interval=bool(self.var_random.get()),
            interval_min_ms=self._safe_int(self.var_min.get(), 800, 10),
            interval_max_ms=self._safe_int(self.var_max.get(), 1200, 10),
            count_limit=max(0, self._safe_int(self.var_count.get(), 0)),
            start_delay_ms=max(0, int(round(self._safe_float(self.var_delay.get(), 2.0) * 1000))),
            restore_cursor=bool(self.var_restore.get()),
        )
        if config.interval_max_ms < config.interval_min_ms:
            config.interval_max_ms = config.interval_min_ms
        return config, ""

    @staticmethod
    def _safe_float(text: str, default: float) -> float:
        try:
            return float(str(text).strip())
        except (TypeError, ValueError):
            return default

    def _on_text_typing(self) -> None:
        """打开逐字录入窗口：输入一句话，等待用户点目标框后逐字键入。"""
        if self.controller.is_running or self.state in ("starting", "running", "paused"):
            self._log("请先停止当前任务，再使用逐字录入", "warn")
            return
        if self._one_shot_busy or self._fill_busy:
            self._log("单次答题/填空进行中，请稍后再用逐字录入", "warn")
            return
        dialog = getattr(self, "_typing_dialog", None)
        if dialog is not None:
            try:
                if dialog.winfo_exists():
                    dialog.deiconify()
                    dialog.lift()
                    dialog.focus_force()
                    return
            except tk.TclError:
                pass
        self._typing_dialog = TextTypingDialog(self.root, on_log=self._log)

    def _on_simple_fill(self) -> None:
        self._on_one_shot_fill()

    def _on_simple_select(self) -> None:
        self._on_one_shot_answer()

    def _on_auto_answer_start(self) -> None:
        if self._auto_answer_running:
            self._log("开始答题正在运行，点「停止」可结束", "warn")
            return
        if self.controller.is_running or self._one_shot_busy or self._fill_busy:
            self._log("当前有任务正在执行，请先停止", "warn")
            return
        scope = choose_answer_scope(self.root)
        if scope is None:
            return
        if scope == "full":
            try:
                vx, vy, vw, vh = winapi.virtual_screen()
                region = (vx, vy, vw, vh)
            except Exception as exc:  # noqa: BLE001
                self._log(f"获取全屏范围失败：{exc}", "error")
                return
            self._start_auto_answer(region)
            return
        if self._picker_active:
            return
        self._picker_active = True

        def on_done(region: tuple[int, int, int, int]) -> None:
            self._picker_active = False
            self._restore_main_after_picker()
            self._start_auto_answer(region)

        def on_cancel() -> None:
            self._picker_active = False
            self._restore_main_after_picker()
            self._log("已取消开始答题框选", "muted")

        self._hide_main_for_picker()
        RegionPicker(
            self.root,
            on_done=on_done,
            on_cancel=on_cancel,
            hint="框选选择题区域；AI 会自动选择答案并点击下一题",
        )

    def _start_auto_answer(self, region: tuple[int, int, int, int]) -> None:
        ai_config, ai_error = self._build_ai_config()
        if ai_config is None:
            self._log(ai_error, "warn")
            messagebox.showwarning("AI 配置不完整", ai_error, parent=self.root)
            return
        self._auto_answer_stop.clear()
        self._auto_answer_running = True
        self._set_status("running")
        self._log("开始答题：自动识别选择题 → 选择答案 → 点击下一题（点「停止」结束）", "accent")

        def worker() -> None:
            rounds = 0
            while not self._auto_answer_stop.is_set():
                try:
                    result = self.controller.answer_once(
                        region,
                        ai_config,
                        page_mode=True,
                        realtime_layout=True,
                        auto_next=True,
                        option_wait_ms=260,
                        next_wait_ms=700,
                    )
                    rounds += 1
                    answers = result.get("answers") or []
                    clicked = result.get("clicks", 0)
                    next_clicked = bool(result.get("next"))
                    self._emit_event(
                        {
                            "kind": "log",
                            "level": "success" if clicked else "info",
                            "message": (
                                f"开始答题第 {rounds} 轮：已点击 {clicked} 个选项"
                                + (f"（{'、'.join(map(str, answers))}）" if answers else "")
                                + ("，已点下一题" if next_clicked else "，未找到下一题")
                            ),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    self._emit_event(
                        {"kind": "log", "level": "error", "message": f"开始答题出错：{exc}"}
                    )
                for _ in range(12):
                    if self._auto_answer_stop.is_set():
                        break
                    time.sleep(0.1)
            self._emit_event({"kind": "auto_answer_done", "rounds": rounds})

        threading.Thread(target=worker, daemon=True, name="auto-answer").start()

    def _on_auto_answer_stop(self) -> None:
        if not self._auto_answer_running:
            return
        self._auto_answer_stop.set()
        self._log("正在停止开始答题…", "warn")

    def _on_auto_answer_done(self, event: dict) -> None:
        self._auto_answer_running = False
        self._set_status("idle")
        self._log(f"开始答题已停止（共 {int(event.get('rounds', 0))} 轮）", "muted")

    def _on_ai_show_answers(self) -> None:
        """AI 看答案：选择矩形/全屏，识别后在弹窗显示答案，不自动操作。"""
        if self.controller.is_running or self._answer_scan_busy:
            self._log("请先停止当前任务，再使用 AI 看答案", "warn")
            return
        if self._one_shot_busy or self._fill_busy:
            self._log("单次答题/填空进行中，请稍候", "warn")
            return
        self._hide_answer_hud()
        scope = choose_answer_scope(self.root)
        if scope is None:
            return
        if scope == "full":
            try:
                vx, vy, vw, vh = winapi.virtual_screen()
                region = (vx, vy, vw, vh)
            except Exception as exc:  # noqa: BLE001
                self._log(f"获取全屏范围失败：{exc}", "error")
                return
            self._run_answer_scan(region, "全屏", append=False)
            return

        if self._picker_active:
            return
        self._picker_active = True
        self._log("AI 看答案：请在屏幕上框选题目区域…", "info")

        def on_done(region: tuple[int, int, int, int]) -> None:
            self._picker_active = False
            # 不恢复主窗口，直接转为最小化，避免框选结束后软件窗口闪一下
            self._picker_root_was_visible = False
            try:
                self.root.iconify()
            except tk.TclError:  # pragma: no cover
                pass
            self._run_answer_scan(region, "矩形框选", append=False)

        def on_cancel() -> None:
            self._picker_active = False
            self._restore_main_after_picker()
            self._log("已取消 AI 看答案框选", "muted")

        try:
            self._hide_main_for_picker()
            RegionPicker(
                self.root,
                on_done=on_done,
                on_cancel=on_cancel,
                hint="拖出矩形框选需要 AI 读答案的题目区域（松开即确认）",
            )
        except Exception as exc:  # noqa: BLE001
            self._picker_active = False
            self._restore_main_after_picker()
            self._log(f"打开框选器失败：{exc}", "error")

    def _run_answer_scan(
        self, region: tuple[int, int, int, int], scope_text: str, append: bool = False
    ) -> None:
        ai_config, ai_error = self._build_ai_config()
        if ai_config is None:
            self._log(ai_error, "warn")
            messagebox.showwarning("AI 配置不完整", ai_error, parent=self.root)
            return
        self._last_answer_region = region
        self._last_answer_scope = scope_text
        if not append:
            self._answer_frame_visible = True
        self._answer_scan_busy = True
        self.btn_simple_answers.set_enabled(False)
        self.btn_simple_quick.set_enabled(False)
        self._log(f"AI 看答案（{scope_text}）：正在识别并生成答案…", "accent")
        self._minimize_for_single_shot()

        def worker() -> None:
            try:
                time.sleep(0.22)
                rgb, width, height = self.controller.capture_region(region)
                image_png = png.encode_png(width, height, rgb)
                answer = ai_client.ask_answers_text(image_png, ai_config)
                self._emit_event(
                    {
                        "kind": "answer_scan_done",
                        "answer": answer,
                        "scope": scope_text,
                        "model": ai_config.model,
                        "append": append,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self._emit_event(
                    {"kind": "answer_scan_error", "error": str(exc), "append": append}
                )

        threading.Thread(target=worker, daemon=True, name="answer-scan").start()

    def _show_answer_hud(self, region: tuple[int, int, int, int] | None) -> None:
        if region is None or not self._answer_frame_visible:
            self._hide_answer_hud()
            return
        try:
            if self._answer_frame is None:
                self._answer_frame = EditableRegionFrame(
                    self.root, on_change=self._on_answer_region_changed
                )
            self._answer_frame.show(region)
        except (tk.TclError, AttributeError):  # pragma: no cover
            pass

    def _on_answer_region_changed(self, region: tuple[int, int, int, int]) -> None:
        self._last_answer_region = region

    def _toggle_answer_frame(self) -> bool:
        self._answer_frame_visible = not bool(self._answer_frame_visible)
        if self._answer_frame_visible:
            self._show_answer_hud(self._last_answer_region)
        else:
            self._hide_answer_hud()
        return self._answer_frame_visible

    def _hide_answer_hud(self) -> None:
        try:
            if self._answer_frame is not None:
                self._answer_frame.hide()
        except (tk.TclError, AttributeError):  # pragma: no cover
            pass

    def _on_answer_popup_closed(self) -> None:
        self._answer_popup = None
        self._hide_answer_hud()

    def _rescan_answer(self) -> None:
        """答案窗里的「再次识别」：使用当前可拖动区域的实时范围，直接重扫。"""
        if self._answer_scan_busy or self._last_answer_region is None:
            return
        try:
            self.root.iconify()
        except tk.TclError:  # pragma: no cover
            pass
        self._run_answer_scan(
            self._last_answer_region,
            self._last_answer_scope or "矩形框选",
            append=True,
        )

    def _on_answer_scan_done(self, event: dict) -> None:
        self._answer_scan_busy = False
        self.btn_simple_answers.set_enabled(True)
        self.btn_simple_quick.set_enabled(True)
        # 主窗口保持最小化，只显示置顶答案窗
        self._single_shot_was_visible = False
        answer = event.get("answer")
        if answer is None:
            self._on_answer_scan_error(event)
            return
        text = str(getattr(answer, "raw", "")).strip()
        elapsed = float(getattr(answer, "elapsed_ms", 0.0)) / 1000.0
        if self._last_answer_scope == "矩形框选":
            self._show_answer_hud(self._last_answer_region)
        else:
            self._hide_answer_hud()
        if event.get("append"):
            popup = self._answer_popup
            if popup is not None:
                try:
                    if popup.winfo_exists():
                        popup.deiconify()
                        popup.lift()
                        popup.append_answer(text)
                        self._log(
                            f"再次识别完成（耗时 {elapsed:.1f} 秒），答案已追加到下方",
                            "success",
                        )
                        return
                except tk.TclError:  # pragma: no cover
                    pass
        if self._answer_popup is not None:
            try:
                if self._answer_popup.winfo_exists():
                    self._answer_popup.destroy()
            except tk.TclError:  # pragma: no cover
                pass
        self._answer_popup = AnswerPopup(
            self.root,
            text,
            on_rescan=self._rescan_answer,
            on_close=self._on_answer_popup_closed,
            on_toggle_frame=self._toggle_answer_frame,
        )
        self._log(
            f"AI 看答案完成（{event.get('scope', '')}，耗时 {elapsed:.1f} 秒），已弹出答案",
            "success",
        )

    def _on_answer_scan_error(self, event: dict) -> None:
        self._answer_scan_busy = False
        self.btn_simple_answers.set_enabled(True)
        self.btn_simple_quick.set_enabled(True)
        error = str(event.get("error", "未知错误"))
        if event.get("append") and self._answer_popup is not None:
            try:
                if self._answer_popup.winfo_exists():
                    self._answer_popup.deiconify()
                    self._answer_popup.lift()
                    self._answer_popup.append_answer(f"再次识别失败：{error}")
                    self._show_answer_hud(self._last_answer_region)
                    self._log(f"再次识别失败：{error}", "error")
                    return
            except tk.TclError:  # pragma: no cover
                pass
        self._restore_after_single_shot()
        self._log(f"AI 看答案失败：{error}", "error")
        self._show_actionable_error("AI 看答案失败", error, "AI 看答案")

    def _on_simple_start(self) -> None:
        if self.mode != "ai":
            self.mode = "ai"
            try:
                self.mode_segment.set_active("ai")
            except tk.TclError:  # pragma: no cover
                pass
        self._on_start()

    def _on_browser_hybrid(self) -> None:
        if self._browser_hybrid_win is not None:
            try:
                if self._browser_hybrid_win.winfo_exists():
                    self._browser_hybrid_win.deiconify()
                    self._browser_hybrid_win.lift()
                    return
            except tk.TclError:  # pragma: no cover
                pass
        self._browser_hybrid_win = BrowserHybridWindow(
            self.root,
            get_ai_config=self._build_ai_config,
            on_close=lambda: setattr(self, "_browser_hybrid_win", None),
        )

    def _toggle_advanced(self) -> None:
        self.advanced_visible = not bool(getattr(self, "advanced_visible", False))
        self.more_options_visible = bool(self.advanced_visible)
        self._apply_advanced_visibility()
        self._log(
            "已展开高级设置（含整页多题、自动滚屏、填空替换等）"
            if self.advanced_visible
            else "已收起高级设置",
            "muted",
        )

    def _toggle_more_options(self) -> None:
        # 兼容旧入口：现在并入「高级设置」
        self._toggle_advanced()

    def _apply_advanced_visibility(self) -> None:
        visible = bool(getattr(self, "advanced_visible", False))
        self.more_options_visible = visible
        try:
            self.btn_advanced.set_text("⚙ 收起高级设置" if visible else "⚙ 高级设置")
        except (tk.TclError, AttributeError):  # pragma: no cover
            pass
        try:
            self.action_card.grid_remove()
        except (tk.TclError, AttributeError):  # pragma: no cover
            pass
        win = getattr(self, "advanced_win", None)
        if win is None:
            return
        if not visible:
            try:
                win.withdraw()
            except tk.TclError:  # pragma: no cover
                pass
            self._sync_region_huds()
            return
        try:
            win.deiconify()
            win.lift()
            win.attributes("-alpha", 1.0)
        except tk.TclError:  # pragma: no cover
            pass
        self.mode_card.pack(fill="x", pady=(0, self.px(12)))
        self._apply_mode_visibility()
        self.help_card.pack(fill="x", pady=(0, self.px(6)))

    def _hide_advanced_window(self) -> None:
        self.advanced_visible = False
        self.more_options_visible = False
        self._apply_advanced_visibility()

    def _toggle_monitor(self) -> None:
        win = getattr(self, "monitor_win", None)
        if win is None:
            return
        try:
            if win.winfo_viewable():
                win.withdraw()
            else:
                win.deiconify()
                win.lift()
                win.attributes("-alpha", 1.0)
        except tk.TclError:  # pragma: no cover
            pass

    def _hide_monitor(self) -> None:
        try:
            self.monitor_win.withdraw()
        except (tk.TclError, AttributeError):  # pragma: no cover
            pass

    def _on_start(self) -> None:
        if self.mode == "ai" and not (
            self.advanced_visible and getattr(self, "more_options_visible", False)
        ):
            self._log("自动答题已停用：请用「AI 看答案」查看答案，或用单次选择/单次填空", "warn")
            messagebox.showinfo(
                "自动答题已停用",
                "AI 模式不再自动点击。\n\n"
                "要选择题请用「单次选择」，填空题请用「单次填空」，"
                "只看答案请用「AI 看答案」。",
                parent=self.root,
            )
            return
        dialog = getattr(self, "_typing_dialog", None)
        if dialog is not None and dialog.is_active:
            self._log("逐字录入进行中，请先按 F8 停止录入", "warn")
            return
        if self._one_shot_busy or self._fill_busy:
            self._log("单次答题/填空进行中，请稍候再开始实时任务", "warn")
            return
        if self.controller.is_running or self.state in ("starting", "running", "paused"):
            return
        config, error = self._build_click_config()
        if error or config is None:
            self._log(error or "参数不完整", "warn")
            messagebox.showwarning("无法开始", error or "参数不完整", parent=self.root)
            return
        ok, message = validate_config(config)
        if not ok:
            self._log(message, "warn")
            messagebox.showwarning("参数有误", message, parent=self.root)
            return
        self._save_settings()
        self._log(
            "主窗口已最小化，任务运行期间不会自动弹出；需要停止请按 F8",
            "info",
        )
        self._minimize_for_run()
        started, error = self.controller.start(config)
        if not started:
            self._restore_main_window()
            self._log(f"启动失败：{error}", "error")
            self._show_actionable_error("启动失败", error, "开始执行")
            self._set_status("idle")
            self._set_controls_running(False)
            return
        self._set_status("running")
        self._started_at = time.perf_counter()
        self._last_score = None
        self._update_stats(0, 0.0, None)
        self._set_controls_running(True)
        self.btn_pause.set_text("暂停  (F7)")
        mode_text = {"image": "找图点击", "ai": "AI 答题"}.get(config.mode, "定点连点")
        self._log(f"已开始：{mode_text}", "success")

    def _on_pause_resume(self) -> None:
        if not self.controller.is_running:
            return
        if self.controller.is_paused:
            self.controller.resume()
            self.btn_pause.set_text("暂停  (F7)")
        else:
            self.controller.pause()
            self.btn_pause.set_text("继续  (F7)")

    def _on_stop(self) -> None:
        dialog = getattr(self, "_typing_dialog", None)
        if dialog is not None and dialog.is_active:
            dialog.stop()
            self._log("正在停止逐字录入…", "muted")
            return
        if not self.controller.is_running:
            return
        self._log("正在停止任务…", "muted")
        self._set_status("stopping")
        try:
            self.controller.stop()
        except Exception:  # pragma: no cover
            pass
        # 引擎会通过事件队列回传 finished 事件，由 _on_engine_finished 统一收尾

    # ------------------------------------------------------------------
    # 其它交互
    # ------------------------------------------------------------------
    def _on_topmost_toggle(self) -> None:
        try:
            self.root.attributes("-topmost", bool(self.var_topmost.get()))
        except tk.TclError:  # pragma: no cover
            pass
        self._log("窗口置顶已开启" if self.var_topmost.get() else "窗口置顶已关闭", "muted")

    def _apply_titlebar_theme(self) -> None:
        try:
            hwnd = winapi.get_toplevel_hwnd(self.root)
            winapi.enable_dark_titlebar(hwnd)
        except Exception:  # pragma: no cover
            pass

    def _on_restart_admin(self) -> None:
        if winapi.is_admin():
            self._log("当前已经是管理员权限", "muted")
            return
        self._save_settings()
        if winapi.relaunch_as_admin():
            self._log("已发起管理员重启，本窗口即将关闭…", "accent")
            self.root.after(250, self._shutdown)
        else:
            self._log("管理员重启失败（可能取消了 UAC 授权）", "warn")

    def _on_tk_exception(self, exc_type, exc_value, exc_tb) -> None:  # pragma: no cover
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            crash = paths.LOGS_DIR / f"crash_{int(time.time())}.log"
            crash.write_text(detail, encoding="utf-8")
        except OSError:
            pass
        try:
            self._log(f"界面异常：{exc_value}", "error")
        except Exception:
            print(detail)
        try:
            from . import error_hints

            error_hints.show_error(
                self.root,
                "ATanswer 运行异常",
                str(exc_value) or "未知界面异常",
                "界面回调",
            )
        except Exception:
            pass

    def _shutdown(self) -> None:
        try:
            self._auto_answer_stop.set()
        except Exception:
            pass
        try:
            if self.controller.is_running:
                self.controller.stop()
        except Exception:
            pass
        try:
            if self.hotkey_manager is not None:
                self.hotkey_manager.stop()
        except Exception:
            pass
        for hud in (self._hud_ai, self._hud_search):
            if hud is not None:
                try:
                    hud.destroy()
                except Exception:
                    pass
        self._hud_ai = self._hud_search = None
        try:
            if self._answer_frame is not None:
                self._answer_frame.destroy()
        except Exception:
            pass
        self._save_settings()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    def run(self) -> None:
        self.root.mainloop()


def run_app() -> None:
    app = MainApp()
    app.run()
