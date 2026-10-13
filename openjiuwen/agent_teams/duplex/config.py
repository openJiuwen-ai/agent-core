# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-team duplex delivery configuration.

Hung off ``TeamAgentSpec.duplex``. Opt-in: the router is assembled only when
``enabled`` is True AND ``mode == "active"``; anything else keeps the original
delivery path. The spec travels into checkpoints and member processes, so only
the NAME of the environment variable holding the API key is stored here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from openjiuwen.core.foundation.llm.system_one.providers import JEV_PROVIDERS, jev_defaults_for


class DuplexConfig(BaseModel):
    """Supervisor model endpoint and switches. JSON-serializable, no secrets."""

    enabled: bool = False
    mode: Literal["off", "active"] = "off"
    format: Literal["openai", "anthropic", "jev"] = "openai"
    api_base: str = ""
    """API base URL. openai / anthropic: required; jev: empty means the TypeSafe base."""
    endpoint: str = ""
    """jev only: endpoint path. Empty means ``/systemone`` for a typesafe.ai host, else ``/decisions``."""
    model: str = ""
    """Model name. jev: empty takes the default of the TypeSafe / OpenRouter base, otherwise required."""
    api_key_env: str = ""
    """Name of the environment variable that holds the API key (never the key). Empty means
    TYPESAFE_API_KEY / OPENROUTER_API_KEY for jev on those hosts, otherwise DUPLEX_ROUTER_API_KEY."""
    timeout_seconds: float = Field(default=2.0, gt=0)
    """Total time budget for one judgment; on expiry the message is delivered as usual."""

    @property
    def active(self) -> bool:
        """Whether the router is assembled (enabled and mode is active)."""
        return self.enabled and self.mode == "active"

    @model_validator(mode="after")
    def _fill_defaults_and_require_endpoint(self) -> "DuplexConfig":
        if self.format == "jev":
            self.api_base = self.api_base.strip() or JEV_PROVIDERS["typesafe"].api_base
            endpoint, model, key_env = jev_defaults_for(self.api_base)
            self.endpoint = self.endpoint.strip() or endpoint
            self.model = self.model.strip() or model
            self.api_key_env = self.api_key_env.strip() or key_env
        else:
            self.api_key_env = self.api_key_env.strip() or "DUPLEX_ROUTER_API_KEY"
        if self.active and not (self.api_base.strip() and self.model.strip()):
            raise ValueError("duplex: api_base and model are required when enabled and mode is active")
        return self
