# -*- coding: utf-8 -*-
"""
实验报告批阅多智能体系统 (Experiment Report Review Multi-Agent System)
=====================================================================

一个完全本地运行的 Streamlit 网页应用，用四个大模型智能体按顺序协作，
对学生的实验报告进行「可解释」的批阅打分。

智能体流水线（Pipeline）
------------------------
    1. 护栏监督员 (Guard Supervisor)   —— 学术风险检查；发现风险直接熔断
    2. 格式审查员 (Format Reviewer)     —— 0-10 分（与内容评审员并行执行）
    3. 内容评审员 (Content Reviewer)    —— 0-70 分（与格式审查员并行执行）
    4. 评分与解释官 (Explainer Judge)   —— 0-100 分，逐条给出扣分依据 + 改进建议

模型调用（双引擎，自动降级）
----------------------------
    接口与 OpenAI 完全兼容：
        base_url = https://api.deepseek.com
        model    = deepseek-chat

    引擎 A（首选）：openai 官方 SDK —— 需要 `pip install openai`
    引擎 B（兜底）：httpx / Python 标准库 —— **零额外安装**，装不上 openai 时自动启用

    两个引擎对外暴露完全一致的 call_agent() 接口，上层智能体代码无需关心用的是哪个。
    API Key 默认从环境变量 DEEPSEEK_API_KEY 读取（也支持侧边栏临时填写）。

本地运行（最省事的路径：只有 Streamlit 是必需依赖）
--------------------------------------------------
    streamlit run app.py

    set DEEPSEEK_API_KEY=sk-xxxxxxxx        # Windows CMD
    $env:DEEPSEEK_API_KEY="sk-xxxxxxxx"     # Windows PowerShell
    export DEEPSEEK_API_KEY=sk-xxxxxxxx     # macOS / Linux

    若想用 openai 官方 SDK（可选，非必需）：
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

作者：本科毕业设计示例项目
Python：3.9+（已在 3.12 实测）
"""

from __future__ import annotations

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import streamlit as st

from samples import SAMPLES, count_by_kind, get_sample, kind_label

# ---------------------------------------------------------------------------
# 全局常量：分制、模型、默认参数
# ---------------------------------------------------------------------------

FORMAT_MAX = 10          # 格式分满分
CONTENT_MAX = 70         # 内容分满分
REVIEW_MAX = FORMAT_MAX + CONTENT_MAX   # 两位审查员的分数小计（80）
TOTAL_MAX = 100          # 最终总分满分：审查员小计 80 分 + 其余 20 分（加分项与报告完整度）

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TEMPERATURE = 0.2   # 批阅任务需要稳定、可复现，温度调低

# 内容评审员的五个子维度（合计 70 分）—— 用于提示词与前端表格
CONTENT_DIMENSIONS: List[Tuple[str, int]] = [
    ("实验原理与目的", 10),
    ("操作步骤与规范性", 15),
    ("数据记录与处理", 20),
    ("结果分析与讨论", 15),
    ("结论与逻辑闭环", 10),
]


# ===========================================================================
# 第一部分：大模型调用层（所有智能体共用的底座）
# ===========================================================================

class LLMError(RuntimeError):
    """调用大模型过程中出现的可预期错误（网络、鉴权、限流、返回体异常等）。"""


def _sdk_stub() -> Any:
    """尝试导入 openai SDK 并做版本校验；不可用则返回 None。

    延迟导入的好处：即使没装 openai，Streamlit 页面也能正常打开，
    自动降级到「标准库/httpx」引擎，而不是直接抛 ImportError 白屏。
    """
    try:
        import openai  # noqa: WPS433 (局部导入是有意为之)
    except ImportError:
        return None

    version = getattr(openai, "__version__", "0.0.0")
    if str(version).split(".")[0] == "0":   # 0.x 是旧版接口，写法完全不同，视为不可用
        return None
    return openai


# ---------------------------------------------------------------------------
# 引擎 A：openai 官方 SDK（首选）
# ---------------------------------------------------------------------------

class SDKEngine:
    """用 openai>=1.30 的官方 SDK 访问 DeepSeek 的 OpenAI 兼容接口。"""

    name = "openai SDK"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float):
        openai = _sdk_stub()
        if openai is None:
            raise LLMError("openai SDK 不可用")
        self.model = model
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> Iterator[str]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            stream = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc

        for chunk in stream:
            if not getattr(chunk, "choices", None):
                continue
            delta = getattr(chunk.choices[0].delta, "content", None)
            if delta:
                yield delta


# ---------------------------------------------------------------------------
# 引擎 B：httpx / 标准库（零额外安装的兜底方案）
# ---------------------------------------------------------------------------

class HTTPXEngine:
    """不依赖 openai SDK，直接用 httpx 或标准库 urllib 调用 /chat/completions。

    适用场景：电脑上装不上 openai（网络/权限受限），但已有 httpx 或干脆什么都没有。
    """

    name = "httpx/标准库"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float):
        self.model = model
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        try:
            import httpx  # noqa: WPS433

            self._httpx = httpx
        except ImportError:
            self._httpx = None

    # ---- 内部工具 ----
    def _payload(self, system_prompt: str, user_prompt: str,
                 temperature: float, max_tokens: int, json_mode: bool, stream: bool) -> bytes:
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _extract_delta(piece: str) -> str:
        """从一行 SSE 数据里取出增量文本。"""
        if not piece or piece == "[DONE]":
            return ""
        try:
            obj = json.loads(piece)
        except json.JSONDecodeError:
            return ""
        if isinstance(obj.get("error"), dict):   # 接口返回了错误体
            raise LLMError(f"接口返回错误：{obj['error'].get('message', obj['error'])}")
        choices = obj.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if content:
            return content
        # 少数网关不支持 stream 时会把完整 message 塞回来
        message = choices[0].get("message") or {}
        return message.get("content") or ""

    def _iter_lines(self, res: Any) -> Iterator[str]:
        """跨 httpx / urllib 统一按行迭代。"""
        for raw_line in res.iter_lines():
            if isinstance(raw_line, bytes):
                raw_line = raw_line.decode("utf-8", "ignore")
            line = raw_line.strip()
            if line.startswith("data:"):
                yield line[5:].strip()

    # ---- 对外统一接口 ----
    def stream(self, system_prompt: str, user_prompt: str,
               temperature: float, max_tokens: int, json_mode: bool) -> Iterator[str]:
        url = f"{self._base}/chat/completions"

        if self._httpx is not None:
            body = self._payload(system_prompt, user_prompt, temperature, max_tokens, json_mode, True)
            try:
                with self._httpx.stream("POST", url, headers=self._headers, content=body,
                                        timeout=self._timeout) as res:
                    if res.status_code >= 400:
                        res.read()
                        raise LLMError(f"HTTP {res.status_code}: {res.text[:300]}")
                    for piece in self._iter_lines(res):
                        text = self._extract_delta(piece)
                        if text:
                            yield text
            except LLMError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise LLMError(f"{type(exc).__name__}: {exc}") from exc
            return

        # ---------- 纯标准库兜底（连 httpx 都没有时） ----------
        import urllib.error
        import urllib.request

        body = self._payload(system_prompt, user_prompt, temperature, max_tokens, json_mode, True)
        request = urllib.request.Request(url, data=body, headers=self._headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as res:
                for raw_line in res:
                    line = raw_line.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    text = self._extract_delta(line[5:].strip())
                    if text:
                        yield text
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:300]
            raise LLMError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc


def make_engine(api_key: str, base_url: str, model: str, timeout: float,
                prefer_sdk: bool = True) -> Tuple[Any, Optional[str]]:
    """创建模型调用引擎，返回 (engine, 降级说明)。

    优先使用 openai 官方 SDK；导入失败或初始化失败时自动降级到 httpx/标准库引擎。
    """
    notice: Optional[str] = None
    if prefer_sdk and _sdk_stub() is not None:
        try:
            return SDKEngine(api_key, base_url, model, timeout), None
        except Exception as exc:  # noqa: BLE001
            notice = f"openai SDK 初始化失败（{type(exc).__name__}: {exc}），已自动降级到 httpx/标准库引擎。"

    if not prefer_sdk and _sdk_stub() is None:
        notice = "未检测到 openai SDK，已自动使用 httpx/标准库引擎（无需安装 openai）。"
    return HTTPXEngine(api_key, base_url, model, timeout), notice


