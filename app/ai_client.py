"""OpenAI 兼容的视觉大模型客户端（纯标准库 urllib 实现）。

把"题目截图"发送给支持视觉的多模态模型（GLM-4V、Qwen-VL、GPT-4o 等），
解析模型回答中的最终选项字母。

只依赖标准库；没有任何第三方 SDK。
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4v-flash"

#: 发给模型的提示词：要求先推理、最后一行严格给出答案字母
DEFAULT_PROMPT = (
    "你是一个严谨的答题助手。请阅读图片中的选择题（题干和选项），分析后给出唯一正确答案。\n"
    "要求：\n"
    "1. 用 1~3 句话简要说明判断依据；\n"
    "2. 最后单独一行严格输出：答案：X（X 为选项字母，例如 A、B、C、D）。\n"
    "不要输出与题目无关的内容。"
)

#: 自动定位选项 / 下一题按钮的提示词：要求只输出 JSON
OPTION_PROMPT = (
    "请分析这张答题界面截图，完成两件事：\n"
    "1. 先数清页面实际有多少个选项：可能只有 A~C，也可能有 A~F；"
    "看到几个就返回几个，绝不能只返回 A~D 而漏掉 E/F；"
    "找出每个选项（A、B、C、D、E、F）的可点击中心位置"
    "（通常是选项字母圆圈中心或整行选项中心）；\n"
    "2. 找出「下一题」/「下一页」按钮的中心位置（如果截图中没有该按钮，则 next 设为 null）。\n"
    "坐标使用相对比例：图片左上角为 (0,0)，右下角为 (1,1)，x 是横向比例，y 是纵向比例。\n"
    "只输出一个 JSON 对象，不要输出任何其它文字或 Markdown 代码块，格式如下：\n"
    '{"options":[{"letter":"A","x":0.08,"y":0.62},{"letter":"B","x":0.08,"y":0.72}],'
    '"next":{"x":0.86,"y":0.93}}\n'
    "如果位置只能估计，也请尽量给出估计值。"
)

#: 实时答题提示词：一次请求同时返回"答案字母 + 各选项位置 + 下一题位置"
ANSWER_LAYOUT_PROMPT = (
    "这是一道选择题的截图，可能是单选题，也可能是多选题（每题可能有 2~5 个正确选项）。请完成三件事：\n"
    "1. 判断题型，给出所有正确答案字母，放进 answers 数组（单选题只有 1 个，多选题列出全部正确选项）；\n"
    "2. 先数清页面实际有多少个选项：可能只有 A~C，也可能有 A~F；"
    "看到几个就返回几个，绝不能只返回 A~D 而漏掉 E/F；"
    "找出每个选项（A、B、C、D、E、F）在图片中的可点击中心位置（选项字母圆圈中心或整行中心）；\n"
    "3. 找出「下一题」/「下一页」/「提交」按钮的中心位置（若没有则 next 设为 null）。\n"
    "坐标使用相对比例：图片左上角为 (0,0)，右下角为 (1,1)，x 是横向比例，y 是纵向比例。\n"
    "只输出一个 JSON 对象，不要输出任何其它文字或 Markdown 代码块，格式如下：\n"
    '{"type":"single","answers":["B"],"options":[{"letter":"A","x":0.08,"y":0.62},'
    '{"letter":"B","x":0.08,"y":0.72},{"letter":"C","x":0.08,"y":0.82},'
    '{"letter":"D","x":0.08,"y":0.92}],"next":{"x":0.86,"y":0.93}}\n'
    "多选题示例：{\"type\":\"multiple\",\"answers\":[\"A\",\"C\"],...}。"
    "如果位置只能估计，也请尽量给出估计值。"
)

#: 整页多题提示词：一页里可能有多道题，一次返回所有未作答题目的答案与位置
PAGE_PROMPT = (
    "这是一张答题页面截图，里面可能包含 1~6 道选择题（题目按 1、2、3… 排列），"
    "其中可能有单选题，也可能有多选题（每题 2~5 个正确选项）。请完成：\n"
    "1. 逐题阅读并分析，判断每道题的题型和所有正确答案；\n"
    "2. 逐个选项检查当前是否已经选中：已选中的选项通常按钮是蓝色/主题色、背景高亮、"
    "边框加粗、打勾或填充圆点；把已经选中的字母放进该题的 selected 数组；\n"
    "3. 不要因为某题已经选中了部分选项就跳过整题！多选题即使已经选中 A、B，"
    "也要返回完整答案、全部选项位置和 selected；程序只会补点未选中的正确选项，不会重复点已选项；\n"
    "4. 对每道题给出所有选项（A~F）在图片中的可点击中心位置；"
    "注意选项数量不固定，可能只有 A~C，也可能有 A~F，看到几个就返回几个，"
    "绝不能只返回 A~D 而漏掉 E/F；\n"
    "5. 每道题额外给出 key：该题题干的前 20 个汉字/字符（不要包含选项文字），用于程序跨滚动去重；\n"
    "6. 找出「下一题 / 下一页 / 提交 / 继续」按钮中心位置，没有则 next 设为 null。\n"
    "坐标使用相对比例：图片左上角为 (0,0)，右下角为 (1,1)，x 是横向比例，y 是纵向比例。\n"
    "只输出一个 JSON 对象，不要输出任何其它文字或 Markdown 代码块，格式如下：\n"
    '{"questions":[{"index":1,"key":"题干前20字","type":"single","answers":["B"],'
    '"selected":["A"],"options":['
    '{"letter":"A","x":0.08,"y":0.30},{"letter":"B","x":0.08,"y":0.38},'
    '{"letter":"C","x":0.08,"y":0.46},{"letter":"D","x":0.08,"y":0.54}]},'
    '{"index":2,"key":"另一题题干前20字","type":"multiple","answers":["A","C"],'
    '"selected":["A"],"options":['
    '{"letter":"A","x":0.08,"y":0.66},{"letter":"B","x":0.08,"y":0.74},'
    '{"letter":"C","x":0.08,"y":0.82},{"letter":"D","x":0.08,"y":0.90}]}],'
    '"next":{"x":0.86,"y":0.95}}\n'
    "特别注意：多选题通常可能有多达 3~4 个正确选项，必须逐项判断所有选项，"
    "不要只选其中一两个，也不要把多选题当成单选题；"
    "多选题必须把全部正确选项字母都放进 answers 数组；"
    "selected 只放“画面上已经选中”的字母，不要把答案字母误当成已选中；"
    "如果某题的位置只能估计，也请尽量给出估计值；如果页面里没有题目，questions 输出空数组 []。"
)

#: 填空题提示词：只返回题号、第几空、答案和答案输入框位置
FILL_PROMPT = (
    "这是一张答题页面截图，里面可能有填空题、简答题或多空填空题。"
    "每道题可能有 1~4 个空（页面上通常写着第1空、第2空…，也会用连续的 ___ / ____ 下划线表示）。"
    "题目里的每一段连续下划线也按从左到右的顺序视为第1空、第2空…，不要因为页面上没有“第N空”文字就跳过。请完成：\n"
    "1. 逐个空作答：每个空单独一条 item；index 是题号，slot 是这道题的第几空（从 1 开始），"
    "answer 只填【这一个空】的答案；同一道题有多个空时，必须返回多条 item，"
    "绝对不要把整道题的多个答案合并成一条；\n"
    "2. 找出这个空对应【答案输入框】在图片中的中心位置；坐标必须严格落在该空自己的输入框内部，"
    "多空题按从上到下依次对应第1空、第2空…，不要整体错位到下一行；"
    "同时给出这个输入框的 bbox（相对比例 [左, 上, 右, 下]），bbox 只包住输入框本身；\n"
    "3. 如果某个空已经填过答案，跳过这个空；\n"
    "4. 找出「提交 / 下一题 / 下一页 / 继续」按钮中心位置，没有则 next 设为 null。\n"
    "坐标使用相对比例：图片左上角为 (0,0)，右下角为 (1,1)，x 是横向比例，y 是纵向比例。\n"
    "只输出一个 JSON 对象，不要输出任何其它文字或 Markdown 代码块，格式如下：\n"
    '{"items":[{"index":12,"slot":1,"answer":"维生素A","x":0.42,"y":0.18,'
    '"bbox":[0.18,0.15,0.72,0.21]},'
    '{"index":12,"slot":2,"answer":"维生素D","x":0.42,"y":0.28,'
    '"bbox":[0.18,0.25,0.72,0.31]},'
    '{"index":13,"slot":1,"answer":"蛋白质","x":0.42,"y":0.55,'
    '"bbox":[0.18,0.52,0.72,0.58]}],'
    '"next":{"x":0.86,"y":0.93}}\n'
    "答案要简洁（如词语、数字、短语），但必须是对应这一空的完整答案；"
    "如果某题没有输入框或无法确定位置，可以不给坐标，但不要编造答案。"
)

#: 覆盖模式：已有内容的空也重新作答（用于修正之前填错的答案）
FILL_OVERWRITE_PROMPT = FILL_PROMPT.replace(
    "3. 如果某个空已经填过答案，跳过这个空；",
    "3. 即使某个空已经有内容，也要返回这个空：忽略输入框里原有的内容，"
    "按题目重新判断这个空应填的正确答案；",
)

_ANSWER_PATTERNS = [
    r"(?:正确|最终|标准)?答案\s*[:：]\s*([A-Fa-f])",
    r"(?:正确|最终|标准)?答案\s*(?:是|为)\s*[:：]?\s*([A-Fa-f])",
    r"(?:正确|最终|标准)?答案\s*[（(]\s*([A-Fa-f])\s*[)）]",
    r"\banswer\s*(?:is|:)\s*([A-Fa-f])\b",
    r"(?:选项|选)\s*[:：]?\s*([A-Fa-f])",
]

#: 解析「答案」后面整段文本，用于识别多选（如 答案：A、C）
_ANSWER_LIST_PATTERNS = [
    r"(?:正确|最终|标准)?答案\s*[:：]\s*([^\n]+)",
    r"(?:正确|最终|标准)?答案\s*(?:是|为)\s*[:：]?\s*([^\n]+)",
    r"(?:正确|最终|标准)?答案\s*[（(]\s*([^)）\n]+)",
    r"\banswers?\s*(?:is|are|:)\s*([^\n]+)",
    r"(?:正确选项|选项|选)\s*[:：]\s*([^\n]+)",
]

_FULLWIDTH_MAP = str.maketrans("ＡＢＣＤＥＦａｂｃｄｅｆ", "ABCDEFABCDEF")


def _extract_letters(segment: str) -> list[str]:
    """从一段文字中提取 A-F 选项字母（支持 A、C / AC / A,C 等写法）。"""
    letters: list[str] = []
    for match in re.finditer(r"(?<![A-Za-z])([A-Fa-f]{1,6})(?![A-Za-z])", segment or ""):
        for char in match.group(1):
            letter = char.upper()
            if letter not in letters:
                letters.append(letter)
    return sorted(letters)


def _answer_letters_from(value) -> list[str]:
    """兼容 answers 数组 / answer 字符串等 AI 返回结构。"""
    if isinstance(value, (list, tuple)):
        return _extract_letters(" ".join(str(item) for item in value))
    if isinstance(value, str):
        return _extract_letters(value)
    return []


def parse_answers(text: str) -> list[str]:
    """从模型返回文本中解析答案字母列表（支持单选和多选）。"""
    normalized = (text or "").translate(_FULLWIDTH_MAP)
    if not normalized.strip():
        raise AIError("AI 返回内容为空")
    # 1) 关键词模式：取最后一处答案（多选题可能一次给出多个字母）
    for pattern in _ANSWER_LIST_PATTERNS:
        matches = re.findall(pattern, normalized)
        if matches:
            letters = _extract_letters(matches[-1])
            if letters:
                return letters
    # 2) 兜底：在最后几行里找独立的 A-F 单词
    lines = [ln.strip() for ln in normalized.strip().splitlines() if ln.strip()]
    for line in reversed(lines[-3:]):
        letters = _extract_letters(line)
        if letters:
            return letters
    raise AIError("未从 AI 返回中找到答案字母（A-F），请检查模型是否支持视觉输入或更换模型")


class AIError(Exception):
    """AI 接口调用 / 返回解析失败。"""


@dataclass
class AIConfig:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    timeout_s: float = 60.0
    temperature: float = 0.0
    proxy: str = ""
    #: 是否开启思考模式（仅 DeepSeek 有效；默认关闭，避免长思考导致超时/空回复）
    enable_thinking: bool = False

    def display(self) -> str:
        return f"{self.model} @ {self.base_url}"


def is_deepseek_endpoint(base_url: str) -> bool:
    """判断是否是 DeepSeek 官方接口（只有它需要/支持 thinking 参数）。"""
    try:
        host = (urllib.parse.urlparse(str(base_url).strip()).hostname or "").lower()
    except ValueError:
        return False
    return host == "deepseek.com" or host.endswith(".deepseek.com")


#: DeepSeek 当前支持的模型名；旧脚本里常见的 deepseek-v4-flash 并不是有效名。
DEEPSEEK_MODELS = ("deepseek-flash", "deepseek-v4-pro")
DEEPSEEK_MODEL_ALIASES = {
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-flash-v4": "deepseek-flash",
    "deepseek-v4": "deepseek-v4-pro",
}


def normalize_model_name(base_url: str, model: str) -> str:
    """把常见错误写法归一化；未知 DeepSeek 模型直接报可读错误。"""
    name = str(model).strip()
    if not is_deepseek_endpoint(base_url):
        return name
    name = DEEPSEEK_MODEL_ALIASES.get(name.lower(), name)
    if name not in DEEPSEEK_MODELS:
        raise AIError(
            f"DeepSeek 只支持模型 {' / '.join(DEEPSEEK_MODELS)}，"
            f"当前填的是“{model}”。请把模型改成 deepseek-flash 后重试。"
        )
    return name


@dataclass
class AIAnswer:
    letter: str
    raw: str
    elapsed_ms: float = 0.0
    #: 引擎使用的绝对屏幕点击坐标（由 PCController 换算后填入）
    click_points: dict[str, tuple[int, int]] | None = None
    next_click: tuple[int, int] | None = None
    #: 多选题可能有多个正确选项；单选时就是 [letter]
    letters: list[str] = field(default_factory=list)

    @property
    def reasoning(self) -> str:
        """去掉最后一行"答案：X"后的推理文本（若存在）。"""
        lines = [ln for ln in self.raw.strip().splitlines()]
        if lines and re.search(r"答案\s*[:：]|[Aa]nswer", lines[-1]):
            return "\n".join(lines[:-1]).strip()
        return self.raw.strip()

    def all_letters(self) -> list[str]:
        """返回所有答案字母；兼容只有 letter 的旧数据。"""
        letters = [str(item).upper() for item in (self.letters or []) if str(item).strip()]
        if letters:
            return letters
        return [self.letter.upper()] if self.letter else []


@dataclass
class AILayoutAnswer(AIAnswer):
    """同时包含答案和选项相对位置的 AI 回答（0~1 比例）。"""

    norm_points: dict[str, tuple[float, float]] = field(default_factory=dict)
    norm_next: tuple[float, float] | None = None


@dataclass
class PageQuestion:
    """整页多题模式中，一道题的答案与选项位置。"""

    index: int
    letter: str
    norm_points: dict[str, tuple[float, float]] = field(default_factory=dict)
    #: 多选时包含全部正确选项字母
    letters: list[str] = field(default_factory=list)
    #: 题干摘要（用于跨滚动去重，防止同一题重复点击）
    key: str = ""
    #: AI 判断该题是否已经作答（选项有高亮/勾选等）
    answered: bool = False
    #: 画面上已经选中的选项字母（用于避免重复点击把选中取消）
    selected: list[str] = field(default_factory=list)


@dataclass
class PageAnswer:
    """整页多题模式的 AI 回答。"""

    questions: list[PageQuestion] = field(default_factory=list)
    norm_next: tuple[float, float] | None = None
    raw: str = ""
    elapsed_ms: float = 0.0
    #: 引擎使用的绝对屏幕坐标（由 PCController 换算后填入）
    abs_points: list[dict[str, tuple[int, int]]] = field(default_factory=list)
    next_click: tuple[int, int] | None = None


@dataclass
class FillItem:
    """填空题：题号、第几空、答案文本、答案输入框相对位置。"""

    index: int
    answer: str
    norm_point: tuple[float, float] | None = None
    #: 同一道题的第几个空（1 开始，0 表示 AI 没给）
    slot: int = 0
    #: 输入框相对 bbox（左、上、右、下，0~1）
    norm_bbox: tuple[float, float, float, float] | None = None
    #: 引擎使用的绝对屏幕坐标（由 PCController 换算后填入）
    abs_point: tuple[int, int] | None = None


@dataclass
class FillAnswer:
    """填空题页面的 AI 返回。"""

    items: list[FillItem] = field(default_factory=list)
    norm_next: tuple[float, float] | None = None
    raw: str = ""
    elapsed_ms: float = 0.0
    next_click: tuple[int, int] | None = None


@dataclass
class OptionLayout:
    """AI 视觉定位出的选项 / 下一题相对坐标（0~1）。"""

    points: dict[str, tuple[float, float]]
    next_point: tuple[float, float] | None
    raw: str
    elapsed_ms: float = 0.0


def validate_config(config: AIConfig | None) -> tuple[bool, str]:
    """检查 AI 配置是否完整。"""
    if config is None:
        return False, "请先填写 AI 接口配置"
    if not str(config.base_url).strip().lower().startswith(("http://", "https://")):
        return False, "API 地址需要以 http:// 或 https:// 开头"
    if not str(config.api_key).strip():
        return False, "请填写 API Key"
    if not str(config.model).strip():
        return False, "请填写模型名称"
    return True, ""


def parse_answer(text: str) -> str:
    """从模型返回文本中解析最终答案字母（单选；多选取第一个）。"""
    return parse_answers(text)[0]


def _endpoint(base_url: str) -> str:
    url = str(base_url).strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def _extract_content(content) -> str:
    """兼容 content 为字符串 / 多段内容列表两种返回结构。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content or "")


