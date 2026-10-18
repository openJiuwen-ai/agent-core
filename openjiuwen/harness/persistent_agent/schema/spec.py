# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Flat configuration extension; runtime handles never belong to this Spec."""

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from pydantic import ConfigDict, Field

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

    from .build_context import PersistentBuildContext

# Development placeholder for the shared-file size limit; the published value is aligned with the host.
SHARE_FILE_DEFAULT_MAX_BYTES = 32 * 1024 * 1024
# Guard against a misconfigured limit, not a delivery policy.
SHARE_FILE_MAX_BYTES_CEILING = 1024 * 1024 * 1024

# Native context-engine limits that silently drop window messages by count.
_WINDOW_TRUNCATION_LIMITS = ("max_context_message_num", "default_window_message_num", "default_window_round_num")


def _truncating_limits(config) -> list[str]:
    """Names of count-based truncation limits set in a context engine config, given as an object or a dict."""
    if config is None:
        return []
    read = config.get if isinstance(config, dict) else lambda name: getattr(config, name, None)
    return [name for name in _WINDOW_TRUNCATION_LIMITS if read(name) is not None]


def _share_file_rail(context: "PersistentBuildContext", workspace_access, max_bytes: int):
    """Files are listed for handoff under the read authorization; reading is not permission to publish."""
    from ..execution.share_file import ShareFileRail

    return ShareFileRail(context.execution, workspace_access, context.binding.control.registrations, max_bytes)


def _record_rail(context: "PersistentBuildContext"):
    from ..execution.record_tool import ExecutionRecordRail

    return ExecutionRecordRail(context)


def _context_engine_config(config):
    """Restore the typed runtime config after the Spec's JSON publication roundtrip."""
    from openjiuwen.core.context_engine.schema.config import ContextEngineConfig

    return ContextEngineConfig.model_validate(config)


