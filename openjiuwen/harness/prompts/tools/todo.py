# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual descriptions and input params for Todo tools."""
from __future__ import annotations

from copy import deepcopy
from string import Template
from typing import Any, Dict

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.prompts.tools.base import (
    ToolMetadataProvider,
)

# ---------------------------------------------------------------------------
# Todo-create description
# ---------------------------------------------------------------------------
_TODO_CREATE_TEMPLATE_CN = Template("""
创建当前会话的待办事项列表，用于跟踪进度、组织复杂任务，帮助用户了解整体执行情况。

## 使用方式

入参为 JSON 数组（每个任务必须包含 id 字段）：
    {"tasks": [{"id": "translate_doc", "content": "翻译文档", "activeForm": "正在翻译文档", "description": "将文档翻译为目标语言", "selected_model_id": "fast", "status": "in_progress"}, {"id": "analyze_arch", "content": "分析代码架构", "activeForm": "正在分析代码架构", "description": "梳理代码模块结构与依赖关系", "selected_model_id": "smart", "status": "pending"}]}

## 规则

- **id 字段为必填**：必须为每个任务指定简短、语义清晰的唯一字符串 ID（如 "translate_doc"、"analyze_code"），禁止使用随机字符或 UUID。同一会话内 ID 不得重复。此 ID 在后续 todo_modify 中用于精准定位任务，是跨轮次更新状态的唯一依据
$status_rules
- status 仅允许 pending、in_progress、completed、cancelled；最多一个 in_progress，也允许没有进行中项或全部已结束
- 任务描述必须具体、可执行、清晰明确
- 调用本工具会覆盖当前会话的任务列表；若需追加任务，请使用 todo_modify
- 已有清单时，增加、拆分或取消个别步骤使用 todo_modify；只有大部分条目都要替换时，才用本工具整体重建。重建时提交要保留的完整清单，携带已有任务的 id 和真实状态；缺少完整清单时先调用 todo_list
- 当没有获取到当前可用模型信息时，不要添加 selected_model_id 字段；否则必须添加 selected_model_id 指定执行任务的模型 ID
""")

_TODO_CREATE_TEMPLATE_EN = Template("""
Create a todo list for the current session to track progress, organize complex tasks, and help the user understand overall execution status.

## Usage

Input is a JSON array (each task must include an id field):
    {"tasks": [{"id": "translate_doc", "content": "Translate document", "activeForm": "Translating document", "description": "Translate the document into the target language", "selected_model_id": "fast", "status": "in_progress"}, {"id": "analyze_arch", "content": "Analyze code architecture", "activeForm": "Analyzing code architecture", "description": "Map out module structure and dependencies", "selected_model_id": "smart", "status": "pending"}]}

## Rules

- **id is required**: You must provide a short, semantically meaningful unique string ID for each task (e.g. "translate_doc", "analyze_code"). Do NOT use random characters or UUIDs. IDs must be unique within a session. This ID is used by todo_modify to precisely locate tasks and is the sole key for cross-turn status updates
$status_rules
- status must be pending, in_progress, completed, or cancelled; at most one in_progress is allowed, including no active task or an entirely closed list
- Task descriptions must be specific, actionable, and clear
- Calling this tool replaces the current session's task list; use todo_modify to append tasks
- When a list already exists, use todo_modify to add, split, or cancel individual steps; rebuild with this tool only when most items are being replaced. When rebuilding, include the complete list to retain, preserving existing ids and actual statuses; read todo_list first if the complete list is unavailable
- When the currently available model information is not obtained, the selected_model_id field should not be added; otherwise, selected_model_id must be added to specify the model ID for executing the task.
""")

