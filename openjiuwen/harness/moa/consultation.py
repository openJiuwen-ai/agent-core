# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Read-only reference fanout and shared consultation budget accounting."""

from __future__ import annotations

import asyncio
from typing import Any

from openjiuwen.core.foundation.llm.schema.message import BaseMessage, SystemMessage, UserMessage
from openjiuwen.harness.moa.config import MoAConfig
from openjiuwen.harness.moa.state import MoAState, ProposalBatch, ProposalSource, TurnKey
from openjiuwen.harness.prompts.moa import REFERENCE_SYSTEM_PROMPT


def _consultation_tool_ids(messages: list[BaseMessage]) -> set[str | None]:
    """Collect consultation call IDs so their results stay out of reference input."""
    omitted_tool_ids: set[str | None] = set()
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            if call.name == "consult_reference_models":
                omitted_tool_ids.add(call.id)
    return omitted_tool_ids


async def consult(config: MoAConfig, messages: list[BaseMessage]) -> tuple[tuple[ProposalSource, ...], int]:
    """Ask references concurrently; preserve configured order and fail open."""
    omitted_tool_ids = _consultation_tool_ids(messages)
    transcript = []
    for message in messages:
        role = getattr(message, "role", "")
        if role == "tool" and (
            getattr(message, "name", None) == "consult_reference_models"
            or getattr(message, "tool_call_id", None) in omitted_tool_ids
        ):
            continue
        if role not in ("user", "assistant", "tool"):
            continue
        content = message.content
        text = (
            content
            if isinstance(content, str)
            else " ".join(
                part if isinstance(part, str) else str(part.get("text", ""))
                for part in content
                if isinstance(part, (str, dict))
            )
        )
        calls = [
            call for call in (getattr(message, "tool_calls", None) or []) if call.name != "consult_reference_models"
        ]
        if calls:
            text += " Actions: " + ", ".join(f"{call.name}({call.arguments[:500]})" for call in calls)
        if text:
            transcript.append(f"{role}: {text[:4_000]}")
    request = [
        SystemMessage(content=REFERENCE_SYSTEM_PROMPT),
        UserMessage(content="\n".join(transcript)[-16_000:]),
    ]

    async def ask(model: Any) -> str:
        response = await asyncio.wait_for(
            model.invoke(messages=request, tools=None),
            timeout=config.reference_timeout,
        )
        return str(response.content or "")[: config.max_reference_chars]

    results = await asyncio.gather(
        *(ask(model) for model in config.reference_models),
        return_exceptions=True,
    )
    sources: list[ProposalSource] = []
    remaining = config.max_advice_chars
    success_count = 0
    for index, result in enumerate(results, 1):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException) or not result:
            continue
        success_count += 1
        if remaining > 0:
            text = result[:remaining]
            sources.append(ProposalSource(index=index, text=text))
            remaining -= len(text)
    return tuple(sources), success_count


async def prepare_reference(
    config: MoAConfig, state: MoAState, messages: list[BaseMessage], target: TurnKey | None
) -> int | None:
    """Spend one consultation batch and bind available proposals to their target.

    None means the batch budget is exhausted. A numeric result is the number
    of successful reference models, including zero when all references fail.
    """
    limit = config.jev.max_reference_calls if config.jev is not None else config.verifier.max_reference_calls
    if limit is not None and state.reference_calls >= limit:
        return None
    state.reference_calls += 1
    sources, success_count = await consult(config, messages)
    if sources and target is not None:
        state.pending = ProposalBatch(sources=sources, target=target)
    return success_count
