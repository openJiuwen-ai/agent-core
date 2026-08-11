# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rail owning LLM-call stability hardening.

This is the single responsible rail for stabilizing LLM calls. Its current
scope covers tool-call related instability (the historic source of most
breakage), and it is the designated home for future LLM-output stability
concerns (repeated output, malformed content, unexpected ``finish_reason``,
etc.).

It centralizes the old scatter of "skip illegal/truncated tool arguments" and
"fix tool-message pairing" logic (previously split across jiuwenswarm's
``jiuwen_core_patch.py`` and ``stream_event_rail.py``).

Current responsibilities (tool-call stability):
  * Detect and skip tool calls whose ``arguments`` are truncated or illegal
    JSON (or whose output was cut off by ``finish_reason == "length"``),
    feeding a guidance ToolMessage back to the model instead of executing
    malformed arguments.
  * Keep the message stream pair-consistent: every ``assistant(tool_calls)``
    must be followed by matching ``ToolMessage``; orphans get a placeholder.

Mounted once in ``create_deep_agent``'s default rails, so it covers
deep_agent / team members / subagents / CLI agents through a single chokepoint.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.message import (
    AssistantMessage,
    BaseMessage,
    ToolMessage,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

# --- ctx.extra keys (cross-rail / cross-hook communication) ---
# {tool_call_id: reason} where reason in {"truncated", "invalid"}
SKIP_KEY = "llm_stability_skip"
# True when the whole model output was cut off by finish_reason=="length"
FORCE_SKIP_ALL_KEY = "llm_stability_force_skip_all"
# consecutive truncation-retry counter (resets to 0 when a clean response arrives)
RETRIES_KEY = "llm_stability_retries"
# budget / content knobs surfaced to the execution path (core layer)
MAX_RETRIES_KEY = "llm_stability_max_retries"
GUIDANCE_KEY = "llm_stability_guidance"
FALLBACK_KEY = "llm_stability_fallback"

# classifier results
EXECUTE = "execute"
SKIP_TRUNCATED = "truncated"
SKIP_INVALID = "invalid"

DEFAULT_MAX_RETRIES = 2
DEFAULT_GUIDANCE = (
    "The previous tool call was truncated because the generated output hit the "
    "output limit. Please re-emit the tool call: include only the necessary "
    "arguments and no extra explanation; if the arguments are still too large, "
    "split them into multiple smaller tool calls."
)
DEFAULT_FALLBACK = (
    "The previous tool call was dropped after repeated truncation. "
    "Proceed by choosing a simpler action, splitting the task into smaller "
    "steps, or giving a direct text answer."
)


def _looks_truncated(s: str) -> bool:
    """Return True when ``s`` looks like an unterminated JSON fragment.

    Heuristic: an unclosed string or an unbalanced structure (`{ [`) with more
    open than close delimiters indicates the output was cut off mid-value.
    """
    depth = 0
    in_string = False
    escape = False
    for ch in s:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    if in_string:
        return True
    return depth > 0


def classify_tool_call(
    arguments: Any,
    *,
    finish_reason: Optional[str] = None,
) -> str:
    """Classify a tool call's arguments for execution/skip.

    Returns one of ``EXECUTE`` / ``SKIP_TRUNCATED`` / ``SKIP_INVALID``.

    ``finish_reason == "length"`` is treated as a strong truncation signal: the
    model output hit the output limit, so the emitted tool calls are likely cut
    off mid-way.
    """
    if finish_reason == "length":
        return SKIP_TRUNCATED

    if isinstance(arguments, dict):
        return EXECUTE

    text = str(arguments or "").strip()
    if not text:
        return SKIP_INVALID

    try:
        parsed = json.loads(text)
        return EXECUTE if isinstance(parsed, dict) else SKIP_INVALID
    except (json.JSONDecodeError, TypeError):
        return SKIP_TRUNCATED if _looks_truncated(text) else SKIP_INVALID


def ensure_json_arguments(arguments: Any) -> str:
    """Return a valid JSON-object string for ``arguments``.

    Legal dicts pass through; illegal/truncated JSON is replaced by ``"{}"`` so
    downstream execution never sees a broken payload.
    """
    if isinstance(arguments, dict):
        return json.dumps(arguments)
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            if isinstance(parsed, dict):
                return arguments
        except (json.JSONDecodeError, TypeError):
            pass
        logger.info("LLMStability: illegal tool arguments replaced: %r", arguments)
    return "{}"


def _flush_pending(
    tool_id_cache: List[Dict[str, Any]],
    tool_message_cache: Dict[str, ToolMessage],
) -> List[ToolMessage]:
    flushed: List[ToolMessage] = []
    for tc in tool_id_cache:
        tc_id = tc["tool_call_id"]
        cached = tool_message_cache.pop(tc_id, None)
        if cached is not None:
            flushed.append(cached)
        else:
            flushed.append(
                ToolMessage(
                    content=(
                        "[Tool execution interrupted] Tool "
                        f"{tc['tool_name']} was interrupted, no result available."
                    ),
                    tool_call_id=tc_id,
                )
            )
    # any remaining cached messages with no pending tool id are orphans → drop
    tool_message_cache.clear()
    tool_id_cache.clear()
    return flushed


def sanitize_tool_pairing(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Return a pair-consistent copy of ``messages``.

    Every ``assistant(tool_calls)`` is followed by matching ``ToolMessage``;
    orphan tool calls get a placeholder ``ToolMessage``; unmatched
    ``ToolMessage`` records are dropped. Broken arguments are normalized to
    ``"{}"`` via ``ensure_json_arguments``.
    """
    if not messages:
        return messages

    result: List[BaseMessage] = []
    tool_id_cache: List[Dict[str, Any]] = []
    tool_message_cache: Dict[str, ToolMessage] = {}

    def _enqueue_tool_calls(msg: AssistantMessage) -> None:
        for tc in msg.tool_calls or []:
            arguments = ensure_json_arguments(getattr(tc, "arguments", "{}"))
            if hasattr(tc, "arguments"):
                tc.arguments = arguments
            tool_id_cache.append(
                {
                    "tool_call_id": getattr(tc, "id", ""),
                    "tool_name": getattr(tc, "name", ""),
                }
            )

    for msg in messages:
        if isinstance(msg, AssistantMessage):
            if tool_id_cache:
                result.extend(_flush_pending(tool_id_cache, tool_message_cache))
            result.append(msg)
            _enqueue_tool_calls(msg)
        elif isinstance(msg, ToolMessage):
            if not tool_id_cache:
                # orphan tool result without a matching assistant tool_calls.
                continue
            if msg.tool_call_id == tool_id_cache[0]["tool_call_id"]:
                result.append(msg)
                tool_id_cache.pop(0)
            else:
                tool_message_cache[msg.tool_call_id] = msg
        else:
            if tool_id_cache:
                result.extend(_flush_pending(tool_id_cache, tool_message_cache))
            result.append(msg)

    if tool_id_cache:
        result.extend(_flush_pending(tool_id_cache, tool_message_cache))
    return result


def _has_orphan_tools(messages: List[BaseMessage]) -> bool:
    """Return True when the stream contains an unpaired tool block."""
    pending = 0
    for msg in messages:
        if isinstance(msg, AssistantMessage) and msg.tool_calls:
            pending += len(msg.tool_calls)
        elif isinstance(msg, ToolMessage):
            if pending:
                pending -= 1
            else:
                return True
    return pending > 0


def _has_broken_arguments(messages: List[BaseMessage]) -> bool:
    """Return True when any assistant tool call has non-executable arguments.

    Detects illegal/truncated JSON arguments even when tool-call pairing is
    complete (e.g. context restored from an interrupted session), so the
    repair path still normalizes them via ``ensure_json_arguments``. Without
    this check, ``_apply_pairing`` would early-return and leave broken
    arguments for the LLM API to reject.
    """
    for msg in messages:
        if isinstance(msg, AssistantMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if classify_tool_call(getattr(tc, "arguments", "{}")) != EXECUTE:
                    return True
    return False


class LLMStabilityRail(DeepAgentRail):
    """Own LLM-call stability: detect + repair truncated / illegal tool args.

    Currently the responsible rail for LLM-call stability, with tool-call
    hardening as its first scope. Future LLM-output stability logic should be
    added here rather than scattered.

    Hooks:
      * ``before_invoke``: one-time sweep of the whole incoming context.
      * ``before_model_call``: pair-consistency safety net before each call.
      * ``after_model_call``: detect truncation, mark tool calls for skip.
      * ``on_model_exception``: repair orphan pairing before a retry.
    """

    priority = 75

    def __init__(
        self,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        guidance: str = DEFAULT_GUIDANCE,
        fallback: str = DEFAULT_FALLBACK,
        force_skip_on_length: bool = True,
    ) -> None:
        super().__init__()
        self._max_retries = max(0, int(max_retries))
        self._guidance = guidance
        self._fallback = fallback
        self._force_skip_on_length = force_skip_on_length

    # ---- helpers ----

    async def _apply_pairing(self, ctx: AgentCallbackContext) -> None:
        context = ctx.context
        if context is None:
            return
        try:
            messages = context.get_messages()
            if not messages or not (
                _has_orphan_tools(messages) or _has_broken_arguments(messages)
            ):
                return
            popped = context.pop_messages(size=len(messages))
            if not popped:
                return
            repaired = sanitize_tool_pairing(popped)
            for msg in repaired:
                await context.add_messages(msg)
        except Exception as exc:  # noqa: BLE001 - never break the agent loop
            logger.warning("LLMStability: failed to repair pairing: %s", exc)

    def _read_model_response(self, ctx: AgentCallbackContext) -> Optional[AssistantMessage]:
        inputs = getattr(ctx, "inputs", None)
        response = getattr(inputs, "response", None)
        return response if isinstance(response, AssistantMessage) else None

    # ---- hooks ----

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        await self._apply_pairing(ctx)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        await self._apply_pairing(ctx)

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        await self._apply_pairing(ctx)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        response = self._read_model_response(ctx)
        skip_map: Dict[str, str] = {}
        force_skip_all = False
        if response is not None and response.tool_calls:
            finish_reason = None
            if self._force_skip_on_length and getattr(response, "finish_reason", None) == "length":
                finish_reason = "length"
            forced = finish_reason == "length"
            force_skip_all = forced
            for tc in response.tool_calls:
                tc_id = getattr(tc, "id", None)
                if forced:
                    if tc_id:
                        skip_map[tc_id] = SKIP_TRUNCATED
                    continue
                if not tc_id:
                    continue
                verdict = classify_tool_call(getattr(tc, "arguments", "{}"))
                if verdict != EXECUTE:
                    skip_map[tc_id] = verdict

        # update consecutive retry counter
        retries = ctx.extra.get(RETRIES_KEY, 0)
        if skip_map or force_skip_all:
            ctx.extra[RETRIES_KEY] = retries + 1
        else:
            ctx.extra[RETRIES_KEY] = 0

        ctx.extra[SKIP_KEY] = skip_map
        ctx.extra[FORCE_SKIP_ALL_KEY] = force_skip_all
        ctx.extra[MAX_RETRIES_KEY] = self._max_retries
        ctx.extra[GUIDANCE_KEY] = self._guidance
        ctx.extra[FALLBACK_KEY] = self._fallback


__all__ = [
    "LLMStabilityRail",
    "classify_tool_call",
    "ensure_json_arguments",
    "sanitize_tool_pairing",
    "SKIP_KEY",
    "FORCE_SKIP_ALL_KEY",
    "RETRIES_KEY",
    "MAX_RETRIES_KEY",
    "GUIDANCE_KEY",
    "FALLBACK_KEY",
    "EXECUTE",
    "SKIP_TRUNCATED",
    "SKIP_INVALID",
]
