# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for session tools."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import ToolMetadataProvider

# ---------------------------------------------------------------------------
# sessions_list tool
# ---------------------------------------------------------------------------
SESSIONS_LIST_DESCRIPTION: Dict[str, str] = {
    "cn": "查看当前所有后台异步子任务(包括运行中、已完成、失败、已取消)及其元数据",
    "en": "List all background async tasks (running, completed, failed, canceled) and its metadata",
}


def get_sessions_list_input_params(language: str = "cn") -> Dict[str, Any]:
    """sessions_list has no input parameters."""
    return {
        "type": "object",
        "properties": {},
        "required": [],
    }


class SessionsListMetadataProvider(ToolMetadataProvider):
    """SessionsListTool 的元数据 provider"""

    def get_name(self) -> str:
        return "sessions_list"

    def get_description(self, language: str = "cn") -> str:
        return SESSIONS_LIST_DESCRIPTION.get(language, SESSIONS_LIST_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_sessions_list_input_params(language)


# ---------------------------------------------------------------------------
# sessions_spawn tool
# ---------------------------------------------------------------------------
SESSIONS_SPAWN_DESCRIPTION_EN = """Create async background subagent task that returns pending status immediately 
while the task executes in the background without blocking the current conversation.

Available agent types and the tools they have access to:
{available_agents}

Important: When using sessions_spawn, 
you must specify the subagent_type and task_description parameters to select the agent type and describe the task.
Do not specify agents you do not have access to!!!

## When to use:
- Tasks that are complex, multi-step, and can be executed independently
- Scenarios requiring parallel processing, focused reasoning, or large context/token usage
- Tasks that require sandboxed execution (e.g., code execution, search, formatting)
- When only the final output is needed and intermediate steps are not required
- When you want to continue handling user queries while a long-running task executes

## When NOT to use:
- Tasks are simple and can be completed quickly
- Intermediate steps need to be observed
- Task decomposition provides no benefit and only adds latency
- User explicitly wants to wait for results before continuing

## Usage Guidelines:
1. Execute independent tasks in parallel whenever possible to improve performance
2. Use sub-agents to isolate complex tasks and avoid main thread overload
3. After spawning, immediately inform user the task is running in background
4. Use sessions_list to check task status and retrieve results
5. Do NOT repeatedly call sessions_spawn for the same task just because status is pending
6. Only create new task when user explicitly requests retry or changes parameters

## Important Notes:
- Actual task execution happens asynchronously in background
- Use sessions_list with task_id to check completion status and get results

## Examples:

Example 1: Parallel independent research
    User: Research achievements of LeBron James and Kobe Bryant, then compare them.
    Assistant: [Spawns 2 parallel sessions_spawn tasks for each player]
    Assistant: [Waits and uses sessions_list to collect results]
    Assistant: [Summarizes comparison for user]
    Reasoning: Complex research tasks that are independent, suitable for parallel execution.

Example 2: High-context isolation
    User: Analyze security vulnerabilities in this large codebase and generate a report.
    Assistant: [Spawns single sessions_spawn task]
    Assistant: [Continues handling other user queries]
    Assistant: [Later retrieves report via sessions_list]
    Reasoning: Isolate high-consumption task to avoid main thread overload.

Example 3: Simple task - DO NOT use sessions_spawn
    User: Read file config.json and tell me the version.
    Assistant: [Directly calls read_file tool, no sessions_spawn]
    Reasoning: Simple task, direct execution is faster and clearer.
"""

SESSIONS_SPAWN_DESCRIPTION_CN = """创建异步后台子代理任务，立即返回 pending 状态，任务在后台执行，不阻塞当前对话。

可用代理类型及对应工具：
{available_agents}

重要：使用 sessions_spawn 时，必须指定 subagent_type, task_description 参数选择代理类型和描述任务。请勿指定你无权访问的其他代理！！！

## 使用场景:
- 任务复杂、多步骤、可独立执行
- 需要并行处理、专注推理、大量上下文 / Token
- 需要沙箱安全执行（代码、搜索、格式化）
- 只需最终输出，不关心中间过程
- 希望在长时间任务执行期间继续处理用户其他问题

## 不使用场景:
- 任务简单，可快速完成
- 需要查看中间步骤
- 拆分无收益、仅增加延迟
- 用户明确要求等待结果后再继续

## 使用原则:
1. 独立任务尽量并行执行以提升性能
2. 用子代理隔离复杂任务，避免主线程过载
3. 提交后立即告知用户任务已在后台执行
4. 不要因为状态是 pending 就重复调用 sessions_spawn 创建相同任务
5. 仅当用户明确要求重试或变更参数时，才发起新任务

## 重要说明:
- 实际任务在后台异步执行

## 示例:

示例 1：并行独立研究
    用户：研究詹姆斯和科比的成就并对比。
    助手：[并行启动 2 个 sessions_spawn 任务分别研究两位球员]
    助手：[汇总对比结果回复用户]
    说明：研究复杂且各球员相互独立，适合拆分并行执行。

示例 2：单任务高上下文隔离
    用户：分析大型代码库的安全漏洞并生成报告。
    助手：[启动单个 sessions_spawn 任务]
    助手：[继续处理用户其他问题]
    说明：用子代理隔离高消耗任务，避免主线程过载。

示例 3：简单任务 - 不要使用 sessions_spawn
    用户：读取 config.json 并告诉我版本号。
    助手：[直接调用 read_file 工具，不用 sessions_spawn]
    说明：简单任务，直接执行更快更清晰。
"""

SESSIONS_SPAWN_DESCRIPTION: Dict[str, str] = {
    "cn": SESSIONS_SPAWN_DESCRIPTION_CN,
    "en": SESSIONS_SPAWN_DESCRIPTION_EN,
}

SESSIONS_SPAWN_PARAMS: Dict[str, Dict[str, str]] = {
    "subagent_type": {
        "cn": "子 agent 类型(如 'general-purpose')",
        "en": "Subagent type (e.g., 'general-purpose')",
    },
    "task_description": {
        "cn": "任务描述",
        "en": "Task description",
    },
}


def get_sessions_spawn_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return JSON Schema for sessions_spawn input parameters."""
    p = SESSIONS_SPAWN_PARAMS
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
        },
        "required": ["subagent_type", "task_description"],
    }


class SessionsSpawnMetadataProvider(ToolMetadataProvider):
    """SessionsSpawnTool 的元数据 provider"""

    def get_name(self) -> str:
        return "sessions_spawn"

    def get_description(self, language: str = "cn") -> str:
        return SESSIONS_SPAWN_DESCRIPTION.get(language, SESSIONS_SPAWN_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_sessions_spawn_input_params(language)


# ---------------------------------------------------------------------------
# sessions_cancel tool
# ---------------------------------------------------------------------------
SESSIONS_CANCEL_DESCRIPTION: Dict[str, str] = {
    "cn": "取消后台异步子任务。此操作会同步阻塞直到任务取消完成。",
    "en": "Cancel background async task. This operation blocks synchronously until cancellation completes.",
}


def get_sessions_cancel_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return JSON Schema for sessions_cancel input parameters."""
    task_id_desc = {
        "cn": "要取消的任务 ID（从 sessions_list 获取）",
        "en": "Task ID to cancel (obtained from sessions_list)",
    }
    return {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": task_id_desc.get(language, task_id_desc["cn"]),
            },
        },
        "required": ["task_id"],
    }


class SessionsCancelMetadataProvider(ToolMetadataProvider):
    """SessionsCancelTool 的元数据 provider"""

    def get_name(self) -> str:
        return "sessions_cancel"

    def get_description(self, language: str = "cn") -> str:
        return SESSIONS_CANCEL_DESCRIPTION.get(language, SESSIONS_CANCEL_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_sessions_cancel_input_params(language)
