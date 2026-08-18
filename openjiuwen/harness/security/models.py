# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""权限系统数据模型与 ``permissions`` 配置结构（TypedDict）。

运行时代码仍以 ``dict[str, Any]`` 承载 YAML/JSON 中的 ``permissions``；TypedDict 仅用于静态检查与文档。

典型挂载：:attr:`openjiuwen.harness.schema.config.DeepAgentConfig.permissions`、
:func:`openjiuwen.harness.security.factory.build_permission_interrupt_rail` 的 ``permissions``、
引擎 ``PermissionEngine.config`` 等。工具级策略由 ``tiered_policy.evaluate_tiered_policy`` 评估。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, NotRequired, TypedDict


class PermissionLevel(str, Enum):
    """权限级别.

    - ALLOW: 直接执行，无需确认
    - ASK:   弹出确认框，用户决定
    - DENY:  拒绝执行，返回错误
    """
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class PermissionResult:
    permission: PermissionLevel
    matched_rule: str | None = None
    reason: str | None = None
    external_paths: list[str] | None = None

    @property
    def is_allowed(self) -> bool:
        return self.permission == PermissionLevel.ALLOW

    @property
    def is_denied(self) -> bool:
        return self.permission == PermissionLevel.DENY

    @property
    def needs_approval(self) -> bool:
        return self.permission == PermissionLevel.ASK


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
    """``permissions.approval_overrides`` 中单条用户/CLI 覆盖。

    ``match_type`` 表示 ``pattern`` 作用在哪种输入上（如 ``path`` 对路径参数、``command`` 对命令
    文本）；``pattern`` 则是该维度上的具体表达式（``re:…`` 正则或路径/通配写法），二者分工不同。

    引擎匹配与合并仅依赖上述字段；历史 YAML 中若存在其它键（如旧版 ``source``），会被忽略。
    """

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


class PermissionsSection(TypedDict, total=False):
    """与 agent YAML 中 ``permissions:`` 段落常见字段对齐的结构说明。

    常见键包括 ``tools``、``defaults``、``rules``、``approval_overrides``、
    ``file_guard``、``external_directory``（deprecated）等。

    - 工具级策略由 :func:`openjiuwen.harness.security.tiered_policy.evaluate_tiered_policy` 评估。
    - 路径防护由 :mod:`openjiuwen.harness.security.file_guard` 评估（可独立关闭）。

    ``schema``（可选）：建议写 ``tiered_policy`` 等，便于人类阅读或与旧文档对齐；
    引擎**不**根据该字段切换实现路径。例如::

        permissions:
          enabled: true
          schema: tiered_policy
          tools:
            read_file: ask
          file_guard:
            enabled: true
            defaults: {read: ask, write: ask, exec: ask}
            paths:
              - path: "/data/public"
                read: allow
                write: ask
                exec: deny
          # deprecated：加载期投影进 file_guard（Legacy）
          external_directory:
            "*": ask

    其它键（例如产品层在 ``permissions`` 下自用的配置）可继续出现在 YAML 中；本 TypedDict
    不枚举 harness 之外的扩展字段。
    """

    enabled: bool
    schema: NotRequired[str]
    defaults: NotRequired[dict[str, Any]]
    tools: NotRequired[dict[str, Any]]
    rules: NotRequired[list[dict[str, Any]]]
    approval_overrides: NotRequired[list[ApprovalOverrideEntry]]
    file_guard: NotRequired[FileGuardSection]
    external_directory: NotRequired[dict[str, str]]  # deprecated → file_guard


__all__ = [
    "ApprovalOverrideEntry",
    "FileGuardDefaults",
    "FileGuardPathEntry",
    "FileGuardSection",
    "PermissionConfirmResponse",
    "PermissionLevel",
    "PermissionResult",
    "PermissionsSection",
]
