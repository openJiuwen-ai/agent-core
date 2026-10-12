# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the swarmflow driver over an external SDK member runtime."""
from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from openjiuwen.agent_teams.external.member_runtime import ExternalHarnessMemberRuntime
from openjiuwen.agent_teams.workflow.backends.external_turns import (
    ExternalMemberTurns,
)
from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.harness_protocol import (
    HarnessCapability,
    HarnessCard,
    HarnessContext,
    HarnessState,
    MessageRole,
    ProviderEvent,
    TurnError,
    TurnEventKind,
    TurnLifecycleEvent,
    TurnMessage,
    TurnResult,
    TurnStatus,
    TurnUsage,
)
from tests.unit_tests.agent_teams.external.test_member_runtime import (
    _FakeHarness,
    _FakeTeamSession,
)


def _context() -> HarnessContext:
    return HarnessContext(
        agent_name="wf-r1-ext-abc", agent_id="t_wf-r1-ext-abc", host_session_id="s1", system_prompt="work",
    )


def _driver(
    name: str = "fake",
    auth_hint_models: Sequence[str] | None = None,
) -> tuple[_FakeHarness, ExternalMemberTurns]:
    # Both abort capabilities: the IO adapter only forwards abort() to a
    # provider that declares them (and only while the harness is RUNNING).
    harness = _FakeHarness(capabilities=frozenset({HarnessCapability.GRACEFUL_ABORT, HarnessCapability.FORCE_ABORT}))
    harness._card = HarnessCard(name=name, implementation_version="1.0", capabilities=harness._card.capabilities)
    runtime = ExternalHarnessMemberRuntime(harness=harness, context=_context())
    return harness, ExternalMemberTurns(runtime, member_name="wf-r1-ext-abc", auth_hint_models=auth_hint_models)


def _model_driver(name: str = "codex") -> tuple[_FakeHarness, ExternalMemberTurns]:
    """A driver whose runtime keeps a reliability context (so model events land)."""
    harness = _FakeHarness(capabilities=frozenset({HarnessCapability.GRACEFUL_ABORT, HarnessCapability.FORCE_ABORT}))
    harness._card = HarnessCard(name=name, implementation_version="1.0", capabilities=harness._card.capabilities)
    runtime = ExternalHarnessMemberRuntime(harness=harness, context=_context(), agent_kind=name)
    runtime.bind_reliability_context(
        session_id="s1",
        team_backend=None,
        leader_name="leader",
        update_status_cb=lambda status: None,
        messager=None,
    )
    return harness, ExternalMemberTurns(runtime, member_name="wf-r1-ext-abc")


async def _finish(
    harness: _FakeHarness,
    text: str,
    *,
    total_tokens: int | None = None,
    usage: TurnUsage | None = None,
) -> None:
    if usage is None:
        usage = TurnUsage(total_tokens=total_tokens) if total_tokens is not None else None
    await harness.emit(
        TurnLifecycleEvent(
            kind=TurnEventKind.FINISHED,
            result=TurnResult(status=TurnStatus.COMPLETED, final_output=text, usage=usage),
        ),
        turn_id="turn-1",
    )


@pytest.mark.asyncio
async def test_codex_usage_split_passes_through_unchanged():
    """Non-cumulative providers: the input/output/cache split lands as reported."""
    harness, driver = _driver("codex")
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("do it"))
    await asyncio.sleep(0)
    await _finish(
        harness,
        "done",
        usage=TurnUsage(
            total_tokens=1200, input_tokens=10970, output_tokens=601, cached_input_tokens=10624
        ),
    )
    outcome = await task
    await driver.stop()
    assert outcome.tokens == 1200
    assert outcome.input_tokens == 10970
    assert outcome.output_tokens == 601
    assert outcome.cache_tokens == 10624


@pytest.mark.asyncio
async def test_claude_usage_split_is_differenced_per_turn_and_reset_aware():
    """Cumulative providers: every counter is diffed against the last turn;
    a counter that went backwards means the CLI reset its conversation, so the
    whole new value belongs to that turn."""
    harness, driver = _driver("claude-code")
    await driver.start(_FakeTeamSession())
    splits: list[tuple[int, int | None, int | None, int | None]] = []
    running = [
        # (total, input, output, cached) — the CLI session's running totals.
        (11571, 10970, 601, 10624),
        (12871, 11770, 701, 11224),
        (800, 700, 100, 300),  # dropped below the last total: conversation reset
    ]
    for total, in_n, out_n, cached in running:
        task = asyncio.create_task(driver.run_turn("x"))
        await asyncio.sleep(0)
        await _finish(
            harness, "ok",
            usage=TurnUsage(total_tokens=total, input_tokens=in_n, output_tokens=out_n, cached_input_tokens=cached),
        )
        o = await task
        splits.append((o.tokens, o.input_tokens, o.output_tokens, o.cache_tokens))
    await driver.stop()
    assert splits == [
        (11571, 10970, 601, 10624),
        (1300, 800, 100, 600),
        (800, 700, 100, 300),
    ]


