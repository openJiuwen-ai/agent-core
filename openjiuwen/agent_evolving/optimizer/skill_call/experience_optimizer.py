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


def _assistant_text_from_response(response: Any) -> str:
    """Normalize assistant output; fall back to reasoning_content when content is empty."""
    content = getattr(response, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        text = "".join(parts).strip()
    elif content is None:
        text = ""
    else:
        text = str(content).strip()

    if text:
        return text

    reasoning = getattr(response, "reasoning_content", None)
    if reasoning and str(reasoning).strip():
        logger.warning(
            "[SkillExperienceOptimizer] falling back to reasoning_content "
            "(empty content; reasoning_content is not the expected JSON output channel)",
        )
        return str(reasoning).strip()
    return ""

# Initial score mapping by signal type
INITIAL_SCORE_BY_SIGNAL = {
    "execution_failure": 0.65,
    "user_correction": 0.70,
    "script_artifact": 0.60,
    "conversation_review": 0.50,
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

# --- Two-stage pipeline: Analyzer (root-cause + candidates) → Formatter (strict JSON) ---

_ANALYZER_SHARED_INPUT_CN = """\
## 输入信息

### 当前 Skill 内容
{skill_content}

### 预检测信号（规则引擎自动提取）
{signals_json}

### 结构化执行轨迹（优先于对话历史分析）
{tool_call_chain}

### 对话历史（补充上下文）
{conversation_snippet}

### 已有 description 经验
{existing_desc_summary}

### 已有 body 经验
{existing_body_summary}"""

_ANALYZER_SHARED_INPUT_EN = """\
## Input Information

### Current Skill Content
{skill_content}

### Pre-detected Signals (rule engine)
{signals_json}

### Structured Execution Trace (prioritize over conversation history)
{tool_call_chain}

### Conversation History (supplementary context)
{conversation_snippet}

### Existing description experiences
{existing_desc_summary}

### Existing body experiences
{existing_body_summary}"""

SKILL_EXPERIENCE_ANALYZER_PROMPT_CN = """\
你是一个 Skill 优化分析专家。根据信号、结构化执行轨迹和对话历史，完成根因归因并产出候选演进经验（自然语言草稿）。

""" + _ANALYZER_SHARED_INPUT_CN + """

## 经验来源

**渠道 A — 预检测信号**：可能包含误报，需经 Step 0 过滤。
**渠道 B — 执行轨迹与对话分析**：重试后成功（workaround）、用户含蓄纠正、低效工具调用模式、遗漏步骤、边界情况。
**渠道 C — script_artifact 信号**：可复用脚本工件（target=script）。

## Step 0：根因归因（对每条信号或发现必须先执行）

判断 failure_type（四选一）：
- **skill_instruction_gap**：Skill 指令/示例/排查缺失导致 → should_evolve=true（confidence≥0.7 时）
- **external_env**：网络/环境/权限/第三方服务 → should_evolve=false
- **user_ambiguity**：用户表述不清，非 Skill 缺陷 → 仅当能补充 Examples 时 should_evolve=true
- **tool_limitation**：工具能力不足 → should_evolve=false

仅 should_evolve=true 且与 Skill 相关的发现可进入 candidates。

## 决策流程（对每条候选）

1. **相关性**：外部因素 → 不进入 candidates
2. **去重**：与已有经验实质相同 → 不进入；有增量 → 标记 merge_target
3. **优先级**：失败/错误 > 效率低；可复现 > 偶发

## 数量限制

candidates 中 action=append 的条目：**文本最多 2 条，脚本最多 1 条**，独立计数。

## 内容规范

- 语言与 Skill 一致；1 标题 + 2-3 列表项；可复用通用规则；单条 content 草稿 ≤500 字符

## 输出格式

只输出以下 JSON 对象，不要其他内容：
```json
{{
  "root_causes": [
    {{
      "failure_type": "skill_instruction_gap | external_env | user_ambiguity | tool_limitation",
      "confidence": 0.85,
      "evidence": ["简要证据"],
      "should_evolve": true
    }}
  ],
  "candidates": [
    {{
      "action": "append",
      "target": "description | body | script",
      "section": "Instructions | Examples | Troubleshooting | Scripts",
      "content": "Markdown 或脚本源码草稿",
      "merge_target": "ev_xxxxxxxx 或 null",
      "priority": 1,
      "script_filename": "仅 script 时填写",
      "script_language": "仅 script 时填写",
      "script_purpose": "仅 script 时填写"
    }}
  ]
}}
```

若无值得记录的经验，返回 `"candidates": []`。"""

SKILL_EXPERIENCE_ANALYZER_PROMPT_EN = """\
You are a Skill optimization analyst. Based on signals, structured execution trace, and conversation history, perform root-cause attribution and produce candidate evolution experiences (drafts).

""" + _ANALYZER_SHARED_INPUT_EN + """

## Experience Sources

**Channel A — Pre-detected signals**: May contain false positives; must pass Step 0 filtering.
**Channel B — Trace & conversation analysis**: Workarounds after retries, implicit user corrections, inefficient tool patterns, missed steps, edge cases.
**Channel C — script_artifact signals**: Reusable script artifacts (target=script).

## Step 0: Root-Cause Attribution (required for each signal or finding)

Determine failure_type (choose one):
- **skill_instruction_gap**: Caused by missing Skill instructions/examples/troubleshooting → should_evolve=true (when confidence≥0.7)
- **external_env**: Network/environment/permissions/third-party → should_evolve=false
- **user_ambiguity**: Unclear user intent, not a Skill defect → should_evolve=true only if Examples can help
- **tool_limitation**: Tool capability limits → should_evolve=false

Only findings with should_evolve=true and Skill relevance enter candidates.

## Decision Flow (per candidate)

1. **Relevance**: External factors → exclude from candidates
2. **Deduplication**: Essentially duplicate → exclude; incremental → set merge_target
3. **Priority**: Failure/errors > inefficiency; reproducible > one-off

## Quantity Limit

action=append entries in candidates: **at most 2 text, 1 script**, counted independently.

## Content Guidelines

- Match Skill language; 1 heading + 2-3 list items; reusable rules; draft content ≤500 chars per entry

## Output Format

Output only the following JSON object, nothing else:
```json
{{
  "root_causes": [
    {{
      "failure_type": "skill_instruction_gap | external_env | user_ambiguity | tool_limitation",
      "confidence": 0.85,
      "evidence": ["brief evidence"],
      "should_evolve": true
    }}
  ],
  "candidates": [
    {{
      "action": "append",
      "target": "description | body | script",
      "section": "Instructions | Examples | Troubleshooting | Scripts",
      "content": "Markdown or script source draft",
      "merge_target": "ev_xxxxxxxx or null",
      "priority": 1,
      "script_filename": "script only",
      "script_language": "script only",
      "script_purpose": "script only"
    }}
  ]
}}
```

If nothing is worth recording, return `"candidates": []`."""

SKILL_EXPERIENCE_ANALYZER_PROMPT: Dict[str, str] = {
    "cn": SKILL_EXPERIENCE_ANALYZER_PROMPT_CN,
    "en": SKILL_EXPERIENCE_ANALYZER_PROMPT_EN,
}

SKILL_EXPERIENCE_FORMATTER_PROMPT_CN = """\
你是 JSON 格式化专家。将下方分析阶段产出的候选经验转换为严格的演进记录 JSON 数组。

## 分析阶段输出
{analyzer_output}

## 目标格式

只输出以下 JSON 数组，不要其他内容（即使只有一条也必须用数组包裹）：
[
  {{
    "action": "append | skip",
    "skip_reason": "irrelevant | duplicate | low_priority（仅 skip 时填写，否则为 null）",
    "target": "description | body | script",
    "section": "Instructions | Examples | Troubleshooting | Scripts",
    "content": "Markdown 内容或脚本源码（仅 append 时填写）",
    "merge_target": "ev_xxxxxxxx 或 null",
    "script_filename": "文件名或 null",
    "script_language": "语言标识或 null",
    "script_purpose": "用途说明或 null"
  }}
]

规则：
1. 保留分析阶段所有 action=append 的候选（文本≤2，脚本≤1）
2. content 中的换行用 \\n，引号正确转义
3. merge_target 为 null 时写 null，不要写字符串 "null\""""

SKILL_EXPERIENCE_FORMATTER_PROMPT_EN = """\
You are a JSON formatting expert. Convert the analyzer-stage candidate experiences below into a strict evolution record JSON array.

## Analyzer Output
{analyzer_output}

## Target Format

Output only the following JSON array, nothing else (wrap in an array even for a single entry):
[
  {{
    "action": "append | skip",
    "skip_reason": "irrelevant | duplicate | low_priority (only when action is skip, else null)",
    "target": "description | body | script",
    "section": "Instructions | Examples | Troubleshooting | Scripts",
    "content": "Markdown or script source (only when action is append)",
    "merge_target": "ev_xxxxxxxx or null",
    "script_filename": "filename or null",
    "script_language": "language id or null",
    "script_purpose": "purpose or null"
  }}
]

Rules:
1. Keep all action=append candidates from the analyzer (text≤2, script≤1)
2. Escape newlines as \\n and quotes correctly in content
3. Use null for merge_target when absent, not the string "null\""""

SKILL_EXPERIENCE_FORMATTER_PROMPT: Dict[str, str] = {
    "cn": SKILL_EXPERIENCE_FORMATTER_PROMPT_CN,
    "en": SKILL_EXPERIENCE_FORMATTER_PROMPT_EN,
}

_TOOL_CHAIN_FAILURE_RE = re.compile(
    r"error|exception|traceback|failed|failure|timeout|timed out"
    r"|errno|connectionerror|oserror|valueerror|typeerror"
    r"|错误|异常|失败|超时",
    re.IGNORECASE,
)
_TOOL_CHAIN_CORRECTION_RE = re.compile(
    r"不对|错了|应该|你搞错|纠正|我的意思是|that's wrong|should be|actually",
    re.IGNORECASE,
)
_TOOL_CHAIN_ARGS_MAX_CHARS = 120
_TOOL_CHAIN_RESULT_MAX_CHARS = 100
_ANALYZER_LOG_MAX_CHARS = 500
# Reasoning models (e.g. GLM-5.1) may spend the entire default output budget on
# reasoning_content before emitting JSON in content; raise the cap for optimizer calls.
_OPTIMIZER_LLM_MAX_TOKENS = 8192


def _extract_message_text(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def _summarize_tool_result(content: str, language: str = "cn") -> tuple[str, str]:
    """Return (status_label, summary) for a tool result line."""
    text = content.strip()
    if not text:
        empty = "(空)" if language == "cn" else "(empty)"
        status = "空" if language == "cn" else "EMPTY"
        return status, empty
    if _TOOL_CHAIN_FAILURE_RE.search(text):
        status = "FAIL" if language == "en" else "失败"
    else:
        status = "OK"
    one_line = " ".join(text.split())
    if len(one_line) > _TOOL_CHAIN_RESULT_MAX_CHARS:
        one_line = one_line[:_TOOL_CHAIN_RESULT_MAX_CHARS] + "..."
    return status, one_line


def build_tool_call_chain(
    messages: List[dict],
    language: str = "cn",
    max_events: int = 40,
) -> str:
    """Build a structured tool-call chain from conversation messages."""
    if not messages:
        return "(无执行轨迹)" if language == "cn" else "(No execution trace)"

    lines: list[str] = []
    turn = 0
    for message in messages:
        role = message.get("role", "")
        if role == "assistant" and message.get("tool_calls"):
            for tool_call in message.get("tool_calls", []):
                if not isinstance(tool_call, dict):
                    continue
                turn += 1
                if turn > max_events:
                    break
                name = tool_call.get("name", "unknown")
                args = tool_call.get("arguments", "")
                if isinstance(args, dict):
                    args_str = json.dumps(args, ensure_ascii=False)
                else:
                    args_str = str(args)
                if len(args_str) > _TOOL_CHAIN_ARGS_MAX_CHARS:
                    args_str = args_str[:_TOOL_CHAIN_ARGS_MAX_CHARS] + "..."
                lines.append(f"[Turn {turn}] assistant → {name}({args_str})")
        elif role in ("tool", "function"):
            turn += 1
            if turn > max_events:
                break
            tool_name = message.get("name") or message.get("tool_name") or "tool"
            status, summary = _summarize_tool_result(
                _extract_message_text(message), language=language,
            )
            lines.append(f"[Turn {turn}] {tool_name} → {status}: {summary}")
        elif role == "user":
            text = _extract_message_text(message).strip()
            if text and _TOOL_CHAIN_CORRECTION_RE.search(text):
                turn += 1
                if turn > max_events:
                    break
                preview = text[:150] + ("..." if len(text) > 150 else "")
                tag = "用户纠正" if language == "cn" else "user_correction"
                lines.append(f"[Turn {turn}] user ({tag}): {preview}")
        if turn >= max_events:
            break

    if not lines:
        return (
            "(无工具调用轨迹；参见对话历史)"
            if language == "cn"
            else "(No tool calls; see conversation history)"
        )
    return "\n".join(lines)


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
    text = re.sub(r"(?<!:)//[^\n]*", "", text)
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
        lines.append(
            f"- {prefix}[{record.id}] [{record.change.section}] {record.change.content}"
        )
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


def _parse_analyzer_response(raw: str) -> Optional[dict]:
    """Parse analyzer-stage JSON object. Returns None on failure."""
    extracted = _extract_json(raw)
    data = extracted
    if not isinstance(data, dict):
        return None
    if "candidates" not in data:
        data["candidates"] = []
    if "root_causes" not in data:
        data["root_causes"] = []
    return data


def _filter_analyzer_candidates(candidates: list) -> list:
    """Keep only append candidates with non-empty content."""
    result: list = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        if item.get("action", "append") != "append":
            continue
        if not str(item.get("content", "")).strip():
            continue
        result.append(item)
    return result


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

    def __init__(
        self,
        llm: Any,
        model: str,
        language: str = "cn",
        *,
        two_stage: bool = True,
    ) -> None:
        super().__init__()
        self._llm = llm
        self._model = model
        self._language = language
        self._two_stage = two_stage

    @property
    def llm(self) -> Any:
        """Get the configured LLM client."""
        return self._llm

    @property
    def model(self) -> str:
        """Get the configured model name."""
        return self._model

    @staticmethod
    def default_targets() -> List[str]:
        return ["experiences"]

    async def _backward(self, signals: List[EvolutionSignal]) -> None:
        """Generate experience records for each bound SkillCallOperator."""
        for op_id, op in self._operators.items():
            skill_name = op_id.removeprefix("skill_call_")
            skill_signals = [
                s for s in self._bad_signals
                if s.skill_name == skill_name or not s.skill_name
            ]
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
                tool_call_chain=state.get("tool_call_chain", ""),
            )
            records = await self.generate_records(ctx)
            if not records:
                logger.info(
                    "[SkillExperienceOptimizer] no records generated for skill=%s", skill_name
                )
                continue
            existing: List = self._parameters[op_id].get_gradient("experiences") or []
            self._parameters[op_id].set_gradient("experiences", existing + records)
            logger.info(
                "[SkillExperienceOptimizer] generated %d record(s) for skill=%s",
                len(records), skill_name,
            )

    def _step(self) -> Updates:
        updates: Updates = {}
        for op_id, param in self._parameters.items():
            records: List = param.get_gradient("experiences") or []
            if records:
                updates[(op_id, "experiences")] = records
        return updates

    def _build_generation_inputs(self, ctx: EvolutionContext) -> dict:
        """Shared prompt inputs for analyzer / single-stage paths."""
        tool_call_chain = ctx.tool_call_chain or build_tool_call_chain(
            ctx.messages, language=self._language,
        )
        conversation_snippet = _build_conversation_snippet(
            ctx.messages, language=self._language,
        )
        signals_json = json.dumps(
            [signal.to_dict() for signal in ctx.signals],
            ensure_ascii=False,
            indent=2,
        )
        desc_summary = _build_existing_summary(
            ctx.existing_desc_records, label="description",
        )
        body_summary = _build_existing_summary(
            ctx.existing_body_records, label="body",
        )
        no_records = "无已有记录" if self._language == "cn" else "No existing records"
        return {
            "skill_content": _summarize_skill_content(ctx.skill_content),
            "signals_json": signals_json,
            "tool_call_chain": tool_call_chain.strip() or (
                "(无)" if self._language == "cn" else "(none)"
            ),
            "conversation_snippet": (conversation_snippet or "").strip(),
            "existing_desc_summary": desc_summary or f"({no_records})",
            "existing_body_summary": body_summary or f"({no_records})",
        }

    async def _invoke_llm(self, prompt: str) -> Optional[str]:
        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=_OPTIMIZER_LLM_MAX_TOKENS,
            )
            return _assistant_text_from_response(response)
        except Exception as exc:
            logger.error("[SkillExperienceOptimizer] LLM call failed: %s", exc)
            return None

    async def _run_analyzer(
        self, ctx: EvolutionContext, inputs: dict,
    ) -> Optional[dict]:
        prompt = SKILL_EXPERIENCE_ANALYZER_PROMPT[self._language].format(**inputs)
        logger.info(
            "[SkillExperienceOptimizer] analyzer stage (skill=%s)", ctx.skill_name,
        )
        raw = await self._invoke_llm(prompt)
        if raw is None:
            return None
        data = _parse_analyzer_response(raw)
        if data is None:
            logger.warning(
                "[SkillExperienceOptimizer] analyzer parse failed (preview: %s)",
                raw[:200],
            )
            retry_raw = await self._invoke_llm(prompt)
            if retry_raw:
                data = _parse_analyzer_response(retry_raw)
        if data is None:
            return None
        data["candidates"] = _filter_analyzer_candidates(data.get("candidates", []))
        analyzer_preview = json.dumps(data, ensure_ascii=False)
        if len(analyzer_preview) > _ANALYZER_LOG_MAX_CHARS:
            analyzer_preview = analyzer_preview[:_ANALYZER_LOG_MAX_CHARS] + "..."
        logger.info(
            "[SkillExperienceOptimizer] analyzer data (skill=%s): %s",
            ctx.skill_name,
            analyzer_preview,
        )
        if not data["candidates"]:
            causes = data.get("root_causes", [])
            logger.info(
                "[SkillExperienceOptimizer] analyzer produced 0 candidates "
                "(root_causes=%d, skill=%s)",
                len(causes),
                ctx.skill_name,
            )
        return data

    async def _run_formatter(
        self, analyzer_data: dict, skill_name: str,
    ) -> Optional[List[EvolutionPatch]]:
        analyzer_output = json.dumps(analyzer_data, ensure_ascii=False, indent=2)
        prompt = SKILL_EXPERIENCE_FORMATTER_PROMPT[self._language].format(
            analyzer_output=analyzer_output,
        )
        logger.info(
            "[SkillExperienceOptimizer] formatter stage (skill=%s)", skill_name,
        )
        raw = await self._invoke_llm(prompt)
        if raw is None:
            return None
        patches = _parse_llm_response(raw)
        if patches is None:
            patches = await self.retry_parse(raw, original_prompt=prompt)
        return patches

    async def generate_records(self, ctx: EvolutionContext) -> List[EvolutionRecord]:
        """Generate and parse evolution records from LLM output."""
        if not ctx.signals:
            return []

        inputs = self._build_generation_inputs(ctx)
        patches: Optional[List[EvolutionPatch]] = None

        if self._two_stage:
            logger.info(
                "[SkillExperienceOptimizer] two-stage pipeline (skill=%s, signals=%d)",
                ctx.skill_name,
                len(ctx.signals),
            )
            analyzer_data = await self._run_analyzer(ctx, inputs)
            if not analyzer_data or not analyzer_data.get("candidates"):
                root_cause_count = len((analyzer_data or {}).get("root_causes", []))
                logger.info(
                    "[SkillExperienceOptimizer] two-stage early exit (skill=%s): "
                    "no candidates (root_causes=%d)",
                    ctx.skill_name,
                    root_cause_count,
                )
                return []
            candidates = analyzer_data.get("candidates", [])
            logger.info(
                "[SkillExperienceOptimizer] analyzer done (skill=%s): "
                "candidates=%d, entering formatter",
                ctx.skill_name,
                len(candidates),
            )
            patches = await self._run_formatter(analyzer_data, ctx.skill_name)
        else:
            prompt = SKILL_EXPERIENCE_GENERATE_PROMPT[self._language].format(
                skill_content=inputs["skill_content"],
                signals_json=inputs["signals_json"],
                tool_call_chain=inputs["tool_call_chain"],
                conversation_snippet=inputs["conversation_snippet"],
                existing_desc_summary=inputs["existing_desc_summary"],
                existing_body_summary=inputs["existing_body_summary"],
            )
            logger.info(
                "[SkillExperienceOptimizer] single-stage LLM (skill=%s)", ctx.skill_name,
            )
            raw = await self._invoke_llm(prompt)
            if raw is None:
                return []
            patches = _parse_llm_response(raw)
            if patches is None:
                patches = await self.retry_parse(raw, original_prompt=prompt)

        if not patches:
            return []

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
            logger.warning(
                "[SkillExperienceOptimizer] output appears truncated, retrying full regeneration"
            )
            retry_prompt = original_prompt
        else:
            logger.warning(
                "[SkillExperienceOptimizer] JSON malformed, requesting fix (preview: %s)",
                broken_raw[:200],
            )
            retry_prompt = _JSON_FIX_PROMPT.format(broken_output=broken_raw)

        retry_raw = await self._invoke_llm(retry_prompt)
        if retry_raw is None:
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
