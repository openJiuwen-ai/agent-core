# coding: utf-8
"""Serializable DeepAgent specifications for network distribution.

All types in this module are Pydantic BaseModels that support full JSON
round-trip via ``model_dump_json()`` / ``model_validate_json()``.
Call ``build()`` on a spec to materialize the corresponding runtime object.
"""
from __future__ import annotations

import inspect
from typing import (
    Any,
    Optional,
)

from pydantic import BaseModel

from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.foundation.tool import (
    McpServerConfig,
    ToolCard,
)
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.config import (
    LocalWorkConfig,
    SandboxGatewayConfig,
)
from openjiuwen.core.sys_operation.sys_operation import SysOperationCard
from openjiuwen.harness.schema.config import (
    AudioModelConfig,
    DEFAULT_ACR_BASE_URL,
    DEFAULT_AUDIO_HTTP_TIMEOUT,
    DEFAULT_MAX_AUDIO_BYTES,
    DEFAULT_OPENAI_AUDIO_QA_MODEL,
    DEFAULT_OPENAI_AUDIO_TRANSCRIPTION_MODEL,
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_VISION_MODEL,
    SubAgentConfig,
    VisionModelConfig,
)
from openjiuwen.harness.workspace.workspace import Workspace

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
    from openjiuwen.harness.rails import (
        TaskPlanningRail,
        SkillUseRail,
        SubagentRail,
    )
    from openjiuwen.harness.rails.filesystem_rail import FileSystemRail

    _RAIL_TYPE_REGISTRY.update({
        "task_planning": TaskPlanningRail,
        "skill_use": SkillUseRail,
        "subagent": SubagentRail,
        "filesystem": FileSystemRail,
    })

    # Optional rails: only register when importable.
    _optional = {
        "context_engineering": (
            "openjiuwen.harness.rails.context_engineering_rail",
            "ContextEngineeringRail",
        ),
        "token_tracking": (
            "openjiuwen.harness.cli.rails.token_tracker",
            "TokenTrackingRail",
        ),
        "tool_tracking": (
            "openjiuwen.harness.cli.rails.tool_tracker",
            "ToolTrackingRail",
        ),
        "ask_user": (
            "openjiuwen.harness.rails.interrupt.ask_user_rail",
            "AskUserRail",
        ),
        "confirm_interrupt": (
            "openjiuwen.harness.rails.interrupt.confirm_rail",
            "ConfirmInterruptRail",
        ),
    }
    import importlib
    for name, (mod_path, cls_name) in _optional.items():
        try:
            mod = importlib.import_module(mod_path)
            _RAIL_TYPE_REGISTRY[name] = getattr(mod, cls_name)
        except (ImportError, AttributeError):
            pass


# ---------------------------------------------------------------------------
# Tool type registry
# ---------------------------------------------------------------------------

_TOOL_TYPE_REGISTRY: dict[str, type] = {}


def register_tool_type(name: str, cls: type) -> None:
    """Register a tool class so it can be referenced by name in BuiltinToolSpec."""
    _TOOL_TYPE_REGISTRY[name] = cls


def _ensure_builtin_tools_registered() -> None:
    """Lazily populate the tool registry with built-in tool types."""
    if _TOOL_TYPE_REGISTRY:
        return
    _optional = {
        "web_search": (
            "openjiuwen.harness.tools.web_tools",
            "WebFreeSearchTool",
        ),
        "web_fetch": (
            "openjiuwen.harness.tools.web_tools",
            "WebFetchWebpageTool",
        ),
    }
    import importlib
    for name, (mod_path, cls_name) in _optional.items():
        try:
            mod = importlib.import_module(mod_path)
            _TOOL_TYPE_REGISTRY[name] = getattr(mod, cls_name)
        except (ImportError, AttributeError):
            pass


