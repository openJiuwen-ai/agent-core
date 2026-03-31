# coding: utf-8
"""Serializable DeepAgent specifications for network distribution.

All types in this module are Pydantic BaseModels that support full JSON
round-trip via ``model_dump_json()`` / ``model_validate_json()``.
Call ``build()`` on a spec to materialize the corresponding runtime object.
"""
from __future__ import annotations

import inspect
from typing import Any, Optional

from pydantic import BaseModel
from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
)

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import ToolCard, McpServerConfig
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.config import LocalWorkConfig, SandboxGatewayConfig
from openjiuwen.core.sys_operation.sys_operation import SysOperationCard
from openjiuwen.deepagents.schema.config import (
    DEFAULT_ACR_BASE_URL,
    DEFAULT_AUDIO_HTTP_TIMEOUT,
    DEFAULT_MAX_AUDIO_BYTES,
    DEFAULT_OPENAI_AUDIO_QA_MODEL,
    DEFAULT_OPENAI_AUDIO_TRANSCRIPTION_MODEL,
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_VISION_MODEL,
    AudioModelConfig,
    SubAgentConfig,
    VisionModelConfig,
)
from openjiuwen.deepagents.schema.stop_condition import StopCondition
from openjiuwen.deepagents.workspace.workspace import Workspace

# ---------------------------------------------------------------------------
# Rail type registry
# ---------------------------------------------------------------------------

_RAIL_TYPE_REGISTRY: dict[str, type[AgentRail]] = {}


def register_rail_type(name: str, cls: type[AgentRail]) -> None:
    """Register a rail class so it can be referenced by name in RailSpec."""
    _RAIL_TYPE_REGISTRY[name] = cls


def _ensure_builtin_rails_registered() -> None:
    """Lazily populate the registry with built-in DeepAgent rails."""
    if _RAIL_TYPE_REGISTRY:
        return
    from openjiuwen.deepagents.rails import (
        TaskPlanningRail,
        SkillUseRail,
        SubagentRail,
        ToolPromptRail,
    )

    _RAIL_TYPE_REGISTRY.update({
        "task_planning": TaskPlanningRail,
        "skill_use": SkillUseRail,
        "subagent": SubagentRail,
        "tool_prompt": ToolPromptRail,
    })


# ---------------------------------------------------------------------------
# Leaf spec types
# ---------------------------------------------------------------------------


class StopConditionSpec(BaseModel):
    """Serializable subset of StopCondition (excludes custom callable)."""

    max_iterations: Optional[int] = None
    max_token_usage: Optional[int] = None
    completion_promise: Optional[str] = None
    timeout_seconds: Optional[float] = None

    def build(self) -> StopCondition:
        return StopCondition(**self.model_dump())


class VisionModelSpec(BaseModel):
    """Serializable mirror of VisionModelConfig dataclass."""

    api_key: str = ""
    base_url: str = DEFAULT_OPENAI_BASE_URL
    model: str = DEFAULT_OPENAI_VISION_MODEL
    max_retries: int = 3

    def build(self) -> VisionModelConfig:
        return VisionModelConfig(**self.model_dump())


class AudioModelSpec(BaseModel):
    """Serializable mirror of AudioModelConfig dataclass."""

    api_key: str = ""
    base_url: str = DEFAULT_OPENAI_BASE_URL
    transcription_model: str = DEFAULT_OPENAI_AUDIO_TRANSCRIPTION_MODEL
    question_answering_model: str = DEFAULT_OPENAI_AUDIO_QA_MODEL
    max_retries: int = 3
    http_timeout: int = DEFAULT_AUDIO_HTTP_TIMEOUT
    max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES
    acr_access_key: str = ""
    acr_access_secret: str = ""
    acr_base_url: str = DEFAULT_ACR_BASE_URL

    def build(self) -> AudioModelConfig:
        return AudioModelConfig(**self.model_dump())


class WorkspaceSpec(BaseModel):
    """Serializable workspace specification.

    Directories are auto-populated at build time based on language.
    """

    root_path: str = "./"
    language: str = "cn"

    def build(self) -> Workspace:
        return Workspace(root_path=self.root_path, language=self.language)


class ProgressiveToolSpec(BaseModel):
    """Progressive tool exposure configuration.

    When present on DeepAgentSpec, progressive tool loading is enabled.
    """

    enabled: bool = True
    always_visible_tools: list[str] = []
    default_visible_tools: list[str] = []
    max_loaded_tools: int = 12


class SysOperationSpec(BaseModel):
    """Serializable system operation specification."""

    id: str
    mode: OperationMode = OperationMode.LOCAL
    work_config: Optional[LocalWorkConfig] = None
    gateway_config: Optional[SandboxGatewayConfig] = None

    def build_card(self) -> SysOperationCard:
        return SysOperationCard(
            id=self.id,
            mode=self.mode,
            work_config=self.work_config,
            gateway_config=self.gateway_config,
        )


class RailSpec(BaseModel):
    """Declarative rail reference resolved via the rail type registry."""

    type: str
    params: dict[str, Any] = {}

    def build(self, *, language: str) -> AgentRail:
        _ensure_builtin_rails_registered()
        cls = _RAIL_TYPE_REGISTRY.get(self.type)
        if cls is None:
            raise ValueError(
                f"Unknown rail type '{self.type}'. "
                f"Registered types: {list(_RAIL_TYPE_REGISTRY)}"
            )
        kw = dict(self.params)
        sig = inspect.signature(cls.__init__)
        if "language" in sig.parameters:
            kw.setdefault("language", language)
        return cls(**kw)


