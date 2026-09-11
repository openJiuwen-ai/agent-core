# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider-owned configuration for the DeepSeek Harness adapter."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Literal, Mapping

from openjiuwen.harness_protocol import JsonObject
from openjiuwen.harness_providers.skills import SkillSource, normalize_skills


@dataclass(frozen=True, slots=True)
class DshHarnessConfig:
    """Configuration passed to ``deepseek_harness.DeepSeekHarness``.

    The optional SDK dependency is deliberately not imported here.  Field
    names mirror ``deepseek_harness.DeepSeekHarnessConfig``; ``dsh_home`` (or
    a non-empty ``DSH_HOME`` in ``env``) is mandatory for the SDK runtime,
    which never falls back to ``~/.dsh`` implicitly.  A custom Cordis
    composition must consume ``system_prompt_env_var`` when it is explicitly
    set. Otherwise the adapter uses the bundled system-prompt plugin through
    a temporary launch overlay.
    """

    provider: str = "deepseek-official"
    model: str = "deepseek-v4-flash"
    reasoning_effort: str | None = None
    max_tokens: int | None = None
    # Replace only the native prefix, or append an independent host section.
    system_prompt_mode: Literal["append", "replace"] = "replace"
    skills: tuple[SkillSource, ...] = ()
    skill_conflict: str = "skip"
    cwd: str | None = None
    runtime_cwd: str | None = None
    dsh_bin: str | None = None
    dsh_home: str | None = None
    profile: str | None = None
    patches: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict, repr=False)
    launch_args_override: tuple[str, ...] | None = None
    initialize_timeout_seconds: float | None = None
    request_timeout_seconds: float | None = None
    shutdown_timeout_seconds: float | None = 1.0
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    system_prompt_env_var: str | None = None
    event_buffer_capacity: int = 1024

    def __post_init__(self) -> None:
        object.__setattr__(self, "skills", normalize_skills(self.skills, self.skill_conflict))
        if self.system_prompt_mode == "append" and self.system_prompt_env_var is not None:
            raise ValueError("append mode cannot use system_prompt_env_var")
        if self.system_prompt_mode not in ("append", "replace"):
            raise ValueError("system_prompt_mode must be 'append' or 'replace'")
        if not isinstance(self.provider, str) or not isinstance(self.model, str):
            raise TypeError("DSH provider and model must be strings")
        if not self.provider or not self.model:
            raise ValueError("DSH provider and model must not be empty")
        optional_strings = (
            "reasoning_effort",
            "cwd",
            "runtime_cwd",
            "dsh_bin",
            "dsh_home",
            "profile",
            "base_url",
            "api_key",
            "system_prompt_env_var",
        )
        for name in optional_strings:
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"DSH {name} must be a string when provided")
        if self.max_tokens is not None:
            if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int):
                raise TypeError("DSH max_tokens must be an integer when provided")
            if self.max_tokens <= 0:
                raise ValueError("DSH max_tokens must be positive")
        if isinstance(self.event_buffer_capacity, bool) or not isinstance(self.event_buffer_capacity, int):
            raise TypeError("DSH event_buffer_capacity must be an integer")
        if self.event_buffer_capacity <= 0:
            raise ValueError("DSH event_buffer_capacity must be positive")
        for name, timeout in (
            ("initialize_timeout_seconds", self.initialize_timeout_seconds),
            ("request_timeout_seconds", self.request_timeout_seconds),
            ("shutdown_timeout_seconds", self.shutdown_timeout_seconds),
        ):
            if timeout is not None:
                if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                    raise TypeError(f"DSH {name} must be numeric when provided")
                if timeout <= 0:
                    raise ValueError(f"DSH {name} must be positive when provided")
        for name in ("dsh_home", "profile", "system_prompt_env_var"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"DSH {name} must not be empty")
        if not isinstance(self.env, Mapping):
            raise TypeError("DSH env must be an object")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.env.items()):
            raise TypeError("DSH env must map strings to strings")
        if not isinstance(self.patches, (list, tuple)) or any(not isinstance(item, str) for item in self.patches):
            raise TypeError("DSH patches must be an array of strings")
        launch_args = self.launch_args_override
        if launch_args is not None:
            if not isinstance(launch_args, (list, tuple)):
                raise TypeError("DSH launch_args_override must be an array")
            if not launch_args or any(not isinstance(arg, str) or not arg for arg in launch_args):
                raise ValueError("DSH launch_args_override must contain non-empty strings")
            object.__setattr__(self, "launch_args_override", tuple(launch_args))
        object.__setattr__(self, "patches", tuple(self.patches))
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))

    @classmethod
    def from_mapping(cls, config: JsonObject) -> "DshHarnessConfig":
        """Validate a provider SPI mapping without starting the DSH runtime."""

        values = dict(config)
        known = {item.name for item in fields(cls)}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown DSH configuration fields: {', '.join(unknown)}")

        env = values.get("env")
        if env is not None:
            if not isinstance(env, Mapping):
                raise TypeError("DSH env must be an object")
            values["env"] = dict(env)

        for name in ("launch_args_override", "patches"):
            items = values.get(name)
            if items is not None:
                if not isinstance(items, (list, tuple)):
                    raise TypeError(f"DSH {name} must be an array")
                values[name] = tuple(items)

        return cls(**values)  # type: ignore[arg-type]


__all__ = ["DshHarnessConfig"]
