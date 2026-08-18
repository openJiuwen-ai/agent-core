# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Completion forwarding with optional gateway-owned trajectory capture."""

from __future__ import annotations

import time
from typing import Any

from fastapi import Request

from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.ports import (
    CollectorCapture,
    TrajectoryCollector,
)
from openjiuwen.core.common.logging import EventStatus, LogEventType, create_log_event, llm_logger, logger

from ...lora_runtime import build_lora_info
from ..upstream import Forwarder
from .http_helpers import build_upstream_headers
from .request_context import require_messages, require_user_id, resolve_trace_id


def _inject_latest_lora(
    *,
    body: dict[str, Any],
    user_id: str,
    lora_repo: Any = None,
    lora_default_policy: str = "disabled",
) -> dict[str, Any] | None:
    if lora_repo is None or lora_default_policy != "latest_by_user":
        return None
    latest_lora = lora_repo.get_latest(user_id)
    if latest_lora:
        body["model"] = user_id
        extra_body = body.get("extra_body")
        if isinstance(extra_body, dict):
            extra_body.pop("lora_name", None)
            if not extra_body:
                body.pop("extra_body", None)
        return build_lora_info(user_id, latest_lora, default_policy=lora_default_policy)
    return None


def _force_gateway_capture_fields(body: dict[str, Any]) -> None:
    body["logprobs"] = True
    body["top_logprobs"] = 1
    body["return_token_ids"] = True


def _strip_internal_capture_fields(
    response: dict[str, Any],
    *,
    include_token_ids: bool,
    include_logprobs: bool,
) -> None:
    if not include_token_ids:
        response.pop("prompt_token_ids", None)
    choices = response.get("choices")
    if not isinstance(choices, list):
        return
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        if not include_token_ids:
            choice.pop("token_ids", None)
        if not include_logprobs:
            choice.pop("logprobs", None)


_FAILURE_METRICS = {
    "missing_completion_ids": ("missing_token_ids", "missing_token_ids"),
    "missing_logprobs": ("logprob_mismatch", "logprob_mismatch"),
    "logprob_length_mismatch": ("logprob_mismatch", "logprob_mismatch"),
    "malformed_logprobs": ("logprob_mismatch", "logprob_mismatch"),
    "session_terminal": ("lifecycle_violations", "lifecycle_violation"),
}


class GatewayCompletionRuntime:
    """Forward provider-normalized completions through one fail-open capture path."""

    def __init__(
        self,
        *,
        config: Any,
        forwarder: Forwarder,
        collector: TrajectoryCollector | None,
        lora_repo: Any = None,
    ) -> None:
        self._config = config
        self._forwarder = forwarder
        self._collector = collector
        self._lora_repo = lora_repo
        self._collection_counters = {
            "attempts": 0,
            "successes": 0,
            "dropped_samples": 0,
            "missing_token_ids": 0,
            "logprob_mismatch": 0,
            "lifecycle_violations": 0,
            "unexpected_failures": 0,
        }

    def collection_stats(self) -> dict[str, int]:
        return dict(self._collection_counters)

    async def execute(
        self,
        *,
        request: Request,
        body: dict[str, Any],
        capture_metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        user_id = require_user_id(request, self._config)
        lora_info = _inject_latest_lora(
            body=body,
            user_id=user_id,
            lora_repo=self._lora_repo,
            lora_default_policy=getattr(self._config, "lora_default_policy", "disabled"),
        )
        if lora_info:
            logger.info(
                "[Gateway] applied LoRA adapter user=%s version=%s path=%s via model field",
                user_id,
                lora_info.get("version"),
                lora_info.get("path"),
            )

        client_wants_stream = bool(body.pop("stream", False))
        client_requested_token_ids = bool(body.get("return_token_ids", False))
        client_requested_logprobs = bool(body.get("logprobs", False))
        collection_session_id = str(body.get("session_id") or request.headers.get("x-session-id") or "").strip()
        capture = await self._prepare_capture(
            body=body,
            user_id=user_id,
            session_id=collection_session_id,
            capture_metadata=capture_metadata,
        )
        capture_fields_forced = capture is not None
        if capture is not None:
            _force_gateway_capture_fields(body)
        response_json = await self._forward(request=request, body=body)
        if capture is not None:
            await self._commit_capture(capture, response_json, collection_session_id)

        if lora_info:
            response_json["rl_lora"] = lora_info
        if capture_fields_forced:
            _strip_internal_capture_fields(
                response_json,
                include_token_ids=client_requested_token_ids,
                include_logprobs=client_requested_logprobs,
            )
        return response_json, client_wants_stream

    async def _prepare_capture(
        self,
        *,
        body: dict[str, Any],
        user_id: str,
        session_id: str,
        capture_metadata: dict[str, Any] | None,
    ) -> CollectorCapture | None:
        if not session_id:
            return None
        collector = self._collector
        if collector is None:
            return None
        capture_request = dict(body)
        capture_request["session_id"] = session_id
        capture_request["user_id"] = user_id
        if capture_metadata:
            capture_request.update(capture_metadata)
        try:
            capture = await collector.capture(session_id, capture_request)
        except Exception as exc:
            self._collection_counters["attempts"] += 1
            self._record_collection_failure(session_id=session_id, stage="preparation", exc=exc)
            return None
        if capture is None:
            return None
        self._collection_counters["attempts"] += 1
        return capture

    async def _forward(self, *, request: Request, body: dict[str, Any]) -> dict[str, Any]:
        started_at = time.perf_counter()
        trace_id = resolve_trace_id(request)
        messages = require_messages(body)
        upstream_headers = build_upstream_headers(request, llm_api_key=self._config.llm_api_key)
        logger.debug(
            "[Gateway %s] proxy_only messages=%d stream=%s",
            trace_id,
            len(messages),
            bool(body.get("stream", False)),
        )
        response = await self._forwarder.forward(body=body, headers=upstream_headers)
        logger.debug("[Gateway] chat_completions cost_ms=%.1f", (time.perf_counter() - started_at) * 1000)
        return response

    async def _commit_capture(
        self,
        capture: CollectorCapture,
        response: dict[str, Any],
        session_id: str,
    ) -> None:
        try:
            await capture.commit(response)
        except Exception as exc:
            self._record_collection_failure(session_id=session_id, stage="commit", exc=exc)
            return
        self._collection_counters["successes"] += 1

    def _record_collection_failure(self, *, session_id: str, stage: str, exc: Exception) -> None:
        code = getattr(exc, "code", None)
        code_value = str(getattr(code, "value", code))
        counter, category = _FAILURE_METRICS.get(code_value, ("unexpected_failures", "unexpected"))
        self._collection_counters["dropped_samples"] += 1
        self._collection_counters[counter] += 1
        event = create_log_event(
            LogEventType.LLM_CALL_ERROR,
            session_id=session_id,
            status=EventStatus.FAILURE,
            error_code=category,
            error_message=str(exc),
            exception=exc,
            extra_params={"stage": stage, "category": category, "error_type": type(exc).__name__},
        )
        llm_logger.error("gateway collection failed during %s", stage, event=event)
