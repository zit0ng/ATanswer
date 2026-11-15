"""极简 PNG 编解码（纯标准库）。

只依赖 zlib/struct，支持：
- 编码：8bit RGB，filter 0（本程序保存模板/截图用）
- 解码：8bit RGB / RGBA / 灰度，非隔行，filter 0~4（兼容常见截图 PNG）

不依赖 Pillow。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def encode_png(width: int, height: int, rgb: bytes, level: int = 1) -> bytes:
    """把 RGB 字节（长度 width*height*3）编码成 PNG。"""
    if width <= 0 or height <= 0:
        raise ValueError("图片尺寸不合法")
    expected = width * height * 3
    if len(rgb) != expected:
        raise ValueError(f"RGB 数据长度不符：期望 {expected}，实际 {len(rgb)}")
    row_size = width * 3
    raw = b"".join(
        b"\x00" + rgb[y * row_size : (y + 1) * row_size] for y in range(height)
    )
    compressed = zlib.compress(raw, level)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )


def save_png(path: str | Path, width: int, height: int, rgb: bytes) -> None:
    Path(path).write_bytes(encode_png(width, height, rgb))


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png(data: bytes) -> tuple[int, int, bytes]:
    """解码 PNG，返回 (width, height, rgb)。仅支持 8bit 非隔行图片。

    无法解码时抛出 ValueError，调用方可回退到 Tk 加载。
    """
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("不是 PNG 文件")
    pos = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = interlace = None
    idat_parts: list[bytes] = []
    while pos + 8 <= len(data):
        length, tag = struct.unpack(">I4s", data[pos : pos + 8])
        pos += 8
        if pos + length + 4 > len(data):
            raise ValueError("PNG 数据被截断")
        chunk_data = data[pos : pos + length]
        pos += length + 4  # 跳过 CRC
        if tag == b"IHDR":
            (
                width,
                height,
                bit_depth,
                color_type,
                _compression,
                _filter,
                interlace,
            ) = struct.unpack(">IIBBBBB", chunk_data)
        elif tag == b"IDAT":
            idat_parts.append(chunk_data)
        elif tag == b"IEND":
            break
    if width is None or height is None:
        raise ValueError("PNG 缺少 IHDR")
    if bit_depth != 8 or interlace != 0:
        raise ValueError("仅支持 8bit 非隔行 PNG")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError("不支持的 PNG 颜色类型")
    raw = zlib.decompress(b"".join(idat_parts))
    stride = width * channels
    if len(raw) < height * (stride + 1):
        raise ValueError("PNG 像素数据不完整")

    out = bytearray(height * stride)
    prev = bytearray(stride)
    offset = 0
    for y in range(height):
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset : offset + stride])
        offset += stride
        if filter_type == 0:
            pass
        elif filter_type == 1:  # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up_left = prev[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(left, prev[i], up_left)) & 0xFF
        else:
            raise ValueError(f"不支持的 PNG 过滤器类型: {filter_type}")
        row_start = y * stride
        out[row_start : row_start + stride] = line
        prev = line

    # 统一转换成 RGB
    if color_type == 2:
        rgb = bytes(out)
    elif color_type == 6:
        rgb = bytearray(width * height * 3)
        row_out = width * 3
        for y in range(height):
            src = y * stride
            dst = y * row_out
            rgb[dst : dst + row_out : 3] = out[src : src + stride : 4]
            rgb[dst + 1 : dst + row_out : 3] = out[src + 1 : src + stride : 4]
            rgb[dst + 2 : dst + row_out : 3] = out[src + 2 : src + stride : 4]
        rgb = bytes(rgb)
    elif color_type == 0:
        rgb = bytearray(width * height * 3)
        row_out = width * 3
        for y in range(height):
            src = y * stride
            dst = y * row_out
            gray_row = out[src : src + width]
            rgb[dst : dst + row_out : 3] = gray_row
            rgb[dst + 1 : dst + row_out : 3] = gray_row
            rgb[dst + 2 : dst + row_out : 3] = gray_row
        rgb = bytes(rgb)
    elif color_type == 4:  # gray + alpha
        rgb = bytearray(width * height * 3)
        row_out = width * 3
        for y in range(height):
            src = y * stride
            dst = y * row_out
            gray_row = out[src : src + stride : 2]
            rgb[dst : dst + row_out : 3] = gray_row
            rgb[dst + 1 : dst + row_out : 3] = gray_row
            rgb[dst + 2 : dst + row_out : 3] = gray_row
        rgb = bytes(rgb)
    else:  # pragma: no cover
        raise ValueError("不支持的 PNG 颜色类型")

    if width * height * 3 != len(rgb):  # pragma: no cover
        raise ValueError("PNG 解码结果长度异常")
    return width, height, rgb
