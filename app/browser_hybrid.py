"""浏览器混合答题窗口：DOM 定位 + 视觉模型读题。

普通网页优先用 DOM 找选项/输入框/下一题；题目文字仍可用截图视觉模型，
因此字体反爬页面不依赖油猴脚本。
"""
from __future__ import annotations

import threading
import time
import queue
import tkinter as tk
from pathlib import Path

from . import ai_client
from .cdp_browser import BrowserSession, find_browser
from .widgets import PALETTE, RoundedButton, font


SCAN_JS = r"""
(() => {
  const registry = (window.__hybridEls = {});
  const scanTag = "h" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  const ids = new WeakMap();
  let seq = 0;
  const idFor = (el) => {
    // 同一元素在一次扫描内保持稳定 ID；每次扫描重新生成，避免旧属性冲突。
    if (ids.has(el)) return ids.get(el);
    const id = scanTag + "-" + (++seq);
    ids.set(el, id);
    el.dataset.hybridId = id;
    registry[id] = el;
    return id;
  };
  const textOf = (el) => (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ");
  const visible = (el) => {
    if (!el || el.disabled) return false;
    const doc = el.ownerDocument || document;
    const view = doc.defaultView || window;
    const r = el.getBoundingClientRect();
    const s = view.getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.visibility !== "hidden" && s.display !== "none";
  };
  const optionVisible = (el) => {
    if (visible(el)) return true;
    const lab = el.labels && el.labels[0];
    return !!(lab && visible(lab));
  };
  const letterOf = (el) => {
    const raw = [
      el.getAttribute("data-letter"), el.getAttribute("aria-label"),
      el.value, el.getAttribute("value")
    ].filter(Boolean).join(" ");
    let m = raw.match(/(?:^|[^A-Z])([A-F])(?:[^A-Z]|$)/i);
    if (m) return m[1].toUpperCase();
    const lab = el.labels && el.labels[0] ? textOf(el.labels[0]) : "";
    m = lab.match(/^\s*([A-F])(?:[\.、．:：)）\s]|$)/i);
    if (m) return m[1].toUpperCase();
    m = textOf(el).match(/^\s*([A-F])(?:[\.、．:：)）\s]|$)/i);
    return m ? m[1].toUpperCase() : "";  };
  const groups = [];
  const groupMap = new Map();
  const inputs = [];
  const blanks = [];
  const nextCandidates = [];
  const seen = new Set();
  const roots = [];

  const groupFor = (el, name) => {
    // 关键：不能从选项自身 closest('li') 分组，否则每个选项都会成为一组。
    // 超星等页面用 li[role=radio]；题目容器是 .singleQuesId / .TiMu。
    const parent = el.parentElement;
    const box = (parent && parent.closest(
      '[class*="singleQuesId"],[class*="TiMu"],[class*="question"],[class*="subject"],[class*="item"],fieldset,[role="group"],[role="radiogroup"]'
    )) || el.closest('ul,ol,fieldset') || parent || el;
    const key = name ? "name:" + name : "box:" + idFor(box || el);
    if (!groupMap.has(key)) {
      const title = textOf(box || el);
      const numberMatch = title.match(/^\s*(\d+)/);
      const group = {
        key, name,
        number: numberMatch ? Number(numberMatch[1]) : 0,
        options: []
      };
      groupMap.set(key, group);
      groups.push(group);
    }
    return groupMap.get(key);
  };

  const collect = (doc) => {
    roots.push(doc);
    const standard = [...doc.querySelectorAll(
      'input[type="radio"],input[type="checkbox"],[role="radio"],[role="checkbox"]'
    )];
    const custom = [...doc.querySelectorAll('div,li,span,label,a,button')].filter((el) => {
      const t = textOf(el);
      if (t.length > 80 || !/^[A-F]\s*(?:[\.、．:：)）]|\s+\S|$)/.test(t)) return false;
      if (el.querySelector('input,textarea,[contenteditable="true"],[class*="option"],[class*="answer"],[role="radio"],[role="checkbox"]')) return false;
      const roleHost = el.closest('[role="radio"],[role="checkbox"],[role="option"]');
      if (roleHost && roleHost !== el) return false;
      // 超星等页面的 li[role=radio] 内部还有 span.num_option，不能重复当选项。
      if (el.tagName === "SPAN" && el.closest('li')) return false;
      const cls = String(el.className || "") + " " + String(el.parentElement && el.parentElement.className || "");
      return /option|answer|choice|item|select|radio|check|judge/i.test(cls) ||
             !!el.getAttribute("role");
    });
    for (const el of [...standard, ...custom]) {
      if (seen.has(el) || !optionVisible(el)) continue;
      seen.add(el);
      const name = el.name || el.getAttribute("name") || "";
      const group = groupFor(el, name);
      const label = el.labels && el.labels[0] ? el.labels[0] : null;
      const cls = String(el.className || "") + " " +
        String(el.parentElement && el.parentElement.className || "");
      group.options.push({
        id: idFor(el),
        letter: letterOf(el),
        text: textOf(label || el).slice(0, 120),
        checked: !!(el.checked || el.getAttribute("aria-checked") === "true" ||
                    /(^|\s)(selected|active|checked)(\s|$)/i.test(cls) ||
                    el.querySelector('.check_answer,.selected,.active,.checked')),
        rect: (() => { const r = el.getBoundingClientRect(); return [r.x,r.y,r.width,r.height]; })()
      });
    }
    for (const el of doc.querySelectorAll(
      'input[type="text"],input:not([type]),textarea,[contenteditable="true"]'
    )) {
      if (!visible(el) || (el.getAttribute("type") || "").toLowerCase() === "search") continue;
      if (el.closest('.blankItemDiv')) continue;
      const cls = String(el.className || "");
      const ph = el.getAttribute("placeholder") || "";
      if (/trans|translate|search/i.test(cls) || /翻译|搜索/.test(ph)) continue;
      inputs.push({
        id: idFor(el),
        tag: el.tagName.toLowerCase(),
        type: (el.getAttribute("type") || "").toLowerCase(),
        placeholder: el.getAttribute("placeholder") || "",
        value: el.isContentEditable ? textOf(el) : (el.value || ""),
        rect: (() => { const r = el.getBoundingClientRect(); return [r.x,r.y,r.width,r.height]; })()
      });
    }
    for (const blank of doc.querySelectorAll('.blankItemDiv')) {
      if (!visible(blank)) continue;
      const input = blank.querySelector('.InpDIV, input, textarea');
      if (!input) continue;
      const container = blank.closest('.singleQuesId, .TiMu, [class*="question"]') || blank.parentElement;
      const title = textOf(container || blank);
      const nm = title.match(/^\s*(\d+)/);
      const label = textOf(blank.querySelector('.tiankong') || blank);
      const sm = label.match(/第\s*(\d+)\s*空/);
      blanks.push({
        id: idFor(blank),
        inputId: idFor(input),
        number: nm ? Number(nm[1]) : 0,
        slot: sm ? Number(sm[1]) : 0,
        label: label.slice(0, 40),
        value: textOf(blank)
      });
    }
    for (const el of doc.querySelectorAll(
      'button,a,[role="button"],input[type="button"],input[type="submit"]'
    )) {
      if (!visible(el)) continue;
      const text = textOf(el).slice(0, 80);
      if (/下一题|下一頁|下一页|继续|next/i.test(text)) {
        nextCandidates.push({id: idFor(el), text});
      }
    }
    for (const frame of doc.querySelectorAll('iframe,frame')) {
      try {
        if (!visible(frame)) continue;
        if (frame.contentDocument) collect(frame.contentDocument);
      } catch (e) {}
    }
  };
  collect(document);
  return {
    groups,
    inputs,
    blanks,
    next: nextCandidates[0] || null,
    pageText: (document.body ? document.body.innerText : "").slice(0, 3000),
    url: location.href,
    frames: roots.length
  };
})()
"""

