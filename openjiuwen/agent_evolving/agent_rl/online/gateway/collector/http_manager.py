"""HTTP-backed collection-session manager for multi-process gateway runs."""

from __future__ import annotations

from typing import Any

import httpx

from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.types import (
    CollectionSessionRecord,
    CollectionSessionSpec,
)
from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.task_reward import TaskReward


class HttpCollectionSessionManager:
    """Delegate collection lifecycle to the Gateway owning TrajectorySession."""

    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self._client is not None:
            response = await self._client.request(method, f"{self._base_url}{path}", **kwargs)
        else:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.request(method, f"{self._base_url}{path}", **kwargs)
        response.raise_for_status()
        return response.json()

    async def create_session(self, spec: CollectionSessionSpec) -> CollectionSessionRecord:
        payload = await self._request(
            "POST",
            "/v1/gateway/collection/sessions",
            json={
                "session_id": spec.session_id,
                "collection_mode": spec.collection_mode.value,
                "model_id": spec.model_id,
                "tokenizer_revision": spec.tokenizer_revision,
                "template_revision": spec.template_revision,
                "reward_mode": spec.reward_mode.value,
            },
        )
        return CollectionSessionRecord.from_json(payload["session"])

    async def get_session(self, session_id: str) -> CollectionSessionRecord | None:
        response = await self._request("GET", f"/v1/gateway/collection/sessions/{session_id}")
        return CollectionSessionRecord.from_json(response["session"])

    async def finalize_session(self, session_id: str) -> CollectionSessionRecord:
        payload = await self._request("POST", f"/v1/gateway/collection/sessions/{session_id}/finalize")
        return CollectionSessionRecord.from_json(payload["session"])

    async def abort_session(self, session_id: str) -> CollectionSessionRecord:
        payload = await self._request("POST", f"/v1/gateway/collection/sessions/{session_id}/abort")
        return CollectionSessionRecord.from_json(payload["session"])

    async def submit_task_reward(self, session_id: str, reward: TaskReward) -> int:
        """Submit one terminal verifier reward to the owning Gateway."""
        payload = await self._request(
            "POST",
            f"/v1/gateway/collection/sessions/{session_id}/task-reward",
            json=reward.to_payload(),
        )
        return int(payload["projected_samples"])