def call_agent(
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    *,
    engine: Any,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = 4096,
    use_json_mode: bool = True,
    on_delta: Optional[Callable[[str], None]] = None,
) -> str:
    """调用 DeepSeek 完成一次「智能体」推理，返回纯文本结果。

    参数说明
    --------
    agent_name    : 智能体名称，仅用于日志/报错提示。
    system_prompt : 该智能体的角色设定（见下方各 ROLE 常量）。
    user_prompt   : 本次任务的具体输入（评分标准 + 学生报告 + 上游结论）。
    engine        : 由 make_engine() 创建的调用引擎（SDK 或 httpx/标准库）。
    on_delta      : 流式回调，每收到一小段增量文本就调用一次，用于前端实时显示。
    use_json_mode : 是否请求 response_format={"type": "json_object"}。若网关不支持，
                    会自动降级为普通模式重试一次。
    """
    if engine is None:
        raise LLMError("模型调用引擎未初始化，请检查 Base URL 与 API Key 配置。")

    def stream_once(json_mode: bool) -> str:
        chunks: List[str] = []
        try:
            for delta in engine.stream(system_prompt, user_prompt, temperature, max_tokens, json_mode):
                chunks.append(delta)
                if on_delta is not None:
                    on_delta(delta)
        except LLMError as exc:
            raise LLMError(f"[{agent_name}] 调用 DeepSeek 失败：{exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"[{agent_name}] 调用 DeepSeek 失败：{type(exc).__name__}: {exc}") from exc
        return "".join(chunks)

    try:
        text = stream_once(use_json_mode)
    except LLMError as exc:
        # 某些 OpenAI 兼容网关不支持 json_object，这里降级重试一次
        if use_json_mode and ("response_format" in str(exc) or "json_object" in str(exc)):
            text = stream_once(False)
        else:
            raise

    if not text.strip():
        raise LLMError(f"[{agent_name}] 模型返回了空内容，请稍后重试或降低温度/更换模型。")
    return text


# ---------------------------------------------------------------------------
# JSON 解析工具：大模型经常给 JSON 包一层 ```json 代码块，这里做健壮提取
# ---------------------------------------------------------------------------

def extract_json(text: str) -> Optional[dict]:
    """从模型输出中尽力提取一个 JSON 对象，失败返回 None（由调用方兜底）。"""
    if not text:
        return None

    cleaned = text.strip()

    # 1) 去掉 ```json ... ``` 围栏
    fence = re.search(r"```(?:json|JSON)?\s*(.+?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()

    # 2) 直接尝试解析
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # 3) 退化为「截取第一个 { 到最后一个 }」再解析
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(cleaned[start:end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


def clamp_int(value: Any, low: int, high: int) -> int:
    """把模型给的分数稳妥地约束到 [low, high] 区间内的整数。"""
    try:
        num = int(round(float(value)))
    except (TypeError, ValueError):
        num = low
    return max(low, min(high, num))


def as_list(value: Any) -> List[Any]:
    """把模型给的任何东西规整成 list（模型有时会返回 None / 字符串）。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def parse_agent_json(raw: str, agent_name: str) -> dict:
    """解析智能体返回的 JSON；解析不出来时返回带 `_raw` 的兜底结构。"""
    data = extract_json(raw)
    if data is None:
        return {"_parse_failed": True, "_raw": raw.strip()}
    data.setdefault("_raw", raw.strip())
    return data


# ===========================================================================
# 第二部分：四个智能体的 System Prompt（角色设定的核心，按职责精心设计）
# ===========================================================================

GUARD_SYSTEM_PROMPT = """你是一名严谨的【学术诚信护栏监督员】，负责在批阅开始前对一份学生实验报告做风险筛查。

# 你的职责
你要识别以下四类学术不端风险：
1. 抄袭 (plagiarism)：大段文字与公开教材、论文、网络资料高度雷同；不同章节文风割裂、明显拼接；
   出现"本文""综上所述"等论文腔调、与实验报告体裁不符；引用他人成果却不标注。
2. 数据篡改 (data_fabrication)：数据异常"完美"（如所有重复实验值完全一致、R² 恰好等于 1.000）；
   前后数据互相矛盾；图表数据与正文对不上；有效数字/单位混乱到像是编造；只有结论没有原始记录。
3. 代写 (ghostwriting)：明显超出本科生水平；出现"作为一个AI模型""根据您的要求"等生成痕迹；
   出现与作者身份不符的经历/设备描述；大段模板化套话且与实验内容无关。
4. 不当言论 (inappropriate_content)：政治敏感、歧视、辱骂、色情暴力、违法违规操作（如危险化学品的违规使用）等。

# 判定尺度（非常重要）
- 你只负责"风险"，不负责打分。有明确证据才判定为风险，**不要因为写作水平差就指控抄袭**。
- 轻微问题（如个别引用不规范、个别口语化表达）不算风险，交给后面的审查员处理。
- 证据必须可定位：指出章节/段落/原句或数据，禁止空泛指控。
- 宁可放过轻微疑点，也不可制造冤案；但出现证据充分的学术不端必须提出。

# 输出格式（只输出 JSON，不要任何解释性文字、不要 Markdown 代码块）
{
  "risk_level": "none" | "low" | "medium" | "high",
  "summary": "一句话说明整体判断",
  "confidence": 0-100 的整数，表示你对自己判断的把握程度,
  "violations": [
    {
      "type": "抄袭" | "数据篡改" | "代写" | "不当言论",
      "severity": "low" | "medium" | "high",
      "evidence": "报告中的具体位置与原句/数据（必须引用原文片段）",
      "reason": "为什么这构成风险",
      "suggestion": "学生应如何整改"
    }
  ],
  "evidence_requests": ["需要人工复核时建议补充核查的材料，如原始实验记录本、代码提交记录等"]
}

# 判定规则
- 完全没发现问题：risk_level = "none"，violations = []，并且必须在 summary 中说明"未发现学术不端风险"。
- 有确定证据的学术不端：risk_level = "medium" 或 "high"，violations 逐条列出。
- 只有模糊疑点：risk_level = "low"，在 violations 中说明疑点并给出 evidence_requests。
- 只输出 JSON，禁止输出 JSON 以外的任何字符。"""


FORMAT_SYSTEM_PROMPT = """你是一名严格的【实验报告格式审查员】，只审查"形式与规范"，不评判实验内容是否正确。

# 审查维度（每条按重要性扣分，总分 10 分，按下表权重分配）
1. 结构完整性 (满分 2.0)：是否具备 实验名称 / 实验目的 / 实验原理 / 仪器与试剂 / 实验步骤 /
   数据记录 / 数据处理 / 结果与讨论 / 实验结论 等必要章节，是否缺项。
2. 排版与可读性 (满分 1.5)：标题层级、段落划分、编号是否统一；是否有大段无标点文字；是否错别字频出。
3. 字数与工作量 (满分 2.0)：正文是否达到指导书要求（若指导书未指定，按本科实验报告常规 800-1500 字衡量）；
   是否存在明显敷衍（整篇不足 400 字）。
4. 图表规范 (满分 2.0)：图表是否有编号（图1、表1）与标题（表题在上、图题在下）；是否在正文中被引用
   （"如图1所示"）；表头与单位是否完整；是否只有图没有说明。
5. 引用与参考文献 (满分 1.5)：引用他人观点/公式/数据是否标注来源；参考文献列表格式是否规范统一。
6. 语言与学术表达 (满分 1.0)：是否存在口语化、网络用语、第一人称口语叙述；时态与术语是否统一。

# 硬性要求
- **每一项扣分都必须定位到报告中的具体位置**（章节名 + 原文片段），否则不得扣分。
- 只指出格式问题，不要评价实验原理是否正确、数据是否合理（那是内容评审员的工作）。
- 分数分布建议：优秀 9-10；良好 7-8；及格 5-6；较差 3-4；极差 0-2。

# 输出格式（只输出 JSON，不要任何解释性文字、不要 Markdown 代码块）
{
  "format_score": 0-10 的数字（可带 0.5）,
  "dimension_scores": [
    {"name": "结构完整性", "score": 数字, "max": 2.0, "comment": "一句话评价"}
  ],
  "issues": [
    {
      "severity": "严重" | "中等" | "轻微",
      "location": "具体位置，如『第3节 数据处理』或『图2下方』",
      "quote": "报告中的原文片段（10-40字，必须真实摘录）",
      "problem": "存在什么格式问题",
      "deduction": 数字（该问题扣了多少分）,
      "suggestion": "可直接照做的修改建议"
    }
  ],
  "highlights": ["做得好的地方，1-3 条，没有就留空数组"],
  "summary": "50-120 字的格式审查总结"
}

# 自检规则
- 所有 issues 的 deduction 之和 + format_score 必须约等于 10（允许 ±0.5 的取整误差）。
- 若报告格式完全合规，issues 可以为空，format_score 给 9-10 分，并在 summary 中说明。
- dimension_scores 中每个维度的 score 不得超过其 max。
- 只输出 JSON，禁止输出 JSON 以外的任何字符。"""


CONTENT_SYSTEM_PROMPT = """你是一名资深的【实验报告内容评审员】，负责审查报告的科学性、规范性与逻辑性。总分 70 分。

# 评分维度与权重（必须严格按此分配，五项之和不得超过 70）
1. 实验原理与目的 (满分 10)：原理表述是否正确、公式是否准确且符号有说明、实验目的与原理是否对应。
2. 操作步骤与规范性 (满分 15)：步骤是否完整可复现、是否遗漏关键控制条件（温度/浓度/时间/空白对照）、
   操作顺序是否正确、是否违反实验安全与规范。
3. 数据记录与处理 (满分 20)：是否有原始数据表、有效数字与单位是否正确、误差/不确定度是否计算、
   公式代入与计算结果是否正确（**要动手核算**，如平均值、相对误差、线性拟合结果）、异常数据是否说明取舍依据。
4. 结果分析与讨论 (满分 15)：是否结合数据与原理分析（而不是复述数据）、是否讨论误差来源、
   是否与理论值/文献值对比、图表趋势是否被正确解读、是否回应了实验目的。
5. 结论与逻辑闭环 (满分 10)：结论是否由数据支撑、是否与目的呼应、有无过度推断或想当然、
   是否给出改进方向。

# 硬性要求
- **每个扣分点必须给出三要素：具体位置 + 原文/数据摘录 + 错误原因**，无法定位的问题不得扣分。
- 发现计算错误时，必须写出"报告写的是 X，正确应为 Y，差异原因 Z"。
- 区分【科学性错误】（会显著扣分）与【表达不足】（轻扣分），不要混为一谈。
- 不要评价字体、编号、参考文献格式（那是格式审查员的工作）。
- 分数分布建议：优秀 60-70；良好 50-59；及格 40-49；较差 25-39；极差 0-24。

# 输出格式（只输出 JSON，不要任何解释性文字、不要 Markdown 代码块）
{
  "content_score": 0-70 的数字,
  "dimension_scores": [
    {"name": "实验原理与目的", "score": 数字, "max": 10, "comment": "一句话评价"}
  ],
  "issues": [
    {
      "severity": "严重" | "中等" | "轻微",
      "location": "具体位置，如『4.2 数据记录表』或『第5段』",
      "quote": "报告中的原文或原始数据（必须真实摘录）",
      "problem": "错在哪里，为什么错",
      "correction": "正确的内容/计算过程/应补充的信息",
      "deduction": 数字（该问题扣了多少分）,
      "suggestion": "改进建议"
    }
  ],
  "highlights": ["实验做得好的地方，1-3 条"],
  "summary": "80-150 字的内容评审总结，说明主要失分原因"
}

# 自检规则
- content_score + 所有 issues 的 deduction 之和 必须约等于 70（允许 ±2 的取整误差）。
- dimension_scores 五项之和必须约等于 content_score（允许 ±2）。
- dimension_scores 中每个维度的 score 不得超过其 max。
- 只输出 JSON，禁止输出 JSON 以外的任何字符。"""


JUDGE_SYSTEM_PROMPT = """你是一名【评分与解释官】，是批阅流水线的最后一环。你的职责不是重新审阅报告，
而是**汇总上游两位审查员的结论**，产出总分与一份"每一分都能追溯到原文"的可解释评分。

# 评分规则（必须严格遵守）
1. 总分满分 100 分，其中：
   - 格式规范 10 分（采用格式审查员的结论）
   - 内容质量 70 分（采用内容评审员的结论）
   - 其余 20 分由你依据评分标准分配，主要用于：加分项（如实验设计创新、数据处理深入、误差分析充分、
     图表规范美观、有拓展思考等）以及报告完整度。若报告平庸无亮点，这 20 分可以低至 0-5 分；
     若报告优秀且有明显亮点，可给到 15-20 分。**必须给出加分依据，不允许无理由给分。**
2. **禁止凭空打分**：格式分与内容分必须以上游审查员的结论为主要依据。
   若你认为上游分数明显不合理，可在 ±3 分内做微调，但必须在 adjustment_note 中写明调整理由。
3. 你的 deduction_items 必须**逐条覆盖**上游 issues 中所有"中等"及以上严重程度的问题，
   并且每一条都要写出：报告中的具体位置（location）、原文依据（quote）、扣分原因（reason）、
   扣分分值（points）、改进建议（suggestion）。
   同时，加分项也必须写进 deduction_items（category 填"加分"，points 填负数表示加分），
   以保证「10 分格式 + 70 分内容 + 20 分加分项 = 100 分」这笔账能完全对上。
4. 若上游数据自相矛盾（例如 issues 扣分之和与给出的分数对不上），以 issues 的扣分为准重新核算，
   并在 adjustment_note 中说明你如何校正。
5. 等级换算：90-100 优秀；80-89 良好；70-79 中等；60-69 及格；0-59 不及格。
6. 语气必须客观、对事不对人，指出问题同时给出可执行的改进路径。

# 输出格式（只输出 JSON，不要任何解释性文字、不要 Markdown 代码块）
{
  "total_score": 0-100 的数字,
  "grade": "优秀" | "良好" | "中等" | "及格" | "不及格",
  "one_line_summary": "一句话总评（30 字以内）",
  "dimension_table": [
    {"dimension": "格式规范", "score": 数字, "max": 10, "weight_reason": "为什么给这个分，引用上游结论"},
    {"dimension": "内容质量", "score": 数字, "max": 70, "weight_reason": "为什么给这个分，引用上游结论"},
    {"dimension": "加分项（创新/深入度/完整度）", "score": 数字, "max": 20, "weight_reason": "加分或不予加分的具体依据"}
  ],
  "deduction_items": [
    {
      "category": "格式" | "内容" | "加分",
      "location": "具体位置",
      "quote": "原文依据",
      "reason": "扣分（或加分）原因",
      "points": 数字（正数=扣分，负数=加分）,
      "suggestion": "改进建议"
    }
  ],
  "highlights": ["值得肯定的地方"],
  "improvement_plan": [
    {"priority": "高" | "中" | "低", "action": "具体可执行的修改动作", "expected_gain": "预计可提升多少分"}
  ],
  "adjustment_note": "若你对上游分数做了调整，说明理由；未调整则写『未调整，完全采用上游结论』",
  "final_comment": "150-250 字的评语：整体表现、主要失分点、下一步提升方向"
}

# 自检规则（提交前必须自检）
- total_score 必须等于 dimension_table 中三项 score 之和，且不得超过 100。
- dimension_table 每项的 score 不得超过其 max。
- 用这条账目自检：100 - 所有扣分项之和 + 所有加分项之和 ≈ total_score（允许 ±2 的取整误差）。
- 只输出 JSON，禁止输出 JSON 以外的任何字符。"""


# ===========================================================================
# 第三部分：各智能体的输入构造（把上游结论注入下游提示词）
# ===========================================================================

def _truncate(text: str, limit: int) -> str:
    """超长文本截断，避免超出上下文窗口；截断处给出显式提示。"""
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.3):]
    return f"{head}\n\n……【此处因长度限制省略约 {len(text) - limit} 字】……\n\n{tail}"


def _tags_block(tags: List[str]) -> str:
    return "、".join(tags) if tags else "（无特别要求）"


def build_guard_prompt(standard: str, report: str, focus_tags: List[str]) -> str:
    return f"""请对下面这份学生实验报告做学术风险筛查。

【重点关注项】{_tags_block(focus_tags)}

【实验指导书 / 评分标准】
<<<STANDARD
{_truncate(standard, 6000)}
STANDARD

【学生实验报告】
<<<REPORT
{_truncate(report, 16000)}
REPORT

请严格按系统提示中的 JSON 格式输出风险筛查结果。"""


def build_format_prompt(standard: str, report: str) -> str:
    return f"""请对下面这份学生实验报告做格式审查，输出 0-10 分的格式得分与问题清单。

【实验指导书 / 评分标准（其中若有明确的格式与字数要求，请以此为准）】
<<<STANDARD
{_truncate(standard, 6000)}
STANDARD

【学生实验报告】
<<<REPORT
{_truncate(report, 16000)}
REPORT

要求：每个问题都必须给出 location（具体位置）与 quote（真实原文摘录），并按系统提示的 JSON 格式输出。"""


def build_content_prompt(standard: str, report: str) -> str:
    dimensions = "；".join(f"{name} {score}分" for name, score in CONTENT_DIMENSIONS)
    return f"""请对下面这份学生实验报告做内容评审，输出 0-70 分的内容得分与问题清单。

【评分维度与满分】{dimensions}

【实验指导书 / 评分标准（判断对错的首要依据，报告的实验目的与要求以此为准）】
<<<STANDARD
{_truncate(standard, 6000)}
STANDARD

【学生实验报告】
<<<REPORT
{_truncate(report, 16000)}
REPORT

要求：
1. 亲自核算报告中的关键计算（平均值、相对误差、拟合结果等），指出算错的地方及正确结果。
2. 每个问题都必须给出 location（具体位置）与 quote（真实原文摘录），并说明扣分原因与正确做法。
3. 严格按系统提示的 JSON 格式输出。"""


def build_judge_prompt(
    standard: str,
    report: str,
    guard: dict,
    fmt: dict,
    content: dict,
    fmt_error: Optional[str] = None,
    content_error: Optional[str] = None,
) -> str:
    """把上游三个智能体的结论打包给评分与解释官。"""

    def _dump(data: dict, error: Optional[str]) -> str:
        if error:
            return f"（该审查员执行失败：{error}，请依据报告本身谨慎评分并在 adjustment_note 中说明）"
        return json.dumps(data, ensure_ascii=False, indent=2)

    return f"""请汇总上游审查结论，给出总分（满分 {TOTAL_MAX} = 格式 {FORMAT_MAX} + 内容 {CONTENT_MAX} + 加分项 20）与可解释的评分报告。

========== 上游结论 1：护栏监督员 ==========
{json.dumps(guard, ensure_ascii=False, indent=2)}

========== 上游结论 2：格式审查员（满分 10） ==========
{_dump(fmt, fmt_error)}

========== 上游结论 3：内容评审员（满分 70） ==========
{_dump(content, content_error)}

========== 原始材料（供你核对 location 与 quote 是否真实存在） ==========
【实验指导书 / 评分标准】
<<<STANDARD
{_truncate(standard, 4000)}
STANDARD

【学生实验报告】
<<<REPORT
{_truncate(report, 12000)}
REPORT

请严格执行系统提示中的评分规则与 JSON 输出格式。"""


# ===========================================================================
# 第四部分：四个智能体的执行封装（每个智能体 = 一个函数）
# ===========================================================================

def run_guard_supervisor(
    standard: str, report: str, llm_kwargs: Dict[str, Any], focus_tags: List[str]
) -> Tuple[dict, str]:
    """智能体 1：护栏监督员。返回 (结构化结果, 原始文本)。"""
    raw = call_agent(
        "护栏监督员",
        GUARD_SYSTEM_PROMPT,
        build_guard_prompt(standard, report, focus_tags),
        **llm_kwargs,
    )
    data = parse_agent_json(raw, "护栏监督员")
    if data.get("_parse_failed"):
        # 解析失败时采取保守策略：标记为待人工复核，而不是直接放行
        data = {
            "risk_level": "low",
            "summary": "护栏监督员返回的内容不是标准 JSON，已保守地标记为需人工复核。",
            "confidence": 0,
            "violations": [],
            "evidence_requests": ["请人工查看下方护栏原始输出"],
            "_parse_failed": True,
            "_raw": raw,
        }
    data["risk_level"] = str(data.get("risk_level", "low")).lower().strip()
    if data["risk_level"] not in {"none", "low", "medium", "high"}:
        data["risk_level"] = "low"
    data["violations"] = [v for v in as_list(data.get("violations")) if isinstance(v, dict)]
    data["evidence_requests"] = [str(x) for x in as_list(data.get("evidence_requests"))]
    data["confidence"] = clamp_int(data.get("confidence", 0), 0, 100)
    return data, raw


def run_format_reviewer(standard: str, report: str, llm_kwargs: Dict[str, Any]) -> Tuple[dict, str]:
    """智能体 2：格式审查员（0-10 分）。"""
    raw = call_agent(
        "格式审查员", FORMAT_SYSTEM_PROMPT, build_format_prompt(standard, report), **llm_kwargs
    )
    data = parse_agent_json(raw, "格式审查员")
    if data.get("_parse_failed"):
        raise LLMError("格式审查员返回内容无法解析为 JSON。原始输出：\n" + raw[:800])
    data["format_score"] = clamp_int(data.get("format_score", 0), 0, FORMAT_MAX)
    data["issues"] = [i for i in as_list(data.get("issues")) if isinstance(i, dict)]
    data["highlights"] = [str(x) for x in as_list(data.get("highlights"))]
    data["dimension_scores"] = [d for d in as_list(data.get("dimension_scores")) if isinstance(d, dict)]
    return data, raw


def run_content_reviewer(standard: str, report: str, llm_kwargs: Dict[str, Any]) -> Tuple[dict, str]:
    """智能体 3：内容评审员（0-70 分）。"""
    raw = call_agent(
        "内容评审员", CONTENT_SYSTEM_PROMPT, build_content_prompt(standard, report), **llm_kwargs
    )
    data = parse_agent_json(raw, "内容评审员")
    if data.get("_parse_failed"):
        raise LLMError("内容评审员返回内容无法解析为 JSON。原始输出：\n" + raw[:800])
    data["content_score"] = clamp_int(data.get("content_score", 0), 0, CONTENT_MAX)
    data["issues"] = [i for i in as_list(data.get("issues")) if isinstance(i, dict)]
    data["highlights"] = [str(x) for x in as_list(data.get("highlights"))]
    data["dimension_scores"] = [d for d in as_list(data.get("dimension_scores")) if isinstance(d, dict)]
    return data, raw


def run_explainer_judge(
    standard: str,
    report: str,
    guard: dict,
    fmt: Optional[dict],
    content: Optional[dict],
    llm_kwargs: Dict[str, Any],
    fmt_error: Optional[str] = None,
    content_error: Optional[str] = None,
) -> Tuple[dict, str]:
    """智能体 4：评分与解释官（0-100 分）。"""
    prompt = build_judge_prompt(
        standard, report, guard, fmt or {}, content or {}, fmt_error, content_error
    )
    raw = call_agent("评分与解释官", JUDGE_SYSTEM_PROMPT, prompt, **llm_kwargs)
    data = parse_agent_json(raw, "评分与解释官")
    if data.get("_parse_failed"):
        raise LLMError("评分与解释官返回内容无法解析为 JSON。原始输出：\n" + raw[:800])

    # 兜底：总分若缺失，则用格式分 + 内容分补齐
    fallback_total = (fmt or {}).get("format_score", 0) + (content or {}).get("content_score", 0)
    data["total_score"] = clamp_int(data.get("total_score", fallback_total), 0, TOTAL_MAX)
    data["grade"] = str(data.get("grade") or _grade_of(data["total_score"]))
    data["dimension_table"] = [d for d in as_list(data.get("dimension_table")) if isinstance(d, dict)]
    data["deduction_items"] = [d for d in as_list(data.get("deduction_items")) if isinstance(d, dict)]
    data["highlights"] = [str(x) for x in as_list(data.get("highlights"))]
    data["improvement_plan"] = [p for p in as_list(data.get("improvement_plan")) if isinstance(p, dict)]
    return data, raw


def _grade_of(score: int) -> str:
    """分数 -> 等级。"""
    if score >= 90:
        return "优秀"
    if score >= 80:
        return "良好"
    if score >= 70:
        return "中等"
    if score >= 60:
        return "及格"
    return "不及格"


# ===========================================================================
# 第五部分：主流程编排（护栏 -> [格式 ‖ 内容] -> 评分解释官）
# ===========================================================================

def review_report(
    standard: str,
    report: str,
    engine: Any,
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    use_json_mode: bool = True,
    focus_tags: List[str],
    allow_parallel: bool = True,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """执行一次完整批阅，返回一个包含所有中间结果的字典。

    参数
    ----
    engine : 由 make_engine() 创建的模型调用引擎（SDK 或 httpx/标准库）。

    返回结构::

        {
          "guard": {...}, "guard_raw": "...",
          "format": {...} | None, "format_raw": "...", "format_error": str | None,
          "content": {...} | None, "content_raw": "...", "content_error": str | None,
          "judge": {...} | None, "judge_raw": "...",
          "interrupted": bool, "status": "completed" | "blocked" | "error",
          "error": str | None,
          "llm_calls": int,
        }
    """
    log = progress or (lambda _msg: None)
    result: Dict[str, Any] = {
        "guard": None, "guard_raw": "",
        "format": None, "format_raw": "", "format_error": None,
        "content": None, "content_raw": "", "content_error": None,
        "judge": None, "judge_raw": "",
        "interrupted": False, "status": "completed", "error": None,
        "llm_calls": 0,
    }

    # 传给各智能体的统一调用参数
    llm_kwargs: Dict[str, Any] = {
        "engine": engine,
        "temperature": float(temperature),
        "use_json_mode": bool(use_json_mode),
    }

    # ---------- 第 1 步：护栏监督员（串行，必须最先执行） ----------
    log("🛡️ 护栏监督员：正在筛查抄袭 / 数据篡改 / 代写 / 不当言论……")
    guard, guard_raw = run_guard_supervisor(standard, report, llm_kwargs, focus_tags)
    result["guard"], result["guard_raw"] = guard, guard_raw
    result["llm_calls"] += 1

    if guard.get("risk_level") in {"medium", "high"}:
        log("🚨 护栏监督员发现高化学术风险，批阅已中断，等待人工复核。")
        result["interrupted"] = True
        result["status"] = "blocked"
        return result

    log(f"✅ 护栏通过（风险等级：{guard.get('risk_level')}），进入双审查员并行审查。")

    # ---------- 第 2 / 3 步：格式审查员 与 内容评审员（并行） ----------
    if allow_parallel:
        log("⚙️ 格式审查员 与 内容评审员 正在并行工作……")
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="reviewer") as pool:
            fmt_future = pool.submit(run_format_reviewer, standard, report, llm_kwargs)
            content_future = pool.submit(run_content_reviewer, standard, report, llm_kwargs)

            # 两个线程的异常各自捕获，避免一个失败拖垮另一个
            try:
                fmt_data, fmt_raw = fmt_future.result()
                result["format"], result["format_raw"] = fmt_data, fmt_raw
                result["llm_calls"] += 1
            except Exception as exc:  # noqa: BLE001
                result["format_error"] = f"{type(exc).__name__}: {exc}"
            try:
                content_data, content_raw = content_future.result()
                result["content"], result["content_raw"] = content_data, content_raw
                result["llm_calls"] += 1
            except Exception as exc:  # noqa: BLE001
                result["content_error"] = f"{type(exc).__name__}: {exc}"
    else:
        log("⚙️ 格式审查员正在工作……")
        try:
            fmt_data, fmt_raw = run_format_reviewer(standard, report, llm_kwargs)
            result["format"], result["format_raw"] = fmt_data, fmt_raw
            result["llm_calls"] += 1
        except Exception as exc:  # noqa: BLE001
            result["format_error"] = f"{type(exc).__name__}: {exc}"
        log("⚙️ 内容评审员正在工作……")
        try:
            content_data, content_raw = run_content_reviewer(standard, report, llm_kwargs)
            result["content"], result["content_raw"] = content_data, content_raw
            result["llm_calls"] += 1
        except Exception as exc:  # noqa: BLE001
            result["content_error"] = f"{type(exc).__name__}: {exc}"

    if result["format"] is None and result["content"] is None:
        result["status"] = "error"
        result["error"] = "格式审查员与内容评审员均执行失败，无法继续评分。"
        log("❌ 两位审查员均执行失败，流程终止。")
        return result

    # ---------- 第 4 步：评分与解释官（串行，依赖上游全部结论） ----------
    log("🧮 评分与解释官：正在汇总扣分依据并生成可解释评分……")
    try:
        judge, judge_raw = run_explainer_judge(
            standard, report,
            result["guard"], result["format"], result["content"], llm_kwargs,
            fmt_error=result["format_error"], content_error=result["content_error"],
        )
        result["judge"], result["judge_raw"] = judge, judge_raw
        result["llm_calls"] += 1
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = f"评分与解释官执行失败：{type(exc).__name__}: {exc}"

    log("🎉 批阅完成。")
    return result


# ===========================================================================
# 第六部分：Markdown 渲染（把结构化结果排成可读报告）
# ===========================================================================

def _fmt_score(value: Any) -> str:
    """分数显示：整数就不带小数点。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(num)) if abs(num - int(num)) < 1e-9 else f"{num:g}"


def render_guard_md(guard: dict, risk_title: str) -> str:
    lines = [f"### 风险等级：{risk_title}", ""]
    lines.append(f"- **判断信心**：{guard.get('confidence', 0)} / 100")
    lines.append(f"- **结论**：{guard.get('summary', '（无）')}")
    lines.append("")

    violations = guard.get("violations") or []
    if violations:
        lines.append("#### 风险明细")
        lines.append("")
        lines.append("| # | 类型 | 严重程度 | 证据（原文位置/片段） | 原因 | 整改建议 |")
        lines.append("|---|---|---|---|---|---|")
        for idx, item in enumerate(violations, 1):
            lines.append(
                "| {i} | {t} | {s} | {e} | {r} | {g} |".format(
                    i=idx,
                    t=str(item.get("type", "-")).replace("|", "/"),
                    s=str(item.get("severity", "-")).replace("|", "/"),
                    e=str(item.get("evidence", "-")).replace("|", "/").replace("\n", " "),
                    r=str(item.get("reason", "-")).replace("|", "/").replace("\n", " "),
                    g=str(item.get("suggestion", "-")).replace("|", "/").replace("\n", " "),
                )
            )
        lines.append("")
    else:
        lines.append("> 未检测到学术不端风险项。")
        lines.append("")

    requests = guard.get("evidence_requests") or []
    if requests:
        lines.append("#### 人工复核建议")
        lines.append("")
        for req in requests:
            lines.append(f"- {req}")
        lines.append("")
    return "\n".join(lines)


def render_issues_md(issues: List[dict], extra_fields: Tuple[str, ...] = ()) -> str:
    """把问题清单渲染成 Markdown 表格。extra_fields 用于内容审查员额外的'正确做法'列。"""
    if not issues:
        return "> 未发现该维度下的问题。"

    base_cols = ["#", "严重程度", "位置", "原文依据", "问题描述", "扣分", "改进建议"]
    cols = list(base_cols)
    for field, title in zip(extra_fields, ("正确做法",)):
        cols.insert(5, title)

    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for idx, item in enumerate(issues, 1):
        row = [
            str(idx),
            str(item.get("severity", "-")),
            str(item.get("location", "-")),
            str(item.get("quote", "-")),
            str(item.get("problem", "-")),
        ]
        for field in extra_fields:
            row.append(str(item.get(field, "-")))
        row += [_fmt_score(item.get("deduction", 0)), str(item.get("suggestion", "-"))]
        cleaned = [c.replace("|", "/").replace("\n", " ").strip() or "-" for c in row]
        lines.append("| " + " | ".join(cleaned) + " |")
    return "\n".join(lines)


def render_dimension_md(dimensions: List[dict], key_name: str = "name") -> str:
    if not dimensions:
        return "> （模型未给出维度明细）"
    lines = ["| 维度 | 得分 | 满分 | 评价 |", "|---|---|---|---|"]
    for dim in dimensions:
        lines.append(
            "| {n} | {s} | {m} | {c} |".format(
                n=str(dim.get(key_name) or dim.get("dimension") or "-").replace("|", "/"),
                s=_fmt_score(dim.get("score", 0)),
                m=_fmt_score(dim.get("max", "-")),
                c=str(dim.get("comment") or dim.get("weight_reason") or "-").replace("|", "/").replace("\n", " "),
            )
        )
    return "\n".join(lines)


def render_judge_md(judge: dict) -> str:
    lines = [
        f"# 综合评分：{_fmt_score(judge.get('total_score', 0))} / {TOTAL_MAX}　（{judge.get('grade', '-')}）",
        "",
        f"**一句话总评**：{judge.get('one_line_summary', '-')}",
        "",
        f"> 计分口径：格式 {FORMAT_MAX} 分 + 内容 {CONTENT_MAX} 分 + 加分项 20 分 = {TOTAL_MAX} 分。"
        f"两位审查员的小计为 {REVIEW_MAX} 分，其余分数由评分与解释官依据评分标准给出，并逐条说明依据。",
        "",
        "## 一、各维度得分",
        "",
        render_dimension_md(judge.get("dimension_table") or [], key_name="dimension"),
        "",
        "## 二、扣分依据（每一分都能追溯到原文）",
        "",
    ]

    items = judge.get("deduction_items") or []
    if items:
        lines += ["| # | 类别 | 位置 | 原文依据 | 扣分/加分原因 | 分值 | 改进建议 |", "|---|---|---|---|---|---|---|"]
        total_deduction = 0.0
        total_bonus = 0.0
        for idx, item in enumerate(items, 1):
            try:
                points = float(item.get("points", 0) or 0)
            except (TypeError, ValueError):
                points = 0.0
            if points < 0:
                total_bonus += -points
            else:
                total_deduction += points
            row = [
                str(idx),
                str(item.get("category", "-")),
                str(item.get("location", "-")),
                str(item.get("quote", "-")),
                str(item.get("reason", "-")),
                _fmt_score(item.get("points", 0)),
                str(item.get("suggestion", "-")),
            ]
            cleaned = [c.replace("|", "/").replace("\n", " ").strip() or "-" for c in row]
            lines.append("| " + " | ".join(cleaned) + " |")
        lines += [
            "",
            f"**账目核对**：{TOTAL_MAX} − 扣分 {_fmt_score(total_deduction)} + 加分 {_fmt_score(total_bonus)} "
            f"= {_fmt_score(TOTAL_MAX - total_deduction + total_bonus)} 分"
            f"（与综合评分 {_fmt_score(judge.get('total_score', 0))} 分的差额来自取整，属正常范围）",
            "",
        ]
    else:
        lines += ["> 未产生扣分项，报告质量优秀。", ""]

    highlights = judge.get("highlights") or []
    if highlights:
        lines += ["## 三、值得肯定之处", ""]
        lines += [f"- {h}" for h in highlights]
        lines += [""]

    plan = judge.get("improvement_plan") or []
    if plan:
        lines += ["## 四、改进计划", "", "| 优先级 | 具体动作 | 预计提分 |", "|---|---|---|"]
        for item in plan:
            lines.append(
                "| {p} | {a} | {g} |".format(
                    p=str(item.get("priority", "-")),
                    a=str(item.get("action", "-")).replace("|", "/").replace("\n", " "),
                    g=str(item.get("expected_gain", "-")).replace("|", "/"),
                )
            )
        lines += [""]

    lines += [
        "## 五、评分调整说明",
        "",
        f"{judge.get('adjustment_note', '未调整，完全采用上游结论。')}",
        "",
        "## 六、总评语",
        "",
        f"{judge.get('final_comment', '-')}",
        "",
    ]
    return "\n".join(lines)


def render_format_md(fmt: Optional[dict], error: Optional[str]) -> str:
    if error:
        return f"### ⚠️ 格式审查员执行失败\n\n```\n{error}\n```"
    if not fmt:
        return "> 无格式审查结果。"
    lines = [
        f"### 格式得分：{_fmt_score(fmt.get('format_score', 0))} / {FORMAT_MAX}",
        "",
        render_dimension_md(fmt.get("dimension_scores") or []),
        "",
        "### 问题清单",
        "",
        render_issues_md(fmt.get("issues") or []),
        "",
    ]
    if fmt.get("highlights"):
        lines += ["### 格式亮点", ""] + [f"- {h}" for h in fmt["highlights"]] + [""]
    lines += ["### 小结", "", str(fmt.get("summary", "-")), ""]
    return "\n".join(lines)


def render_content_md(content: Optional[dict], error: Optional[str]) -> str:
    if error:
        return f"### ⚠️ 内容评审员执行失败\n\n```\n{error}\n```"
    if not content:
        return "> 无内容审查结果。"
    lines = [
        f"### 内容得分：{_fmt_score(content.get('content_score', 0))} / {CONTENT_MAX}",
        "",
        render_dimension_md(content.get("dimension_scores") or []),
        "",
        "### 问题清单",
        "",
        render_issues_md(content.get("issues") or [], extra_fields=("correction",)),
        "",
    ]
    if content.get("highlights"):
        lines += ["### 内容亮点", ""] + [f"- {h}" for h in content["highlights"]] + [""]
    lines += ["### 小结", "", str(content.get("summary", "-")), ""]
    return "\n".join(lines)


def build_full_markdown(result: Dict[str, Any], standard: str, report: str) -> str:
    """把一次批阅的全部结果拼成一份可下载的 Markdown 报告。"""
    guard = result.get("guard") or {}
    risk_title = {
        "none": "✅ 未发现学术不端风险",
        "low": "🟡 存在轻微疑点（建议人工关注）",
        "medium": "🟠 存在学术不端风险（已中断批阅）",
        "high": "🔴 存在严重学术不端风险（已中断批阅）",
    }.get(guard.get("risk_level", "low"), "❓ 未知")

    parts = [
        "# 实验报告批阅结果",
        "",
        "## 〇、护栏检查结果",
        "",
        render_guard_md(guard, risk_title),
        "",
    ]
    if result.get("status") == "blocked":
        parts += [
            "---",
            "",
            "> **⛔ 批阅已中断**：护栏监督员判定该报告存在学术不端风险，未进入后续评分环节，请提交人工复核。",
            "",
        ]
        return "\n".join(parts)

    parts += [
        "---",
        "",
        "## 一、评分与解释",
        "",
        render_judge_md(result.get("judge") or {}),
        "",
        "---",
        "",
        "## 二、格式审查详情",
        "",
        render_format_md(result.get("format"), result.get("format_error")),
        "",
        "---",
        "",
        "## 三、内容审查详情",
        "",
        render_content_md(result.get("content"), result.get("content_error")),
        "",
        "---",
        "",
        "## 附：原始输入（便于复核）",
        "",
        "### 实验指导书 / 评分标准",
        "",
        "```text",
        _truncate(standard, 4000),
        "```",
        "",
        "### 学生实验报告",
        "",
        "```text",
        _truncate(report, 8000),
        "```",
        "",
    ]
    return "\n".join(parts)


# ===========================================================================
# 第七部分：示例库（来自同目录 samples.py，共 6 套，可下拉切换）
# ===========================================================================
#
#   samples.SAMPLES 里每一套示例包含：
#       name     —— 下拉框显示的名称（含"正确范例 / 含典型错误 / 学术不端"标签）
#       kind     —— good / bad / violation
#       standard —— 该实验的指导书与评分标准
#       report   —— 学生提交的报告原文
#       note     —— 预埋了哪些问题、预期得分区间（显示在界面上帮助讲解）
#
#   其中 kind == "violation" 的那套会触发护栏熔断，用于演示"直接中断批阅"的机制。


# ===========================================================================
# 第八部分：Streamlit 界面
# ===========================================================================

def init_state() -> None:
    """初始化 session_state，避免多次 rerun 丢失结果。"""
    defaults = {
        "result": None,
        "running": False,
        "standard_text": "",
        "report_text": "",
        "engine_status": None,      # 最近一次"测试连接"的结果
        "engine_name": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def resolve_api_key() -> Tuple[str, str]:
    """按优先级解析 API Key，返回 (key, 来源说明)。

    优先级：侧边栏临时填写 > 环境变量 DEEPSEEK_API_KEY > Streamlit secrets > 同目录 .env 文件
    """
    manual = st.session_state.get("manual_api_key", "").strip()
    if manual:
        return manual, "侧边栏临时输入"

    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key, "环境变量 DEEPSEEK_API_KEY"

    # Streamlit 官方推荐方式（.streamlit/secrets.toml）
    try:
        secret_key = str(st.secrets.get("DEEPSEEK_API_KEY", "")).strip()  # type: ignore[union-attr]
        if secret_key:
            return secret_key, ".streamlit/secrets.toml"
    except Exception:  # noqa: BLE001 —— 没有 secrets 文件时会抛异常，属正常情况
        pass

    # 兼容简单的 .env 文件（无需 python-dotenv）
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.isfile(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    if key.strip() == "DEEPSEEK_API_KEY":
                        value = value.strip().strip('"').strip("'")
                        if value and "请替换" not in value:
                            return value, "同目录 .env 文件"
        except OSError:
            pass

    return "", "未找到"


def render_sidebar() -> Dict[str, Any]:
    """渲染侧边栏配置，返回运行参数。"""
    with st.sidebar:
        st.header("⚙️ 运行配置")

        # ---------- API Key ----------
        st.text_input(
            "临时 API Key（可选，优先级最高）",
            key="manual_api_key",
            type="password",
            help="留空则依次读取环境变量 DEEPSEEK_API_KEY、Streamlit secrets、同目录 .env 文件。"
                 "此处填写的内容只保存在当前会话内存中，不会写入磁盘。",
        )
        api_key, key_source = resolve_api_key()
        if api_key:
            st.success(f"已获取 API Key（来源：{key_source}）", icon="🔑")
        else:
            st.warning("未检测到 API Key，请设置环境变量或在上面填写。", icon="⚠️")

        # ---------- 调用引擎 ----------
        engine_mode = st.radio(
            "模型调用引擎",
            ["自动（优先 openai SDK，失败则降级）", "仅用 httpx / 标准库（零安装）"],
            index=0,
            help="如果电脑上装不上 openai 包，选第二项即可，功能完全一致。",
        )
        prefer_sdk = engine_mode.startswith("自动")

        # ---------- 接口参数 ----------
        with st.expander("接口参数", expanded=False):
            base_url = st.text_input("Base URL", value=DEFAULT_BASE_URL)
            model = st.text_input("模型名称", value=DEFAULT_MODEL)
            temperature = st.slider("温度 temperature", 0.0, 1.0, DEFAULT_TEMPERATURE, 0.05,
                                    help="批阅任务建议 0.1~0.3，保证评分稳定。")
            timeout = st.number_input("单次请求超时（秒）", min_value=30, max_value=900, value=180, step=30)
            use_json_mode = st.checkbox("启用 JSON 输出模式", value=True,
                                        help="让模型直接返回 JSON，解析更稳。若网关报错会自动降级。")
            allow_parallel = st.checkbox("格式与内容审查并行执行", value=True,
                                         help="两位审查员互不依赖，并行可节省约一半时间。")

        # ---------- 引擎自检 ----------
        if st.button("🔌 测试连接", use_container_width=True):
            if not api_key:
                st.session_state["engine_status"] = ("error", "请先配置 API Key。")
            else:
                with st.spinner("正在呼叫 DeepSeek……"):
                    try:
                        test_engine, notice = make_engine(
                            api_key, base_url.strip() or DEFAULT_BASE_URL,
                            model.strip() or DEFAULT_MODEL, float(timeout), prefer_sdk,
                        )
                        reply = call_agent(
                            "连接测试", "你是一个测试助手。", "请只回复两个字：连接成功",
                            engine=test_engine, temperature=0.0, max_tokens=16, use_json_mode=False,
                        )
                        st.session_state["engine_name"] = test_engine.name
                        msg = f"引擎：[{test_engine.name}]　模型回复：{reply.strip()[:40]}"
                        if notice:
                            msg += f"\n\nℹ️ {notice}"
                        st.session_state["engine_status"] = ("ok", msg)
                    except Exception as exc:  # noqa: BLE001
                        st.session_state["engine_status"] = ("error", f"{type(exc).__name__}: {exc}")

        status = st.session_state.get("engine_status")
        if status:
            kind, message = status
            (st.success if kind == "ok" else st.error)(message, icon="✅" if kind == "ok" else "❌")

        # ---------- 重点关注项 ----------
        st.subheader("🎯 护栏重点关注项")
        focus_tags = st.multiselect(
            "选择需要特别关注的风险类型（会写入护栏提示词）",
            ["抄袭", "数据篡改", "代写/AI代写", "不当言论"],
            default=["抄袭", "数据篡改"],
        )

        st.divider()
        st.caption(
            "模型：DeepSeek（OpenAI 兼容接口）\n\n"
            "智能体：护栏监督员 → 格式审查员 ‖ 内容评审员 → 评分与解释官\n\n"
            f"内置示例：{len(SAMPLES)} 套（正确 / 含错 / 学术不端），在主界面顶部下拉选择\n\n"
            f"当前解释器：`{sys.executable}`"
        )

    return {
        "api_key": api_key,
        "prefer_sdk": prefer_sdk,
        "base_url": base_url.strip() or DEFAULT_BASE_URL,
        "model": model.strip() or DEFAULT_MODEL,
        "temperature": temperature,
        "timeout": float(timeout),
        "use_json_mode": use_json_mode,
        "allow_parallel": allow_parallel,
        "focus_tags": focus_tags,
    }


def render_results(result: Dict[str, Any]) -> None:
    """分区展示批阅结果。"""
    guard = result.get("guard") or {}
    risk = guard.get("risk_level", "low")

    # ---------- 顶部状态条 ----------
    if result.get("status") == "blocked":
        st.error(
            "⛔ **批阅已中断：护栏监督员检测到学术不端风险**\n\n"
            "系统已停止后续评分，请提交人工复核。",
            icon="🚨",
        )
    elif result.get("status") == "error":
        st.error(f"❌ 批阅过程中出现错误：{result.get('error')}", icon="❌")
    else:
        judge = result.get("judge") or {}
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("总分", f"{_fmt_score(judge.get('total_score', 0))} / 100")
        col2.metric("等级", str(judge.get("grade", "-")))
        col3.metric("格式分", f"{_fmt_score((result.get('format') or {}).get('format_score', 0))} / {FORMAT_MAX}")
        col4.metric("内容分", f"{_fmt_score((result.get('content') or {}).get('content_score', 0))} / {CONTENT_MAX}")

    st.divider()

    tabs = st.tabs(["📊 评分与解释", "📐 格式审查详情", "🔬 内容审查详情", "🛡️ 护栏检查结果"])

    with tabs[0]:
        if result.get("status") == "blocked":
            st.warning("因护栏熔断，未产生评分结果。", icon="⛔")
        elif result.get("judge"):
            st.markdown(render_judge_md(result["judge"]))
        else:
            st.warning(f"未产生评分结果。{result.get('error') or ''}")

    with tabs[1]:
        st.markdown(render_format_md(result.get("format"), result.get("format_error")))

    with tabs[2]:
        st.markdown(render_content_md(result.get("content"), result.get("content_error")))

    with tabs[3]:
        risk_title = {
            "none": "✅ 未发现学术不端风险",
            "low": "🟡 存在轻微疑点（建议人工关注）",
            "medium": "🟠 存在学术不端风险（已中断批阅）",
            "high": "🔴 存在严重学术不端风险（已中断批阅）",
        }.get(risk, "❓ 未知")
        st.markdown(render_guard_md(guard, risk_title))

    # ---------- 原始输出（便于调试 prompt） ----------
    with st.expander("🧾 查看各智能体的原始 JSON 输出"):
        st.caption("提示：若某一步解析失败，可在此查看模型原文，据此微调提示词。")
        raw_map = [
            ("护栏监督员", result.get("guard_raw")),
            ("格式审查员", result.get("format_raw")),
            ("内容评审员", result.get("content_raw")),
            ("评分与解释官", result.get("judge_raw")),
        ]
        for title, raw in raw_map:
            if raw:
                st.markdown(f"**{title}**")
                st.code(raw, language="json")


def main() -> None:
    st.set_page_config(
        page_title="实验报告批阅多智能体系统",
        page_icon="🧪",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    init_state()

    st.title("🧪 实验报告批阅多智能体系统")
    st.caption(
        "四个智能体协作批阅：**护栏监督员** → **格式审查员** ‖ **内容评审员** → **评分与解释官**　|　"
        "评分可解释：每一分都能追溯到报告原文"
    )

    params = render_sidebar()

    # ---------- 示例库选择（先选案例，再一键填充） ----------
    counts = count_by_kind()
    st.markdown("#### 📚 示例库")
    select_col, fill_col = st.columns([3, 2])

    with select_col:
        sample_index = st.selectbox(
            "选择一套示例（指导书 + 学生报告），可一键填充到下方两个输入框",
            options=list(range(len(SAMPLES))),
            format_func=lambda i: SAMPLES[i]["name"],
            index=0,
            label_visibility="collapsed",
        )

    current_sample = get_sample(sample_index)

    with fill_col:
        btn1, btn2, btn3 = st.columns(3)
        with btn1:
            fill_standard = st.button("📋 填指导书", use_container_width=True,
                                      help="只填充左侧的指导书/评分标准")
        with btn2:
            fill_report = st.button("📝 填报告", use_container_width=True,
                                    help="只填充右侧的学生报告")
        with btn3:
            fill_both = st.button("⚡ 一键填充", type="primary", use_container_width=True,
                                  help="两个输入框一起填充")

    if fill_standard or fill_both:
        st.session_state["standard_text"] = current_sample["standard"]
    if fill_report or fill_both:
        st.session_state["report_text"] = current_sample["report"]

    # 显示当前案例的说明（预埋了哪些问题、预期得分），方便讲解与自测
    st.info(
        f"**{kind_label(current_sample['kind'])}**　|　{current_sample['note']}",
        icon="🧭",
    )
    st.caption(
        f"示例库共 {len(SAMPLES)} 套：✅ 正确范例 {counts.get('good', 0)} 套　·　"
        f"❌ 含典型错误 {counts.get('bad', 0)} 套　·　"
        f"🚨 学术不端（会触发护栏熔断）{counts.get('violation', 0)} 套"
    )

    # ---------- 输入区 ----------
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("① 实验指导书 / 评分标准")
        # 注意：这里直接写 st.session_state["standard_text"]，Streamlit 会把它作为该组件的当前值，
        # 因此「填充示例」按钮只需改 session_state 即可生效（不需要再传 value 参数，避免冲突告警）。
        standard = st.text_area(
            "粘贴实验指导书、实验要求或评分细则",
            height=340,
            key="standard_text",
            placeholder="例如：\n【实验目的】……\n【报告要求】正文不少于 800 字，图表须编号……\n【评分标准】格式 10 分 + 内容 70 分……",
            label_visibility="collapsed",
        )

    with col_right:
        st.subheader("② 学生实验报告")
        report = st.text_area(
            "粘贴学生提交的实验报告全文",
            height=340,
            key="report_text",
            placeholder="例如：\n一、实验目的……\n二、实验原理……\n三、数据记录……",
            label_visibility="collapsed",
        )

    st.caption(
        f"当前字数：指导书 {len(standard)} 字 ｜ 报告 {len(report)} 字　"
        "（超长内容会自动截断以适配上下文窗口）"
    )

    # ---------- 开始按钮 ----------
    start = st.button("🚀 开始批阅", type="primary", use_container_width=True, disabled=st.session_state["running"])

    if start:
        # 前置校验
        problems = []
        if not params["api_key"]:
            problems.append("未提供 API Key（请设置环境变量 DEEPSEEK_API_KEY 或在侧边栏填写）")
        if len(report.strip()) < 30:
            problems.append("学生实验报告内容过短（少于 30 字），无法批阅")
        if len(standard.strip()) < 10:
            problems.append("实验指导书/评分标准内容过短，无法作为评分依据")
        if problems:
            st.error("无法开始批阅：\n\n" + "\n".join(f"- {p}" for p in problems), icon="⚠️")
            return

        st.session_state["result"] = None
        st.session_state["running"] = True

        # 创建调用引擎（自动选择 openai SDK 或 httpx/标准库）
        engine, engine_notice = make_engine(
            params["api_key"], params["base_url"], params["model"],
            float(params["timeout"]), bool(params["prefer_sdk"]),
        )
        st.session_state["engine_name"] = engine.name

        progress_box = st.status(f"🚀 多智能体流水线已启动……（调用引擎：{engine.name}）", expanded=True)
        if engine_notice:
            progress_box.write(f"ℹ️ {engine_notice}")
        log_lines: List[str] = []

        def on_progress(message: str) -> None:
            log_lines.append(message)
            progress_box.write(message)

        try:
            with st.spinner("智能体正在协作批阅，请稍候……（首次调用约需 30~90 秒）"):
                result = review_report(
                    standard,
                    report,
                    engine,
                    temperature=float(params["temperature"]),
                    use_json_mode=bool(params["use_json_mode"]),
                    focus_tags=params["focus_tags"],
                    allow_parallel=bool(params["allow_parallel"]),
                    progress=on_progress,
                )
            st.session_state["result"] = result
            if result.get("status") == "blocked":
                progress_box.update(label="⛔ 护栏熔断，批阅已中断", state="error", expanded=True)
            elif result.get("status") == "error":
                progress_box.update(label="❌ 批阅出错", state="error", expanded=True)
            else:
                progress_box.update(label="✅ 批阅完成", state="complete", expanded=False)
        except Exception as exc:  # noqa: BLE001 —— 界面上给出明确错误而不是白屏
            st.session_state["result"] = None
            progress_box.update(label="❌ 调用失败", state="error", expanded=True)
            st.error(f"调用大模型失败：{type(exc).__name__}: {exc}", icon="❌")
            with st.expander("常见问题排查"):
                st.markdown(
                    "- **401 / Authentication Error**：API Key 不正确或未生效，检查 `DEEPSEEK_API_KEY`。\n"
                    "- **Connection error / timeout**：网络不通，或 Base URL 写错；确认可访问 `https://api.deepseek.com`。\n"
                    "- **Insufficient Balance**：DeepSeek 账户余额不足。\n"
                    "- **json_object / response_format 报错**：在侧边栏关闭「启用 JSON 输出模式」。\n"
                    "- **模型返回空内容**：稍后重试，或把温度降到 0.1。\n"
                    "- **想换调用引擎**：在侧边栏把「模型调用引擎」改为「仅用 httpx / 标准库（零安装）」。"
                )
        finally:
            st.session_state["running"] = False

    # ---------- 结果区 ----------
    result = st.session_state.get("result")
    if result:
        st.divider()
        if st.session_state.get("engine_name"):
            st.caption(f"本次批阅使用的调用引擎：**{st.session_state['engine_name']}**")
        render_results(result)

        # ---------- 导出 ----------
        st.divider()
        st.subheader("📥 导出批阅结果")
        full_md = build_full_markdown(result, st.session_state.get("standard_text", ""),
                                     st.session_state.get("report_text", ""))
        col_a, col_b = st.columns(2)
        with col_a:
            st.download_button(
                "⬇️ 下载 Markdown 批阅报告",
                data=full_md.encode("utf-8"),
                file_name="实验报告批阅结果.md",
                mime="text/markdown",
                use_container_width=True,
            )
        with col_b:
            st.download_button(
                "⬇️ 下载完整 JSON 结果",
                data=json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"),
                file_name="实验报告批阅结果.json",
                mime="application/json",
                use_container_width=True,
            )
        with st.expander("👀 预览可下载的 Markdown 报告"):
            st.markdown(full_md)


if __name__ == "__main__":
    # 直接 `python app.py` 时给出友好提示（Streamlit 应用必须用 streamlit run 启动）
    try:
        import streamlit.runtime  # noqa: F401
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        _in_streamlit = get_script_run_ctx() is not None
    except Exception:  # noqa: BLE001
        _in_streamlit = False

    if _in_streamlit:
        main()
    else:
        print("=" * 68)
        print("这是一个 Streamlit 应用，请使用以下命令启动：")
        print()
        print("    streamlit run app.py")
        print()
        print(f"当前解释器：{sys.executable}")
        print("=" * 68)
