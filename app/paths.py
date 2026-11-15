"""应用路径与目录管理。

数据优先写入程序目录下的 data/；若程序目录只读，则回退到
%LOCALAPPDATA%\\SmartClicker。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_NAME, VERSION

if getattr(sys, "frozen", False):  # PyInstaller 打包后
    APP_ROOT = Path(sys.executable).resolve().parent
    _RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", APP_ROOT))
else:
    APP_ROOT = Path(__file__).resolve().parent.parent
    _RESOURCE_ROOT = APP_ROOT
ASSETS_DIR = _RESOURCE_ROOT / "assets"


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _choose_data_dir() -> Path:
    candidates: list[Path] = [APP_ROOT / "data"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "SmartClicker")
    candidates.append(Path.home() / ".smart_clicker")
    for cand in candidates:
        if _is_writable(cand):
            return cand
    raise RuntimeError("无法创建数据目录，请检查程序所在目录的写入权限")


DATA_DIR: Path = _choose_data_dir()
TEMPLATES_DIR: Path = DATA_DIR / "templates"
TMP_DIR: Path = DATA_DIR / "tmp"
LOGS_DIR: Path = DATA_DIR / "logs"
SETTINGS_FILE: Path = DATA_DIR / "settings.json"
ICON_FILE: Path = ASSETS_DIR / "app.ico"
README_FILE: Path = APP_ROOT / "README.md"

for _d in (TEMPLATES_DIR, TMP_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def temp_png_path(prefix: str = "shot") -> Path:
    """返回一个位于临时目录的 png 路径（调用方负责清理）。"""
    import time

    return TMP_DIR / f"{prefix}_{int(time.time() * 1000)}.png"


def app_title() -> str:
    return f"{APP_NAME} v{VERSION}"