CLICK_JS = r"""
((id) => {
  const registry = window.__hybridEls || {};
  const el = registry[id] || document.querySelector('[data-hybrid-id="' + id + '"]');
  if (!el) return {ok:false, error:"元素不存在"};
  let target = el;
  if (el.tagName === "INPUT") {
    const lab = (el.labels && el.labels[0]) ||
      (el.id ? el.ownerDocument.querySelector('label[for="' + el.id + '"]') : null);
    if (lab) target = lab;
  }
  target.click();
  return {ok:true, checked: !!(el.checked || el.getAttribute("aria-checked") === "true")};
})(%s)
"""

FILL_JS = r"""
((id, value) => {
  const registry = window.__hybridEls || {};
  const el = registry[id] || document.querySelector('[data-hybrid-id="' + id + '"]');
  if (!el) return {ok:false, error:"元素不存在"};
  el.focus();
  if (el.isContentEditable) {
    el.textContent = value;
  } else {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
  }
  el.dispatchEvent(new Event("input", {bubbles:true}));
  el.dispatchEvent(new Event("change", {bubbles:true}));
  return {ok:true, value: el.isContentEditable ? el.textContent : el.value};
})(%s, %s)
"""

BLANK_FOCUS_JS = r"""
((id) => {
  const registry = window.__hybridEls || {};
  const blank = registry[id] || document.querySelector('[data-hybrid-id="' + id + '"]');
  if (!blank) return {ok:false, error:"填空容器不存在"};
  const inp = blank.querySelector('.InpDIV');
  if (inp) inp.click();
  const frame = blank.querySelector('.textDIV iframe');
  if (frame && frame.contentDocument && frame.contentDocument.body) {
    const body = frame.contentDocument.body;
    body.focus();
    try {
      const range = frame.contentDocument.createRange();
      range.selectNodeContents(body);
      const sel = frame.contentWindow.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    } catch (e) {}
    return {ok:true, kind:"ueditor"};
  }
  const target = blank.querySelector('input,textarea,[contenteditable="true"]');
  if (target) {
    target.focus();
    return {ok:true, kind:"native"};
  }
  return {ok:false, error:"没有可聚焦的编辑器"};
})(%s)
"""