class PersistentAgentSpec(DeepAgentSpec):
    """Construction-only v1 spec. Declaring a policy does not implement execution."""

    model_config = ConfigDict(extra="forbid")

    # Identifies this serialized Spec shape so unsupported configuration versions fail closed.
    schema_version: Literal[1] = 1
    # Advertises only capabilities the runtime can validate and bind for this Agent.
    enabled_features: frozenset[Literal["construction", "workspace_read", "workspace_write", "window_management"]] = (
        Field(default=frozenset({"construction"}), min_length=1)
    )
    # Largest file share_file may hand off, in bytes actually read; the host checks the same published limit.
    share_file_max_bytes: int = Field(default=SHARE_FILE_DEFAULT_MAX_BYTES, gt=0, le=SHARE_FILE_MAX_BYTES_CEILING)
    # Most earlier Executions a continuation reads back through; set by the deployment, required for continuation.
    continuation_chain_limit: int | None = Field(default=None, gt=0)
    # Capacity check of every final model request (F_20 D-09). Setting either field asks for it; the model
    # request config's context_window and max_tokens are then required too. No default numbers are assumed.
    model_input_limit_tokens: int | None = Field(default=None, gt=0)
    capacity_safety_margin_tokens: int | None = Field(default=None, ge=0)

    def fingerprint(self) -> str:
        """Hash the entire JSON publication, not runtime handles or only base fields."""
        try:
            data = self.model_dump(mode="json")
            data["enabled_features"] = sorted(data["enabled_features"])
            canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        except Exception as exc:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, cause=exc, error_msg="Spec is not JSON serializable"
            ) from exc
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def resolve_parts(self, context=None):
        """Keep the parent's public parts pipeline and retain rollback inventory."""
        from .build_context import PersistentBuildContext

        if not isinstance(context, PersistentBuildContext) or not context.binding.attempted:
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="use Persistent construction entry")
        if "workspace_write" in self.enabled_features:
            if "workspace_read" not in self.enabled_features:
                raise build_error(
                    StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                    error_msg="workspace_write requires workspace_read",
                )
            if not self.enable_sys_operation:
                raise build_error(
                    StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                    error_msg="workspace_write requires enable_sys_operation",
                )
            if context.ports.workspace_write_access is None:
                raise build_error(
                    StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                    error_msg="workspace_write_access is required for workspace_write",
                )
        if "window_management" in self.enabled_features:
            from ..execution.capacity import capacity_limits

            if capacity_limits(self) is None:
                raise build_error(
                    StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                    error_msg="window_management requires explicit model capacity limits",
                )
        parts = super().resolve_parts(context)
        context.binding.parts = parts
        if parts.config.context_engine_config is not None:
            parts.config.context_engine_config = _context_engine_config(parts.config.context_engine_config)
        from openjiuwen.harness.rails.context_engineer import ContextAssembleRail, ContextProcessorRail
        from openjiuwen.harness.rails.personal_context import PersonalContextRail

        # The window is managed per Execution: these rails refresh prompt content or compress history
        # on every model call, which breaks the cacheable prefix and Persistent's own switch timing.
        # Rejected rather than dropped, so a published rail is never switched off silently.
        unsupported = (ContextAssembleRail, PersonalContextRail, ContextProcessorRail)
        mounted = sorted({type(rail).__name__ for rail in parts.rails if isinstance(rail, unsupported)})
        if mounted:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg=f"Persistent windows are managed per Execution; remove {', '.join(mounted)}",
            )
        truncating = _truncating_limits(self.context_engine_config)
        if truncating:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg=f"Persistent windows are not truncated by count; unset {', '.join(truncating)}",
            )
        if "workspace_read" in self.enabled_features:
            if not self.enable_sys_operation:
                raise build_error(
                    StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                    error_msg="workspace_read requires enable_sys_operation",
                )
            workspace_access = context.ports.workspace_access
            if workspace_access is None:
                raise build_error(
                    StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                    error_msg="workspace_access is required for workspace_read",
                )
            from ..workspace.file_access import PersistentWorkspaceReadRail

            parts.rails.append(PersistentWorkspaceReadRail(context.execution, workspace_access))
            parts.rails.append(_share_file_rail(context, workspace_access, self.share_file_max_bytes))
        if "workspace_write" in self.enabled_features:
            workspace_write_access = context.ports.workspace_write_access
            if workspace_write_access is None:
                raise build_error(
                    StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                    error_msg="workspace_write_access is required for workspace_write",
                )
            from ..workspace.file_access import PersistentWorkspaceWriteRail

            parts.rails.append(PersistentWorkspaceWriteRail(context.execution, workspace_write_access))
        from ..execution.model_rail import PersistentModelCallRail
        from ..execution.stream_lifecycle import NativeStreamTaskLifecycle
        from ..execution.tool_intent_rail import PersistentToolIntentRail

        parts.config.stream_task_lifecycle = NativeStreamTaskLifecycle()
        if context.ports.group_chat is not None:
            from ..tools.group_history import SEARCH_TOOL_NAME, PersistentGroupMessageSearchTool

            if any(card.name == SEARCH_TOOL_NAME for card in parts.tool_cards):
                raise build_error(
                    StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                    error_msg=f"{SEARCH_TOOL_NAME} is reserved for Persistent group history",
                )
            tool = PersistentGroupMessageSearchTool(execution=context.execution, group_chat=context.ports.group_chat)
            parts.tool_instances.append(tool)
            parts.tool_cards.append(tool.card)
            parts.config.tools = list(parts.tool_cards)
        from ..execution.session_state import WindowStore
        from ..tools.session_history import SEARCH_TOOL_NAME as SESSION_HISTORY_TOOL_NAME
        from ..tools.session_history import PersistentSessionHistorySearchTool

        if any(card.name == SESSION_HISTORY_TOOL_NAME for card in parts.tool_cards):
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg=f"{SESSION_HISTORY_TOOL_NAME} is reserved for Persistent session history",
            )
        window_backend = context.ports.window_backend
        if window_backend is None:
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="window_backend is required for Persistent session history",
            )
        tool = PersistentSessionHistorySearchTool(
            execution=context.execution,
            window_store=WindowStore(window_backend),
        )
        parts.tool_instances.append(tool)
        parts.tool_cards.append(tool.card)
        parts.config.tools = list(parts.tool_cards)
        if "window_management" in self.enabled_features:
            from ..tools.new_context import NEW_CONTEXT_TOOL_NAME, PersistentNewContextTool

            if any(card.name == NEW_CONTEXT_TOOL_NAME for card in parts.tool_cards):
                raise build_error(
                    StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                    error_msg=f"{NEW_CONTEXT_TOOL_NAME} is reserved for Persistent window management",
                )
            tool = PersistentNewContextTool(context.binding.control)
            parts.tool_instances.append(tool)
            parts.tool_cards.append(tool.card)
            parts.config.tools = list(parts.tool_cards)
        # Inert until an execution entry attaches its emitter to this binding.
        parts.rails.append(PersistentModelCallRail(context.binding, spec=self))
        if self.continuation_chain_limit is not None:
            # Declared for every Execution of the Spec, so tool definitions stay the same across a Request.
            parts.rails.append(_record_rail(context))
        # Every Persistent tool call passes through the write-intent barrier.
        parts.rails.append(PersistentToolIntentRail(context.binding))
        return parts

    def build(self, context: "PersistentBuildContext | None" = None) -> "DeepAgent":
        """Validate and bind one invocation, then build the original DeepAgent class."""
        from ..factory import create_persistent_agent

        return create_persistent_agent(self, context=context)
