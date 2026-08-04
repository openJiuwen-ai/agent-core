from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.symphony import LLMClient, OpenJiuwenLLMClient
from openjiuwen.symphony.interfaces import LLMClient as InterfaceLLMClient
from openjiuwen.symphony.interfaces import llm_usage_context


class _FakeModel:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls = []

    async def invoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def test_openjiuwen_llm_contract_is_exported_from_public_api() -> None:
    assert LLMClient is InterfaceLLMClient


@pytest.mark.asyncio
async def test_openjiuwen_llm_client_adapts_model_and_repairs_json() -> None:
    model = _FakeModel(SimpleNamespace(content='```json\n{"accepted": true,}\n```'))
    client = OpenJiuwenLLMClient(model)

    result = await client.complete_json_async(
        system_prompt="system",
        user_content="payload",
        timeout=12,
        request_overrides={"extra_body": {"thinking": {"type": "disabled"}}},
    )

    assert result == '{"accepted": true}'
    assert model.calls == [
        (
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "payload"},
            ],
            {
                "extra_body": {"thinking": {"type": "disabled"}},
                "timeout": 12,
            },
        )
    ]


@pytest.mark.asyncio
async def test_openjiuwen_llm_client_reports_context_to_async_observer() -> None:
    response = SimpleNamespace(content=[{"text": '{"ok":'}, {"content": "true}"}])
    observed = []

    async def observer(item, stage, operation):
        observed.append((item, stage, operation))

    client = OpenJiuwenLLMClient(
        _FakeModel(response),
        response_observer=observer,
    )

    with llm_usage_context("orchestration", "beam_rerank"):
        result = await client.complete_json_async(
            system_prompt="system",
            user_content="payload",
        )

    assert result == '{"ok": true}'
    assert observed == [(response, "orchestration", "beam_rerank")]


@pytest.mark.asyncio
async def test_openjiuwen_llm_client_observer_failure_does_not_break_completion() -> None:
    def observer(*_args):
        raise RuntimeError("usage sink unavailable")

    client = OpenJiuwenLLMClient(
        _FakeModel(SimpleNamespace(content='{"ok": true}')),
        response_observer=observer,
    )

    assert await client.complete_json_async(system_prompt="system", user_content="payload") == '{"ok": true}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "message"),
    [
        (_FakeModel(error=TimeoutError("slow")), "graph matching request failed: slow"),
        (_FakeModel(SimpleNamespace(content="")), "graph matching request failed: response content is empty"),
    ],
)
async def test_openjiuwen_llm_client_wraps_failures(model: _FakeModel, message: str) -> None:
    client = OpenJiuwenLLMClient(model)

    with pytest.raises(RuntimeError, match=message):
        await client.complete_json_async(
            system_prompt="system",
            user_content="payload",
            error_context="graph matching",
        )
