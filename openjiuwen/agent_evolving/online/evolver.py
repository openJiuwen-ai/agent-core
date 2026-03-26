# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM-based experience generation for online skill evolution."""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional, Dict

from openjiuwen.agent_evolving.online.schema import (
    VALID_SECTIONS,
    EvolutionPatch,
    EvolutionRecord,
    EvolutionContext,
    EvolutionTarget,
)
from openjiuwen.core.common.logging import logger

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

## 经验来源

经验来自两个渠道，都要处理：

**渠道 A — 预检测信号**：上方「预检测信号」中列出的条目，由规则引擎自动从对话中提取，可能包含误报。

**渠道 B — 对话历史直接分析**：直接审视「对话历史」，发现规则引擎未捕获的有价值经验，包括但不限于：
- Agent 经过多次尝试/重试才成功的 workaround（说明 Skill 缺少相关指导）
- 用户含蓄的纠正或补充说明（未使用"错了""不对"等显式关键词）
- 低效的工具调用模式（如多余步骤、错误的调用顺序）
- Agent 遗漏的关键步骤（用户不得不手动补充）
- 需要特殊处理的边界情况（Skill 中未覆盖的场景）

如果对话历史中没有额外发现，不需要强制生成；如果有发现，与预检测信号的经验一起输出。

## 数量限制

最终输出的有效经验（action 为 append 的条目）**不得超过 2 条**。
如果候选经验超过 2 条，按以下优先级保留最重要的 2 条，其余标记为 skip：
1. 导致任务失败或产出错误结果的问题 > 导致效率低下但最终成功的问题
2. 高频/可复现的模式 > 单次偶发现象
3. 渠道 A（预检测信号）与渠道 B（对话分析）的发现同等对待，仅按影响程度排序

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

**target 判断（二选一）：**
- **description**（描述/元数据层）：涉及 Skill 适用场景判断错误、描述不准确、缺少关键词导致未被选中或误选
- **body**（正文/指令层）：涉及执行步骤、工具调用错误、操作流程、排查逻辑

**section 选择参考：**
- execution_failure / workaround 类：通常归入 Troubleshooting
- user_correction / 流程偏差类：通常归入 Instructions 或 Examples

## 内容生成规范
1. 语言一致：输出语言必须与 Skill 完全一致（中文 Skill 输出中文，英文 Skill 输出英文）
2. 标题层级：使用与 Skill 相同的标题层级（##、### 等）
3. 每条记录：1 个标题 + 2-3 个无序列表分点（- 或 *），禁止子层级
4. 每条记录只涉及一个 section 类型，不混合
5. 提取可复用的通用规则，非临时补丁（好："遇到 X 错误时，先检查 Y 再执行 Z"；差："某用户某次提到某问题"）
6. 内容必须是 Skill 中未提及的新知识，精炼简洁
7. 多个发现指向同一问题时合并为一条；不同问题分别生成
8. 有效经验（action 为 append）最多 2 条，宁缺毋滥——只保留对 Skill 改进影响最大的发现

