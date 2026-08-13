# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""权限引擎 - 核心权限控制模块.

职责:
  1. 加载 / 热更新 permissions 配置
  2. 评估工具调用权限 (allow / ask / deny)

审批流程由 rail 处理，引擎本身只负责权限判定。

判定管线::

    result_A = evaluate_tiered_policy(...)          # 工具权限（始终）
    result_B = FileGuardChecker.evaluate(...)       # 路径防护（file_guard.enabled）
    return strictest(result_A, result_B)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, cast

from openjiuwen.harness.security.file_guard import (
    FileGuardChecker,
    build_file_guard_checker,
)
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
        trusted_dirs: list[Path] | None = None,
    ):
        # 运行时为可变 dict；TypedDict 仅作入参形状说明
        self.config: dict[str, Any] = cast(dict[str, Any], config or {})
        self._enabled = self.config.get("enabled", True)
        self._permission_checks_active: Callable[[], bool] | None = None
        self._llm = llm
        self._model_name = model_name
        self._workspace_root = workspace_root
        self._trusted_dirs = trusted_dirs or []
        self._file_guard: FileGuardChecker | None = build_file_guard_checker(
            self.config,
            workspace_root=self._workspace_root,
            trusted_dirs=self._trusted_dirs,
        )

    # ---------- 配置 ----------

    def _rebuild_file_guard(self) -> None:
        self._file_guard = build_file_guard_checker(
            self.config,
            workspace_root=self._workspace_root,
            trusted_dirs=self._trusted_dirs,
        )

    def update_config(self, config: PermissionsSection | dict[str, Any]) -> None:
        """热更新配置."""
        self.config = cast(dict[str, Any], config)
        self._enabled = config.get("enabled", True)
        self._rebuild_file_guard()

    def update_trusted_dirs(self, trusted_dirs: list[Path]) -> None:
        """更新受信任目录列表."""
        self._trusted_dirs = trusted_dirs
        self._rebuild_file_guard()

    def update_workspace_root(self, workspace_root: Path | None) -> None:
        """热更新 file_guard 绑定的当前任务 workspace."""
        self._workspace_root = (
            Path(workspace_root).expanduser().resolve()
            if workspace_root is not None
            else None
        )
        self._rebuild_file_guard()

    @property
    def trusted_dirs(self) -> list[Path]:
        """获取当前受信任目录列表."""
        return self._trusted_dirs

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
        """直接评估全局权限，不受 enabled 与宿主「是否校验」短路影响。

        ``include_external_directory`` 保留旧参数名；实际控制是否合并路径防护
        （FileGuard / Legacy ExternalDirectory 投影）。
        """
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

        if include_external_directory and self._file_guard is not None:
            path_result = self._file_guard.evaluate(tool_name, tool_args)
            if path_result is not None:
                path_rule = path_result.matched_rule or "file_guard"
                if permission is None:
                    permission = path_result.permission
                    matched_rule = path_rule
                else:
                    permission = tiered_policy_strictest(permission, path_result.permission)
                    matched_rule = f"{matched_rule}|{path_rule}"

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

        # 1. Pipeline A：工具级 + 参数规则 + 默认
        from openjiuwen.harness.security.findings import (
            escalate_with_findings,
            findings_for_tool_call,
        )
        from openjiuwen.harness.security.network_guard import evaluate_network_guard

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

        # 1b. NetworkGuard（与 mode 相关；Full Access 默认放行）
        net_result = evaluate_network_guard(self.config, tool_name, tool_args)
        if net_result is not None:
            permission = tiered_policy_strictest(permission, net_result.permission)
            net_rule = net_result.matched_rule or "network_guard"
            matched_rule = f"{matched_rule}|{net_rule}"
            logger.info(
                "[PermissionEngine] permission.network_guard.result tool=%s permission=%s matched_rule=%s",
                tool_name,
                net_result.permission.value,
                net_rule,
            )

        # 2. Pipeline B：file_guard（可独立关闭；含 ExternalDirectory Legacy 投影）
        if self._file_guard is not None:
            path_result = self._file_guard.evaluate(tool_name, tool_args)
            if path_result is not None:
                path_rule = path_result.matched_rule or "file_guard"
                logger.info(
                    "[PermissionEngine] permission.file_guard.result tool=%s checked=true permission=%s "
                    "matched_rule=%s external_paths=%s merged_with=%s",
                    tool_name,
                    path_result.permission.value,
                    path_rule,
                    path_result.external_paths,
                    permission.value,
                )
                prev = permission
                permission = tiered_policy_strictest(permission, path_result.permission)
                # 展示决定最终级别的规则：一侧更严时只留该侧，同级才拼接。
                if path_result.permission == permission and prev != permission:
                    matched_rule = path_rule
                elif path_result.permission == permission and prev == permission:
                    if matched_rule and path_rule and matched_rule != path_rule:
                        matched_rule = f"{matched_rule}|{path_rule}"
                    else:
                        matched_rule = matched_rule or path_rule
                external_paths = path_result.external_paths
            else:
                logger.info(
                    "[PermissionEngine] permission.file_guard.result tool=%s checked=true permission=none "
                    "matched_rule=none external_paths=[]",
                    tool_name,
                )
        else:
            logger.info(
                "[PermissionEngine] permission.file_guard.result tool=%s checked=false reason=disabled",
                tool_name,
            )

        findings = findings_for_tool_call(tool_name, tool_args)
        mode = str(self.config.get("mode") or "auto")
        # 用户/会话已对整条 command 写入 approval_overrides 时，不再用 structure findings
        # 把 ALLOW 抬回 ASK（否则管道命令永远无法「记住后放行」）。
        if not matched_rule_uses_approval_override(matched_rule):
            permission = escalate_with_findings(permission, findings, mode=mode)

        result = PermissionResult(
            permission=permission,
            matched_rule=matched_rule,
            reason=self._get_reason(permission, tool_name, matched_rule),
            external_paths=external_paths,
            findings=list(findings) if findings else None,
        )

        logger.info(
            "[PermissionEngine] permission.check.final tool=%s permission=%s matched_rule=%s "
            "external_paths=%s findings=%d",
            tool_name,
            permission.value,
            matched_rule,
            external_paths or [],
            len(findings),
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
