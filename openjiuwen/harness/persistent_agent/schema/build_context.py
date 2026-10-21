# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Live, single-use construction context. Never serialize this object."""

import weakref
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING
from uuid import uuid4

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.single_agent import Session
from openjiuwen.harness.schema.build_context import BuildContext

from ..execution.control import ExecutionControl
from ..ports import PersistentPorts
from .execution import ExecutionContext

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent
    from openjiuwen.harness.factory import DeepAgentParts

    from .spec import PersistentAgentSpec


@dataclass
class ConstructionBinding:
    """Shared only by derived views of ONE invocation's build context."""

    owner_id: str = field(default_factory=lambda: "persistent_" + uuid4().hex)
    lock: Lock = field(default_factory=Lock, repr=False)
    attempted: bool = False
    execution: ExecutionContext | None = None
    session: Session | None = field(default=None, repr=False)
    ports: PersistentPorts | None = field(default=None, repr=False)
    agent_ref: weakref.ReferenceType | None = field(default=None, repr=False)
    parts: "DeepAgentParts | None" = field(default=None, repr=False)
    runtime: object | None = field(default=None, repr=False)
    control: ExecutionControl = field(default_factory=ExecutionControl, repr=False)


@dataclass(kw_only=True)
class PersistentBuildContext(BuildContext):
    """Per-invocation host data and single-use binding for native construction."""

    execution: ExecutionContext
    session: Session
    ports: PersistentPorts
    system_prompt_addendum: str | None = None
    binding: ConstructionBinding = field(default_factory=ConstructionBinding, init=False, repr=False)

    def __post_init__(self):
        """Allocate a unique tool owner for this construction context."""
        if self.tool_owner_id is not None:
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="Persistent owner is allocated per construction"
            )
        self.tool_owner_id = self.binding.owner_id

    def derive(self, **overrides) -> "PersistentBuildContext":
        """Copy ordinary build fields while retaining this invocation binding."""
        clone = super().derive(**overrides)
        clone.extras = dict(clone.extras)
        return clone

    def validate(self, spec: "PersistentAgentSpec") -> None:
        """Static validation only; host ports and the window backend are not contacted."""
        if not isinstance(self.execution, ExecutionContext) or not isinstance(self.session, Session):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="real execution and Agent Session required"
            )
        if not isinstance(self.ports, PersistentPorts):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="PersistentPorts required")
        if self.system_prompt_addendum is not None and not isinstance(self.system_prompt_addendum, str):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="invalid system_prompt_addendum")
        if self.session.get_session_id() != self.execution.session_id:
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="Session mismatch")
        if spec.card is None or spec.card.id != self.execution.agent_id:
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="AgentCard mismatch")
        if spec.fingerprint() != self.execution.config_fingerprint:
            raise build_error(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg="config fingerprint mismatch")
        if spec.enabled_features != self.execution.capability_snapshot:
            raise build_error(StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR, error_msg="capability snapshot mismatch")
        if self.tool_owner_id != self.binding.owner_id:
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="owner mismatch")
        if "workspace_write" in spec.enabled_features:
            if "workspace_read" not in spec.enabled_features:
                raise build_error(
                    StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                    error_msg="workspace_write requires workspace_read",
                )
            if not spec.enable_sys_operation:
                raise build_error(
                    StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                    error_msg="workspace_write requires enable_sys_operation",
                )
            if self.ports.workspace_write_access is None:
                raise build_error(
                    StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                    error_msg="workspace_write_access is required for workspace_write",
                )
        if "workspace_read" in spec.enabled_features and self.ports.workspace_access is None:
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="workspace_access is required for workspace_read",
            )
        if "workspace_read" in spec.enabled_features and not spec.enable_sys_operation:
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="workspace_read requires enable_sys_operation",
            )
        self.ports.validate()

    def claim(self, spec: "PersistentAgentSpec") -> None:
        """Reserve this binding once; a failed build also needs a fresh context."""
        with self.binding.lock:
            if self.binding.attempted:
                raise build_error(
                    StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="construction context already used"
                )
            self.validate(spec)
            self.binding.attempted = True
            self.binding.execution = self.execution
            self.binding.session = self.session
            self.binding.ports = self.ports

    def validate_binding(self, agent: "DeepAgent") -> None:
        """Verify the exact constructed object and immutable invocation identity."""
        bound = self.binding
        if (
            bound.agent_ref is None
            or bound.agent_ref() is not agent
            or self.execution != bound.execution
            or self.session is not bound.session
            or self.ports is not bound.ports
            or self.tool_owner_id != bound.owner_id
            or self.session.get_session_id() != bound.execution.session_id
        ):
            raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="construction binding mismatch")
