# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Capture complete OpenAI completions and publish rewarded RL samples."""

# TaskRegistry and CapturePipeline form one package-level module cluster; the
# registry's capture/turn operations are intentionally internal to that cluster.
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Real
from typing import Any, Protocol

from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.sample_payloads import build_sample
from openjiuwen.agent_evolving.agent_rl.online.task_registry import (
    FinishReason,
    TaskConflictError,
    TaskNotFoundError,
    TaskRecord,
    TaskRegistry,
    TaskStatus,
    TurnClosedError,
    _TurnTransition,
)
from openjiuwen.agent_evolving.agent_rl.storage.trajectory_store import TrajectorySampleStore


@dataclass(frozen=True, slots=True)
class _GenerationData:
    assistant_message: dict[str, Any]
    prompt_ids: tuple[int, ...]
    completion_ids: tuple[int, ...]
    completion_logprobs: tuple[float, ...]
    finish_reason: str
    routed_experts: Any | None = None
    routing_metadata: Mapping[str, Any] | None = None


class Judge(Protocol):
    """Score a complete policy call using the next user message as feedback."""

    async def score(
        self,
        request: Mapping[str, Any],
        response: Mapping[str, Any],
        followup_user_message: str,
    ) -> float:
        """Return a reward in ``[0, 1]`` or raise when any vote fails."""


