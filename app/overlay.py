"""全屏遮罩式取点工具：坐标拾取器 / 屏幕区域框选器。

全部使用 Tk 的 Toplevel + Win32 SetWindowPos 覆盖整个虚拟桌面。
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import png, winapi
from .paths import temp_png_path

# 遮罩界面用色（与主界面主题保持一致）
_BG = "#070a10"
_ACCENT = "#3d8bfd"
_CYAN = "#22d3ee"
_TEXT = "#e8eef8"
_MUTED = "#93a0b5"
_DANGER = "#f87171"


def _place_fullscreen(window: tk.Toplevel) -> tuple[int, int, int, int]:
    """把窗口铺满虚拟桌面，返回虚拟桌面信息。"""
    vx, vy, vw, vh = winapi.virtual_screen()
    window.update_idletasks()
    hwnd = winapi.get_toplevel_hwnd(window)
    if hwnd:
        winapi.place_window(hwnd, vx, vy, vw, vh, topmost=True)
    return vx, vy, vw, vh


class CoordinatePicker:
    """坐标拾取器：移动鼠标到目标位置后点击确认（或 3 秒后自动记录）。"""

    def __init__(
        self,
        master: tk.Misc,
        on_done: Callable[[int, int], None],
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self._on_done = on_done
        self._on_cancel = on_cancel
        self._closed = False
        self._count = 3
        self._vx, self._vy, self._vw, self._vh = winapi.virtual_screen()

        self.window = tk.Toplevel(master)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        try:
            self.window.attributes("-alpha", 0.28)
        except tk.TclError:  # pragma: no cover
            pass
        self.window.configure(bg="black")
        self.window.bind("<Escape>", lambda _e: self._cancel())
        self.window.bind("<Button-1>", self._confirm_event)
        self.window.bind("<Motion>", self._on_motion)

        self.canvas = tk.Canvas(
            self.window, bg="black", highlightthickness=0, cursor="crosshair"
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Escape>", lambda _e: self._cancel())
        self.canvas.bind("<Button-1>", self._confirm_event)
        self.canvas.bind("<Motion>", self._on_motion)

        vx, vy, vw, vh = _place_fullscreen(self.window)
        self._vx, self._vy = vx, vy
        center_x, center_y = vw // 2, vh // 2
        self._hint = self.canvas.create_text(
            center_x,
            center_y - 40,
            text="移动鼠标到目标位置，点击左键确认",
            fill=_TEXT,
            font=("Microsoft YaHei UI", 22),
        )
        self._countdown = self.canvas.create_text(
            center_x,
            center_y + 40,
            text="3",
            fill=_CYAN,
            font=("Microsoft YaHei UI", 40, "bold"),
        )
        self._coords = self.canvas.create_text(
            center_x,
            center_y + 110,
            text="按 ESC 取消（3 秒后自动记录当前位置）",
            fill=_MUTED,
            font=("Microsoft YaHei UI", 13),
        )
        self._cross_h = self.canvas.create_line(0, 0, 0, 0, fill=_ACCENT, dash=(6, 6))
        self._cross_v = self.canvas.create_line(0, 0, 0, 0, fill=_ACCENT, dash=(6, 6))
        self.window.focus_force()
        self._tick()

    # -- 交互 -------------------------------------------------------------
    def _on_motion(self, event: tk.Event) -> None:
        x = event.x_root - self._vx
        y = event.y_root - self._vy
        self.canvas.coords(self._cross_h, 0, y, self._vw, y)
        self.canvas.coords(self._cross_v, x, 0, x, self._vh)
        self.canvas.itemconfigure(
            self._coords, text=f"当前位置：({event.x_root}, {event.y_root})   ·   ESC 取消"
        )

    def _confirm_event(self, event: tk.Event) -> None:
        self._finish(int(event.x_root), int(event.y_root))

    def _tick(self) -> None:
        if self._closed:
            return
        self._count -= 1
        if self._count <= 0:
            x, y = winapi.get_cursor()
            self._finish(int(x), int(y))
            return
        try:
            self.canvas.itemconfigure(self._countdown, text=str(self._count))
        except tk.TclError:  # pragma: no cover
            return
        self.window.after(1000, self._tick)

    def _finish(self, x: int, y: int) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.window.destroy()
        except tk.TclError:  # pragma: no cover
            pass
        if callable(self._on_done):
            self._on_done(x, y)

    def _cancel(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.window.destroy()
        except tk.TclError:  # pragma: no cover
            pass
        if callable(self._on_cancel):
            self._on_cancel()


class RegionPicker:
    """屏幕区域框选器：全屏截图后拖动鼠标框选一个矩形区域。

    回调返回虚拟桌面绝对坐标 ``(x, y, w, h)``。
    """

    def __init__(
        self,
        master: tk.Misc,
        on_done: Callable[[tuple[int, int, int, int]], None],
        on_cancel: Callable[[], None] | None = None,
        hint: str = "按住鼠标左键拖动，框选要识别的区域",
        resizable: bool = False,
        initial_region: tuple[int, int, int, int] | None = None,
    ) -> None:
        self._on_done = on_done
        self._on_cancel = on_cancel
        self._closed = False
        self._start: tuple[int, int] | None = None
        self._resizable = bool(resizable)
        self._selection: tuple[int, int, int, int] | None = None
        self._resize_mode: str | None = None
        self._resize_origin: tuple[int, int, int, int] | None = None
        self._move_start: tuple[int, int, int, int] | None = None
        self._temp_path = None
        self._photo = None

        self.window = tk.Toplevel(master)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=_BG)
        self.window.bind("<Escape>", lambda _e: self._cancel())
        self.window.bind("<Button-3>", lambda _e: self._cancel())

        self.canvas = tk.Canvas(self.window, bg=_BG, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        # 先截屏，再铺满全屏（截图里不会包含遮罩本身）
        try:
            with winapi.ScreenGrabber() as grabber:
                grabber.capture()
                rgb = grabber.rgb()
                screenshot_w, screenshot_h = grabber.width, grabber.height
                self._vx, self._vy = grabber.vx, grabber.vy
        except Exception:  # pragma: no cover - 截图失败时仍允许框选
            rgb = None
            vx, vy, vw, vh = winapi.virtual_screen()
            screenshot_w, screenshot_h = vw, vh
            self._vx, self._vy = vx, vy

        if rgb is not None:
            self._temp_path = temp_png_path("region")
            try:
                png.save_png(self._temp_path, screenshot_w, screenshot_h, rgb)
                self._photo = tk.PhotoImage(file=str(self._temp_path), master=self.window)
            except Exception:  # pragma: no cover
                self._photo = None
            finally:
                try:
                    if self._temp_path is not None:
                        self._temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        vx, vy, vw, vh = _place_fullscreen(self.window)
        self._vw, self._vh = vw, vh
        scale = winapi.get_dpi_scale()
        self._photo_offset = (0, 0)
        if self._photo is not None:
            self._photo_offset = ((vw - self._photo.width()) // 2, (vh - self._photo.height()) // 2)
            self.canvas.create_image(
                self._photo_offset[0], self._photo_offset[1], image=self._photo, anchor="nw"
            )

        # 顶部提示条（深色实底，保证任何背景下都清晰）
        banner_h = int(round(84 * scale))
        self.canvas.create_rectangle(0, 0, vw, banner_h, fill="#05070b", outline="")
        self.canvas.create_line(0, banner_h, vw, banner_h, fill=_ACCENT, width=1)
        self.canvas.create_text(
            int(28 * scale),
            int(13 * scale),
            anchor="nw",
            text="框选识别区域",
            fill=_TEXT,
            font=("Microsoft YaHei UI", 15, "bold"),
        )
        self.canvas.create_text(
            int(28 * scale),
            int(48 * scale),
            anchor="nw",
            text=(
                hint
                + ("    ·    拖动边缘调整大小，Enter/双击确认" if resizable else "")
                + "    ·    ESC / 右键取消"
            ),
            fill=_MUTED,
            font=("Microsoft YaHei UI", 10),
        )
        self._size_text = self.canvas.create_text(
            vw - int(28 * scale),
            int(30 * scale),
            anchor="ne",
            text="",
            fill=_CYAN,
            font=("Consolas", 13),
        )
        self._rect = self.canvas.create_rectangle(
            0, 0, 0, 0, outline=_CYAN, width=2, dash=(6, 4)
        )
        self._cross_h = self.canvas.create_line(0, 0, 0, 0, fill=_CYAN, dash=(3, 5))
        self._cross_v = self.canvas.create_line(0, 0, 0, 0, fill=_CYAN, dash=(3, 5))

        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Escape>", lambda _e: self._cancel())
        self.window.bind("<Escape>", lambda _e: self._cancel())
        self.window.bind("<Return>", lambda _e: self._confirm())
        self.window.bind("<KP_Enter>", lambda _e: self._confirm())
        self.window.focus_force()
        self.canvas.focus_set()
        if initial_region is not None and resizable:
            x, y, w, h = (int(v) for v in initial_region)
            if w >= 8 and h >= 8:
                self._selection = (x, y, w, h)
                self._draw_selection(self._selection)
                self.canvas.itemconfigure(
                    self._size_text,
                    text=f"选区 {w} × {h} px · 拖边缘调整，Enter/双击确认",
                )

    # -- 交互 -------------------------------------------------------------
    def _event_abs(self, event: tk.Event) -> tuple[int, int]:
        return int(event.x_root), int(event.y_root)

    def _on_motion(self, event: tk.Event) -> None:
        if self._photo is not None:
            lx = event.x - self._photo_offset[0]
            ly = event.y - self._photo_offset[1]
            self.canvas.coords(self._cross_h, 0, ly, self._vw, ly)
            self.canvas.coords(self._cross_v, lx, 0, lx, self._vh)

    def _mode_at(self, x: int, y: int) -> str | None:
        if self._selection is None:
            return None
        left, top, width, height = self._selection
        right, bottom = left + width, top + height
        margin = 14
        near_left = abs(x - left) <= margin
        near_right = abs(x - right) <= margin
        near_top = abs(y - top) <= margin
        near_bottom = abs(y - bottom) <= margin
        inside_x = left <= x <= right
        inside_y = top <= y <= bottom
        if near_left and near_top:
            return "tl"
        if near_right and near_top:
            return "tr"
        if near_left and near_bottom:
            return "bl"
        if near_right and near_bottom:
            return "br"
        if near_left and inside_y:
            return "l"
        if near_right and inside_y:
            return "r"
        if near_top and inside_x:
            return "t"
        if near_bottom and inside_x:
            return "b"
        if inside_x and inside_y:
            return "move"
        return None

    def _draw_selection(self, selection: tuple[int, int, int, int]) -> None:
        left, top, width, height = selection
        lx, ly = left - self._vx, top - self._vy
        self.canvas.coords(self._rect, lx, ly, lx + width, ly + height)
        self.canvas.itemconfigure(self._size_text, text=f"选区 {width} × {height} px")

    def _on_double_click(self, _event: tk.Event) -> None:
        if self._resizable and self._selection is not None:
            self._confirm()

    def _on_press(self, event: tk.Event) -> None:
        x, y = self._event_abs(event)
        if self._resizable and self._selection is not None:
            mode = self._mode_at(x, y)
            if mode is not None:
                self._resize_mode = mode
                self._resize_origin = self._selection
                self._move_start = (x, y)
                return
        self._start = (x, y)
        self._selection = None
        self.canvas.coords(self._rect, event.x, event.y, event.x, event.y)

    def _on_drag(self, event: tk.Event) -> None:
        x, y = self._event_abs(event)
        if self._resize_mode and self._resize_origin is not None:
            left, top, width, height = self._resize_origin
            right, bottom = left + width, top + height
            if self._resize_mode == "move" and self._move_start is not None:
                dx = x - self._move_start[0]
                dy = y - self._move_start[1]
                candidate = (left + dx, top + dy, width, height)
            else:
                if self._resize_mode in ("l", "tl", "bl"):
                    left = x
                if self._resize_mode in ("r", "tr", "br"):
                    right = x
                if self._resize_mode in ("t", "tl", "tr"):
                    top = y
                if self._resize_mode in ("b", "bl", "br"):
                    bottom = y
                if right < left:
                    left, right = right, left
                if bottom < top:
                    top, bottom = bottom, top
                candidate = (left, top, right - left, bottom - top)
            if candidate[2] >= 8 and candidate[3] >= 8:
                self._selection = candidate
                self._draw_selection(candidate)
            return
        if self._start is None:
            return
        sx, sy = self._start
        cx = event.x_root - self._vx
        cy = event.y_root - self._vy
        sx_local = sx - self._vx
        sy_local = sy - self._vy
        self.canvas.coords(self._rect, sx_local, sy_local, cx, cy)
        w = abs(cx - sx_local)
        h = abs(cy - sy_local)
        self.canvas.itemconfigure(self._size_text, text=f"选区 {w} × {h} px")

    def _on_release(self, event: tk.Event) -> None:
        if self._resize_mode:
            self._resize_mode = None
            self._resize_origin = None
            self._move_start = None
            return
        if self._start is None:
            return
        x0, y0 = self._start
        x1, y1 = self._event_abs(event)
        self._start = None
        left, top = min(x0, x1), min(y0, y1)
        width, height = abs(x1 - x0), abs(y1 - y0)
        if width < 8 or height < 8:
            return
        selection = (left, top, width, height)
        if self._resizable:
            self._selection = selection
            self._draw_selection(selection)
            self.canvas.itemconfigure(
                self._size_text,
                text=f"选区 {width} × {height} px · 可拖边缘调整，Enter/双击确认",
            )
            return
        self._finish(selection)

    def _confirm(self) -> None:
        if self._selection is None:
            return
        self._finish(self._selection)

    def _finish(self, region: tuple[int, int, int, int]) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.window.destroy()
        except tk.TclError:  # pragma: no cover
            pass
        if callable(self._on_done):
            self._on_done(region)

    def _cancel(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.window.destroy()
        except tk.TclError:  # pragma: no cover
            pass
        if callable(self._on_cancel):
            self._on_cancel()


# 透明色键（该颜色会被 Windows 视为完全透明）
_HUD_KEY = "#010203"


class EditableRegionFrame:
    """置顶、可拖动边缘的区域框（只画边框，不遮挡框内点击）。

    用 4 条边 + 4 个角组成 8 个小窗口，拖动边缘即可实时改变选区。
    """

    EDGE = 7
    CORNER = 14

    def __init__(self, master: tk.Misc, on_change=None, color: str = "#22c55e", alpha: float = 0.40) -> None:
        self._master = master
        self._on_change = on_change
        self._color = color
        self._alpha = alpha
        self._region: tuple[int, int, int, int] | None = None
        self._visible = False
        self._drag_mode: str | None = None
        self._drag_start_mouse: tuple[int, int] | None = None
        self._drag_start_region: tuple[int, int, int, int] | None = None
        self._windows: dict[str, tk.Toplevel] = {
            name: self._make_window(cursor)
            for name, cursor in (
                ("top", "sb_v_double_arrow"),
                ("bottom", "sb_v_double_arrow"),
                ("left", "sb_h_double_arrow"),
                ("right", "sb_h_double_arrow"),
                ("tl", "size_nw_se"),
                ("tr", "size_ne_sw"),
                ("bl", "size_ne_sw"),
                ("br", "size_nw_se"),
            )
        }

    def _make_window(self, cursor: str) -> tk.Toplevel:
        win = tk.Toplevel(self._master)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", self._alpha)
        except tk.TclError:  # pragma: no cover
            pass
        win.configure(bg=self._color)
        win.withdraw()
        win.configure(cursor=cursor)
        try:
            win.update_idletasks()
            winapi.set_exclude_from_capture(winapi.get_toplevel_hwnd(win))
        except Exception:  # noqa: BLE001
            pass
        win.bind("<Button-1>", lambda event, w=win: self._on_press(event, w))
        win.bind("<B1-Motion>", lambda event, w=win: self._on_drag(event, w))
        win.bind("<ButtonRelease-1>", lambda _event: self._on_release())
        return win

    def show(self, region: tuple[int, int, int, int] | None) -> None:
        if not region:
            self.hide()
            return
        self._region = tuple(int(v) for v in region)
        self._visible = True
        self._layout()
        for win in self._windows.values():
            try:
                win.deiconify()
                win.update_idletasks()
                winapi.set_exclude_from_capture(winapi.get_toplevel_hwnd(win))
                win.lift()
                win.attributes("-topmost", True)
            except tk.TclError:  # pragma: no cover
                pass

    def hide(self) -> None:
        self._visible = False
        for win in self._windows.values():
            try:
                win.withdraw()
            except tk.TclError:  # pragma: no cover
                pass

    def destroy(self) -> None:
        self._visible = False
        for win in self._windows.values():
            try:
                win.destroy()
            except tk.TclError:  # pragma: no cover
                pass
        self._windows.clear()

    def is_visible(self) -> bool:
        return self._visible

    def _layout(self) -> None:
        if self._region is None:
            return
        x, y, w, h = self._region
        x2, y2 = x + w, y + h
        e = self.EDGE
        c = self.CORNER
        geometry = {
            "top": (x, y, w, e),
            "bottom": (x, y2 - e, w, e),
            "left": (x, y, e, h),
            "right": (x2 - e, y, e, h),
            "tl": (x, y, c, c),
            "tr": (x2 - c, y, c, c),
            "bl": (x, y2 - c, c, c),
            "br": (x2 - c, y2 - c, c, c),
        }
        for name, (gx, gy, gw, gh) in geometry.items():
            win = self._windows.get(name)
            if win is None:
                continue
            try:
                win.geometry(f"{max(1, gw)}x{max(1, gh)}+{gx}+{gy}")
            except tk.TclError:  # pragma: no cover
                pass

    @staticmethod
    def _mode_for(name: str) -> str:
        return {"top": "t", "bottom": "b", "left": "l", "right": "r"}.get(name, name)

    def _on_press(self, event: tk.Event, win: tk.Toplevel) -> None:
        name = next((key for key, item in self._windows.items() if item is win), None)
        if name is None or self._region is None:
            return
        self._drag_mode = self._mode_for(name)
        self._drag_start_mouse = (int(event.x_root), int(event.y_root))
        self._drag_start_region = self._region

    def _on_drag(self, event: tk.Event, _win: tk.Toplevel) -> None:
        if (
            self._drag_mode is None
            or self._drag_start_mouse is None
            or self._drag_start_region is None
        ):
            return
        dx = int(event.x_root) - self._drag_start_mouse[0]
        dy = int(event.y_root) - self._drag_start_mouse[1]
        x, y, w, h = self._drag_start_region
        left, top, right, bottom = x, y, x + w, y + h
        mode = self._drag_mode
        if "l" in mode:
            left += dx
        if "r" in mode:
            right += dx
        if "t" in mode:
            top += dy
        if "b" in mode:
            bottom += dy
        if right - left < 24:
            if "l" in mode:
                left = right - 24
            else:
                right = left + 24
        if bottom - top < 24:
            if "t" in mode:
                top = bottom - 24
            else:
                bottom = top + 24
        self._region = (left, top, right - left, bottom - top)
        self._layout()
        if callable(self._on_change):
            self._on_change(self._region)

    def _on_release(self) -> None:
        self._drag_mode = None
        self._drag_start_mouse = None
        self._drag_start_region = None


class RegionHUD:
    """在屏幕上持续显示一个选区矩形框（置顶、鼠标穿透、可显示/隐藏）。

    用于让用户随时看到"题目区域 / 搜索区域"框在哪里。
    """

    def __init__(self, master: tk.Misc) -> None:
        self._visible = False
        self._region: tuple[int, int, int, int] | None = None
        self._label = ""
        self._color = _CYAN
        self._scale = winapi.get_dpi_scale()
        self._vx, self._vy, self._vw, self._vh = winapi.virtual_screen()

        self.window = tk.Toplevel(master)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        try:
            self.window.attributes("-transparentcolor", _HUD_KEY)
        except tk.TclError:  # pragma: no cover
            pass
        self.window.configure(bg=_HUD_KEY)
        self.canvas = tk.Canvas(
            self.window, bg=_HUD_KEY, highlightthickness=0, bd=0, takefocus=0
        )
        self.canvas.pack(fill="both", expand=True)
        self.window.update_idletasks()
        hwnd = winapi.get_toplevel_hwnd(self.window)
        if hwnd:
            winapi.place_window(hwnd, self._vx, self._vy, self._vw, self._vh, topmost=True)
            winapi.set_click_through(hwnd, True)
        self.window.withdraw()

    # ------------------------------------------------------------------
    def show(
        self,
        region: tuple[int, int, int, int] | None,
        label: str = "",
        color: str = _CYAN,
    ) -> None:
        if not region:
            self.hide()
            return
        x, y, w, h = (int(v) for v in region)
        self._region = (x - self._vx, y - self._vy, w, h)
        self._label = str(label or "")
        self._color = color
        self._redraw()
        try:
            self.window.deiconify()
            self.window.lift()
            self.window.attributes("-topmost", True)
        except tk.TclError:  # pragma: no cover
            return
        self._visible = True

    def hide(self) -> None:
        try:
            self.window.withdraw()
        except tk.TclError:  # pragma: no cover
            pass
        self._visible = False

    def is_visible(self) -> bool:
        return self._visible

    def destroy(self) -> None:
        try:
            self.window.destroy()
        except tk.TclError:  # pragma: no cover
            pass
        self._visible = False

    # ------------------------------------------------------------------
    def _redraw(self) -> None:
        try:
            self.canvas.delete("all")
        except tk.TclError:  # pragma: no cover
            return
        if self._region is None:
            return
        lx, ly, w, h = self._region
        s = self._scale
        width = max(1, int(round(2 * s)))
        dash = (max(3, int(round(6 * s))), max(2, int(round(4 * s))))
        self.canvas.create_rectangle(
            lx, ly, lx + w, ly + h,
            outline=self._color, width=width, dash=dash,
        )
        corner = int(round(20 * s))
        corner_w = max(2, int(round(3 * s)))
        for cx, cy, dx, dy in (
            (lx, ly, 1, 1),
            (lx + w, ly, -1, 1),
            (lx, ly + h, 1, -1),
            (lx + w, ly + h, -1, -1),
        ):
            self.canvas.create_line(cx, cy, cx + dx * corner, cy,
                                    fill=self._color, width=corner_w)
            self.canvas.create_line(cx, cy, cx, cy + dy * corner,
                                    fill=self._color, width=corner_w)
        if self._label:
            gap = int(round(6 * s))
            pad_x = int(round(8 * s))
            pad_y = int(round(4 * s))
            font = ("Microsoft YaHei UI", 9, "bold")
            tx = lx + gap
            ty = ly - int(round(30 * s))
            if ty < 4:
                ty = ly + gap
            text_id = self.canvas.create_text(
                tx + pad_x, ty + pad_y, text=self._label,
                fill=_TEXT, font=font, anchor="nw",
            )
            bbox = self.canvas.bbox(text_id)
            if bbox:
                chip = self.canvas.create_rectangle(
                    bbox[0] - pad_x, bbox[1] - pad_y,
                    bbox[2] + pad_x, bbox[3] + pad_y,
                    fill="#0b0f16", outline=self._color, width=1,
                )
                self.canvas.tag_lower(chip, text_id)
