# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serializable DeepAgent specifications for network distribution.

All types in this module are Pydantic BaseModels that support full JSON
round-trip via ``model_dump_json()`` / ``model_validate_json()``.
Call ``build()`` on a spec to materialize the corresponding runtime object.

This is the harness-level home for ``DeepAgentSpec`` and its leaf specs:
``RailSpec`` / ``BuiltinToolSpec`` / ``SubAgentSpec``. It was relocated here
from ``agent_teams/schema`` because every declaration in it depends only on
``core.*`` + ``harness.*`` — it describes a single DeepAgent's runtime shape,
not team topology. Keeping it under ``harness/schema`` lets
``DeepAgent`` / ``factory`` / team code all reference one DeepAgent-level
schema with the dependency direction ``agent_teams -> harness``.

The flat route is the single source of truth here: ``DeepAgentSpec`` carries
tools / mcps / rails / subagents / skills directly; ``resolve_parts`` /
``build`` materialize them via the leaf ``Spec.build()`` chain. /
Hot load uses ``ExpertHarnessSpec``
→ ``resolve_expert_harness_parts`` → ``expert_harness_runtime.apply_expert_harness_hot``.
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
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
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.config import (
    LocalWorkConfig,
    SandboxGatewayConfig,
)
from openjiuwen.core.sys_operation.sys_operation import SysOperationCard, SysOperation
from openjiuwen.harness.schema.build_context import BuildContext
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

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

# ---------------------------------------------------------------------------
# Capability provider registries
# ---------------------------------------------------------------------------

# A provider is a factory that resolves ``(params, context)`` into a live
# instance (or a list of instances). A provider receives the runtime
# ``BuildContext`` and can therefore build capabilities that depend on live,
# non-serializable handles. Every rail / tool / sub-agent — built-in and
# platform — registers here through the manifest catalog; there is no longer a
# separate class registry.
_RAIL_PROVIDER_REGISTRY: dict[str, Callable[..., Any]] = {}
_TOOL_PROVIDER_REGISTRY: dict[str, Callable[..., Any]] = {}
_SUBAGENT_PROVIDER_REGISTRY: dict[str, Callable[..., Any]] = {}


def register_rail_provider(name: str, factory: Callable[..., Any]) -> None:
    """Register a rail provider factory referenced by name in ``RailSpec``.

    The factory is invoked as ``factory(params, context)`` and may return a
    single rail or a list of rails. Built-in rails register here too via the
    manifest catalog; a later registration for the same name overrides it.
    """
    _RAIL_PROVIDER_REGISTRY[name] = factory


def register_tool_provider(name: str, factory: Callable[..., Any]) -> None:
    """Register a tool provider factory referenced by name in ``BuiltinToolSpec``.

    The factory is invoked as ``factory(params, context)`` and may return a
    single tool or a list of tools. Built-in tools register here too via the
    manifest catalog; a later registration for the same name overrides it.
    """
    _TOOL_PROVIDER_REGISTRY[name] = factory


def register_subagent_provider(name: str, factory: Callable[..., Any]) -> None:
    """Register a sub-agent provider factory keyed by ``SubAgentSpec.factory_name``.

    The factory is invoked as ``factory(factory_kwargs, context)`` and returns a
    ``SubAgentConfig`` (or a list). Resolution is opt-in: a ``SubAgentSpec`` only
    uses it when its ``factory_name`` matches a registered provider, so existing
    factory names are unaffected.
    """
    _SUBAGENT_PROVIDER_REGISTRY[name] = factory


def _as_built_list(built: Any) -> list:
    """Normalize a build result into a flat list, dropping ``None`` entries.

    Providers may return a single instance or a list; class-based builds return
    a single instance. Callers use this to uniformly flatten and to treat an
    empty list as "skip this capability".
    """
    if built is None:
        return []
    if isinstance(built, list):
        return [item for item in built if item is not None]
    return [built]


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


# Alias matching the harness naming convention; ``ModelSpec`` is the canonical
# import for callers that want a model-layer name. Both names refer to the
# same class object so identity checks (``is``) across import paths hold.
ModelSpec = TeamModelConfig


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
        root_path = self.root_path or "./"
        return Workspace(root_path=root_path, language=self.language)


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

    def resolve(self) -> "SysOperation":
        """Get-or-create the live SysOperation for this spec.

        Idempotent across harness restarts. A member's sys_operation id is
        stable for the lifetime of the session, so when a team pauses and a new
        message rebuilds every member harness, the fresh build re-resolves the
        same resource id. ``add_sys_operation`` is a strict add that errors on a
        duplicate id, so resolve the existing instance first and only add when
        absent — otherwise each resume logs a spurious "resource already exist"
        error per member.

        Returns:
            The live SysOperation registered under this spec's id.
        """
        from openjiuwen.core.runner.runner import Runner

        card = self.build_card()
        sys_operation = Runner.resource_mgr.get_sys_operation(card.id)
        if sys_operation is None:
            Runner.resource_mgr.add_sys_operation(card)
            sys_operation = Runner.resource_mgr.get_sys_operation(card.id)
        return sys_operation


