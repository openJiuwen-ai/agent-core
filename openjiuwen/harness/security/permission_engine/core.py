# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""权限引擎 - 核心权限控制模块.

职责:
  1. 加载 / 热更新 permissions 配置
  2. 评估工具调用权限 (allow / ask / deny)

审批流程由 rail 处理，引擎本身只负责权限判定。

判定管线::

    result_A = evaluate_tiered_policy(...)          # 工具权限（始终）
    result_B = FileGuardChecker.evaluate(...)       # 路径防护（file_guard.enabled）
    result_C = NetGuardChecker.evaluate(...)        # 网络防护（net_guard.enabled）
    return strictest(result_A, result_B, result_C)

旧宿主（未 compose）传入的 raw YAML 在 ingest 时补包内命令规则 / 敏感路径 / net_urls；
判定函数本身不 load YAML。
"""
from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast

from openjiuwen.harness.security.permission_engine.fileguard.file_guard import (
    FileGuardChecker,
    build_file_guard_checker,
)
from openjiuwen.harness.security.permission_engine.fileguard.sensitive_paths import (
    merge_package_sensitive_paths,
)
from openjiuwen.harness.security.permission_engine.host import ToolPermissionHost
from openjiuwen.harness.security.permission_engine.models import PermissionsSection
from openjiuwen.harness.security.permission_engine.models import (
    PermissionLevel,
    PermissionResult,
)
from openjiuwen.harness.security.permission_engine.netguard.net_guard import (
    NetGuardChecker,
    build_net_guard_checker,
)
from openjiuwen.harness.security.permission_engine.netguard.net_urls import (
    _has_package_net_urls,
    merge_package_net_urls,
)
from openjiuwen.harness.security.permission_engine.toolguard.builtin_rules import (
    inline_package_command_rules,
)
from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import (
    evaluate_tiered_policy,
    matched_rule_uses_approval_override,
    maybe_escalate_shell_operators,
    strictest as tiered_policy_strictest,
)

if TYPE_CHECKING:
    from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail

logger = logging.getLogger(__name__)


def _has_builtin_layer(items: Any) -> bool:
    if not isinstance(items, list):
        return False
    return any(isinstance(item, dict) and item.get("layer") == "builtin" for item in items)


# Old swarm product YAML often has severity without action. Map like
# permission_mode=normal: LOW/MEDIUM→allow, HIGH/CRITICAL→ask.
_LEGACY_SEVERITY_TO_ACTION = {
    "LOW": "allow",
    "MEDIUM": "allow",
    "HIGH": "ask",
    "CRITICAL": "ask",
}


def _fill_legacy_host_rule_actions(cfg: dict[str, Any]) -> dict[str, Any]:
    rules = cfg.get("rules")
    if not isinstance(rules, list):
        return cfg
    filled: list[Any] = []
    for rule in rules:
        if not isinstance(rule, dict):
            filled.append(rule)
            continue
        item = dict(rule)
        action = item.get("action")
        if isinstance(action, str) and action.strip():
            filled.append(item)
            continue
        sev = str(item.get("severity") or "").strip().upper()
        mapped = _LEGACY_SEVERITY_TO_ACTION.get(sev)
        if mapped:
            item["action"] = mapped
        filled.append(item)
    cfg["rules"] = filled
    return cfg


def prepare_permissions_for_engine(
    config: PermissionsSection | dict[str, Any] | None,
) -> dict[str, Any]:
    """Fill package policy when the host still passes raw Global YAML.

    New swarm compose already inlines ``layer: builtin`` rules/paths; skip those.
    ``evaluate_tiered_policy`` does not load YAML — only this ingest path does.
    """
    cfg: dict[str, Any] = cast(dict[str, Any], config or {})
    if not isinstance(cfg, dict):
        cfg = {}

    if not _has_builtin_layer(cfg.get("rules")):
        logger.info("[PermissionEngine] permission.package_policy.legacy_host.inline_command_rules")
        cfg = inline_package_command_rules(cfg)

    fg = cfg.get("file_guard")
    if isinstance(fg, dict) and fg.get("enabled") and not _has_builtin_layer(fg.get("paths")):
        logger.info("[PermissionEngine] permission.package_policy.legacy_host.merge_sensitive_paths")
        cfg = merge_package_sensitive_paths(cfg)

    ng = cfg.get("net_guard")
    if isinstance(ng, dict) and ng.get("enabled") and not _has_package_net_urls(ng.get("urls")):
        logger.info("[PermissionEngine] permission.package_policy.legacy_host.merge_net_urls")
        cfg = merge_package_net_urls(cfg)
    cfg = _fill_legacy_host_rule_actions(cfg)
    return cfg


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
        self.config: dict[str, Any] = prepare_permissions_for_engine(config)
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
        self._net_guard: NetGuardChecker | None = build_net_guard_checker(self.config)

    # ---------- 配置 ----------

    def _rebuild_file_guard(self) -> None:
        self._file_guard = build_file_guard_checker(
            self.config,
            workspace_root=self._workspace_root,
            trusted_dirs=self._trusted_dirs,
        )
        self._net_guard = build_net_guard_checker(self.config)

    def update_config(self, config: PermissionsSection | dict[str, Any]) -> None:
        """热更新配置."""
        self.config = prepare_permissions_for_engine(config)
        self._enabled = self.config.get("enabled", True)
        self._rebuild_file_guard()

    def update_trusted_dirs(self, trusted_dirs: list[Path]) -> None:
        """更新受信任目录列表."""
        self._trusted_dirs = trusted_dirs
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

        if self._net_guard is not None:
            net_result = self._net_guard.evaluate(tool_name, tool_args)
            if net_result is not None:
                net_rule = net_result.matched_rule or "net_guard"
                if permission is None:
                    permission = net_result.permission
                    matched_rule = net_rule
                else:
                    permission = tiered_policy_strictest(permission, net_result.permission)
                    matched_rule = f"{matched_rule}|{net_rule}"

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
                permission = tiered_policy_strictest(permission, path_result.permission)
                matched_rule = f"{matched_rule}|{path_rule}"
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

        # 3. Pipeline C：net_guard（可独立关闭；仅 fetch 工具）
        if self._net_guard is not None:
            net_result = self._net_guard.evaluate(tool_name, tool_args)
            if net_result is not None:
                net_rule = net_result.matched_rule or "net_guard"
                logger.info(
                    "[PermissionEngine] permission.net_guard.result tool=%s checked=true permission=%s "
                    "matched_rule=%s merged_with=%s",
                    tool_name,
                    net_result.permission.value,
                    net_rule,
                    permission.value,
                )
                permission = tiered_policy_strictest(permission, net_result.permission)
                matched_rule = f"{matched_rule}|{net_rule}"
            else:
                logger.info(
                    "[PermissionEngine] permission.net_guard.result tool=%s checked=true permission=none "
                    "matched_rule=none",
                    tool_name,
                )
        else:
            logger.info(
                "[PermissionEngine] permission.net_guard.result tool=%s checked=false reason=disabled",
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


def build_permission_interrupt_rail(
    *,
    permissions: PermissionsSection,
    llm: Any = None,
    model_name: str | None = None,
    engine: PermissionEngine | None = None,
    host: ToolPermissionHost | None = None,
    workspace_root: Path | None = None,
) -> "PermissionInterruptRail | None":
    """若 ``permissions.enabled`` 为真则创建护栏，否则返回 ``None``。"""
    from openjiuwen.harness.rails.security import PermissionInterruptRail

    if not isinstance(permissions, dict) or not permissions.get("enabled", False):
        return None

    h = host or ToolPermissionHost()
    if h.resolve_workspace_dir is None and workspace_root is not None:
        root = workspace_root.resolve()

        def _root() -> Path:
            return root

        h = replace(h, resolve_workspace_dir=_root)

    return PermissionInterruptRail(
        config=deepcopy(permissions),
        engine=engine,
        tool_names=None,
        llm=llm,
        model_name=model_name,
        host=h,
    )


__all__ = [
    "PermissionEngine",
    "build_permission_interrupt_rail",
]
