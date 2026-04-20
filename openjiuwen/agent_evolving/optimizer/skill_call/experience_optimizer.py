# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Online Skill experience optimizer."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from openjiuwen.agent_evolving.checkpointing.types import (
    VALID_SECTIONS,
    EvolutionContext,
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.optimizer.base import BaseOptimizer
from openjiuwen.agent_evolving.signal.base import EvolutionSignal
from openjiuwen.agent_evolving.trajectory.types import Updates
from openjiuwen.core.common.logging import logger

# Initial score mapping by signal type
INITIAL_SCORE_BY_SIGNAL = {
    "execution_failure": 0.65,
    "user_correction": 0.70,
    "script_artifact": 0.60,
    "conversation_review": 0.50,
}

NEW_SKILL_PROPOSAL_PROMPT_CN = """\
你是一个 Skill 设计专家。根据对话历史，判断是否值得创建一个新的 Skill 来封装这个工作流。

## 对话历史
{conversation_snippet}

## 现有 Skill 列表
{existing_skill_names}

## 判断标准
1. 对话包含复杂的多步骤工作流
2. 工作流具有通用性和可复用性
3. 现有 Skill 无法覆盖此场景
4. 工作流涉及特定工具组合或特定领域知识

## 输出格式
如果值得创建新 Skill，输出：
```json
{{
  "should_create": true,
  "name": "建议的 Skill 名称（英文，短横线连接）",
  "description": "一句话描述 Skill 用途",
  "body": "完整的 Skill 内容（Instructions、Examples 等）",
  "reason": "为什么值得创建这个 Skill"
}}
```

如果不值得创建，输出：
```json
{{"should_create": false}}
```

只输出 JSON，不要其他内容。"""

NEW_SKILL_PROPOSAL_PROMPT_EN = """\
You are a Skill design expert. Based on the conversation history, determine whether it's worth creating a new Skill to encapsulate this workflow.

## Conversation History
{conversation_snippet}

## Existing Skill Names
{existing_skill_names}

## Criteria
1. The conversation contains a complex multi-step workflow
2. The workflow has generality and reusability
3. Existing Skills cannot cover this scenario
4. The workflow involves specific tool combinations or domain knowledge

## Output Format
If a new Skill is worth creating, output:
```json
{{
  "should_create": true,
  "name": "Suggested Skill name (English, kebab-case)",
  "description": "One-sentence description of Skill purpose",
  "body": "Complete Skill content (Instructions, Examples, etc.)",
  "reason": "Why this Skill is worth creating"
}}
```

If not worth creating, output:
```json
{{"should_create": false}}
```

Output only JSON, no other content."""

NEW_SKILL_PROPOSAL_PROMPT: Dict[str, str] = {
    "cn": NEW_SKILL_PROPOSAL_PROMPT_CN,
    "en": NEW_SKILL_PROPOSAL_PROMPT_EN,
}

