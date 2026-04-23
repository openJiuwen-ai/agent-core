# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual descriptions and input params for Todo tools."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

# ---------------------------------------------------------------------------
# Todo-create description
# ---------------------------------------------------------------------------
TODO_CREATE_DESCRIPTION_CN = """
创建当前会话的待办事项列表，用于跟踪进度、组织复杂任务，帮助用户了解整体执行情况。

## 何时使用

主动在以下场景调用：
- 任务需要 3 个或更多步骤
- 用户提供多个待完成事项（编号列表、逗号或分号分隔）
- 用户明确要求使用待办清单
- 任务具有规划性质（多步骤实现、功能开发等）

识别到规划需求后，立即调用本工具。

## 何时不使用

- 单个简单任务
- 纯信息查询或对话
- 可在 3 步以内完成的琐碎任务

## 使用方式

入参为 JSON 数组：
    {"tasks": [{"content": "翻译文档", "selected_model_id": "fast"}, {"content": "分析代码架构", "selected_model_id": "smart"}]}

## 规则

- 第一个任务自动设为 in_progress，其余为 pending
- 同一时间只能有一个 in_progress 任务
- 任务描述必须具体、可执行、清晰明确
- 调用本工具会覆盖当前会话的任务列表；若需追加任务，请使用 todo_modify
- 当没有获取到当前可用模型信息时，不要添加 selected_model_id 字段；否则必须添加 selected_model_id 指定执行任务的模型 ID
"""

TODO_CREATE_DESCRIPTION_EN = """
Create a todo list for the current session to track progress, organize complex tasks, and help the user understand overall execution status.

## When to Use

Call proactively in these scenarios:
- Task requires 3 or more distinct steps
- User provides multiple items to complete (numbered list, comma- or semicolon-separated)
- User explicitly requests a todo list
- Task has planning nature (multi-step implementation, feature development, etc.)

Once you identify a planning need, call this tool immediately.

## When NOT to Use

- Single, straightforward task
- Pure informational queries or conversation
- Tasks completable in fewer than 3 trivial steps

## Usage

Input is a JSON array:
    {"tasks": [{"content": "Translate document", "selected_model_id": "fast"}, {"content": "Analyze code architecture", "selected_model_id": "smart"}]}

## Rules

- First task is automatically set to in_progress, others to pending
- Only one task can be in_progress at a time
- Task descriptions must be specific, actionable, and clear
- Calling this tool replaces the current session's task list; use todo_modify to append tasks
- When the currently available model information is not obtained, the selected_model_id field should not be added; otherwise, selected_model_id must be added to specify the model ID for executing the task.
"""

