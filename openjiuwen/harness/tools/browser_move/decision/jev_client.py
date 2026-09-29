# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Browser budgets and validation around the shared Jev System One client."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from json import JSONDecodeError
from typing import Any

import httpx

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.exception.errors import (
    ValidationError as RequestValidationError,
)
from openjiuwen.core.foundation.llm.system_one import JevSystemOneClient
from openjiuwen.core.foundation.llm.utils.request_sanitizer import clean_unicode

from ..playwright_runtime.browser_logging import browser_agent_log_info
from .config import BrowserDecisionConfig

_TRACE: ContextVar[dict[str, str]] = ContextVar("browser_jev_trace", default={})


@contextmanager
def decision_trace(decision_id: str, task_id: str):
    token = _TRACE.set({"decision_id": decision_id, "task_id": task_id})
    try:
        yield
    finally:
        _TRACE.reset(token)


class DecisionUnavailable(ValueError):
    """A safe diagnostic code; never contains response bodies or credentials."""


def validate_choice(answer: Any, criteria: dict[str, str], threshold: float) -> str:
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise DecisionUnavailable("invalid_choice_type")
    probabilities = answer.get("probabilities")
    chosen, confidence = answer.get("choice"), answer.get("confidence")
    if (not isinstance(chosen, str) or chosen not in criteria or not isinstance(probabilities, dict)
            or set(probabilities) != set(criteria)):
        raise DecisionUnavailable("invalid_choice_options")
    values = [confidence, *probabilities.values()]
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or not 0 <= v <= 1
           for v in values):
        raise DecisionUnavailable("invalid_choice_probabilities")
    if abs(math.fsum(probabilities.values()) - 1) > 0.01 + 1e-12:
        raise DecisionUnavailable("invalid_choice_distribution")
    if probabilities[chosen] + 1e-6 < max(probabilities.values()):
        raise DecisionUnavailable("invalid_choice_winner")
    if confidence < threshold:
        raise DecisionUnavailable("uncertain_choice")
    return chosen


def validate_action(answers: Any, questions: dict[str, Any], threshold: float) -> tuple[str, dict[str, Any]]:
    """Validate both chosen heads; never pair an operation with another group's target."""
    if not isinstance(answers, dict):
        raise DecisionUnavailable("invalid_choice_type")
    operation = validate_choice(answers.get("action"), questions["action"]["criteria"], threshold)
    if operation in {"HANDOFF", "FINISH"}:
        return operation, answers["action"]
    head = "target_" + operation
    if head not in questions:
        raise DecisionUnavailable("invalid_choice_group")
    key = validate_choice(answers.get(head), questions[head]["criteria"], threshold)
    return key, answers[head]


class JevClient:
    def __init__(self, config: BrowserDecisionConfig, *, client: httpx.AsyncClient | None = None):
        self.config = config
        self._client = client
        self._owns_client = client is None

    async def evaluate(self, payload: dict[str, Any], *, deadline_at: float) -> dict[str, Any]:
        key = os.environ.get(self.config.api_key_env, "").strip()
        if not key:
            raise DecisionUnavailable("missing_jev_key")
        if self._client is None:
            self._client = httpx.AsyncClient(follow_redirects=False)
        # This is a total request/retry budget, not a new timeout per attempt.
        remaining = min(self.config.request_timeout_ms / 1000, deadline_at - time.time())
        if remaining <= 0:
            raise DecisionUnavailable("jev_deadline")
        try:
            return await asyncio.wait_for(self._request(payload, key), timeout=remaining)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise DecisionUnavailable("jev_timeout") from exc
        except httpx.HTTPError as exc:
            raise DecisionUnavailable("jev_transport_error") from exc

    async def _request(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        if not isinstance(payload, dict) or "state" not in payload or "questions" not in payload:
            raise DecisionUnavailable("invalid_jev_request")
        client = JevSystemOneClient(
            api_key=key, api_base=self.config.api_base, model_name=self.config.model,
            endpoint_path="/decisions" if self.config.provider == "openrouter" else "/systemone",
            timeout=self.config.request_timeout_ms / 1000, max_retries=0, http_client=self._client,
        )
        # Core owns the typed protocol. Browser policy owns bounded retries and
        # the total deadline; do not multiply these with standalone SDK retries.
        for attempt in range(self.config.max_retries + 1):
            started = time.monotonic()
            diagnostic = {**_TRACE.get(), "attempt": attempt + 1, "provider": self.config.provider}
            browser_agent_log_info("[BROWSER_POLICY_HTTP_START] %s", json.dumps(diagnostic))
            try:
                result = await client.system_one(
                    state=clean_unicode(payload["state"]), questions=clean_unicode(payload["questions"]),
                    model=payload.get("model"),
                )
                diagnostic["status"] = "ok"
            except BaseError as exc:
                diagnostic["status"] = "error"
                cause = exc.cause
                if isinstance(cause, httpx.HTTPStatusError):
                    response = cause.response
                    diagnostic["http_status"] = response.status_code
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < self.config.max_retries:
                            try:
                                delay = max(0.1, float(response.headers.get("Retry-After", "0.1")))
                            except ValueError:
                                delay = 0.1
                            if math.isfinite(delay) and delay <= 1:
                                await asyncio.sleep(delay)
                                continue
                    raise DecisionUnavailable(f"jev_http_{response.status_code}") from None
                if isinstance(cause, httpx.TimeoutException):
                    reason = "jev_timeout"
                elif isinstance(cause, httpx.HTTPError):
                    reason = "jev_transport_error"
                elif isinstance(cause, JSONDecodeError):
                    reason = "invalid_jev_json"
                elif isinstance(exc, RequestValidationError):
                    reason = "invalid_jev_request"
                else:
                    reason = "invalid_jev_response"
                # Never include the SDK's error message, URL or validation input
                # in browser diagnostics; only preserve a fixed reason code.
                raise DecisionUnavailable(reason) from None
            finally:
                diagnostic["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
                diagnostic.setdefault("status", "interrupted")
                browser_agent_log_info("[BROWSER_POLICY_HTTP_END] %s", json.dumps(diagnostic))
            result = result.model_dump(exclude_none=True)
            self._validate_model(result.get("model"))
            return result
        raise DecisionUnavailable("jev_retry_exhausted")

    def _validate_model(self, resolved: Any) -> None:
        requested = self.config.model
        if self.config.provider == "openrouter":
            if not isinstance(resolved, str) or not re.fullmatch(
                r"typesafe/jev-\d+\.\d+(?:\.\d+)?(?:-\d{8})?", resolved,
            ):
                raise DecisionUnavailable("invalid_jev_model")
            if requested == "~typesafe/jev-latest" or resolved == requested:
                return
            if not re.search(r"-\d{8}$", requested) and re.fullmatch(re.escape(requested) + r"-\d{8}", resolved):
                return
            raise DecisionUnavailable("jev_model_mismatch")
        if not isinstance(resolved, str) or not resolved.startswith("jev-"):
            raise DecisionUnavailable("invalid_jev_model")
        if requested not in {"jev-latest", "jev-preview"} and resolved != requested:
            raise DecisionUnavailable("jev_model_mismatch")

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
