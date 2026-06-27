# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig, ProviderType
from openjiuwen.core.foundation.llm.schema.message import UserMessage


def _make_client() -> OpenAIModelClient:
    return OpenAIModelClient(
        ModelRequestConfig(model="gpt-4o", temperature=0.2),
        ModelClientConfig(
            client_provider=ProviderType.OpenAI,
            api_key="sk-secret-key",
            api_base="https://api.openai.com/v1",
            verify_ssl=False,
            custom_headers={"Authorization": "Bearer hidden-token", "X-Trace": "visible"},
        ),
    )


def _build_tool_response() -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = MagicMock()
    response.choices[0].message.content = ""
    response.choices[0].message.reasoning_content = None

    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.index = 0
    tool_call.function = MagicMock()
    tool_call.function.name = "lookup"
    tool_call.function.arguments = '{"query": "weather"}'
    response.choices[0].message.tool_calls = [tool_call]

    response.usage = MagicMock()
    response.usage.prompt_tokens = 11
    response.usage.completion_tokens = 7
    response.usage.total_tokens = 18
    response.usage.prompt_tokens_details = None
    return response


def _read_single_trace(trace_dir: Path) -> dict:
    files = sorted(trace_dir.glob("*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


def _single_trace_file(trace_dir: Path) -> Path:
    files = sorted(trace_dir.glob("*.json"))
    assert len(files) == 1
    return files[0]


@pytest.mark.asyncio
async def test_openai_invoke_writes_request_and_response_trace(monkeypatch, tmp_path: Path):
    trace_dir = tmp_path / "llm-trace"
    monkeypatch.setenv("OPENJIUWEN_LLM_TRACE_DIR", str(trace_dir))

    client = _make_client()
    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = AsyncMock(return_value=_build_tool_response())

    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Lookup data",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            },
        }
    ]

    with patch.object(client, "_create_async_openai_client", return_value=mock_async_client):
        await client.invoke(
            [UserMessage(content="use the lookup tool")],
            tools=tools,
            custom_headers={"Authorization": "Bearer request-token"},
        )

    trace = _read_single_trace(trace_dir)
    assert trace["provider"] == "OpenAI"
    assert trace["api_base"] == "https://api.openai.com/v1"
    assert trace["stream"] is False
    assert trace["request"]["model"] == "gpt-4o"
    assert trace["request"]["messages"][0]["content"] == "use the lookup tool"
    assert trace["request"]["tools"] == tools
    assert "Authorization" not in trace["request"]["extra_headers"]
    assert trace["request"]["extra_headers"]["X-Trace"] == "visible"
    assert trace["api_key"] == "***REDACTED***"
    assert trace["response"]["tool_calls"][0]["name"] == "lookup"
    assert trace["response"]["tool_calls"][0]["arguments"] == '{"query": "weather"}'
    assert trace["response"]["usage"]["total_tokens"] == 18


@pytest.mark.asyncio
async def test_openai_trace_filename_starts_with_sortable_timestamp(monkeypatch, tmp_path: Path):
    trace_dir = tmp_path / "llm-trace"
    monkeypatch.setenv("OPENJIUWEN_LLM_TRACE_DIR", str(trace_dir))

    client = _make_client()
    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = AsyncMock(return_value=_build_tool_response())

    with patch.object(client, "_create_async_openai_client", return_value=mock_async_client):
        await client.invoke([UserMessage(content="hello")])

    trace_file = _single_trace_file(trace_dir)
    assert re.match(r"^\d{8}T\d{6}\d{6}Z_[0-9a-f]{32}\.json$", trace_file.name)


@pytest.mark.asyncio
async def test_openai_stream_trace_marks_stream_and_records_final_message(monkeypatch, tmp_path: Path):
    trace_dir = tmp_path / "llm-trace"
    monkeypatch.setenv("OPENJIUWEN_LLM_TRACE_DIR", str(trace_dir))

    client = _make_client()

    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta = MagicMock()
    chunk.choices[0].delta.content = "hello"
    chunk.choices[0].delta.reasoning_content = None
    chunk.choices[0].delta.tool_calls = None
    chunk.choices[0].finish_reason = "stop"
    chunk.usage = None

    async def chunk_generator():
        yield chunk

    mock_async_client = AsyncMock()
    mock_async_client.chat.completions.create = AsyncMock(return_value=chunk_generator())

    with patch.object(client, "_create_async_openai_client", return_value=mock_async_client):
        async for _ in client.stream([UserMessage(content="hello")]):
            pass

    trace = _read_single_trace(trace_dir)
    assert trace["stream"] is True
    assert trace["request"]["stream"] is True
    assert trace["request"]["stream_options"]["include_usage"] is True
    assert trace["response"]["content"] == "hello"