_TODO_CREATE_TEMPLATES = {"cn": _TODO_CREATE_TEMPLATE_CN, "en": _TODO_CREATE_TEMPLATE_EN}
_TODO_CREATE_OPTIONAL_STATUS_RULES = {
    "cn": (
        "- 全部省略 status 时，第一个任务自动设为 in_progress，其余为 pending；重建时全部省略也会重新初始化状态\n"
        "- 至少一项提供 status 时，保留显式状态，省略的记为 pending，不自动启动第一项"
    ),
    "en": (
        "- If every status is omitted, the first task becomes in_progress and the rest pending; "
        "rebuilding with all statuses omitted also resets the list\n"
        "- If any status is supplied, preserve explicit statuses and default omitted ones to pending, "
        "without automatically starting the first task"
    ),
}
_TODO_CREATE_REQUIRED_STATUS_RULES = {
    "cn": "- 每项必须显式传入真实 status；缺少状态时整批拒绝，本次不写入",
    "en": (
        "- Every task must explicitly include its actual status; "
        "missing statuses reject the entire call without writing"
    ),
}
# Status is one entry of the field lists in the tasks parameter description: by default it ends the
# optional fields; when required it moves above the optional-fields header.
_STATUS_PARAMETER_RULES = {
    "cn": {True: "- status：任务的真实状态，每项必填",
           False: "- status：任务状态，缺省规则见工具说明"},
    "en": {True: "- status: actual task status; required for every item",
           False: "- status: task status; see the tool's default rules when omitted"},
}
_OPTIONAL_FIELDS_HEADER = {"cn": "可选字段：", "en": "Optional field:"}


def _status_lang(language: str) -> str:
    """Language key for the status texts above; anything other than "en" uses Chinese."""
    return "en" if language == "en" else "cn"


def get_todo_create_description(language: str = "cn") -> str:
    """Return the todo_create description with the default (optional) status rules."""
    lang = _status_lang(language)
    return _TODO_CREATE_TEMPLATES[lang].substitute(status_rules=_TODO_CREATE_OPTIONAL_STATUS_RULES[lang])


TODO_CREATE_DESCRIPTION_CN = get_todo_create_description("cn")
TODO_CREATE_DESCRIPTION_EN = get_todo_create_description("en")

