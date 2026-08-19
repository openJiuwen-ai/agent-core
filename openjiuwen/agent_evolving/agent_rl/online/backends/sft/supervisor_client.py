# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HTTP client for supervisor model calls used by SFT rollouters."""

from __future__ import annotations

from typing import Any

import httpx

from openjiuwen.agent_evolving.agent_rl.online.backends.sft.sample_builder import (
    json_safe,
    normalize_assistant_message,
    normalize_tool_definitions,
)


class SupervisorClient:
    """Call an OpenAI-compatible supervisor and optional custom rollout endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        model: str = "",
        timeout: float = 120.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.model = model
        self.timeout = float(timeout)
        self._owned_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=self.timeout)

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return one assistant message from an OpenAI-compatible chat endpoint."""
        body: dict[str, Any] = {
            "messages": json_safe(messages),
            "stream": False,
        }
        if self.model:
            body["model"] = self.model
        normalized_tools = normalize_tool_definitions(tools)
        if normalized_tools:
            body["tools"] = normalized_tools
        if metadata:
            body["metadata"] = json_safe(metadata)
        response = await self._client.post(
            f"{self.base_url}/v1/chat/completions",
            json=body,
            headers=self._headers(),
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                message = choice.get("message") or choice.get("delta")
                if message is not None:
                    return normalize_assistant_message(message)
        return normalize_assistant_message(payload)

    async def rollout(self, *, raw_trajectory: dict[str, Any], scenario: str) -> dict[str, Any]:
        """Call a custom end-to-end rollout endpoint for scenario 2-1."""
        response = await self._client.post(
            f"{self.base_url}/v1/sft/rollout",
            json={"scenario": scenario, "raw_trajectory": json_safe(raw_trajectory)},
            headers=self._headers(),
        )
        response.raise_for_status()
        payload = response.json()
        return json_safe(payload) if isinstance(payload, dict) else {"result": payload}

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}
