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
- 本 session 已有 pending/in_progress 且用户要续跑/重试（先 todo_list → todo_modify，勿 todo_create 覆盖）

## 使用方式

用分号(;)分隔多个任务：
    {
        "tasks": "设计用户界面；实现接口集成；添加单元测试"
    }

可选 `force`（默认 false）：仅当用户**明确要求重新规划/清空计划**时设为 true，才会覆盖已有 pending/in_progress 任务。

## 规则

- 第一个任务自动设为 in_progress，其余为 pending
- 同一时间只能有一个 in_progress 任务
- 任务描述必须具体、可执行、清晰明确
- 调用本工具会覆盖当前会话的任务列表；若需追加任务，请使用 todo_modify
- **若 todo.json 中已有 pending/in_progress 任务，默认拒绝创建**；续跑请 todo_list + todo_modify
- force=true 的唯一用途：有活跃项且用户要求重规划时，绕过默认拒绝并覆盖当前列表
- 本工具仅影响**当前 session** 的 todo.json；不影响其他 session（含子 Agent session）
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
- This session already has pending/in_progress and the user wants to continue/retry
  (todo_list → todo_modify first; do not todo_create over them)

## Usage

Use semicolons (;) to separate multiple tasks:
    {
        "tasks": "Design user interface;Implement API integration;Add unit tests"
    }

Optional `force` (default false): set true only when the user **explicitly asks to replan or clear the plan**,
to overwrite existing pending/in_progress tasks.

## Rules

- First task is automatically set to in_progress, others to pending
- Only one task can be in_progress at a time
- Task descriptions must be specific, actionable, and clear
- Calling this tool replaces the current session's task list; use todo_modify to append tasks
- **Rejected by default if pending/in_progress todos exist**; resume with todo_list + todo_modify
- The only use of force=true: when active items exist and the user asks to replan,
  bypass default rejection and overwrite the current list
- This tool only affects **this session's** todo.json;
  does not affect other sessions (including sub-agent sessions)
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

使用 todo_modify 的场景（必要时再触发 todo_list）：
- 已知任务 ID，直接更新状态、内容或追加新任务
- 任务刚完成，立即标记为 completed
- 重要：不确定任务 ID 是否准确时，调用 todo_list 获取最新的任务列表和准确的 ID，再调用 todo_modify。因为错误的 ID（即使只错一个字符）会导致操作失败。
"""

TODO_LIST_DESCRIPTION_EN = """
Retrieve and display all todo items for the current session

## When to Use todo_list (vs. todo_modify)

Use todo_list when:
- You need an overview of all tasks and their IDs before deciding how to update
- You are unsure which tasks are currently in_progress or pending

Use todo_modify directly (Trigger todo_list when necessary) when:
- You already know the task ID and want to update status, content, or append new tasks
- A task just finished and you want to mark it completed immediately
- Important: If you are not 100% sure about the task IDs, call todo_list first to get the accurate ID list, then call todo_modify. An incorrect ID (even one wrong character) will cause the operation to fail.
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
        "todo_data": {"target_id": "uuid-target", "items": [{"id": "uuid-new", "content": "插入的任务", "activeForm": "执行插入的任务", "status": "pending"}]}
    }

insert_before：在指定任务之前插入新任务（目标任务状态须为 pending）：
    {
        "action": "insert_before",
        "todo_data": {"target_id": "uuid-target", "items": [{"id": "uuid-new", "content": "插入的任务", "activeForm": "执行插入的任务", "status": "pending"}]}
    }

核心规则：
- 同一时间只能有一个任务处于 in_progress 状态
- update 操作：id 字段不可修改，其他字段支持部分更新
- insert_after：目标任务状态必须为 in_progress 或 pending
- insert_before：目标任务状态必须为 pending

## 顺序（强制，update 时由工具校验）

任务列表有固定顺序（与 todo_create 创建顺序一致）。

1. **按序更新**：不得跳过仍为 pending 的前序任务将后项标为 in_progress/completed；in_progress 只能落在第一个非终态任务上；若当前项为 in_progress，须在同批先标 completed 再开下一项；跳过步骤须同批将前序标为 cancelled。
2. **批量更新前**：跨多个 Stage/步骤变更状态时，或不确定 UUID 时，先 todo_list 再 modify。
3. **仅改当前 in_progress 的 content/activeForm**（不改 status）时，可直接 update；其余跨任务状态变更适用上述规则。