## 输出格式
只输出以下 JSON 数组，不要其他内容（即使只有一条，也必须用数组包裹）：
[
  {{
    "action": "append | skip",
    "skip_reason": "irrelevant | duplicate | low_priority（仅 action 为 skip 时填写，否则为 null）",
    "target": "description | body",
    "section": "Instructions | Examples | Troubleshooting",
    "content": "Markdown 内容（仅 action 为 append 时填写）",
    "merge_target": "ev_xxxxxxxx 或 null"
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

## Experience Sources

Experiences come from two channels, both must be processed:

**Channel A — Pre-detected Signals**: The entries listed in the "Pre-detected Signals" section above, automatically extracted from the conversation by the rule engine. May contain false positives.

**Channel B — Direct Conversation History Analysis**: Directly examine the "Conversation History" to discover valuable experiences not captured by the rule engine, including but not limited to:
- Workarounds where the Agent succeeded only after multiple attempts/retries (indicating the Skill lacks relevant guidance)
- Implicit corrections or supplementary explanations from the user (without using explicit keywords like "wrong" or "incorrect")
- Inefficient tool invocation patterns (e.g., redundant steps, incorrect invocation order)
- Critical steps missed by the Agent (where the user had to manually fill in)
- Edge cases requiring special handling (scenarios not covered by the Skill)

If no additional findings exist in the conversation history, do not force generation; if findings exist, output them together with the pre-detected signal experiences.

## Quantity Limit

The final output of valid experiences (entries with action "append") **must not exceed 2**.
If candidate experiences exceed 2, retain the 2 most important ones by the following priority and mark the rest as skip:
1. Issues causing task failure or incorrect results > Issues causing inefficiency but eventual success
2. High-frequency / reproducible patterns > One-off occurrences
3. Findings from Channel A (pre-detected signals) and Channel B (conversation analysis) are treated equally, sorted only by impact level

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

**section selection reference:**
- execution_failure / workaround types: Usually belong to Troubleshooting
- user_correction / process deviation types: Usually belong to Instructions or Examples

## Content Generation Guidelines
1. Language consistency: Output language must match the Skill exactly (Chinese Skill outputs Chinese, English Skill outputs English)
2. Heading levels: Use the same heading levels as the Skill (##, ###, etc.)
3. Each record: 1 heading + 2-3 unordered list items (- or *), no sub-levels allowed
4. Each record covers only one section type, no mixing
5. Extract reusable general rules, not temporary patches (good: "When encountering error X, first check Y then execute Z"; bad: "A certain user once mentioned a certain issue")
6. Content must be new knowledge not already mentioned in the Skill, concise and refined
7. When multiple findings point to the same issue, merge into one entry; generate separately for different issues
8. Valid experiences (action "append") are limited to at most 2 — quality over quantity — only retain the findings with the greatest impact on Skill improvement

## Output Format
Output only the following JSON array, nothing else (even if there is only one entry, it must be wrapped in an array):
[
  {{
    "action": "append | skip",
    "skip_reason": "irrelevant | duplicate | low_priority (fill only when action is skip, otherwise null)",
    "target": "description | body",
    "section": "Instructions | Examples | Troubleshooting",
    "content": "Markdown content (fill only when action is append)",
    "merge_target": "ev_xxxxxxxx or null"
  }}
]"""

SKILL_EXPERIENCE_GENERATE_PROMPT: Dict[str, str] = {
    "cn": SKILL_EXPERIENCE_GENERATE_PROMPT_CN,
    "en": SKILL_EXPERIENCE_GENERATE_PROMPT_EN,
}


def build_conversation_snippet(
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
    for message in messages[-max_messages:]:
        role = message.get("role", "unknown")
        text = _extract_text(message).strip() or (f"({'无文本' if language == 'cn' else 'No text'})")
        _ = content_preview_chars
        tool_calls = message.get("tool_calls")
        if role == "assistant" and tool_calls:
            names = [
                tool_call.get("name", "")
                for tool_call in tool_calls
                if isinstance(tool_call, dict)
            ]
            prefix = f"[assistant] (tool_calls: {', '.join(names)})\n  "
        else:
            prefix = f"[{role}] "
        lines.append(prefix + text)
    return "\n".join(lines)


class SkillEvolver:
    """Pure logic layer: generate skill experience with LLM."""

    def __init__(self, llm: Any, model: str, language: str = "cn") -> None:
        self._llm = llm
        self._model = model
        self._language = language

    async def generate_skill_experience(
        self,
        ctx: EvolutionContext,
    ) -> List[EvolutionRecord]:
        """Generate and parse evolution records from LLM output."""
        if not ctx.signals:
            return []

        conversation_snippet = build_conversation_snippet(ctx.messages, language=self._language)
        signals_json = json.dumps(
            [signal.to_dict() for signal in ctx.signals],
            ensure_ascii=False,
            indent=2,
        )
        desc_summary = self._build_existing_summary(
            ctx.existing_desc_records,
            label="description",
        )
        body_summary = self._build_existing_summary(
            ctx.existing_body_records,
            label="body",
        )
        prompt = SKILL_EXPERIENCE_GENERATE_PROMPT[self._language].format(
            skill_content=ctx.skill_content,
            signals_json=signals_json,
            conversation_snippet=(conversation_snippet or "").strip(),
            existing_desc_summary=desc_summary or f"({'无已有记录' if self._language == 'cn' else 'No existing records'})",
            existing_body_summary=body_summary or f"({'无已有记录' if self._language == 'cn' else 'No existing records'})",
        )

        logger.info("[SkillEvolver] calling LLM (skill=%s)", ctx.skill_name)
        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("[SkillEvolver] LLM call failed: %s", exc)
            return []

        patches = self._parse_llm_response(raw)
        source = ctx.signals[0].signal_type
        merged_context = "; ".join(signal.excerpt for signal in ctx.signals)
        records: List[EvolutionRecord] = []

        for patch in patches:
            if patch.action == "skip":
                logger.info(
                    "[SkillEvolver] LLM decided to skip (reason=%s)",
                    patch.skip_reason or "unknown",
                )
                continue
            if not patch.content.strip():
                logger.info("[SkillEvolver] LLM returned empty content, skipping")
                continue
            record = EvolutionRecord.make(
                source=source,
                context=merged_context,
                change=patch,
            )
            records.append(record)
            logger.info(
                "[SkillEvolver] generated record %s -> [%s] target=%s merge_target=%s",
                record.id,
                patch.section,
                patch.target.value,
                patch.merge_target,
            )
        return records[:2]

    def update_llm(self, llm: Any, model: str) -> None:
        """Update runtime llm/model for hot reload."""
        self._llm = llm
        self._model = model

    @staticmethod
    def _build_existing_summary(
        records: List[EvolutionRecord],
        label: str = "",
    ) -> str:
        if not records:
            return ""
        lines: List[str] = []
        for record in records:
            prefix = f"[{label}] " if label else ""
            lines.append(
                f"- {prefix}[{record.id}] [{record.change.section}] {record.change.content}"
            )
        return "\n".join(lines)

    @staticmethod
    def _parse_llm_response(raw: str) -> List[EvolutionPatch]:
        """Parse response JSON array/object into EvolutionPatch list."""
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
        raw = raw.strip()

        data = None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            matched = re.search(r"\[.*\]", raw, re.DOTALL)
            if matched:
                try:
                    data = json.loads(matched.group(0))
                except json.JSONDecodeError:
                    pass
            if data is None:
                matched = re.search(r"\{.*\}", raw, re.DOTALL)
                if matched:
                    try:
                        data = json.loads(matched.group(0))
                    except json.JSONDecodeError:
                        pass
            if data is None:
                logger.warning(
                    "[SkillEvolver] cannot parse LLM response as JSON: %s",
                    raw[:200],
                )
                return []

        items = data if isinstance(data, list) else [data]
        patches: List[EvolutionPatch] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            patch = SkillEvolver._parse_single_patch(item)
            if patch is not None:
                patches.append(patch)
        return patches

    @staticmethod
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
        )