def _open_url(request: urllib.request.Request, timeout: float, proxy: str):
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    else:
        handler = urllib.request.ProxyHandler({})  # 默认直连，避免系统代理干扰国内接口
    opener = urllib.request.build_opener(handler)
    return opener.open(request, timeout=timeout)


def _post_chat(
    image_png: bytes,
    config: AIConfig,
    prompt: str,
    max_tokens: int = 800,
) -> tuple[str, float]:
    """向 OpenAI 兼容接口发送一条"文本 + 图片"消息，返回 (文本, 耗时ms)。"""
    ok, message = validate_config(config)
    if not ok:
        raise AIError(message)
    if not image_png:
        raise AIError("题目截图为空")

    started = time.perf_counter()
    b64 = base64.b64encode(image_png).decode("ascii")
    deepseek = is_deepseek_endpoint(config.base_url)
    token_limit = int(max_tokens)
    if deepseek and config.enable_thinking:
        token_limit = max(token_limit, 4000)
    payload = {
        "model": normalize_model_name(config.base_url, config.model),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
        "temperature": float(config.temperature),
        "max_tokens": token_limit,
        "stream": False,
    }
    if deepseek:
        # DeepSeek 思考模式默认开启，长思考会导致响应很慢甚至被服务端中断；
        # 答题/坐标识别场景默认关闭，用户可在界面里打开。
        payload["thinking"] = {"type": "enabled" if config.enable_thinking else "disabled"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        _endpoint(config.base_url),
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {config.api_key.strip()}",
        },
    )

    raw_bytes = None
    last_error: Exception | None = None
    for attempt in (1, 2, 3):
        try:
            with _open_url(request, max(5.0, float(config.timeout_s)), config.proxy.strip()) as resp:
                raw_bytes = resp.read()
            break
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:600]
            except Exception:
                pass
            raise AIError(f"AI 接口返回 HTTP {exc.code}：{detail or exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < 3:
                # 远程断开/网络抖动：退避后重试，最多 3 次
                time.sleep(1.0 if attempt == 1 else 2.0)
                continue

    if raw_bytes is None:
        hint = "请检查网络/代理设置；已自动重试 3 次"
        if deepseek:
            hint += "；若使用 DeepSeek，请保持「允许模型思考」关闭后重试"
        if isinstance(last_error, TimeoutError):
            raise AIError(f"AI 接口请求超时，{hint}") from last_error
        if isinstance(last_error, urllib.error.URLError):
            reason = getattr(last_error, "reason", last_error)
            raise AIError(f"无法连接 AI 接口（{config.base_url}）：{reason}；{hint}") from last_error
        raise AIError(f"AI 请求失败：{last_error}；{hint}") from last_error

    try:
        data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise AIError("AI 接口返回的内容不是有效 JSON，请检查 API 地址是否正确") from exc
    if not isinstance(data, dict):
        raise AIError("AI 接口返回格式异常")
    if data.get("error"):
        error = data["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise AIError(f"AI 接口报错：{message}")
    choices = data.get("choices") or []
    if not choices:
        raise AIError("AI 接口没有返回 choices，请检查模型名称是否正确")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message_obj = first.get("message") or {}
    content = message_obj.get("content")
    if content is None:
        content = first.get("text") or ""
    raw_text = _extract_content(content).strip()
    if not raw_text:
        reasoning = _extract_content(message_obj.get("reasoning_content")).strip()
        if reasoning:
            raise AIError(
                "模型开启了思考模式并消耗完输出长度，没有给出最终答案。"
                "请在「AI 接口设置」中关闭「允许模型思考」后重试"
            )
        raise AIError("AI 返回内容为空")
    return raw_text, (time.perf_counter() - started) * 1000.0


def ask_question(
    image_png: bytes,
    config: AIConfig,
    prompt: str = DEFAULT_PROMPT,
) -> AIAnswer:
    """把 PNG 截图发送给多模态模型，返回解析后的答案。

    失败抛出 :class:`AIError`（带中文可读信息）。
    """
    raw_text, elapsed_ms = _post_chat(image_png, config, prompt, max_tokens=800)
    letters = parse_answers(raw_text)
    return AIAnswer(
        letter=letters[0], raw=raw_text, elapsed_ms=elapsed_ms, letters=letters
    )


def _extract_json_object(text: str) -> dict:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise AIError("AI 没有返回 JSON 格式的选项位置，请重试或换一个视觉模型")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AIError("AI 返回的选项位置 JSON 无法解析，请重试") from exc
    if not isinstance(data, dict):
        raise AIError("AI 返回的选项位置格式不正确")
    return data


def _parse_norm_bbox(value) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if not all(-0.02 <= item <= 1.02 for item in (x1, y1, x2, y2)):
        return None
    x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
    if x2 - x1 < 0.005 or y2 - y1 < 0.003:
        return None
    return x1, y1, x2, y2


def _parse_norm_point(value) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        nx = float(value.get("x"))
        ny = float(value.get("y"))
    except (TypeError, ValueError):
        return None
    if not (-0.02 <= nx <= 1.02 and -0.02 <= ny <= 1.02):
        return None
    return max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))