TODO_CREATE_DESCRIPTION: Dict[str, str] = {
    "cn": TODO_CREATE_DESCRIPTION_CN,
    "en": TODO_CREATE_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Todo-list description
# ---------------------------------------------------------------------------
TODO_LIST_DESCRIPTION_CN = """
检索并显示当前会话的所有待办事项。

## 何时使用 todo_list（而非 todo_modify）

使用 todo_list 的场景：
- 需要查看当前任务全貌和各任务ID，再决定如何更新
- 不确定当前有哪些任务处于 in_progress 或 pending

使用 todo_modify 的场景（不需要先调用 todo_list）：
- 已知任务 ID，直接更新任务信息
- 任务刚完成，立即标记为 completed
"""

TODO_LIST_DESCRIPTION_EN = """
Retrieve and display all todo items for the current session

## When to Use todo_list (vs. todo_modify)

Use todo_list when:
- You need an overview of all tasks and their IDs before deciding how to update
- You are unsure which tasks are currently in_progress or pending

Use todo_modify directly (no need to call todo_list first) when:
- You already know the task ID and want to update task information
- A task just finished and you want to mark it completed immediately
"""

TODO_LIST_DESCRIPTION: Dict[str, str] = {
    "cn": TODO_LIST_DESCRIPTION_CN,
    "en": TODO_LIST_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Todo-modify description
# ---------------------------------------------------------------------------
TODO_MODIFY_DESCRIPTION_CN = """
修改当前会话的待办事项。支持批量操作，尽量将多个变更合并为一次调用。

核心用途：更新（update）、删除（delete）、取消（cancel）、追加（append）、在其后插入（insert_after）、在其前插入（insert_before）

重要说明：
- 若需重新规划整个任务列表，请调用 todo_create
- 支持批量操作，尽量将多个变更合并为一次调用，避免连续多次调用

action 支持的操作类型：

update：修改现有任务的状态或标题（id 不可修改，支持部分字段更新）：
    {
        "action": "update",
        "todos": [
            {"id": "uuid-1", "status": "completed"},
            {"id": "uuid-2", "status": "in_progress"}
        ]
    }

支持修改 selected_model_id：若任务 selected_model_id 不为空，且执行结果质量不佳（输出不准确、逻辑错误、未达预期），应根据模型描述更新质量更高的模型ID：
    {
        "action": "update",
        "todos": [
            {"id": "uuid-1", "selected_model_id": "smart", "status": "pending"}
        ]
    }

cancel：将指定任务标记为 cancelled（任务将被忽略，不再执行）：
    {
        "action": "cancel",
        "ids": ["uuid-1", "uuid-2"]
    }

delete：从列表中永久删除指定任务：
    {
        "action": "delete",
        "ids": ["uuid-1"]
    }

append：在列表末尾追加新任务：
    {
        "action": "append",
        "todos": [
            {"id": "uuid-new", "content": "新任务内容", "activeForm": "执行新任务", "description": "任务的详细描述", "status": "pending"}
        ]
    }

insert_after：在指定任务之后插入新任务（目标任务状态须为 in_progress 或 pending）：
    {
        "action": "insert_after",
        "todo_data": {"target_id": "uuid-target", "items": [{"id": "uuid-new", "content": "插入的任务", "activeForm": "执行插入的任务", "description": "任务的详细描述", "status": "pending", "selected_model_id": "fast"}]}
    }

insert_before：在指定任务之前插入新任务（目标任务状态须为 pending）：
    {
        "action": "insert_before",
        "todo_data": {"target_id": "uuid-target", "items": [{"id": "uuid-new", "content": "插入的任务", "activeForm": "执行插入的任务", "description": "任务的详细描述", "status": "pending"}]}
    }

核心规则：
- 同一时间只能有一个任务处于 in_progress 状态
- update 操作：id 字段不可修改，其他字段支持部分更新
- insert_after：目标任务状态必须为 in_progress 或 pending
- insert_before：目标任务状态必须为 pending
- 如果任务的 selected_model_id 为空时，任何操作都不要更改 selected_model_id 字段
"""

TODO_MODIFY_DESCRIPTION_EN = """
Modify todo items for the current session. Supports batch operations — consolidate multiple changes into a single call whenever possible.

Core purpose: update, delete, cancel, append, insert_after, insert_before.

Important notes:
- To re-plan the entire task list, call todo_create instead
- Batch multiple changes into one call; avoid calling todo_modify repeatedly in succession

Supported action types:

update: Modify status or content of existing tasks (id cannot be changed; partial field updates supported):
    {
        "action": "update",
        "todos": [
            {"id": "uuid-1", "status": "completed"},
            {"id": "uuid-2", "status": "in_progress"}
        ]
    }

Support modifying selected_model_id: If the task's selected_model_id is not empty and the execution result is of poor quality (inaccurate output, logical errors, or failure to meet expectations), the model ID should be updated according to the model description to a higher-quality model:
    {
        "action": "update",
        "todos": [
            {"id": "uuid-1", "selected_model_id": "smart", "status": "pending"}
        ]
    }

cancel: Mark specified tasks as cancelled (tasks will be ignored and not executed):
    {
        "action": "cancel",
        "ids": ["uuid-1", "uuid-2"]
    }

delete: Permanently remove specified tasks from the list:
    {
        "action": "delete",
        "ids": ["uuid-1"]
    }

append: Add new tasks at the end of the list:
    {
        "action": "append",
        "todos": [
            {"id": "uuid-new", "content": "New task content", "activeForm": "Executing new task", "description": "Detailed description of the task", "status": "pending"}
        ]
    }

insert_after: Insert new tasks after the specified task (target must be in_progress or pending):
    {
        "action": "insert_after",
        "todo_data": {"target_id": "uuid-target", "items": [{"id": "uuid-new", "content": "Inserted task", "activeForm": "Executing inserted task", "description": "Detailed description of the task", "status": "pending", "selected_model_id": "fast"}]}
    }

insert_before: Insert new tasks before the specified task (target must be pending):
    {
        "action": "insert_before",
        "todo_data": {"target_id": "uuid-target", "items": [{"id": "uuid-new", "content": "Inserted task", "activeForm": "Executing inserted task", "description": "Detailed description of the task", "status": "pending"}]}
    }

Core rules:
- Only one task can be in_progress at a time
- update action: id field cannot be modified; other fields support partial updates
- insert_after: target task status must be in_progress or pending
- insert_before: target task status must be pending
- If the task's selected_model_id is empty, do not modify the selected_model_id field in any operation.
"""

TODO_MODIFY_DESCRIPTION: Dict[str, str] = {
    "cn": TODO_MODIFY_DESCRIPTION_CN,
    "en": TODO_MODIFY_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Todo-get description
# ---------------------------------------------------------------------------
TODO_GET_DESCRIPTION_CN = """
根据任务 ID 获取单个任务的完整详情。

入参：id（任务唯一标识符）

返回：完整的任务信息，包括 id、content（任务摘要）、activeForm、description（任务详细内容）、status、depends_on、result_summary、meta_data、selected_model_id。
"""

TODO_GET_DESCRIPTION_EN = """
Get full details of a single task by its ID.

Input: id (unique task identifier)

Returns: complete task info including id, content (task summary), activeForm, description (detailed content), status, depends_on, result_summary, meta_data, selected_model_id.
"""

TODO_GET_DESCRIPTION: Dict[str, str] = {
    "cn": TODO_GET_DESCRIPTION_CN,
    "en": TODO_GET_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Parameter-level bilingual descriptions
# ---------------------------------------------------------------------------
TODO_CREATE_PARAMS: Dict[str, Dict[str, str]] = {
    "tasks": {
        "cn": (
            "子任务列表，JSON 数组格式。每个元素为任务对象，必填字段：\n"
            "- content：任务摘要描述\n"
            "- activeForm：content 的进行语态（如 content 为「翻译文档」，activeForm 为「正在翻译文档」）\n"
            "- description：任务详细内容\n"
            "可选字段：\n"
            "- selected_model_id：执行任务的模型 ID，见系统提示词「模型选择策略」"
        ),
        "en": (
            "List of subtasks in JSON array format. Each element is a task object with required fields:\n"
            "- content: task summary description\n"
            "- activeForm: present-tense form of content "
            "(e.g., content 'Translate document' -> activeForm 'Translating document')\n"
            "- description: detailed task content\n"
            "Optional field:\n"
            "- selected_model_id: model ID, see 'Model Selection Strategy' in system prompt"
        ),
    },
}

_TODO_ITEM_PARAMS: Dict[str, Dict[str, str]] = {
    "id": {"cn": "任务唯一标识符", "en": "Unique task identifier"},
    "content": {"cn": "任务摘要描述", "en": "Task summary description"},
    "activeForm": {"cn": "content 的进行语态", "en": "Present-tense form of content"},
    "description": {"cn": "任务详细内容", "en": "Detailed task content"},
    "status": {"cn": "任务状态", "en": "Task status"},
    "selected_model_id": {
        "cn": (
            "执行此任务使用的模型 ID。见系统提示词「模型选择策略」。"
            "若任务结果不满意，可通过 todo_modify 更换更强的模型 ID 后重试。"
        ),
        "en": (
            "Model ID for this task. See 'Model Selection Strategy' in system prompt. "
            "If task result is unsatisfactory, update via todo_modify and retry."
        ),
    },
}

TODO_MODIFY_PARAMS: Dict[str, Dict[str, str]] = {
    "action": {"cn": "要执行的操作类型", "en": "Operation type to perform"},
    "ids": {"cn": "要操作的任务 ID 列表", "en": "List of task IDs to operate on"},
    "ids_item": {"cn": "任务唯一标识符", "en": "Unique task identifier"},
    "todos": {
        "cn": (
            "根据 action 字段处理的待办事项数组。"
            "支持修改 selected_model_id：若某任务执行结果质量不佳（输出不准确、逻辑错误、未达预期），"
            "应将 selected_model_id 更新为更高等级的模型 ID，然后将任务状态重置为 pending 或 in_progress 以触发重新执行。"
        ),
        "en": (
            "Array of todo items to process based on the action field. "
            "Supports updating selected_model_id: if a task produces poor results (inaccurate output, "
            "logical errors, unmet objectives), update selected_model_id to a model ID whose description "
            "indicates stronger capability, "
            "and reset the task status to pending or in_progress to trigger re-execution."
        ),
    },
    "todo_data": {"cn": "用于 insert_after/insert_before 操作的对象", "en": "Object for insert_after/insert_before actions"},
    "todo_data_target_id": {"cn": "目标任务 ID", "en": "Target task ID"},
    "todo_data_items": {"cn": "要插入的任务列表", "en": "Tasks to insert"},
}


TODO_GET_PARAMS: Dict[str, Dict[str, str]] = {
    "id": {
        "cn": "任务唯一标识符",
        "en": "Unique task identifier",
    },
}


def _todo_item_properties(language: str = "cn") -> Dict[str, Any]:
    """Return the shared todo item sub-schema properties."""
    p = _TODO_ITEM_PARAMS

    def _d(key: str) -> str:
        return p[key].get(language, p[key]["cn"])

    return {
        "id": {"type": "string", "description": _d("id")},
        "content": {"type": "string", "description": _d("content")},
        "activeForm": {"type": "string", "description": _d("activeForm")},
        "description": {"type": "string", "description": _d("description")},
        "status": {
            "type": "string",
            "description": _d("status"),
            "enum": ["pending", "in_progress", "completed", "cancelled"],
        },
        "selected_model_id": {
            "type": "string",
            "description": _d("selected_model_id"),
        },
    }


def get_todo_create_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for todo_create tool input_params."""
    p = TODO_CREATE_PARAMS
    tasks_desc = p["tasks"].get(language, p["tasks"]["cn"])
    item_props = _todo_item_properties(language)
    return {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": tasks_desc,
                "items": {
                    "type": "object",
                    "properties": {
                        "content": item_props["content"],
                        "activeForm": item_props["activeForm"],
                        "description": item_props["description"],
                        "selected_model_id": item_props["selected_model_id"],
                    },
                    "required": ["content", "activeForm", "description"],
                },
            },
        },
        "required": ["tasks"],
    }


def get_todo_list_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for todo_read tool input_params."""
    return {
        "type": "object",
        "properties": {},
        "required": [],
    }


def get_todo_modify_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for todo_modify tool input_params."""
    p = TODO_MODIFY_PARAMS

    def _d(key: str) -> str:
        return p[key].get(language, p[key]["cn"])
    item_props = _todo_item_properties(language)
    todo_item_schema = {
        "type": "object",
        "properties": {
            "id": item_props["id"],
            "content": item_props["content"],
            "activeForm": item_props["activeForm"],
            "description": item_props["description"],
            "status": item_props["status"],
            "selected_model_id": item_props["selected_model_id"],
        },
        "required": ["id"],
    }
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": _d("action"),
                "enum": ["update", "delete", "cancel", "append", "insert_after", "insert_before"],
            },
            "ids": {
                "type": "array",
                "description": _d("ids"),
                "items": {"type": "string"},
            },
            "todos": {
                "type": "array",
                "description": _d("todos"),
                "items": todo_item_schema,
            },
            "todo_data": {
                "type": "object",
                "description": _d("todo_data"),
                "properties": {
                    "target_id": {"type": "string", "description": _d("todo_data_target_id")},
                    "items": {
                        "type": "array",
                        "description": _d("todo_data_items"),
                        "items": todo_item_schema,
                    },
                },
                "required": ["target_id", "items"],
            },
        },
        "required": ["action"],
    }


def get_todo_get_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for todo_get tool input_params."""
    p = TODO_GET_PARAMS
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": p["id"].get(language, p["id"]["cn"])},
        },
        "required": ["id"],
    }


class TodoCreateMetadataProvider(ToolMetadataProvider):
    """TodoCreate 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "todo_create"

    def get_description(self, language: str = "cn") -> str:
        return TODO_CREATE_DESCRIPTION.get(
            language, TODO_CREATE_DESCRIPTION["cn"]
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_todo_create_input_params(language)


class TodoListMetadataProvider(ToolMetadataProvider):
    """TodoList 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "todo_list"

    def get_description(self, language: str = "cn") -> str:
        return TODO_LIST_DESCRIPTION.get(
            language, TODO_LIST_DESCRIPTION["cn"]
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_todo_list_input_params(language)


class TodoModifyMetadataProvider(ToolMetadataProvider):
    """TodoModify 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "todo_modify"

    def get_description(self, language: str = "cn") -> str:
        return TODO_MODIFY_DESCRIPTION.get(
            language, TODO_MODIFY_DESCRIPTION["cn"]
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_todo_modify_input_params(language)


class TodoGetMetadataProvider(ToolMetadataProvider):
    """TodoGet 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "todo_get"

    def get_description(self, language: str = "cn") -> str:
        return TODO_GET_DESCRIPTION.get(language, TODO_GET_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_todo_get_input_params(language)
