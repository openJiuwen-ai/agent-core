# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""PermissionInterruptRail - tool permission guardrail using ConfirmInterruptRail.

Implements permission checks via the agent_shield permission manager and triggers HITL interrupts
for ASK decisions using the built-in interrupt rail flow.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Optional, cast

from agent_shield.tool_permission import get_permission_manager

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import (
    INTERRUPT_AUTO_CONFIRM_KEY,
    RESUME_BATCH_ALLOW_KEYS,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.interrupt.confirm_rail import (
    ConfirmInterruptRail,
    ConfirmPayload,
)
from openjiuwen.harness.security.host import (
    PermissionConfirmationRequest,
    PermissionSceneHookInput,
    ToolPermissionHost,
)
from openjiuwen.harness.security.models import (
    PermissionConfirmResponse,
    PermissionLevel,
    PermissionResult,
    PermissionsSection,
)
from openjiuwen.harness.security.patterns import (
    merge_permission_allow_rule_into_permissions,
    write_permissions_section_to_agent_config_yaml,
)
from openjiuwen.harness.security.permission_key import DEFAULT_KEY
from openjiuwen.harness.security.shell_ast import parse_shell_for_permission

TOOL_NAME_ALIASES = {
    "free_search": "mcp_free_search",
    "paid_search": "mcp_paid_search",
    "fetch_webpage": "mcp_fetch_webpage",
    "exec_command": "mcp_exec_command",
}


def compute_auto_confirm_key(tool_call: Optional[ToolCall]) -> str:
    """Module-level auto-confirm key computation, shared by the rail and the
    tool-interrupt handler (batch-scoped allow keys).

    Key semantics: bare tool name; shell-like tools use ``tool:subcommand``
    when the command is simple and single-purpose, else ``""`` (not
    rememberable). Identical to the rail's historical ``_get_auto_confirm_key``
    behavior — extracted so resume-time batch computation cannot drift from
    rail-time matching.
    """
    if tool_call is None:
        return ""

    tool_name = tool_call.name or ""
    tool_args = PermissionInterruptRail.parse_tool_args(tool_call)

    if tool_name in {"bash", "mcp_exec_command", "create_terminal"}:
        cmd = tool_args.get("command", tool_args.get("cmd", ""))
        return PermissionInterruptRail.build_shell_auto_confirm_key(tool_name, str(cmd or ""))

    return tool_name


def compute_batch_allow_key(tool_call: Optional[ToolCall]) -> str:
    """Batch-allow key for resume replay (P3-2), scoped to the exact call.

    Unlike session auto_confirm keys (bare tool name / ``tool:subcommand`` —
    the user explicitly opts in at that granularity), the batch-scoped allow
    must NOT expand a single allow_once to sibling calls whose arguments the
    user never reviewed. The key therefore fingerprints the full argument
    dict: only calls with the same tool name AND identical arguments share a
    key. Falls back to "" (no batch expansion) on any parse failure —
    fail-closed for privilege expansion.
    """
    if tool_call is None:
        return ""

    tool_name = (tool_call.name or "").strip()
    if not tool_name:
        return ""

    try:
        tool_args = PermissionInterruptRail.parse_tool_args(tool_call)
        canonical_args = json.dumps(
            tool_args, sort_keys=True, ensure_ascii=False, default=str,
        )
        args_digest = hashlib.sha256(canonical_args.encode("utf-8")).hexdigest()[:12]
    except Exception:
        return ""
    return f"{tool_name}:{args_digest}"


class PermissionInterruptRail(ConfirmInterruptRail):
    """Permission interrupt rail.

    - ALLOW: continue
    - DENY: reject
    - ASK: interrupt with ConfirmPayload schema

    对**任意**工具名执行 ``before_tool_call`` 权限判定（不再按工具名子集短路跳过）。
    可选 ``tool_names`` 仅传给基类作 :meth:`get_tools` 展示；不参与是否拦截。

    Auto-confirm is stored in session state (INTERRUPT_AUTO_CONFIRM_KEY).
    Supports fine-grained auto-confirm keys for bash commands (e.g., bash_dir, bash_rm).

    ``inherit_to_subagents`` is True so general-purpose subagents also run
    this rail (factory injects parent rails **by reference**). Product hosts
    that surface ASK over a parent session card (e.g. jiuwenswarm) must return
    :class:`~openjiuwen.harness.security.models.PermissionConfirmResponse` for
    subagent tools instead of ``"interrupt"``; nested checkpoint HITL would
    otherwise break outer ``task_tool``. Auto-confirm still lands on the
    child session that owns ``ctx``.
    """

    priority: int = 90
    inherit_to_subagents = True

    def __init__(
        self,
        tool_names: Optional[Iterable[str]] = None,
        llm: Any = None,
        model_name: str | None = None,
        host: ToolPermissionHost | None = None,
        permission_key: str | None = None,
    ) -> None:
        """Create the rail.

        The rail owns no permission config.  It decides with the config the
        host registered under ``permission_key`` in the process-wide permission
        manager (``default`` when omitted); registering, replacing and removing
        that config is the host's job.  A key nobody registered makes the
        decision raise ``KeyError``.
        """
        super().__init__(tool_names=tool_names)
        if permission_key is not None and (not isinstance(permission_key, str) or not permission_key.strip()):
            raise ValueError("permission_key must be a non-empty string")
        self._host = host or ToolPermissionHost()
        self._key = permission_key if permission_key is not None else DEFAULT_KEY
        logger.info(
            "[PermissionEngine] permission.rail.init intercept=all_tools optional_tool_tags=%s key=%s",
            sorted(self._tool_names),
            self._key,
        )

    @property
    def permission_key(self) -> str:
        """The key of the permission config this rail decides with."""
        return self._key

    def _normalize_tool_name(self, tool_name: str) -> str:
        """Normalize tool name using aliases.

        Maps tool names from openjiuwen.harness.tools to mcp_* names used in config.
        """
        return TOOL_NAME_ALIASES.get(tool_name, tool_name)

    def _get_auto_confirm_key(self, tool_call: ToolCall) -> str:
        """Generate a conservative session auto-confirm key for the tool call."""
        return compute_auto_confirm_key(tool_call)

    @staticmethod
    def build_shell_auto_confirm_key(tool_name: str, command: str) -> str:
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

    def _registered_config(self) -> dict[str, Any]:
        """Deep copy of the config registered under this rail's key."""
        return deepcopy(dict(get_permission_manager().get_config(self._key)))

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
        # Use the trusted_dirs the host put into the registered config's
        # ``runtime`` so persist-time external-path detection matches the
        # runtime check.
        runtime = self._registered_config().get("runtime")
        raw_dirs = runtime.get("trusted_dirs") if isinstance(runtime, dict) else None
        trusted_dirs = [Path(d) for d in raw_dirs] if isinstance(raw_dirs, list) else []
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
        self, normalized_name: str, tool_args: dict
    ) -> bool:
        """工具级「始终允许」与 file_guard 路径白名单：先合并磁盘，再写盘。"""
        from openjiuwen.harness.security.patterns import merge_file_guard_access_allows

        # Read from on-disk snapshot first (via host callback) so that
        # entries the user already deleted from config.yaml are NOT
        # restored.  Fall back to the registered config only when the snapshot
        # is unavailable.
        base_cfg: PermissionsSection | None = None
        if self._host.get_permissions_snapshot is not None:
            try:
                snap = self._host.get_permissions_snapshot()
                if isinstance(snap, dict):
                    base_cfg = cast(PermissionsSection, snap)
            except Exception:
                logger.debug(
                    "[PermissionEngine] permission.persist.snapshot_failed",
                    exc_info=True,
                )
        if base_cfg is None:
            base_cfg = cast(PermissionsSection, self._registered_config())

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

        # Whoever persists the rule refreshes the registered config.  A host
        # callback does both; with the built-in yaml writer the rail is the
        # persister, so it refreshes after a successful write.
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
        else:
            persisted = bool(
                write_permissions_section_to_agent_config_yaml(
                    self._host.permission_yaml_path,
                    cfg,
                )
            )
            if persisted:
                self._publish_persisted(cast(dict[str, Any], cfg))
        return persisted

    def _publish_persisted(self, cfg: dict[str, Any]) -> None:
        """Register ``cfg`` under this rail's key, keeping the registered ``runtime``."""
        merged = dict(cfg)
        runtime = self._registered_config().get("runtime")
        if runtime is not None and "runtime" not in merged:
            merged["runtime"] = runtime
        get_permission_manager().update_config(merged, self._key)

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
                        permission_key=self._key,
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
            checks_active = self._host.tool_permission_checks_active
            if checks_active is not None and not checks_active():
                return self.approve()
            try:
                result = await get_permission_manager().check_permission(
                    normalized_name,
                    tool_args,
                    self._key,
                )
            except Exception:
                logger.error(
                    "[PermissionEngine] permission.rail.check_failed tool=%s normalized=%s key=%s",
                    tool_name,
                    normalized_name,
                    self._key,
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

            # Batch-scoped allow (P3-2 批次级授权): during resume replay of an
            # interrupted parallel batch, an identical sibling call (same tool
            # name AND same arguments) was already approved by the user
            # (allow_once) — approve this one too instead of issuing yet
            # another card. The key is an argument fingerprint, deliberately
            # NOT the session auto_confirm key: a sibling with different
            # arguments must not inherit the approval (no silent consent
            # expansion). Scoped to the replay only; a fresh batch in a later
            # iteration re-asks.
            extra = getattr(ctx, "extra", None)
            batch_allow_keys = extra.get(RESUME_BATCH_ALLOW_KEYS) if isinstance(extra, dict) else None
            if isinstance(batch_allow_keys, (set, frozenset, dict)) and batch_allow_keys:
                batch_allow_key = compute_batch_allow_key(tool_call)
                if batch_allow_key and batch_allow_key in batch_allow_keys:
                    logger.info(
                        "[PermissionEngine] permission.batch_allow.hit tool=%s key=%s",
                        tool_name,
                        batch_allow_key,
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
                    if confirm_payload.approved and confirm_payload.auto_confirm and confirm_payload.persist_allow:
                        persisted = self._persist_allow_always(normalized_name, tool_args)
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
            ))

        persisted = False
        if payload.approved and payload.auto_confirm and payload.persist_allow:
            persisted = self._persist_allow_always(normalized_name, tool_args)
            logger.info(
                "[PermissionEngine] permission.persist.result tool=%s confirm_path=%s persisted=%s persist_allow=%s",
                tool_name,
                self._confirm_path_label(),
                persisted,
                payload.persist_allow,
            )
        elif payload.approved and payload.auto_confirm and not payload.persist_allow:
            logger.info(
                "[PermissionEngine] permission.session_only tool=%s confirm_path=%s auto_confirm_key=%s",
                tool_name,
                self._confirm_path_label(),
                auto_confirm_key,
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
        tool_name = tool_call.name if tool_call else ""
        tool_args = self.parse_tool_args(tool_call)

        parts = [
            f"**工具 `{tool_name}` 需要授权才能执行**\n\n",
            "请确认是否允许该操作。\n\n",
        ]

        args_preview = self.format_args_preview(tool_args)
        if args_preview and args_preview != "{}":
            parts.append(f"参数：\n```json\n{args_preview}\n```\n")

        parts.append(f"\n匹配规则：`{result.matched_rule or 'N/A'}`")

        external_paths = getattr(result, "external_paths", None) or []
        if external_paths:
            parts.append(f"\n\n**路径：** `{', '.join(external_paths)}`")

        parts.append(self._build_always_allow_hint(tool_call))

        return "".join(parts)

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
            shell_key = self.build_shell_auto_confirm_key(tool_name, str(cmd or ""))
            if shell_key:
                return (
                    f'\n\n> 选择「本会话内允许」可在本会话内自动放行 ``{shell_key}`` 类调用；'
                    f'选择「永久记住」可将此规则写回磁盘，所有会话均自动放行。'
                )
            if auto_confirm_key:
                return (
                    f'\n\n> 选择「本会话内允许」可在本会话内自动放行 ``{auto_confirm_key}`` 类调用。'
                )
            return ""

        if auto_confirm_key:
            path_desc = f"在 ``{path_hint}`` 下" if path_hint else ""
            return (
                f'\n\n> 选择「本会话内允许」可在本会话内自动放行 ``{tool_name}`` 类工具{path_desc}的调用；'
                f'选择「永久记住」可将此规则写回磁盘，所有会话均自动放行。'
            )
        return ""


__all__ = [
    "PermissionInterruptRail",
]