@pytest.mark.asyncio
async def test_schema_turn_parses_json_or_raises():
    """A schema turn appends the JSON instruction and parses the fenced object;
    a reply with no JSON object is a BackendError (native "not submitted")."""
    harness, driver = _driver("codex")
    await driver.start(_FakeTeamSession())
    schema = {"type": "object", "properties": {"risk": {"type": "string"}}, "required": ["risk"]}
    task = asyncio.create_task(driver.run_turn("review", schema_json=schema))
    await asyncio.sleep(0)
    await _finish(harness, 'Here you go:\n```json\n{"risk": "low"}\n```')
    outcome = await task
    assert outcome.structured == {"risk": "low"}
    assert '"risk"' in harness.send_calls[0][0].content

    task = asyncio.create_task(driver.run_turn("review", schema_json=schema))
    await asyncio.sleep(0)
    await _finish(harness, "no json here")
    with pytest.raises(BackendError):
        await task
    await driver.stop()


@pytest.mark.asyncio
async def test_failed_turn_raises_backend_error_with_provider_message():
    harness, driver = _driver("codex")
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("x"))
    await asyncio.sleep(0)
    await harness.emit(
        TurnLifecycleEvent(
            kind=TurnEventKind.FAILED,
            result=TurnResult(status=TurnStatus.FAILED, error=TurnError(message="rate limited")),
        ),
        turn_id="turn-1",
    )
    with pytest.raises(BackendError, match="rate limited"):
        await task
    await driver.stop()


@pytest.mark.asyncio
async def test_reported_model_works_without_a_reliability_context():
    """Swarmflow workers bind no reliability context; the model is captured on the runtime.

    Without this, a CLI that fell back to its own default model (auth fallback)
    reports it but AGENT_COMPLETED keeps the option hint — the UI then shows a
    model the member never ran on.
    """
    harness, driver = _driver("claude-code")
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("do it"))
    await asyncio.sleep(0)
    await harness.emit(
        ProviderEvent(
            provider="claude-code",
            event_type="session/model_changed",
            schema_version="1",
            payload={"model": "GLM-5.3"},
        )
    )
    await _finish(harness, "done", total_tokens=900)
    await task
    await driver.stop()
    assert driver.reported_model == "GLM-5.3"


@pytest.mark.asyncio
async def test_cancellation_aborts_the_runtime_immediately():
    harness, driver = _driver("codex")
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("long"))
    await asyncio.sleep(0)
    harness.state = HarnessState.RUNNING  # the turn is in flight from the provider's point of view
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await driver.stop()
    assert len(harness.abort_calls) == 1


_CLAUDE_TRANSCRIPT = (
        TurnMessage(message_id="u-1", role=MessageRole.USER, content=()),
        TurnMessage(message_id="msg_01a", role=MessageRole.ASSISTANT, content=(),
                    data={"uuid": "11111111-2222-3333-4444-555555555555"}),
        TurnMessage(message_id="t-1", role=MessageRole.TOOL, content=()),
        TurnMessage(message_id="msg_01b", role=MessageRole.ASSISTANT, content=(),
                    data={"uuid": "3f2a0000-1111-2222-3333-444444444444"}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "result_kwargs", "expected_marker"),
    [
        ("codex", {"provider_data": {"native_turn_id": "turn_abc"}}, "turn_abc"),
        ("claude-code", {"messages": _CLAUDE_TRANSCRIPT}, "3f2a0000-1111-2222-3333-444444444444"),
    ],
)
async def test_turn_marker_follows_the_provider_cut_point(provider, result_kwargs, expected_marker):
    """Codex marks a turn by its native turn id; Claude by the last assistant transcript uuid."""
    harness, driver = _driver(provider)
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("x"))
    await asyncio.sleep(0)
    await harness.emit(
        TurnLifecycleEvent(
            kind=TurnEventKind.FINISHED,
            result=TurnResult(status=TurnStatus.COMPLETED, final_output="ok", **result_kwargs),
        ),
        turn_id="turn-1",
    )
    outcome = await task
    await driver.stop()
    assert outcome.marker == expected_marker


# ---------------------------------------------------------------------------
# Auth-classified failure wrapping (two-audience copy)
# ---------------------------------------------------------------------------

_AUTH_ERROR_TEXT = (
    "Reconnecting... 2/5 unexpected status 401 Unauthorized: Missing bearer or basic "
    "authentication in header, url: https://api.openai.com/v1/responses"
)


async def _fail_auth(harness: _FakeHarness) -> None:
    # The real signal: reconnect exhaustion surfaces a FAILED turn whose
    # TurnError carries ``category="auth_required"`` (the same gate
    # ``_fallback_applies`` / auth_fallback promotion key on).
    await harness.emit(
        TurnLifecycleEvent(
            kind=TurnEventKind.FAILED,
            result=TurnResult(
                status=TurnStatus.FAILED,
                error=TurnError(message=_AUTH_ERROR_TEXT, code="CODEX_FALLBACK_EXHAUSTED", category="auth_required"),
            ),
        ),
        turn_id="turn-1",
    )


@pytest.mark.asyncio
async def test_auth_failure_is_wrapped_two_audience_with_compatible_list():
    """The surfaced message front-loads the root cause and appends the workaround + pool list."""
    harness, driver = _driver("codex", auth_hint_models=["GLM-5.3", "sonnet"])
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("x"))
    await asyncio.sleep(0)
    await _fail_auth(harness)
    with pytest.raises(BackendError) as exc:
        await task
    msg = str(exc.value)
    assert msg.startswith("codex CLI login failed (Reconnecting... 2/5 unexpected status 401 Unauthorized")
    assert "401" in msg and "https://api.openai.com/v1/responses" in msg
    assert "environment problem, not a script error" in msg
    assert msg.endswith("workaround: add model=<pool model> to this node (compatible: GLM-5.3, sonnet)")
    await driver.stop()
