"""Windows 底层能力封装（ctypes，纯标准库）。

包含：
- DPI 感知设置
- 屏幕抓取（GDI BitBlt → 32bpp BGRA，提供灰度 / RGB / 区域裁剪）
- 鼠标移动与点击（SendInput）
- 全局热键注册与消息循环原语（RegisterHotKey / GetMessage）
- 管理员权限检测、以管理员身份重启
- 窗口位置设置（用于全屏遮罩选择器）
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
BI_RGB = 0

# ---------------------------------------------------------------------------
# 基础结构体
# ---------------------------------------------------------------------------
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 1)]


# ---------------------------------------------------------------------------
# API 绑定
# ---------------------------------------------------------------------------
if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int

    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.StretchBlt.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    ]
    gdi32.StretchBlt.restype = wintypes.BOOL
    gdi32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.GetDeviceCaps.restype = ctypes.c_int

    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT

    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL

    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.GetMessageW.restype = ctypes.c_int
    user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostThreadMessageW.restype = wintypes.BOOL

    user32.SetProcessDPIAware.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    shell32.IsUserAnAdmin.restype = wintypes.BOOL
    shell32.ShellExecuteW.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_int,
    ]
    shell32.ShellExecuteW.restype = wintypes.HINSTANCE

    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        _HAS_DPI_CONTEXT = True
    except AttributeError:  # pragma: no cover
        _HAS_DPI_CONTEXT = False

    try:
        user32.GetDpiForSystem.restype = wintypes.UINT
        _HAS_GET_DPI = True
    except AttributeError:  # pragma: no cover
        _HAS_GET_DPI = False
else:  # pragma: no cover - 仅 Windows 支持
    user32 = gdi32 = kernel32 = shell32 = None
    _HAS_DPI_CONTEXT = _HAS_GET_DPI = False


# ---------------------------------------------------------------------------
# DPI
# ---------------------------------------------------------------------------
_DPI_SCALE: float | None = None


def enable_dpi_awareness() -> float:
    """设置 Per-Monitor-V2 DPI 感知（必须在创建 Tk 窗口之前调用）。

    返回系统缩放比例（如 1.5 表示 150%）。
    """
    global _DPI_SCALE
    if not IS_WINDOWS:
        _DPI_SCALE = 1.0
        return 1.0
    try:
        if _HAS_DPI_CONTEXT:
            # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        else:  # pragma: no cover
            user32.SetProcessDPIAware()
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    _DPI_SCALE = get_dpi_scale()
    return _DPI_SCALE


def get_dpi_scale() -> float:
    """获取主显示器缩放比例。"""
    global _DPI_SCALE
    if _DPI_SCALE is not None:
        return _DPI_SCALE
    if not IS_WINDOWS:  # pragma: no cover
        return 1.0
    try:
        dpi = user32.GetDpiForSystem() if _HAS_GET_DPI else 0
        if not dpi:
            dc = user32.GetDC(None)
            dpi = gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
            user32.ReleaseDC(None, dc)
        _DPI_SCALE = round(dpi / 96.0, 4) if dpi else 1.0
    except Exception:  # pragma: no cover
        _DPI_SCALE = 1.0
    return _DPI_SCALE


def virtual_screen() -> tuple[int, int, int, int]:
    """返回虚拟桌面 (x, y, width, height)，多显示器时为整个虚拟桌面。"""
    if not IS_WINDOWS:  # pragma: no cover
        return 0, 0, 1920, 1080
    return (
        user32.GetSystemMetrics(76),  # SM_XVIRTUALSCREEN
        user32.GetSystemMetrics(77),  # SM_YVIRTUALSCREEN
        user32.GetSystemMetrics(78),  # SM_CXVIRTUALSCREEN
        user32.GetSystemMetrics(79),  # SM_CYVIRTUALSCREEN
    )


# ---------------------------------------------------------------------------
# 屏幕抓取
# ---------------------------------------------------------------------------
class ScreenGrabber:
    """高性能全屏抓取器（复用同一个 DIB 缓冲区）。

    使用方式::

        sg = ScreenGrabber()
        sg.capture()
        gray = sg.gray()        # 绿通道灰度（长度 w*h），用于模板匹配
        rgb = sg.rgb()          # RGB（长度 w*h*3），用于预览
        crop = sg.crop_rgb(x, y, w, h)
        sg.close()

    坐标说明：``x, y`` 使用虚拟桌面坐标减去 virtual_screen() 的原点后的
    局部坐标（即 0 <= x < w）。
    """

    def __init__(self) -> None:
        if not IS_WINDOWS:  # pragma: no cover
            raise RuntimeError("屏幕抓取仅支持 Windows")
        self.vx, self.vy, self.vw, self.vh = virtual_screen()
        self.width = self.vw
        self.height = self.vh
        self.stride = self.width * 4
        self._raw = bytearray(self.stride * self.height)
        self._closed = False

        self._screen_dc = user32.GetDC(None)
        self._mem_dc = gdi32.CreateCompatibleDC(self._screen_dc)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self.width
        bmi.bmiHeader.biHeight = -self.height  # 负值 = 自顶向下的 DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        self._bits = ctypes.c_void_p()
        self._hbmp = gdi32.CreateDIBSection(
            self._mem_dc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(self._bits), None, 0
        )
        if not self._hbmp:
            raise OSError("创建屏幕抓取缓冲区失败")
        self._old_obj = gdi32.SelectObject(self._mem_dc, self._hbmp)

    # -- 抓取 ---------------------------------------------------------------
    def capture(self) -> None:
        if self._closed:
            raise RuntimeError("ScreenGrabber 已关闭")
        ok = gdi32.BitBlt(
            self._mem_dc,
            0,
            0,
            self.width,
            self.height,
            self._screen_dc,
            self.vx,
            self.vy,
            SRCCOPY | CAPTUREBLT,
        )
        if not ok:
            raise OSError("屏幕抓取失败（BitBlt 返回 0）")
        ctypes.memmove(
            (ctypes.c_char * len(self._raw)).from_buffer(self._raw),
            self._bits,
            len(self._raw),
        )

    # -- 转换 ---------------------------------------------------------------
    def gray(self) -> bytes:
        """返回全屏绿通道灰度字节（长度 width*height）。"""
        return bytes(self._raw[1::4])

    def rgb(self) -> bytes:
        """返回全屏 RGB 字节（长度 width*height*3）。"""
        width, height, raw = self.width, self.height, self._raw
        out = bytearray(width * height * 3)
        row_out = width * 3
        row_in = width * 4
        for y in range(height):
            src = y * row_in
            dst = y * row_out
            out[dst : dst + row_out : 3] = raw[src + 2 : src + row_in : 4]
            out[dst + 1 : dst + row_out : 3] = raw[src + 1 : src + row_in : 4]
            out[dst + 2 : dst + row_out : 3] = raw[src : src + row_in : 4]
        return bytes(out)

    def crop_rgb(self, x: int, y: int, w: int, h: int) -> bytes:
        """裁剪局部坐标区域并转换为 RGB。超出屏幕的部分会被裁剪。"""
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(self.width, int(x) + int(w))
        y1 = min(self.height, int(y) + int(h))
        if x1 <= x0 or y1 <= y0:
            raise ValueError("裁剪区域不合法")
        cw, ch = x1 - x0, y1 - y0
        out = bytearray(cw * ch * 3)
        row_out = cw * 3
        for r in range(ch):
            src = (y0 + r) * self.stride + x0 * 4
            dst = r * row_out
            out[dst : dst + row_out : 3] = self._raw[src + 2 : src + cw * 4 : 4]
            out[dst + 1 : dst + row_out : 3] = self._raw[src + 1 : src + cw * 4 : 4]
            out[dst + 2 : dst + row_out : 3] = self._raw[src : src + cw * 4 : 4]
        return bytes(out)

    def grab_rgb(self) -> tuple[bytes, int, int]:
        """抓一帧并返回 (rgb, width, height)。"""
        self.capture()
        return self.rgb(), self.width, self.height

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            gdi32.SelectObject(self._mem_dc, self._old_obj)
            gdi32.DeleteObject(self._hbmp)
            gdi32.DeleteDC(self._mem_dc)
            user32.ReleaseDC(None, self._screen_dc)
        except Exception:  # pragma: no cover
            pass

    def __enter__(self) -> "ScreenGrabber":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# 鼠标
# ---------------------------------------------------------------------------
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

WHEEL_DELTA = 120

BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def _send_mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> int:
    inp = INPUT()
    inp.type = 0  # INPUT_MOUSE
    inp.mi.dx = dx
    inp.mi.dy = dy
    inp.mi.mouseData = data & 0xFFFFFFFF
    inp.mi.dwFlags = flags
    inp.mi.time = 0
    inp.mi.dwExtraInfo = 0
    return int(user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)))


def get_cursor() -> tuple[int, int]:
    """获取鼠标当前位置（虚拟桌面绝对坐标）。"""
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def move_cursor(x: int, y: int) -> bool:
    """把鼠标移动到虚拟桌面绝对坐标 (x, y)。"""
    vx, vy, vw, vh = virtual_screen()
    ax = int(round((x - vx) * 65535 / max(vw - 1, 1)))
    ay = int(round((y - vy) * 65535 / max(vh - 1, 1)))
    ax = max(0, min(65535, ax))
    ay = max(0, min(65535, ay))
    return _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, ax, ay) == 1


def click_at(
    x: int,
    y: int,
    button: str = "left",
    double: bool = False,
    restore_cursor: bool = False,
    settle: float = 0.012,
) -> bool:
    """在 (x, y) 处点击鼠标。

    - button: left / right / middle
    - double: 是否双击
    - restore_cursor: 点击后把鼠标移回原位
    """
    if button not in BUTTON_FLAGS:
        raise ValueError(f"不支持的鼠标按键: {button}")
    down, up = BUTTON_FLAGS[button]
    old = get_cursor() if restore_cursor else None
    ok = move_cursor(x, y)
    if not ok:
        return False
    if settle:
        time.sleep(settle)
    _send_mouse(down)
    _send_mouse(up)
    if double:
        time.sleep(0.045)
        _send_mouse(down)
        _send_mouse(up)
    if old is not None:
        time.sleep(0.01)
        move_cursor(*old)
    return True


def scroll_wheel(
    steps: int,
    x: int | None = None,
    y: int | None = None,
    restore_cursor: bool = False,
) -> bool:
    """鼠标滚轮滚动。

    - steps > 0：向下滚动（页面内容向下翻）；
    - steps < 0：向上滚动；
    - x / y：滚轮位置（绝对屏幕坐标）；省略则在当前位置滚动。
    """
    if not IS_WINDOWS:  # pragma: no cover
        return False
    old = get_cursor() if (restore_cursor or (x is not None and y is not None)) else None
    if x is not None and y is not None:
        if not move_cursor(int(x), int(y)):
            return False
        time.sleep(0.02)
    delta = WHEEL_DELTA if steps < 0 else -WHEEL_DELTA
    try:
        for _ in range(abs(int(steps))):
            _send_mouse(MOUSEEVENTF_WHEEL, data=delta)
            time.sleep(0.02)
    finally:
        if restore_cursor and old is not None:
            time.sleep(0.01)
            move_cursor(*old)
    return True


# ---------------------------------------------------------------------------
# 键盘（用于填空题输入答案）
# ---------------------------------------------------------------------------
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL = 0x11
VK_A = 0x41
VK_V = 0x56
VK_RETURN = 0x0D
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _send_keyboard(scan: int = 0, vk: int = 0, flags: int = 0) -> int:
    """发送一个键盘输入事件（SendInput）。"""
    if not IS_WINDOWS:  # pragma: no cover
        return 0
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = vk
    inp.ki.wScan = scan
    inp.ki.dwFlags = flags
    inp.ki.time = 0
    inp.ki.dwExtraInfo = 0
    return int(user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)))


def _tap_key(vk: int) -> None:
    _send_keyboard(vk=vk, flags=0)
    time.sleep(0.01)
    _send_keyboard(vk=vk, flags=KEYEVENTF_KEYUP)
    time.sleep(0.01)


def _ctrl_key(vk: int) -> None:
    """发送 Ctrl + 指定按键（如 Ctrl+A / Ctrl+V）。"""
    _send_keyboard(vk=VK_CONTROL, flags=0)
    time.sleep(0.01)
    _tap_key(vk)
    _send_keyboard(vk=VK_CONTROL, flags=KEYEVENTF_KEYUP)
    time.sleep(0.02)


def select_all() -> bool:
    """向当前焦点窗口发送 Ctrl+A。"""
    if not IS_WINDOWS:  # pragma: no cover
        return False
    try:
        _ctrl_key(VK_A)
        return True
    except Exception:  # pragma: no cover
        return False


def get_clipboard_text() -> str | None:
    """读取当前剪贴板文本（失败返回 None）。"""
    if not IS_WINDOWS:  # pragma: no cover
        return None
    try:
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.GetClipboardData.restype = wintypes.HANDLE
        user32.OpenClipboard(None)
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            kernel32.GlobalLock.restype = ctypes.c_void_p
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception:  # pragma: no cover
        return None


def set_clipboard_text(text: str) -> bool:
    """把文本写入剪贴板（Unicode）。"""
    if not IS_WINDOWS:  # pragma: no cover
        return False
    try:
        data = str(text)
        buffer = ctypes.create_unicode_buffer(data)
        size = ctypes.sizeof(buffer)
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(ptr, buffer, size)
        finally:
            kernel32.GlobalUnlock(handle)
        user32.OpenClipboard(None)
        try:
            user32.EmptyClipboard()
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)
                return False
        finally:
            user32.CloseClipboard()
        return True
    except Exception:  # pragma: no cover
        return False


def type_unicode_text(
    text: str,
    interval: float = 0.008,
    should_stop=None,
    on_progress=None,
) -> bool:
    """逐字符输入文本（不依赖剪贴板）。

    should_stop: 可选回调，返回 True 时立即停止；
    on_progress: 可选回调 ``on_progress(已输入字符数, 总字符数)``。
    """
    if not IS_WINDOWS:  # pragma: no cover
        return False
    total = len(str(text))
    typed = 0
    try:
        for char in str(text):
            if should_stop is not None and should_stop():
                return False
            if char == "\n":
                _tap_key(VK_RETURN)
            else:
                encoded = char.encode("utf-16-le")
                codes = [
                    int.from_bytes(encoded[i : i + 2], "little")
                    for i in range(0, len(encoded), 2)
                ]
                for code in codes:
                    _send_keyboard(scan=code, flags=KEYEVENTF_UNICODE)
                    _send_keyboard(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
                    time.sleep(interval)
            typed += 1
            if on_progress is not None:
                on_progress(typed, total)
        return True
    except Exception:  # pragma: no cover
        return False


def paste_text(text: str, restore_clipboard: bool = True) -> bool:
    """向当前焦点控件粘贴文本（优先剪贴板，失败回退逐字输入）。"""
    if not IS_WINDOWS:  # pragma: no cover
        return False
    old = get_clipboard_text() if restore_clipboard else None
    if set_clipboard_text(text):
        _ctrl_key(VK_V)
        time.sleep(0.12)
        if restore_clipboard and old is not None:
            time.sleep(0.05)
            set_clipboard_text(old)
        return True
    return type_unicode_text(text)


def fill_textbox(text: str) -> bool:
    """把文本填入当前焦点文本框：先全选，再粘贴/输入替换。"""
    select_all()
    return paste_text(text, restore_clipboard=True)


def fill_textbox_typing(text: str) -> bool:
    """把文本逐字键入当前焦点文本框（不经过剪贴板）。

    适合禁止粘贴、但允许键盘输入的页面/输入框。
    """
    select_all()
    time.sleep(0.03)
    return type_unicode_text(text)


# ---------------------------------------------------------------------------
# 全局热键
# ---------------------------------------------------------------------------
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

VK_F6 = 0x75
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78


def create_message_queue() -> None:
    """强制为当前线程创建消息队列（注册热键前调用更稳妥）。"""
    msg = MSG()
    user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)


def register_hotkey(hotkey_id: int, vk: int, modifiers: int = 0) -> bool:
    return bool(user32.RegisterHotKey(None, int(hotkey_id), int(modifiers), int(vk)))


def unregister_hotkey(hotkey_id: int) -> None:
    try:
        user32.UnregisterHotKey(None, int(hotkey_id))
    except Exception:  # pragma: no cover
        pass


def get_message(msg: MSG) -> int:
    """阻塞读取一条消息；返回 0 表示收到 WM_QUIT，-1 表示错误。"""
    return int(user32.GetMessageW(ctypes.byref(msg), None, 0, 0))


def post_thread_quit(thread_id: int) -> None:
    user32.PostThreadMessageW(wintypes.DWORD(thread_id), WM_QUIT, 0, 0)


def get_current_thread_id() -> int:
    return int(kernel32.GetCurrentThreadId())


# ---------------------------------------------------------------------------
# 窗口 / 权限
# ---------------------------------------------------------------------------
HWND_TOPMOST = wintypes.HWND(-1)
SWP_SHOWWINDOW = 0x0040
SWP_NOACTIVATE = 0x0010
GA_ROOT = 2


def get_toplevel_hwnd(tk_window) -> int:
    """把 tkinter 窗口对象转换成顶层 HWND。"""
    try:
        hwnd = int(tk_window.winfo_id())
    except Exception:  # pragma: no cover
        return 0
    root = user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT)
    return int(root) if root else hwnd


def place_window(hwnd: int, x: int, y: int, w: int, h: int, topmost: bool = True) -> bool:
    """设置窗口的绝对位置与大小（用于多显示器/全屏遮罩）。"""
    flags = SWP_SHOWWINDOW | SWP_NOACTIVATE
    return bool(
        user32.SetWindowPos(
            wintypes.HWND(hwnd),
            HWND_TOPMOST if topmost else wintypes.HWND(0),
            int(x),
            int(y),
            int(w),
            int(h),
            flags,
        )
    )


def is_admin() -> bool:
    if not IS_WINDOWS:  # pragma: no cover
        return False
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover
        return False


def relaunch_as_admin() -> bool:
    """以管理员身份重新启动本程序（会弹出 UAC）。成功发起返回 True。"""
    if not IS_WINDOWS:  # pragma: no cover
        return False
    try:
        if getattr(sys, "frozen", False):
            args = sys.argv[1:]
            target = sys.executable
        else:
            args = sys.argv
            target = sys.executable
        params = subprocess.list2cmdline(args)
        result = shell32.ShellExecuteW(None, "runas", target, params, None, 1)
        return int(result) > 32
    except Exception:  # pragma: no cover
        return False


def set_app_user_model_id(app_id: str) -> None:
    """设置 AppUserModelID，让任务栏使用本程序的图标。"""
    if not IS_WINDOWS:  # pragma: no cover
        return
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
        shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    except Exception:
        pass


def get_foreground_window_title() -> str:
    if not IS_WINDOWS:  # pragma: no cover
        return ""
    try:
        hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value
    except Exception:  # pragma: no cover
        return ""


def enable_dark_titlebar(hwnd: int, caption_bgr: int = 0x000B0F16) -> bool:
    """让窗口标题栏使用深色（Windows 10 1809+ / Windows 11）。

    caption_bgr：标题栏颜色（COLORREF，0x00BBGGRR），默认 #0b0f16。
    """
    if not IS_WINDOWS or not hwnd:  # pragma: no cover
        return False

    def _set(dwm, attr: int, value: int) -> bool:
        try:
            data = ctypes.c_int(value)
            result = dwm.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), ctypes.c_uint(attr), ctypes.byref(data), ctypes.sizeof(data)
            )
            return result == 0
        except Exception:
            return False

    try:
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:  # pragma: no cover
        return False
    ok = _set(dwm, 20, 1)  # DWMWA_USE_IMMERSIVE_DARK_MODE
    if not ok:
        ok = _set(dwm, 19, 1)  # 旧版 Windows 10
    # Windows 11：自定义标题栏 / 边框颜色
    _set(dwm, 35, caption_bgr)  # DWMWA_CAPTION_COLOR
    _set(dwm, 36, 0x00E8EEF8)  # DWMWA_TEXT_COLOR
    _set(dwm, 34, 0x00232C3D)  # DWMWA_BORDER_COLOR
    return ok


GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080


def set_exclude_from_capture(hwnd: int) -> bool:
    """让窗口在屏幕截图/录屏中不可见（Windows 10 2004+）。"""
    if not IS_WINDOWS or not hwnd:  # pragma: no cover
        return False
    try:
        # WDA_EXCLUDEFROMCAPTURE = 0x00000011
        return bool(user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), 0x11))
    except Exception:  # pragma: no cover
        return False


def set_click_through(hwnd: int, enabled: bool = True) -> bool:
    """让窗口鼠标穿透且不抢焦点（用于屏幕上的选区框 HUD）。"""
    if not IS_WINDOWS or not hwnd:  # pragma: no cover
        return False
    try:
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        style = int(user32.GetWindowLongPtrW(wintypes.HWND(hwnd), GWL_EXSTYLE))
        flags = WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        if enabled:
            flags |= WS_EX_TRANSPARENT
            style |= flags
        else:
            style = (style | flags) & ~WS_EX_TRANSPARENT
        user32.SetWindowLongPtrW(wintypes.HWND(hwnd), GWL_EXSTYLE, style)
        return True
    except Exception:  # pragma: no cover
        return False
