# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider-owned configuration for the DeepSeek Harness adapter."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Mapping

from openjiuwen.agent_teams.external.protocol import JsonObject


@dataclass(frozen=True, slots=True)
class DshHarnessConfig:
    """Configuration passed to ``deepseek_harness.DeepSeekHarness``.

    The optional SDK dependency is deliberately not imported here.  A custom
    Cordis composition must consume ``system_prompt_env_var`` when it is set;
    the DSH Python SDK does not expose a native system-prompt argument.
    """

    provider: str = "deepseek-official"
    model: str = "deepseek-v4-flash"
    max_tokens: int | None = None
    cwd: str | None = None
    runtime_cwd: str | None = None
    session_root: str | None = None
    cordis: str | None = None
    env: Mapping[str, str] = field(default_factory=dict, repr=False)
    runtime_bin: str | None = None
    launch_args_override: tuple[str, ...] | None = None
    request_timeout_seconds: float | None = None
    shutdown_timeout_seconds: float | None = 1.0
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    system_prompt_env_var: str | None = None
    event_buffer_capacity: int = 1024

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not isinstance(self.model, str):
            raise TypeError("DSH provider and model must be strings")
        if not self.provider or not self.model:
            raise ValueError("DSH provider and model must not be empty")
        optional_strings = (
            "cwd",
            "runtime_cwd",
            "session_root",
            "cordis",
            "runtime_bin",
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
            ("request_timeout_seconds", self.request_timeout_seconds),
            ("shutdown_timeout_seconds", self.shutdown_timeout_seconds),
        ):
            if timeout is not None:
                if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                    raise TypeError(f"DSH {name} must be numeric when provided")
                if timeout <= 0:
                    raise ValueError(f"DSH {name} must be positive when provided")
        if self.system_prompt_env_var is not None and not self.system_prompt_env_var:
            raise ValueError("DSH system_prompt_env_var must not be empty")
        if not isinstance(self.env, Mapping):
            raise TypeError("DSH env must be an object")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.env.items()):
            raise TypeError("DSH env must map strings to strings")
        launch_args = self.launch_args_override
        if launch_args is not None:
            if not isinstance(launch_args, (list, tuple)):
                raise TypeError("DSH launch_args_override must be an array")
            if not launch_args or any(not isinstance(arg, str) or not arg for arg in launch_args):
                raise ValueError("DSH launch_args_override must contain non-empty strings")
            object.__setattr__(self, "launch_args_override", tuple(launch_args))
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

        launch_args = values.get("launch_args_override")
        if launch_args is not None:
            if not isinstance(launch_args, (list, tuple)):
                raise TypeError("DSH launch_args_override must be an array")
            values["launch_args_override"] = tuple(launch_args)

        return cls(**values)  # type: ignore[arg-type]


__all__ = ["DshHarnessConfig"]
