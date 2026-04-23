# coding: utf-8
"""Tests for TeamAgent._execute_round streaming error retry.

The retry logic lives in ``TeamAgent._execute_round`` and relies on the
task-loop executor emitting ``ControllerOutputChunk(payload.type='task_failed',
data=[TextDataFrame(text='[code] ...')])`` frames into the streaming chunk
flow. These tests inject a fake ``Runner.run_agent_streaming`` and drive
``_execute_round`` directly without spinning up a real DeepAgent.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List

import pytest

from openjiuwen.agent_teams.agent import team_agent as team_agent_module
from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.status import ExecutionStatus
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from tests.test_logger import logger


def _make_failed_chunk(code: int, message: str) -> SimpleNamespace:
    """Build a fake ControllerOutputChunk with a TASK_FAILED payload."""
    return SimpleNamespace(
        payload=SimpleNamespace(
            type="task_failed",
            data=[SimpleNamespace(text=f"[{code}] {message}")],
            metadata={"task_id": "t1"},
        ),
    )


def _make_failed_chunk_raw(text: str) -> SimpleNamespace:
    """Build a fake TASK_FAILED chunk with arbitrary text (no [code] prefix)."""
    return SimpleNamespace(
        payload=SimpleNamespace(
            type="task_failed",
            data=[SimpleNamespace(text=text)],
            metadata={"task_id": "t1"},
        ),
    )


def _make_answer_chunk(text: str) -> SimpleNamespace:
    """Build a fake normal answer chunk."""
    return SimpleNamespace(
        type="answer",
        payload={"output": text, "result_type": "answer"},
    )


def _make_team_agent_stub() -> TeamAgent:
    """Return a minimally initialized TeamAgent sufficient for _execute_round.

    Bypasses ``TeamAgent.__init__`` to avoid dragging in the full team
    bootstrap (runtime context, messager, backend, etc.). Only the attributes
    that ``_execute_round`` actually reads are populated.
    """
    agent = TeamAgent.__new__(TeamAgent)
    agent._deep_agent = object()  # type: ignore[assignment]
    agent._session_id = "sess-1"
    agent._stream_queue = asyncio.Queue()
    agent._streaming_active = False
    execution_log: list[ExecutionStatus] = []

    async def _record_execution(status: ExecutionStatus) -> None:
        execution_log.append(status)

    agent._update_execution = _record_execution  # type: ignore[method-assign]
    agent._execution_log = execution_log  # type: ignore[attr-defined]
    return agent


def _install_fake_runner(
    monkeypatch: pytest.MonkeyPatch,
    rounds: List[List[Any]],
) -> List[Any]:
    """Patch Runner.run_agent_streaming to iterate ``rounds`` one list per call.

    Each call to ``run_agent_streaming`` consumes one list from ``rounds``
    (in order) and yields its chunks. Captures the ``inputs`` of every call.
    """
    call_inputs: List[Any] = []
    rounds_iter = iter(rounds)

    async def fake_run_agent_streaming(agent, inputs, *, session=None, **kwargs):
        call_inputs.append(inputs)
        try:
            chunks = next(rounds_iter)
        except StopIteration:
            chunks = []
        for chunk in chunks:
            yield chunk

    monkeypatch.setattr(
        team_agent_module.Runner,
        "run_agent_streaming",
        fake_run_agent_streaming,
    )
    return call_inputs


async def _drain_queue(queue: asyncio.Queue) -> List[Any]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


@pytest.mark.asyncio
async def test_retry_on_181001_then_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _make_team_agent_stub()
    call_inputs = _install_fake_runner(
        monkeypatch,
        rounds=[
            [_make_failed_chunk(181001, "model call failed, reason: timeout")],
            [_make_failed_chunk(181001, "model call failed, reason: timeout")],
            [_make_failed_chunk(181001, "model call failed, reason: timeout")],
            [_make_answer_chunk("final answer")],
        ],
    )

    warnings: list[tuple] = []
    errors: list[tuple] = []
    monkeypatch.setattr(
        team_agent_module.team_logger,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )
    monkeypatch.setattr(
        team_agent_module.team_logger,
        "error",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    await agent._execute_round("initial query")

    logger.info("call_inputs=%s, queue=%s", call_inputs, agent._stream_queue.qsize())

    assert len(call_inputs) == 4
    assert call_inputs[0] == {"query": "initial query"}
    # Retries carry the canned retry query.
    for retry_call in call_inputs[1:]:
        assert retry_call == {"query": team_agent_module._RETRY_QUERY}

    assert len(warnings) == 3
    assert not errors

    chunks = await _drain_queue(agent._stream_queue)
    # Only the success-round answer chunk reaches the downstream queue — all
    # error frames from the three failed rounds were swallowed.
    assert len(chunks) == 1
    assert chunks[0].type == "answer"
    assert chunks[0].payload["output"] == "final answer"

    assert ExecutionStatus.COMPLETED in agent._execution_log  # type: ignore[attr-defined]
    assert ExecutionStatus.FAILED not in agent._execution_log  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_retries_exhausted_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _make_team_agent_stub()
    # 11 rounds of 181001 → exceeds MAX_RETRY_ATTEMPTS=10.
    failing_rounds = [
        [_make_failed_chunk(181001, f"timeout #{i}")]
        for i in range(team_agent_module._MAX_RETRY_ATTEMPTS + 1)
    ]
    call_inputs = _install_fake_runner(monkeypatch, rounds=failing_rounds)

    warnings: list[tuple] = []
    errors: list[tuple] = []
    monkeypatch.setattr(
        team_agent_module.team_logger,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )
    monkeypatch.setattr(
        team_agent_module.team_logger,
        "error",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    with pytest.raises(BaseError) as exc_info:
        await agent._execute_round("initial query")

    assert exc_info.value.status == StatusCode.AGENT_TEAM_EXECUTION_ERROR
    assert "181001" in str(exc_info.value)

    assert len(call_inputs) == team_agent_module._MAX_RETRY_ATTEMPTS + 1
    assert len(warnings) == team_agent_module._MAX_RETRY_ATTEMPTS
    # Two error log lines: one from the explicit raise site, one from the
    # outer ``except Exception`` in _execute_round.
    assert len(errors) == 2

    assert ExecutionStatus.FAILED in agent._execution_log  # type: ignore[attr-defined]
    assert ExecutionStatus.COMPLETED not in agent._execution_log  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_non_retryable_code_raises_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _make_team_agent_stub()
    call_inputs = _install_fake_runner(
        monkeypatch,
        rounds=[
            [_make_failed_chunk(182012, "tool execution error, card=X, reason=Y")],
        ],
    )

    warnings: list[tuple] = []
    monkeypatch.setattr(
        team_agent_module.team_logger,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    with pytest.raises(BaseError) as exc_info:
        await agent._execute_round("initial query")

    assert "182012" in str(exc_info.value)
    assert len(call_inputs) == 1
    assert not warnings
    assert ExecutionStatus.FAILED in agent._execution_log  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_missing_code_prefix_is_non_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _make_team_agent_stub()
    call_inputs = _install_fake_runner(
        monkeypatch,
        rounds=[
            [_make_failed_chunk_raw("unexpected error without code")],
        ],
    )

    warnings: list[tuple] = []
    monkeypatch.setattr(
        team_agent_module.team_logger,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    with pytest.raises(BaseError):
        await agent._execute_round("initial query")

    assert len(call_inputs) == 1
    assert not warnings


@pytest.mark.asyncio
async def test_trailing_frames_after_error_are_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _make_team_agent_stub()
    # First round: TASK_FAILED, then trailing garbage (simulates the blank
    # answer chunk + anything else before END_FRAME naturally terminates the
    # iterator). Second round: normal answer.
    call_inputs = _install_fake_runner(
        monkeypatch,
        rounds=[
            [
                _make_failed_chunk(181001, "model call failed, reason: boom"),
                _make_answer_chunk("should NOT reach downstream"),
                _make_answer_chunk("also should NOT reach downstream"),
            ],
            [_make_answer_chunk("final")],
        ],
    )

    monkeypatch.setattr(
        team_agent_module.team_logger,
        "warning",
        lambda *args, **kwargs: None,
    )

    await agent._execute_round("initial query")

    assert len(call_inputs) == 2
    chunks = await _drain_queue(agent._stream_queue)
    assert len(chunks) == 1
    assert chunks[0].payload["output"] == "final"


def test_detect_task_failed_parses_code_and_text() -> None:
    chunk = _make_failed_chunk(181001, "model call failed, reason: timeout")
    result = team_agent_module._detect_task_failed(chunk)
    assert result is not None
    code, text = result
    assert code == 181001
    assert text == "[181001] model call failed, reason: timeout"


def test_detect_task_failed_returns_none_for_normal_chunk() -> None:
    chunk = _make_answer_chunk("hello")
    assert team_agent_module._detect_task_failed(chunk) is None


def test_detect_task_failed_none_code_when_no_prefix() -> None:
    chunk = _make_failed_chunk_raw("no prefix here")
    result = team_agent_module._detect_task_failed(chunk)
    assert result is not None
    code, text = result
    assert code is None
    assert text == "no prefix here"


def test_detect_task_failed_handles_empty_data() -> None:
    chunk = SimpleNamespace(
        payload=SimpleNamespace(type="task_failed", data=[], metadata={}),
    )
    result = team_agent_module._detect_task_failed(chunk)
    assert result == (None, "")
