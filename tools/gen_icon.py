"""生成应用图标与 Logo（开发用脚本，需要 Pillow）。

运行：
    <带 Pillow 的 Python> tools/gen_icon.py
输出：
    assets/app.ico   多尺寸 Windows 图标
    assets/logo.png  界面标题栏 Logo
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 512
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)


def make_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # 圆角渐变背景
    gradient = Image.new("RGBA", (SIZE, SIZE))
    grad_draw = ImageDraw.Draw(gradient)
    for y in range(SIZE):
        t = y / SIZE
        r = int(0x0B + (0x17 - 0x0B) * t)
        g = int(0x12 + (0x2A - 0x12) * t)
        b = int(0x1E + (0x45 - 0x1E) * t)
        grad_draw.line([(0, y), (SIZE, y)], fill=(r, g, b, 255))

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([10, 10, SIZE - 10, SIZE - 10], radius=112, fill=255)
    img.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [10, 10, SIZE - 10, SIZE - 10], radius=112, outline=(0x2E, 0x3E, 0x5C, 255), width=5
    )

    # 目标环
    cx, cy = 205, 200
    draw.ellipse([cx - 158, cy - 158, cx + 158, cy + 158], outline=(0x3D, 0x8B, 0xFD, 255), width=20)
    draw.ellipse([cx - 112, cy - 112, cx + 112, cy + 112], outline=(0x3D, 0x8B, 0xFD, 190), width=14)
    draw.ellipse([cx - 62, cy - 62, cx + 62, cy + 62], outline=(0x22, 0xD3, 0xEE, 255), width=12)
    draw.ellipse([cx - 17, cy - 17, cx + 17, cy + 17], fill=(0x22, 0xD3, 0xEE, 255))
    # 十字刻度
    for x0, y0, x1, y1 in (
        (cx, cy - 186, cx, cy - 160),
        (cx, cy + 160, cx, cy + 186),
        (cx - 186, cy, cx - 160, cy),
        (cx + 160, cy, cx + 186, cy),
    ):
        draw.line([(x0, y0), (x1, y1)], fill=(0x22, 0xD3, 0xEE, 255), width=10)

    # 光标箭头
    arrow = [
        (300, 292),
        (300, 414),
        (330, 386),
        (352, 436),
        (382, 424),
        (360, 374),
        (396, 374),
    ]
    draw.polygon(arrow, fill=(0xF3, 0xF8, 0xFF, 255), outline=(0x0B, 0x10, 0x18, 255))
    draw.line(arrow + [arrow[0]], fill=(0x0B, 0x10, 0x18, 255), width=5)
    return img


def main() -> None:
    icon = make_icon()
    ico_path = ASSETS / "app.ico"
    icon.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    logo = icon.resize((128, 128), Image.LANCZOS)
    logo.save(ASSETS / "logo.png", format="PNG")
    print("wrote", ico_path)
    print("wrote", ASSETS / "logo.png")


if __name__ == "__main__":
    main()
