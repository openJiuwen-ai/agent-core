# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""权限引擎 - 核心权限控制模块.

职责:
  1. 加载 / 热更新 permissions 配置
  2. 评估工具调用权限 (allow / ask / deny)

审批流程由 rail 处理，引擎本身只负责权限判定。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, cast

from openjiuwen.harness.security.checker import ExternalDirectoryChecker
from openjiuwen.harness.security.models import PermissionsSection
from openjiuwen.harness.security.models import (
    PermissionLevel,
    PermissionResult,
)
from openjiuwen.harness.security.tiered_policy import (
    evaluate_tiered_policy,
    matched_rule_uses_approval_override,
    maybe_escalate_shell_operators,
    strictest as tiered_policy_strictest,
)

logger = logging.getLogger(__name__)


class PermissionEngine:
    """权限引擎 - 负责加载配置、评估权限."""

    def __init__(
        self,
        config: PermissionsSection | dict[str, Any] | None = None,
        llm: Any = None,
        model_name: str | None = None,
        workspace_root: Path | None = None,
    ):
        # 运行时为可变 dict；TypedDict 仅作入参形状说明
        self.config: dict[str, Any] = cast(dict[str, Any], config or {})
        self._enabled = self.config.get("enabled", True)
        self._permission_checks_active: Callable[[], bool] | None = None
        self._llm = llm
        self._model_name = model_name
        self._workspace_root = workspace_root
        self._external_checker = ExternalDirectoryChecker(
            self.config, workspace_root=self._workspace_root
        )

    # ---------- 配置 ----------

    def update_config(self, config: PermissionsSection | dict[str, Any]) -> None:
        """热更新配置."""
        self.config = cast(dict[str, Any], config)
        self._enabled = config.get("enabled", True)
        self._external_checker = ExternalDirectoryChecker(
            config, workspace_root=self._workspace_root
        )

    def update_llm(self, llm: Any, model_name: str | None) -> None:
        """保留接口供 PermissionInterruptRail 等热更新模型（当前不用于权限路径）。"""
        self._llm = llm
        self._model_name = model_name

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_permission_checks_active(self, fn: Callable[[], bool] | None) -> None:
        """由宿主 / :class:`PermissionInterruptRail` 注入：当前上下文是否应执行工具权限校验。"""
        self._permission_checks_active = fn

    def check_tool_permission_directly(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> tuple[PermissionLevel | None, str | None]:
        """直接检查工具权限，不受 enabled 开关与宿主「是否校验」短路影响.

        用于 owner_scopes 等需要获取原始权限级别的场景。

        Returns:
            (permission_level, matched_rule) - 权限级别可能为 None（无匹配规则）.
        """
        return self.evaluate_global_policy_directly(tool_name, tool_args)

    def evaluate_global_policy_directly(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        *,
        include_external_directory: bool = True,
    ) -> tuple[PermissionLevel | None, str | None]:
        """直接评估全局权限，不受 enabled 与宿主「是否校验」短路影响。"""
        if not isinstance(tool_args, dict):
            logger.warning(
                "[PermissionEngine] direct tool_args is not a dict (type=%s), using {}",
                type(tool_args).__name__,
            )
            tool_args = {}

        matched_rule: str | None = None
        permission, matched_rule = evaluate_tiered_policy(self.config, tool_name, tool_args)
        if matched_rule == "tiered_policy:fallback(no_config)":
            permission = None
            matched_rule = None
        elif not matched_rule_uses_approval_override(matched_rule):
            permission = maybe_escalate_shell_operators(tool_name, tool_args, permission)

        if include_external_directory:
            ext_result = self._external_checker.check_external_paths(tool_name, tool_args)
            if ext_result is not None:
                if permission is None:
                    permission = ext_result.permission
                    matched_rule = ext_result.matched_rule or "external_directory"
                else:
                    permission = tiered_policy_strictest(permission, ext_result.permission)
                    matched_rule = f"{matched_rule}|{ext_result.matched_rule or 'external_directory'}"

        return permission, matched_rule

    # ---------- 权限检查 ----------

    async def check_permission(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> PermissionResult:
        """检查工具调用权限.

        Returns:
            PermissionResult 包含权限级别和匹配规则.
        """
        logger.info(
            "[PermissionEngine] permission.check.start tool=%s enabled=%s",
            tool_name,
            self._enabled,
        )

        if not self._enabled:
            logger.info("[PermissionEngine] permission.check.skip reason=system_disabled decision=allow")
            return PermissionResult(
                permission=PermissionLevel.ALLOW,
                reason="Permission system is disabled",
            )

        active_fn = self._permission_checks_active
        if active_fn is not None and not active_fn():
            logger.info(
                "[PermissionEngine] permission.check.skip reason=permission_checks_inactive decision=allow",
            )
            return PermissionResult(
                permission=PermissionLevel.ALLOW,
                reason="Tool permission checks are inactive for this context",
            )

        if not isinstance(tool_args, dict):
            logger.warning(
                "[PermissionEngine] tool_args is not a dict (type=%s), using {}",
                type(tool_args).__name__,
            )
            tool_args = {}

        # 1. 工具级 + 参数规则 + 默认（分层策略 evaluate_tiered_policy）
        external_paths: list[str] | None = None
        permission, matched_rule = self.evaluate_global_policy_directly(
            tool_name,
            tool_args,
            include_external_directory=False,
        )
        if permission is None:
            permission = PermissionLevel.ASK
            matched_rule = "default"
        logger.info(
            "[PermissionEngine] permission.policy.result tool=%s permission=%s matched_rule=%s",
            tool_name,
            permission.value, matched_rule,
        )

        # 2. 外部路径：与当前决策取更严（不放宽）
        ext_result = self._external_checker.check_external_paths(tool_name, tool_args)
        if ext_result is not None:
            logger.info(
                "[PermissionEngine] permission.external.result tool=%s checked=true permission=%s "
                "matched_rule=%s external_paths=%s merged_with=%s",
                tool_name,
                ext_result.permission.value,
                ext_result.matched_rule or "external_directory",
                ext_result.external_paths,
                permission.value,
            )
            permission = tiered_policy_strictest(permission, ext_result.permission)
            matched_rule = f"{matched_rule}|{ext_result.matched_rule or 'external_directory'}"
            external_paths = ext_result.external_paths
        else:
            logger.info(
                "[PermissionEngine] permission.external.result tool=%s checked=true permission=none "
                "matched_rule=none external_paths=[]",
                tool_name,
            )

        result = PermissionResult(
            permission=permission,
            matched_rule=matched_rule,
            reason=self._get_reason(permission, tool_name, matched_rule),
            external_paths=external_paths,
        )

        logger.info(
            "[PermissionEngine] permission.check.final tool=%s permission=%s matched_rule=%s "
            "external_paths=%s",
            tool_name,
            permission.value,
            matched_rule,
            external_paths or [],
        )
        return result

    # ---------- 辅助 ----------

    @staticmethod
    def _get_reason(
        permission: PermissionLevel, tool_name: str, matched_rule: str
    ) -> str:
        if permission == PermissionLevel.ALLOW:
            return f"Allowed by rule: {matched_rule}"
        if permission == PermissionLevel.DENY:
            return f"Denied by rule: {matched_rule}"
        return f"Approval required for {tool_name} (rule: {matched_rule})"
