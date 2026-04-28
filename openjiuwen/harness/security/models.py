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
    """工具权限 ASK 场景下用户对「允许一次 / 记住并写回策略 / 拒绝」的确认结果。

    ``approved`` 且 ``auto_confirm`` 时，护栏走合并 ``permissions``、更新内存并写盘的路径
    （与 ``PermissionInterruptRail._persist_allow_always`` 一致）；仅 ``approved`` 则为本次放行。
    """

    approved: bool
    feedback: str = ""
    auto_confirm: bool = False


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


class PermissionsSection(TypedDict, total=False):
    """与 agent YAML 中 ``permissions:`` 段落常见字段对齐的结构说明。

    常见键包括 ``tools``、``defaults``、``rules``、``approval_overrides``、
    ``external_directory`` 等；由 :func:`openjiuwen.harness.security.tiered_policy.evaluate_tiered_policy`
    统一解释。

    ``schema``（可选）：建议写 ``tiered_policy`` 等，便于人类阅读或与旧文档对齐；
    引擎**不**根据该字段切换实现路径。例如（与仓库
    ``examples/permissions/permission_demo.py`` 中结构相近）::

        permissions:
          enabled: true
          schema: tiered_policy
          permission_mode: normal
          tools:
            read_file: ask
            write_file: deny
          defaults:
            "*": allow
          rules:
            # 用户参数级规则：``action`` 直接为 allow / ask / deny；也可用 ``severity`` 由引擎映射
            # ``match_type: path`` = 对路径型参数取值做匹配；``pattern`` = 在该取值上要满足的正则/通配
            - id: deny_read_env_files
              tools: [read_file]
              match_type: path
              pattern: "re:\\.env(\\.local)?$"
              action: deny
          approval_overrides:
            # 用户/CLI 持久化覆盖形态与 :class:`ApprovalOverrideEntry` 一致
            - id: example_allow_git_status
              tools: [bash, mcp_exec_command, create_terminal]
              match_type: command
              pattern: "re:^git\\s+status\\s*$"
              action: allow
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
    external_directory: NotRequired[dict[str, str]]


__all__ = [
    "ApprovalOverrideEntry",
    "PermissionConfirmResponse",
    "PermissionLevel",
    "PermissionResult",
    "PermissionsSection",
]
