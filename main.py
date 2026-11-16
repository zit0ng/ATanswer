"""ATanswer · Windows 版 —— 启动入口。

用法：
    python main.py          # 带控制台（便于查看错误）
    pythonw main.py         # 无控制台窗口（推荐日常使用）
或直接双击「启动.bat」。
"""
from __future__ import annotations

import subprocess
import sys
import time
import traceback
from pathlib import Path


def _write_crash_log(detail: str) -> str:
    try:
        from app import paths

        path = paths.LOGS_DIR / f"startup_error_{int(time.time())}.log"
        path.write_text(detail, encoding="utf-8")
        return str(path)
    except Exception:  # pragma: no cover
        return ""


def _message_box(message: str, flags: int = 0x10) -> int:
    try:
        import ctypes

        return int(ctypes.windll.user32.MessageBoxW(None, message, "ATanswer", flags))
    except Exception:  # pragma: no cover
        print(message)
        return 1


def _elevation_command() -> tuple[str, str, str]:
    """返回 (可执行文件, 参数, 工作目录)。"""
    cwd = str(Path(__file__).resolve().parent)
    if getattr(sys, "frozen", False):
        return sys.executable, subprocess.list2cmdline(sys.argv[1:]), str(Path(sys.executable).resolve().parent)
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    exe = str(pythonw if pythonw.exists() else python)
    params = subprocess.list2cmdline([str(Path(__file__).resolve()), *sys.argv[1:]])
    return exe, params, cwd


def ensure_admin() -> bool:
    """尝试以管理员身份重启当前程序。

    返回 True 表示当前进程应继续启动；返回 False 表示用户拒绝继续。
    如果成功拉起管理员进程，当前进程会直接退出。
    """
    try:
        from app import winapi

        if winapi.is_admin():
            return True
        from app import error_hints

        exe, params, cwd = _elevation_command()
        import ctypes

        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, cwd, 1
        )
        if int(result) > 32:
            return False  # 新进程已经启动，当前进程退出
        reason = f"以管理员身份启动失败（ShellExecute 错误码 {int(result)}）。"
        message = (
            f"{reason}\n\n"
            + error_hints.format_error(reason, "启动管理员模式")
            + "\n\n是否仍以普通权限继续运行？"
        )
        choice = _message_box(message, 0x04 | 0x30)  # MB_YESNO | WARNING
        return choice == 6  # IDYES
    except Exception as exc:  # noqa: BLE001
        _message_box(f"管理员模式启动检查失败：{exc}\n\n是否继续以普通权限运行？", 0x04 | 0x30)
        return True


def main() -> int:
    if not sys.platform.startswith("win"):
        _message_box("当前版本仅支持 Windows 系统。")
        return 1
    if not ensure_admin():
        return 0
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
        try:
            from app import error_hints

            hints = error_hints.format_error(detail[-1200:], "程序启动失败")
        except Exception:
            hints = "可能原因：防火墙拦截、权限不足、运行库缺失或程序文件不完整。"
        message = "程序启动失败：\n\n" + detail[-1600:] + "\n\n" + hints
        if log_path:
            message += f"\n\n崩溃日志：{log_path}"
        _message_box(message)
        return 1


if __name__ == "__main__":
    sys.exit(main())
