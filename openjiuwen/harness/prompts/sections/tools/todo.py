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

用分号(;)分隔多个任务：
    {
        "tasks": "设计用户界面；实现接口集成；添加单元测试"
    }

## 规则

- 第一个任务自动设为 in_progress，其余为 pending
- 同一时间只能有一个 in_progress 任务
- 任务描述必须具体、可执行、清晰明确
- 调用本工具会覆盖当前会话的任务列表；若需追加任务，请使用 todo_modify
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

Use semicolons (;) to separate multiple tasks:
    {
        "tasks": "Design user interface;Implement API integration;Add unit tests"
    }

## Rules

- First task is automatically set to in_progress, others to pending
- Only one task can be in_progress at a time
- Task descriptions must be specific, actionable, and clear
- Calling this tool replaces the current session's task list; use todo_modify to append tasks
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
- 已知任务 ID，直接更新状态、内容或追加新任务
- 任务刚完成，立即标记为 completed
"""

TODO_LIST_DESCRIPTION_EN = """
Retrieve and display all todo items for the current session

## When to Use todo_list (vs. todo_modify)

Use todo_list when:
- You need an overview of all tasks and their IDs before deciding how to update
- You are unsure which tasks are currently in_progress or pending

Use todo_modify directly (no need to call todo_list first) when:
- You already know the task ID and want to update status, content, or append new tasks
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

update：修改现有任务的状态或内容（id 不可修改，支持部分字段更新）：
    {
        "action": "update",
        "todos": [
            {"id": "uuid-1", "status": "completed"},
            {"id": "uuid-2", "status": "in_progress"}
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
            {"id": "uuid-new", "content": "新任务内容", "activeForm": "执行新任务", "status": "pending"}
        ]
    }

insert_after：在指定任务之后插入新任务（目标任务状态须为 in_progress 或 pending）：
    {
        "action": "insert_after",
        "todo_data": ["uuid-target", [{"id": "uuid-new", "content": "插入的任务", "activeForm": "执行插入的任务", "status": "pending"}]]
    }

insert_before：在指定任务之前插入新任务（目标任务状态须为 pending）：
    {
        "action": "insert_before",
        "todo_data": ["uuid-target", [{"id": "uuid-new", "content": "插入的任务", "activeForm": "执行插入的任务", "status": "pending"}]]
    }

核心规则：
- 同一时间只能有一个任务处于 in_progress 状态
- update 操作：id 字段不可修改，其他字段支持部分更新
- insert_after：目标任务状态必须为 in_progress 或 pending
- insert_before：目标任务状态必须为 pending
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
            {"id": "uuid-new", "content": "New task content", "activeForm": "Executing new task", "status": "pending"}
        ]
    }

insert_after: Insert new tasks after the specified task (target must be in_progress or pending):
    {
        "action": "insert_after",
        "todo_data": ["uuid-target", [{"id": "uuid-new", "content": "Inserted task", "activeForm": "Executing inserted task", "status": "pending"}]]
    }

insert_before: Insert new tasks before the specified task (target must be pending):
    {
        "action": "insert_before",
        "todo_data": ["uuid-target", [{"id": "uuid-new", "content": "Inserted task", "activeForm": "Executing inserted task", "status": "pending"}]]
    }

Core rules:
- Only one task can be in_progress at a time
- update action: id field cannot be modified; other fields support partial updates
- insert_after: target task status must be in_progress or pending
- insert_before: target task status must be pending
"""

TODO_MODIFY_DESCRIPTION: Dict[str, str] = {
    "cn": TODO_MODIFY_DESCRIPTION_CN,
    "en": TODO_MODIFY_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Parameter-level bilingual descriptions
# ---------------------------------------------------------------------------
TODO_CREATE_PARAMS: Dict[str, Dict[str, str]] = {
    "tasks": {
        "cn": "任务列表（仅支持分号分隔）。示例：'创建登录表单；实现表单验证；添加错误处理'",
        "en": ("Task list (only semicolons supported). "
               "Example: 'Create login form;Implement form validation;Add error handling'"),
    },
}

_TODO_ITEM_PARAMS: Dict[str, Dict[str, str]] = {
    "id": {"cn": "任务唯一标识符", "en": "Unique task identifier"},
    "content": {"cn": "任务详细描述", "en": "Detailed task description"},
    "activeForm": {"cn": "任务进行时描述", "en": "Present-tense task description"},
    "status": {"cn": "任务状态", "en": "Task status"},
}

TODO_MODIFY_PARAMS: Dict[str, Dict[str, str]] = {
    "action": {"cn": "要执行的操作类型", "en": "Operation type to perform"},
    "ids": {"cn": "要操作的任务 ID 列表", "en": "List of task IDs to operate on"},
    "ids_item": {"cn": "任务唯一标识符", "en": "Unique task identifier"},
    "todos": {"cn": "根据 action 字段处理的待办事项数组", "en": "Array of todo items to process based on the action field"},
    "todo_data": {"cn": "用于 insert_after/insert_before 操作的数组", "en": "Array for insert_after/insert_before actions"},
    "todo_data_target_id": {"cn": "目标任务 ID", "en": "Target task ID"},
    "todo_data_items": {"cn": "要插入的待办事项列表", "en": "List of todo objects to insert"},
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
        "status": {
            "type": "string",
            "description": _d("status"),
            "enum": ["pending", "in_progress", "completed", "cancelled"],
        },
    }


def get_todo_create_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for todo_write tool input_params."""
    p = TODO_CREATE_PARAMS
    return {
        "type": "object",
        "properties": {
            "tasks": {"type": "string", "description": p["tasks"].get(language, p["tasks"]["cn"])},
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
                "items": {"type": "string", "description": _d("ids_item")},
            },
            "todos": {
                "type": "array",
                "description": _d("todos"),
                "items": {
                    "type": "object",
                    "properties": item_props,
                    "required": ["id", "content", "activeForm", "status"],
                },
            },
            "todo_data": {
                "type": "array",
                "description": _d("todo_data"),
                "items": [
                    {"type": "string", "description": _d("todo_data_target_id")},
                    {
                        "type": "array",
                        "description": _d("todo_data_items"),
                        "items": {
                            "type": "object",
                            "properties": item_props,
                            "required": ["id", "content", "activeForm", "status"],
                        },
                    },
                ],
            },
        },
        "required": ["action"],
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
