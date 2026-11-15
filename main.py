"""ATanswer · Windows 版 —— 启动入口。

用法：
    python main.py          # 带控制台（便于查看错误）
    pythonw main.py         # 无控制台窗口（推荐日常使用）
或直接双击「启动.bat」。
"""
from __future__ import annotations

import sys
import time
import traceback


def _write_crash_log(detail: str) -> str:
    try:
        from app import paths

        path = paths.LOGS_DIR / f"startup_error_{int(time.time())}.log"
        path.write_text(detail, encoding="utf-8")
        return str(path)
    except Exception:  # pragma: no cover
        return ""


def _message_box(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "ATanswer", 0x10)
    except Exception:  # pragma: no cover
        print(message)


def main() -> int:
    if not sys.platform.startswith("win"):
        _message_box("当前版本仅支持 Windows 系统。")
        return 1
    try:
        from app import APP_ID, winapi

        winapi.enable_dpi_awareness()
        winapi.set_app_user_model_id(APP_ID)
        from app.ui import run_app

        run_app()
        return 0
    except Exception:  # noqa: BLE001 - 启动阶段的任何异常都要写日志并提示
        detail = traceback.format_exc()
        log_path = _write_crash_log(detail)
        message = "程序启动失败：\n\n" + detail[-1800:]
        if log_path:
            message += f"\n\n崩溃日志：{log_path}"
        _message_box(message)
        return 1


if __name__ == "__main__":
    sys.exit(main())
