# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Aggregate subagent stream chunks into a final turn output."""

from __future__ import annotations

from typing import Any


class TurnOutputAggregator:
    """Aggregate a subagent turn's final output from stream chunks."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self._answer: str | None = None
        self._result_type: str | None = None

    def consume(self, chunk: Any) -> None:
        chunk_type = getattr(chunk, "type", None)
        payload = getattr(chunk, "payload", None)
        if isinstance(chunk, dict):
            chunk_type = chunk.get("type", chunk_type)
            payload = chunk.get("payload", payload)
        if not isinstance(payload, dict):
            return

        if chunk_type == "llm_output":
            content = payload.get("content")
            if isinstance(content, str):
                self._parts.append(content)
            return

        if chunk_type == "llm_reasoning":
            content = payload.get("content")
            if isinstance(content, str):
                self._reasoning_parts.append(content)
            return

        if chunk_type == "answer":
            self._result_type = payload.get("result_type") or self._result_type
            output = payload.get("output")
            if isinstance(output, str):
                self._answer = output
                return
            content = payload.get("content")
            if isinstance(content, str):
                self._parts.append(content)

    def output(self) -> str:
        if self._answer is not None:
            return self._answer
        return "".join(self._parts)

    def reasoning_text(self) -> str:
        return "".join(self._reasoning_parts)

    def is_error(self) -> bool:
        return self._result_type == "error"


__all__ = ["TurnOutputAggregator"]