SKILL_EXPERIENCE_GENERATE_PROMPT_CN = """\
你是一个 Skill 优化专家。根据对话中发现的问题信号和对话历史，为 Skill 生成演进经验。

## 输入信息

### 当前 Skill 内容
{skill_content}

### 预检测信号（规则引擎自动提取）
{signals_json}

### 对话历史
{conversation_snippet}

### 已有 description 经验
{existing_desc_summary}

### 已有 body 经验
{existing_body_summary}

### 用户主动描述的优化方向（可选）
{user_query}

## 经验来源

经验来自两个渠道，都要处理：

**渠道 A — 预检测信号**：上方「预检测信号」中列出的条目，由规则引擎自动从对话中提取，可能包含误报。

**渠道 B — 对话历史直接分析**：直接审视「对话历史」，发现规则引擎未捕获的有价值经验，包括但不限于：
- Agent 经过多次尝试/重试才成功的 workaround（说明 Skill 缺少相关指导）
- 用户含蓄的纠正或补充说明（未使用"错了""不对"等显式关键词）
- 低效的工具调用模式（如多余步骤、错误的调用顺序）
- Agent 遗漏的关键步骤（用户不得不手动补充）
- 需要特殊处理的边界情况（Skill 中未覆盖的场景）

**渠道 C — 脚本工件提取**：检查「预检测信号」中 type 为 "script_artifact" 的条目。这些是 Agent 在对话中生成并成功执行的脚本代码。评估其复用价值：
- 高复用价值：图表生成（matplotlib/plotly）、图标/配图生成（PIL）、数据处理（JSON/CSV/Excel 转换）、自动化脚本（批量操作、格式化等）
- 排除标准：仅包含硬编码特定数据的一次性脚本（纯硬编码特定内容才排除）
- 脚本类经验使用 target="script"，section="Scripts"

如果对话历史中没有额外发现，不需要强制生成；如果有发现，与预检测信号的经验一起输出。

## 数量限制

最终输出的有效经验（action 为 append 的条目）：**文本经验不超过 2 条，脚本经验不超过 1 条**，独立计数互不影响。
如果候选经验超过限制，按以下优先级保留最重要的，其余标记为 skip：
1. 导致任务失败或产出错误结果的问题 > 导致效率低下但最终成功的问题
2. 高频/可复现的模式 > 单次偶发现象
3. 渠道 A/B/C 的发现同等对待，仅按影响程度排序

## 决策流程（对每条潜在经验按顺序执行）

### 第一步：相关性判断
判断该经验是否与 Skill 本身相关：
- 相关：问题由 Skill 的指令、脚本、示例或排查逻辑导致 -> 继续第二步
- 不相关：问题由外部因素导致（网络、环境、权限、第三方服务等）-> 输出 {{"action": "skip", "skip_reason": "irrelevant"}}

### 第二步：去重判断
对比已有演进经验（description 和 body 两个列表）：
- 实质相同：与某条已有记录内容重复 -> 输出 {{"action": "skip", "skip_reason": "duplicate"}}
- 高度相似但有增量：与某条已有记录相关但有新信息 -> 输出合并后的完整内容，并设置 "merge_target" 为目标记录 id
- 全新：与已有记录无关 -> 继续第三步

### 第三步：优先级筛选与生成
将所有通过前两步的候选经验按优先级排序，仅为排名前 2 的候选生成内容，其余输出 {{"action": "skip", "skip_reason": "low_priority"}}。
确定经验归属层（target）和章节（section），然后生成内容。

**target 判断（三选一）：**
- **description**（描述/元数据层）：涉及 Skill 适用场景判断错误、描述不准确、缺少关键词导致未被选中或误选
- **body**（正文/指令层）：涉及执行步骤、工具调用错误、操作流程、排查逻辑
- **script**（脚本工件层）：Agent 生成并成功执行的可复用脚本代码（渠道 C）

**section 选择参考：**
- execution_failure / workaround 类：通常归入 Troubleshooting
- user_correction / 流程偏差类：通常归入 Instructions 或 Examples
- script_artifact 类：归入 Scripts

## 内容生成规范
1. 语言一致：输出语言必须与 Skill 完全一致（中文 Skill 输出中文，英文 Skill 输出英文）
2. 标题层级：使用与 Skill 相同的标题层级（##、### 等）
3. 每条记录：1 个标题 + 2-3 个无序列表分点（- 或 *），禁止子层级
4. 每条记录只涉及一个 section 类型，不混合
5. 提取可复用的通用规则，非临时补丁（好："遇到 X 错误时，先检查 Y 再执行 Z"；差："某用户某次提到某问题"）
6. 内容必须是 Skill 中未提及的新知识，精炼简洁
7. 多个发现指向同一问题时合并为一条；不同问题分别生成
8. 文本经验（action 为 append，target 为 description/body）最多 2 条；脚本经验（target 为 script）最多 1 条
9. 脚本经验的 content 字段直接放完整脚本源码，同时填写 script_filename、script_language、script_purpose

## 输出格式
只输出以下 JSON 数组，不要其他内容（即使只有一条，也必须用数组包裹）：
[
  {{
    "action": "append | skip",
    "skip_reason": "irrelevant | duplicate | low_priority（仅 action 为 skip 时填写，否则为 null）",
    "target": "description | body | script",
    "section": "Instructions | Examples | Troubleshooting | Scripts",
    "content": "Markdown 内容或脚本源码（仅 action 为 append 时填写）",
    "merge_target": "ev_xxxxxxxx 或 null",
    "script_filename": "文件名（仅 target 为 script 时填写，如 generate_chart.py）",
    "script_language": "语言标识（仅 target 为 script 时填写，如 python）",
    "script_purpose": "用途说明（仅 target 为 script 时填写）"
  }}
]"""