# ---------------------------------------------------------------------------
# TeamModelConfig (must precede specs that reference it)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Leaf spec types
# ---------------------------------------------------------------------------


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

    When ``stable_base`` is True (default for team members), the workspace
    root is resolved to ``{repo_root}/.agent_teams/workspaces/`` so that it
    survives ephemeral worktree cleanup.  The caller (factory or team_agent)
    is responsible for resolving ``repo_root`` and rewriting ``root_path``
    before calling ``build()``.
    """

    root_path: str = "./"
    language: str = "cn"
    stable_base: bool = False
    """When True, workspace path is anchored under .agent_teams/workspaces/."""

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

    def build(self, *, language: str, workspace: Optional[Workspace] = None) -> AgentRail:
        """Build a rail instance.

        Args:
            language: Auto-injected if the constructor accepts it.
            workspace: Used by ``skill_use`` to resolve default skills_dir
                from workspace's ``skills/`` node when not explicitly provided.
        """
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
        # skill_use: resolve skills_dir from workspace when not explicit.
        if self.type == "skill_use" and "skills_dir" not in kw:
            dirs: list[str] = []
            if workspace:
                skills_base = workspace.get_node_path("skills")
                if skills_base:
                    dirs.append(str(skills_base))
            # Also scan default CLI skill directories.
            dirs.extend([
                "~/.openjiuwen/workspace/skills",
                "~/.claude/skills",
            ])
            kw["skills_dir"] = dirs
        return cls(**kw)


class BuiltinToolSpec(BaseModel):
    """Declarative tool reference resolved via the tool type registry.

    Mirrors RailSpec: ``type`` selects a registered tool class,
    ``params`` are forwarded to its constructor.  If the constructor
    accepts a ``language`` parameter it is auto-injected.
    """

    type: str
    params: dict[str, Any] = {}

    def build(self, *, language: str, tool_id: str | None = None) -> Any:
        """Construct a live Tool instance.

        Args:
            language: Auto-injected if constructor accepts it.
            tool_id: Auto-injected if constructor accepts it.
                Callers can pass e.g. ``f"{member_name}.{type}"``
                to namespace tools per member.
        """
        _ensure_builtin_tools_registered()
        cls = _TOOL_TYPE_REGISTRY.get(self.type)
        if cls is None:
            raise ValueError(
                f"Unknown tool type '{self.type}'. "
                f"Registered types: {list(_TOOL_TYPE_REGISTRY)}"
            )
        kw = dict(self.params)
        sig = inspect.signature(cls.__init__)
        if "language" in sig.parameters:
            kw.setdefault("language", language)
        if tool_id and "tool_id" in sig.parameters:
            kw.setdefault("tool_id", tool_id)
        return cls(**kw)


# ---------------------------------------------------------------------------
# SubAgentSpec
# ---------------------------------------------------------------------------


class SubAgentSpec(BaseModel):
    """Serializable sub-agent specification."""

    agent_card: AgentCard
    system_prompt: str
    tools: list[ToolCard | BuiltinToolSpec] = []
    mcps: list[McpServerConfig] = []
    model: Optional[TeamModelConfig] = None
    rails: Optional[list[RailSpec]] = None
    skills: Optional[list[str]] = None
    workspace: Optional[WorkspaceSpec] = None
    sys_operation: Optional[SysOperationSpec] = None
    language: Optional[str] = None
    prompt_mode: Optional[str] = None
    enable_task_loop: bool = False
    max_iterations: Optional[int] = None
    factory_name: Optional[str] = None
    factory_kwargs: dict[str, Any] = {}

    def build(self, *, parent_model: Model, language: str) -> SubAgentConfig:
        """Materialize into a runtime SubAgentConfig.

        Args:
            parent_model: Fallback model when self.model is None.
            language: Resolved language for rail construction.
        """
        resolved_model = self.model.build() if self.model else None
        resolved_workspace = self.workspace.build() if self.workspace else None
        resolved_rails = (
            [r.build(language=language, workspace=resolved_workspace) for r in self.rails]
            if self.rails
            else None
        )

        resolved_sys_op = None
        if self.sys_operation:
            from openjiuwen.core.runner.runner import Runner

            card = self.sys_operation.build_card()
            Runner.resource_mgr.add_sys_operation(card)
            resolved_sys_op = Runner.resource_mgr.get_sys_operation(card.id)

        # Resolve tools: BuiltinToolSpec → Tool instance, ToolCard passes through.
        sa_prefix = self.agent_card.id or self.agent_card.name
        resolved_tools: list = []
        for item in self.tools:
            if isinstance(item, BuiltinToolSpec):
                tid = f"{sa_prefix}.{item.type}"
                resolved_tools.append(item.build(language=language, tool_id=tid))
            else:
                resolved_tools.append(item)

        return SubAgentConfig(
            agent_card=self.agent_card,
            system_prompt=self.system_prompt,
            tools=resolved_tools,
            mcps=list(self.mcps),
            model=resolved_model,
            rails=resolved_rails,
            skills=self.skills,
            workspace=resolved_workspace,
            sys_operation=resolved_sys_op,
            language=self.language,
            prompt_mode=self.prompt_mode,
            enable_task_loop=self.enable_task_loop,
            max_iterations=self.max_iterations,
            factory_name=self.factory_name,
            factory_kwargs=dict(self.factory_kwargs),
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
    tools: Optional[list[ToolCard | BuiltinToolSpec]] = None
    mcps: Optional[list[McpServerConfig]] = None
    subagents: Optional[list[SubAgentSpec]] = None
    rails: Optional[list[RailSpec]] = None
    enable_task_loop: bool = False
    enable_async_subagent: bool = False
    add_general_purpose_agent: bool = False
    max_iterations: int = 15
    workspace: Optional[WorkspaceSpec] = None
    skills: Optional[list[str]] = None
    enable_skill_discovery: bool = False
    sys_operation: Optional[SysOperationSpec] = None
    language: Optional[str] = None
    prompt_mode: Optional[str] = None
    vision_model: Optional[VisionModelSpec] = None
    audio_model: Optional[AudioModelSpec] = None
    enable_task_planning: bool = False
    restrict_to_sandbox: bool = False
    auto_create_workspace: bool = True
    completion_timeout: float = 600.0
    progressive_tool: Optional[ProgressiveToolSpec] = None
    approval_required_tools: Optional[list[str]] = None

    def _resolve_tools(self, language: str, tool_id_prefix: str | None = None) -> list | None:
        """Resolve tools list: BuiltinToolSpec → Tool instance, ToolCard passes through."""
        if not self.tools:
            return None
        resolved: list = []
        for item in self.tools:
            if isinstance(item, BuiltinToolSpec):
                tid = f"{tool_id_prefix}.{item.type}" if tool_id_prefix else None
                resolved.append(item.build(language=language, tool_id=tid))
            else:
                resolved.append(item)
        return resolved or None

    def build(self) -> "DeepAgent":
        """Materialize a live DeepAgent from this spec."""
        from openjiuwen.core.runner.runner import Runner
        from openjiuwen.harness.factory import create_deep_agent
        from openjiuwen.harness.prompts import resolve_language

        llm_model = self.model.build() if self.model else None
        language = resolve_language(self.language)

        vision_config = self.vision_model.build() if self.vision_model else None
        audio_config = self.audio_model.build() if self.audio_model else None
        workspace = self.workspace.build() if self.workspace else None

        rails = (
            [r.build(language=language, workspace=workspace) for r in self.rails]
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
            tools=self._resolve_tools(language, self.card.id if self.card else None),
            mcps=self.mcps,
            subagents=subagents,
            rails=rails,
            enable_task_loop=self.enable_task_loop,
            enable_async_subagent=self.enable_async_subagent,
            add_general_purpose_agent=self.add_general_purpose_agent,
            max_iterations=self.max_iterations,
            workspace=workspace,
            skills=self.skills,
            enable_skill_discovery=self.enable_skill_discovery,
            sys_operation=sys_operation,
            language=self.language,
            prompt_mode=self.prompt_mode,
            vision_model_config=vision_config,
            audio_model_config=audio_config,
            enable_task_planning=self.enable_task_planning,
            restrict_to_work_dir=self.restrict_to_sandbox,
            auto_create_workspace=self.auto_create_workspace,
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
    "BuiltinToolSpec",
    "DeepAgentSpec",
    "ProgressiveToolSpec",
    "RailSpec",
    "SubAgentSpec",
    "SysOperationSpec",
    "TeamModelConfig",
    "VisionModelSpec",
    "WorkspaceSpec",
    "register_rail_type",
    "register_tool_type",
]
