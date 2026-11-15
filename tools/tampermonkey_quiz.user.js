// ==UserScript==
// @name         智能点击助手 · 网页文字答题（油猴）
// @namespace    smart-clicker
// @version      1.9.0
// @description  直接读取网页题目文字，调用 AI 批量作答：支持单选、多选、填空（含 contenteditable）；默认开启思考模式，答案更准
// @match        *://*/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @connect      api.deepseek.com
// @connect      open.bigmodel.cn
// @connect      dashscope.aliyuncs.com
// @connect      api.openai.com
// @connect      *
// @run-at       document-idle
// @noframes
// ==/UserScript==

(function () {
  'use strict';

  // ==================== 需要你修改的配置 ====================
  const CONFIG = {
    // 和软件里「AI 接口设置」保持一致
    apiBase: 'https://api.deepseek.com',
    apiKey: '',
    model: 'deepseek-flash',
    disableThinking: true,      // 已移除深度思考开关，固定快速模式
    maxTokens: 2500,            // 固定快速模式 token 上限

    // 运行方式
    mode: 'both',               // choice=只答选择题, fill=只填填空, both=都做
    autoRun: false,             // 默认关闭：打开页面不自动答题；可在面板勾选"自动运行"
    autoRunDelay: 1500,         // 页面变化后等待多久再扫描（毫秒）
    answerDelay: 260,           // 每题点击/填写后的等待（毫秒）
    debug: true,                // 控制台/面板输出调试信息

    // 页面选择器：如果自动识别不到，把你自己平台的 class 填到这里
    selectors: {
      question: '',             // 题目容器，例如 '.question-item, .subject, .timu'
      option: '',               // 选项元素，例如 '.option-item, .option, label.option'
      stem: '',                 // 题干元素，例如 '.question-title, .stem'
      input: 'input[type="text"], input:not([type]), textarea, [contenteditable="true"], [contenteditable=""]',
      nextButton: '',           // 可选：全部答完后自动点击的按钮（留空不点）
    },

    // 填空题识别关键词（附近文本包含这些才当成填空题）
    fillKeywords: ['题', '答案', '填空', '____', '___', '（）'],
  };
  // ==========================================================

  const LETTERS = 'ABCDEF';
  const ANSWERED_FLAG = 'scAnswered';
  const FILLED_FLAG = 'scFilled';
  const answeredSignatures = new Set();
  const blockIds = new WeakMap();
  let blockSeq = 0;
  let running = false;
  let stopRequested = false;
  let autoEnabled = false;
  let autoCheckbox = null;

  let activeRequest = null;
  let scheduledTimer = null;
  let manualToggleGuard = null;
  let lastPromptText = '';
  let panel = null;
  let logBox = null;
  let statusEl = null;
  let rootsCache = null;

  // ---------------- 小工具 ----------------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function log(message, type = 'info') {
    if (CONFIG.debug) {
      try {
        console.log('[文本答题]', message);
      } catch (e) {}
    }
    if (!logBox) return;
    const colors = { info: '#8fa3bf', ok: '#22c55e', warn: '#f59e0b', err: '#ef4444' };
    const line = document.createElement('div');
    line.style.color = colors[type] || colors.info;
    line.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    logBox.appendChild(line);
    logBox.scrollTop = logBox.scrollHeight;
    while (logBox.childElementCount > 200) logBox.removeChild(logBox.firstElementChild);
  }

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  function textOf(el) {
    if (!el) return '';
    return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function blockTextRaw(el) {
    if (!el) return '';
    const text = el.innerText || el.textContent || '';
    return text.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim().slice(0, 1200);
  }

  function isVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return false;
    const style = window.getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
  }

  function visualSort(elements) {
    return elements.slice().sort((a, b) => {
      const ra = (a.closest && a.closest('label') || a).getBoundingClientRect();
      const rb = (b.closest && b.closest('label') || b).getBoundingClientRect();
      const topDiff = (ra.top || 0) - (rb.top || 0);
      if (Math.abs(topDiff) > 4) return topDiff;
      return (ra.left || 0) - (rb.left || 0);
    });
  }

  function blockKey(block) {
    if (!blockIds.has(block)) blockIds.set(block, ++blockSeq);
    return blockIds.get(block);
  }

  function letterAt(index) {
    return LETTERS[index] || '?';
  }

  function normalizeLetters(value) {
    const list = Array.isArray(value) ? value : [value];
    const out = [];
    for (const item of list) {
      const text = String(item == null ? '' : item).toUpperCase();
      for (const ch of text) {
        if (LETTERS.includes(ch) && !out.includes(ch)) out.push(ch);
      }
    }
    return out.sort();
  }

  // ---------------- 页面结构收集（含同源 iframe / Shadow DOM） ----------------
  const AUTO_OPTION_HINT = /option|choice|answer|opt\b|radio|select|item/i;
  const QUESTION_HINT = /题|选择|单选|多选|判断|问答|填空|\?|？|____|（\s*）|\(\s*\)/;

  function getRoots() {
    const now = Date.now();
    if (rootsCache && now - rootsCache.time < 300) return rootsCache.roots;
    const roots = [];
    const seen = new Set();
    const visitDocument = (doc, depth) => {
      if (!doc || seen.has(doc) || depth > 3) return;
      seen.add(doc);
      roots.push(doc);
      let all = [];
      try {
        all = Array.from(doc.querySelectorAll('*'));
      } catch (e) {
        return;
      }
      for (const el of all) {
        if (el.shadowRoot) {
          if (!seen.has(el.shadowRoot)) {
            seen.add(el.shadowRoot);
            roots.push(el.shadowRoot);
            visitRoot(el.shadowRoot, depth + 1);
          }
        }
        if (el.tagName === 'IFRAME') {
          try {
            if (el.contentDocument) visitDocument(el.contentDocument, depth + 1);
          } catch (e) {}
        }
      }
    };
    const visitRoot = (root, depth) => {
      let children = [];
      try {
        children = Array.from(root.querySelectorAll('*'));
      } catch (e) {
        return;
      }
      for (const el of children) {
        if (el.shadowRoot && !seen.has(el.shadowRoot)) {
          seen.add(el.shadowRoot);
          roots.push(el.shadowRoot);
          visitRoot(el.shadowRoot, depth + 1);
        }
        if (el.tagName === 'IFRAME') {
          try {
            if (el.contentDocument) visitDocument(el.contentDocument, depth + 1);
          } catch (e) {}
        }
      }
    };
    visitDocument(document, 0);
    rootsCache = { time: Date.now(), roots };
    return roots;
  }

  function queryAll(selector) {
    const result = [];
    const seen = new Set();
    for (const root of getRoots()) {
      let list = [];
      try {
        list = Array.from(root.querySelectorAll(selector));
      } catch (e) {
        continue;
      }
      for (const el of list) {
        if (!seen.has(el)) {
          seen.add(el);
          result.push(el);
        }
      }
    }
    return result;
  }

  function queryAllElementWithin(root, selector) {
    const output = [];
    const seen = new Set();
    const visit = (node) => {
      let list = [];
      try {
        list = Array.from(node.querySelectorAll(selector));
      } catch (e) {
        return;
      }
      for (const el of list) {
        if (!seen.has(el)) {
          seen.add(el);
          output.push(el);
        }
      }
      let children = [];
      try {
        children = Array.from(node.querySelectorAll('*'));
      } catch (e) {
        return;
      }
      for (const el of children) {
        if (el.shadowRoot) visit(el.shadowRoot);
      }
    };
    if (root) visit(root);
    return output;
  }

  function querySelectorWithin(root, selector) {
    const list = queryAllElementWithin(root, selector);
    return list.length ? list[0] : null;
  }

  function optionLabelFor(input) {
    let label = input.closest('label');
    if (!label && input.id) {
      try {
        label = input.getRootNode().querySelector(`label[for="${CSS.escape(input.id)}"]`);
      } catch (e) {}
    }
    return label || input.parentElement || input;
  }

  function candidateOptionElements() {
    const selector = [
      '[class*="option"]', '[class*="Option"]',
      '[class*="choice"]', '[class*="Choice"]',
      '[class*="answer"]', '[class*="Answer"]',
      '[class*="opt"]', '[data-option]', 'label', 'li',
    ].join(',');
    return queryAll(selector).filter((el) => {
      if (!isVisible(el)) return false;
      const text = textOf(el);
      if (!text || text.length > 180) return false;
      const hint = `${el.className || ''} ${el.getAttribute('data-option') || ''}`;
      if (AUTO_OPTION_HINT.test(hint)) return true;
      if (/^[A-F][.、:：)）]\s*/i.test(text)) return true;
      if (/^[A-F]\s*[\u4e00-\u9fa5]/.test(text)) return true;
      return false;
    });
  }

  function stemFromBlock(block, options) {
    try {
      const clone = block.cloneNode(true);
      clone.querySelectorAll('input[type="radio"], input[type="checkbox"], label').forEach((el) => el.remove());
      if (CONFIG.selectors.option) {
        try {
          clone.querySelectorAll(CONFIG.selectors.option).forEach((el) => el.remove());
        } catch (e) {}
      }
      let text = blockTextRaw(clone);
      const optionTexts = new Set(
        options
          .map((option) => String(option.text || '').replace(/\s+/g, ' ').trim())
          .filter(Boolean)
      );
      const lines = text.split('\n').map((line) => line.trim()).filter(Boolean);
      text = lines.filter((line) => !optionTexts.has(line)).join('\n').trim();
      if (text.length >= 2) return text;
    } catch (e) {}
    return blockTextRaw(block);
  }

  function nearestQuestionText(block) {
    let node = block;
    for (let depth = 0; depth < 4 && node; depth += 1, node = node.parentElement) {
      const value = textOf(node);
      if (value && value.length > 8 && QUESTION_HINT.test(value)) return value.slice(0, 900);
    }
    return textOf(block).slice(0, 900);
  }

  // ---------------- 提取题目 ----------------
  function findQuestionBlock(el) {
    const custom = CONFIG.selectors.question;
    if (custom) {
      try {
        const found = el.closest(custom);
        if (found) return found;
      } catch (e) {}
    }
    let node = el;
    let firstWithOptions = null;
    let best = null;
    for (let depth = 0; depth < 10 && node && node !== document.body; depth += 1, node = node.parentElement) {
      if (!node.querySelectorAll) continue;
      const inputs = node.querySelectorAll('input[type="radio"], input[type="checkbox"]');
      if (inputs.length < 2) continue;
      if (!firstWithOptions) firstWithOptions = node;
      let extra = textOf(node);
      for (const input of inputs) {
        if (input.type !== 'radio' && input.type !== 'checkbox') continue;
        const optionText = textOf(optionLabelFor(input));
        if (optionText) extra = extra.split(optionText).join(' ');
      }
      const extraLength = extra.replace(/\s+/g, '').length;
      if (!best || extraLength > best.length) {
        best = { node, length: extraLength };
      }
      // 除去选项文字后仍有题干内容，说明这个容器同时包含题干+选项
      if (extraLength >= 5) return node;
    }
    return (best && best.node) || firstWithOptions || el.closest('label')?.parentElement || el.parentElement;
  }

  function scanChoiceByInputs() {
    let inputs = [];
    try {
      inputs = queryAll('input[type="radio"], input[type="checkbox"]').filter((input) => {
        if (input.disabled) return false;
        const label = optionLabelFor(input);
        return isVisible(input) || isVisible(label);
      });
    } catch (e) {
      return [];
    }
    if (!inputs.length) return [];
    const groups = new Map();
    for (const input of inputs) {
      const block = findQuestionBlock(input);
      if (!block) continue;
      const key = `${input.name || 'noname'}:${blockKey(block)}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(input);
    }
    const questions = [];
    for (const list of groups.values()) {
      if (list.length < 2) continue;
      const block = findQuestionBlock(list[0]);
      if (!block || block.dataset[ANSWERED_FLAG] === '1') continue;
      const blockText = textOf(block);
      if (!blockText) continue;
      const options = [];
      for (const input of visualSort(list)) {
        const clickEl = optionLabelFor(input);
        const optionText = textOf(clickEl) || input.value || '';
        if (!optionText) continue;
        options.push({ letter: letterAt(options.length), text: optionText, input, clickEl });
      }
      if (options.length < 2) continue;
      // 保留题目原文（可能含选项文字），避免字符串替换误删题干；再单独给出结构化选项
      const stem = stemFromBlock(block, options) || blockText;
      questions.push({
        index: questions.length + 1,
        stem,
        options,
        block,
        kind: 'choice',
      });
    }
    return questions;
  }

  function scanChoiceByOptions() {
    // 兜底：没有 radio/checkbox 的平台，用"选项样式"启发式分组
    const candidates = candidateOptionElements();
    const groups = new Map();
    for (const el of candidates) {
      const parent = el.parentElement;
      if (!parent) continue;
      if (!groups.has(parent)) groups.set(parent, []);
      groups.get(parent).push(el);
    }
    const questions = [];
    for (const [parent, elements] of groups) {
      if (elements.length < 2) continue;
      const sorted = visualSort(elements);
      let block = parent;
      for (let depth = 0; depth < 3 && block.parentElement; depth += 1) {
        const text = textOf(block);
        if (QUESTION_HINT.test(text) && text.length > 12) break;
        block = block.parentElement;
      }
      if (!block || block.dataset[ANSWERED_FLAG] === '1') continue;
      const optionTexts = sorted.map((el) => textOf(el)).filter(Boolean);
      if (optionTexts.length < 2) continue;
      const blockText = nearestQuestionText(block);
      if (!blockText || !QUESTION_HINT.test(blockText)) continue;
      const options = sorted.map((el, index) => {
        const match = textOf(el).match(/^([A-F])(?=[.、:：)）\s])/i);
        return {
          letter: match ? match[1].toUpperCase() : letterAt(index),
          text: textOf(el),
          input: el.querySelector('input[type="radio"], input[type="checkbox"]'),
          clickEl: el,
        };
      }).filter((option) => option.text);
      if (options.length < 2) continue;
      const stem = stemFromBlock(block, options) || blockText;
      questions.push({
        index: questions.length + 1,
        stem,
        options,
        block,
        kind: 'choice',
      });
    }
    return questions;
  }

  function scanChoiceBySelector() {
    const qSel = CONFIG.selectors.question;
    const oSel = CONFIG.selectors.option;
    if (!qSel || !oSel) return [];
    let blocks = [];
    try {
      blocks = queryAll(qSel);
    } catch (e) {
      return [];
    }
    const questions = [];
    for (const block of blocks) {
      if (!isVisible(block) || block.dataset[ANSWERED_FLAG] === '1') continue;
      let optionEls = [];
      try {
        optionEls = visualSort(queryAllElementWithin(block, oSel).filter(isVisible));
      } catch (e) {}
      if (optionEls.length < 2) continue;
      const options = optionEls.map((el, index) => ({
        letter: letterAt(index),
        text: textOf(el),
        input: el.querySelector('input[type="radio"], input[type="checkbox"]'),
        clickEl: el,
      }));
      const stemEl = CONFIG.selectors.stem ? querySelectorWithin(block, CONFIG.selectors.stem) : null;
      questions.push({
        index: questions.length + 1,
        stem: (stemEl ? textOf(stemEl) : '') || stemFromBlock(block, options) || blockTextRaw(block) || textOf(block),
        options,
        block,
        kind: 'choice',
      });
    }
    return questions;
  }

  function scanChoices() {
    const found = new Map();
    for (const question of scanChoiceByInputs()) {
      found.set(blockKey(question.block), question);
    }
    for (const question of scanChoiceBySelector()) {
      if (!found.has(blockKey(question.block))) {
        found.set(blockKey(question.block), question);
      }
    }
    // 没有 input 也没有配置选择器时，尝试样式启发式
    if (!found.size) {
      for (const question of scanChoiceByOptions()) {
        if (!found.has(blockKey(question.block))) {
          found.set(blockKey(question.block), question);
        }
      }
    }
    const list = Array.from(found.values());
    list.forEach((question, index) => {
      question.index = index + 1;
    });
    return list;
  }

  function looksLikeFillQuestion(blockText, placeholder) {
    const all = `${blockText} ${placeholder || ''}`;
    if (CONFIG.fillKeywords.some((keyword) => all.includes(keyword))) return true;
    return /答案|填空|请输入|作答/.test(placeholder || '');
  }

  function scanFills() {
    let inputs = [];
    try {
      inputs = queryAll(CONFIG.selectors.input);
    } catch (e) {
      return [];
    }
    const items = [];
    for (const input of inputs) {
      if (!isVisible(input) || input.disabled || input.readOnly) continue;
      const editable = Boolean(input.isContentEditable);
      if (!editable) {
        if (input.dataset[FILLED_FLAG] === '1') continue;
        if (input.type === 'hidden' || input.type === 'submit' || input.type === 'button') continue;
        if (input.value && String(input.value).trim()) continue;
      } else {
        if (input.dataset[FILLED_FLAG] === '1') continue;
        if (textOf(input)) continue;
      }
      const block = findQuestionBlock(input) || input.parentElement;
      const blockText = textOf(block).slice(0, 900);
      if (blockText.length < 6) continue;
      if (!looksLikeFillQuestion(blockText, input.placeholder || '')) continue;
      items.push({
        index: items.length + 1,
        stem: blockText,
        placeholder: input.placeholder || '',
        input,
        block,
        kind: 'fill',
        editable,
      });
    }
    // 按题目容器分组，给同一道题的多个空分配 slot（第几空）
    const groups = new Map();
    for (const item of items) {
      const key = item.block ? blockKey(item.block) : `fill:${groups.size}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    }
    const grouped = [];
    let questionNo = 0;
    for (const group of groups.values()) {
      questionNo += 1;
      const firstStem = group[0]?.stem || '';
      const numberMatch = firstStem.match(/^\s*(\d+)\b/);
      const questionIndex = numberMatch ? Number(numberMatch[1]) : questionNo;
      group.forEach((item, position) => {
        item.index = questionIndex;
        item.slot = position + 1;
        grouped.push(item);
      });
    }
    return grouped;
  }

  // ---------------- AI 请求 ----------------
  function httpPost(url, headers, body) {
    return new Promise((resolve, reject) => {
      if (typeof GM_xmlhttpRequest === 'function') {
        let settled = false;
        const finish = () => {
          if (!settled) {
            settled = true;
            activeRequest = null;
          }
        };
        const request = GM_xmlhttpRequest({
          method: 'POST',
          url,
          headers,
          data: body,
          timeout: 60000,
          onload(response) {
            finish();
            if (response.status >= 200 && response.status < 300) {
              resolve(response.responseText);
            } else {
              reject(new Error(`HTTP ${response.status}：${String(response.responseText || '').slice(0, 300)}`));
            }
          },
          onerror() {
            finish();
            reject(new Error('网络请求失败（检查 API 地址/网络）'));
          },
          ontimeout() {
            finish();
            reject(new Error('AI 请求超时（可调小题目数量后重试）'));
          },
        });
        activeRequest = {
          abort() {
            try {
              if (request && typeof request.abort === 'function') request.abort();
            } catch (e) {}
          },
        };
      } else {
        const controller = new AbortController();
        activeRequest = { abort: () => controller.abort() };
        fetch(url, { method: 'POST', headers, body, signal: controller.signal })
          .then(async (response) => {
            activeRequest = null;
            const text = await response.text();
            if (!response.ok) throw new Error(`HTTP ${response.status}：${text.slice(0, 300)}`);
            resolve(text);
          })
          .catch((error) => {
            activeRequest = null;
            reject(error);
          });
      }
    });
  }

  function extractJson(text) {
    let cleaned = String(text || '').trim();
    cleaned = cleaned.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/i, '');
    const start = cleaned.indexOf('{');
    const end = cleaned.lastIndexOf('}');
    if (start < 0 || end <= start) throw new Error('AI 返回内容不是 JSON');
    return JSON.parse(cleaned.slice(start, end + 1));
  }

  async function askAI(prompt) {
    if (!CONFIG.apiKey || CONFIG.apiKey.includes('在这里填')) {
      throw new Error('请先在脚本顶部 CONFIG 里填写 apiKey');
    }
    const base = String(CONFIG.apiBase || '').replace(/\/+$/, '');
    const url = base.endsWith('/chat/completions') ? base : `${base}/chat/completions`;
    const deepseek = /deepseek\.com/i.test(base);
    const tokenLimit = CONFIG.maxTokens || 2500;
    const payload = {
      model: CONFIG.model,
      messages: [{ role: 'user', content: prompt }],
      temperature: 0,
      max_tokens: tokenLimit,
      stream: false,
    };
    if (deepseek) {
      // 固定快速模式：不再提供深度思考开关
      payload.thinking = { type: 'disabled' };
    }
    const responseText = await httpPost(
      url,
      {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        Authorization: `Bearer ${CONFIG.apiKey}`,
      },
      JSON.stringify(payload)
    );
    const data = JSON.parse(responseText);
    if (data.error) {
      const message = typeof data.error === 'string' ? data.error : data.error.message || 'AI 接口报错';
      throw new Error(message);
    }
    const content = data?.choices?.[0]?.message?.content;
    const raw = typeof content === 'string'
      ? content
      : Array.isArray(content)
        ? content.map((part) => (typeof part === 'string' ? part : part?.text || '')).join('\n')
        : '';
    if (!raw.trim()) throw new Error('AI 返回内容为空（可检查模型是否支持文本）');
    return extractJson(raw);
  }

  // ---------------- 作答 ----------------
  function buildChoicePrompt(questions) {
    const body = questions.map((question) => {
      const options = question.options.map((option) => `${option.letter}. ${option.text}`).join('\n');
      return `第${question.index}题\n【题目原文（可能重复包含选项文字）】\n${question.stem}\n【结构化选项（以此为准）】\n${options}`;
    }).join('\n\n');
    return [
      '你是一位严谨的答题助手。请逐题仔细阅读题目原文和全部选项，先在思考中认真分析/计算，再给出答案；',
      '不要凭题号、选项位置或常见套路猜测；多选题必须给出全部正确选项，宁可按题目推理也不要漏选。',
      '题目原文里可能重复包含选项文字，以【结构化选项】的字母和内容为准。',
      '如果题目已作答或无法确定，不要编造，可以跳过。',
      '只输出一个 JSON，不要解释，格式：',
      '{"answers":[{"index":1,"answers":["B"]},{"index":2,"answers":["A","C"]}]}',
      '',
      body,
    ].join('\n');
  }

  function buildFillPrompt(items) {
    const body = items.map((item) => {
      return `第${item.index}题 第${item.slot}空\n【题目原文】\n${item.stem}${item.placeholder ? `\n（输入框提示：${item.placeholder}）` : ''}`;
    }).join('\n\n');
    return [
      '你是一位严谨的答题助手。页面中的每道题可能有 2~4 个空，下面每个条目对应【一个独立的空】。',
      '题目里的每一段连续下划线（___ / ____）也按从左到右的顺序视为第1空、第2空…。',
      '请逐空作答：每个空只填它自己的答案，绝对不要把同一道题的多个空合并成一个答案；',
      '答案要完整、简洁，只要答案本身，不要解释；无法确定可以跳过。',
      '只输出一个 JSON，格式：',
      '{"answers":[{"index":12,"slot":1,"answer":"维生素A"},{"index":12,"slot":2,"answer":"维生素D"}]}',
      '',
      body,
    ].join('\n');
  }

  function clickOption(option) {
    const input = option.input;
    if (input) {
      if (input.checked) return true;
      input.click();
      if (!input.checked) {
        // 有些平台把真实 input 隐藏了，只监听 label/父元素的鼠标事件
        const target = option.clickEl || input.parentElement || input;
        try {
          ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach((type) => {
            target.dispatchEvent(new MouseEvent(type, {
              bubbles: true, cancelable: true, view: window,
            }));
          });
        } catch (e) {}
      }
      return true;
    }
    if (option.clickEl) {
      option.clickEl.click();
      return true;
    }
    return false;
  }

  function focusAndSelectAll(el) {
    try {
      el.focus();
      const range = document.createRange();
      range.selectNodeContents(el);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      return true;
    } catch (e) {
      return false;
    }
  }

  function fillContentEditable(el, value) {
    try {
      focusAndSelectAll(el);
      let inserted = false;
      try {
        inserted = document.execCommand('insertText', false, value);
      } catch (e) {}
      if (!inserted || !textOf(el).includes(String(value).slice(0, 1))) {
        el.textContent = value;
      }
      try {
        el.dispatchEvent(new InputEvent('beforeinput', {
          bubbles: true, cancelable: true, inputType: 'insertText', data: value,
        }));
      } catch (e) {}
      try {
        el.dispatchEvent(new InputEvent('input', {
          bubbles: true, inputType: 'insertText', data: value,
        }));
      } catch (e) {
        el.dispatchEvent(new Event('input', { bubbles: true }));
      }
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'a' }));
      return true;
    } catch (e) {
      return false;
    }
  }

  function fillInput(input, value) {
    if (!input) return false;
    if (input.isContentEditable) {
      return fillContentEditable(input, value);
    }
    const proto = input.tagName === 'TEXTAREA'
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    try {
      if (descriptor && descriptor.set) {
        descriptor.set.call(input, value);
      } else {
        input.value = value;
      }
    } catch (e) {
      input.value = value;
    }
    if (String(input.value || '') !== String(value)) {
      // 某些受控输入框会拒绝直接赋值：聚焦后尝试浏览器原生插入
      try {
        input.focus();
        input.select();
        document.execCommand('insertText', false, value);
      } catch (e) {}
    }
    try {
      input.dispatchEvent(new InputEvent('input', {
        bubbles: true, inputType: 'insertText', data: value,
      }));
    } catch (e) {
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'a' }));
    return true;
  }

  async function applyChoiceAnswers(questions, answers) {
    let count = 0;
    for (const question of questions) {
      if (stopRequested) break;
      const item = answers.find((entry) => Number(entry.index) === question.index);
      if (!item) continue;
      const letters = normalizeLetters(item.answers != null ? item.answers : item.answer);
      if (!letters.length) continue;
      const chosen = [];
      for (const letter of letters) {
        const option = question.options.find((entry) => entry.letter === letter);
        if (!option) {
          log(`第${question.index}题答案字母 ${letter} 没有找到对应选项（页面选项：${question.options.map((o) => o.letter).join('、')}）`, 'warn');
          continue;
        }
        if (clickOption(option)) chosen.push(letter);
        await sleep(Math.max(30, Math.round(CONFIG.answerDelay / Math.max(1, letters.length))));
      }
      if (!chosen.length) {
        log(`第${question.index}题没有点到任何选项`, 'warn');
        continue;
      }
      if (chosen.length) {
        question.block.dataset[ANSWERED_FLAG] = '1';
        count += 1;
        log(`第${question.index}题：${chosen.join('、')}（已选）`, 'ok');
      }
    }
    return count;
  }

  async function applyFillAnswers(items, answers) {
    let count = 0;
    for (const item of items) {
      if (stopRequested) break;
      const entry = answers.find((a) => {
        if (Number(a.index) !== Number(item.index)) return false;
        if (a.slot == null) return true;
        return Number(a.slot) === Number(item.slot);
      });
      if (!entry) continue;
      const value = String(entry.answer == null ? '' : entry.answer).trim();
      if (!value) continue;
      if (fillInput(item.input, value)) {
        item.input.dataset[FILLED_FLAG] = '1';
        if (item.block) item.block.dataset[ANSWERED_FLAG] = '1';
        count += 1;
        log(`第${item.index}题 第${item.slot}空：${value}（已填）`, 'ok');
        await sleep(Math.max(50, CONFIG.answerDelay));
      }
    }
    return count;
  }

  function questionSignature(choices, fills) {
    const choicePart = choices.map((question) => (
      `${question.stem}::${question.options.map((option) => `${option.letter}.${option.text}`).join('|')}`
    )).join('||');
    const fillPart = fills.map((item) => `${item.stem}::${item.placeholder}`).join('||');
    return `${choicePart}###${fillPart}`;
  }

  // ---------------- 识别文本查看 ----------------
  function showPromptViewer() {
    ensurePanel();
    const overlay = document.createElement('div');
    overlay.style.cssText = [
      'position:fixed', 'left:50%', 'top:50%', 'transform:translate(-50%,-50%)',
      'z-index:2147483647', 'width:min(720px,92vw)', 'max-height:80vh',
      'display:flex', 'flex-direction:column', 'gap:8px', 'padding:14px',
      'background:#0b0f16', 'color:#e8eef8', 'border:1px solid #24324a',
      'border-radius:12px', 'box-shadow:0 12px 40px rgba(0,0,0,.6)',
      'font-family:"Microsoft YaHei UI",system-ui,sans-serif', 'font-size:12px',
    ].join(';');
    const title = document.createElement('div');
    title.textContent = '最近一次发送给 AI 的题目文本（可直接对比网页内容）';
    title.style.cssText = 'font-weight:700;color:#dbeafe;';
    const warn = document.createElement('div');
    warn.textContent = '提示：如果这里文字和网页上肉眼看到的题目不一致（乱码/生僻字），说明平台用了自定义字体反爬，油猴读到的不是视觉文字，请改用软件截图识别模式。';
    warn.style.cssText = 'color:#f59e0b;line-height:1.5;';
    const textarea = document.createElement('textarea');
    textarea.value = lastPromptText || '还没有发送过题目文本。先点「开始识别」或「开始答题」。';
    textarea.readOnly = true;
    textarea.style.cssText = [
      'width:100%', 'height:420px', 'max-height:60vh', 'resize:vertical',
      'background:#111827', 'color:#e5e7eb', 'border:1px solid #24324a',
      'border-radius:8px', 'padding:10px', 'font-family:Consolas,monospace',
      'font-size:12px', 'white-space:pre-wrap', 'box-sizing:border-box',
    ].join(';');
    const actions = document.createElement('div');
    actions.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;';
    const copyBtn = document.createElement('button');
    copyBtn.textContent = '复制文本';
    copyBtn.style.cssText = 'padding:7px 12px;border-radius:8px;border:0;background:#2563eb;color:#fff;cursor:pointer;';
    copyBtn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(textarea.value);
        copyBtn.textContent = '已复制';
      } catch (e) {
        textarea.focus();
        textarea.select();
        document.execCommand('copy');
        copyBtn.textContent = '已复制';
      }
    });
    const closeBtn = document.createElement('button');
    closeBtn.textContent = '关闭';
    closeBtn.style.cssText = 'padding:7px 12px;border-radius:8px;border:1px solid #3a4a66;background:#111827;color:#cbd5e1;cursor:pointer;';
    closeBtn.addEventListener('click', () => overlay.remove());
    actions.appendChild(copyBtn);
    actions.appendChild(closeBtn);
    overlay.appendChild(title);
    overlay.appendChild(warn);
    overlay.appendChild(textarea);
    overlay.appendChild(actions);
    document.body.appendChild(overlay);
  }

  // ---------------- 页面结构诊断 ----------------
  function diagnose() {
    ensurePanel();
    try {
      const roots = getRoots();
      const inputsAll = queryAll('input[type="radio"], input[type="checkbox"]');
      const inputsUsable = inputsAll.filter((input) => {
        const label = optionLabelFor(input);
        return isVisible(input) || isVisible(label);
      });
      const labels = queryAll('label');
      const candidates = candidateOptionElements();
      const editables = queryAll('[contenteditable="true"], [contenteditable=""]');
      const iframes = document.querySelectorAll('iframe').length;
      let fontFamilies = [];
      try {
        fontFamilies = Array.from(document.fonts || []).map((face) => face.family).filter(Boolean);
      } catch (e) {}
      const uniqueFonts = Array.from(new Set(fontFamilies)).slice(0, 10);
      const shadowCount = roots.length - 1;
      let configuredQuestions = 0;
      if (CONFIG.selectors.question) {
        try {
          configuredQuestions = queryAll(CONFIG.selectors.question).length;
        } catch (e) {}
      }
      const info = {
        url: location.href,
        radioCheckbox: inputsAll.length,
        usableInputs: inputsUsable.length,
        labels: labels.length,
        候选选项: candidates.length,
        可编辑区域: editables.length,
        iframes,
        自定义字体数: uniqueFonts.length,
        自定义字体: uniqueFonts,
        同源iframe或shadowRoot: shadowCount,
        配置的题目容器命中: configuredQuestions,
      };
      log(`诊断：${JSON.stringify(info)}`);
      if (uniqueFonts.length) {
        log('页面加载了自定义字体：如果「查看识别文本」和肉眼文字不一致，可能是字体反爬，请用软件截图识别模式', 'warn');
      }
      const sample = candidates.slice(0, 6).map((el) => textOf(el).slice(0, 60));
      if (sample.length) log(`候选选项示例：${JSON.stringify(sample)}`);
      const firstInput = inputsUsable[0];
      if (firstInput) {
        const block = findQuestionBlock(firstInput);
        log(`第一个题目区块文本：${textOf(block).slice(0, 160)}`);
      }
      if (!candidates.length && !configuredQuestions) {
        log('页面里没有发现常见选项结构：请按 F12 找到题目容器和选项元素的 class，填进 CONFIG.selectors.question / option', 'warn');
      }
      setStatus('诊断完成，详见日志');
    } catch (error) {
      log(`诊断出错：${error.message || error}`, 'err');
    }
  }

  async function runAnswer(manual = false, recognizeOnly = false) {
    if (running) return;
    ensurePanel();
    running = true;
    stopRequested = false;
    setStatus(recognizeOnly ? '扫描并识别页面…' : '扫描页面…');
    try {
      const choiceQuestions = (CONFIG.mode === 'choice' || CONFIG.mode === 'both') ? scanChoices() : [];
      const fillItems = (CONFIG.mode === 'fill' || CONFIG.mode === 'both') ? scanFills() : [];
      log(`识别到选择题 ${choiceQuestions.length} 道、填空题 ${fillItems.length} 道`);
      if (!choiceQuestions.length && !fillItems.length) {
        setStatus('没有找到可作答的题');
        log(
          '没有识别到题目：页面里没有可用的 radio/checkbox，也没有命中你配置的选择器。'
          + '点「扫描诊断」查看页面结构（radio/checkbox 数量、候选选项数量、iframe 数量）',
          'warn'
        );
        return;
      }
      const signature = questionSignature(choiceQuestions, fillItems);
      if (!manual && answeredSignatures.has(signature)) {
        log('这一页的题目已经答过，跳过重复作答');
        setStatus('已跳过（答过）');
        return;
      }
      let choiceCount = 0;
      let fillCount = 0;
      lastPromptText = '';
      if (choiceQuestions.length) {
        if (CONFIG.debug) {
          log(`第1题识别文本（前 120 字）：${choiceQuestions[0].stem.replace(/\s+/g, ' ').slice(0, 120)}`);
        }
        const prompt = buildChoicePrompt(choiceQuestions);
        lastPromptText = prompt;
        setStatus(`AI 识别 ${choiceQuestions.length} 道选择题…`);
        log('请求 AI 分析选择题（快速模式）…');
        const result = await askAI(prompt);
        if (stopRequested) {
          setStatus('已停止');
          return;
        }
        const answers = Array.isArray(result.answers) ? result.answers : [];
        if (!answers.length) {
          log('AI 没有返回选择题答案', 'warn');
        } else if (CONFIG.debug || recognizeOnly) {
          log(`AI 选择题返回：${JSON.stringify(answers).slice(0, 240)}`);
        }
        if (recognizeOnly) {
          log(
            answers.length
              ? `识别完成：${answers.length} 道选择题已分析，没有点击任何选项`
              : '识别完成：没有拿到选择题答案',
            answers.length ? 'ok' : 'warn'
          );
        } else {
          choiceCount = await applyChoiceAnswers(choiceQuestions, answers);
        }
        if (stopRequested) {
          setStatus('已停止');
          return;
        }
      }
      if (fillItems.length) {
        if (CONFIG.debug) {
          log(`填空题条目：${fillItems.map((item) => `${item.index}-${item.slot}`).join(' | ')}，第1条文本前 120 字：${fillItems[0].stem.replace(/\s+/g, ' ').slice(0, 120)}`);
        }
        const prompt = buildFillPrompt(fillItems);
        lastPromptText = lastPromptText ? `${lastPromptText}\n\n===== 填空 =====\n\n${prompt}` : prompt;
        setStatus(`AI 识别 ${fillItems.length} 道填空题…`);
        log('请求 AI 分析填空题（快速模式）…');
        const result = await askAI(prompt);
        if (stopRequested) {
          setStatus('已停止');
          return;
        }
        const answers = Array.isArray(result.answers) ? result.answers : [];
        if (!answers.length) {
          log('AI 没有返回填空题答案', 'warn');
        } else if (CONFIG.debug || recognizeOnly) {
          log(`AI 填空题返回：${JSON.stringify(answers).slice(0, 260)}`);
        }
        if (recognizeOnly) {
          log(
            answers.length
              ? `识别完成：${answers.length} 个填空答案已分析，没有填写任何输入框`
              : '识别完成：没有拿到填空题答案',
            answers.length ? 'ok' : 'warn'
          );
        } else {
          fillCount = await applyFillAnswers(fillItems, answers);
        }
        if (stopRequested) {
          setStatus('已停止');
          return;
        }
      }
      if (recognizeOnly) {
        setStatus('识别完成（未点击）');
        log('识别完成：已显示分析结果，没有点击选项/填写输入框；确认后可点「开始答题」', 'ok');
        return;
      }
      if (!stopRequested) {
        answeredSignatures.add(signature);
        setStatus(`完成：选择题 ${choiceCount} 道，填空题 ${fillCount} 道`);
        log(`作答完成：选择题 ${choiceCount} 道，填空题 ${fillCount} 道`, 'ok');
        if (CONFIG.selectors.nextButton) {
          try {
            const next = document.querySelector(CONFIG.selectors.nextButton);
            if (next && isVisible(next)) {
              log('点击「下一题」…');
              next.click();
            }
          } catch (e) {}
        }
      } else {
        setStatus('已停止');
      }
    } catch (error) {
      if (stopRequested) {
        setStatus('已停止');
        log('本次作答已停止', 'warn');
      } else {
        log(`出错：${error.message || error}`, 'err');
        setStatus(`出错：${error.message || error}`);
      }
    } finally {
      running = false;
      stopManualToggleGuard();
    }
  }

  // ---------------- 面板 ----------------
  function ensurePanel() {
    if (panel || !document.body) return;
    const host = document.createElement('div');
    host.id = 'sc-text-answer-host';
    host.style.cssText = [
      'position:fixed', 'right:18px', 'bottom:18px', 'z-index:2147483647',
      'font-family:"Microsoft YaHei UI",system-ui,sans-serif', 'font-size:13px',
    ].join(';');

    const toggle = document.createElement('button');
    toggle.textContent = '文本答题';
    toggle.style.cssText = [
      'height:38px', 'padding:0 16px', 'border-radius:19px', 'border:1px solid #3b82f6',
      'background:#2563eb', 'color:#fff', 'font-size:13px', 'font-weight:600',
      'cursor:pointer', 'box-shadow:0 4px 14px rgba(0,0,0,.35)',
    ].join(';');

    panel = document.createElement('div');
    panel.style.cssText = [
      'display:none', 'width:360px', 'max-height:320px', 'overflow:auto',
      'margin-bottom:10px', 'padding:12px', 'border-radius:12px',
      'background:#0b0f16', 'border:1px solid #24324a', 'color:#e8eef8',
      'box-shadow:0 10px 30px rgba(0,0,0,.55)',
    ].join(';');
    // 面板内的点击不要冒泡给答题页面，避免页面全局点击处理器改复选框状态
    panel.addEventListener('click', (event) => event.stopPropagation());
    panel.addEventListener('mousedown', (event) => event.stopPropagation());

    const title = document.createElement('div');
    title.textContent = '智能点击助手 · 网页文字答题 v1.9.0';
    title.style.cssText = 'font-weight:700;margin-bottom:8px;color:#dbeafe;';
    panel.appendChild(title);

    const startRow = document.createElement('div');
    startRow.style.cssText = 'display:flex;margin-bottom:8px;border-radius:8px;overflow:hidden;border:1px solid #1d4ed8;';
    const recognizeBtn = document.createElement('button');
    recognizeBtn.type = 'button';
    recognizeBtn.textContent = '开始识别';
    recognizeBtn.style.cssText = 'flex:1;padding:9px 10px;border:0;background:#2563eb;color:#fff;cursor:pointer;font-weight:600;';
    const startDivider = document.createElement('span');
    startDivider.style.cssText = 'width:1px;background:#1e40af;flex:0 0 1px;';
    const answerBtn = document.createElement('button');
    answerBtn.type = 'button';
    answerBtn.textContent = '开始答题';
    answerBtn.style.cssText = 'flex:1;padding:9px 10px;border:0;background:#1d4ed8;color:#fff;cursor:pointer;font-weight:600;';
    recognizeBtn.addEventListener('click', (event) => {
      if (event) {
        event.preventDefault();
        event.stopPropagation();
      }
      resetManualRunToggles();
      log(`开始识别（只分析，不点击；快速模式；自动运行：关，v1.9.0）`);
      runAnswer(true, true);
    });
    answerBtn.addEventListener('click', (event) => {
      if (event) {
        event.preventDefault();
        event.stopPropagation();
      }
      resetManualRunToggles();
      log(`开始答题（识别并点击；快速模式；自动运行：关，v1.9.0）`);
      runAnswer(true, false);
    });
    startRow.appendChild(recognizeBtn);
    startRow.appendChild(startDivider);
    startRow.appendChild(answerBtn);
    const startHint = document.createElement('div');
    startHint.textContent = '开始识别：只读取并分析；开始答题：分析后自动点击/填写';
    startHint.style.cssText = 'color:#93a4bd;font-size:11px;margin:-4px 0 8px;';
    const stopBtn = document.createElement('button');
    stopBtn.type = 'button';
    stopBtn.textContent = '停止';
    stopBtn.style.cssText = 'padding:8px 10px;border-radius:8px;border:1px solid #3a4a66;background:#111827;color:#cbd5e1;cursor:pointer;';
    stopBtn.addEventListener('click', (event) => {
      if (event) {
        event.preventDefault();
        event.stopPropagation();
      }
      stopManualToggleGuard();
      stopRequested = true;
      setAutoEnabled(false);
      if (scheduledTimer) {
        clearTimeout(scheduledTimer);
        scheduledTimer = null;
      }
      if (activeRequest) {
        try {
          activeRequest.abort();
        } catch (e) {}
        activeRequest = null;
      }
      setStatus('已停止（自动运行已关闭）');
      log('已停止：本次运行中止，自动运行关闭；需要时点「开始识别」或「开始答题」', 'warn');
    });
    const diagBtn = document.createElement('button');
    diagBtn.type = 'button';
    diagBtn.textContent = '扫描诊断';
    diagBtn.style.cssText = 'padding:8px 10px;border-radius:8px;border:1px solid #3a4a66;background:#111827;color:#93c5fd;cursor:pointer;';
    diagBtn.addEventListener('click', diagnose);
    const promptBtn = document.createElement('button');
    promptBtn.type = 'button';
    promptBtn.textContent = '查看识别文本';
    promptBtn.style.cssText = 'padding:8px 10px;border-radius:8px;border:1px solid #3a4a66;background:#111827;color:#cbd5e1;cursor:pointer;';
    promptBtn.addEventListener('click', showPromptViewer);
    const buttons = document.createElement('div');
    buttons.style.cssText = 'display:flex;gap:8px;margin-bottom:8px;';
    buttons.appendChild(stopBtn);
    buttons.appendChild(diagBtn);
    buttons.appendChild(promptBtn);
    panel.appendChild(startRow);
    panel.appendChild(startHint);
    panel.appendChild(buttons);

    const autoRow = document.createElement('label');
    autoRow.style.cssText = 'display:flex;align-items:center;gap:6px;margin-bottom:8px;color:#cbd5e1;font-size:12px;cursor:pointer;';
    autoCheckbox = document.createElement('input');
    autoCheckbox.type = 'checkbox';
    autoCheckbox.autocomplete = 'off';
    autoCheckbox.setAttribute('autocomplete', 'off');
    autoCheckbox.checked = autoEnabled;
    autoCheckbox.addEventListener('change', () => setAutoEnabled(autoCheckbox.checked));
    const autoText = document.createElement('span');
    autoText.textContent = '自动运行（打开页面后自动扫描作答，默认关闭）';
    autoRow.appendChild(autoCheckbox);
    autoRow.appendChild(autoText);
    panel.appendChild(autoRow);

    statusEl = document.createElement('div');
    statusEl.textContent = (autoEnabled ? '自动运行：开' : '自动运行：关') + ' · 快速模式';
    statusEl.style.cssText = 'color:#93c5fd;margin-bottom:8px;';
    panel.appendChild(statusEl);

    logBox = document.createElement('div');
    logBox.style.cssText = 'max-height:190px;overflow:auto;line-height:1.55;font-size:12px;';
    panel.appendChild(logBox);

    toggle.addEventListener('click', () => {
      const shown = panel.style.display === 'block';
      panel.style.display = shown ? 'none' : 'block';
      if (!shown && CONFIG.debug) log('面板已打开；「开始识别」只分析，「开始答题」分析后点击');
    });

    host.appendChild(panel);
    host.appendChild(toggle);
    document.body.appendChild(host);
    log('脚本已加载，等待页面题目出现…');
  }

  // ---------------- 自动运行 ----------------
  function resetManualRunToggles() {
    stopRequested = false;
    // 手动点击「开始识别/开始答题」= 单次运行，绝不打开自动运行
    setAutoEnabled(false);
    if (autoCheckbox) autoCheckbox.checked = false;
    // 兜底：页面脚本/浏览器表单恢复有时会在点击后把自动运行重新勾上；
    // 手动运行期间每 150ms 强制同步一次。
    if (manualToggleGuard) clearInterval(manualToggleGuard);
    manualToggleGuard = setInterval(() => {
      if (!running) {
        clearInterval(manualToggleGuard);
        manualToggleGuard = null;
        return;
      }
      if (autoEnabled) setAutoEnabled(false);
      if (autoCheckbox && autoCheckbox.checked) autoCheckbox.checked = false;
    }, 150);
    log(`启动前状态：自动运行=${autoEnabled ? '开' : '关'}，模型=快速模式`, 'muted');
  }

  function stopManualToggleGuard() {
    if (manualToggleGuard) {
      clearInterval(manualToggleGuard);
      manualToggleGuard = null;
    }
  }

  function setAutoEnabled(enabled) {
    const next = Boolean(enabled);
    const changed = autoEnabled !== next;
    autoEnabled = next;
    if (autoCheckbox) autoCheckbox.checked = autoEnabled;
    if (!autoEnabled && scheduledTimer) {
      clearTimeout(scheduledTimer);
      scheduledTimer = null;
    }
    if (!changed) return;
    if (autoEnabled) {
      scheduleAutoRun(CONFIG.autoRunDelay);
      setStatus('自动运行：开');
      log('自动运行已开启：页面变化后会自动扫描作答', 'warn');
    } else {
      setStatus('自动运行：关');
      log('自动运行已关闭', 'info');
    }
  }

  function scheduleAutoRun(delay) {
    if (!autoEnabled || stopRequested) return;
    if (scheduledTimer) clearTimeout(scheduledTimer);
    scheduledTimer = setTimeout(() => {
      scheduledTimer = null;
      if (!autoEnabled || stopRequested || running) return;
      const choices = (CONFIG.mode === 'choice' || CONFIG.mode === 'both') ? scanChoices() : [];
      const fills = (CONFIG.mode === 'fill' || CONFIG.mode === 'both') ? scanFills() : [];
      if (choices.length || fills.length) {
        runAnswer(false);
      }
    }, Math.max(300, delay || CONFIG.autoRunDelay));
  }

  function startObserver() {
    if (!document.body) return;
    const observer = new MutationObserver(() => {
      if (!autoEnabled || running || stopRequested) return;
      scheduleAutoRun(CONFIG.autoRunDelay);
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function init() {
    // 自动运行不做持久化：每次打开页面都默认关闭，只对当前页面手动勾选生效
    autoEnabled = false;
    ensurePanel();
    startObserver();
    if (autoEnabled) {
      scheduleAutoRun(CONFIG.autoRunDelay);
    } else {
      log('脚本已就绪：自动运行关闭，点「文本答题」→「开始识别」或「开始答题」', 'info');
    }
    log('当前模式：固定快速模式（已移除深度思考）', 'info');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
