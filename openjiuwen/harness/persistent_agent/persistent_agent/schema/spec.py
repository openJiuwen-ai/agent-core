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


class PersistentAgentSpec(DeepAgentSpec):
    """Construction-only v1 spec. Declaring a policy does not implement execution."""

    model_config = ConfigDict(extra="forbid")

    # Identifies this serialized Spec shape so unsupported configuration versions fail closed.
    schema_version: Literal[1] = 1
    # Advertises only capabilities the runtime can validate and bind for this Agent.
    enabled_features: frozenset[Literal["construction", "workspace_read"]] = Field(
        default=frozenset({"construction"}), min_length=1
    )

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
        parts = super().resolve_parts(context)
        context.binding.parts = parts
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
            from ..workspace.rail import PersistentWorkspaceReadRail

            parts.rails.append(PersistentWorkspaceReadRail(context.execution, workspace_access))
        from ..execution.model_rail import PersistentModelCallRail
        from ..execution.stream_lifecycle import NativeStreamTaskLifecycle

        parts.config.stream_task_lifecycle = NativeStreamTaskLifecycle()
        # Inert until an execution entry attaches its emitter to this binding.
        parts.rails.append(PersistentModelCallRail(context.binding))
        return parts

    def build(self, context: "PersistentBuildContext | None" = None) -> "DeepAgent":
        """Validate and bind one invocation, then build the original DeepAgent class."""
        from ..factory import create_persistent_agent

        return create_persistent_agent(self, context=context)
