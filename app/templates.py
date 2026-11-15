"""模板图片的加载、保存与管理。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from . import png
from .matcher import TemplateQuality, check_template
from .paths import TEMPLATES_DIR


@dataclass
class Template:
    path: str
    width: int
    height: int
    rgb: bytes
    gray: bytes
    quality: TemplateQuality

    def display_name(self) -> str:
        return Path(self.path).name


def template_from_rgb(path: str, rgb: bytes, width: int, height: int) -> Template:
    """由 RGB 数据构造模板对象（灰度取绿通道，与屏幕抓取保持一致）。"""
    if len(rgb) < width * height * 3:
        raise ValueError("模板数据长度不正确")
    gray = bytes(rgb[1::3])  # 与 ScreenGrabber.gray() 一致：取绿通道
    return Template(
        path=str(path),
        width=width,
        height=height,
        rgb=rgb,
        gray=gray,
        quality=check_template(gray, width, height),
    )


def save_template(rgb: bytes, width: int, height: int, prefix: str = "tpl") -> Path:
    """把 RGB 数据保存为模板 PNG，返回文件路径。"""
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.png"
    path = TEMPLATES_DIR / name
    png.save_png(path, width, height, rgb)
    return path


def _load_with_tk(path: str, tk_root) -> tuple[int, int, bytes]:
    """用 Tk 的 PhotoImage 读取图片（支持 Tk 能解码的 PNG/BMP/GIF）。"""
    import tkinter as tk

    image = tk.PhotoImage(file=path, master=tk_root)
    width, height = image.width(), image.height()
    if width <= 0 or height <= 0:
        raise ValueError("图片尺寸不正确")
    rgb = bytearray(width * height * 3)
    idx = 0
    for y in range(height):
        for x in range(width):
            value = image.get(x, y)
            if isinstance(value, str) and value:
                parts = value.split()
                if len(parts) >= 3:
                    rgb[idx] = int(parts[0])
                    rgb[idx + 1] = int(parts[1])
                    rgb[idx + 2] = int(parts[2])
            idx += 3
    return width, height, bytes(rgb)


def load_template(path: str, tk_root=None) -> Template:
    """加载模板图片。

    优先使用内置 PNG 解码（快、无 Tk 依赖）；失败时回退到 Tk PhotoImage。
    仅支持 Tk 能识别的格式（推荐 PNG）。
    """
    file = Path(path)
    if not file.exists():
        raise FileNotFoundError(f"模板文件不存在：{file}")
    data = file.read_bytes()
    width = height = None
    rgb: bytes | None = None
    if file.suffix.lower() == ".png":
        try:
            width, height, rgb = png.decode_png(data)
        except ValueError:
            rgb = None
    if rgb is None:
        if tk_root is None:
            raise ValueError("无法解码该图片，请另存为 PNG 格式后再试")
        width, height, rgb = _load_with_tk(str(file), tk_root)
    return template_from_rgb(str(file), rgb, width, height)