def parse_option_layout(
    data: dict, min_points: int = 2
) -> tuple[dict[str, tuple[float, float]], tuple[float, float] | None]:
    """从 AI 返回的 JSON 中解析选项相对坐标与下一题坐标。"""
    raw_options = data.get("options")
    points: dict[str, tuple[float, float]] = {}
    if isinstance(raw_options, dict):
        items = []
        for letter, point in raw_options.items():
            items.append({"letter": letter, **(point if isinstance(point, dict) else {})})
    elif isinstance(raw_options, list):
        items = raw_options
    else:
        raise AIError("AI 返回 JSON 中缺少 options 字段")
    for item in items:
        if not isinstance(item, dict):
            continue
        letter = str(item.get("letter", "")).strip().upper()
        if letter not in "ABCDEF":
            continue
        point = _parse_norm_point(item)
        if point is not None:
            points[letter] = point
    if len(points) < min_points:
        raise AIError("AI 识别到的选项位置不足，请重新框选题目区域或重试")
    next_point = _parse_norm_point(data.get("next"))
    return dict(sorted(points.items())), next_point


def _letter_from_value(value) -> str | None:
    if isinstance(value, str):
        match = re.search(r"[A-Fa-f]", value)
        if match:
            return match.group(0).upper()
    return None


def ask_with_layout(image_png: bytes, config: AIConfig) -> AILayoutAnswer:
    """一次请求同时拿到答案字母、各选项位置和下一题位置。

    如果模型没有按 JSON 返回，则回退为普通文本答案解析（此时位置为空，
    引擎会使用用户配置的固定坐标）。
    """
    raw_text, elapsed_ms = _post_chat(image_png, config, ANSWER_LAYOUT_PROMPT, max_tokens=1600)
    try:
        data = _extract_json_object(raw_text)
    except AIError:
        # 回退：只解析答案字母（支持多选文本）
        letters = parse_answers(raw_text)
        return AILayoutAnswer(
            letter=letters[0], raw=raw_text, elapsed_ms=elapsed_ms, letters=letters
        )
    letters = _answer_letters_from(data.get("answers"))
    if not letters:
        letters = _answer_letters_from(data.get("answer"))
    if not letters:
        try:
            letters = parse_answers(raw_text)
        except AIError as exc:
            raise AIError(
                "AI 返回的 JSON 中缺少答案字母 answers。"
                "这通常是填空题/简答题，却用了「开始答题/单次选择」（选择题模式）；"
                "请改用「单次填空」，或框选含 A/B/C/D 选项的选择题区域。"
            ) from exc
    try:
        points, next_point = parse_option_layout(data, min_points=0)
    except AIError:
        points, next_point = {}, None
    return AILayoutAnswer(
        letter=letters[0],
        raw=raw_text,
        elapsed_ms=elapsed_ms,
        letters=letters,
        norm_points=points,
        norm_next=next_point,
    )


