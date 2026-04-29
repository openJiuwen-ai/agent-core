# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""PermissionInterruptRail - tool permission guardrail using ConfirmInterruptRail.

Implements permission checks via PermissionEngine and triggers HITL interrupts
for ASK decisions using the built-in interrupt rail flow.
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
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
    merge_external_directory_allow_into_permissions,
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
            self._engine = PermissionEngine(
                config=self._static_config,
                llm=llm,
                model_name=model_name,
                workspace_root=workspace_root,
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
        tool_args = self._parse_tool_args(tool_call)

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
        auto_confirm: bool,
        session: Any,
        auto_confirm_key: str,
        persisted: bool,
    ) -> bool:
        return bool(auto_confirm and session is not None and auto_confirm_key and not persisted)

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

    def _collect_external_directory_persist_paths(
        self,
        normalized_name: str,
        tool_args: dict,
        permissions_cfg: dict[str, Any] | PermissionsSection,
    ) -> list[str]:
        """若本次调用对外部路径为 ASK，返回应写入白名单的路径列表；否则返回空列表。"""
        from openjiuwen.harness.security.checker import ExternalDirectoryChecker

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
        try:
            checker = ExternalDirectoryChecker(
                permissions_cfg, workspace_root=workspace
            )
            ext_result = checker.check_external_paths(normalized_name, tool_args)
        except Exception:
            logger.warning(
                "[PermissionEngine] permission.persist.external.check_failed",
                exc_info=True,
            )
            return []
        if ext_result is None or ext_result.permission != PermissionLevel.ASK:
            return []
        paths = ext_result.external_paths or []
        return list(paths) if paths else []

    def _persist_allow_always(
        self, normalized_name: str, tool_args: dict
    ) -> bool:
        """工具级「始终允许」与 external_directory 白名单：先合并内存，再写盘。"""
        cfg = cast(
            PermissionsSection,
            deepcopy(self._engine.config),
        )
        cfg, ok_tool = merge_permission_allow_rule_into_permissions(
            cfg, normalized_name, tool_args
        )
        ext_paths = self._collect_external_directory_persist_paths(
            normalized_name, tool_args, cfg
        )
        ok_ext = False
        if ext_paths:
            cfg, ok_ext = merge_external_directory_allow_into_permissions(cfg, ext_paths)
        if not ok_tool and not ok_ext:
            return False

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

    async def resolve_interrupt(
        self,
        ctx: AgentCallbackContext,
        tool_call: Optional[ToolCall],
        user_input: Optional[Any],
        auto_confirm_config: Optional[dict] = None,
    ):
        tool_name = tool_call.name if tool_call is not None else ""
        normalized_name = self._normalize_tool_name(tool_name)
        tool_args = self._parse_tool_args(tool_call)
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
            fresh: dict | None = None
            if self._host.get_permissions_snapshot is not None:
                try:
                    snap = self._host.get_permissions_snapshot()
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
            result = await self._engine.check_permission(
                tool_name=normalized_name,
                tool_args=tool_args,
            )

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
                    if confirm_payload.approved and confirm_payload.auto_confirm:
                        persisted = self._persist_allow_always(normalized_name, tool_args)
                    logger.info(
                        "[PermissionEngine] permission.persist.result tool=%s confirm_path=hosted persisted=%s",
                        tool_name,
                        persisted,
                    )
                    if self._should_store_auto_confirm(
                        auto_confirm=confirm_payload.auto_confirm,
                        session=ctx.session,
                        auto_confirm_key=auto_confirm_key,
                        persisted=persisted,
                    ):
                        self._store_auto_confirm(ctx, auto_confirm_key)
                    if confirm_payload.approved:
                        decision = "allow_always" if confirm_payload.auto_confirm else "allow_once"
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
        payload = self._parse_confirm_payload(user_input)
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
        if payload.approved and payload.auto_confirm:
            persisted = self._persist_allow_always(normalized_name, tool_args)
            logger.info(
                "[PermissionEngine] permission.persist.result tool=%s confirm_path=%s persisted=%s",
                tool_name,
                self._confirm_path_label(),
                persisted,
            )

        if self._should_store_auto_confirm(
            auto_confirm=payload.auto_confirm,
            session=ctx.session,
            auto_confirm_key=auto_confirm_key,
            persisted=persisted,
        ):
            self._store_auto_confirm(ctx, auto_confirm_key)

        if payload.approved:
            decision = "allow_always" if payload.auto_confirm else "allow_once"
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
    def _parse_tool_args(tool_call: Optional[ToolCall]) -> dict:
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
    def _parse_confirm_payload(user_input: Any) -> Optional[PermissionConfirmResponse]:
        if isinstance(user_input, PermissionConfirmResponse):
            return user_input
        if isinstance(user_input, ConfirmPayload):
            return PermissionConfirmResponse(
                approved=user_input.approved,
                feedback=user_input.feedback,
                auto_confirm=user_input.auto_confirm,
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
            )
        if isinstance(user_input, str):
            try:
                raw_payload = json.loads(user_input)
            except Exception:
                return None
            if not isinstance(raw_payload, dict):
                return None
            return PermissionInterruptRail._parse_confirm_payload(raw_payload)
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
    def _format_args_preview(tool_args: dict) -> str:
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
        tool_args = self._parse_tool_args(tool_call)

        parts = [
            f"**工具 `{tool_name}` 需要授权才能执行**\n\n",
            "请确认是否允许该操作。\n\n",
        ]

        args_preview = self._format_args_preview(tool_args)
        if args_preview and args_preview != "{}":
            parts.append(f"参数：\n```json\n{args_preview}\n```\n")

        parts.append(f"\n匹配规则：`{result.matched_rule or 'N/A'}`")

        external_paths = getattr(result, "external_paths", None) or []
        if external_paths:
            parts.append(f"\n\n**外部路径：** `{', '.join(external_paths)}`")

        parts.append(self._build_always_allow_hint(tool_call))

        return "".join(parts)

    def _build_always_allow_hint(self, tool_call: Optional[ToolCall]) -> str:
        if tool_call is None:
            return ""
        
        tool_name = tool_call.name or ""
        tool_args = self._parse_tool_args(tool_call)
        auto_confirm_key = self._get_auto_confirm_key(tool_call)
        
        if tool_name == "bash":
            cmd = tool_args.get("command", tool_args.get("cmd", ""))
            shell_key = self._build_shell_auto_confirm_key(tool_name, str(cmd or ""))
            if shell_key:
                return (
                    '\n\n> 若选择「记住 / 总是允许」并提交 ``auto_confirm: true``，'
                    "将合并权限配置并尝试写回磁盘（与仅本次允许相对）。"
                )
        if tool_name == "mcp_exec_command":
            cmd = tool_args.get("command", tool_args.get("cmd", ""))
            if self._build_shell_auto_confirm_key(tool_name, str(cmd or "")):
                return (
                    '\n\n> 若选择「记住 / 总是允许」并提交 ``auto_confirm: true``，'
                    "将合并权限配置并尝试写回磁盘（与仅本次允许相对）。"
                )
        if tool_name == "create_terminal":
            cmd = tool_args.get("command", tool_args.get("cmd", ""))
            if self._build_shell_auto_confirm_key(tool_name, str(cmd or "")):
                return (
                    '\n\n> 若选择「记住 / 总是允许」并提交 ``auto_confirm: true``，'
                    "将合并权限配置并尝试写回磁盘（与仅本次允许相对）。"
                )
        if auto_confirm_key:
            return (
                f'\n\n> 若选择「记住 / 总是允许」并提交 ``auto_confirm: true``，'
                f"将合并权限配置并写回磁盘；同时可在本会话内自动放行 ``{auto_confirm_key}`` 类调用。"
            )
        return ""


__all__ = [
    "PermissionInterruptRail",
]
