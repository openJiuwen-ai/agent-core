# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Budgeted, transient proposals on copies of the final model request."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from openjiuwen.core.common.logging import agent_logger
from openjiuwen.core.foundation.llm.reasoning import (
    is_reasoning_config_intent,
    resolve_reasoning_plan,
    resolve_reasoning_protocol,
)
from openjiuwen.core.foundation.llm.schema.message import BaseMessage, UserMessage
from openjiuwen.harness.moa.state import ProposalAttempt, ProposalBatch, active_moa
from openjiuwen.harness.prompts.moa import render_reference_proposals


def _request_extras(model_config: Any) -> dict[str, Any]:
    dump = getattr(model_config, "model_dump", None)
    if not callable(dump):
        return {}
    return dump(
        exclude={"model_name", "model", "temperature", "top_p", "max_tokens", "stop", "context_window", "reasoning"},
        exclude_none=True,
    )


def _anthropic_output_tokens(model: Any, kwargs: dict[str, Any], requested: Any) -> int | None:
    """Mirror the adapter's ceiling after reasoning resolution and overrides."""
    params = _request_extras(model.model_config)
    params.update(kwargs)
    plan = resolve_reasoning_plan(
        model.model_client_config,
        model.model_config,
        request_model=kwargs.get("model"),
        explicit_kwargs=kwargs,
    )
    override = is_reasoning_config_intent(kwargs.get("reasoning"))
    for key, value in plan.sdk_params.items():
        if override or key not in params:
            params[key] = value
    extra_body = params.get("extra_body")
    if extra_body is not None and not isinstance(extra_body, Mapping):
        return None
    extra_body = dict(extra_body or {})
    extra_body = {**extra_body, **plan.extra_body} if override else {**plan.extra_body, **extra_body}

    ceiling = requested or 8192
    if not isinstance(ceiling, int) or isinstance(ceiling, bool) or ceiling <= 0:
        return None
    thinking = params.get("thinking")
    budget = thinking.get("budget_tokens") if isinstance(thinking, Mapping) else None
    if isinstance(budget, int) and ceiling <= budget:
        ceiling = budget + 1024
    # The SDK merges extra_body after the adapter has raised its ceiling.
    # Its explicit wire override therefore wins over the generated value.
    if "max_tokens" in extra_body:
        ceiling = extra_body["max_tokens"]
    return ceiling if isinstance(ceiling, int) and not isinstance(ceiling, bool) and ceiling > 0 else None


def _output_tokens(model: Any, kwargs: dict[str, Any]) -> int | None:
    """Reserve only an output ceiling that applies to the actual request."""
    try:
        client_config = getattr(model, "model_client_config", None)
        model_config = getattr(model, "model_config", None)
        protocol = resolve_reasoning_protocol(client_config)
        requested = kwargs.get("max_tokens")
        if requested is None:
            requested = getattr(model_config, "max_tokens", None)
        if protocol == "anthropic_messages":
            return _anthropic_output_tokens(model, kwargs, requested)
        if protocol == "responses" and not bool(kwargs.get("send_max_output_tokens", False)):
            return None

        extras = _request_extras(model_config)
        extras.update(kwargs)
        # Both configured and per-call extra_body are expanded by the current
        # Responses / Chat adapters; a per-call body replaces the configured one.
        extra_body = extras.get("extra_body")
        if extra_body is not None and not isinstance(extra_body, Mapping):
            return None
        output_key = "max_output_tokens" if protocol == "responses" else "max_tokens"
        if protocol == "responses" and output_key in extras:
            requested = extras[output_key]
        if extra_body and output_key in extra_body:
            requested = extra_body[output_key]
        # A separate completion ceiling cannot safely be reconciled with an
        # OpenAI-compatible endpoint's max_tokens precedence here.
        if "max_completion_tokens" in extras or (extra_body and "max_completion_tokens" in extra_body):
            return None
        return requested if isinstance(requested, int) and not isinstance(requested, bool) and requested > 0 else None
    except Exception:
        return None


def _render(batch: ProposalBatch, raw_limit: int) -> str:
    return render_reference_proposals(((source.index, source.text) for source in batch.sources), raw_limit)


def _proposal_messages(
    messages: list[BaseMessage], batch: ProposalBatch, context: Any, tools: Any, output_tokens: int | None
) -> tuple[list[BaseMessage], str, int]:
    """Build a complete budgeted request copy or return a skip reason."""
    if output_tokens is None:
        return messages, "unknown_output_tokens", 0
    window = getattr(context, "context_window_tokens", None)
    counter_getter = getattr(context, "token_counter", None)
    try:
        limit = window() if callable(window) else None
        counter = counter_getter() if callable(counter_getter) else None
        if not isinstance(limit, int) or limit <= 0 or counter is None:
            return messages, "unknown_context_budget", 0
        tool_tokens = counter.count_tools(tools) if tools else 0
        if not isinstance(tool_tokens, int) or tool_tokens < 0:
            return messages, "unknown_tool_tokens", 0

        def fits(raw_length: int) -> bool:
            candidate = [
                *messages,
                UserMessage(content=_render(batch, raw_length), metadata={"transient": True}),
            ]
            used = counter.count_messages(candidate)
            return isinstance(used, int) and used >= 0 and used + tool_tokens + output_tokens + 128 <= limit

        total = sum(len(source.text) for source in batch.sources)
        if not total or not fits(1):
            return messages, "insufficient_context", 0
        low, high = 1, total
        while low < high:
            middle = (low + high + 1) // 2
            if fits(middle):
                low = middle
            else:
                high = middle - 1
        reason = "injected" if low == total else "trimmed"
        return [*messages, UserMessage(content=_render(batch, low), metadata={"transient": True})], reason, low
    except Exception:
        return messages, "unknown_context_budget", 0


class ProposalModel:
    """Transparent Model proxy that appends private proposals to the final request."""

    def __init__(self, model: Any) -> None:
        self._model = model

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)

    def _request(self, messages: list[BaseMessage], kwargs: dict[str, Any]) -> list[BaseMessage]:
        state = active_moa.get()
        if state is None or state.closed:
            return messages
        if state.pending is None or state.active_key is None:
            return messages
        batch = state.pending
        if batch.target != state.active_key:
            return messages
        request, reason, kept_chars = _proposal_messages(
            messages, batch, state.context, kwargs.get("tools"), _output_tokens(self._model, kwargs)
        )
        batch.attempts.append(ProposalAttempt(state.active_key, request is not messages, reason, kept_chars))
        agent_logger.debug(
            "MoA proposal request: turn=%s sources=%s reason=%s kept_chars=%s",
            state.active_key[1],
            len(batch.sources),
            reason,
            kept_chars,
        )
        return request

    async def invoke(self, messages: list[BaseMessage], **kwargs: Any) -> Any:
        """Send proposals through a copied request without changing history."""
        return await self._model.invoke(messages=self._request(messages, kwargs), **kwargs)

    async def stream(self, messages: list[BaseMessage], **kwargs: Any) -> AsyncIterator[Any]:
        """Stream from a copied request; failed or cancelled attempts retain eligibility."""
        async for chunk in self._model.stream(messages=self._request(messages, kwargs), **kwargs):
            yield chunk
