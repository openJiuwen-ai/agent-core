# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persistent configuration/identity checks followed by the shared Spec pipeline."""

import weakref
from pathlib import Path

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec

from .execution.binding import InvocationState
from .execution.session_state import offloading_processors
from .schema.build_context import PersistentBuildContext
from .schema.spec import PersistentAgentSpec


def create_persistent_agent(spec: PersistentAgentSpec, *, context: PersistentBuildContext) -> DeepAgent:
    """Build exactly one native DeepAgent, without starting a Persistent task.

    A host-provisioned workspace must already exist. Port methods and the window
    backend are not called; execution behavior belongs to the execution entries.
    Only trusted, side-effect-free construction providers may be supplied.
    """
    if not isinstance(spec, PersistentAgentSpec) or not isinstance(context, PersistentBuildContext):
        raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="Persistent Spec and context required")
    claimed = False
    try:
        # A JSON roundtrip both revalidates mutated specs and excludes live handles.
        snapshot = PersistentAgentSpec.model_validate_json(spec.model_dump_json())
        context.validate(snapshot)
        if any(isinstance(tool, ToolCard) and not tool.stateless for tool in snapshot.tools or ()):
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="stateful ToolCard references are shared; use a fresh BuiltinToolSpec provider",
            )
        if snapshot.workspace is not None and (
            snapshot.auto_create_workspace or not Path(snapshot.workspace.root_path).is_dir()
        ):
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="workspace must be provisioned; disable auto_create_workspace",
            )
        context.claim(snapshot)
        claimed = True
        working = context.derive()
        agent = DeepAgentSpec.build(snapshot, context=working)
        # Providers must not rebind identity while resolving capabilities.
        context.validate(snapshot)
        if (
            context.execution != context.binding.execution
            or context.session is not context.binding.session
            or context.ports is not context.binding.ports
        ):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="context changed during construction")
        offloading = offloading_processors(agent)
        if offloading:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg=f"Persistent windows do not support offloading; remove {', '.join(offloading)}",
            )
        context.binding.agent_ref = weakref.ref(agent)
        context.binding.runtime = InvocationState.capture(agent, context.binding.owner_id)
        context.binding.parts = None
        return agent
    except Exception as exc:
        parts = context.binding.parts if claimed else None
        if parts is not None:
            for tool in parts.tool_instances or ():
                card = tool.card
                if card is not None and not card.stateless and card.id == f"{card.name}_{context.binding.owner_id}":
                    # Never remove another owner's resources or shared stateless tools.
                    if Runner.resource_mgr.get_tool(card.id) is tool:
                        Runner.resource_mgr.remove_tool(card.id)
            context.binding.parts = None
        if isinstance(exc, BaseError):
            raise
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, cause=exc, error_msg="Persistent construction failed"
        ) from exc