# ---------------------------------------------------------------------------
# SubAgentSpec
# ---------------------------------------------------------------------------


class SubAgentSpec(BaseModel):
    """Serializable sub-agent specification."""

    agent_card: AgentCard
    system_prompt: str
    tools: list[ToolCard] = []
    mcps: list[McpServerConfig] = []
    model: Optional[TeamModelConfig] = None
    rails: Optional[list[RailSpec]] = None
    skills: Optional[list[str]] = None

    def build(self, *, parent_model: Model, language: str) -> SubAgentConfig:
        """Materialize into a runtime SubAgentConfig.

        Args:
            parent_model: Fallback model when self.model is None.
            language: Resolved language for rail construction.
        """
        resolved_model = self.model.build() if self.model else None
        resolved_rails = (
            [r.build(language=language) for r in self.rails]
            if self.rails
            else None
        )
        return SubAgentConfig(
            agent_card=self.agent_card,
            system_prompt=self.system_prompt,
            tools=list(self.tools),
            mcps=list(self.mcps),
            model=resolved_model,
            rails=resolved_rails,
            skills=self.skills,
        )


# ---------------------------------------------------------------------------
# DeepAgentSpec
# ---------------------------------------------------------------------------


class DeepAgentSpec(BaseModel):
    """Fully JSON-serializable specification for constructing a DeepAgent.

    Serialize with ``model_dump_json()`` for network distribution.
    Deserialize with ``model_validate_json()`` and call ``build()``
    to obtain a live DeepAgent instance.
    """

    model: Optional[TeamModelConfig] = None
    card: Optional[AgentCard] = None
    system_prompt: Optional[str] = None
    tools: Optional[list[ToolCard]] = None
    mcps: Optional[list[McpServerConfig]] = None
    subagents: Optional[list[SubAgentSpec]] = None
    rails: Optional[list[RailSpec]] = None
    stop_condition: Optional[StopConditionSpec] = None
    enable_task_loop: bool = False
    max_iterations: int = 15
    workspace: Optional[WorkspaceSpec] = None
    skills: Optional[list[str]] = None
    sys_operation: Optional[SysOperationSpec] = None
    language: Optional[str] = None
    prompt_mode: Optional[str] = None
    vision_model: Optional[VisionModelSpec] = None
    audio_model: Optional[AudioModelSpec] = None
    enable_task_planning: bool = False
    completion_timeout: float = 600.0
    progressive_tool: Optional[ProgressiveToolSpec] = None

    def build(self) -> "DeepAgent":
        """Materialize a live DeepAgent from this spec."""
        from openjiuwen.core.runner.runner import Runner
        from openjiuwen.deepagents.factory import create_deep_agent
        from openjiuwen.deepagents.prompts import resolve_language

        llm_model = self.model.build() if self.model else None
        language = resolve_language(self.language)

        stop_condition = self.stop_condition.build() if self.stop_condition else None
        vision_config = self.vision_model.build() if self.vision_model else None
        audio_config = self.audio_model.build() if self.audio_model else None
        workspace = self.workspace.build() if self.workspace else None

        rails = (
            [r.build(language=language) for r in self.rails]
            if self.rails
            else None
        )
        subagents = (
            [s.build(parent_model=llm_model, language=language) for s in self.subagents]
            if self.subagents
            else None
        )

        sys_operation = None
        if self.sys_operation:
            card = self.sys_operation.build_card()
            Runner.resource_mgr.add_sys_operation(card)
            sys_operation = Runner.resource_mgr.get_sys_operation(card.id)

        return create_deep_agent(
            model=llm_model,
            card=self.card,
            system_prompt=self.system_prompt,
            tools=self.tools,
            mcps=self.mcps,
            subagents=subagents,
            rails=rails,
            stop_condition=stop_condition,
            enable_task_loop=self.enable_task_loop,
            max_iterations=self.max_iterations,
            workspace=workspace,
            skills=self.skills,
            sys_operation=sys_operation,
            language=self.language,
            prompt_mode=self.prompt_mode,
            vision_model_config=vision_config,
            audio_model_config=audio_config,
            enable_task_planning=self.enable_task_planning,
            completion_timeout=self.completion_timeout,
            **self._progressive_tool_kwargs(),
        )

    def _progressive_tool_kwargs(self) -> dict[str, Any]:
        if self.progressive_tool is None:
            return {}
        pt = self.progressive_tool
        return {
            "progressive_tool_enabled": pt.enabled,
            "progressive_tool_always_visible_tools": pt.always_visible_tools,
            "progressive_tool_default_visible_tools": pt.default_visible_tools,
            "progressive_tool_max_loaded_tools": pt.max_loaded_tools,
        }


__all__ = [
    "AudioModelSpec",
    "DeepAgentSpec",
    "ProgressiveToolSpec",
    "RailSpec",
    "StopConditionSpec",
    "SubAgentSpec",
    "SysOperationSpec",
    "VisionModelSpec",
    "WorkspaceSpec",
    "register_rail_type",
]


class TeamModelConfig(BaseModel):
    """Serializable model configuration for a team role."""

    model_client_config: ModelClientConfig
    model_request_config: Optional[ModelRequestConfig] = None

    def build(self) -> "Model":
        """Create a Model instance from this config."""
        return Model(
            model_client_config=self.model_client_config,
            model_config=self.model_request_config,
        )
