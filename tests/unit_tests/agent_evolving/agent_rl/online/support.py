"""Reusable in-process adapters for online-RL tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fakeredis.aioredis import FakeRedis

from openjiuwen.agent_evolving.agent_rl.online.gateway.app.server import build_gateway_app
from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.types import CollectionSessionSpec


class InMemoryRedis(FakeRedis):
    """Decoded async Redis fake shared by online-RL tests."""

    def __init__(self) -> None:
        super().__init__(decode_responses=True)


class _InertTrajectoryRuntime:
    rail_ingestor = SimpleNamespace()

    async def snapshot_stats(self) -> dict[str, Any]:
        return {
            "total_samples": 0,
            "trajectory_store_backend": "InertTrajectoryStore",
            "trajectory_store_total": 0,
            "trajectory_store_pending": 0,
        }


class _InertTrainingTaskStore:
    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = str(payload.get("task_id") or "task-test")
        return {"task_id": task_id, "status": "pending", **payload}

    async def list_tasks(self, *, limit: int = 100) -> list[dict[str, Any]]:
        del limit
        return []

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        del task_id
        return None

    async def request_stop(self, task_id: str) -> dict[str, Any] | None:
        del task_id
        return None

    async def update_task_status(
        self,
        task_id: str,
        *,
        status: str,
        error: str = "",
    ) -> dict[str, Any] | None:
        del task_id, status, error
        return None


def collection_spec(**overrides: Any) -> CollectionSessionSpec:
    values = {
        "session_id": "session-1",
        "collection_mode": "gateway",
        "model_id": "model-1",
        "tokenizer_revision": "tokenizer-r1",
        "template_revision": "template-r1",
    }
    values.update(overrides)
    return CollectionSessionSpec(**values)


def openai_response(
    *,
    prompt_ids: list[int] | None = None,
    token_ids: list[int] | None = None,
    text: str = "pong",
    finish_reason: str = "stop",
    model: str = "model-1",
    response_id: str = "chatcmpl-test",
    logprobs: list[float] | None = None,
) -> dict[str, Any]:
    prompt_ids = [101] if prompt_ids is None else prompt_ids
    token_ids = [201] if token_ids is None else token_ids
    logprobs = [-0.1] * len(token_ids) if logprobs is None else logprobs
    return {
        "id": response_id,
        "model": model,
        "prompt_token_ids": prompt_ids,
        "choices": [
            {
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
                "token_ids": token_ids,
                "logprobs": {"content": [{"token": text, "logprob": value} for value in logprobs]},
            }
        ],
        "usage": {
            "prompt_tokens": len(prompt_ids),
            "completion_tokens": len(token_ids),
            "total_tokens": len(prompt_ids) + len(token_ids),
        },
    }


def gateway_test_app(
    *,
    forwarder: Any,
    collector: Any = None,
    trajectory_runtime: Any = None,
    **config_overrides: Any,
) -> Any:
    """Build a Gateway app with inert dependencies and overridable config."""
    config = {
        "gateway_api_key": "",
        "llm_api_key": "",
        "llm_url": "http://upstream.invalid",
        "model_id": "model-1",
        "single_user_default": False,
        "lora_default_policy": "disabled",
        "anthropic_max_completion_tokens": 0,
        "instance_id": "test-instance",
    }
    config.update(config_overrides)

    async def close_resources() -> None:
        return None

    return build_gateway_app(
        config=SimpleNamespace(**config),
        forwarder=forwarder,
        upstream_client=SimpleNamespace(),
        trajectory_runtime=trajectory_runtime or _InertTrajectoryRuntime(),
        training_task_store=_InertTrainingTaskStore(),
        close_resources=close_resources,
        collector=collector,
    )
