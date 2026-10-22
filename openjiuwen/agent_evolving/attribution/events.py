# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM call events shared by the single-call stages (task parse / keyword Jev)."""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_evolving.attribution.model import LlmRequestEvent, StatusSink
from openjiuwen.core.common.logging import logger


def emit_single_llm_event(
    sink: StatusSink | None,
    *,
    stage: str,
    model: str,
    counter: list[int],
    started_at_ns: int,
    ended_at_ns: int,
    preview: str | None = None,
    tool_calls: list[str] | None = None,
    token_usage: dict[str, Any] | None = None,
    retry_index: int = 0,
    error: str | None = None,
) -> None:
    """Report one single-call LLM event (task parse / keyword Jev)."""
    if sink is None:
        return
    counter[0] += 1
    try:
        sink.report(
            LlmRequestEvent(
                request_id=f"llm-{counter[0]}",
                stage=stage,
                call_kind="single_llm",
                status="failed" if error else "succeeded",
                started_at_ns=started_at_ns,
                ended_at_ns=ended_at_ns,
                model=model,
                response_preview=preview[:100] if preview else None,
                tool_calls=list(tool_calls or ()),
                token_usage=dict(token_usage or {}),
                retry_index=retry_index,
                error=error[:200] if error else None,
            )
        )
    except Exception:  # noqa: BLE001 - status loss must not break the run
        logger.debug("[%s] status sink raised; ignored", stage)