SKILL_EXPERIENCE_GENERATE_PROMPT_EN = """\
You are a Skill optimization expert. Based on problem signals discovered in the conversation and the conversation history, generate evolution experiences for the Skill.

## Input Information

### Current Skill Content
{skill_content}

### Pre-detected Signals (automatically extracted by the rule engine)
{signals_json}

### Conversation History
{conversation_snippet}

### Existing description experiences
{existing_desc_summary}

### Existing body experiences
{existing_body_summary}

### User-specified optimization direction (optional)
{user_query}

## Experience Sources

Experiences come from two channels, both must be processed:

**Channel A — Pre-detected Signals**: The entries listed in the "Pre-detected Signals" section above, automatically extracted from the conversation by the rule engine. May contain false positives.

**Channel B — Direct Conversation History Analysis**: Directly examine the "Conversation History" to discover valuable experiences not captured by the rule engine, including but not limited to:
- Workarounds where the Agent succeeded only after multiple attempts/retries (indicating the Skill lacks relevant guidance)
- Implicit corrections or supplementary explanations from the user (without using explicit keywords like "wrong" or "incorrect")
- Inefficient tool invocation patterns (e.g., redundant steps, incorrect invocation order)
- Critical steps missed by the Agent (where the user had to manually fill in)
- Edge cases requiring special handling (scenarios not covered by the Skill)

**Channel C — Script Artifact Extraction**: Check the "Pre-detected Signals" for entries with type "script_artifact". These are scripts that the Agent generated and successfully executed during the conversation. Evaluate their reuse value:
- High reuse value: chart generation (matplotlib/plotly), icon/image generation (PIL), data processing (JSON/CSV/Excel conversion), automation scripts (batch operations, formatting, etc.)
- Exclusion criteria: one-off scripts that only contain hardcoded specific data
- Script experiences use target="script", section="Scripts"

If no additional findings exist in the conversation history, do not force generation; if findings exist, output them together with the pre-detected signal experiences.

## Quantity Limit

The final output of valid experiences (entries with action "append"): **text experiences must not exceed 2, script experiences must not exceed 1**, counted independently.
If candidate experiences exceed the limit, retain the most important ones by the following priority and mark the rest as skip:
1. Issues causing task failure or incorrect results > Issues causing inefficiency but eventual success
2. High-frequency / reproducible patterns > One-off occurrences
3. Findings from Channel A/B/C are treated equally, sorted only by impact level

## Decision Flow (execute sequentially for each potential experience)

### Step 1: Relevance Check
Determine whether the experience is related to the Skill itself:
- Relevant: The issue is caused by the Skill's instructions, scripts, examples, or troubleshooting logic -> proceed to Step 2
- Irrelevant: The issue is caused by external factors (network, environment, permissions, third-party services, etc.) -> output {{"action": "skip", "skip_reason": "irrelevant"}}

### Step 2: Deduplication Check
Compare against existing evolution experiences (both description and body lists):
- Essentially identical: Duplicates an existing record -> output {{"action": "skip", "skip_reason": "duplicate"}}
- Highly similar but with incremental value: Related to an existing record but contains new information -> output the merged complete content and set "merge_target" to the target record id
- Entirely new: Unrelated to existing records -> proceed to Step 3

### Step 3: Priority Filtering and Generation
Sort all candidates that passed the first two steps by priority, generate content only for the top 2, and output {{"action": "skip", "skip_reason": "low_priority"}} for the rest.
Determine the experience's target layer (target) and section (section), then generate the content.

**target selection (choose one):**
- **description** (metadata layer): Involves incorrect Skill applicability judgment, inaccurate description, missing keywords causing the Skill to be unselected or incorrectly selected
- **body** (instruction layer): Involves execution steps, tool invocation errors, operational procedures, troubleshooting logic
- **script** (script artifact layer): Reusable scripts that the Agent generated and successfully executed (Channel C)

**section selection reference:**
- execution_failure / workaround types: Usually belong to Troubleshooting
- user_correction / process deviation types: Usually belong to Instructions or Examples
- script_artifact types: Belong to Scripts

## Content Generation Guidelines
1. Language consistency: Output language must match the Skill exactly (Chinese Skill outputs Chinese, English Skill outputs English)
2. Heading levels: Use the same heading levels as the Skill (##, ###, etc.)
3. Each record: 1 heading + 2-3 unordered list items (- or *), no sub-levels allowed
4. Each record covers only one section type, no mixing
5. Extract reusable general rules, not temporary patches (good: "When encountering error X, first check Y then execute Z"; bad: "A certain user once mentioned a certain issue")
6. Content must be new knowledge not already mentioned in the Skill, concise and refined
7. When multiple findings point to the same issue, merge into one entry; generate separately for different issues
8. Text experiences (action "append", target description/body): at most 2; script experiences (target script): at most 1
9. For script experiences, put the full script source code in the content field, and fill in script_filename, script_language, script_purpose

## Output Format
Output only the following JSON array, nothing else (even if there is only one entry, it must be wrapped in an array):
[
  {{
    "action": "append | skip",
    "skip_reason": "irrelevant | duplicate | low_priority (fill only when action is skip, otherwise null)",
    "target": "description | body | script",
    "section": "Instructions | Examples | Troubleshooting | Scripts",
    "content": "Markdown content or script source code (fill only when action is append)",
    "merge_target": "ev_xxxxxxxx or null",
    "script_filename": "filename (only when target is script, e.g. generate_chart.py)",
    "script_language": "language identifier (only when target is script, e.g. python)",
    "script_purpose": "purpose description (only when target is script)"
  }}
]"""

