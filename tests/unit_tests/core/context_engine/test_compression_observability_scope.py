# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest
from pydantic import BaseModel

from openjiuwen.core.context_engine.context.compression_scope import (
    context_compression_operation,
    current_context_compression_operation_id,
)
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.processor.base import ContextEvent, ContextProcessor
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.foundation.llm.model import Model


class _CapturingClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs


class _ProcessorConfig(BaseModel):
    model: str = "compression-model"


class _ModelCallingProcessor(ContextProcessor):
    def __init__(self, model: Model) -> None:
        super().__init__(_ProcessorConfig())
        self._model = model

    async def trigger_get_context_window(self, context, context_window, **kwargs):
        return True

    async def on_get_context_window(self, context, context_window, **kwargs):
        await self._model.invoke("compress inside real processor lifecycle")
        return ContextEvent(event_type=self.processor_type()), context_window

    def load_state(self, state):
        return None

    def save_state(self):
        return {}


@pytest.mark.asyncio
async def test_model_calls_inside_compression_scope_are_correlated() -> None:
    model = object.__new__(Model)
    model._client = _CapturingClient()

    with context_compression_operation("operation-1"):
        result = await model.invoke(
            "compress this context",
            request_purpose="assistant",
            context_operation_id="wrong-operation",
        )

    assert result["request_purpose"] == "compaction"
    assert result["context_operation_id"] == "operation-1"
    assert current_context_compression_operation_id() == ""


@pytest.mark.asyncio
async def test_ordinary_model_calls_are_not_misclassified_as_compaction() -> None:
    model = object.__new__(Model)
    model._client = _CapturingClient()

    result = await model.invoke("ordinary request")

    assert "request_purpose" not in result
    assert "context_operation_id" not in result


@pytest.mark.asyncio
async def test_real_context_processor_lifecycle_binds_operation_to_nested_model_call() -> None:
    model = object.__new__(Model)
    client = _CapturingClient()
    model._client = client
    context = SessionModelContext(
        "context-1",
        "session-1",
        ContextEngineConfig(),
        history_messages=[],
        processors=[_ModelCallingProcessor(model)],
    )

    await context.get_context_window()

    assert len(client.calls) == 1
    assert client.calls[0]["request_purpose"] == "compaction"
    assert len(client.calls[0]["context_operation_id"]) == 32
    assert current_context_compression_operation_id() == ""
