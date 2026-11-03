# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Task tool metadata for tool registration.

This module provides ONLY the tool registration metadata:
- Tool name
- Tool description template (with {available_agents} placeholder)
- Tool input parameters schema

For other system prompt sections, see ``sections/task_tool.py`` (non-tool prompts).
"""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import (
    ToolMetadataProvider,
)

# ---------------------------------------------------------------------------
# General-purpose subagent description (bilingual) - used in available_agents list
# ---------------------------------------------------------------------------
GENERAL_PURPOSE_AGENT_DESC: Dict[str, str] = {
    "cn": "通用型子代理，继承主代理的工具与能力（文件读写、bash、MCP、skills 等），"
          "适合执行独立的复杂子任务（调研、搜代码、多步实现等）。"
          "子代理运行在独立上下文窗口中，中间工具调用结果不会污染主代理上下文。"
          "当你在搜索关键词或文件时，如果不确定前几次尝试就能找到正确匹配，"
          "就用这个子代理来帮你搜索。",
    "en": "General-purpose subagent that inherits the parent agent's tools and "
          "capabilities (file I/O, bash, MCP, skills, etc.) for independent complex "
          "subtasks such as research, code search, and multi-step implementation. "
          "Runs in an isolated context window so intermediate tool results do not "
          "pollute the parent agent's context. When you are searching for a keyword "
          "or file and are not confident you will find the right match in the first "
          "few tries, use this subagent to perform the search for you.",
}

# ---------------------------------------------------------------------------
# Tool description (bilingual) - for tool registration ONLY
# ---------------------------------------------------------------------------
TASK_TOOL_DESCRIPTION_EN = """\
Launch a new subagent to handle complex, multi-step tasks autonomously.

The task_tool launches specialized subagents that autonomously handle complex tasks. Each subagent type has
specific capabilities and tools available to it.

## Task group coordination
- Dispatch work as its prerequisites become available. Batch independent tasks ready now; do not wait for
unrelated tasks or to fill a batch. By default, keep at most five unfinished tasks across all batches in this
parent turn; refill as tasks finish, unless the user requests more. Do not split work just to fill slots.
Serialize tasks that conflict over shared resources.
- Put each task's subagent_type/task_description inside tasks. Include needed prior findings in follow-ups,
and pass still-needed pending_ids in task_ids to wait for either existing or new tasks.
- Process ready results before choosing the next action. Use wait only when no new task or other useful work
can advance. Make one dispatch or wait call per step.
- submitted identifies new tasks, ready contains results, and pending_ids identifies unfinished tasks. Preserve
actual statuses, errors and partial work. timed_out ends only this wait; tasks keep running.
- Query existing tasks with wait, rather than redispatch; cancel tasks no longer needed. Collect needed results
or cancel outstanding tasks before final delivery.
- New dispatches create independent tasks; approval recovery and same-call retries retain the original tasks.

Available subagent types and the tools they have access to:

{available_agents}

For each new task, specify a subagent_type parameter to select which subagent type to use. The value must
exactly match one of the names listed above under "Available subagent types".
When subagent_type is "browser_agent", you must also provide browser_capabilities as a list of additional
capability categories selected from the browser capability catalog above. Use an empty list for a core-only
browser task. Do not provide browser_capabilities for other subagent types.

When NOT to use the task_tool:

- If you want to read a specific file path, use read_file or glob instead of the task_tool, to find the match
more quickly
- If you are searching for a specific class definition like "class Foo", use grep or glob instead, to find the
match more quickly
- If you are searching for code within a specific file or set of 2-3 files, use read_file instead of the
task_tool, to find the match more quickly
- Other tasks that are not related to the subagent descriptions above

Usage notes:

- Provide a task_description with the goal, relevant context, scope, read/write permissions, completion
criteria, and expected output — the subagent starts with no memory of this conversation.
- When the subagent is done, it returns a result for you to review and synthesize. Provide the user with a
complete final response; do not merely announce that the subagent finished or refer the user to its message.
- Each newly dispatched task starts with an isolated context — provide a complete task description.
- Verify key conclusions, produced files, and completion status as needed without repeating the entire
delegated investigation. Preserve reported errors and unfinished work; do not present partial results as
complete.
- Clearly tell the subagent whether you expect it to write code or just to do research (search, file reads,
web fetches, etc.), since it is not aware of the user's intent.
- If the subagent description mentions that it should be used proactively, then you should try your best to
use it without the user having to ask for it first. Use your judgement.

## Writing the prompt

