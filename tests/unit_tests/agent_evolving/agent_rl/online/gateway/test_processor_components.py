from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.agent_evolving.agent_rl.online.gateway.app.server import _forward_chat_completions
from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.sample_payloads import (
    build_sample,
)


class _FakeForwarder:
    def __init__(self) -> None:
        self.forward_calls: list[dict] = []

    async def forward(self, body: dict, headers: dict):
        self.forward_calls.append({"body": body, "headers": headers})
        return {"choices": [{"message": {"role": "assistant", "content": "pong"}}]}


class _FakeJudgeScorer:
    def __init__(self, score_result=None) -> None:
        self.calls: list[dict] = []
        self.closed = False
        self.score_result = score_result or {"score": 0.75, "votes": ["ok"], "details": {}}

    async def score(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.score_result)

    async def close(self) -> None:
        self.closed = True


def test_build_sample_builds_shared_masks():
    sample = build_sample(
        sample_id="sample-1",
        user_id="user-1",
        session_id="s1",
        turn_num=1,
        mode="judge_output",
        io_mode="string",
        model="m1",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function"}],
        assistant_message={"role": "assistant", "content": "pong"},
        usage={"total_tokens": 5},
        finish_reason="stop",
        prompt_text="prompt",
        prompt_ids=[1, 2, 3],
        response_text="pong",
        response_ids=[4, 5],
        response_logprobs=[-0.1, -0.2],
        tool_calls=[],
        request_extras={"temperature": 0.2},
        extra_fields={"rail_meta": {"protocol_version": "rail-v1"}},
    )

    assert sample["trajectory"]["input_ids"] == [1, 2, 3, 4, 5]
    assert sample["trajectory"]["attention_mask"] == [1, 1, 1, 1, 1]
    assert sample["trajectory"]["response_mask"] == [0, 0, 0, 1, 1]
    assert sample["request"]["temperature"] == 0.2
    assert sample["rail_meta"]["protocol_version"] == "rail-v1"


@pytest.mark.asyncio
async def test_processor_chat_completion_proxies_without_turn_or_sample_work():
    forwarder = _FakeForwarder()
    config = SimpleNamespace(
        llm_api_key="",
    )

    request = SimpleNamespace(headers={"x-request-id": "trace-9", "x-user-id": "user-9"})
    result = await _forward_chat_completions(
        request=request,
        body={"messages": [{"role": "user", "content": "hello"}]},
        config=config,
        forwarder=forwarder,
    )

    assert result["choices"][0]["message"]["content"] == "pong"
    assert len(forwarder.forward_calls) == 1