SKILL_EXPERIENCE_GENERATE_PROMPT: Dict[str, str] = {
    "cn": SKILL_EXPERIENCE_GENERATE_PROMPT_CN,
    "en": SKILL_EXPERIENCE_GENERATE_PROMPT_EN,
}


def _build_conversation_snippet(
    messages: List[dict],
    max_messages: int = 30,
    content_preview_chars: int = 300,
    language: str = "cn",
) -> str:
    """Build compact dialogue snippet for LLM prompt context."""
    if not messages:
        return ""

    def _extract_text(message: dict) -> str:
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts)
        return str(content)

    lines: List[str] = []
    recent = messages[-max_messages:]
    for i, message in enumerate(recent):
        role = message.get("role", "unknown")
        text = _extract_text(message).strip() or ("(无文本)" if language == "cn" else "(No text)")
        budget = content_preview_chars * 2 if i >= len(recent) - 5 else content_preview_chars
        if len(text) > budget:
            orig_len = len(text)
            text = text[:budget] + (
                f"\n... [已截断，原始长度 {orig_len} 字符]"
                if language == "cn"
                else f"\n... [truncated, original {orig_len} chars]"
            )
        tool_calls = message.get("tool_calls")
        if role == "assistant" and tool_calls:
            names = [tool_call.get("name", "") for tool_call in tool_calls if isinstance(tool_call, dict)]
            prefix = f"[assistant] (tool_calls: {', '.join(names)})\n  "
        else:
            prefix = f"[{role}] "
        lines.append(prefix + text)
    return "\n".join(lines)


_SKILL_CONTENT_MAX_CHARS = 6000
_HEADING_RE = re.compile(r"^#{1,4}\s+")
_SECTION_PREVIEW_CHARS = 200


def _summarize_skill_content(raw: str, max_chars: int = _SKILL_CONTENT_MAX_CHARS) -> str:
    """Condense a large SKILL.md for the evolution LLM."""
    if len(raw) <= max_chars:
        return raw

    sections = _split_into_sections(raw)
    if not sections:
        return raw[:max_chars] + f"\n... [已截断，原始共 {len(raw)} 字符]"

    parts: list[str] = [sections[0]]
    if len(sections) > 1:
        parts.append("\n[以下章节仅保留标题与开头摘要，完整内容已省略]\n")
        for section in sections[1:]:
            parts.append(_preview_section(section))

    summary = "\n".join(parts)
    if len(summary) > max_chars:
        summary = summary[:max_chars] + f"\n... [已截断，原始 SKILL.md 共 {len(raw)} 字符]"
    return summary