BLANK_READ_JS = r"""
((id) => {
  const blank = (window.__hybridEls || {})[id];
  if (!blank) return "";
  const frame = blank.querySelector('.textDIV iframe');
  if (frame && frame.contentDocument && frame.contentDocument.body) {
    return frame.contentDocument.body.innerText || "";
  }
  const target = blank.querySelector('input,textarea,[contenteditable="true"]');
  return target ? (target.value || target.innerText || "") : "";
})(%s)
"""


class BrowserHybridWindow(tk.Toplevel):
    def __init__(self, master: tk.Misc, get_ai_config, on_close=None) -> None:
        super().__init__(master)
        self._get_ai_config = get_ai_config
        self._on_close = on_close
        self._session: BrowserSession | None = None
        self._running = False
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._ui_queue: queue.Queue = queue.Queue()
        self.title("浏览器混合答题")
        self.configure(bg=PALETTE["bg"])
        try:
            self.attributes("-alpha", 1.0)
        except tk.TclError:  # pragma: no cover
            pass
        self.geometry("860x650")
        self.protocol("WM_DELETE_WINDOW", self._close)

        wrap = tk.Frame(self, bg=PALETTE["bg"], padx=14, pady=14)
        wrap.pack(fill="both", expand=True)

        head = tk.Frame(wrap, bg=PALETTE["bg"])
        head.pack(fill="x", pady=(0, 10))
        tk.Label(head, text="浏览器混合答题", bg=PALETTE["bg"], fg=PALETTE["text"],
                 font=font(16, bold=True)).pack(side="left")
        tk.Label(head, text="DOM 定位 + 视觉读题（适合字体反爬页面）",
                 bg=PALETTE["bg"], fg=PALETTE["muted"], font=font(9)
                 ).pack(side="left", padx=(12, 0))

        row = tk.Frame(wrap, bg=PALETTE["bg"])
        row.pack(fill="x", pady=(0, 6))
        tk.Label(row, text="浏览器exe", bg=PALETTE["bg"], fg=PALETTE["muted"],
                 font=font(9), width=9, anchor="w").pack(side="left")
        self.var_browser = tk.StringVar(value=find_browser())
        tk.Entry(row, textvariable=self.var_browser, bg=PALETTE["bg2"],
                 fg=PALETTE["text"], insertbackground=PALETTE["text"],
                 relief="flat", font=font(9)).pack(side="left", fill="x", expand=True)
        tk.Label(row, text="端口", bg=PALETTE["bg"], fg=PALETTE["muted"],
                 font=font(9)).pack(side="left", padx=(10, 4))
        self.var_port = tk.StringVar(value="18765")
        tk.Entry(row, textvariable=self.var_port, width=7, bg=PALETTE["bg2"],
                 fg=PALETTE["text"], insertbackground=PALETTE["text"],
                 relief="flat", font=font(9)).pack(side="left")

        row = tk.Frame(wrap, bg=PALETTE["bg"])
        row.pack(fill="x", pady=(0, 8))
        tk.Label(row, text="起始网址", bg=PALETTE["bg"], fg=PALETTE["muted"],
                 font=font(9), width=9, anchor="w").pack(side="left")
        self.var_url = tk.StringVar()
        tk.Entry(row, textvariable=self.var_url, bg=PALETTE["bg2"],
                 fg=PALETTE["text"], insertbackground=PALETTE["text"],
                 relief="flat", font=font(9)).pack(side="left", fill="x", expand=True)
        self.var_auto_next = tk.BooleanVar(value=True)
        tk.Checkbutton(
            row, text="自动下一题（无下一题则答完本页停止）", variable=self.var_auto_next,
            bg=PALETTE["bg"], fg=PALETTE["muted"], selectcolor=PALETTE["bg2"],
            activebackground=PALETTE["bg"], activeforeground=PALETTE["text"],
            font=font(9),
        ).pack(side="left", padx=(10, 0))

        btns = tk.Frame(wrap, bg=PALETTE["bg"])
        btns.pack(fill="x", pady=(0, 8))
        self.btn_connect = RoundedButton(
            btns, text="启动/连接浏览器", command=self._connect,
            width=150, height=34, radius=8, bg=PALETTE["bg2"],
            hover_bg=PALETTE["card_hover"], border_color=PALETTE["border"],
            font=font(10),
        )
        self.btn_connect.pack(side="left")
        self.btn_start = RoundedButton(
            btns, text="开始", command=self._start,
            width=90, height=34, radius=8, bg=PALETTE["accent_dark"],
            hover_bg=PALETTE["accent"], font=font(10, bold=True),
        )
        self.btn_start.pack(side="left", padx=(8, 0))
        self.btn_stop = RoundedButton(
            btns, text="停止", command=self._stop,
            width=80, height=34, radius=8, bg=PALETTE["danger"],
            hover_bg=PALETTE["danger_hover"], font=font(10, bold=True),
        )
        self.btn_stop.pack(side="left", padx=(8, 0))
        self.status = tk.Label(btns, text="未连接", bg=PALETTE["bg"],
                               fg=PALETTE["muted"], font=font(9))
        self.status.pack(side="left", padx=(12, 0))

        self.log = tk.Text(
            wrap, height=18, bg="#0b1220", fg="#dbeafe", insertbackground="#dbeafe",
            relief="flat", font=font(9, mono=True), wrap="word",
        )
        self.log.pack(fill="both", expand=True)
        self.after(80, self._poll_ui)

    def _poll_ui(self) -> None:
        try:
            while True:
                kind, value = self._ui_queue.get_nowait()
                if kind == "log":
                    self._append_log(str(value))
                elif kind == "status":
                    text, color = value
                    self.status.configure(text=str(text), fg=color)
        except queue.Empty:
            pass
        except tk.TclError:  # pragma: no cover
            return
        try:
            self.after(80, self._poll_ui)
        except tk.TclError:  # pragma: no cover
            pass

    def _append_log(self, message: str) -> None:
        try:
            self.log.insert("end", time.strftime("[%H:%M:%S] ") + message + "\n")
            self.log.see("end")
        except tk.TclError:  # pragma: no cover
            pass

    def _log(self, message: str) -> None:
        if threading.current_thread() is threading.main_thread():
            self._append_log(message)
        else:
            self._ui_queue.put(("log", message))

    def _set_status(self, text: str, color: str = "") -> None:
        if threading.current_thread() is threading.main_thread():
            self.status.configure(text=text, fg=color or PALETTE["muted"])
        else:
            self._ui_queue.put(("status", (text, color or PALETTE["muted"])))

    def _connect(self) -> None:
        try:
            port = int(self.var_port.get().strip() or "18765")
            path = self.var_browser.get().strip()
            if not path or not Path(path).exists():
                path = find_browser()
                self.var_browser.set(path)
            if not path:
                raise RuntimeError("未找到 Chrome/Edge，请填写浏览器 exe 路径")
            # 端口被其它软件占用时，自动向后寻找空闲端口。
            if self._session is None and not self._port_available(port):
                free_port = self._find_free_port(port + 1)
                self._log(f"端口 {port} 被占用，自动改用 {free_port}")
                port = free_port
                self.var_port.set(str(port))
            session = BrowserSession(port, path,
                                     str(Path(__file__).resolve().parent.parent / "data" / "browser-profile"))
            if self._session is not None:
                self._session.close()
            url = self.var_url.get().strip()
            session.launch(url if url.startswith(("http://", "https://")) else "")
            session.connect()
            self._session = session
            self.status.configure(text="已连接", fg=PALETTE["success"])
            self._log(f"已连接浏览器：{session.target_info.get('url', '')}")
        except Exception as exc:  # noqa: BLE001
            self.status.configure(text="连接失败", fg=PALETTE["danger"])
            self._log(f"连接失败：{exc}")

    @staticmethod
    def _port_available(port: int) -> bool:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", int(port)))
                return True
            except OSError:
                return False

    @classmethod
    def _find_free_port(cls, start: int) -> int:
        for port in range(max(1024, int(start)), 65535):
            if cls._port_available(port):
                return port
        raise RuntimeError("没有找到空闲端口")

    def _start(self) -> None:
        if self._running:
            return
        if self._session is None:
            self._connect()
        if self._session is None:
            return
        try:
            # 页面可能是连接后才打开的，重新选择当前活动页面
            self._session.connect()
        except Exception as exc:  # noqa: BLE001
            self.status.configure(text="未找到答题页", fg=PALETTE["danger"])
            self._log(f"当前没有可扫描的答题页：{exc}")
            return
        ai_config, error = self._get_ai_config()
        if ai_config is None:
            self._log(error)
            return
        self._running = True
        self._stop.clear()
        self.status.configure(text="运行中", fg=PALETTE["success"])
        self._log("开始混合答题：扫描 DOM → 视觉读题 → DOM 填写/选择 → 下一题")
        auto_next = bool(self.var_auto_next.get())
        self._worker = threading.Thread(
            target=self._run_loop, args=(ai_config, auto_next),
            daemon=True, name="browser-hybrid"
        )
        self._worker.start()

    def _stop(self) -> None:
        if not self._running:
            return
        self._stop.set()
        self._log("正在停止…")

    def _run_loop(self, ai_config, auto_next: bool) -> None:
        while not self._stop.is_set():
            try:
                if self._session is None:
                    break
                data = self._session.eval(SCAN_JS)
                groups = data.get("groups") or []
                inputs = data.get("inputs") or []
                blanks = data.get("blanks") or []
                if not groups and not inputs and not blanks:
                    self._log("当前页没有找到可操作的选项卡或输入框")
                    break
                self._log(
                    f"页面：{data.get('url', '')}；选项组 {len(groups)}，"
                    f"填空 {len(blanks)}，输入框 {len(inputs)}"
                )
                if groups:
                    answers = self._collect_choice_answers(ai_config)
                    self._do_choices(answers, groups)
                if blanks:
                    self._do_blanks(ai_config, blanks)
                elif inputs:
                    image = self._session.screenshot(full_page=True)
                    self._do_fills(image, ai_config, inputs)
                if not auto_next:
                    break
                nxt = data.get("next")
                if nxt:
                    self._session.eval(CLICK_JS % repr(nxt["id"]))
                    self._log(f"已点击下一题：{nxt.get('text', '')}")
                    time.sleep(1.4)
                else:
                    self._log("本页没有「下一题」：按单页模式处理，已答完本页后停止")
                    break
            except Exception as exc:  # noqa: BLE001
                self._log(f"出错：{exc}")
                time.sleep(1.5)
                break
        self._running = False
        self._set_status("已停止", PALETTE["muted"])
        self._log("混合答题已停止")

    def _collect_choice_answers(self, ai_config) -> dict[int, object]:
        """单页可能很长，按 1200px 分段识别，避免一页 18 题只看到前几题。"""
        try:
            width, height = self._session.page_size()
        except Exception:  # noqa: BLE001
            width, height = 0, 0
        if width <= 0 or height <= 0:
            image = self._session.screenshot(full_page=True)
            page = ai_client.ask_page(image, ai_config)
            return {int(getattr(q, "index", i + 1)): q for i, q in enumerate(page.questions or [])}
        answers: dict[int, object] = {}
        chunk = 1200
        overlap = 120
        for y in range(0, height, chunk):
            clip_h = min(chunk + overlap, height - y)
            if clip_h < 80:
                break
            try:
                image = self._session.screenshot_clip(0, y, width, clip_h)
                page = ai_client.ask_page(image, ai_config)
            except Exception as exc:  # noqa: BLE001
                self._log(f"分段识别 y={y} 失败：{exc}")
                continue
            for q in page.questions or []:
                try:
                    index = int(getattr(q, "index", 0))
                except (TypeError, ValueError):
                    index = 0
                if index > 0 and index not in answers:
                    answers[index] = q
            self._log(f"分段 y={y}：本段识别 {len(page.questions or [])} 题")
        self._log(f"分段识别完成：共 {len(answers)} 道选择题")
        return answers

    def _do_choices(self, answers: dict[int, object], groups: list[dict]) -> None:
        if not answers:
            self._log("AI 没有返回选择题答案")
            return
        by_number = {
            int(g.get("number") or 0): g for g in groups
            if int(g.get("number") or 0) > 0
        }
        ordered = sorted(answers.items())
        for position, (number, question) in enumerate(ordered):
            group = by_number.get(number)
            if group is None and position < len(groups):
                group = groups[position]
            if group is None:
                continue
            letters = [
                str(x).upper() for x in (getattr(question, "letters", None) or [])
                if str(x).strip()
            ]
            if not letters:
                raw = str(getattr(question, "letter", "")).upper()
                letters = [c for c in raw if c in "ABCDEF"]
            options = {str(o.get("letter", "")).upper(): o for o in group.get("options", [])}
            for letter in letters:
                option = options.get(letter)
                if not option:
                    continue
                if option.get("checked"):
                    self._log(f"第 {number or position + 1} 题选项 {letter} 已选中，跳过")
                    continue
                result = self._session.eval(CLICK_JS % repr(option["id"]))
                self._log(f"第 {number or position + 1} 题点击 {letter}：{result}")

    def _collect_fill_answers(self, ai_config) -> dict[tuple[int, int], str]:
        """分段识别填空，返回 {(题号, 第几空): 答案}。"""
        try:
            width, height = self._session.page_size()
        except Exception:  # noqa: BLE001
            width, height = 0, 0
        answers: dict[tuple[int, int], str] = {}
        sequential: list[str] = []

        def absorb(answer) -> None:
            for item in answer.items or []:
                value = str(getattr(item, "answer", "") or "").strip()
                if not value:
                    continue
                try:
                    number = int(getattr(item, "index", 0) or 0)
                except (TypeError, ValueError):
                    number = 0
                try:
                    slot = int(getattr(item, "slot", 0) or 0)
                except (TypeError, ValueError):
                    slot = 0
                if number > 0 and slot > 0:
                    answers.setdefault((number, slot), value)
                else:
                    sequential.append(value)

        if width <= 0 or height <= 0:
            try:
                absorb(ai_client.ask_fill(self._session.screenshot(full_page=True), ai_config, overwrite=True))
            except Exception as exc:  # noqa: BLE001
                self._log(f"填空题识别失败：{exc}")
            return answers
        chunk = 1200
        overlap = 120
        for y in range(0, height, chunk):
            clip_h = min(chunk + overlap, height - y)
            if clip_h < 80:
                break
            try:
                image = self._session.screenshot_clip(0, y, width, clip_h)
                absorb(ai_client.ask_fill(image, ai_config, overwrite=True))
            except Exception as exc:  # noqa: BLE001
                self._log(f"填空分段 y={y} 识别失败：{exc}")
        self._log(f"填空分段识别完成：{len(answers)} 个带题号的答案")
        if sequential:
            for i, value in enumerate(sequential):
                answers.setdefault((-1, i + 1), value)
        return answers

    def _do_blanks(self, ai_config, blanks: list[dict]) -> None:
        answers = self._collect_fill_answers(ai_config)
        ordered = [v for (n, s), v in sorted(answers.items()) if n > 0]
        sequential = [v for (n, s), v in sorted(answers.items()) if n < 0]
        for i, blank in enumerate(blanks):
            number = int(blank.get("number") or 0)
            slot = int(blank.get("slot") or 0)
            value = answers.get((number, slot))
            if not value and i < len(ordered):
                value = ordered[i]
            if not value and i < len(sequential):
                value = sequential[i]
            if not value:
                self._log(f"第 {number or '?'} 题第 {slot or '?'} 空：AI 未给答案")
                continue
            blank_id = blank["id"]
            focus = self._session.eval(BLANK_FOCUS_JS % repr(blank_id))
            if not focus or not focus.get("ok"):
                self._log(f"第 {number} 题第 {slot} 空聚焦失败：{focus}")
                continue
            time.sleep(0.20)
            # 再次聚焦，等待编辑器从隐藏层切换出来
            self._session.eval(BLANK_FOCUS_JS % repr(blank_id))
            try:
                self._session.clear_focused()
                self._session.insert_text(value)
                time.sleep(0.20)
                readback = str(self._session.eval(BLANK_READ_JS % repr(blank_id)) or "").strip()
                self._log(
                    f"填写第 {number} 题第 {slot} 空：{value} → 回读：{readback[:40]}"
                )
            except Exception as exc:  # noqa: BLE001
                self._log(f"填写第 {number} 题第 {slot} 空失败：{exc}")

    def _do_fills(self, image: bytes, ai_config, inputs: list[dict]) -> None:
        answer = ai_client.ask_fill(image, ai_config, overwrite=True)
        items = list(answer.items or [])
        for i, item in enumerate(items):
            if i >= len(inputs):
                break
            value = str(getattr(item, "answer", "") or "").strip()
            if not value:
                continue
            inp = inputs[i]
            current = str(inp.get("value", "") or "").strip()
            if current == value:
                self._log(f"输入框 {i+1} 已是目标值，跳过")
                continue
            result = self._session.eval(FILL_JS % (repr(inp["id"]), repr(value)))
            self._log(f"填写输入框 {i+1}：{value} → {result}")

    def _close(self) -> None:
        self._stop.set()
        try:
            if self._session is not None:
                self._session.close()
        except Exception:
            pass
        if callable(self._on_close):
            try:
                self._on_close()
            except Exception:
                pass
        self.destroy()
