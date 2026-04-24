# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgent configuration dataclasses."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from openjiuwen.core.foundation.llm.model import Model

from openjiuwen.core.single_agent.rail.base import AgentRail

from openjiuwen.core.foundation.tool import Tool, ToolCard, McpServerConfig

from openjiuwen.core.single_agent.schema.agent_card import (
    AgentCard,
)
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.schema.agent_mode import AgentMode
from openjiuwen.harness.workspace.workspace import (
    Workspace,
)

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
        card: Agent identity card.
        system_prompt: System prompt injected into the
            ReAct agent's prompt template.
        context_engine_config: Reserved for P1 context
            engineering configuration. If set, applied
            as the inner ReAct agent's ``ContextEngineConfig``
            when the embedded agent is created.
        enable_task_loop: Whether to enable the outer
            task loop (P1).
        enable_async_subagent: Enable async subagent via SessionRail (default False).
            When True and subagents are configured, SessionRail is mounted instead of SubagentRail.
        add_general_purpose_agent: Add general-purpose agent.
            When True, a general-purpose agent is added as sub-agents.
        max_iterations: Maximum ReAct iterations per
            single invoke.
        subagents: Sub-agent specifications or Sub-agent instance.
        tools: Tool cards mounted on the agent.
        mcps: MCP server configs mounted on the agent.
        workspace: Workspace path for file operations.
        skills: Skill definitions (P1).
        backend: Backend protocol instance (P2).
        sys_operation: System operation.
        completion_timeout: Max seconds to wait for a
            single task-loop iteration to complete.
            Used by the outer loop's wait_completion().
        enable_plan_mode: Whether to enable plan mode.
        permissions: Tool permission policy dict (enabled, tools, rules, …); when
            enabled, DeepAgent mounts PermissionInterruptRail automatically.
        permission_host: Optional ToolPermissionHost callbacks (YAML path, ACP,
            workspace, hot-reload snapshot).
    """

    model: Optional[Model] = None
    card: Optional[AgentCard] = None
    system_prompt: Optional[str] = None
    context_engine_config: Optional[Any] = None
    enable_task_loop: bool = False
    enable_async_subagent: bool = False
    add_general_purpose_agent: bool = False
    max_iterations: int = 15
    subagents: Optional[List[SubAgentConfig | "DeepAgent"]] = None
    tools: Optional[List[ToolCard]] = None
    mcps: Optional[List[McpServerConfig]] = None
    workspace: Optional[Workspace] = None
    skills: Optional[Union[str, List[str]]] = None
    enable_skill_discovery: bool = False
    backend: Optional[Any] = None
    sys_operation: Optional[SysOperation] = None
    auto_create_workspace: bool = True
    completion_timeout: float = 600.0
    language: Optional[str] = None
    prompt_mode: Optional[str] = None
    vision_model_config: Optional[VisionModelConfig] = None
    audio_model_config: Optional[AudioModelConfig] = None
    rails: Optional[List[AgentRail]] = None
    enable_plan_mode: bool = False
    model_selection: Optional[Dict[Model, str]] = None

    # Progressive tool exposure config
    progressive_tool_enabled: bool = False
    progressive_tool_always_visible_tools: List[str] = field(
        default_factory=list
    )
    progressive_tool_default_visible_tools: List[str] = field(
        default_factory=list
    )
    progressive_tool_max_loaded_tools: int = 12

    # Plan mode config
    default_mode: AgentMode = AgentMode.NORMAL

    # Tool permission guardrail (tiered_policy / interrupt confirm)
    permissions: dict[str, Any] | None = None
    permission_host: Any = None


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
    enable_task_loop: bool = False
    max_iterations: Optional[int] = None
    factory_name: Optional[str] = None
    factory_kwargs: dict[str, Any] = field(default_factory=dict)
    enable_plan_mode: bool = False
