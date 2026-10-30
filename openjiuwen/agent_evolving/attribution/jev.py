# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Jev decision-model client facade for diagnostic detection.

A thin facade over the core ``JevSystemOneClient``; settings resolve from
``JEV_BASE_URL`` / ``JEV_MODEL`` / ``JEV_API_KEY`` / ``JEV_THRESHOLD`` with
in-repo defaults.  The core client's default ``api_base`` points at
typesafe.ai, so the facade explicitly forwards ``JEV_BASE_URL``.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from openjiuwen.core.foundation.llm.system_one import JevSystemOneClient

SYSTEMONE_PATH = "/v1/systemone"

DEFAULT_JEV_SETTINGS: dict[str, str] = {
    "JEV_BASE_URL": "http://localhost:8031",
    "JEV_MODEL": "kev-latest",
    "JEV_API_KEY": "",
    "JEV_THRESHOLD": "0.8",
}

#: Sentinel key accepted by a local kev deployment when no ``JEV_API_KEY`` is configured.
_LOCAL_KEV_PLACEHOLDER_KEY = "local-kev-placeholder"


def resolve_jev_settings() -> dict[str, str]:
    """Resolve ``JEV_*`` from the environment with in-repo defaults."""
    settings = dict(DEFAULT_JEV_SETTINGS)
    for key in DEFAULT_JEV_SETTINGS:
        env_value = os.environ.get(key)
        if env_value:
            settings[key] = env_value
    return settings


class JevClient:
    """Facade over ``JevSystemOneClient`` returning plain-dict answers."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_JEV_SETTINGS["JEV_BASE_URL"],
        model: str = DEFAULT_JEV_SETTINGS["JEV_MODEL"],
        api_key: str = "",
        timeout: float = 30.0,
    ) -> None:
        settings = resolve_jev_settings()
        resolved_base_url = (base_url or settings["JEV_BASE_URL"]).rstrip("/")
        resolved_model = model or settings["JEV_MODEL"]
        resolved_api_key = api_key or settings["JEV_API_KEY"]
        if not resolved_api_key:
            resolved_api_key = _LOCAL_KEV_PLACEHOLDER_KEY
        self._client = JevSystemOneClient(
            api_key=resolved_api_key,
            api_base=resolved_base_url,
            model_name=resolved_model,
            timeout=timeout,
            max_retries=2,
            retry_backoff=0.5,
        )

    async def system_one(self, state: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
        """Post one decision request and return plain-dict answers."""
        response = await self._client.system_one(state=state, questions=dict(questions))
        return {
            key: value.model_dump() if hasattr(value, "model_dump") else value
            for key, value in response.answers.items()
        }

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()


__all__ = [
    "DEFAULT_JEV_SETTINGS",
    "SYSTEMONE_PATH",
    "JevClient",
    "resolve_jev_settings",
]
