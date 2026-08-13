# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""PermissionInterruptRail - tool permission guardrail using ConfirmInterruptRail.

Implements permission checks via PermissionEngine and triggers HITL interrupts
for ASK decisions using the built-in interrupt rail flow.
"""
from __future__ import annotations

import json
from copy import deepcopy

from typing import Any, Iterable, Optional, cast
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import INTERRUPT_AUTO_CONFIRM_KEY
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.confirm_rail import (
    ConfirmInterruptRail,
    ConfirmPayload,
)

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.host import (
    PermissionConfirmationRequest,
    PermissionSceneHookInput,
    ToolPermissionHost,
)
from openjiuwen.harness.security.models import (
    PermissionConfirmResponse,
    PermissionLevel,
    PermissionResult,
)
from openjiuwen.harness.security.models import PermissionsSection
from openjiuwen.harness.security.patterns import (
    merge_permission_allow_rule_into_permissions,
    write_permissions_section_to_agent_config_yaml,
)
from openjiuwen.harness.security.shell_ast import parse_shell_for_permission


TOOL_NAME_ALIASES = {
    "free_search": "mcp_free_search",
    "paid_search": "mcp_paid_search",
    "fetch_webpage": "mcp_fetch_webpage",
    "exec_command": "mcp_exec_command",
}


class PermissionInterruptRail(ConfirmInterruptRail):
    """Permission interrupt rail.

    - ALLOW: continue
    - DENY: reject
    - ASK: interrupt with ConfirmPayload schema

    对**任意**工具名执行 ``before_tool_call`` 权限判定（不再按工具名子集短路跳过）。
    可选 ``tool_names`` 仅传给基类作 :meth:`get_tools` 展示；不参与是否拦截。

    Auto-confirm is stored in session state (INTERRUPT_AUTO_CONFIRM_KEY).
    Supports fine-grained auto-confirm keys for bash commands (e.g., bash_dir, bash_rm).
    """

    priority: int = 90

    def __init__(
        self,
        config: Optional[PermissionsSection | dict[str, Any]] = None,
        engine: Optional[PermissionEngine] = None,
        tool_names: Optional[Iterable[str]] = None,
        llm: Any = None,
        model_name: str | None = None,
        host: ToolPermissionHost | None = None,
        sandbox_intent: str | None = None,
        permission_mode: str | None = None,
    ) -> None:
        super().__init__(tool_names=tool_names)
        from openjiuwen.harness.security.factory import compose_effective_permissions

        raw = cast(dict[str, Any], config or {})
        # 入参可能是产品原始配置或已合成配置；统一再 compose 一次（幂等）
        effective = compose_effective_permissions(raw)
        self._static_config = effective.permissions
        self.sandbox_intent = sandbox_intent or effective.sandbox_intent
        self.permission_mode = permission_mode or effective.mode
        self._host = host or ToolPermissionHost()
        if engine is not None:
            self._engine = engine
            self._engine.update_config(self._static_config)
        else:
            workspace_root = None
            if self._host.resolve_workspace_dir is not None:
                try:
                    workspace_root = self._host.resolve_workspace_dir()
                except Exception:
                    logger.debug(
                        "[PermissionEngine] permission.rail.workspace_resolve_failed",
                        exc_info=True,
                    )
            trusted_dirs = []
            self._engine = PermissionEngine(
                config=self._static_config,
                llm=llm,
                model_name=model_name,
                workspace_root=workspace_root,
                trusted_dirs=trusted_dirs,
            )
        if self._host.tool_permission_checks_active is not None:
            self._engine.set_permission_checks_active(self._host.tool_permission_checks_active)
        logger.info(
            "[PermissionEngine] permission.rail.init intercept=all_tools optional_tool_tags=%s "
            "tools_keys=%s mode=%s sandbox_intent=%s llm_enabled=%s model_name=%s",
            sorted(self._tool_names),
            list((self._static_config.get("tools") or {}).keys()),
            self.permission_mode,
            self.sandbox_intent,
            self._engine._llm is not None,
            self._engine._model_name,
        )

    def _normalize_tool_name(self, tool_name: str) -> str:
        """Normalize tool name using aliases.

        Maps tool names from openjiuwen.harness.tools to mcp_* names used in config.
        """
        return TOOL_NAME_ALIASES.get(tool_name, tool_name)

    def _get_auto_confirm_key(self, tool_call: ToolCall) -> str:
        """Generate a conservative session auto-confirm key for the tool call."""
        if tool_call is None:
            return ""

        tool_name = tool_call.name or ""
        tool_args = self.parse_tool_args(tool_call)

        if tool_name in {"bash", "mcp_exec_command", "create_terminal", "powershell"}:
            cmd = tool_args.get("command", tool_args.get("cmd", ""))
            return self._build_shell_auto_confirm_key(tool_name, str(cmd or ""))

        path_key = self._build_path_auto_confirm_key(tool_name, tool_args)
        if path_key:
            return path_key

        return tool_name

    @staticmethod
    def _normalize_auto_confirm_path(path: str) -> str:
        return path.replace("\\", "/").rstrip("/") or path.replace("\\", "/")

    @classmethod
    def _extract_auto_confirm_path(cls, tool_args: dict) -> str:
        from openjiuwen.harness.security.tiered_policy import expand_path_arg_values

        for key in (
            "path",
            "file_path",
            "target_file",
            "file",
            "old_path",
            "new_path",
            "source_path",
            "dest_path",
            "directory",
            "dir",
            "abs_file_path_list",
        ):
            values = expand_path_arg_values(tool_args.get(key))
            if values:
                return cls._normalize_auto_confirm_path(values[0])
        return ""

    @classmethod
    def _build_path_auto_confirm_key(cls, tool_name: str, tool_args: dict) -> str:
        """路径类工具：``tool:normalized_path``，避免整工具放行绕过 file_guard。"""
        path_tools = {
            "read_file",
            "write_file",
            "edit_file",
            "read_text_file",
            "write_text_file",
            "write",
            "read",
            "glob_file_search",
            "glob",
            "list_dir",
            "list_files",
            "grep",
            "search_replace",
            "send_file_to_user",
        }
        if tool_name not in path_tools:
            return ""
        path = cls._extract_auto_confirm_path(tool_args)
        if not path:
            return ""
        return f"{tool_name}:{path}"

    _SHELL_AUTO_CONFIRM_SEG_SEP = "|+|"

    @classmethod
    def _build_shell_auto_confirm_keys(cls, tool_name: str, command: str) -> list[str]:
        """按子命令分段生成 auto_confirm key（与 suggestion / shell_subcommands 一致）。"""
        text = (command or "").strip()
        if not text:
            return []

        shell_ast_result = parse_shell_for_permission(text)
        flags = shell_ast_result.flags
        if flags.has_compound_operators:
            return []
        if flags.has_risky_structure() and not flags.has_pipeline:
            return []
        # 管道仅含 | 时仍可分段记住；重定向/替换等危险结构不给 key。
        if any((
            flags.has_subshell,
            flags.has_command_group,
            flags.has_command_substitution,
            flags.has_process_substitution,
            flags.has_parameter_expansion,
            flags.has_heredoc,
            flags.has_input_redirection,
            flags.has_output_redirection,
        )):
            return []
        if shell_ast_result.kind != "simple":
            return []

        keys: list[str] = []
        for subcommand in shell_ast_result.subcommands:
            seg = (subcommand.text or "").strip()
            if seg:
                keys.append(f"{tool_name}:{seg}")
        return keys

    @classmethod
    def _build_shell_auto_confirm_key(cls, tool_name: str, command: str) -> str:
        keys = cls._build_shell_auto_confirm_keys(tool_name, command)
        if not keys:
            return ""
        if len(keys) == 1:
            return keys[0]
        # 多段拼成稳定单 key，供 interrupt 映射；store/check 时再拆回各段。
        return cls._SHELL_AUTO_CONFIRM_SEG_SEP.join(keys)

    @staticmethod
    def _should_store_auto_confirm(
        *,
        approved: bool,
        auto_confirm: bool,
        session: Any,
        auto_confirm_key: str,
        persisted: bool,
    ) -> bool:
        """Whether to store auto-confirm in session state.

        Session auto-confirm is stored when:
        - ``approved`` and ``auto_confirm`` are both True (user wants to remember)
        - A valid session and auto_confirm_key exist
        - The rule has NOT already been persisted to disk (persisted rules are
          loaded by PermissionEngine at session start, so no need for session-level
          duplication)

        ``persist_allow`` is a separate decision: when True, the allow rule is also
        written to disk (via ``_persist_allow_always``); when False, only the
        session-level auto_confirm is stored.
        """
        return bool(approved and auto_confirm and session is not None and auto_confirm_key and not persisted)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = ctx.inputs.tool_name
        tool_call = ctx.inputs.tool_call
        normalized_name = self._normalize_tool_name(tool_name)
        logger.info(
            "[PermissionEngine] permission.rail.before_tool_call tool=%s normalized=%s "
            "optional_tool_tags=%s",
            tool_name,
            normalized_name,
            sorted(self._tool_names),
        )

        tool_call_id = self._resolve_tool_call_id(tool_call)
        user_input = self._get_user_input(ctx, tool_call_id)
        auto_confirm_config = None
        if ctx.session:
            auto_confirm_config = ctx.session.get_state(INTERRUPT_AUTO_CONFIRM_KEY)
            if not isinstance(auto_confirm_config, dict):
                auto_confirm_config = {}

        decision = await self.resolve_interrupt(
            ctx=ctx,
            tool_call=tool_call,
            user_input=user_input,
            auto_confirm_config=auto_confirm_config,
        )
        ctx.extra["_interrupt_decision"] = decision
        self._apply_decision(ctx, tool_call, tool_name, decision)

    def set_trusted_dirs(self, trusted_dirs: Optional[Iterable[Any]]) -> None:
        """Per-request hot update of trusted directories.

        Hosts (e.g. JiuWenSwarm adapter) call this when ``trusted_dirs`` arrives
        with each request so that the external_directory check treats these
        subtrees as internal and skips ask/deny for them.

        Accepts raw strings (resolved to absolute ``Path``); ``None``/empty
        clears the list. ``Path`` objects are passed through as-is.
        """
        from pathlib import Path as _Path

        normalized: list[_Path] = []
        if trusted_dirs:
            for d in trusted_dirs:
                if not d:
                    continue
                try:
                    normalized.append(_Path(str(d)).expanduser().resolve(strict=False))
                except (OSError, RuntimeError):
                    continue
        self._engine.update_trusted_dirs(normalized)
        self._sync_workspace_root_from_host()
        logger.info(
            "[PermissionEngine] permission.rail.trusted_dirs_updated count=%d",
            len(normalized),
        )

    def _sync_workspace_root_from_host(self) -> None:
        """用 Host 当前任务 workspace 刷新 file_guard，避免沿用构造时的 agent 根。"""
        from pathlib import Path as _Path

        if self._host.resolve_workspace_dir is None:
            return
        try:
            workspace = self._host.resolve_workspace_dir()
        except Exception:
            logger.debug(
                "[PermissionEngine] permission.rail.workspace_resolve_failed",
                exc_info=True,
            )
            return
        if workspace is None:
            return
        self._engine.update_workspace_root(_Path(workspace))

    def update_config(
        self,
        config: PermissionsSection | dict[str, Any],
        tool_names: Optional[Iterable[str]] = None,
    ) -> None:
        """Hot-update static permission config；可选 ``tool_names`` 仅更新基类标签集合。

        Host 若已聚合三层，应传入 ``compose`` 后的 effective.permissions。
        入参会再跑一轮 compose（幂等）：effective 上的 ``allow_tools`` 必须保留，
        否则会剥掉 User/Session 整工具信任。
        """
        from openjiuwen.harness.security.factory import compose_effective_permissions

        effective = compose_effective_permissions(cast(dict[str, Any], config))
        cfg_dict = effective.permissions
        self._static_config = cfg_dict
        self.sandbox_intent = effective.sandbox_intent
        self.permission_mode = effective.mode
        self._engine.update_config(cfg_dict)
        if self._host.tool_permission_checks_active is not None:
            self._engine.set_permission_checks_active(self._host.tool_permission_checks_active)
        if tool_names is not None:
            self._tool_names = {str(x).strip() for x in tool_names if str(x).strip()}
        logger.info(
            "[PermissionEngine] permission.rail.config_updated intercept=all_tools "
            "optional_tool_tags=%s mode=%s sandbox_intent=%s",
            sorted(self._tool_names),
            self.permission_mode,
            self.sandbox_intent,
        )

    def _collect_file_guard_persist_accesses(
        self,
        normalized_name: str,
        tool_args: dict,
        permissions_cfg: dict[str, Any] | PermissionsSection,
    ) -> list[tuple[str, str]]:
        """若本次调用路径层为 ASK，返回 ``(path, action)`` 供按轴写入 file_guard。"""
        from openjiuwen.harness.security.file_guard import build_file_guard_checker

        if self._host.resolve_workspace_dir is None:
            return []
        try:
            workspace = self._host.resolve_workspace_dir()
        except Exception:
            logger.debug(
                "[PermissionEngine] permission.persist.external.workspace_resolve_failed",
                exc_info=True,
            )
            return []
        # Use the engine's per-request trusted_dirs (set via set_trusted_dirs)
        # so persist-time external-path detection matches the runtime check.
        trusted_dirs = list(self._engine.trusted_dirs)
        try:
            checker = build_file_guard_checker(
                permissions_cfg,
                workspace_root=workspace,
                trusted_dirs=trusted_dirs,
            )
            if checker is None:
                return []
            return list(checker.collect_ask_accesses(normalized_name, tool_args))
        except Exception:
            logger.warning(
                "[PermissionEngine] permission.persist.external.check_failed",
                exc_info=True,
            )
            return []

    def _call_permissions_snapshot(self, session_id: str | None = None) -> dict | None:
        """调用 Host snapshot；兼容 ``()`` 与 ``(session_id)`` 两种签名。"""
        if self._host.get_permissions_snapshot is None:
            return None
        try:
            snap = self._host.get_permissions_snapshot(session_id)
        except TypeError:
            try:
                snap = self._host.get_permissions_snapshot()
            except Exception:
                logger.debug(
                    "[PermissionEngine] permission.rail.snapshot_failed",
                    exc_info=True,
                )
                return None
        except Exception:
            logger.debug(
                "[PermissionEngine] permission.rail.snapshot_failed",
                exc_info=True,
            )
            return None
        return snap if isinstance(snap, dict) else None

    def _merge_pattern_or_file_guard_allow(
        self,
        normalized_name: str,
        tool_args: dict,
        session_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """合并 pattern 级 allow、allow_tools 回退与/或 file_guard 路径放宽。"""
        from openjiuwen.harness.security.patterns import merge_file_guard_access_allows

        base_cfg: PermissionsSection | None = None
        snap = self._call_permissions_snapshot(session_id)
        if isinstance(snap, dict):
            base_cfg = cast(PermissionsSection, snap)
        if base_cfg is None:
            base_cfg = cast(PermissionsSection, deepcopy(self._engine.config))

        cfg, ok_tool = merge_permission_allow_rule_into_permissions(
            base_cfg, normalized_name, tool_args
        )
        accesses = self._collect_file_guard_persist_accesses(
            normalized_name, tool_args, cfg
        )
        ok_ext = False
        if accesses:
            cfg, ok_ext = merge_file_guard_access_allows(cfg, accesses)
        return cast(dict[str, Any], cfg), bool(ok_tool or ok_ext)

    def _resolve_persist_session_id(self, ctx: Any | None) -> str:
        """从 callback ctx.session 解析会话 id（ContextVar 在 HITL resume 时常为空）。"""
        if ctx is None:
            return ""
        session = getattr(ctx, "session", None)
        if session is None:
            return ""
        for attr_name in ("get_session_id", "session_id"):
            attr = getattr(session, attr_name, None)
            try:
                value = attr() if callable(attr) else attr
            except Exception:
                value = None
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _attach_persist_session_id(
        self, cfg: dict[str, Any], ctx: Any | None
    ) -> dict[str, Any]:
        sid = self._resolve_persist_session_id(ctx)
        if sid:
            out = dict(cfg)
            out["_persist_session_id"] = sid
            return out
        return cfg

    def _persist_allow_always(
        self, normalized_name: str, tool_args: dict, ctx: Any | None = None
    ) -> bool:
        """永久允许：pattern 级 approval_overrides 与/或 file_guard 路径白名单。"""
        sid = self._resolve_persist_session_id(ctx)
        cfg, applied = self._merge_pattern_or_file_guard_allow(
            normalized_name, tool_args, session_id=sid or None
        )
        if not applied:
            return False
        cfg = self._attach_persist_session_id(cfg, ctx)

        prev_cfg = deepcopy(self._engine.config)
        self.update_config(cfg)

        if self._host.persist_allow_rule is not None:
            try:
                persisted = bool(
                    self._host.persist_allow_rule(cast(dict[str, Any], cfg))
                )
            except Exception:
                logger.warning(
                    "[PermissionEngine] permission.persist.host_failed",
                    exc_info=True,
                )
                persisted = False
            if not persisted:
                self.update_config(prev_cfg)
        else:
            if not write_permissions_section_to_agent_config_yaml(
                self._host.permission_yaml_path,
                cfg,
            ):
                self.update_config(prev_cfg)
                persisted = False
            else:
                persisted = True
        return persisted

    def _persist_session_allow(
        self, normalized_name: str, tool_args: dict, ctx: Any | None = None
    ) -> bool:
        """会话内记住：有安全 suggestion 时合并并交给 Host 写 session 层。"""
        if self._host.persist_session_allow_rule is None:
            return False
        sid = self._resolve_persist_session_id(ctx)
        cfg, applied = self._merge_pattern_or_file_guard_allow(
            normalized_name, tool_args, session_id=sid or None
        )
        if not applied:
            return False
        cfg = self._attach_persist_session_id(cfg, ctx)
        prev_cfg = deepcopy(self._engine.config)
        self.update_config(cfg)
        try:
            persisted = bool(
                self._host.persist_session_allow_rule(cast(dict[str, Any], cfg))
            )
        except Exception:
            logger.warning(
                "[PermissionEngine] permission.persist.session_host_failed",
                exc_info=True,
            )
            persisted = False
        if not persisted:
            self.update_config(prev_cfg)
        return persisted

    async def resolve_interrupt(
        self,
        ctx: AgentCallbackContext,
        tool_call: Optional[ToolCall],
        user_input: Optional[Any],
        auto_confirm_config: Optional[dict] = None,
    ):
        tool_name = tool_call.name if tool_call is not None else ""
        normalized_name = self._normalize_tool_name(tool_name)
        tool_args = self.parse_tool_args(tool_call)
        auto_confirm_key = self._get_auto_confirm_key(tool_call)

        logger.info(
            "[PermissionEngine] permission.rail.resolve tool=%s normalized=%s "
            "tool_args=%s auto_confirm_key=%s user_input_type=%s",
            tool_name, normalized_name, tool_args, auto_confirm_key,
            type(user_input).__name__ if user_input else None
        )

        if self._host.permission_scene_hook is not None:
            try:
                scene_out = await self._host.permission_scene_hook(
                    PermissionSceneHookInput(
                        ctx=ctx,
                        tool_call=tool_call,
                        user_input=user_input,
                        normalized_tool_name=normalized_name,
                        tool_args=tool_args,
                        engine=self._engine,
                    ),
                )
            except Exception:
                logger.warning(
                    "[PermissionEngine] permission.scene_hook.failed",
                    exc_info=True,
                )
                scene_out = None
            if scene_out is not None:
                if scene_out[0] == "approve":
                    return self.approve()
                if scene_out[0] == "reject":
                    msg = scene_out[1] if len(scene_out) > 1 else "[PERMISSION_DENIED]"
                    return self.reject(tool_result=msg)

        if user_input is None:
            logger.info(
                "[PermissionEngine] permission.rail.first_check tool=%s normalized=%s",
                tool_name, normalized_name
            )
            # 与磁盘上的 permissions 对齐：若仅写盘未先/未后刷新内存，此处用旧 _static_config
            # 会抹掉 approval_overrides 等；应提供 get_permissions_snapshot 或在落盘后已 update_config。
            # 必须带上 session_id，否则会丢掉 Session 层 allow_tools / file_guard.paths。
            sid = self._resolve_persist_session_id(ctx)
            fresh = self._call_permissions_snapshot(sid or None)
            if isinstance(fresh, dict):
                self.update_config(fresh)
            else:
                self._engine.update_config(self._static_config)
            self._sync_workspace_root_from_host()
            try:
                result = await self._engine.check_permission(
                    tool_name=normalized_name,
                    tool_args=tool_args,
                )
            except Exception:
                logger.error(
                    "[PermissionEngine] permission.rail.check_failed tool=%s normalized=%s",
                    tool_name,
                    normalized_name,
                )
                raise

            if result.permission == PermissionLevel.ALLOW:
                logger.info(
                    "[PermissionEngine] permission.rail.result tool=%s decision=allow matched_rule=%s",
                    tool_name,
                    result.matched_rule,
                )
                return self.approve()

            if result.permission == PermissionLevel.DENY:
                logger.warning(
                    "[PermissionEngine] permission.rail.result tool=%s decision=deny matched_rule=%s",
                    tool_name,
                    result.matched_rule,
                )
                return self.reject(tool_result=f"[PERMISSION_DENIED] {result.reason or 'Operation not allowed'}")

            if self._is_auto_confirmed(auto_confirm_config, auto_confirm_key):
                logger.info(
                    "[PermissionEngine] permission.auto_confirm.hit tool=%s key=%s",
                    tool_name,
                    auto_confirm_key,
                )
                return self.approve()

            if self._host.request_permission_confirmation is not None:
                ext_out = await self._host.request_permission_confirmation(
                    PermissionConfirmationRequest(
                        ctx=ctx,
                        tool_call=tool_call,
                        result=result,
                        auto_confirm_key=auto_confirm_key,
                    ),
                )
                if ext_out != "interrupt":
                    if ext_out is None:
                        return self.reject(
                            tool_result=(
                                f"[PERMISSION_DENIED] {result.reason or 'Operation requires approval'} "
                                "(Hosted permission request failed)"
                            ),
                        )
                    if not isinstance(ext_out, PermissionConfirmResponse):
                        logger.warning(
                            "[PermissionEngine] permission.hosted_confirm.invalid_type type=%s",
                            type(ext_out).__name__,
                        )
                        return self.reject(
                            tool_result=(
                                f"[PERMISSION_DENIED] {result.reason or 'Operation requires approval'} "
                                "(Invalid hosted permission response)"
                            ),
                        )
                    confirm_payload = ext_out
                    persisted = False
                    if (
                        confirm_payload.approved
                        and confirm_payload.auto_confirm
                        and confirm_payload.persist_allow
                    ):
                        persisted = self._persist_allow_always(normalized_name, tool_args, ctx)
                    elif (
                        confirm_payload.approved
                        and confirm_payload.auto_confirm
                        and not confirm_payload.persist_allow
                    ):
                        persisted = self._persist_session_allow(normalized_name, tool_args, ctx)
                    logger.info(
                        "[PermissionEngine] permission.persist.result tool=%s "
                        "confirm_path=hosted persisted=%s persist_allow=%s",
                        tool_name,
                        persisted,
                        confirm_payload.persist_allow,
                    )
                    if self._should_store_auto_confirm(
                        approved=confirm_payload.approved,
                        auto_confirm=confirm_payload.auto_confirm,
                        session=ctx.session,
                        auto_confirm_key=auto_confirm_key,
                        persisted=persisted,
                    ):
                        self._store_auto_confirm(ctx, auto_confirm_key)
                    if confirm_payload.approved:
                        decision = (
                            "allow_always_persist" if confirm_payload.persist_allow
                            else "allow_always_session" if confirm_payload.auto_confirm
                            else "allow_once"
                        )
                        logger.info(
                            "[PermissionEngine] permission.user.decision tool=%s confirm_path=hosted "
                            "decision=%s persisted=%s",
                            tool_name,
                            decision,
                            persisted,
                        )
                        return self.approve()
                    logger.info(
                        "[PermissionEngine] permission.user.decision tool=%s confirm_path=hosted decision=deny",
                        tool_name,
                    )
                    return self.reject(
                        tool_result=(
                            confirm_payload.feedback or "[PERMISSION_REJECTED] User rejected the request."
                        ),
                    )

            logger.info(
                "[PermissionEngine] permission.interrupt.ask tool=%s matched_rule=%s",
                tool_name,
                result.matched_rule,
            )
            message = self._build_message(tool_call, result)
            return self.interrupt(InterruptRequest(
                message=message,
                payload_schema=ConfirmPayload.to_schema(),
                metadata=self._build_interrupt_metadata(tool_call, result),
            ))

        logger.info("[PermissionEngine] permission.rail.user_response tool=%s", tool_name)
        payload = self.parse_confirm_payload(user_input)
        if payload is None:
            message = self._build_message(tool_call, PermissionResult(
                permission=PermissionLevel.ASK,
                matched_rule=None,
                reason="Invalid confirmation payload",
            ))
            return self.interrupt(InterruptRequest(
                message=message,
                payload_schema=ConfirmPayload.to_schema(),
                metadata=self._build_interrupt_metadata(
                    tool_call,
                    PermissionResult(permission=PermissionLevel.ASK, matched_rule=None),
                ),
            ))

        persisted = False
        if payload.approved and payload.auto_confirm and payload.persist_allow:
            persisted = self._persist_allow_always(normalized_name, tool_args, ctx)
            logger.info(
                "[PermissionEngine] permission.persist.result tool=%s confirm_path=%s persisted=%s persist_allow=%s",
                tool_name,
                self._confirm_path_label(),
                persisted,
                payload.persist_allow,
            )
        elif payload.approved and payload.auto_confirm and not payload.persist_allow:
            persisted = self._persist_session_allow(normalized_name, tool_args, ctx)
            logger.info(
                "[PermissionEngine] permission.session_only tool=%s confirm_path=%s "
                "auto_confirm_key=%s persisted=%s",
                tool_name,
                self._confirm_path_label(),
                auto_confirm_key,
                persisted,
            )

        if self._should_store_auto_confirm(
            approved=payload.approved,
            auto_confirm=payload.auto_confirm,
            session=ctx.session,
            auto_confirm_key=auto_confirm_key,
            persisted=persisted,
        ):
            self._store_auto_confirm(ctx, auto_confirm_key)

        if payload.approved:
            decision = (
                "allow_always_persist" if payload.persist_allow
                else "allow_always_session" if payload.auto_confirm
                else "allow_once"
            )
            logger.info(
                "[PermissionEngine] permission.user.decision tool=%s confirm_path=%s decision=%s persisted=%s",
                tool_name,
                self._confirm_path_label(),
                decision,
                persisted,
            )
            return self.approve()

        logger.info(
            "[PermissionEngine] permission.user.decision tool=%s confirm_path=%s decision=deny",
            tool_name,
            self._confirm_path_label(),
        )
        return self.reject(tool_result=payload.feedback or "[PERMISSION_REJECTED] User rejected the request.")

    @staticmethod
    def parse_tool_args(tool_call: Optional[ToolCall]) -> dict:
        if tool_call is None:
            return {}
        args = tool_call.arguments
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
            except Exception:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        if isinstance(args, dict):
            return args
        return {}

    @staticmethod
    def parse_confirm_payload(user_input: Any) -> Optional[PermissionConfirmResponse]:
        if isinstance(user_input, PermissionConfirmResponse):
            return user_input
        if isinstance(user_input, ConfirmPayload):
            return PermissionConfirmResponse(
                approved=user_input.approved,
                feedback=user_input.feedback,
                auto_confirm=user_input.auto_confirm,
                persist_allow=user_input.persist_allow,
            )
        if isinstance(user_input, dict):
            try:
                payload = ConfirmPayload.model_validate(user_input)
            except Exception:
                return None
            return PermissionConfirmResponse(
                approved=payload.approved,
                feedback=payload.feedback,
                auto_confirm=payload.auto_confirm,
                persist_allow=payload.persist_allow,
            )
        if isinstance(user_input, str):
            try:
                raw_payload = json.loads(user_input)
            except Exception:
                return None
            if not isinstance(raw_payload, dict):
                return None
            return PermissionInterruptRail.parse_confirm_payload(raw_payload)
        return None

    def _confirm_path_label(self) -> str:
        return "hosted" if self._host.request_permission_confirmation is not None else "interrupt"

    @classmethod
    def _split_auto_confirm_keys(cls, auto_confirm_key: str) -> list[str]:
        if not auto_confirm_key:
            return []
        sep = cls._SHELL_AUTO_CONFIRM_SEG_SEP
        if sep in auto_confirm_key:
            return [k for k in auto_confirm_key.split(sep) if k]
        return [auto_confirm_key]

    @classmethod
    def _is_auto_confirmed(cls, auto_confirm_config: Optional[dict], auto_confirm_key: str) -> bool:
        if auto_confirm_config is None or not auto_confirm_key:
            return False
        # 完整拼接 key（interrupt 路径可能只写这一条）
        if auto_confirm_config.get(auto_confirm_key, False):
            return True
        keys = cls._split_auto_confirm_keys(auto_confirm_key)
        if len(keys) <= 1:
            return False
        # 多段：各分段 key 均已记住才命中（与分段 approval_overrides 一致）
        return all(auto_confirm_config.get(k, False) for k in keys)

    @classmethod
    def _store_auto_confirm(cls, ctx: AgentCallbackContext, auto_confirm_key: str) -> None:
        if not auto_confirm_key:
            return
        config = ctx.session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) or {}
        if not isinstance(config, dict):
            config = {}
        for key in cls._split_auto_confirm_keys(auto_confirm_key):
            config[key] = True
        # 多段同时保留拼接 key，兼容只写 composite 的 interrupt 回写
        if cls._SHELL_AUTO_CONFIRM_SEG_SEP in auto_confirm_key:
            config[auto_confirm_key] = True
        ctx.session.update_state({INTERRUPT_AUTO_CONFIRM_KEY: config})
        logger.info("[PermissionEngine] permission.auto_confirm.store key=%s", auto_confirm_key)

    @staticmethod
    def _read_session_attr_value(session: Any, attr_name: str) -> Any:
        attr = getattr(session, attr_name, None)
        if not callable(attr):
            return attr
        try:
            return attr()
        except Exception:
            logger.debug(
                "[PermissionEngine] permission.rail.session_attr_read_failed attr=%s",
                attr_name,
                exc_info=True,
            )
            return None

    @staticmethod
    def _resolve_session_id(ctx: AgentCallbackContext) -> str | None:
        session = getattr(ctx, "session", None)
        if session is None:
            return None

        for attr_name in ("get_session_id", "session_id"):
            value = PermissionInterruptRail._read_session_attr_value(session, attr_name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def format_args_preview(tool_args: dict) -> str:
        try:
            return json.dumps(tool_args, ensure_ascii=False, indent=2)[:1000]
        except Exception:
            return str(tool_args)[:1000]

    def _build_message(
        self,
        tool_call: Optional[ToolCall],
        result: PermissionResult,
    ) -> str:
        from openjiuwen.harness.security.ask_presentation import (
            build_permission_ask_presentation,
            render_ask_presentation_message,
        )

        tool_name = tool_call.name if tool_call else ""
        tool_args = self.parse_tool_args(tool_call)
        presentation = build_permission_ask_presentation(tool_name, tool_args, result)
        hint = self._build_always_allow_hint(tool_call)
        return render_ask_presentation_message(presentation, always_allow_hint=hint)

    def _build_interrupt_metadata(
        self,
        tool_call: Optional[ToolCall],
        result: PermissionResult,
    ) -> dict:
        from openjiuwen.harness.security.ask_presentation import (
            build_permission_ask_presentation,
        )

        tool_name = tool_call.name if tool_call else ""
        tool_args = self.parse_tool_args(tool_call)
        presentation = build_permission_ask_presentation(tool_name, tool_args, result)
        return {
            "ask_category": presentation.category,
            "ask_title": presentation.title,
            "ask_summary": presentation.summary,
            "matched_rule": result.matched_rule or "",
        }

    def _build_always_allow_hint(self, tool_call: Optional[ToolCall]) -> str:
        if tool_call is None:
            return ""
        
        tool_name = tool_call.name or ""
        tool_args = self.parse_tool_args(tool_call)
        auto_confirm_key = self._get_auto_confirm_key(tool_call)

        path_hint = ""
        for key in ("path", "file_path", "target_file", "file", "old_path", "new_path"):
            val = tool_args.get(key)
            if isinstance(val, str) and val.strip():
                path_hint = val.strip()
                break
        if not path_hint:
            for key, val in tool_args.items():
                if not isinstance(val, str) or not val.strip():
                    continue
                if "/" not in val and "\\" not in val:
                    continue
                path_hint = val.strip()
                break

        if tool_name in {"bash", "mcp_exec_command", "create_terminal", "powershell"}:
            cmd = tool_args.get("command", tool_args.get("cmd", ""))
            shell_key = self._build_shell_auto_confirm_key(tool_name, str(cmd or ""))
            if shell_key:
                return (
                    f'\n\n> 选择「会话内记住」可在本会话内自动放行 ``{shell_key}`` 类调用；'
                    f'选择「永久记住」可将此规则写回磁盘，所有会话均自动放行。'
                )
            if auto_confirm_key:
                return (
                    f'\n\n> 选择「会话内记住」可在本会话内自动放行 ``{auto_confirm_key}`` 类调用。'
                )
            return ""

        if auto_confirm_key and ":" in auto_confirm_key and path_hint:
            return (
                f'\n\n> 选择「会话内记住」可在本会话内自动放行 ``{auto_confirm_key}``；'
                f'选择「永久记住」可将此规则写回磁盘，所有会话均自动放行。'
            )

        if auto_confirm_key:
            path_desc = f"在 ``{path_hint}`` 下" if path_hint else ""
            return (
                f'\n\n> 选择「会话内记住」可在本会话内自动放行 ``{tool_name}`` 类工具{path_desc}的调用；'
                f'选择「永久记住」可将此规则写回磁盘，所有会话均自动放行。'
            )
        return ""


__all__ = [
    "PermissionInterruptRail",
]