ID 精确性要求（极其重要）：
- 任务 ID 是 UUID 格式，必须从 todo_create 返回值或 todo_list 结果中原样复制
- 禁止凭记忆推测或修改 ID 中的任何字符，即使只错一个字符也会导致操作失败
- 如果不确定任务 ID，请先调用 todo_list 获取准确的 ID 列表
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
        "todo_data": {"target_id": "uuid-target", "items": [{"id": "uuid-new", "content": "Inserted task", "activeForm": "Executing inserted task", "status": "pending"}]}
    }

insert_before: Insert new tasks before the specified task (target must be pending):
    {
        "action": "insert_before",
        "todo_data": {"target_id": "uuid-target", "items": [{"id": "uuid-new", "content": "Inserted task", "activeForm": "Executing inserted task", "status": "pending"}]}
    }

Core rules:
- Only one task can be in_progress at a time
- update action: id field cannot be modified; other fields support partial updates
- insert_after: target task status must be in_progress or pending
- insert_before: target task status must be pending

## Sequential order (enforced on update)

The todo list has a fixed order (same as todo_create).

1. **Update in order**: Do not skip pending earlier tasks when setting later tasks to in_progress/completed; in_progress may only be on the first non-terminal task; if the current task is in_progress, complete it in the same batch before starting the next; to skip a step, cancel the earlier task in the same batch.
2. **Before batch status changes** across stages or when UUIDs are uncertain, call todo_list first.
3. **Content-only updates** to the current in_progress task (no status change) may use update directly; cross-task status changes follow the rules above.

ID Accuracy Requirement (Critical):
- Task IDs are UUID format and MUST be copied exactly from todo_create results or todo_list output
- Do NOT guess or modify any character in the ID — even a single wrong character will cause the operation to fail
- If unsure about task IDs, call todo_list first to get the accurate ID list
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
    "force": {
        "cn": "是否强制覆盖已有活跃计划（默认 false）。仅当用户明确要求重来/重规划时设为 true",
        "en": ("Whether to force-overwrite an existing active plan (default false). "
               "Set true only when the user explicitly asks to replan or restart planning"),
    },
}

_TODO_ITEM_PARAMS: Dict[str, Dict[str, str]] = {
    "id": {"cn": "任务唯一标识符（UUID格式，必须从 todo_create 或 todo_list 结果中原样复制，禁止推测或修改）",
           "en": "Unique task identifier "
           "(UUID format, must be copied exactly from todo_create or todo_list results — do NOT guess or modify)"},
    "content": {"cn": "任务详细描述", "en": "Detailed task description"},
    "activeForm": {"cn": "任务进行时描述", "en": "Present-tense task description"},
    "status": {"cn": "任务状态", "en": "Task status"},
}

TODO_MODIFY_PARAMS: Dict[str, Dict[str, str]] = {
    "action": {"cn": "要执行的操作类型", "en": "Operation type to perform"},
    "ids": {"cn": "要操作的任务 ID 列表", "en": "List of task IDs to operate on"},
    "ids_item": {"cn": "任务唯一标识符", "en": "Unique task identifier"},
    "todos": {"cn": "根据 action 字段处理的待办事项数组", "en": "Array of todo items to process based on the action field"},
    "todo_data": {"cn": "用于 insert_after/insert_before 操作的对象", "en": "Object for insert_after/insert_before actions"},
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
            "force": {"type": "boolean", "description": p["force"].get(language, p["force"]["cn"])},
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
                "type": "object",
                "description": _d("todo_data"),
                "properties": {
                    "target_id": {"type": "string", "description": _d("todo_data_target_id")},
                    "items": {
                        "type": "array",
                        "description": _d("todo_data_items"),
                        "items": {
                            "type": "object",
                            "properties": item_props,
                            "required": ["id", "content", "activeForm", "status"],
                        },
                    },
                },
                "required": ["target_id", "items"],
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
