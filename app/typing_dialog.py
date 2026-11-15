"""逐字录入对话框：输入一句话，等待用户聚焦目标框后逐字符键入。"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox

from . import typing
from .widgets import PALETTE, RoundedButton, font


class TextTypingDialog(tk.Toplevel):
    """把用户输入的一段文本逐字键入当前焦点文本框（不经过剪贴板）。"""

    def __init__(self, master: tk.Misc, on_log=None) -> None:
        super().__init__(master)
        self._root = master.winfo_toplevel()
        self._on_log = on_log
        self._active = False
        self._closed = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_typed = 0
        self._total = 0

        self.title("逐字录入 · 智能点击助手")
        self.configure(bg=PALETTE["bg"])
        self.resizable(True, True)
        self.transient(self._root)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda _event: self._on_close())
        self._build()
        self._center()
        self.after(80, self._focus_text)

    # -- UI ---------------------------------------------------------------
    def _build(self) -> None:
        wrap = tk.Frame(self, bg=PALETTE["bg"], padx=18, pady=16)
        wrap.pack(fill="both", expand=True)

        tk.Label(
            wrap, text="逐字录入", bg=PALETTE["bg"], fg=PALETTE["text"],
            font=font(18, bold=True), anchor="w",
        ).pack(fill="x")
        tk.Label(
            wrap,
            text="把要录入的句子粘贴到下面 → 点「开始录入」→ 在等待时间内点击目标文本框，程序会逐字键入。",
            bg=PALETTE["bg"], fg=PALETTE["muted"], font=font(10),
            anchor="w", justify="left", wraplength=760,
        ).pack(fill="x", pady=(4, 12))

        text_wrap = tk.Frame(wrap, bg=PALETTE["card"], highlightthickness=1,
                             highlightbackground=PALETTE["border"])
        text_wrap.pack(fill="both", expand=True)
        self.text = tk.Text(
            text_wrap, height=9, wrap="word", undo=True,
            bg=PALETTE["bg2"], fg=PALETTE["text"],
            insertbackground=PALETTE["text"], selectbackground=PALETTE["accent_dark"],
            relief="flat", borderwidth=0, font=font(12), padx=10, pady=8,
        )
        self.text.pack(side="left", fill="both", expand=True)
        scroll = tk.Scrollbar(text_wrap, command=self.text.yview)
        scroll.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=scroll.set)

        tools = tk.Frame(wrap, bg=PALETTE["bg"])
        tools.pack(fill="x", pady=(6, 0))
        self.count_label = tk.Label(
            tools, text="0 字", bg=PALETTE["bg"], fg=PALETTE["dim"], font=font(9),
        )
        self.count_label.pack(side="left")
        self._small_button(tools, "粘贴", self._paste_clipboard, width=70).pack(
            side="right", padx=(6, 0)
        )
        self._small_button(tools, "清空", self._clear_text, width=58).pack(side="right")

        opts = tk.Frame(wrap, bg=PALETTE["bg"])
        opts.pack(fill="x", pady=(12, 0))
        tk.Label(opts, text="每个字间隔", bg=PALETTE["bg"], fg=PALETTE["muted"],
                 font=font(10)).pack(side="left")
        self.interval_var = tk.StringVar(value="30")
        tk.Entry(
            opts, textvariable=self.interval_var, width=6, justify="center",
            bg=PALETTE["bg2"], fg=PALETTE["text"], insertbackground=PALETTE["text"],
            relief="flat", highlightthickness=1, highlightbackground=PALETTE["border"],
            font=font(10),
        ).pack(side="left", padx=(5, 3))
        tk.Label(opts, text="毫秒", bg=PALETTE["bg"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")

        tk.Label(opts, text="开始前等待", bg=PALETTE["bg"], fg=PALETTE["muted"],
                 font=font(10)).pack(side="left", padx=(18, 0))
        self.delay_var = tk.StringVar(value="3.0")
        tk.Entry(
            opts, textvariable=self.delay_var, width=6, justify="center",
            bg=PALETTE["bg2"], fg=PALETTE["text"], insertbackground=PALETTE["text"],
            relief="flat", highlightthickness=1, highlightbackground=PALETTE["border"],
            font=font(10),
        ).pack(side="left", padx=(5, 3))
        tk.Label(opts, text="秒（留给点目标框）", bg=PALETTE["bg"], fg=PALETTE["dim"],
                 font=font(9)).pack(side="left")

        checks = tk.Frame(wrap, bg=PALETTE["bg"])
        checks.pack(fill="x", pady=(10, 0))
        self.replace_var = tk.BooleanVar(value=True)
        self.enter_var = tk.BooleanVar(value=False)
        self._check(checks, "录入前全选并替换目标框内容", self.replace_var).pack(side="left")
        self._check(checks, "结束后按回车", self.enter_var).pack(side="left", padx=(18, 0))

        self.status_label = tk.Label(
            wrap, text="等待开始", bg=PALETTE["bg"], fg=PALETTE["cyan"],
            font=font(10), anchor="w", justify="left", wraplength=760,
        )
        self.status_label.pack(fill="x", pady=(14, 0))

        buttons = tk.Frame(wrap, bg=PALETTE["bg"])
        buttons.pack(fill="x", pady=(12, 0))
        self.btn_start = RoundedButton(
            buttons, text="开始录入", command=self._on_start,
            width=160, height=40, radius=10,
            bg=PALETTE["accent"], hover_bg=PALETTE["accent_hover"],
            press_bg=PALETTE["accent_dark"], fg="#ffffff", font=font(11, bold=True),
        )
        self.btn_start.pack(side="left")
        self.btn_stop = RoundedButton(
            buttons, text="停止", command=self.stop,
            width=90, height=40, radius=10,
            bg=PALETTE["danger"], hover_bg=PALETTE["danger_hover"],
            press_bg="#c24343", fg="#ffffff", font=font(11),
        )
        self.btn_stop.pack(side="left", padx=(8, 0))
        self.btn_stop.set_enabled(False)
        self._small_button(buttons, "关闭", self._on_close).pack(side="right")
        tk.Label(
            wrap,
            text="提示：等待期间点一下目标输入框；F8 可随时停止。不会使用剪贴板粘贴。",
            bg=PALETTE["bg"], fg=PALETTE["dim"], font=font(9), anchor="w",
            justify="left", wraplength=760,
        ).pack(fill="x", pady=(10, 0))

        self.text.bind("<<Modified>>", self._on_text_modified)

    def _small_button(
        self, parent: tk.Misc, text: str, command, width: int = 100
    ) -> RoundedButton:
        return RoundedButton(
            parent, text=text, command=command, width=width, height=26, radius=6,
            bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
            border_color=PALETTE["border"], font=font(9),
        )

    def _check(self, parent: tk.Misc, text: str, variable: tk.BooleanVar) -> tk.Checkbutton:
        return tk.Checkbutton(
            parent, text=text, variable=variable, bg=PALETTE["bg"],
            fg=PALETTE["muted"], activebackground=PALETTE["bg"],
            activeforeground=PALETTE["text"], selectcolor=PALETTE["bg2"],
            font=font(10), relief="flat", borderwidth=0, highlightthickness=0,
        )

    def _center(self) -> None:
        self.update_idletasks()
        width = max(620, self.winfo_reqwidth())
        height = max(520, self.winfo_reqheight())
        try:
            x = self._root.winfo_rootx() + max(0, (self._root.winfo_width() - width) // 2)
            y = self._root.winfo_rooty() + max(0, (self._root.winfo_height() - height) // 2)
        except tk.TclError:  # pragma: no cover
            x = y = 100
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _focus_text(self) -> None:
        if self._closed:
            return
        try:
            self.text.focus_set()
        except tk.TclError:  # pragma: no cover
            pass

    # -- text helpers -----------------------------------------------------
    def _on_text_modified(self, _event=None) -> None:
        try:
            self.text.edit_modified(False)
            count = len(self.text.get("1.0", "end-1c"))
            self.count_label.configure(text=f"{count} 字")
        except tk.TclError:  # pragma: no cover
            pass

    def _clear_text(self) -> None:
        if self._active:
            return
        self.text.delete("1.0", "end")

    def _paste_clipboard(self) -> None:
        if self._active:
            return
        try:
            value = self.clipboard_get()
        except tk.TclError:
            self.status_label.configure(text="剪贴板里没有文本", fg=PALETTE["warning"])
            return
        self.text.insert("insert", value)
        self.status_label.configure(text="已粘贴到文本框", fg=PALETTE["muted"])

    # -- typing -----------------------------------------------------------
    @property
    def is_active(self) -> bool:
        return self._active

    def _on_start(self) -> None:
        if self._active:
            return
        text = self.text.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showwarning("没有内容", "请先输入或粘贴要录入的句子。", parent=self)
            return
        try:
            interval = max(1, min(1000, int(float(self.interval_var.get().strip()))))
        except ValueError:
            messagebox.showwarning("参数有误", "每个字间隔请填毫秒数（如 30）。", parent=self)
            return
        try:
            delay = max(0.0, min(60.0, float(self.delay_var.get().strip())))
        except ValueError:
            messagebox.showwarning("参数有误", "开始前等待请填秒数（如 3）。", parent=self)
            return

        self._active = True
        self._stop_event.clear()
        self._last_typed = 0
        self._total = len(typing.normalize_text(text))
        self.btn_start.set_enabled(False)
        self.btn_stop.set_enabled(True)
        self.text.configure(state="disabled")
        self.status_label.configure(
            text=f"已开始：{delay:.1f} 秒后输入 {self._total} 字；请立刻点击目标文本框…",
            fg=PALETTE["warning"],
        )
        # 收起窗口，把屏幕让给目标输入框；结束时自动恢复
        try:
            self.withdraw()
            self._root.iconify()
        except tk.TclError:  # pragma: no cover
            pass
        options = typing.TypingOptions(
            text=text,
            interval_ms=interval,
            start_delay_ms=int(round(delay * 1000)),
            replace_existing=bool(self.replace_var.get()),
            press_enter=bool(self.enter_var.get()),
        )
        self._thread = threading.Thread(
            target=self._worker, args=(options,), daemon=True
        )
        self._thread.start()

    def _worker(self, options: typing.TypingOptions) -> None:
        try:
            result = typing.type_text(
                options,
                should_stop=self._stop_event.is_set,
                on_progress=self._on_progress_threadsafe,
                on_status=self._on_status_threadsafe,
            )
        except Exception as exc:  # noqa: BLE001
            self._post(self._finish_error, str(exc))
            return
        self._post(self._finish, result)

    def _post(self, func, *args) -> None:
        if self._closed:
            return
        try:
            self.after(0, lambda: func(*args))
        except (tk.TclError, RuntimeError):  # pragma: no cover
            pass

    def _on_progress_threadsafe(self, done: int, total: int) -> None:
        # 只在必要时更新，避免每个字符都排队刷新界面
        if done == total or done % 5 == 0 or done <= 2:
            self._post(self._update_progress, done, total)

    def _on_status_threadsafe(self, message: str) -> None:
        self._post(self._update_status, message)

    def _update_progress(self, done: int, total: int) -> None:
        self._last_typed = done
        try:
            self.status_label.configure(
                text=f"正在逐字输入：{done}/{total} 字（F8 可停止）",
                fg=PALETTE["cyan"],
            )
        except tk.TclError:  # pragma: no cover
            pass

    def _update_status(self, message: str) -> None:
        try:
            self.status_label.configure(text=message, fg=PALETTE["cyan"])
        except tk.TclError:  # pragma: no cover
            pass

    def stop(self) -> None:
        if not self._active:
            return
        self._stop_event.set()
        try:
            self.btn_stop.set_enabled(False)
            self.status_label.configure(text="正在停止…", fg=PALETTE["warning"])
        except tk.TclError:  # pragma: no cover
            pass

    def _restore_window(self) -> None:
        try:
            self._root.deiconify()
            self.deiconify()
            self.lift()
        except tk.TclError:  # pragma: no cover
            pass

    def _finish(self, result: typing.TypingResult) -> None:
        self._active = False
        self._last_typed = result.typed_chars
        self._restore_window()
        try:
            self.btn_start.set_enabled(True)
            self.btn_stop.set_enabled(False)
            self.text.configure(state="normal")
            self.status_label.configure(
                text=result.message,
                fg=PALETTE["success"] if result.completed else PALETTE["warning"],
            )
            self._on_text_modified()
        except tk.TclError:  # pragma: no cover
            return
        if self._on_log:
            level = "success" if result.completed else "warn"
            self._on_log(f"逐字录入：{result.message}", level)

    def _finish_error(self, message: str) -> None:
        self._active = False
        self._restore_window()
        try:
            self.btn_start.set_enabled(True)
            self.btn_stop.set_enabled(False)
            self.text.configure(state="normal")
            self.status_label.configure(
                text=f"录入失败：{message}", fg=PALETTE["danger"]
            )
        except tk.TclError:  # pragma: no cover
            return
        if self._on_log:
            self._on_log(f"逐字录入失败：{message}", "error")

    def _on_close(self) -> None:
        was_active = self._active
        if was_active:
            if not messagebox.askyesno(
                "停止录入", "正在录入文本，确定停止并关闭吗？", parent=self
            ):
                return
            self._stop_event.set()
        self._closed = True
        if was_active:
            try:
                self._root.deiconify()
            except tk.TclError:  # pragma: no cover
                pass
        try:
            self.destroy()
        except tk.TclError:  # pragma: no cover
            pass
