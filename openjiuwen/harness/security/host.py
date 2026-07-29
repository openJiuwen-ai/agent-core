# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""宿主注入：权限快照、宿主侧确认、持久化与工作区路径。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from openjiuwen.harness.security.models import PermissionConfirmResponse, PermissionResult


@dataclass(frozen=True)
class PermissionSceneHookInput:
    """传给 :data:`PermissionSceneHook` 的单一入参（避免一长串位置参数）。"""

    ctx: Any
    tool_call: Any
    user_input: Any
    normalized_tool_name: str
    tool_args: dict[str, Any]
    engine: Any


PermissionSceneHook = Callable[
    [PermissionSceneHookInput],
    Awaitable[tuple[str, ...] | None],
]
"""宿主场景：在通用 tiered 判定前介入（如数字分身 / owner_scopes）。
返回 ``None`` 表示继续走引擎 tiered 判定；
返回 ``("approve",)`` 直接放行；``("reject", msg)`` 拒绝并附带 ``tool_result``。
"""


@dataclass(frozen=True)
class PermissionConfirmationRequest:
    """传给 :data:`RequestPermissionConfirmationHook` 的单一入参。"""

    ctx: Any
    tool_call: Any
    result: PermissionResult
    auto_confirm_key: str


PermissionConfirmationResult = PermissionConfirmResponse | Literal["interrupt"] | None

RequestPermissionConfirmationHook = Callable[
    [PermissionConfirmationRequest],
    Awaitable[PermissionConfirmationResult],
]
"""对 ``PermissionLevel.ASK`` 征求用户确认。

返回
  - :class:`~openjiuwen.harness.security.models.PermissionConfirmResponse`：
    与内置中断恢复相同语义；``approved`` + ``auto_confirm`` 表示「记住」并持久化策略，仅 ``approved`` 为本次放行；
  - 字面量 ``\"interrupt\"``：不使用宿主确认，回退到内置 ``ConfirmInterrupt`` 流程；
  - ``None``：宿主确认失败，工具调用将被拒绝。
"""


@dataclass
class ToolPermissionHost:
    """由 Agent 服务或 CLI 在构造 DeepAgent / PermissionInterruptRail 时注入。"""

    get_permissions_snapshot: Callable[[], dict[str, Any]] | None = None
    """返回与 ``config['permissions']`` 同结构的 dict，用于热同步磁盘配置。"""

    persist_allow_rule: Callable[[dict[str, Any]], bool] | None = None
    """自定义「总是允许」写盘；入参为护栏已合并好的整份 ``permissions`` dict（与默认 YAML
    路径下内存中的结果一致，含 ``external_directory`` 等）。

    调用顺序：护栏先 ``merge_permission_allow_rule_into_permissions``、按需
    ``merge_external_directory_allow_into_permissions``，再 ``update_config(merged)``，
    最后调用本回调；返回 ``False`` 时护栏会回滚内存配置。未设置本回调时则使用
    :func:`openjiuwen.harness.security.patterns.write_permissions_section_to_agent_config_yaml`
    写入 ``permission_yaml_path``。
    """

    resolve_workspace_dir: Callable[[], Path] | None = None
    """外部路径校验用的 workspace 根目录。"""

    permission_yaml_path: Path | None = None
    """Agent 配置文件路径；权限段写盘时优先使用（文件可尚不存在，父目录须存在）。

    未设置时，内置 ``write_permissions_section_to_agent_config_yaml`` 无法解析 YAML 路径。
    """

    tool_permission_checks_active: Callable[[], bool] | None = None
    """若返回假则跳过工具权限校验（等价放行）；未设置则始终执行校验。

    产品层可注入（例如按入口/会话类型决定是否走权限引擎）。
    """

    request_permission_confirmation: RequestPermissionConfirmationHook | None = None
    """见 :data:`RequestPermissionConfirmationHook`；未设置时 ASK 一律走内置中断确认。"""

    permission_scene_hook: PermissionSceneHook | None = None
    """宿主场景钩子（如数字分身）；见 :data:`PermissionSceneHook`。"""


__all__ = [
    "PermissionConfirmationRequest",
    "PermissionConfirmationResult",
    "PermissionSceneHook",
    "PermissionSceneHookInput",
    "RequestPermissionConfirmationHook",
    "ToolPermissionHost",
]