Brief the subagent like a smart colleague who just walked into the room — it hasn't seen this conversation,
doesn't know what you've tried, doesn't understand why this task matters.
- Explain what you're trying to accomplish and why.
- Describe what you've already learned or ruled out.
- Give enough context about the surrounding problem that the subagent can make judgment calls rather than just
following a narrow instruction.
- If you need a short response, say so, but retain necessary evidence, errors, and unfinished work in the
handoff.
- For blocked tasks, request completed work, the specific error, remaining work, and needed input; do not
request identical retries without new information or a meaningful change of approach.
- Lookups: hand over the exact command. Investigations: hand over the question — prescribed steps become dead
weight when the premise is wrong.

Terse command-style prompts produce shallow, generic work.

**Never delegate understanding.** Don't write "based on your findings, fix the bug" or "based on the research,
implement it." Those phrases push synthesis onto the subagent instead of doing it yourself. Write prompts that
prove you understood: include file paths, line numbers, what specifically to change."""

TASK_TOOL_DESCRIPTION_CN = """\
启动新的子代理，自主处理复杂、多步骤任务。

task_tool 启动专门的子代理来自主处理复杂任务。每种子代理类型都有特定的能力和可用工具。

## 任务组协作
- 按依赖是否就绪推进：将当前可开始的独立任务合入一次 dispatch；后续任务只需等待自身依赖，不为无关任务或凑批次而等待。
同一主代理本轮跨批次默认保持不超过五个未完成任务，完成一个即可补派一个；用户明确要求更多时可放宽，不为凑数拆任务。共享资源冲突时串行。
- 每个任务的 subagent_type/task_description 放在 tasks 内；后续任务携带必要的上次结论，并将仍需结果的 pending_ids 放入 task_ids，联合等待新旧任务。
- 每次返回先处理 ready，再决定下一步；没有新任务或其他工作可推进时才用 wait。每步只调用一次 dispatch 或 wait。
- submitted 是本次新任务，ready 是就绪结果，pending_ids 是未完成任务。保留真实状态、错误和部分成果；timed_out 仅结束本次等待，任务仍继续。
- 查询已有任务使用 wait，不重复派发；不再需要的任务使用 cancel。最终交付前收齐所需结果或取消剩余任务。
- 每次新派发创建独立任务；审批恢复和相同调用重试继续原任务。

可用代理类型及其工具：

{available_agents}

每个新任务必须通过 subagent_type 参数选择子代理类型，且其值必须与上方「可用代理类型」列表中的名称完全一致。
当 subagent_type 为 "browser_agent" 时，还必须指定 browser_capabilities，并从上方浏览器能力目录中选择额外能力类别。仅使用核心浏览器能力时传入空列表；其他子代理类型不要提供 browser_capabilities。

何时不使用 task_tool：

- 如果你想读取某个具体文件路径，直接用 read_file 或 glob，比用 task_tool 更快
- 如果你在查找某个具体的类定义（如 "class Foo"），直接用 grep 或 glob，比用 task_tool 更快
- 如果你在 2-3 个特定文件内搜索代码，直接用 read_file，比用 task_tool 更快
- 其他与上述代理描述无关的任务

使用注意事项：

- task_description 应包含目标、必要上下文、范围、读写权限、完成标准和输出要求——子代理没有本次对话的任何记忆。
- 子代理完成后会返回结果供你验收和汇总。你应向用户提供完整的最终答复，不能只宣布子代理已完成或让用户自行查看它的消息。
- 每个新派发的子任务使用独立上下文——请提供完整的任务描述。
- 按需核验关键结论、产出文件和完成情况，避免重新执行整份委派调查。保留报告的错误和未完成部分，不把部分成果描述为全部完成。
- 明确告知子代理你期望它写代码还是仅做调研（搜索、读文件、抓取网页等），因为它不知道用户的意图。
- 如果子代理的描述中提到应主动使用它，你应尽量在用户没有明确要求时就使用它。自行判断。

## 如何写好任务描述

像给一位刚走进房间的聪明同事做简报一样描述任务——子代理没看过本次对话，不知道你尝试过什么，也不理解这个任务为什么重要。
- 说明你想达成什么目标以及为什么。
- 描述你已经了解到的信息或已排除的可能性。
- 提供足够的问题背景，让子代理能够自主判断，而不是只能机械执行狭窄的指令。
- 如果需要简短回复，请明确说明，但交接中仍须保留必要依据、错误和未完成部分。
- 对受阻任务，要求返回已完成内容、具体错误、剩余工作和所需帮助；没有新信息或实质方法变化时，不要求重复相同的失败操作。
- 查找类任务：直接给出精确命令。调研类任务：给出要回答的问题——规定好的步骤在前提不成立时只会成为累赘。

