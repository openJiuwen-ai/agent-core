# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Gateway-owned trajectory capture and collection-session lifecycle."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.ports import GatewaySamplePipeline
from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.response import (
    UpstreamGenerationData,
    parse_vllm_response,
)
from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.types import (
    CollectionSessionError,
    CollectionSessionErrorCode,
    CollectionSessionRecord,
    CollectionSessionSpec,
    CollectionSessionStatus,
    RewardMode,
)
from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.sample_payloads import build_sample
from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.task_reward import TaskReward

_SESSION_KEY_PREFIX = "rl:gateway_collection_session"


class _PreparedCapture:
    def __init__(
        self,
        *,
        spec: CollectionSessionSpec,
        request: Mapping[str, Any],
        commit_sample: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._spec = spec
        self._request = dict(request)
        self._commit_sample = commit_sample
        self._committed = False
        self._call_id = uuid.uuid4().hex

    async def commit(self, response: Mapping[str, Any]) -> dict[str, Any]:
        if self._committed:
            raise RuntimeError("capture already committed")
        self._committed = True
        sample = self._build_sample(parse_vllm_response(response))
        await self._commit_sample(self._spec.session_id, sample)
        return sample

    def _build_sample(self, data: UpstreamGenerationData) -> dict[str, Any]:
        assistant_message = _json_value(data.assistant_message)
        response_text = str(assistant_message.get("content") or assistant_message.get("reasoning_content") or "")
        messages = list(self._request.get("messages") or [])
        turn_id = (
            sum(1 for message in messages if isinstance(message, Mapping) and message.get("role") == "assistant") + 1
        )
        trajectory_id = f"gateway:{self._spec.session_id}:call-{self._call_id}"
        extra_fields = {
            "trajectory_id": trajectory_id,
            "step_index": 0,
            "collection_identity": {
                "collection_mode": "gateway",
                "session_id": self._spec.session_id,
                "call_id": self._call_id,
                "turn_id": turn_id,
                "model_id": self._spec.model_id,
                "tokenizer_revision": self._spec.tokenizer_revision,
                "template_revision": self._spec.template_revision,
            },
        }
        adjustments = self._request.get("_gateway_request_adjustments")
        if isinstance(adjustments, Mapping):
            extra_fields["request_adjustments"] = _json_value(adjustments)
        sample = build_sample(
            sample_id=f"{trajectory_id}:0",
            user_id=str(self._request.get("user_id") or ""),
            session_id=self._spec.session_id,
            turn_num=turn_id,
            mode="gateway",
            io_mode="gateway",
            model=self._spec.model_id,
            messages=messages,
            tools=self._request.get("tools"),
            assistant_message=assistant_message,
            usage={
                "prompt_tokens": len(data.prompt_ids),
                "completion_tokens": len(data.completion_ids),
                "total_tokens": len(data.prompt_ids) + len(data.completion_ids),
            },
            finish_reason=data.finish_reason,
            prompt_text="",
            prompt_ids=list(data.prompt_ids),
            response_text=response_text,
            response_ids=list(data.completion_ids),
            response_logprobs=list(data.completion_logprobs),
            response_token_mask=[1] * len(data.completion_ids),
            tool_calls=list(assistant_message.get("tool_calls") or []),
            extra_fields=extra_fields,
        )
        if data.routed_experts is not None:
            sample["trajectory"]["routed_experts"] = _json_value(data.routed_experts)
        if data.routing_metadata is not None:
            sample["trajectory"]["routing_metadata"] = _json_value(data.routing_metadata)
        return sample


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    return value


class GatewayTrajectoryCollector:
    """Persist sessions and turn exact upstream token data into training samples."""

    def __init__(
        self,
        *,
        redis: Any,
        sample_pipeline: GatewaySamplePipeline,
    ) -> None:
        self._redis = redis
        self._sample_pipeline = sample_pipeline
        self._transition_lock = asyncio.Lock()

    async def create_session(self, spec: CollectionSessionSpec) -> CollectionSessionRecord:
        record = CollectionSessionRecord(spec=spec)
        created = await self._redis.set(self._session_key(spec.session_id), record.to_storage_json(), nx=True)
        if not created:
            raise CollectionSessionError(
                CollectionSessionErrorCode.DUPLICATE_CREATE,
                f"collection session already exists: {spec.session_id}",
            )
        return record

    async def get_session(self, session_id: str) -> CollectionSessionRecord | None:
        payload = await self._redis.get(self._session_key(session_id))
        return None if payload is None else CollectionSessionRecord.from_json(payload)

    async def capture(self, session_id: str, request: Mapping[str, Any]) -> _PreparedCapture | None:
        async with self._transition_lock:
            record = await self.get_session(session_id)
            if (
                record is None
                or not record.accepts_captures
            ):
                return None
            if record.spec.reward_mode is RewardMode.DELAYED_FEEDBACK:
                messages = request.get("messages")
                await self._sample_pipeline.on_gateway_followup(
                    session_id,
                    list(messages) if isinstance(messages, list) else [],
                )
            return _PreparedCapture(
                spec=record.spec,
                request=request,
                commit_sample=self._commit_sample,
            )

    async def finalize_session(self, session_id: str) -> CollectionSessionRecord:
        async with self._transition_lock:
            record = await self._require_session(session_id)
            finalizing = record.begin_finalize()
            if finalizing != record:
                await self._save(finalizing, "begin finalize")
            if finalizing.status is CollectionSessionStatus.FINALIZED:
                return finalizing
            return await self._complete_finalize(finalizing)

    async def abort_session(self, session_id: str) -> CollectionSessionRecord:
        async with self._transition_lock:
            record = await self._require_session(session_id)
            aborted = record.abort()
            await self._sample_pipeline.discard_gateway_session(session_id)
            await self._save(aborted, "abort")
            return aborted

    async def _complete_finalize(self, record: CollectionSessionRecord) -> CollectionSessionRecord:
        await self._sample_pipeline.flush_gateway_session(record.spec.session_id)
        completed = record.complete_finalize()
        await self._save(completed, "complete finalize effects")
        return completed

    async def submit_task_reward(self, session_id: str, reward: TaskReward) -> int:
        async with self._transition_lock:
            record = await self._require_session(session_id)
            record.require_task_reward()
            return await self._sample_pipeline.submit_task_reward(session_id, reward)

    async def _commit_sample(self, session_id: str, sample: dict[str, Any]) -> None:
        async with self._transition_lock:
            await self._require_active(session_id)
            await self._sample_pipeline.stage_gateway_sample(sample)

    async def _require_active(self, session_id: str) -> CollectionSessionRecord:
        record = await self._require_session(session_id)
        record.require_active()
        return record

    async def _require_session(self, session_id: str) -> CollectionSessionRecord:
        record = await self.get_session(session_id)
        if record is None:
            raise CollectionSessionError(
                CollectionSessionErrorCode.UNKNOWN_SESSION,
                f"unknown collection session: {session_id}",
            )
        return record

    async def _save(self, record: CollectionSessionRecord, operation: str) -> None:
        saved = await self._redis.set(
            self._session_key(record.spec.session_id),
            record.to_storage_json(),
            xx=True,
        )
        if saved:
            return
        raise CollectionSessionError(
            CollectionSessionErrorCode.PERSISTENCE_FAILURE,
            f"failed to persist {operation} for collection session: {record.spec.session_id}",
        )

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"{_SESSION_KEY_PREFIX}:{session_id}"
