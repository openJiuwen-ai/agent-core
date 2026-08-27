# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""PermissionInterruptRail - tool permission guardrail using ConfirmInterruptRail.

Implements permission checks via PermissionEngine and triggers HITL interrupts
for ASK decisions using the built-in interrupt rail flow.
"""
from __future__ import annotations

import inspect
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
from openjiuwen.harness.security.permission_engine.host import (
    PermissionConfirmationRequest,
    PermissionSceneHookInput,
    ToolPermissionHost,
)
from openjiuwen.harness.security.permission_engine.models import (
    PermissionConfirmResponse,
    PermissionLevel,
    PermissionResult,
)
from openjiuwen.harness.security.permission_engine.models import PermissionsSection
from openjiuwen.harness.security.patterns import (
    merge_permission_allow_rule_into_permissions,
    write_permissions_section_to_agent_config_yaml,
)
from openjiuwen.harness.security.shell_ast import parse_shell_for_permission


TOOL_NAME_ALIASES = {
    "free_search": "mcp_free_search",
    "paid_search": "mcp_paid_search",
    "fetch_webpage": "mcp_fetch_webpage",
    "web_fetch_webpage": "mcp_fetch_webpage",
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
    ) -> None:
        super().__init__(tool_names=tool_names)
        self._static_config = cast(dict[str, Any], config or {})
        self._host = host or ToolPermissionHost()
        if engine is not None:
            self._engine = engine
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
            "tools_keys=%s llm_enabled=%s model_name=%s",
            sorted(self._tool_names),
            list((self._static_config.get("tools") or {}).keys()),
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

        if tool_name in {"bash", "mcp_exec_command", "create_terminal"}:
            cmd = tool_args.get("command", tool_args.get("cmd", ""))
            return self._build_shell_auto_confirm_key(tool_name, str(cmd or ""))

        return tool_name

    @staticmethod
    def _build_shell_auto_confirm_key(tool_name: str, command: str) -> str:
        text = (command or "").strip()
        if not text:
            return ""

        shell_ast_result = parse_shell_for_permission(text)
        if shell_ast_result.kind != "simple":
            return ""
        if shell_ast_result.flags.has_risky_structure():
            return ""
        if len(shell_ast_result.subcommands) != 1:
            return ""

        subcommand = (shell_ast_result.subcommands[0].text or "").strip()
        if not subcommand:
            return ""
        return f"{tool_name}:{subcommand}"

    @staticmethod
    def _should_store_auto_confirm(
        *,
        approved: bool,
        auto_confirm: bool,
        session: Any,
        auto_confirm_key: str,
        persisted: bool,
        permanent: bool = False,
    ) -> bool:
        """Whether to store auto-confirm in session state.

        Store when the user wants to remember (``approved`` + ``auto_confirm``)
        and a valid session key exists.

        Skip only when a **permanent** (disk) rule was actually persisted:
        those rules are reloaded from the host snapshot on the next check.
        Session-overlay persist must still store auto-confirm, because
        ``first_check`` reloads the disk snapshot and would otherwise drop
        in-memory session rules.
        """
        if not (approved and auto_confirm and session is not None and auto_confirm_key):
            return False
        if permanent and persisted:
            return False
        return True

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
        logger.info(
            "[PermissionEngine] permission.rail.trusted_dirs_updated count=%d",
            len(normalized),
        )

    def update_config(
        self,
        config: PermissionsSection | dict[str, Any],
        tool_names: Optional[Iterable[str]] = None,
    ) -> None:
        """Hot-update static permission config；可选 ``tool_names`` 仅更新基类标签集合。"""
        cfg_dict = cast(dict[str, Any], config)
        self._static_config = cfg_dict
        self._engine.update_config(cfg_dict)
        if self._host.tool_permission_checks_active is not None:
            self._engine.set_permission_checks_active(self._host.tool_permission_checks_active)
        if tool_names is not None:
            self._tool_names = {str(x).strip() for x in tool_names if str(x).strip()}
        logger.info(
            "[PermissionEngine] permission.rail.config_updated intercept=all_tools optional_tool_tags=%s",
            sorted(self._tool_names),
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

    def _persist_allow_always(
        self,
        normalized_name: str,
        tool_args: dict,
        *,
        session_id: str | None = None,
    ) -> bool:
        """永久允许：merge 后走 ``persist_allow_rule``（无 Host 则写 YAML）。"""
        return self._persist_merged_allow(
            normalized_name,
            tool_args,
            persist_hook=self._host.persist_allow_rule,
            write_yaml_fallback=True,
            session_id=session_id,
        )

    def _persist_session_allow(
        self,
        normalized_name: str,
        tool_args: dict,
        *,
        session_id: str | None = None,
    ) -> bool:
        """会话内记住：merge 后走 ``persist_session_allow_rule``（无钩子则只更新内存）。"""
        return self._persist_merged_allow(
            normalized_name,
            tool_args,
            persist_hook=self._host.persist_session_allow_rule,
            write_yaml_fallback=False,
            session_id=session_id,
        )

    def _persist_merged_allow(
        self,
        normalized_name: str,
        tool_args: dict,
        *,
        persist_hook: Any,
        write_yaml_fallback: bool,
        session_id: str | None = None,
    ) -> bool:
        """工具级记住与 file_guard 路径白名单：先合并快照，再写盘。"""
        from openjiuwen.harness.security.patterns import merge_file_guard_access_allows

        base_cfg: PermissionsSection | None = None
        if self._host.get_permissions_snapshot is not None:
            try:
                snap = self._invoke_permissions_hook(
                    self._host.get_permissions_snapshot,
                    session_id=session_id,
                )
                if isinstance(snap, dict):
                    base_cfg = cast(PermissionsSection, snap)
            except Exception:
                logger.debug(
                    "[PermissionEngine] permission.persist.snapshot_failed",
                    exc_info=True,
                )
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
        if not ok_tool and not ok_ext:
            return False

        prev_cfg = deepcopy(self._engine.config)
        self.update_config(cfg)

        if persist_hook is not None:
            try:
                persisted = bool(
                    self._invoke_permissions_hook(
                        persist_hook,
                        cast(dict[str, Any], cfg),
                        session_id=session_id,
                    )
                )
            except Exception:
                logger.warning(
                    "[PermissionEngine] permission.persist.host_failed",
                    exc_info=True,
                )
                persisted = False
            if not persisted:
                self.update_config(prev_cfg)
            return persisted

        if not write_yaml_fallback:
            return True

        if not write_permissions_section_to_agent_config_yaml(
            self._host.permission_yaml_path,
            cfg,
        ):
            self.update_config(prev_cfg)
            return False
        return True

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
        session_id = self._resolve_session_id(ctx)

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
            fresh: dict | None = None
            if self._host.get_permissions_snapshot is not None:
                try:
                    snap = self._invoke_permissions_hook(
                        self._host.get_permissions_snapshot,
                        session_id=session_id,
                    )
                    fresh = snap if isinstance(snap, dict) else None
                except Exception:
                    logger.debug(
                        "[PermissionEngine] permission.rail.snapshot_failed",
                        exc_info=True,
                    )
            if isinstance(fresh, dict):
                self.update_config(fresh)
            else:
                self._engine.update_config(self._static_config)
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
                    if confirm_payload.wants_permanent_persist():
                        persisted = self._persist_allow_always(
                            normalized_name, tool_args, session_id=session_id
                        )
                    elif confirm_payload.wants_session_persist():
                        persisted = self._persist_session_allow(
                            normalized_name, tool_args, session_id=session_id
                        )
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
                        permanent=confirm_payload.wants_permanent_persist(),
                    ):
                        self._store_auto_confirm(ctx, auto_confirm_key)
                    if confirm_payload.approved:
                        decision = (
                            "allow_always_persist" if confirm_payload.wants_permanent_persist()
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
            invalid = PermissionResult(
                permission=PermissionLevel.ASK,
                matched_rule=None,
                reason="Invalid confirmation payload",
            )
            message = self._build_message(tool_call, invalid)
            return self.interrupt(InterruptRequest(
                message=message,
                payload_schema=ConfirmPayload.to_schema(),
                metadata=self._build_interrupt_metadata(tool_call, invalid),
            ))

        persisted = False
        if payload.wants_permanent_persist():
            persisted = self._persist_allow_always(
                normalized_name, tool_args, session_id=session_id
            )
            logger.info(
                "[PermissionEngine] permission.persist.result tool=%s confirm_path=%s persisted=%s persist_allow=%s",
                tool_name,
                self._confirm_path_label(),
                persisted,
                payload.persist_allow,
            )
        elif payload.wants_session_persist():
            persisted = self._persist_session_allow(
                normalized_name, tool_args, session_id=session_id
            )
            logger.info(
                "[PermissionEngine] permission.session_persist.result tool=%s "
                "confirm_path=%s persisted=%s auto_confirm_key=%s",
                tool_name,
                self._confirm_path_label(),
                persisted,
                auto_confirm_key,
            )

        if self._should_store_auto_confirm(
            approved=payload.approved,
            auto_confirm=payload.auto_confirm,
            session=ctx.session,
            auto_confirm_key=auto_confirm_key,
            persisted=persisted,
            permanent=payload.wants_permanent_persist(),
        ):
            self._store_auto_confirm(ctx, auto_confirm_key)

        if payload.approved:
            decision = (
                "allow_always_persist" if payload.wants_permanent_persist()
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

    @staticmethod
    def _is_auto_confirmed(auto_confirm_config: Optional[dict], tool_name: str) -> bool:
        if auto_confirm_config is None:
            return False
        return auto_confirm_config.get(tool_name, False)

    @staticmethod
    def _store_auto_confirm(ctx: AgentCallbackContext, auto_confirm_key: str) -> None:
        config = ctx.session.get_state(INTERRUPT_AUTO_CONFIRM_KEY) or {}
        if not isinstance(config, dict):
            config = {}
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
    def _hook_accepts_keyword(hook: Any, name: str) -> bool:
        try:
            params = inspect.signature(hook).parameters
        except (TypeError, ValueError):
            return False
        if name in params:
            return True
        return any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )

    @staticmethod
    def _invoke_permissions_hook(
        hook: Any,
        *args: Any,
        session_id: str | None = None,
    ) -> Any:
        """Call a host callback, forwarding session_id when the hook accepts it."""
        if session_id and PermissionInterruptRail._hook_accepts_keyword(
            hook, "session_id"
        ):
            return hook(*args, session_id=session_id)
        return hook(*args)

    @staticmethod
    def _resolve_session_id(ctx: AgentCallbackContext) -> str | None:
        session = getattr(ctx, "session", None)
        if session is not None:
            for attr_name in ("get_session_id", "session_id"):
                value = PermissionInterruptRail._read_session_attr_value(
                    session, attr_name
                )
                if isinstance(value, str) and value.strip():
                    return value.strip()
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            for key in ("session_id", "conversation_id"):
                value = extra.get(key)
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
        from openjiuwen.harness.security.permission_engine.approve.ask_presentation import (
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
        from openjiuwen.harness.security.permission_engine.approve.ask_presentation import (
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

        if tool_name in {"bash", "mcp_exec_command", "create_terminal"}:
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
