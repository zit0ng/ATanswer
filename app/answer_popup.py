"""AI 看答案：选择识别范围 + 答案弹窗（不自动点击）。"""
from __future__ import annotations

import re
import tkinter as tk

from . import winapi
from .widgets import PALETTE, RoundedButton, font


def choose_answer_scope(master: tk.Misc) -> str | None:
    """让用户选择矩形框选或全屏识别，返回 'rect' / 'full' / None。"""
    win = tk.Toplevel(master)
    win.title("AI 看答案 · 选择识别范围")
    win.configure(bg=PALETTE["bg"])
    try:
        win.attributes("-alpha", 0.90)
    except tk.TclError:  # pragma: no cover
        pass
    win.transient(master.winfo_toplevel())
    win.resizable(False, False)
    win.grab_set()
    result: dict[str, str | None] = {"value": None}

    body = tk.Frame(win, bg=PALETTE["bg"], padx=22, pady=18)
    body.pack(fill="both", expand=True)
    tk.Label(
        body, text="选择识别范围", bg=PALETTE["bg"], fg=PALETTE["text"],
        font=font(16, bold=True), anchor="w",
    ).pack(fill="x")
    tk.Label(
        body,
        text="只识别并显示答案，不会自动点击或填写。",
        bg=PALETTE["bg"], fg=PALETTE["muted"], font=font(10), anchor="w",
    ).pack(fill="x", pady=(4, 14))

    row = tk.Frame(body, bg=PALETTE["bg"])
    row.pack(fill="x")

    def choose(value: str) -> None:
        result["value"] = value
        win.destroy()

    RoundedButton(
        row, text="矩形框选", command=lambda: choose("rect"),
        width=150, height=42, radius=10,
        bg=PALETTE["accent"], hover_bg=PALETTE["accent_hover"],
        press_bg=PALETTE["accent_dark"], fg="#ffffff", font=font(11, bold=True),
    ).pack(side="left")
    RoundedButton(
        row, text="全屏识别", command=lambda: choose("full"),
        width=150, height=42, radius=10,
        bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
        border_color=PALETTE["border"], font=font(11),
    ).pack(side="left", padx=(10, 0))
    RoundedButton(
        row, text="取消", command=lambda: choose(None),
        width=80, height=42, radius=10,
        bg=PALETTE["bg2"], hover_bg=PALETTE["card_hover"],
        border_color=PALETTE["border"], font=font(10),
    ).pack(side="left", padx=(10, 0))

    win.protocol("WM_DELETE_WINDOW", lambda: choose(None))
    win.update_idletasks()
    width, height = max(420, win.winfo_reqwidth()), max(180, win.winfo_reqheight())
    try:
        x = master.winfo_rootx() + max(0, (master.winfo_width() - width) // 2)
        y = master.winfo_rooty() + max(0, (master.winfo_height() - height) // 2)
    except tk.TclError:  # pragma: no cover
        x = y = 100
    win.geometry(f"{width}x{height}+{x}+{y}")
    master.wait_window(win)
    return result["value"]


class AnswerPopup(tk.Toplevel):
    """半透明置顶答案窗：只显示题目和答案，可再次识别并追加答案。"""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        subtitle: str = "",
        on_rescan=None,
        on_close=None,
        on_toggle_frame=None,
    ) -> None:
        super().__init__(master)
        self._on_rescan = on_rescan
        self._on_close = on_close
        self._on_toggle_frame = on_toggle_frame
        self._rescanning = False
        self._frame_visible = True
        self._spin_job = None
        self.configure(bg="#0b1220")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.90)
        except tk.TclError:  # pragma: no cover
            pass
        self.bind("<Escape>", lambda _e: self._close())

        border = tk.Frame(self, bg="#0b1220", highlightthickness=1,
                          highlightbackground="#2b3b55")
        border.pack(fill="both", expand=True)
        top = tk.Frame(border, bg="#0b1220")
        top.pack(fill="x", padx=(12, 8), pady=(6, 0))
        self._topbar = top
        top.bind("<Button-1>", self._start_drag)
        top.bind("<B1-Motion>", self._drag)
        tk.Label(
            top, text="AI 答案", bg="#0b1220", fg="#93c5fd",
            font=font(10, bold=True), anchor="w",
        ).pack(side="left")
        self.rescan_label = tk.Label(
            top, text="再次识别", bg="#0b1220", fg="#7dd3fc",
            font=font(10), cursor="hand2", padx=4,
        )
        self.rescan_label.pack(side="left", padx=(10, 0))
        self.rescan_label.bind("<Button-1>", lambda _e: self._rescan())
        self.clear_label = tk.Label(
            top, text="删除记录", bg="#0b1220", fg="#fca5a5",
            font=font(10), cursor="hand2", padx=4,
        )
        self.clear_label.pack(side="left", padx=(8, 0))
        self.clear_label.bind("<Button-1>", lambda _e: self.clear_answers())
        self.toggle_frame_label = tk.Label(
            top, text="隐藏矩形框", bg="#0b1220", fg="#bae6fd",
            font=font(10), cursor="hand2", padx=4,
        )
        self.toggle_frame_label.pack(side="left", padx=(8, 0))
        self.toggle_frame_label.bind("<Button-1>", lambda _e: self._toggle_frame())
        self._close_label = tk.Label(
            top, text="×", bg="#0b1220", fg="#94a3b8",
            font=font(14, bold=True), cursor="hand2", padx=4,
        )
        self._close_label.pack(side="right")
        self._close_label.bind("<Button-1>", lambda _e: self._close())
        self.spinner = tk.Canvas(
            top, width=18, height=18, bg="#0b1220", highlightthickness=0, bd=0
        )
        self._spinner_arc = self.spinner.create_arc(
            3, 3, 15, 15, start=0, extent=270, style="arc",
            outline="#7dd3fc", width=2,
        )
        self.spinner.pack_forget()

        self.text = tk.Text(
            border, width=58, height=14, wrap="word",
            bg="#0b1220", fg="#e8eef8", insertbackground="#e8eef8",
            relief="flat", borderwidth=0, font=font(11),
            padx=14, pady=8, highlightthickness=0,
        )
        self.text.pack(fill="both", expand=True, padx=4, pady=(2, 10))
        self.text.tag_configure("answer", foreground="#22c55e")
        self.text.tag_configure("separator", foreground="#475569")
        self._insert_answers(str(text))
        self.text.configure(state="disabled")

        self.update_idletasks()
        width = max(520, min(760, self.winfo_reqwidth()))
        height = max(260, min(640, self.winfo_reqheight()))
        try:
            x = master.winfo_screenwidth() - width - 60
            y = 80
        except tk.TclError:  # pragma: no cover
            x = y = 80
        self.geometry(f"{width}x{height}+{x}+{y}")
        try:
            self.update_idletasks()
            winapi.set_exclude_from_capture(winapi.get_toplevel_hwnd(self))
        except Exception:  # noqa: BLE001
            pass
        self.after(50, self._focus)

    def _close(self) -> None:
        if callable(self._on_close):
            try:
                self._on_close()
            except Exception:  # noqa: BLE001
                pass
        self.destroy()

    def _rescan(self) -> None:
        if self._rescanning or not callable(self._on_rescan):
            return
        self._rescanning = True
        self.start_loading()
        self.rescan_label.configure(fg="#94a3b8")
        try:
            self._on_rescan()
        except Exception:  # noqa: BLE001
            self.finish_rescan()

    def start_loading(self) -> None:
        try:
            self.spinner.pack(side="right", padx=(0, 4), before=self._close_label)
        except tk.TclError:  # pragma: no cover
            return
        self._spin(0)

    def _spin(self, angle: int) -> None:
        if not self._rescanning:
            return
        try:
            self.spinner.itemconfigure(self._spinner_arc, start=angle)
            self._spin_job = self.after(70, lambda: self._spin((angle + 30) % 360))
        except tk.TclError:  # pragma: no cover
            pass

    def stop_loading(self) -> None:
        if self._spin_job is not None:
            try:
                self.after_cancel(self._spin_job)
            except tk.TclError:  # pragma: no cover
                pass
            self._spin_job = None
        try:
            self.spinner.pack_forget()
        except tk.TclError:  # pragma: no cover
            pass

    def _toggle_frame(self) -> None:
        if not callable(self._on_toggle_frame):
            return
        try:
            self._frame_visible = bool(self._on_toggle_frame())
        except Exception:  # noqa: BLE001
            return
        try:
            self.toggle_frame_label.configure(
                text="隐藏矩形框" if self._frame_visible else "显示矩形框"
            )
        except tk.TclError:  # pragma: no cover
            pass

    def finish_rescan(self) -> None:
        self._rescanning = False
        self.stop_loading()
        try:
            self.rescan_label.configure(text="再次识别", fg="#7dd3fc")
        except tk.TclError:  # pragma: no cover
            pass

    def _insert_answers(self, text: str, first: bool = True) -> None:
        """逐行插入；题号之间加分隔线，答案文字绿色。"""
        lines = str(text).splitlines()
        inserted_any = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                self.text.insert("end", "\n")
                continue
            if not first or inserted_any:
                if re.match(r"^\s*\d+\s*[.、]", line):
                    self.text.insert("end", "\n" + "─" * 30 + "\n", "separator")
            marker = re.search(r"(答案\s*[:：])", line)
            if marker:
                prefix = line[: marker.start()]
                answer = line[marker.start():]
                self.text.insert("end", prefix)
                self.text.insert("end", answer, "answer")
            elif stripped.startswith("答案"):
                self.text.insert("end", line, "answer")
            else:
                self.text.insert("end", line)
            self.text.insert("end", "\n")
            inserted_any = True

    def clear_answers(self) -> None:
        try:
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.configure(state="disabled")
        except tk.TclError:  # pragma: no cover
            pass

    def append_answer(self, text: str) -> None:
        value = str(text).strip()
        if not value:
            return
        try:
            self.text.configure(state="normal")
            current = self.text.get("1.0", "end-1c").rstrip()
            if current:
                self.text.insert("end", "\n\n" + "═" * 30 + "\n\n", "separator")
            self._insert_answers(value, first=True)
            self.text.configure(state="disabled")
            self.text.see("end")
            self.lift()
        except tk.TclError:  # pragma: no cover
            pass
        finally:
            self.finish_rescan()

    def _focus(self) -> None:
        try:
            self.lift()
            self.focus_force()
        except tk.TclError:  # pragma: no cover
            pass

    def _start_drag(self, event) -> None:
        if event.widget is not self._topbar:
            return
        self._drag_start = (event.x_root, event.y_root, self.winfo_x(), self.winfo_y())

    def _drag(self, event) -> None:
        start = getattr(self, "_drag_start", None)
        if not start:
            return
        sx, sy, wx, wy = start
        self.geometry(f"+{wx + event.x_root - sx}+{wy + event.y_root - sy}")