class CapturePipeline:
    """Validate, stage, score, and publish complete policy calls."""

    def __init__(
        self,
        *,
        registry: TaskRegistry,
        trajectory_store: TrajectorySampleStore,
        judge: Judge | None = None,
    ) -> None:
        self._registry = registry
        self._trajectory_store = trajectory_store
        self._judge = judge

    async def before(
        self,
        rl_task_id: str,
        capture_id: str,
        agent_turn_id: str | None,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate and reserve one capture, publishing a preceding delayed turn."""

        task = await self._require_task(rl_task_id)
        normalized = self._validate_request(task, capture_id, agent_turn_id, request)
        try:
            while True:
                reservation = await self._registry._begin_capture(
                    rl_task_id,
                    capture_id,
                    agent_turn_id,
                    self._fingerprint(normalized),
                )
                if not isinstance(reservation, _TurnTransition):
                    return normalized
                followup = self._last_user_message(normalized["messages"])
                await self._publish_delayed_turn(
                    task,
                    reservation.previous_turn_id,
                    followup_user_message=followup,
                    tag="feedback",
                )
                await self._registry._advance_turn(
                    rl_task_id,
                    reservation.previous_turn_id,
                    reservation.next_turn_id,
                )
        except TurnClosedError:
            await self._abort_capture_failure(rl_task_id)
            raise

    async def after(
        self,
        rl_task_id: str,
        capture_id: str,
        agent_turn_id: str | None,
        request: Mapping[str, Any],
        response: Mapping[str, Any],
    ) -> None:
        """Validate and stage a complete OpenAI request and response once."""

        try:
            task = await self._require_task(rl_task_id)
            normalized_request = self._validate_request(task, capture_id, agent_turn_id, request)
            normalized_response = self._validate_response(task, response)
            sample = self._build_sample(
                task,
                capture_id,
                agent_turn_id,
                normalized_request,
                normalized_response,
            )
            await self._registry._commit_capture(
                rl_task_id,
                capture_id,
                agent_turn_id,
                self._fingerprint(normalized_response),
                sample,
            )
        except (TaskConflictError, ValueError):
            await self._abort_capture_failure(rl_task_id)
            raise

    async def discard(self, rl_task_id: str, capture_id: str, agent_turn_id: str | None) -> None:
        """Idempotently release a capture abandoned before a complete response."""

        await self._registry._discard_capture(rl_task_id, capture_id, agent_turn_id)

    async def finish(self, rl_task_id: str, reason: FinishReason) -> TaskRecord:
        """Publish the final delayed turn, then finalize an active Task."""

        task = await self._require_task(rl_task_id)
        if task.status is not TaskStatus.ACTIVE:
            return task
        if task.reward_mode.value == "delayed_feedback":
            current_turn = await self._registry._current_turn(rl_task_id)
            if current_turn is not None:
                await self._publish_delayed_turn(
                    task,
                    current_turn,
                    followup_user_message="",
                    tag="session_done",
                    wait_for_captures=False,
                )
        return await self._registry.finalize(rl_task_id, reason)

    async def abort(self, rl_task_id: str, reason: FinishReason) -> TaskRecord:
        """Abort a Task and discard only its unpublished samples."""

        return await self._registry.abort(rl_task_id, reason)

    async def submit_reward(self, rl_task_id: str, reward: float) -> int:
        """Idempotently publish all terminal samples with one reward."""

        reward = float(reward)
        if not 0.0 <= reward <= 1.0:
            raise ValueError("reward must be between 0 and 1")
        samples, sample_count, completed = await self._registry._claim_terminal_reward(rl_task_id, reward)
        if completed:
            return sample_count
        projected = []
        for sample in samples:
            item = copy.deepcopy(sample)
            item["judge"] = {"score": reward, "source": "terminal"}
            projected.append(item)
        await self._save_samples_once(projected)
        await self._registry._complete_terminal_reward(rl_task_id, reward, sample_count)
        return sample_count

    async def _require_task(self, rl_task_id: str) -> TaskRecord:
        task = await self._registry.get(rl_task_id)
        if task is None:
            raise TaskNotFoundError(f"unknown RL Task: {rl_task_id}")
        return task

    @staticmethod
    def _validate_request(
        task: TaskRecord,
        capture_id: str,
        agent_turn_id: str | None,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not capture_id.strip():
            raise ValueError("capture_id is required")
        if not isinstance(request, Mapping):
            raise ValueError("request must be an object")
        normalized = CapturePipeline._json_value(request)
        if normalized.get("model") != task.policy_model:
            raise TaskConflictError("request model does not match Task policy")
        if not isinstance(normalized.get("messages"), list):
            raise ValueError("request messages must be a list")
        if normalized.get("n", 1) != 1:
            raise ValueError("RL capture requires n=1")
        if task.reward_mode.value == "delayed_feedback" and not str(agent_turn_id or "").strip():
            raise ValueError("agent_turn_id is required for delayed_feedback Task")
        normalized["logprobs"] = True
        normalized["top_logprobs"] = 1
        normalized["return_token_ids"] = True
        if normalized.get("stream") is True:
            stream_options = normalized.get("stream_options")
            if not isinstance(stream_options, dict):
                stream_options = {}
            normalized["stream_options"] = {**stream_options, "include_usage": True}
        return normalized

    @staticmethod
    def _validate_response(task: TaskRecord, response: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(response, Mapping):
            raise ValueError("response must be an object")
        normalized = CapturePipeline._json_value(response)
        if normalized.get("object") != "chat.completion":
            raise ValueError("response object must be chat.completion")
        if normalized.get("model") != task.policy_model:
            raise TaskConflictError("response model does not match Task policy")
        choices = normalized.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("response must contain exactly one choice")
        truth = CapturePipeline._parse_response(normalized)
        usage = normalized.get("usage")
        if not isinstance(usage, Mapping):
            raise ValueError("response is missing usage")
        expected_usage = {
            "prompt_tokens": len(truth.prompt_ids),
            "completion_tokens": len(truth.completion_ids),
            "total_tokens": len(truth.prompt_ids) + len(truth.completion_ids),
        }
        for field_name, expected in expected_usage.items():
            value = usage.get(field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value != expected:
                raise ValueError(f"response usage {field_name} does not match token IDs")
        return normalized

    @staticmethod
    def _build_sample(
        task: TaskRecord,
        capture_id: str,
        agent_turn_id: str | None,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> dict[str, Any]:
        truth = CapturePipeline._parse_response(response)
        assistant_message = copy.deepcopy(truth.assistant_message)
        response_text = str(assistant_message.get("content") or assistant_message.get("reasoning_content") or "")
        sample = build_sample(
            sample_id=capture_id,
            user_id=task.model_id,
            session_id=task.agent_session_id,
            turn_num=0,
            mode="gateway",
            io_mode="gateway",
            model=task.model_id,
            messages=copy.deepcopy(request["messages"]),
            tools=copy.deepcopy(request.get("tools")),
            assistant_message=assistant_message,
            usage=copy.deepcopy(response["usage"]),
            finish_reason=truth.finish_reason,
            prompt_text="",
            prompt_ids=list(truth.prompt_ids),
            response_text=response_text,
            response_ids=list(truth.completion_ids),
            response_logprobs=list(truth.completion_logprobs),
            tool_calls=copy.deepcopy(assistant_message.get("tool_calls") or []),
            response_token_mask=[1] * len(truth.completion_ids),
            extra_fields={
                "trajectory_id": capture_id,
                "rl_task_id": task.rl_task_id,
                "task_id": task.rl_task_id,
                "agent_turn_id": agent_turn_id,
                "policy_version": task.policy_lora_name,
                "source": "rl_task",
            },
        )
        sample["request"] = copy.deepcopy(request)
        sample["response"] = copy.deepcopy(response)
        if truth.routed_experts is not None:
            sample["trajectory"]["routed_experts"] = CapturePipeline._json_value(truth.routed_experts)
        if truth.routing_metadata is not None:
            sample["trajectory"]["routing_metadata"] = CapturePipeline._json_value(truth.routing_metadata)
        return sample

    async def _save_samples_once(self, samples: list[dict[str, Any]]) -> set[str]:
        return await self._trajectory_store.save_samples_once(samples)

    async def _publish_delayed_turn(
        self,
        task: TaskRecord,
        agent_turn_id: str,
        *,
        followup_user_message: str,
        tag: str,
        wait_for_captures: bool = True,
    ) -> None:
        while True:
            claim = await self._registry._claim_turn_publish(task.rl_task_id, agent_turn_id)
            if claim == "published":
                return
            if claim == "failed":
                raise TaskConflictError(f"turn publication failed: {agent_turn_id}")
            if claim == "waiting" and not wait_for_captures:
                raise TaskConflictError("RL Task still has open captures")
            if claim in {"waiting", "publishing"}:
                await asyncio.sleep(0.01)
                continue
            break

        saved_ids: set[str] = set()
        try:
            if self._judge is None:
                raise RuntimeError("delayed_feedback Task requires Judge")
            samples = await self._registry._turn_pending_samples(task.rl_task_id, agent_turn_id)
            scores = await asyncio.gather(
                *[self._judge.score(sample["request"], sample["response"], followup_user_message) for sample in samples]
            )
            published: list[dict[str, Any]] = []
            for sample, raw_score in zip(samples, scores):
                score = float(raw_score)
                if not 0.0 <= score <= 1.0:
                    raise ValueError("Judge reward must be between 0 and 1")
                item = copy.deepcopy(sample)
                item["judge"] = {"score": score, "source": "judge", "tag": tag}
                published.append(item)
            saved_ids = await self._save_samples_once(published)
            await self._registry._complete_turn_publish(task.rl_task_id, agent_turn_id)
        except Exception:
            for sample_id in saved_ids:
                await self._trajectory_store.delete_sample(sample_id)
            await self._registry._fail_turn_publish(task.rl_task_id, agent_turn_id)
            await self._abort_capture_failure(task.rl_task_id)
            raise

    async def _abort_capture_failure(self, rl_task_id: str) -> None:
        task = await self._registry.get(rl_task_id)
        if task is not None:
            await self._registry.abort(rl_task_id, FinishReason.CAPTURE_FAILED)

    @staticmethod
    def _fingerprint(value: Mapping[str, Any]) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _last_user_message(messages: list[Any]) -> str:
        for message in reversed(messages):
            if isinstance(message, Mapping) and message.get("role") == "user":
                content = CapturePipeline._replace_images(message.get("content"))
                if isinstance(content, str):
                    return content
                return json.dumps(content, ensure_ascii=False, sort_keys=True)
        return ""

    @staticmethod
    def _replace_images(value: Any) -> Any:
        if isinstance(value, Mapping):
            item_type = str(value.get("type") or "")
            if item_type in {"image", "image_url", "input_image"} or "image_url" in value:
                return "[image]"
            return {str(key): CapturePipeline._replace_images(item) for key, item in value.items()}
        if isinstance(value, list):
            return [CapturePipeline._replace_images(item) for item in value]
        return value

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): CapturePipeline._json_value(item) for key, item in value.items()}
        if isinstance(value, tuple | list):
            return [CapturePipeline._json_value(item) for item in value]
        if hasattr(value, "tolist"):
            return CapturePipeline._json_value(value.tolist())
        return value

    @staticmethod
    def _parse_response(response: Mapping[str, Any]) -> _GenerationData:
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
            raise ValueError("response must contain exactly one completion choice")
        choice = choices[0]
        assistant_message = choice.get("message")
        if not isinstance(assistant_message, Mapping):
            raise ValueError("response is missing assistant message")
        finish_reason = choice.get("finish_reason")
        if not isinstance(finish_reason, str) or not finish_reason:
            raise ValueError("response is missing finish reason")
        prompt_ids = CapturePipeline._token_ids(response.get("prompt_token_ids"), "prompt token IDs")
        completion_ids = CapturePipeline._token_ids(choice.get("token_ids"), "completion token IDs")
        content = choice.get("logprobs")
        content = content.get("content") if isinstance(content, Mapping) else None
        if not isinstance(content, list):
            raise ValueError("response is missing token log-probabilities")
        logprobs: list[float] = []
        for item in content:
            logprob = item.get("logprob") if isinstance(item, Mapping) else None
            if isinstance(logprob, bool) or not isinstance(logprob, Real):
                raise ValueError("response has invalid token log-probabilities")
            normalized_logprob = float(logprob)
            if not math.isfinite(normalized_logprob) or normalized_logprob > 0.0:
                raise ValueError("response has invalid token log-probabilities")
            logprobs.append(normalized_logprob)
        if len(completion_ids) != len(logprobs):
            raise ValueError("completion token IDs and log-probabilities must align")
        return _GenerationData(
            assistant_message=dict(assistant_message),
            prompt_ids=prompt_ids,
            completion_ids=completion_ids,
            completion_logprobs=tuple(logprobs),
            finish_reason=finish_reason,
            routed_experts=choice.get("routed_experts", response.get("routed_experts")),
            routing_metadata=choice.get("routing_metadata", response.get("routing_metadata")),
        )

    @staticmethod
    def _token_ids(value: Any, field_name: str) -> tuple[int, ...]:
        if (
            not isinstance(value, list)
            or not value
            or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value)
        ):
            raise ValueError(f"response has invalid {field_name}")
        return tuple(value)


__all__ = ["CapturePipeline", "Judge"]
