"""把项目打包为可分发 zip（开发/发布用）。

运行：
    python tools/make_release.py
输出：
    <工作区根目录>/ATanswer-Windows-v1.0.0.zip
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "ATanswer-Windows-v1.0.0"
OUTPUT = ROOT.parent / f"{NAME}.zip"


def iter_files(base: Path):
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(base)
        if any(part == "__pycache__" for part in rel.parts):
            continue
        if path.suffix == ".pyc":
            continue
        if rel.parts and rel.parts[0] in ("data", ".git"):
            continue
        if path.name in (".gitignore", ".gitattributes"):
            continue
        if path.is_dir():
            continue
        yield path


def main() -> int:
    if OUTPUT.exists():
        OUTPUT.unlink()
    files = list(iter_files(ROOT))
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # 保留空目录结构（data 等运行时目录）
        for extra in ("data", "data/templates", "data/tmp", "data/logs"):
            info = zipfile.ZipInfo(f"{NAME}/{extra}/")
            zf.writestr(info, b"")
        for path in files:
            arcname = f"{NAME}/{path.relative_to(ROOT).as_posix()}"
            zf.write(path, arcname)
    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"打包完成：{OUTPUT}")
    print(f"文件数：{len(files)}，大小：{size_mb:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
