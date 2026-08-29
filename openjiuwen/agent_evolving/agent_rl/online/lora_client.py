# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Loopback AIGW LoRA control adapter for TrainingRunner."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from openjiuwen.agent_evolving.agent_rl.online.training_runner import PolicySnapshot
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error


class AIGWLoRAClient:
    """Resolve and activate the Service model's current LoRA through AIGW."""

    def __init__(
        self,
        *,
        endpoint: str,
        model_id: str,
        timeout: float = 150.0,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model_id = model_id
        self._timeout = timeout
        self._http_client = http_client

    async def active_policy(self) -> PolicySnapshot:
        """Return the active policy, treating an explicit base state as base."""

        try:
            response = await self._http_client.get(
                f"{self._endpoint}/internal/v1/rl/loras/{self._model_id}",
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise_error(StatusCode.AGENT_RL_LORA_CALL_FAILED, cause=exc, error_msg=str(exc))
        if response.status_code >= 400:
            raise_error(
                StatusCode.AGENT_RL_LORA_CALL_FAILED,
                error_msg=f"query status {response.status_code}: {response.text[:500]}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise_error(StatusCode.AGENT_RL_LORA_CALL_FAILED, cause=exc, error_msg="invalid query response JSON")
        if not isinstance(payload, Mapping):
            raise_error(
                StatusCode.AGENT_RL_LORA_CALL_FAILED,
                error_msg="query response JSON must be an object",
            )
        active = payload.get("active_lora")
        if payload.get("status") == "base" or not isinstance(active, dict):
            return PolicySnapshot()
        return PolicySnapshot(
            lora_name=str(active.get("lora_name") or "base"),
            lora_path=str(active.get("lora_path") or ""),
        )

    async def activate(self, **kwargs: Any) -> None:
        """Activate one artifact with the Run's captured parent version."""

        if kwargs.get("model_id") != self._model_id:
            raise_error(
                StatusCode.AGENT_RL_SERVICE_PARAM_ERROR,
                error_msg="activation model_id does not match RL Service model_id",
            )
        payload = {
            "base_model": kwargs["base_model"],
            "lora_name": kwargs["lora_name"],
            "lora_path": kwargs["lora_path"],
            "expected_lora_name": kwargs["expected_lora_name"],
            "training_run_id": kwargs["training_run_id"],
        }
        try:
            response = await self._http_client.post(
                f"{self._endpoint}/internal/v1/rl/loras/activate",
                json=payload,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            policy = await self.active_policy()
            if policy.lora_name == kwargs["lora_name"]:
                return
            raise_error(
                StatusCode.AGENT_RL_LORA_CALL_FAILED,
                cause=exc,
                error_msg="LoRA activation request timed out",
            )
        except httpx.HTTPError as exc:
            raise_error(StatusCode.AGENT_RL_LORA_CALL_FAILED, cause=exc, error_msg=str(exc))
        if response.status_code >= 400:
            raise_error(
                StatusCode.AGENT_RL_LORA_CALL_FAILED,
                error_msg=f"activation status {response.status_code}: {response.text[:500]}",
            )


__all__ = ["AIGWLoRAClient"]