def _parse_options_items(data: dict) -> dict[str, tuple[float, float]]:
    raw_options = data.get("options")
    points: dict[str, tuple[float, float]] = {}
    if isinstance(raw_options, dict):
        items = [
            {"letter": letter, **(point if isinstance(point, dict) else {})}
            for letter, point in raw_options.items()
        ]
    elif isinstance(raw_options, list):
        items = raw_options
    else:
        return {}
    for item in items:
        if not isinstance(item, dict):
            continue
        letter = str(item.get("letter", "")).strip().upper()
        if letter not in "ABCDEF":
            continue
        point = _parse_norm_point(item)
        if point is not None:
            points[letter] = point
    return dict(sorted(points.items()))


def _parse_selected_letters(item: dict) -> list[str]:
    """解析 AI 返回的已选中选项字母（selected / selected_letters / 选项内 selected）。"""
    raw = item.get("selected")
    if raw is None:
        raw = item.get("selected_letters", item.get("chosen"))
    letters: list[str] = []
    if isinstance(raw, str):
        letters = [char for char in raw.upper() if char in "ABCDEF"]
    elif isinstance(raw, (list, tuple)):
        for entry in raw:
            if isinstance(entry, dict):
                letter = str(entry.get("letter", "")).upper().strip()
                if letter in "ABCDEF" and entry.get("selected", True):
                    letters.append(letter)
            else:
                text = str(entry).upper()
                letters.extend(char for char in text if char in "ABCDEF")
    options = item.get("options")
    if isinstance(options, list):
        for option in options:
            if not isinstance(option, dict):
                continue
            letter = str(option.get("letter", "")).upper().strip()
            if letter in "ABCDEF" and bool(option.get("selected")):
                letters.append(letter)
    return list(dict.fromkeys(letters))


