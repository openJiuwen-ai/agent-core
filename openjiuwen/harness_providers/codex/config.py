# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider-owned configuration for the Codex harness."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Literal, Mapping

from openjiuwen.harness_protocol import JsonObject
from openjiuwen.harness_providers.skills import SkillSource, normalize_skills

_DEFAULT_TURN_IDLE_TIMEOUT_S = 180.0
_DEFAULT_TURN_IDLE_RETRIES = 1
_DEFAULT_MAX_WILL_RETRY_COUNT = 5
_DEFAULT_MCP_STARTUP_TIMEOUT_S = 120
_REASONING_SUMMARY = "detailed"
_APPROVAL_MODES = ("auto", "prompt", "writes", "approve")


@dataclass(frozen=True, slots=True)
class CodexModelConfig:
    """External model endpoint rendered as Codex ``model_provider`` overrides."""

    model: str | None = None
    provider: str | None = None
    api_base: str | None = None
    api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for name in ("model", "provider", "api_base", "api_key"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"Codex model {name} must be a non-empty string when provided")

    @classmethod
    def from_mapping(cls, config: Mapping[str, object] | None) -> "CodexModelConfig | None":
        """Build a model config from a JSON mapping; ``None`` passes through."""
        if config is None:
            return None
        if not isinstance(config, Mapping):
            raise TypeError("Codex model config must be an object")
        known = {item.name for item in fields(cls)}
        unknown = sorted(set(config) - known)
        if unknown:
            raise ValueError(f"unknown Codex model config fields: {', '.join(unknown)}")
        return cls(**dict(config))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class CodexHarnessConfig:
    """Configuration for ``openai_codex.AsyncCodex`` and its per-agent thread.

    The optional SDK is deliberately not imported here.  ``config_overrides``
    are appended verbatim to the ``CodexConfig`` overrides after the ones the
    harness renders itself (model endpoint, MCP servers).
    """

    # Append to effective developer instructions, or replace their field.
    system_prompt_mode: Literal["append", "replace"] = "replace"
    skills: tuple[SkillSource, ...] = ()
    skill_conflict: str = "skip"
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict, repr=False)
    inherit_process_env: bool = True
    codex_bin: str | None = None
    model: CodexModelConfig | None = None
    fallback_model: CodexModelConfig | None = None
    config_overrides: tuple[str, ...] = ()
    thread_config: Mapping[str, object] = field(default_factory=lambda: {"model_reasoning_summary": _REASONING_SUMMARY})
    bypass_approvals_and_sandbox: bool = False
    turn_idle_timeout_s: float = _DEFAULT_TURN_IDLE_TIMEOUT_S
    turn_idle_retries: int = _DEFAULT_TURN_IDLE_RETRIES
    max_will_retry_count: int = _DEFAULT_MAX_WILL_RETRY_COUNT
    mcp_env_passthrough: tuple[str, ...] = ()
    mcp_startup_timeout_s: int = _DEFAULT_MCP_STARTUP_TIMEOUT_S
    mcp_required: bool = True
    mcp_default_tools_approval_mode: str | None = None
    client_name: str = "openjiuwen_harness"
    client_title: str = "OpenJiuwen Harness"
    experimental_raw_events: bool = True
    event_buffer_capacity: int = 1024

    def __post_init__(self) -> None:
        object.__setattr__(self, "skills", normalize_skills(self.skills, self.skill_conflict))
        if self.system_prompt_mode not in ("append", "replace"):
            raise ValueError("system_prompt_mode must be 'append' or 'replace'")
        for name in ("cwd", "codex_bin", "client_name", "client_title"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"Codex {name} must be a string when provided")
        if not isinstance(self.env, Mapping):
            raise TypeError("Codex env must be an object")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.env.items()):
            raise TypeError("Codex env must map strings to strings")
        for name in ("config_overrides", "mcp_env_passthrough"):
            value = getattr(self, name)
            if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
                raise TypeError(f"Codex {name} must be an array of strings")
        if not isinstance(self.thread_config, Mapping):
            raise TypeError("Codex thread_config must be an object")
        if isinstance(self.turn_idle_timeout_s, bool) or not isinstance(self.turn_idle_timeout_s, (int, float)):
            raise TypeError("Codex turn_idle_timeout_s must be numeric")
        if self.turn_idle_timeout_s <= 0:
            raise ValueError("Codex turn_idle_timeout_s must be greater than zero")
        for name in ("turn_idle_retries", "max_will_retry_count", "mcp_startup_timeout_s", "event_buffer_capacity"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"Codex {name} must be an integer")
        if self.turn_idle_retries < 0 or self.max_will_retry_count < 0:
            raise ValueError("Codex retry counters must be non-negative")
        if self.mcp_startup_timeout_s <= 0 or self.event_buffer_capacity <= 0:
            raise ValueError("Codex mcp_startup_timeout_s and event_buffer_capacity must be positive")
        approval_mode = self.mcp_default_tools_approval_mode
        if approval_mode is not None and approval_mode not in _APPROVAL_MODES:
            raise ValueError(f"Codex mcp_default_tools_approval_mode must be one of {', '.join(_APPROVAL_MODES)}")
        if self.model is not None and not isinstance(self.model, CodexModelConfig):
            raise TypeError("Codex model must be a CodexModelConfig")
        if self.fallback_model is not None and not isinstance(self.fallback_model, CodexModelConfig):
            raise TypeError("Codex fallback_model must be a CodexModelConfig")
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))
        object.__setattr__(self, "config_overrides", tuple(self.config_overrides))
        object.__setattr__(self, "mcp_env_passthrough", tuple(self.mcp_env_passthrough))
        object.__setattr__(self, "thread_config", MappingProxyType(dict(self.thread_config)))

    @classmethod
    def from_mapping(cls, config: JsonObject) -> "CodexHarnessConfig":
        """Validate a provider SPI mapping without importing the Codex SDK."""

        values = dict(config)
        known = {item.name for item in fields(cls)}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown Codex configuration fields: {', '.join(unknown)}")
        for name in ("model", "fallback_model"):
            if name in values:
                values[name] = CodexModelConfig.from_mapping(values[name])  # type: ignore[arg-type]
        for name in ("env", "thread_config"):
            if name in values and values[name] is not None:
                if not isinstance(values[name], Mapping):
                    raise TypeError(f"Codex {name} must be an object")
                values[name] = dict(values[name])
        for name in ("config_overrides", "mcp_env_passthrough"):
            if name in values and values[name] is not None:
                if not isinstance(values[name], (list, tuple)):
                    raise TypeError(f"Codex {name} must be an array")
                values[name] = tuple(values[name])
        return cls(**values)  # type: ignore[arg-type]


__all__ = ["CodexHarnessConfig", "CodexModelConfig"]
