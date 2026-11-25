"""统一错误提示：只显示最可能原因和联系方式。"""
from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import messagebox

CONTACT = "zit0ngeneral@outlook.com"


def firewall_state() -> str:
    """返回 ON / OFF / UNKNOWN。"""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            ["netsh", "advfirewall", "show", "allprofiles", "state"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=4,
            creationflags=flags,
        )
        text = (proc.stdout or "") + (proc.stderr or "")
        upper = text.upper()
        if "ON" in upper or "启用" in text or "開啟" in text or "开启" in text:
            return "ON"
        if "OFF" in upper or "关闭" in text or "關閉" in text or "停用" in text:
            return "OFF"
    except Exception:
        pass
    return "UNKNOWN"


def likely_reason(error: str = "", context: str = "", state: str = "UNKNOWN") -> str:
    text = f"{context} {error}".lower()
    if (
        "insufficient" in text
        or "balance" in text
        or "402" in text
        or "余额" in error
        or "欠费" in error
    ):
        return "AI 接口账户余额不足或欠费，请充值或更换有余额的 API Key。"
    if "401" in text or "unauthorized" in text or "invalid api key" in text:
        return "AI 接口 API Key 无效，请检查 Key 和接口地址是否匹配。"
    if "新标签页" in error or "答题页" in error or "newtab" in text:
        return "程序打开的浏览器里没有答题页，请在那个 Edge/Chrome 窗口打开答题页后再连接。"
    if "dll" in text or "runtime" in text or "python" in text:
        return "Python 或 VC++ 运行库不完整，请使用便携版并保留 _internal 文件夹。"
    if "port" in text or "端口" in text or "10053" in text:
        return "浏览器调试连接被中断，通常是页面被关闭/切换，或调试端口被占用。"
    if "timeout" in text or "timed out" in text or "connection" in text or "网络" in text:
        return "网络、代理或 AI 接口连接超时，请检查网络和 API 地址。"
    if "permission" in text or "access" in text or "admin" in text or "权限" in error:
        return "程序权限不足，请以管理员身份运行。"
    if state == "ON":
        return "Windows 防火墙或安全软件可能拦截了程序，请允许 ATanswer 通过防火墙。"
    if state == "OFF":
        return "浏览器连接、页面状态或 API 配置异常，请确认答题页已打开且 API 配置正确。"
    return "程序运行异常，请重试；若持续出现，请检查浏览器页面、权限和 API 配置。"


def format_error(error: str, context: str = "") -> str:
    reason = likely_reason(error, context, firewall_state())
    return f"最可能原因：{reason}\n\n联系我:{CONTACT}"


def show_error(parent: tk.Misc | None, title: str, error: str, context: str = "") -> None:
    message = f"{error}\n\n{format_error(error, context)}"
    try:
        messagebox.showerror(title, message, parent=parent)
    except Exception:
        try:
            messagebox.showerror(title, message)
        except Exception:
            pass
