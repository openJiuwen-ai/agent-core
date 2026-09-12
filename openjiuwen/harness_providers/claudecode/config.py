# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider-owned configuration for the Claude Code harness."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Literal, Mapping

from openjiuwen.harness_protocol import JsonObject
from openjiuwen.harness_providers.skills import SkillSource, normalize_skills

_PERMISSION_MODES = ("default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto")


@dataclass(frozen=True, slots=True)
class ClaudeModelConfig:
    """Model endpoint used by the Claude CLI.

    ``api_base`` / ``api_key`` are injected through the CLI ``--settings``
    flag-settings layer so they win over the user's ``settings.json``.
    """

    model: str | None = None
    api_base: str | None = None
    api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for name in ("model", "api_base", "api_key"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"Claude model {name} must be a non-empty string when provided")

    @classmethod
    def from_mapping(cls, config: Mapping[str, object] | None) -> "ClaudeModelConfig | None":
        """Build a model config from a JSON mapping; ``None`` passes through."""
        if config is None:
            return None
        if not isinstance(config, Mapping):
            raise TypeError("Claude model config must be an object")
        known = {item.name for item in fields(cls)}
        unknown = sorted(set(config) - known)
        if unknown:
            raise ValueError(f"unknown Claude model config fields: {', '.join(unknown)}")
        return cls(**dict(config))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ClaudeCodeHarnessConfig:
    """Configuration passed to ``claude_agent_sdk.ClaudeAgentOptions``.

    The optional SDK is deliberately not imported here.  ``session_id`` pins
    the CLI session id; when omitted a stable UUID is derived from the host
    session and agent name so persistent sessions survive restarts.
    """

    skills: tuple[SkillSource, ...] = ()
    skill_conflict: str = "skip"
    cwd: str | None = None
    add_dirs: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict, repr=False)
    inherit_process_env: bool = True
    cli_path: str | None = None
    model: ClaudeModelConfig | None = None
    fallback_model: ClaudeModelConfig | None = None
    session_id: str | None = None
    permission_mode: str = "bypassPermissions"
    system_prompt_mode: Literal["append", "replace"] = "append"
    include_partial_messages: bool = True
    max_turns: int | None = None
    settings: str | None = None
    settings_env: Mapping[str, str] = field(default_factory=dict, repr=False)
    event_buffer_capacity: int = 1024

    def __post_init__(self) -> None:
        object.__setattr__(self, "skills", normalize_skills(self.skills, self.skill_conflict))
        for name in ("cwd", "cli_path", "session_id", "settings"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"Claude {name} must be a string when provided")
        if self.session_id is not None and not self.session_id:
            raise ValueError("Claude session_id must not be empty")
        if not isinstance(self.add_dirs, (list, tuple)) or any(not isinstance(item, str) for item in self.add_dirs):
            raise TypeError("Claude add_dirs must be an array of strings")
        if not isinstance(self.env, Mapping):
            raise TypeError("Claude env must be an object")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.env.items()):
            raise TypeError("Claude env must map strings to strings")
        if not isinstance(self.settings_env, Mapping):
            raise TypeError("Claude settings_env must be an object")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.settings_env.items()):
            raise TypeError("Claude settings_env must map strings to strings")
        if self.permission_mode not in _PERMISSION_MODES:
            raise ValueError(f"Claude permission_mode must be one of {', '.join(_PERMISSION_MODES)}")
        if self.system_prompt_mode not in ("append", "replace"):
            raise ValueError("Claude system_prompt_mode must be 'append' or 'replace'")
        if self.max_turns is not None:
            if isinstance(self.max_turns, bool) or not isinstance(self.max_turns, int) or self.max_turns <= 0:
                raise ValueError("Claude max_turns must be a positive integer when provided")
        if isinstance(self.event_buffer_capacity, bool) or not isinstance(self.event_buffer_capacity, int):
            raise TypeError("Claude event_buffer_capacity must be an integer")
        if self.event_buffer_capacity <= 0:
            raise ValueError("Claude event_buffer_capacity must be positive")
        if self.model is not None and not isinstance(self.model, ClaudeModelConfig):
            raise TypeError("Claude model must be a ClaudeModelConfig")
        if self.fallback_model is not None and not isinstance(self.fallback_model, ClaudeModelConfig):
            raise TypeError("Claude fallback_model must be a ClaudeModelConfig")
        object.__setattr__(self, "add_dirs", tuple(self.add_dirs))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))

    @classmethod
    def from_mapping(cls, config: JsonObject) -> "ClaudeCodeHarnessConfig":
        """Validate a provider SPI mapping without importing the Claude SDK."""

        values = dict(config)
        known = {item.name for item in fields(cls)}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown Claude Code configuration fields: {', '.join(unknown)}")
        for name in ("model", "fallback_model"):
            if name in values:
                values[name] = ClaudeModelConfig.from_mapping(values[name])  # type: ignore[arg-type]
        if "env" in values and values["env"] is not None:
            if not isinstance(values["env"], Mapping):
                raise TypeError("Claude env must be an object")
            values["env"] = dict(values["env"])
        if "add_dirs" in values and values["add_dirs"] is not None:
            if not isinstance(values["add_dirs"], (list, tuple)):
                raise TypeError("Claude add_dirs must be an array")
            values["add_dirs"] = tuple(values["add_dirs"])
        return cls(**values)  # type: ignore[arg-type]


__all__ = ["ClaudeCodeHarnessConfig", "ClaudeModelConfig"]