简短的命令式描述会产生浅层、泛泛的结果。

**永远不要委托理解。** 不要写"根据你的发现修复 bug"或"根据调研结果实现它"。这类说法把综合分析推给了子代理，而不是你自己完成。写出的描述应能证明你已经理解了问题：包含文件路径、行号、具体需要修改的内容。"""

DESCRIPTION: Dict[str, str] = {
    "cn": TASK_TOOL_DESCRIPTION_CN,
    "en": TASK_TOOL_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Tool parameters (bilingual)
# ---------------------------------------------------------------------------
TASK_TOOL_PARAMS: Dict[str, Dict[str, str]] = {
    "subagent_type": {
        "cn": "子代理类型（必填，须与上方「可用代理类型」列表中的名称完全一致；"
              "不要使用列表中未出现的名称）",
        "en": "Required subagent type; must exactly match a name from the "
              "\"Available subagent types\" list above. Do not use any name "
              "that is not listed there.",
    },
    "task_description": {
        "cn": "任务描述（必填，含目标、必要上下文、范围与读写权限、完成标准和输出要求）",
        "en": "Required task goal, relevant context, scope and read/write permissions, completion criteria, and output",
    },
    "browser_capabilities": {
        "cn": "浏览器子代理所需的额外能力类别列表；仅使用核心能力时传入空列表",
        "en": "Additional capability categories required by browser_agent; use an empty list for core-only tasks",
    },
}


def get_task_tool_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the task-group protocol used by task_tool."""
    cn = language != "en"
    item_properties = {
        "subagent_type": {"type": "string", "description": TASK_TOOL_PARAMS["subagent_type"]["cn" if cn else "en"]},
        "task_description": {
            "type": "string", "description": TASK_TOOL_PARAMS["task_description"]["cn" if cn else "en"],
        },
        "browser_capabilities": {
            "type": "array", "items": {"type": "string"},
            "description": TASK_TOOL_PARAMS["browser_capabilities"]["cn" if cn else "en"],
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string", "enum": ["dispatch", "wait", "cancel"],
                "description": (
                    "dispatch 派发新任务并等待任一结果；wait 等待已有任务；cancel 取消指定任务。"
                    if cn else "dispatch starts tasks and waits for any result; "
                    "wait waits for existing tasks; cancel cancels tasks."
                ),
            },
            "tasks": {
                "type": "array", "minItems": 1, "maxItems": 128,
                "description": ("dispatch 的新任务数组（单个也用数组）；wait/cancel 不传。"
                                if cn else "New tasks for dispatch (use an array even for one); omit for wait/cancel."),
                "items": {"type": "object", "properties": item_properties,
                          "required": ["subagent_type", "task_description"]},
            },
            "task_ids": {
                "type": "array", "maxItems": 128, "items": {"type": "string"},
                "description": (
                    "已有任务 ID：首次派发可省略；追加时带上仍需结果的 pending_ids，以等待新旧任务任一结果；wait/cancel 必填。"
                    if cn else "Existing task IDs: omit on first dispatch; when adding tasks, "
                    "include still-needed pending_ids to wait for old or new results. Required for wait/cancel."
                ),
            },
            "timeout_s": {
                "type": "number", "minimum": 0,
                "description": (
                    "本次等待的最长秒数；0 立即查询，不传使用默认上限。等待到期不会停止子任务。"
                    if cn else "Wait limit in seconds; 0 queries immediately, omit for default. "
                    "Expiry does not stop tasks."
                ),
            },
        },
        "required": ["action"],
    }


class TaskMetadataProvider(ToolMetadataProvider):
    """Task tool metadata provider for tool registration.

    Provides tool name, description template, and parameter schema.
    Does NOT provide system prompt content.
    """

    def get_name(self) -> str:
        """Return tool name."""
        return "task_tool"

    def get_description(self, language: str = "cn") -> str:
        """Return tool description template with {available_agents} placeholder.

        Args:
            language: 'cn' or 'en'.

        Returns:
            Tool description template string.
        """
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        """Return JSON Schema for tool input parameters.

        Args:
            language: 'cn' or 'en'.

        Returns:
            JSON Schema dict for tool parameters.
        """
        return get_task_tool_input_params(language)