class RailSpec(BaseModel):
    """Declarative rail reference resolved via the rail provider registry."""

    type: str
    params: dict[str, Any] = {}

    def build(
        self,
        *,
        language: str,
        workspace: Optional[Workspace] = None,
        context: "BuildContext | None" = None,
    ) -> Any:
        """Build a rail instance (or a list of rails) via its registered provider.

        Args:
            language: Forwarded on the build context so the provider / class
                adapter injects it when the constructor accepts it.
            workspace: Forwarded on the build context (used by ``skill_use`` to
                resolve a default skills_dir from the workspace ``skills/`` node).
            context: Runtime carrier forwarded to the rail provider. When None,
                a minimal context carrying ``language`` + ``workspace`` is
                synthesized so providers still observe them.

        Returns:
            A rail, a list of rails, or ``None``; callers flatten the result.
        """
        from openjiuwen.harness.manifest import ensure_builtin_elements_registered

        ensure_builtin_elements_registered()
        factory = _RAIL_PROVIDER_REGISTRY.get(self.type)
        if factory is None:
            raise ValueError(
                f"Unknown rail type '{self.type}'. "
                f"Registered types: {list(_RAIL_PROVIDER_REGISTRY)}"
            )
        if context is None:
            context = BuildContext(language=language, workspace=workspace)
        return factory(dict(self.params), context)


