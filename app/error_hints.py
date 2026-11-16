"""统一错误提示：任何报错都附带可能原因（含 Windows 防火墙检查）。"""
from __future__ import annotations

import subprocess
import tkinter as tk
from tkinter import messagebox


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


def likely_causes(error: str = "", context: str = "", state: str = "UNKNOWN") -> list[str]:
    text = f"{context} {error}".lower()
    causes = [
        "程序没有以管理员权限运行，导致无法点击目标窗口或读写文件。",
        "源文件不完整：便携版必须保留 ATanswer.exe 和 _internal 文件夹。",
        "浏览器路径不正确，或 18765/18766 端口被其它程序占用。",
        "AI 接口地址、API Key、网络代理或余额有问题。",
        "目标页面尚未完全加载，或当前标签页不是答题页。",
    ]
    if "新标签页" in error or "答题页" in error or "newtab" in text:
        causes.insert(0, "启动/连接后，必须在程序打开的那个 Edge 窗口里进入答题页；原来的 Edge 窗口不算。")
        causes.insert(1, "「起始网址」为空，或者只粘贴了半截 URL；请填完整 http(s) 地址。")
    if "python" in text or "dll" in text or "runtime" in text:
        causes.insert(0, "当前电脑的 Python 或 VC++ 运行库不完整；请改用便携版，不要单独运行源码。")
    if state == "ON":
        causes.insert(0, "检测到 Windows 防火墙开启，可能拦截了浏览器调试端口；请允许 ATanswer/pythonw 通过防火墙。")
    if "port" in text or "端口" in text:
        causes.insert(0, "浏览器调试端口被占用；可换端口，或关闭之前由程序启动的浏览器。")
    # 去重，保持顺序
    return list(dict.fromkeys(causes))


def format_error(error: str, context: str = "") -> str:
    state = firewall_state()
    lines = []
    if state == "ON":
        lines.append("检测到 Windows 防火墙处于开启状态，可能是它拦截了程序。")
        lines.append("可在「Windows 安全中心 → 防火墙和网络保护 → 允许应用通过防火墙」中允许 ATanswer。")
        lines.append("")
    elif state == "OFF":
        lines.append("检测到 Windows 防火墙当前关闭，问题更可能是权限、路径、网络代理或页面未打开。")
        lines.append("")
    else:
        lines.append("未能确认 Windows 防火墙状态；如果仍然失败，请检查防火墙/安全软件拦截。")
        lines.append("")
    lines.append("可能的原因：")
    for item in likely_causes(error, context, state)[:7]:
        lines.append(f" - {item}")
    lines.append("")
    lines.append("如果仍无法启动，请把完整报错和日志文件发给开发者。")
    return "\n".join(lines)


def show_error(parent: tk.Misc | None, title: str, error: str, context: str = "") -> None:
    try:
        messagebox.showerror(title, f"{error}\n\n{format_error(error, context)}", parent=parent)
    except Exception:
        try:
            messagebox.showerror(title, f"{error}\n\n{format_error(error, context)}")
        except Exception:
            pass
