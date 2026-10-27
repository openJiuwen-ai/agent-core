"""OpenJiuwenLlm sends a fixed thinking-off body on every distill invoke."""

from __future__ import annotations

import pytest

from openjiuwen.harness.personal_context.distill.llm import OpenJiuwenLlm


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeModel:
    def __init__(self) -> None:
        self.calls: list[tuple[list[object], dict[str, object]]] = []

    async def invoke(self, messages: list[object], **kwargs: object) -> _Response:
        self.calls.append((messages, kwargs))
        return _Response("ok")


@pytest.mark.asyncio
async def test_complete_disables_thinking_with_host_max_tokens() -> None:
    model = _FakeModel()
    llm = OpenJiuwenLlm(model, max_tokens=16384)

    text = await llm.complete(system="s", user="u")

    assert text == "ok"
    assert len(model.calls) == 1
    _messages, kwargs = model.calls[0]
    assert kwargs["temperature"] == 0.2
    assert kwargs["max_tokens"] == 16384
    assert kwargs["timeout"] == 180.0
    assert kwargs["extra_body"] == {
        "thinking": {"type": "disabled"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