def ask_page(image_png: bytes, config: AIConfig) -> PageAnswer:
    """整页多题：一次请求返回页面中所有未作答题目的答案与选项位置。"""
    raw_text, elapsed_ms = _post_chat(image_png, config, PAGE_PROMPT, max_tokens=2500)
    data = _extract_json_object(raw_text)
    raw_questions = data.get("questions")
    if not isinstance(raw_questions, list):
        raise AIError("AI 返回 JSON 中缺少 questions 数组")
    questions: list[PageQuestion] = []
    for position, item in enumerate(raw_questions[:10]):
        if not isinstance(item, dict):
            continue
        letters = _answer_letters_from(item.get("answers"))
        if not letters:
            letters = _answer_letters_from(item.get("answer"))
        selected = _parse_selected_letters(item)
        answered = bool(item.get("answered") or item.get("done"))
        if not letters:
            continue
        points = _parse_options_items(item)
        if not points and not selected:
            continue
        try:
            index = int(item.get("index", position + 1))
        except (TypeError, ValueError):
            index = position + 1
        key = str(
            item.get("key") or item.get("stem") or item.get("text") or ""
        ).strip()[:80]
        questions.append(
            PageQuestion(
                index=index,
                letter=letters[0] if letters else "",
                norm_points=points,
                letters=letters,
                key=key,
                answered=answered,
                selected=selected,
            )
        )
    next_point = _parse_norm_point(data.get("next"))
    return PageAnswer(
        questions=questions,
        norm_next=next_point,
        raw=raw_text,
        elapsed_ms=elapsed_ms,
    )


