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
from typing import Any, Callable

from openjiuwen.harness.security.checker import (
    ExternalDirectoryChecker,
    ToolPermissionChecker,
)
from openjiuwen.harness.security.models import (
    PermissionLevel,
    PermissionResult,
)
from openjiuwen.harness.security.tiered_policy import (
    evaluate_tiered_policy,
    matched_rule_uses_approval_override,
    maybe_escalate_shell_operators,
    permissions_schema_is_tiered_policy,
    strictest as tiered_policy_strictest,
)

logger = logging.getLogger(__name__)


class PermissionEngine:
    """权限引擎 - 负责加载配置、评估权限."""

    def __init__(
        self,
        config: dict | None = None,
        llm: Any = None,
        model_name: str | None = None,
        workspace_root: Path | None = None,
    ):
        self.config = config or {}
        self._enabled = self.config.get("enabled", True)
        self._channel_enforce: Callable[[str], bool] | None = None
        self._llm = llm
        self._model_name = model_name
        self._workspace_root = workspace_root
        self._tool_checker = ToolPermissionChecker(self.config)
        self._external_checker = ExternalDirectoryChecker(
            self.config, workspace_root=self._workspace_root
        )

    # ---------- 配置 ----------

    def update_config(self, config: dict):
        """热更新配置."""
        self.config = config
        self._enabled = config.get("enabled", True)
        self._tool_checker = ToolPermissionChecker(config)
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

    def set_channel_enforce(self, enforce: Callable[[str], bool] | None) -> None:
        """由宿主 / :class:`PermissionInterruptRail` 注入：是否对某 ``channel_id`` 执行权限校验。"""
        self._channel_enforce = enforce

    def check_tool_permission_directly(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        channel_id: str = "web",
    ) -> tuple[PermissionLevel | None, str | None]:
        """直接检查工具权限，不受 enabled 开关和 channel 限制.

        用于 owner_scopes 等需要获取原始权限级别的场景。

        Returns:
            (permission_level, matched_rule) - 权限级别可能为 None（无匹配规则）.
        """
        return self.evaluate_global_policy_directly(tool_name, tool_args, channel_id)

    def evaluate_global_policy_directly(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        channel_id: str = "web",
        *,
        include_external_directory: bool = True,
    ) -> tuple[PermissionLevel | None, str | None]:
        """直接评估全局权限，不受 enabled/channel 短路影响。"""
        if not isinstance(tool_args, dict):
            logger.warning(
                "[PermissionEngine] direct tool_args is not a dict (type=%s), using {}",
                type(tool_args).__name__,
            )
            tool_args = {}

        matched_rule: str | None = None
        if permissions_schema_is_tiered_policy(self.config):
            permission, matched_rule = evaluate_tiered_policy(self.config, tool_name, tool_args)
            if matched_rule == "tiered_policy:fallback(no_config)":
                permission = None
                matched_rule = None
            elif not matched_rule_uses_approval_override(matched_rule):
                permission = maybe_escalate_shell_operators(tool_name, tool_args, permission)
        else:
            permission, matched_rule = self._tool_checker.check_tool(tool_name, tool_args, channel_id)

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
        channel_id: str = "web",
        session_id: str | None = None,
    ) -> PermissionResult:
        """检查工具调用权限.

        Returns:
            PermissionResult 包含权限级别和匹配规则.
        """
        logger.info(
            "[PermissionEngine] permission.check.start tool=%s channel=%s enabled=%s",
            tool_name, channel_id, self._enabled,
        )

        if not self._enabled:
            logger.info("[PermissionEngine] permission.check.skip reason=system_disabled decision=allow")
            return PermissionResult(
                permission=PermissionLevel.ALLOW,
                reason="Permission system is disabled",
            )

        normalized_channel = (channel_id or "").strip() or "web"
        enforce = self._channel_enforce
        # 未注入 ``set_channel_enforce`` 时不在 harness 内按通道白名单短路；宿主（如 jiuwenclaw）可注入谓词。
        channel_ok = enforce(normalized_channel) if enforce is not None else True
        if not channel_ok:
            logger.info(
                "[PermissionEngine] permission.check.skip reason=channel_disabled channel=%s",
                normalized_channel,
            )
            return PermissionResult(
                permission=PermissionLevel.ALLOW,
                reason=f"Skipped for channel: {normalized_channel}",
            )

        if not isinstance(tool_args, dict):
            logger.warning(
                "[PermissionEngine] tool_args is not a dict (type=%s), using {}",
                type(tool_args).__name__,
            )
            tool_args = {}

        # 1. 工具级 + 参数规则 + 默认（legacy 或 tiered_policy）
        external_paths: list[str] | None = None
        permission, matched_rule = self.evaluate_global_policy_directly(
            tool_name,
            tool_args,
            channel_id,
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
            risk=None,
            external_paths=external_paths,
        )

        logger.info(
            "[PermissionEngine] permission.check.final tool=%s channel=%s permission=%s matched_rule=%s "
            "external_paths=%s",
            tool_name,
            channel_id,
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


# ----- 全局单例 -----
_permission_engine: PermissionEngine | None = None


def init_permission_engine(config: dict | None = None) -> PermissionEngine:
    """初始化全局权限引擎."""
    global _permission_engine
    if _permission_engine is None:
        _permission_engine = PermissionEngine(config)
    if config is not None:
        _permission_engine.update_config(config)
    return _permission_engine


def get_permission_engine() -> PermissionEngine:
    """获取全局权限引擎实例 (懒初始化)."""
    global _permission_engine
    if _permission_engine is None:
        _permission_engine = PermissionEngine()
    return _permission_engine


def set_permission_engine(engine: PermissionEngine):
    """替换全局权限引擎 (测试用)."""
    global _permission_engine
    _permission_engine = engine
