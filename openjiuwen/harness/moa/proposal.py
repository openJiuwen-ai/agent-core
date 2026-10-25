# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Budgeted, transient proposals on copies of the final model request."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from openjiuwen.core.common.logging import agent_logger
from openjiuwen.core.context_engine.context_engine import ContextEngine
from openjiuwen.core.foundation.llm.reasoning import (
    is_reasoning_config_intent,
    resolve_reasoning_plan,
    resolve_reasoning_protocol,
)
from openjiuwen.core.foundation.llm.schema.message import BaseMessage, UserMessage
from openjiuwen.core.foundation.llm.utils.endpoint_profiles import normalize_model_client_config
from openjiuwen.harness.moa.state import ProposalAttempt, ProposalBatch, active_moa
from openjiuwen.harness.prompts.moa import render_reference_proposals


@dataclass(frozen=True)
class _OutputBudget:
    mode: Literal["known", "unspecified", "unresolved"]
    tokens: int | None = None
    reason: str = ""


def _valid_tokens(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _request_extras(model_config: Any) -> dict[str, Any]:
    dump = getattr(model_config, "model_dump", None)
    if not callable(dump):
        raise ValueError("model request configuration cannot be read")
    extras = dump(
        exclude={"model_name", "model", "temperature", "top_p", "max_tokens", "stop", "context_window", "reasoning"},
        exclude_none=True,
    )
    if not isinstance(extras, Mapping):
        raise ValueError("model request configuration is not a mapping")
    return dict(extras)


def _anthropic_output_tokens(
    client_config: Any, model_config: Any, extras: dict[str, Any], kwargs: dict[str, Any], requested: Any,
) -> int | None:
    """Mirror the adapter's ceiling after reasoning resolution and overrides."""
    params = {**extras, **kwargs}
    plan = resolve_reasoning_plan(
        client_config,
        model_config,
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


def _output_budget(model: Any, kwargs: dict[str, Any]) -> _OutputBudget:
    """Distinguish a missing wire ceiling from an unresolvable request."""
    try:
        client_config = model.model_client_config
        model_config = model.model_config
        if client_config is None or model_config is None:
            return _OutputBudget("unresolved", reason="missing_model_config")
        api_mode = getattr(client_config, "api_mode", None)
        api_mode = getattr(api_mode, "value", api_mode)
        if api_mode not in (None, "chat_completions", "responses", "anthropic_messages"):
            return _OutputBudget("unresolved", reason="unsupported_protocol")
        normalized_client = normalize_model_client_config(client_config)
        provider = normalized_client.client_provider
        provider = getattr(provider, "value", provider)
        if api_mode != "anthropic_messages" and provider not in {"OpenAI", "OpenAIAccount", "Anthropic"}:
            return _OutputBudget("unresolved", reason="unsupported_adapter")
        protocol = resolve_reasoning_protocol(normalized_client)
        requested = kwargs.get("max_tokens")
        if requested is None:
            requested = model_config.max_tokens
        if requested is not None and not _valid_tokens(requested):
            return _OutputBudget("unresolved", reason="invalid_output_tokens")

        extras = _request_extras(model_config)
        configured_body = extras.get("extra_body")
        body = kwargs.get("extra_body", configured_body)
        if body is not None and not isinstance(body, Mapping):
            return _OutputBudget("unresolved", reason="invalid_extra_body")

        if protocol == "anthropic_messages":
            effective = {**extras, **kwargs}
            if any(key in effective or key in (body or {}) for key in ("max_completion_tokens", "max_output_tokens")):
                return _OutputBudget("unresolved", reason="unsupported_output_alias")
            tokens = _anthropic_output_tokens(client_config, model_config, extras, kwargs, requested)
            return _OutputBudget("known", tokens) if tokens is not None else _OutputBudget(
                "unresolved", reason="invalid_output_tokens"
            )

        if protocol == "responses":
            # Responses merges call extra-body fields into request extras, then
            # expands the retained configured nested body. Unlike Chat, an empty
            # call body does not remove the configured nested output ceiling.
            if configured_body is not None and not isinstance(configured_body, Mapping):
                return _OutputBudget("unresolved", reason="invalid_extra_body")
            effective = {
                **extras,
                **{key: value for key, value in kwargs.items() if key not in {"max_tokens", "extra_body"}},
            }
            if "extra_body" in kwargs and kwargs["extra_body"] is not None:
                effective.update(kwargs["extra_body"])
            nested = effective.pop("extra_body", None)
            if nested is not None and not isinstance(nested, Mapping):
                return _OutputBudget("unresolved", reason="invalid_extra_body")
            effective = {**(nested or {}), **effective}
            if "max_completion_tokens" in effective or "max_tokens" in effective:
                return _OutputBudget("unresolved", reason="unsupported_output_alias")
            if "max_output_tokens" in effective:
                tokens = effective["max_output_tokens"]
                return _OutputBudget("known", tokens) if _valid_tokens(tokens) else _OutputBudget(
                    "unresolved", reason="invalid_output_tokens"
                )
            if bool(kwargs.get("send_max_output_tokens", False)) and requested is not None:
                return _OutputBudget("known", requested)
            return _OutputBudget("unspecified")

        extras.update(kwargs)
        if any(key in extras or key in (body or {}) for key in ("max_completion_tokens", "max_output_tokens")):
            return _OutputBudget("unresolved", reason="unsupported_output_alias")
        if body is not None and "max_tokens" in body:
            requested = body["max_tokens"]
            if not _valid_tokens(requested):
                return _OutputBudget("unresolved", reason="invalid_output_tokens")
        return _OutputBudget("known", requested) if requested is not None else _OutputBudget("unspecified")
    except Exception:
        return _OutputBudget("unresolved", reason="unreadable_output_config")


def _output_tokens(model: Any, kwargs: dict[str, Any]) -> int | None:
    """Retain the existing integer-or-None helper for resolved ceilings."""
    return _output_budget(model, kwargs).tokens


def _render(batch: ProposalBatch, raw_limit: int) -> str:
    return render_reference_proposals(((source.index, source.text) for source in batch.sources), raw_limit)


def _proposal_messages(
    messages: list[BaseMessage], batch: ProposalBatch, context: Any, tools: Any, budget: _OutputBudget
) -> tuple[list[BaseMessage], str, int]:
    """Build a complete budgeted request copy or return a skip reason."""
    if budget.mode == "unresolved":
        return messages, budget.reason or "unknown_output_tokens", 0
    window = getattr(context, "context_window_tokens", None)
    counter_getter = getattr(context, "token_counter", None)
    try:
        limit = window() if callable(window) else None
        counter = counter_getter() if callable(counter_getter) else None
        if not _valid_tokens(limit) or counter is None:
            return messages, "unknown_context_budget", 0
        tool_tokens = counter.count_tools(tools) if tools else 0
        if not isinstance(tool_tokens, int) or isinstance(tool_tokens, bool) or tool_tokens < 0:
            return messages, "unknown_tool_tokens", 0

        def fits(raw_length: int) -> bool:
            candidate = [
                *messages,
                UserMessage(content=_render(batch, raw_length), metadata={"transient": True}),
            ]
            used = counter.count_messages(candidate)
            if not isinstance(used, int) or isinstance(used, bool) or used < 0:
                return False
            if budget.mode == "known":
                return used + tool_tokens + budget.tokens + 128 <= limit
            return used + tool_tokens + 128 <= limit

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


@dataclass(frozen=True)
class _ProposalRequest:
    messages: list[BaseMessage]
    batch: ProposalBatch | None = None
    budget_mode: Literal["known", "unspecified", "unresolved"] = "unresolved"
    injected: bool = False


def _is_input_context_overflow(exception: Exception) -> bool:
    """Narrow the existing context matcher before replaying an unsent request."""
    if not ContextEngine.is_context_overflow_error(exception):
        return False
    pending: list[Any] = [exception]
    seen: set[int] = set()
    messages: list[str] = []
    codes: list[str] = []
    while pending and len(seen) < 24:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, BaseException):
            kind = type(value).__name__.lower()
            if isinstance(value, (TimeoutError, ConnectionError)) or any(
                word in kind for word in ("timeout", "authentication", "permission", "ratelimit")
            ):
                return False
            messages.append(str(value))
            pending.extend(
                item for item in (
                    getattr(value, "cause", None), value.__cause__, value.__context__,
                ) if item is not None
            )
        if isinstance(value, Mapping):
            status = value.get("status_code", value.get("status"))
            code = value.get("code")
            parameter = value.get("param")
            message = value.get("message")
            pending.extend(item for item in (value.get("error"), value.get("body")) if item is not None)
        else:
            status = getattr(value, "status_code", None)
            code = getattr(value, "code", None)
            parameter = getattr(value, "param", None)
            message = getattr(value, "message", None)
            pending.extend(
                item for item in (
                    getattr(value, "body", None), getattr(value, "error", None), getattr(value, "response", None),
                ) if item is not None
            )
        if isinstance(status, int) and status not in (400, 413, 422):
            return False
        if parameter in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
            return False
        if isinstance(code, str):
            normalized = code.casefold()
            if normalized in {"invalid_api_key", "rate_limit_exceeded", "insufficient_quota"}:
                return False
            codes.append(normalized)
        if isinstance(message, str):
            messages.append(message)
    if pending:
        return False
    if "context_length_exceeded" in codes:
        return True
    for message in messages:
        text = message.casefold().replace("_", " ").replace("-", " ")
        if any(key in text for key in ("max tokens", "max completion tokens", "max output tokens")):
            continue
        if any(re.search(pattern, text) for pattern in (
            r"\b(?:prompt|input)(?: tokens?(?: count| length)?| length)? (?:is |was )?too long\b",
            r"\b(?:prompt|input)(?: tokens?(?: count| length)?| length)? "
            r"(?:exceeds?|exceeded|is over) (?:the )?(?:maximum |model's )?"
            r"(?:context (?:length|window)|(?:input |prompt )?(?:token )?limit)\b",
            r"\bcontext (?:length|window) (?:is |was |has been )?(?:exceeded|over (?:the )?limit)\b",
        )):
            return True
    return False


class ProposalModel:
    """Transparent Model proxy that appends private proposals to the final request."""

    def __init__(self, model: Any) -> None:
        self._model = model

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)

    def _prepare_request(self, messages: list[BaseMessage], kwargs: dict[str, Any]) -> _ProposalRequest:
        state = active_moa.get()
        if state is None or state.closed:
            return _ProposalRequest(messages)
        if state.pending is None or state.active_key is None:
            return _ProposalRequest(messages)
        batch = state.pending
        if batch.target != state.active_key:
            return _ProposalRequest(messages)
        budget = _output_budget(self._model, kwargs)
        if batch.disabled_reason is not None:
            request, reason, kept_chars = messages, "batch_disabled", 0
        else:
            request, reason, kept_chars = _proposal_messages(
                messages, batch, state.context, kwargs.get("tools"), budget
            )
        injected = request is not messages
        batch.attempts.append(ProposalAttempt(
            state.active_key, injected, reason, kept_chars, budget_mode=budget.mode,
        ))
        agent_logger.info(
            "MoA proposal request: invoke=%s batch=%s turn=%s sources=%s budget_mode=%s "
            "injected=%s reason=%s kept_chars=%s fallback=False disabled=%s",
            state.invoke_id, batch.batch_id, state.active_key[1], len(batch.sources),
            budget.mode, injected, reason, kept_chars, batch.disabled_reason is not None,
        )
        return _ProposalRequest(request, batch, budget.mode, injected)

    def _request(self, messages: list[BaseMessage], kwargs: dict[str, Any]) -> list[BaseMessage]:
        return self._prepare_request(messages, kwargs).messages

    @staticmethod
    def _disable_for_fallback(request: _ProposalRequest, exception: Exception) -> bool:
        batch = request.batch
        state = active_moa.get()
        if (
            request.budget_mode != "unspecified" or not request.injected or batch is None
            or batch.disabled_reason is not None or state is None or state.closed
        ):
            return False
        try:
            if not _is_input_context_overflow(exception):
                return False
        except Exception:
            return False
        batch.disabled_reason = "context_overflow"
        batch.attempts.append(ProposalAttempt(
            batch.target, False, "context_overflow_fallback", budget_mode="unspecified", fallback=True,
        ))
        agent_logger.info(
            "MoA proposal request: invoke=%s batch=%s turn=%s sources=%s budget_mode=unspecified "
            "injected=False reason=context_overflow_fallback kept_chars=0 fallback=True disabled=True",
            state.invoke_id, batch.batch_id, batch.target[1], len(batch.sources),
        )
        return True

    async def invoke(self, messages: list[BaseMessage], **kwargs: Any) -> Any:
        """Send proposals through a copied request without changing history."""
        request = self._prepare_request(messages, kwargs)
        try:
            return await self._model.invoke(messages=request.messages, **kwargs)
        except Exception as exc:
            if not self._disable_for_fallback(request, exc):
                raise
        return await self._model.invoke(messages=messages, **kwargs)

    async def stream(self, messages: list[BaseMessage], **kwargs: Any) -> AsyncIterator[Any]:
        """Stream a copied request without replaying any emitted chunk."""
        request = self._prepare_request(messages, kwargs)
        emitted = False
        try:
            async for chunk in self._model.stream(messages=request.messages, **kwargs):
                emitted = True
                yield chunk
            return
        except Exception as exc:
            if emitted or not self._disable_for_fallback(request, exc):
                raise
        async for chunk in self._model.stream(messages=messages, **kwargs):
            yield chunk