#: 只读取答案、不自动作答的提示词
ANSWER_TEXT_PROMPT = (
    "请识别图片中所有可见题目，并直接给出答案。要求：\n"
    "1. 严格按下面格式逐题输出：第一行写题号和题目，第二行以“答案：”开头写答案；\n"
    "2. 选择题写正确选项字母（多选题写全部字母，如 A、C）；\n"
    "3. 填空题按第1空、第2空…分别写出答案，题目里的 ___/____ 也按从左到右计数；\n"
    "4. 不要推测图片外的内容，不要输出点击坐标，不要自动作答；\n"
    "5. 用简洁中文，只输出题目和“答案：”行，不要额外解释。"
)


@dataclass
class TextAnswer:
    raw: str
    elapsed_ms: float = 0.0


def ask_answers_text(image_png: bytes, config: AIConfig) -> TextAnswer:
    """只让 AI 读题并给答案，不返回点击坐标，也不自动作答。"""
    raw_text, elapsed_ms = _post_chat(image_png, config, ANSWER_TEXT_PROMPT, max_tokens=2500)
    return TextAnswer(raw=raw_text, elapsed_ms=elapsed_ms)


def ask_fill(image_png: bytes, config: AIConfig, overwrite: bool = False) -> FillAnswer:
    """填空题：一次请求返回页面内所有未作答填空题的题号、第几空、答案和输入框位置。

    overwrite=True 时，已有内容的空也会重新作答（用于覆盖之前填错的答案）。
    """
    prompt = FILL_OVERWRITE_PROMPT if overwrite else FILL_PROMPT
    raw_text, elapsed_ms = _post_chat(image_png, config, prompt, max_tokens=2000)
    data = _extract_json_object(raw_text)
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise AIError("AI 返回 JSON 中缺少 items 数组")
    items: list[FillItem] = []
    for position, item in enumerate(raw_items[:20]):
        if not isinstance(item, dict):
            continue
        answer = str(item.get("answer", "")).strip()
        if not answer:
            continue
        try:
            index = int(item.get("index", position + 1))
        except (TypeError, ValueError):
            index = position + 1
        try:
            slot = int(item.get("slot", item.get("blank", 0)))
        except (TypeError, ValueError):
            slot = 0
        norm_bbox = _parse_norm_bbox(item.get("bbox") or item.get("box"))
        norm_point = _parse_norm_point(item)
        if norm_point is None and norm_bbox is not None:
            norm_point = ((norm_bbox[0] + norm_bbox[2]) / 2, (norm_bbox[1] + norm_bbox[3]) / 2)
        items.append(
            FillItem(
                index=index,
                answer=answer,
                norm_point=norm_point,
                slot=slot,
                norm_bbox=norm_bbox,
            )
        )
    next_point = _parse_norm_point(data.get("next"))
    return FillAnswer(
        items=items,
        norm_next=next_point,
        raw=raw_text,
        elapsed_ms=elapsed_ms,
    )


def detect_option_layout(image_png: bytes, config: AIConfig) -> OptionLayout:
    """让视觉模型自动定位选项 A~F 与「下一题」按钮的相对位置。"""
    raw_text, elapsed_ms = _post_chat(image_png, config, OPTION_PROMPT, max_tokens=1200)
    data = _extract_json_object(raw_text)
    points, next_point = parse_option_layout(data)
    return OptionLayout(points=points, next_point=next_point, raw=raw_text, elapsed_ms=elapsed_ms)
