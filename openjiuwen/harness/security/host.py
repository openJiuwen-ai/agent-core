# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""宿主注入：权限快照、ACP 审批、持久化与工作区路径。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable


PermissionSceneHook = Callable[
    [Any, Any, Any, str, dict[str, Any], str, Any],
    Awaitable[tuple[str, ...] | None],
]
"""宿主场景：在通用 tiered 判定前介入（如数字分身 / owner_scopes）。

返回 ``None`` 表示继续走引擎 tiered 判定；
返回 ``("approve",)`` 直接放行；``("reject", msg)`` 拒绝并附带 ``tool_result``。
"""


@dataclass
class ToolPermissionHost:
    """由 Agent 服务或 CLI 在构造 DeepAgent / PermissionInterruptRail 时注入。"""

    get_permissions_snapshot: Callable[[], dict[str, Any]] | None = None
    """返回与 ``config['permissions']`` 同结构的 dict，用于热同步磁盘配置。"""

    request_acp_permission: Callable[
        [str, dict[str, Any]],
        Awaitable[Any],
    ] | None = None
    """ACP：``(session_id, request_params) -> jsonrpc 风格 dict``。"""

    persist_allow_rule: Callable[[str, dict[str, Any]], bool] | None = None
    """「总是允许」落盘；未设置时回退到 :func:`patterns.persist_permission_allow_rule`。"""

    resolve_workspace_dir: Callable[[], Path] | None = None
    """外部路径校验用的 workspace 根目录。"""

    permission_yaml_path: Path | None = None
    """Agent 配置文件路径；``persist_*`` 写入时优先使用（文件可尚不存在，父目录须存在）。

    未设置且未注册 :func:`openjiuwen.harness.security.patterns.set_agent_config_yaml_path_provider` 时，
    默认持久化路径无法解析。
    """

    channel_permission_enforce: Callable[[str], bool] | None = None
    """若返回真则对该 ``channel_id`` 执行工具权限校验；否则跳过（等价放行）。

    未设置时 harness 不按通道短路，权限策略对所有 ``channel_id`` 生效。
    产品层（如 jiuwenclaw）可注入基于配置的白名单谓词。
    """

    permission_scene_hook: PermissionSceneHook | None = None
    """宿主场景钩子（如数字分身）；见 :data:`PermissionSceneHook`。"""


__all__ = ["PermissionSceneHook", "ToolPermissionHost"]