TODO_CREATE_DESCRIPTION: Dict[str, str] = {
    "cn": TODO_CREATE_DESCRIPTION_CN,
    "en": TODO_CREATE_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Todo-list description
# ---------------------------------------------------------------------------
TODO_LIST_DESCRIPTION_CN = """
检索并显示当前会话的完整清单，包含 pending、in_progress、completed、cancelled，保留原顺序；每项包含 id、content、status、depends_on。

## 何时使用 todo_list（而非 todo_modify）

使用 todo_list 的场景：
- 需要查看当前任务全貌和各任务ID，再决定如何更新
- 不确定当前有哪些任务处于 in_progress 或 pending

使用 todo_modify 的场景（不需要先调用 todo_list）：
- 已知任务 ID，直接更新任务信息
- 任务刚完成，立即标记为 completed
"""

TODO_LIST_DESCRIPTION_EN = """
Retrieve the complete current session list, including pending, in_progress, completed, and cancelled, preserving order; each item includes id, content, status, and depends_on.

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
修改当前会话的待办事项，包括：更新（update）、删除（delete）、取消（cancel）、追加（append）、在其后插入（insert_after）、在其前插入（insert_before）

重要说明：
- 在原清单上增加、拆分、取消个别步骤使用本工具（append、insert_after、insert_before、cancel），已完成项保持不动；只有大部分条目都需要替换时，才调用 todo_create 整体重建
- 清单保持执行顺序：新步骤要在某个已有 pending 步骤（如校对、交付）之前执行的，用 insert_before 插到该步骤前面；append 只用于排在所有已有步骤之后执行的步骤
- 同时发生的结构变更可批量处理。状态变化时及时更新；发现多项已完成但漏记时，核实后可以一次 update 补记。尚未完成或仍等待必要验证的工作不得提前标记 completed
- **id 使用你在 todo_create 时自定义的语义 ID**（如 "translate_doc"），不要使用 UUID
- **只传入发生变化的字段**：update 每次调用只需给出 id 加上实际改变的字段（通常只是 status），不要把 content、description、activeForm 等未变化的字段重新完整传一遍

action 支持的操作类型：

update：修改现有任务的状态或标题（id 不可修改，只需传入发生变化的字段，其余字段保持不传即可）：
    {
        "action": "update",
        "todos": [
            {"id": "translate_doc", "status": "completed"},
            {"id": "analyze_code", "status": "in_progress"}
        ]
    }

支持修改 selected_model_id：若任务 selected_model_id 不为空，且执行结果质量不佳（输出不准确、逻辑错误、未达预期），应根据模型描述更新质量更高的模型ID：
    {
        "action": "update",
        "todos": [
            {"id": "translate_doc", "selected_model_id": "smart", "status": "pending"}
        ]
    }

cancel：将指定任务标记为 cancelled（任务将被忽略，不再执行）：
    {
        "action": "cancel",
        "ids": ["translate_doc", "analyze_code"]
    }

delete：从列表中永久删除指定任务：
    {
        "action": "delete",
        "ids": ["translate_doc"]
    }

append：在列表末尾追加新任务（id 由你指定，须简短语义且唯一）：
    {
        "action": "append",
        "todos": [
            {"id": "write_report", "content": "新任务内容", "activeForm": "执行新任务", "description": "任务的详细描述", "status": "pending"}
        ]
    }

insert_after：在指定任务之后插入新任务（目标任务状态须为 completed、in_progress 或 pending）：
    {
        "action": "insert_after",
        "todo_data": {"target_id": "translate_doc", "items": [{"id": "review_translation", "content": "插入的任务", "activeForm": "执行插入的任务", "description": "任务的详细描述", "status": "pending", "selected_model_id": "fast"}]}
    }

insert_before：在指定任务之前插入新任务（目标任务状态须为 pending；插入的条目全部为 completed 时也可为 in_progress）：
    {
        "action": "insert_before",
        "todo_data": {"target_id": "analyze_code", "items": [{"id": "setup_env", "content": "插入的任务", "activeForm": "执行插入的任务", "description": "任务的详细描述", "status": "pending"}]}
    }

核心规则：
- update 操作：id 字段不可修改，其他字段支持部分更新
- 同一时间只能有一个任务处于 in_progress
- insert_after：目标任务状态必须为 completed、in_progress 或 pending
- insert_before：目标任务状态必须为 pending；插入的条目全部为 completed 时，目标也可以是 in_progress
- 保存时清单按 completed/cancelled、in_progress、pending 的顺序排列，同一状态内保持原有顺序
- 如果任务的 selected_model_id 为空时，任何操作都不要更改 selected_model_id 字段
"""

TODO_MODIFY_DESCRIPTION_EN = """
Modify todo items for the current session, including: update, delete, cancel, append, insert_after, and insert_before.

Important notes:
- Use this tool (append, insert_after, insert_before, cancel) to add, split, or cancel individual steps in the existing list, leaving completed items untouched; call todo_create to rebuild only when most items are being replaced
- Keep the list in execution order: insert a new step before the existing pending step it should precede (for example before review or delivery) with insert_before; use append only for steps that run after all existing ones
- Batch structural changes that occur together. Update statuses promptly; if multiple completed items were left unrecorded, verify them and record them in one update. Never mark unfinished work or work awaiting required verification completed
- **Use the semantic id you assigned in todo_create** (e.g. "translate_doc"); never use UUIDs
- **Only send the fields that changed**: for update, each call only needs the id plus the fields that actually changed (usually just status) — do not resend unchanged fields like content, description, or activeForm

Supported action types:

update: Modify status or content of existing tasks (id cannot be changed; only send the fields that changed, omit the rest):
    {
        "action": "update",
        "todos": [
            {"id": "translate_doc", "status": "completed"},
            {"id": "analyze_code", "status": "in_progress"}
        ]
    }

Support modifying selected_model_id: If the task's selected_model_id is not empty and the execution result is of poor quality (inaccurate output, logical errors, or failure to meet expectations), the model ID should be updated according to the model description to a higher-quality model:
    {
        "action": "update",
        "todos": [
            {"id": "translate_doc", "selected_model_id": "smart", "status": "pending"}
        ]
    }

cancel: Mark specified tasks as cancelled (tasks will be ignored and not executed):
    {
        "action": "cancel",
        "ids": ["translate_doc", "analyze_code"]
    }

delete: Permanently remove specified tasks from the list:
    {
        "action": "delete",
        "ids": ["translate_doc"]
    }

append: Add new tasks at the end of the list (id must be a short semantic string you choose, unique within the session):
    {
        "action": "append",
        "todos": [
            {"id": "write_report", "content": "New task content", "activeForm": "Executing new task", "description": "Detailed description of the task", "status": "pending"}
        ]
    }

insert_after: Insert new tasks after the specified task (target must be completed, in_progress, or pending):
    {
        "action": "insert_after",
        "todo_data": {"target_id": "translate_doc", "items": [{"id": "review_translation", "content": "Inserted task", "activeForm": "Executing inserted task", "description": "Detailed description of the task", "status": "pending", "selected_model_id": "fast"}]}
    }

insert_before: Insert new tasks before the specified task (target must be pending, or in_progress when every inserted item is completed):
    {
        "action": "insert_before",
        "todo_data": {"target_id": "analyze_code", "items": [{"id": "setup_env", "content": "Inserted task", "activeForm": "Executing inserted task", "description": "Detailed description of the task", "status": "pending"}]}
    }

Core rules:
- update: the id field cannot be modified; all other fields support partial updates
- Only one task can be in_progress at a time
- insert_after: target task status must be completed, in_progress, or pending
- insert_before: target task status must be pending; when every inserted item is completed, the target may also be in_progress
- On save the list is ordered completed/cancelled, then in_progress, then pending, keeping the existing order within each status
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
            "- id：任务唯一标识符，由你自行指定，必须简短且语义清晰（如 \"translate_doc\"、\"analyze_code\"），"
            "禁止使用随机字符或 UUID；同一会话内 ID 不得重复；后续 todo_modify 按此 ID 精准定位任务\n"
            "- content：任务摘要描述\n"
            "- activeForm：content 的进行语态（如 content 为「翻译文档」，activeForm 为「正在翻译文档」）\n"
            "- description：任务详细内容\n"
            "可选字段：\n"
            "- selected_model_id：执行任务的模型 ID，见系统提示词「模型选择策略」"
        ),
        "en": (
            "List of subtasks in JSON array format. Each element is a task object with required fields:\n"
            "- id: unique task identifier that YOU provide; must be short and semantically meaningful "
            "(e.g. 'translate_doc', 'analyze_code'); do NOT use random chars or UUIDs; "
            "IDs must be unique within a session; todo_modify uses this ID to locate tasks precisely\n"
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
    "id": {
        "cn": "任务唯一标识符，由你自行指定的简短语义字符串（如 \"translate_doc\"），禁止使用 UUID，同一会话内须唯一",
        "en": (
            "Unique task identifier — a short semantic string you provide (e.g. 'translate_doc'); "
            "do NOT use UUIDs; must be unique within a session"
        ),
    },
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
    tasks_desc += "\n" + _status_parameter_rule(language, False)
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
                        "id": item_props["id"],
                        "content": item_props["content"],
                        "activeForm": item_props["activeForm"],
                        "description": item_props["description"],
                        "status": item_props["status"],
                        "selected_model_id": item_props["selected_model_id"],
                    },
                    "required": ["id", "content", "activeForm", "description"],
                },
            },
        },
        "required": ["tasks"],
    }


def _status_parameter_rule(language: str, required: bool) -> str:
    return _STATUS_PARAMETER_RULES[_status_lang(language)][required]


def _move_status_to_required_fields(description: str, lang: str) -> str:
    """Drop the optional status entry and list status among the required fields."""
    lines = [line for line in description.split("\n") if line != _status_parameter_rule(lang, False)]
    required = _status_parameter_rule(lang, True)
    if required not in lines:
        header = _OPTIONAL_FIELDS_HEADER[lang]
        if header in lines:
            lines.insert(lines.index(header), required)
        else:
            lines.append(required)
    return "\n".join(lines).strip()


def require_todo_create_status(card: ToolCard, language: str) -> None:
    """Make status required on a built todo_create card.

    The card may come from a registered provider, so only the built-in status rules are
    replaced; other provider text and schema constraints are kept.
    """
    lang = _status_lang(language)
    optional = _TODO_CREATE_OPTIONAL_STATUS_RULES[lang]
    required = _TODO_CREATE_REQUIRED_STATUS_RULES[lang]
    if optional in card.description:
        card.description = card.description.replace(optional, required)
    else:
        card.description += "\n" + required
    schema = deepcopy(card.input_params)
    tasks = schema["properties"]["tasks"]
    tasks["description"] = _move_status_to_required_fields(tasks.get("description", ""), lang)
    item = tasks["items"]
    status = item["properties"].setdefault("status", {})
    standard = _todo_item_properties(lang)["status"]
    status.setdefault("type", standard["type"])
    status.setdefault("description", standard["description"])
    status["enum"] = [value for value in status.get("enum", standard["enum"]) if value in standard["enum"]]
    required_fields = item.setdefault("required", [])
    if "status" not in required_fields:
        required_fields.append("status")
    card.input_params = schema


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
        return get_todo_create_description(language)

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