def _split_into_sections(text: str) -> list[str]:
    """Split markdown into sections by top-level headings (# or ##)."""
    lines = text.split("\n")
    sections: list[str] = []
    current: list[str] = []

    for line in lines:
        if _HEADING_RE.match(line) and current:
            sections.append("\n".join(current))
            current = []
        current.append(line)

    if current:
        sections.append("\n".join(current))
    return sections


def _preview_section(section: str, preview_chars: int = _SECTION_PREVIEW_CHARS) -> str:
    """Return heading + first preview_chars of body text."""
    lines = section.split("\n")
    heading = lines[0]
    body = "\n".join(lines[1:]).strip()
    if not body:
        return heading
    if len(body) <= preview_chars:
        return section
    return f"{heading}\n{body[:preview_chars]}..."


def _fix_json_text(text: str) -> str:
    """Apply common fixes to malformed JSON produced by LLMs."""
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text.strip()


def _try_parse(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_json(raw: str) -> Optional[Any]:
    """Best-effort JSON extraction from LLM output."""
    raw = raw.strip()
    if not raw:
        return None

    result = _try_parse(raw)
    if result is not None:
        return result

    fixed = _fix_json_text(raw)
    result = _try_parse(fixed)
    if result is not None:
        return result

    for pattern in (r"\[[\s\S]*\]", r"\{[\s\S]*\}"):
        matched = re.search(pattern, fixed)
        if matched:
            result = _try_parse(matched.group(0))
            if result is not None:
                return result
            refixed = _fix_json_text(matched.group(0))
            result = _try_parse(refixed)
            if result is not None:
                return result

    return None


_CONTEXT_MAX_CHARS = 500


def _build_context(signals: list, max_chars: int = _CONTEXT_MAX_CHARS) -> str:
    """Build a concise context string from signals, capped to max_chars."""
    if not signals:
        return ""
    per_signal = max(80, max_chars // len(signals))
    parts: list[str] = []
    for sig in signals:
        excerpt = sig.excerpt.strip()
        if len(excerpt) > per_signal:
            excerpt = excerpt[:per_signal] + "..."
        parts.append(f"[{sig.signal_type}] {excerpt}")
    return " | ".join(parts)


def _looks_truncated(text: str) -> bool:
    """Heuristic check: does the LLM output look like it was cut off mid-way?"""
    opens = text.count("{") + text.count("[")
    closes = text.count("}") + text.count("]")
    return opens > closes + 1


def _build_existing_summary(records: List[EvolutionRecord], label: str = "") -> str:
    if not records:
        return ""
    lines: List[str] = []
    for record in records:
        prefix = f"[{label}] " if label else ""
        lines.append(f"- {prefix}[{record.id}] [{record.change.section}] {record.change.content}")
    return "\n".join(lines)


def _parse_single_patch(data: dict) -> Optional[EvolutionPatch]:
    action = data.get("action", "append")
    if action == "skip":
        return EvolutionPatch(
            section="",
            action="skip",
            content="",
            skip_reason=data.get("skip_reason", "unknown"),
        )

    section = data.get("section", "Troubleshooting")
    if section not in VALID_SECTIONS:
        section = "Troubleshooting"

    raw_target = data.get("target", "body")
    try:
        target = EvolutionTarget(raw_target)
    except ValueError:
        target = EvolutionTarget.BODY

    merge_target = data.get("merge_target")
    if merge_target in ("null", None):
        merge_target = None

    return EvolutionPatch(
        section=section,
        action="append",
        content=data.get("content", ""),
        target=target,
        merge_target=merge_target,
        script_filename=data.get("script_filename"),
        script_language=data.get("script_language"),
        script_purpose=data.get("script_purpose"),
    )


def _parse_llm_response(raw: str) -> Optional[List[EvolutionPatch]]:
    """Parse response JSON into EvolutionPatch list. Returns None on parse failure."""
    data = _extract_json(raw)
    if data is None:
        return None

    items = data if isinstance(data, list) else [data]
    patches: List[EvolutionPatch] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        patch = _parse_single_patch(item)
        if patch is not None:
            patches.append(patch)
    return patches


_JSON_FIX_PROMPT = """\
你上次的输出不是合法 JSON，请修复并重新输出。
只输出修复后的 JSON 数组，不要任何解释文字。

## 目标格式
[
  {{
    "action": "append | skip",
    "skip_reason": "irrelevant | duplicate | low_priority（仅 skip 时填写，否则为 null）",
    "target": "description | body | script",
    "section": "Instructions | Examples | Troubleshooting | Scripts",
    "content": "Markdown 内容（注意 JSON 转义：换行用 \\\\n，引号用 \\\\"）",
    "merge_target": "ev_xxxxxxxx 或 null",
    "script_filename": "文件名或 null",
    "script_language": "语言标识或 null",
    "script_purpose": "用途说明或 null"
  }}
]

## 原始输出（请从中提取并修复）
{broken_output}"""


class SkillExperienceOptimizer(BaseOptimizer):
    """Online Skill experience optimizer.

    Signals arrive from SkillEvolutionRail; context is None (online path),
    so _get_bad_signals() retains all signals. _backward() groups signals
    by skill_name and generates EvolutionRecord(s).
    """

    domain = "skill_experience"

    def __init__(self, llm: Any, model: str, language: str = "cn") -> None:
        super().__init__()
        self._llm = llm
        self._model = model
        self._language = language

    @staticmethod
    def default_targets() -> List[str]:
        return ["experiences"]

    async def _backward(self, signals: List[EvolutionSignal]) -> None:
        """Generate experience records for each bound SkillCallOperator."""
        for op_id, op in self._operators.items():
            skill_name = op_id.removeprefix("skill_call_")
            skill_signals = [s for s in self._bad_signals if s.skill_name == skill_name or not s.skill_name]
            if not skill_signals:
                continue
            state = op.get_state()
            ctx = EvolutionContext(
                skill_name=skill_name,
                signals=skill_signals,
                skill_content=state.get("skill_content", ""),
                messages=state.get("messages", []),
                existing_desc_records=state.get("desc_records", []),
                existing_body_records=state.get("body_records", []),
                user_query=state.get("user_query", ""),
            )
            records = await self.generate_records(ctx)
            if not records:
                logger.info("[SkillExperienceOptimizer] no records generated for skill=%s", skill_name)
                continue
            existing: List = self._parameters[op_id].get_gradient("experiences") or []
            self._parameters[op_id].set_gradient("experiences", existing + records)
            logger.info(
                "[SkillExperienceOptimizer] generated %d record(s) for skill=%s",
                len(records),
                skill_name,
            )

    def _step(self) -> Updates:
        updates: Updates = {}
        for op_id, param in self._parameters.items():
            records: List = param.get_gradient("experiences") or []
            if records:
                updates[(op_id, "experiences")] = records
        return updates

    async def generate_records(self, ctx: EvolutionContext) -> List[EvolutionRecord]:
        """Generate and parse evolution records from LLM output."""
        if not ctx.signals:
            return []

        conversation_snippet = _build_conversation_snippet(ctx.messages, language=self._language)
        signals_json = json.dumps(
            [signal.to_dict() for signal in ctx.signals],
            ensure_ascii=False,
            indent=2,
        )
        desc_summary = _build_existing_summary(ctx.existing_desc_records, label="description")
        body_summary = _build_existing_summary(ctx.existing_body_records, label="body")
        skill_content = _summarize_skill_content(ctx.skill_content)
        prompt = SKILL_EXPERIENCE_GENERATE_PROMPT[self._language].format(
            skill_content=skill_content,
            signals_json=signals_json,
            conversation_snippet=(conversation_snippet or "").strip(),
            existing_desc_summary=desc_summary or ("无已有记录" if self._language == "cn" else "No existing records"),
            existing_body_summary=body_summary or ("无已有记录" if self._language == "cn" else "No existing records"),
            user_query=ctx.user_query or ("无" if self._language == "cn" else "None"),
        )

        logger.info("[SkillExperienceOptimizer] calling LLM (skill=%s)", ctx.skill_name)
        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("[SkillExperienceOptimizer] LLM call failed: %s", exc)
            return []

        patches = _parse_llm_response(raw)
        if patches is None:
            patches = await self.retry_parse(raw, original_prompt=prompt)
        source = ctx.signals[0].signal_type
        merged_context = _build_context(ctx.signals)
        text_records: List[EvolutionRecord] = []
        script_records: List[EvolutionRecord] = []

        for patch in patches:
            if patch.action == "skip":
                logger.info(
                    "[SkillExperienceOptimizer] LLM decided to skip (reason=%s)",
                    patch.skip_reason or "unknown",
                )
                continue
            if not patch.content.strip():
                logger.info("[SkillExperienceOptimizer] LLM returned empty content, skipping")
                continue
            is_script = patch.target == EvolutionTarget.SCRIPT
            if is_script and len(script_records) >= 1:
                continue
            if not is_script and len(text_records) >= 2:
                continue
            initial_score = INITIAL_SCORE_BY_SIGNAL.get(source, 0.6)
            record = EvolutionRecord.make(
                source=source,
                context=merged_context,
                change=patch,
                score=initial_score,
            )
            if is_script:
                script_records.append(record)
            else:
                text_records.append(record)
            logger.info(
                "[SkillExperienceOptimizer] generated record %s -> [%s] target=%s merge_target=%s",
                record.id,
                patch.section,
                patch.target.value,
                patch.merge_target,
            )
        return text_records + script_records

    async def retry_parse(self, broken_raw: str, original_prompt: str) -> List[EvolutionPatch]:
        """One-shot retry: fix JSON if malformed, or regenerate if truncated."""
        truncated = _looks_truncated(broken_raw)
        if truncated:
            logger.warning("[SkillExperienceOptimizer] output appears truncated, retrying full regeneration")
            retry_prompt = original_prompt
        else:
            logger.warning(
                "[SkillExperienceOptimizer] JSON malformed, requesting fix (preview: %s)",
                broken_raw[:200],
            )
            retry_prompt = _JSON_FIX_PROMPT.format(broken_output=broken_raw)

        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": retry_prompt}],
            )
            retry_raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("[SkillExperienceOptimizer] retry LLM call failed: %s", exc)
            return []

        patches = _parse_llm_response(retry_raw)
        if patches is None:
            strategy = "regeneration" if truncated else "fix"
            logger.warning("[SkillExperienceOptimizer] retry (%s) also failed, giving up", strategy)
            return []
        logger.info("[SkillExperienceOptimizer] retry succeeded, got %d patches", len(patches))
        return patches

    def update_llm(self, llm: Any, model: str) -> None:
        """Update runtime llm/model for hot reload."""
        self._llm = llm
        self._model = model

    async def generate_new_skill_proposal(
        self,
        messages: List[dict],
        existing_skill_names: List[str],
    ) -> Optional[Dict[str, Any]]:
        """Generate a new skill proposal based on conversation history.

        Args:
            messages: Conversation messages
            existing_skill_names: List of existing skill names

        Returns:
            Proposal dict with name, description, body, reason if should_create=True,
            None otherwise
        """
        conversation_snippet = _build_conversation_snippet(messages, language=self._language)
        prompt = NEW_SKILL_PROPOSAL_PROMPT[self._language].format(
            conversation_snippet=conversation_snippet,
            existing_skill_names=", ".join(existing_skill_names) if existing_skill_names else "(none)",
        )

        logger.info("[SkillExperienceOptimizer] generating new skill proposal")
        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("[SkillExperienceOptimizer] new skill proposal LLM call failed: %s", exc)
            return None

        data = _extract_json(raw)
        if data is None:
            logger.warning("[SkillExperienceOptimizer] failed to parse new skill proposal response")
            return None

        if not data.get("should_create"):
            logger.info("[SkillExperienceOptimizer] LLM decided not to create new skill")
            return None

        required_fields = ["name", "description", "body", "reason"]
        for field in required_fields:
            if field not in data:
                logger.warning(
                    "[SkillExperienceOptimizer] new skill proposal missing field: %s",
                    field,
                )
                return None

        logger.info(
            "[SkillExperienceOptimizer] generated new skill proposal: %s",
            data["name"],
        )
        return data
