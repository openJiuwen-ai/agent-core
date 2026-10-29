# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Skill 加载/结束事件提取，供 SkillAuthorizationRail 与 SkillComplianceRail 共享。

dev-stable 适配说明（相对 0708）：

- agent-core 无 ``openjiuwen.core.context_engine.active_skill_bodies``，
  ``relative_file_path`` 归一化固定使用本模块的本地实现；
- dev-stable 的 skill_tool 不写 ``is_skill_body`` metadata，且其只允许读取
  ``SKILL.md`` 文件（含嵌套 ``sub/SKILL.md``），因此 ``is_skill_body`` 改为派生
  信号：tool 调用成功即视为返回了 skill 正文。成功判定优先读
  ``tool_result.success``（agent-core ``ToolCallInputs.tool_result`` 在
  after_tool_call 前已填充），缺失时回退 tool_msg 正文的已知错误前缀启发式。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from openjiuwen.harness.security.skill_authorization.schema import (
    is_skill_authorization_enabled,
)

ROOT_SKILL_FILE = "SKILL.md"
SKILL_TOOL_NAME = "skill_tool"
SKILL_COMPLETE_TOOL_NAME = "skill_complete"
SKILL_AUTHORIZATION_GATE_HANDLED_KEY = "_skill_authorization_gate_handled"

#: dev-stable skill_tool 的失败返回值前缀（agent-core ``SkillTool.invoke`` 的
#: ``ToolOutput(success=False, error=...)`` 经 ``_build_tool_message_content``
#: 原样写入 tool_msg.content）。仅作 ``tool_result`` 缺失时的回退启发式。
_SKILL_TOOL_ERROR_PREFIXES = (
    "Skill not found",
    "Invalid relative_file_path",
    "skill_tool only supports",
)


def _normalize_rel_path(relative_file_path: str) -> str:
    raw = (relative_file_path or "").strip()
    if not raw:
        return ROOT_SKILL_FILE
    p = raw.replace("\\", "/").removeprefix("./")
    if "/" in p:
        prefix, base = p.rsplit("/", 1)
    else:
        prefix, base = "", p
    if "." not in base and base.casefold() == "skill":
        return f"{prefix}/SKILL.md" if prefix else ROOT_SKILL_FILE
    return raw


def normalize_relative_file_path(relative_file_path: Any) -> str:
    """规范化 ``relative_file_path``；空值按 ``SKILL.md`` 处理（与 skill_tool 一致）。"""
    return _normalize_rel_path(str(relative_file_path or ""))


def parse_tool_call_arguments(tool_call: Any) -> dict[str, Any]:
    """解析 ``tool_call.arguments``（dict 或 JSON 字符串），失败返回 ``{}``。"""
    args = getattr(tool_call, "arguments", None)
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except Exception:  # noqa: BLE001
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _tool_call_succeeded(tool_msg: Any, tool_result: Any = None) -> bool:
    """判定 tool 调用是否成功（dev-stable 替代 0708 的 ``is_skill_body`` metadata）。

    优先使用 ``tool_result.success``（权威信号）；缺失时回退 tool_msg 正文的
    已知错误前缀启发式，无法判定时按失败处理（fail-closed）。
    """
    success = getattr(tool_result, "success", None)
    if isinstance(success, bool):
        return success
    content = getattr(tool_msg, "content", None)
    if not isinstance(content, str):
        return False
    stripped = content.strip()
    return not stripped.startswith(_SKILL_TOOL_ERROR_PREFIXES)


@dataclass(frozen=True)
class SkillLifecycleEvent:
    """一次 skill 相关工具调用的规范化事件视图。"""

    tool_name: str
    skill_name: str
    relative_file_path: str
    is_skill_body: bool
    tool_call_id: str = ""


def _extract_skill_name(tool_call: Any, metadata: dict[str, Any]) -> str:
    meta_name = metadata.get("skill_name")
    if isinstance(meta_name, str) and meta_name.strip():
        return meta_name.strip()
    arg_name = parse_tool_call_arguments(tool_call).get("skill_name")
    return str(arg_name).strip() if arg_name else ""


def build_skill_lifecycle_event(
    tool_name: Any,
    tool_call: Any,
    tool_msg: Any,
    tool_result: Any = None,
) -> SkillLifecycleEvent | None:
    """从 ``(tool_name, tool_call, tool_msg[, tool_result])`` 构建事件；非 skill 工具返回 ``None``。"""
    name = str(tool_name or "").strip()
    if name not in (SKILL_TOOL_NAME, SKILL_COMPLETE_TOOL_NAME):
        return None
    metadata = getattr(tool_msg, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}

    rel_path = metadata.get("relative_file_path")
    if rel_path is None:
        rel_path = parse_tool_call_arguments(tool_call).get("relative_file_path")
    # skill_tool 仅允许读取 SKILL.md：调用成功即返回了 skill 正文。
    is_skill_body = name == SKILL_TOOL_NAME and _tool_call_succeeded(tool_msg, tool_result)
    return SkillLifecycleEvent(
        tool_name=name,
        skill_name=_extract_skill_name(tool_call, metadata),
        relative_file_path=normalize_relative_file_path(rel_path),
        is_skill_body=is_skill_body,
        tool_call_id=str(getattr(tool_call, "id", "") or ""),
    )


def extract_skill_lifecycle_event(ctx: Any) -> SkillLifecycleEvent | None:
    """从 ``after_tool_call`` 的 ctx 提取 skill 生命周期事件；非 skill 工具返回 ``None``。"""
    inputs = getattr(ctx, "inputs", None)
    if inputs is None:
        return None
    return build_skill_lifecycle_event(
        getattr(inputs, "tool_name", ""),
        getattr(inputs, "tool_call", None),
        getattr(inputs, "tool_msg", None),
        getattr(inputs, "tool_result", None),
    )


def is_root_skill_load(event: SkillLifecycleEvent | None) -> bool:
    """根 ``SKILL.md`` 加载成功事件（加载失败不带 skill body 标记，自然排除）。"""
    return (
        event is not None
        and event.tool_name == SKILL_TOOL_NAME
        and event.relative_file_path == ROOT_SKILL_FILE
        and event.is_skill_body
    )


def is_skill_complete(event: SkillLifecycleEvent | None) -> bool:
    return event is not None and event.tool_name == SKILL_COMPLETE_TOOL_NAME


# ---------- before_tool_call 侧判定 ----------


def is_root_skill_load_call(tool_name: str, tool_args: dict[str, Any] | None) -> bool:
    if (tool_name or "").strip() != SKILL_TOOL_NAME:
        return False
    rel = (tool_args or {}).get("relative_file_path")
    return normalize_relative_file_path(rel) == ROOT_SKILL_FILE


def is_skill_complete_call(tool_name: str) -> bool:
    return (tool_name or "").strip() == SKILL_COMPLETE_TOOL_NAME


def is_skill_authorization_gate_call(
    tool_name: str,
    tool_args: dict[str, Any] | None,
    permissions_config: Any,
) -> bool:
    """功能开关开启时，根 ``SKILL.md`` 加载与 ``skill_complete`` 归 Skill 门禁专属路由。

    供 ``SkillAuthorizationRail.before_tool_call`` 与 ``PermissionInterruptRail``
    短路分支共用同一份判定，确保两处路由边界永远一致。
    """
    if not is_skill_authorization_enabled(permissions_config):
        return False
    return is_root_skill_load_call(tool_name, tool_args) or is_skill_complete_call(tool_name)
