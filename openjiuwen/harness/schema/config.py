# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent configuration dataclasses."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from openjiuwen.core.foundation.kv_cache import KVCacheAffinityConfig
from openjiuwen.core.foundation.llm.model import Model

from openjiuwen.core.single_agent.rail.base import AgentRail

from openjiuwen.core.foundation.tool import Tool, ToolCard, McpServerConfig

from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.schema.agent_mode import AgentMode
from openjiuwen.harness.security.models import PermissionsSection
from openjiuwen.harness.workspace.workspace import (
    Workspace,
)
from openjiuwen.harness.multi_rollout.config import (
    MultiRolloutConfig,
)

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENROUTER_VISION_MODEL = "google/gemini-2.5-pro"
DEFAULT_OPENAI_VISION_MODEL = "gpt-4.1-mini"

DEFAULT_OPENAI_AUDIO_TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
DEFAULT_OPENAI_AUDIO_QA_MODEL = "gpt-4o-audio-preview"
DEFAULT_ACR_BASE_URL = (
    "https://identify-ap-southeast-1.acrcloud.com/v1/identify"
)
DEFAULT_AUDIO_HTTP_TIMEOUT = 20
DEFAULT_MAX_AUDIO_BYTES = 25 * 1024 * 1024


def _parse_int_from_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass
class VisionModelConfig:
    """Shared runtime configuration for all DeepAgent vision tools."""

    api_key: str = ""
    base_url: str = DEFAULT_OPENAI_BASE_URL
    model: str = DEFAULT_OPENAI_VISION_MODEL
    max_retries: int = 3

    @classmethod
    def from_env(cls) -> "VisionModelConfig":
        """Build a vision config from environment variables."""
        api_key = (
            os.getenv("VISION_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
        base_url = (
            os.getenv("VISION_BASE_URL")
            or os.getenv("VISION_API_BASE")
            or os.getenv("OPENROUTER_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or DEFAULT_OPENAI_BASE_URL
        )
        model = os.getenv("VISION_MODEL") or os.getenv("VISION_MODEL_NAME")

        if not model:
            if "openrouter.ai" in base_url:
                model = DEFAULT_OPENROUTER_VISION_MODEL
            else:
                model = DEFAULT_OPENAI_VISION_MODEL

        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            max_retries=_parse_int_from_env("VISION_MAX_RETRIES", 3),
        )


def is_vision_model_config_complete(
    config: Optional[VisionModelConfig],
) -> bool:
    """Return whether a vision model config can be used for model calls."""
    if config is None:
        return False
    return bool(
        str(config.api_key or "").strip()
        and str(config.base_url or "").strip()
        and str(config.model or "").strip()
    )


@dataclass
class AudioModelConfig:
    """Shared runtime configuration for all DeepAgent audio tools."""

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

    @classmethod
    def from_env(cls) -> "AudioModelConfig":
        """Build an audio config from environment variables."""
        return cls(
            api_key=(
                os.getenv("AUDIO_API_KEY")
                or os.getenv("OPENAI_API_KEY")
                or ""
            ),
            base_url=(
                os.getenv("AUDIO_BASE_URL")
                or os.getenv("AUDIO_API_BASE")
                or os.getenv("OPENAI_BASE_URL")
                or DEFAULT_OPENAI_BASE_URL
            ),
            transcription_model=(
                os.getenv("AUDIO_TRANSCRIPTION_MODEL")
                or os.getenv("AUDIO_MODEL_NAME")
                or DEFAULT_OPENAI_AUDIO_TRANSCRIPTION_MODEL
            ),
            question_answering_model=(
                os.getenv("AUDIO_QUESTION_ANSWERING_MODEL")
                or os.getenv("AUDIO_MODEL_NAME")
                or DEFAULT_OPENAI_AUDIO_QA_MODEL
            ),
            max_retries=_parse_int_from_env("AUDIO_MAX_RETRIES", 3),
            http_timeout=_parse_int_from_env(
                "AUDIO_HTTP_TIMEOUT",
                DEFAULT_AUDIO_HTTP_TIMEOUT,
            ),
            max_audio_bytes=_parse_int_from_env(
                "AUDIO_MAX_AUDIO_BYTES",
                DEFAULT_MAX_AUDIO_BYTES,
            ),
            acr_access_key=os.getenv("ACR_ACCESS_KEY", ""),
            acr_access_secret=os.getenv("ACR_ACCESS_SECRET", ""),
            acr_base_url=os.getenv("ACR_BASE_URL", DEFAULT_ACR_BASE_URL),
        )


@dataclass
class DeepAgentConfig:
    """Runtime configuration for DeepAgent.

    Attributes:
        model: Pre-constructed Model instance for LLM
            calls.
        card: Agent identity card. Its id is a *persistence* identity: it is the
            entity segment of every checkpointer key, so it must stay stable
            across restarts for a session's state to be recoverable.
        tool_owner_id: Owner id used to qualify this agent's stateful tool
            registrations in the process-global resource manager. Defaults to
            ``card.id``. Set it when several agents legitimately share one card
            identity (e.g. one adapter per session) so their tool instances do
            not overwrite each other under the same id — the checkpointer key
            keeps using ``card.id`` and is unaffected.
        system_prompt: System prompt injected into the
            ReAct agent's prompt template.
        context_engine_config: Reserved for P1 context
            engineering configuration. If set, applied
            as the inner ReAct agent's ``ContextEngineConfig``
            when the embedded agent is created.
        kv_cache_affinity_config: KV cache affinity configuration
            applied as the inner ReAct agent's
            ``KVCacheAffinityConfig`` when the embedded agent is created.
        enable_task_loop: Whether to enable the outer
            task loop (P1).
        enable_async_subagent: Enable async subagent mode (default False).
            When True, SubagentRail registers session tools for async subagent spawning;
            when False, it registers synchronous task tools.
        enable_subagent_runtime: Enable persistent subagent runtime tools
            (subagent_spawn/wait/list). Takes precedence over enable_async_subagent.
        add_general_purpose_agent: Add general-purpose agent.
            When True, a general-purpose agent is added as sub-agents.
        max_iterations: Maximum ReAct iterations per
            single invoke.
        subagents: Sub-agent specifications or Sub-agent instance.
        tools: Tool cards mounted on the agent.
        mcps: MCP server configs mounted on the agent.
        workspace: Workspace root for this agent's own artifacts (memory,
            skills view, produced files). NOT the shell working directory —
            see ``cwd``.
        cwd: Working directory shell commands run in and relative paths
            resolve against. Defaults to the workspace root, which keeps the
            single-agent case unchanged. Team members point it at the project
            directory (or their isolated worktree) while keeping their own
            workspace for artifacts.
        project_root: Project identity anchor (repo root). Defaults to
            ``cwd``. Used for access-boundary checks, not for path
            resolution.
        skills: Skill definitions (P1).
        backend: Backend protocol instance (P2).
        sys_operation: System operation.
        completion_timeout: Max seconds to wait for a
            single task-loop iteration to complete.
            Used by the outer loop's wait_completion().
            None means no limit.
        enable_plan_mode: Whether to enable plan mode.
        permissions: Tool permission policy dict (enabled, tools, rules, …); when
            enabled, DeepAgent mounts PermissionInterruptRail automatically.
            常见键结构见 :class:`openjiuwen.harness.security.models.PermissionsSection`。
        permission_host: Optional ToolPermissionHost callbacks (YAML path,
            workspace, hot-reload snapshot, hosted confirmation).
        parallel_tool_calls: Whether or not tool calls are executed in parallel
            (True for parallel, False for sequential)
    """

    model: Optional[Model] = None
    card: Optional[AgentCard] = None
    tool_owner_id: Optional[str] = None
    system_prompt: Optional[str] = None
    context_engine_config: Optional[Any] = None
    kv_cache_affinity_config: Optional[KVCacheAffinityConfig] = None
    enable_task_loop: bool = False
    enable_async_subagent: bool = False
    enable_subagent_runtime: bool = False
    add_general_purpose_agent: bool = False
    max_iterations: int = 15
    subagents: Optional[List[SubAgentConfig | "DeepAgent"]] = None
    tools: Optional[List[ToolCard]] = None
    mcps: Optional[List[McpServerConfig]] = None
    workspace: Optional[Workspace] = None
    cwd: Optional[str] = None
    project_root: Optional[str] = None
    skills: Optional[Union[str, List[str]]] = None
    enable_skill_discovery: bool = False
    backend: Optional[Any] = None
    sys_operation: Optional[SysOperation] = None
    auto_create_workspace: bool = True
    completion_timeout: Optional[float] = 600.0
    language: Optional[str] = None
    prompt_mode: Optional[str] = None
    vision_model_config: Optional[VisionModelConfig] = None
    audio_model_config: Optional[AudioModelConfig] = None
    enable_read_image_multimodal: Optional[bool] = None
    rails: Optional[List[AgentRail]] = None
    enable_plan_mode: bool = False
    model_selection: Optional[Dict[Model, str]] = None

    # Progressive tool exposure config
    progressive_tool_enabled: bool = False

    # Plan mode config
    default_mode: AgentMode = AgentMode.NORMAL

    # Tool permission guardrail (tiered_policy / interrupt confirm)
    permissions: PermissionsSection | None = None
    permission_host: Any = None

    # Whether or not the inner ReactAgent executes tool calls in parallel.
    parallel_tool_calls: bool = True

    # Auto-mount ToolCallResilienceRail: bounded retry of retryable tool-call
    # failures (transport/timeout markers) via the @rail retry loop. Non-
    # idempotent tools (write/shell/spawn) are never retried. Turn off for
    # deployments that supply their own retry rail or want raw exceptions.
    enable_tool_resilience_rail: bool = True

    # Filesystem sandbox: when True, file ops are restricted to workspace/project root.
    # Subagents inherit the stricter of their own spec and this value.
    restrict_to_work_dir: bool = True

    # Multi-rollout: spawn N parallel attempts for the same task.
    multi_rollout: "MultiRolloutConfig" = field(
        default_factory=lambda: MultiRolloutConfig()
    )


@dataclass
class SubAgentConfig:
    """Configuration for a DeepAgent sub-agent."""

    agent_card: AgentCard
    system_prompt: str
    tools: List[Tool | ToolCard] = field(default_factory=list)
    mcps: List[McpServerConfig] = field(default_factory=list)
    model: Optional[Model] = None
    rails: Optional[List[AgentRail]] = None
    skills: Optional[List[str]] = None
    backend: Optional[Any] = None
    workspace: Optional[Workspace] = None
    sys_operation: Optional[SysOperation] = None
    language: Optional[str] = None
    prompt_mode: Optional[str] = None
    # None inherits the parent DeepAgent setting.  Set explicitly when the
    # subagent uses a model with different native image capabilities.
    enable_read_image_multimodal: Optional[bool] = None
    enable_task_loop: bool = False
    max_iterations: Optional[int] = None
    factory_name: Optional[str] = None
    factory_kwargs: dict[str, Any] = field(default_factory=dict)
    enable_plan_mode: bool = False
    parallel_tool_calls: bool = True
    restrict_to_work_dir: bool = True
    display_name: Optional[str] = None
    role: Optional[str] = None
