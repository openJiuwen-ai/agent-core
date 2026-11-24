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
          "在独立上下文中执行被委派的子任务；中间工具调用结果不进入主代理上下文。",
    "en": "General-purpose subagent that inherits the parent agent's tools and "
          "capabilities (file I/O, bash, MCP, skills, etc.) and runs delegated "
          "subtasks in an isolated context; intermediate tool results do not "
          "enter the parent agent's context.",
}

# ---------------------------------------------------------------------------
# Tool description (bilingual) - for tool registration ONLY
# ---------------------------------------------------------------------------
TASK_TOOL_DESCRIPTION_EN = (
    "Synchronously run a fresh subagent. Give full objective/context/constraints/findings and "
    "expected output; the child has no conversation history. Verify and synthesize its result. Use "
    "resume_task_id only for the same unfinished/corrected browser task within the original deadline "
    "and one-resume limit; a preserved cua_agent timeout session continues only missing work. For "
    "browser_agent, supply browser_capabilities ([] for core-only); omit it for other types. Follow "
    "system routing/evidence/recovery rules. Available types and capabilities:\n{available_agents}"
)

TASK_TOOL_DESCRIPTION_CN = """\
启动新的子代理，自主处理复杂、多步骤任务。

task_tool 启动专门的子代理来自主处理复杂任务。每种子代理类型都有\
特定的能力和可用工具。

可用代理类型及其工具：

{available_agents}

使用 task_tool 时，请通过 subagent_type 参数选择要使用的子代理类型。\
使用 task_tool 时，必须显式指定 subagent_type，且其值必须与上方\
「可用代理类型」列表中的名称完全一致。
当 subagent_type 为 "browser_agent" 时，还必须指定 browser_capabilities，\
并从上方浏览器能力目录中选择额外能力类别。仅使用核心浏览器能力时传入\
空列表；其他子代理类型不要提供 browser_capabilities。

何时不使用 task_tool：

- 如果你想读取某个具体文件路径，直接用 read_file 或 glob，\
比用 task_tool 更快
- 如果你在查找某个具体的类定义（如 "class Foo"），\
直接用 grep 或 glob，比用 task_tool 更快
- 如果你在 2-3 个特定文件内搜索代码，\
直接用 read_file，比用 task_tool 更快
- 其他与上述代理描述无关的任务

使用注意事项：

- task_description 应包含完整的上下文信息——子代理没有本次对话的任何记忆
- 子代理完成后会返回一条消息给你。该结果对用户不可见。\
如需向用户展示结果，你应发送一条文字消息，简明总结子代理的结果。
- 每次 task_tool 调用默认都是全新启动——请提供完整的任务描述。\
只有在原期限内明确继续或纠正同一个浏览器任务，\
或明确继续同一个未完成的桌面（cua_agent）任务时，才传入上次返回的 resume_task_id。
- 若 cua_agent 返回 status="timeout" 且 retryable=true，其桌面会话已保留：\
请用同一个 resume_task_id 继续，任务描述只写尚未完成的部分，不要从头重做。
- 对 browser_agent，保留 browser_result.status 以及 summary、evidence 和带来源的 observations。\
partial 中也可能已有可用答案。执行回执说明派发/ACK；summary 和完成判断由 worker 提供。\
区分来源观察与原因推测，遵守原文限制，阻断列表为空不能证明页面没有障碍。不要为填字段槽而重复查询。
- retryable=true 只是允许续跑，不是要求续跑。只有用户要求确实尚未回答时，\
才可使用同一 resume_task_id 至多续跑一次；recommended_recovery 是提示，不是完成清单。\
若 worker 报告 completed 但仍有明确未满足的用户要求，可带同一 resume_task_id 和定向修复指令显式纠正，\
仍受原期限和一次续跑限制。否则报告已有结果，不要重做已满足的目标或新增要求。
- 子代理的输出通常应当被信任。
- 明确告知子代理你期望它写代码还是仅做调研\
（搜索、读文件、抓取网页等），因为它不知道用户的意图。
- 如果子代理的描述中提到应主动使用它，\
你应尽量在用户没有明确要求时就使用它。自行判断。
- 如果用户明确要求"并行"运行子代理，\
你必须在同一条消息中发出多个 task_tool 调用。\
例如，如果你需要同时启动 build-validator 子代理和 \
test-runner 子代理，请在同一条消息中发出两个 tool 调用。

## 如何写好任务描述

像给一位刚走进房间的聪明同事做简报一样描述任务——子代理没看过本次对话，\
不知道你尝试过什么，也不理解这个任务为什么重要。
- 说明你想达成什么目标以及为什么。
- 描述你已经了解到的信息或已排除的可能性。
- 提供足够的问题背景，让子代理能够自主判断，\
而不是只能机械执行狭窄的指令。
- 如果需要简短回复，请明确说明（"在 200 字内汇报"）。
- 查找类任务：直接给出精确命令。调研类任务：给出要回答的问题——\
规定好的步骤在前提不成立时只会成为累赘。

简短的命令式描述会产生浅层、泛泛的结果。

**永远不要委托理解。** 不要写"根据你的发现修复 bug"或"根据调研结果实现它"。\
这类说法把综合分析推给了子代理，而不是你自己完成。\
写出的描述应能证明你已经理解了问题：\
包含文件路径、行号、具体需要修改的内容。
"""

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
        "en": "Exact currently listed type; do not invent types.",
    },
    "task_description": {
        "cn": "任务描述（必填，需包含完整上下文）",
        "en": "Required task description with full context for the subagent",
    },
    "browser_capabilities": {
        "cn": "浏览器子代理所需的额外能力类别列表；仅使用核心能力时传入空列表",
        "en": "Additional capability categories required by browser_agent; use an empty list for core-only tasks",
    },
    "resume_task_id": {
        "cn": (
            "仅在继续同一个未完成的浏览器或桌面（cua_agent）任务时，"
            "传入上一次 task_tool 返回的 resume_task_id；新任务不要传"
        ),
        "en": "Continue the same unfinished browser/preserved cua task; omit for new work.",
    },
}


def get_task_tool_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for task tool input_params.

    Args:
        language: 'cn' or 'en'.

    Returns:
        JSON Schema dict for tool parameters.
    """
    p = TASK_TOOL_PARAMS
    return {
        "type": "object",
        "properties": {
            "subagent_type": {
                "type": "string",
                "description": p["subagent_type"].get(language, p["subagent_type"]["cn"]),
            },
            "task_description": {
                "type": "string",
                "description": p["task_description"].get(language, p["task_description"]["cn"]),
            },
            "browser_capabilities": {
                "type": "array",
                "items": {"type": "string"},
                "description": p["browser_capabilities"].get(language, p["browser_capabilities"]["cn"]),
            },
            "resume_task_id": {
                "type": "string",
                "description": p["resume_task_id"].get(language, p["resume_task_id"]["cn"]),
            },
        },
        "required": ["subagent_type", "task_description"],
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