class BuiltinToolSpec(BaseModel):
    """Declarative tool reference resolved via the tool provider registry.

    Mirrors RailSpec: ``type`` selects a registered tool provider,
    ``params`` are forwarded to it. A class-based tool gets ``language``
    auto-injected by the class adapter when its constructor accepts it.
    """

    type: str
    params: dict[str, Any] = {}

    def build(
        self,
        *,
        language: str,
        tool_id: str | None = None,
        context: "BuildContext | None" = None,
    ) -> Any:
        """Construct a live Tool instance (or list) via its registered provider.

        Args:
            language: Forwarded on the build context so the provider / class
                adapter injects it when the constructor accepts it.
            tool_id: Reserved for per-member tool-id namespacing; not forwarded
                to the provider. Per-agent id qualification now happens at
                registration time in ``AbilityManager.add_ability`` (stateful
                tools get an agent-qualified id; stateless tools keep a shared
                bare id), so providers need not consume it.
            context: Runtime carrier forwarded to the tool provider. When None,
                a minimal context carrying ``language`` is synthesized.
        """
        from openjiuwen.harness.manifest import ensure_builtin_elements_registered

        ensure_builtin_elements_registered()
        factory = _TOOL_PROVIDER_REGISTRY.get(self.type)
        if factory is None:
            raise ValueError(
                f"Unknown tool type '{self.type}'. "
                f"Registered types: {list(_TOOL_PROVIDER_REGISTRY)}"
            )
        if context is None:
            context = BuildContext(language=language)
        return factory(dict(self.params), context)


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

    def build(
        self,
        *,
        parent_model: Model,
        language: str,
        context: "BuildContext | None" = None,
    ) -> Any:
        """Materialize into a runtime SubAgentConfig.

        Args:
            parent_model: Fallback model when self.model is None.
            language: Resolved language for rail construction.
            context: Runtime carrier; if ``factory_name`` matches a registered
                sub-agent provider it builds the config directly.
        """
        from openjiuwen.harness.manifest import ensure_builtin_elements_registered

        ensure_builtin_elements_registered()
        if self.factory_name and self.factory_name in _SUBAGENT_PROVIDER_REGISTRY:
            return _SUBAGENT_PROVIDER_REGISTRY[self.factory_name](dict(self.factory_kwargs), context)
        resolved_model = self.model.build() if self.model else None
        resolved_workspace = self.workspace.build() if self.workspace else None
        resolved_rails = None
        if self.rails:
            resolved_rails = []
            for rail_spec in self.rails:
                resolved_rails.extend(
                    _as_built_list(
                        rail_spec.build(language=language, workspace=resolved_workspace, context=context),
                    ),
                )

        resolved_sys_op = self.sys_operation.resolve() if self.sys_operation else None

        # Resolve tools: BuiltinToolSpec → Tool instance, ToolCard passes through.
        sa_prefix = self.agent_card.id or self.agent_card.name
        resolved_tools: list = []
        for item in self.tools:
            if isinstance(item, BuiltinToolSpec):
                tid = f"{sa_prefix}.{item.type}"
                resolved_tools.extend(_as_built_list(item.build(language=language, tool_id=tid, context=context)))
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
    enable_task_loop: bool = True
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
    context_engine_config: Optional[Any] = None
    """Context engine configuration forwarded to ``DeepAgentConfig``.

    When set, this is passed to ``resolve_deep_agent_parts()`` and ultimately to
    ``DeepAgentConfig.context_engine_config``, which the inner ReAct agent
    reads to configure context compression (``context_window_tokens``,
    compressor thresholds, etc.).

    JSON-serializable — typically a dict matching ``ContextEngineConfig``
    or the model itself when used in-process.
    """

    def _resolve_tools(
        self,
        language: str,
        tool_id_prefix: str | None = None,
        context: "BuildContext | None" = None,
    ) -> list | None:
        """Resolve tools list: BuiltinToolSpec → Tool instance, ToolCard passes through."""
        if not self.tools:
            return None
        resolved: list = []
        for item in self.tools:
            if isinstance(item, BuiltinToolSpec):
                tid = f"{tool_id_prefix}.{item.type}" if tool_id_prefix else None
                resolved.extend(_as_built_list(item.build(language=language, tool_id=tid, context=context)))
            else:
                resolved.append(item)
        return resolved or None

    def resolve_parts(self, context: "BuildContext | None" = None) -> Any:
        """Assemble this spec's DeepAgent parts without creating an instance.

        The provider-resolution half of :meth:`build`: resolves the model,
        rails (via provider + per-member ``BuildContext``), sub-agents, tools,
        and sys_operation, then delegates to ``resolve_deep_agent_parts``.
        Returns a ``DeepAgentParts``. A ``NativeHarness`` uses this to configure
        itself directly (forward construction, no throwaway template);
        :meth:`build` uses it to materialize a fresh ``DeepAgent``.

        Args:
            context: Optional runtime carrier forwarded to capability providers.
                A per-build view is derived with the resolved workspace and card
                id before rails / tools / sub-agents are built.
        """
        from openjiuwen.harness.factory import resolve_deep_agent_parts
        from openjiuwen.harness.prompts import resolve_language

        llm_model = self.model.build() if self.model else None
        language = resolve_language(self.language)

        vision_config = self.vision_model.build() if self.vision_model else None
        audio_config = self.audio_model.build() if self.audio_model else None
        workspace = self.workspace.build() if self.workspace else None
        member_card_id = self.card.id if self.card else None

        if context is not None:
            # Team / caller-supplied context: derive so we do not mutate the
            # caller's fields; rebind extras because derive() is a shallow copy.
            build_ctx = context.derive(
                workspace=workspace,
                member_card_id=member_card_id,
            )
            build_ctx.extras = dict(build_ctx.extras)
        else:
            # Harness cold start with no context: bootstrap a minimal carrier.
            build_ctx = BuildContext(
                language=language,
                workspace=workspace,
                member_card_id=member_card_id,
            )

        # Always publish the parent model (maybe None when Spec has no model)
        # so core.subagent.* / progressive_tool factories can read extras.
        build_ctx.extras["_parent_model"] = llm_model

        rails = None
        if self.rails:
            rails = []
            for rail_spec in self.rails:
                rails.extend(
                    _as_built_list(rail_spec.build(language=language, workspace=workspace, context=build_ctx)),
                )
        subagents = None
        if self.subagents:
            subagents = []
            for subagent_spec in self.subagents:
                subagents.extend(
                    _as_built_list(
                        subagent_spec.build(parent_model=llm_model, language=language, context=build_ctx),
                    ),
                )

        sys_operation = self.sys_operation.resolve() if self.sys_operation else None

        return resolve_deep_agent_parts(
            llm_model,
            card=self.card,
            system_prompt=self.system_prompt,
            tools=self._resolve_tools(language, self.card.id if self.card else None, context=build_ctx),
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
            context_engine_config=self.context_engine_config,
            **self._progressive_tool_kwargs(),
        )

    def build(self, context: "BuildContext | None" = None) -> "DeepAgent":
        """Materialize a live DeepAgent from this spec.

        Resolves the construction parts via :meth:`resolve_parts` and applies
        them onto a fresh ``DeepAgent`` instance.

        Args:
            context: Optional runtime carrier forwarded to capability providers.
                A per-build view is derived with the resolved workspace and card
                id before rails / tools / sub-agents are built.
        """
        from openjiuwen.harness.deep_agent import DeepAgent
        from openjiuwen.harness.factory import apply_deep_agent_parts

        parts = self.resolve_parts(context)
        agent = DeepAgent(parts.config.card)
        apply_deep_agent_parts(agent, parts)
        return agent

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
    "ModelSpec",
    "ProgressiveToolSpec",
    "RailSpec",
    "SubAgentSpec",
    "SysOperationSpec",
    "TeamModelConfig",
    "VisionModelSpec",
    "WorkspaceSpec",
    "register_rail_provider",
    "register_subagent_provider",
    "register_tool_provider",
]
