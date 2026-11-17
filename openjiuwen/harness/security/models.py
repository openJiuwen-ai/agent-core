# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""权限系统数据模型与 ``permissions`` 配置结构（TypedDict）。

运行时代码仍以 ``dict[str, Any]`` 承载 YAML/JSON 中的 ``permissions``；TypedDict 仅用于静态检查与文档。

典型挂载：:attr:`openjiuwen.harness.schema.config.DeepAgentConfig.permissions`、
:func:`openjiuwen.harness.security.factory.build_permission_interrupt_rail` 的 ``permissions`` 等。
实际判定由 agent-security 的 ``PermissionManager`` 负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict

from agent_shield.tool_permission import PermissionLevel, PermissionResult

# 判定结果类型由 agent_shield 定义；这里转出，保持原有导入路径可用。


@dataclass(frozen=True)
class PermissionConfirmResponse:
    """工具权限 ASK 场景下用户对「允许一次 / 会话内记住 / 永久记住 / 拒绝」的确认结果。

    - ``approved and auto_confirm and persist_allow``：永久记住，走合并 ``permissions``、
      更新内存并写盘的路径（与 ``PermissionInterruptRail._persist_allow_always`` 一致）。
    - ``approved and auto_confirm and not persist_allow``：会话内记住，仅写入 session state
      的 ``__interrupt_auto_confirm__``，不写磁盘。
    - ``approved and not auto_confirm``：仅本次放行。
    - ``not approved``：拒绝。
    """

    approved: bool
    feedback: str = ""
    auto_confirm: bool = False
    persist_allow: bool = False


class ApprovalOverrideEntry(TypedDict, total=False):
    """``permissions.approval_overrides`` 中单条用户/CLI 覆盖。"""

    id: str
    tools: list[str]
    match_type: str
    pattern: str
    action: str


class FileGuardPathEntry(TypedDict, total=False):
    """``permissions.file_guard.paths[]`` 单条路径策略。"""

    path: str
    read: str
    write: str
    exec: str
    match: NotRequired[str]  # ``prefix``（缺省）| ``glob``


class FileGuardDefaults(TypedDict, total=False):
    """未命中 ``paths`` 时的默认动作（建议 ask，避免静默放行）。"""

    read: str
    write: str
    exec: str


class FileGuardSection(TypedDict, total=False):
    """路径防护配置（Pipeline B），可独立于工具权限开关。"""

    enabled: bool
    defaults: NotRequired[FileGuardDefaults]
    paths: NotRequired[list[FileGuardPathEntry]]


class SoftDeleteSection(TypedDict, total=False):
    """Windows 软删除与批量删除审批。只有布尔 ``enabled: true`` 才开启。"""

    enabled: bool
    archive_dir: NotRequired[str]
    batch_delete_threshold: NotRequired[int]


class PermissionsSection(TypedDict, total=False):
    """与 agent YAML 中 ``permissions:`` 段落常见字段对齐的结构说明。

    工具级策略与路径防护均由 agent-security 的 ``PermissionManager`` 评估。
    ``schema`` 仅用于说明配置，不用于切换实现路径。
    """

    enabled: bool
    schema: NotRequired[str]
    defaults: NotRequired[dict[str, Any]]
    tools: NotRequired[dict[str, Any]]
    rules: NotRequired[list[dict[str, Any]]]
    approval_overrides: NotRequired[list[ApprovalOverrideEntry]]
    file_guard: NotRequired[FileGuardSection]
    soft_delete: NotRequired[SoftDeleteSection]
    external_directory: NotRequired[dict[str, str]]  # deprecated → file_guard


__all__ = [
    "ApprovalOverrideEntry",
    "FileGuardDefaults",
    "FileGuardPathEntry",
    "FileGuardSection",
    "SoftDeleteSection",
    "PermissionConfirmResponse",
    "PermissionLevel",
    "PermissionResult",
    "PermissionsSection",
]
