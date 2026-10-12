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
    parse_structured_text,
)
from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.harness_protocol import (
    HarnessCapability,
    HarnessCard,
    HarnessContext,
    HarnessState,
    MessageRole,
    ProviderEvent,
    StateChangedEvent,
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


async def _finish(harness: _FakeHarness, text: str, *, total_tokens: int | None = None) -> None:
    usage = TurnUsage(total_tokens=total_tokens) if total_tokens is not None else None
    await harness.emit(
        TurnLifecycleEvent(
            kind=TurnEventKind.FINISHED,
            result=TurnResult(status=TurnStatus.COMPLETED, final_output=text, usage=usage),
        ),
        turn_id="turn-1",
    )


@pytest.mark.asyncio
async def test_run_turn_returns_final_output_and_codex_usage_as_is():
    harness, driver = _driver("codex")
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("do it"))
    await asyncio.sleep(0)
    await _finish(harness, "done", total_tokens=1200)
    outcome = await task
    await driver.stop()
    assert outcome.text == "done"
    assert outcome.structured is None
    assert outcome.tokens == 1200
    assert harness.send_calls[0][0].content == "do it"


@pytest.mark.asyncio
async def test_claude_usage_is_differenced_per_turn_and_reset_aware():
    harness, driver = _driver("claude-code")
    await driver.start(_FakeTeamSession())
    tokens: list[int] = []
    for total in (3000, 4500, 800):
        task = asyncio.create_task(driver.run_turn("x"))
        await asyncio.sleep(0)
        await _finish(harness, "ok", total_tokens=total)
        tokens.append((await task).tokens)
    await driver.stop()
    assert tokens == [3000, 1500, 800]


@pytest.mark.asyncio
async def test_schema_turn_parses_json_and_appends_instruction():
    harness, driver = _driver("codex")
    await driver.start(_FakeTeamSession())
    schema = {"type": "object", "properties": {"risk": {"type": "string"}}, "required": ["risk"]}
    task = asyncio.create_task(driver.run_turn("review", schema_json=schema))
    await asyncio.sleep(0)
    await _finish(harness, 'Here you go:\n```json\n{"risk": "low"}\n```')
    outcome = await task
    await driver.stop()
    assert outcome.structured == {"risk": "low"}
    assert '"risk"' in harness.send_calls[0][0].content


@pytest.mark.asyncio
async def test_schema_turn_without_json_raises_backend_error():
    harness, driver = _driver("codex")
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("review", schema_json={"type": "object"}))
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
async def test_reported_model_reflects_model_event_after_run_turn():
    """A model-reporting event mid-turn is visible on the driver once the turn ends."""
    harness, driver = _model_driver()
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("do it"))
    await asyncio.sleep(0)
    await harness.emit(
        ProviderEvent(
            provider="codex",
            event_type="session/model_changed",
            schema_version="1",
            payload={"model": "GLM-5.3"},
        )
    )
    await _finish(harness, "done", total_tokens=1200)
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


def test_parse_structured_text_accepts_bare_and_fenced_json():
    assert parse_structured_text('{"a": 1}') == {"a": 1}
    assert parse_structured_text('text\n```json\n{"a": 1}\n```\nmore') == {"a": 1}
    assert parse_structured_text('prefix {"a": {"b": 2}} suffix') == {"a": {"b": 2}}
    assert parse_structured_text('x\n```json\n{"a": {"b": 2}}\n```') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        parse_structured_text("nothing")


@pytest.mark.asyncio
async def test_runtime_termination_mid_turn_raises_backend_error():
    harness, driver = _driver("codex")
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("long"))
    await asyncio.sleep(0)
    await harness.emit(StateChangedEvent(old=HarnessState.RUNNING, new=HarnessState.TERMINATED))
    with pytest.raises(BackendError, match="terminated"):
        await task
    await driver.stop()


@pytest.mark.asyncio
async def test_stale_round_event_is_ignored():
    harness, driver = _driver("codex")
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("x"))
    await asyncio.sleep(0)
    # Emit a FINISHED event for a stale turn — must not resolve the current turn's future.
    await harness.emit(
        TurnLifecycleEvent(
            kind=TurnEventKind.FINISHED,
            result=TurnResult(status=TurnStatus.COMPLETED, final_output="stale"),
        ),
        turn_id="turn-0",
    )
    await asyncio.sleep(0)
    assert not task.done()
    # Now emit the event for the real turn — resolves.
    await _finish(harness, "real", total_tokens=100)
    outcome = await task
    await driver.stop()
    assert outcome.text == "real"


@pytest.mark.asyncio
async def test_codex_turn_marker_is_native_turn_id():
    harness, driver = _driver("codex")
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("x"))
    await asyncio.sleep(0)
    await harness.emit(
        TurnLifecycleEvent(
            kind=TurnEventKind.FINISHED,
            result=TurnResult(status=TurnStatus.COMPLETED, final_output="ok",
                              provider_data={"native_turn_id": "turn_abc"}),
        ),
        turn_id="turn-1",
    )
    outcome = await task
    await driver.stop()
    assert outcome.marker == "turn_abc"


@pytest.mark.asyncio
async def test_claude_turn_marker_is_last_assistant_transcript_uuid():
    harness, driver = _driver("claude-code")
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("x"))
    await asyncio.sleep(0)
    messages = (
        TurnMessage(message_id="u-1", role=MessageRole.USER, content=()),
        TurnMessage(message_id="msg_01a", role=MessageRole.ASSISTANT, content=(),
                    data={"uuid": "11111111-2222-3333-4444-555555555555"}),
        TurnMessage(message_id="t-1", role=MessageRole.TOOL, content=()),
        TurnMessage(message_id="msg_01b", role=MessageRole.ASSISTANT, content=(),
                    data={"uuid": "3f2a0000-1111-2222-3333-444444444444"}),
    )
    await harness.emit(
        TurnLifecycleEvent(
            kind=TurnEventKind.FINISHED,
            result=TurnResult(status=TurnStatus.COMPLETED, final_output="ok", messages=messages),
        ),
        turn_id="turn-1",
    )
    outcome = await task
    await driver.stop()
    assert outcome.marker == "3f2a0000-1111-2222-3333-444444444444"


@pytest.mark.asyncio
async def test_claude_turn_marker_is_none_without_a_transcript_uuid():
    """The API message id is not a transcript uuid; offline fork_session rejects it."""
    harness, driver = _driver("claude-code")
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("x"))
    await asyncio.sleep(0)
    await harness.emit(
        TurnLifecycleEvent(
            kind=TurnEventKind.FINISHED,
            result=TurnResult(
                status=TurnStatus.COMPLETED, final_output="ok",
                messages=(TurnMessage(message_id="msg_01a", role=MessageRole.ASSISTANT, content=()),),
            ),
        ),
        turn_id="turn-1",
    )
    outcome = await task
    await driver.stop()
    assert outcome.marker is None


@pytest.mark.asyncio
async def test_marker_is_none_when_provider_reports_nothing():
    harness, driver = _driver("codex")
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("x"))
    await asyncio.sleep(0)
    await _finish(harness, "ok")
    assert (await task).marker is None
    await driver.stop()


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


@pytest.mark.asyncio
async def test_auth_failure_without_compatible_list_says_fix_the_login():
    harness, driver = _driver("codex", auth_hint_models=[])
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("x"))
    await asyncio.sleep(0)
    await _fail_auth(harness)
    with pytest.raises(BackendError) as exc:
        await task
    msg = str(exc.value)
    assert msg.endswith("fix the CLI login on this machine and re-run")
    assert "(compatible:" not in msg
    await driver.stop()


@pytest.mark.asyncio
async def test_auth_failure_without_constructor_hint_also_says_fix_the_login():
    harness, driver = _driver("claude-code")  # auth_hint_models defaults to None
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("x"))
    await asyncio.sleep(0)
    await _fail_auth(harness)
    with pytest.raises(BackendError) as exc:
        await task
    msg = str(exc.value)
    assert msg.startswith("claude-code CLI login failed (Reconnecting...")
    assert msg.endswith("fix the CLI login on this machine and re-run")
    await driver.stop()


@pytest.mark.asyncio
async def test_non_auth_failure_passes_through_unwrapped():
    """A generic (rate_limited) failure must NOT get the 'CLI login failed' copy."""
    harness, driver = _driver("codex", auth_hint_models=["GLM-5.3"])
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("x"))
    await asyncio.sleep(0)
    await harness.emit(
        TurnLifecycleEvent(
            kind=TurnEventKind.FAILED,
            result=TurnResult(
                status=TurnStatus.FAILED,
                error=TurnError(message="rate limited", category="rate_limited"),
            ),
        ),
        turn_id="turn-1",
    )
    with pytest.raises(BackendError, match="^external member 'wf-r1-ext-abc' failed: rate limited$"):
        await task
    await driver.stop()


@pytest.mark.asyncio
async def test_auth_failure_wrap_survives_truncation_to_160_chars():
    harness, driver = _driver("codex", auth_hint_models=["GLM-5.3", "sonnet"])
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("x"))
    await asyncio.sleep(0)
    await _fail_auth(harness)
    with pytest.raises(BackendError) as exc:
        await task
    head = str(exc.value)[:160]
    assert "401" in head
    assert "url: https://api" in head
    await driver.stop()


@pytest.mark.asyncio
async def test_auth_failure_wrap_keeps_model_slot_for_the_backend():
    """The wrap must not disturb Task 5's model plumbing: the runtime still reports the model
    the backend attaches to the BackendError (the slot is filled backend-side, not here)."""
    harness, driver = _model_driver()
    await driver.start(_FakeTeamSession())
    task = asyncio.create_task(driver.run_turn("x"))
    await asyncio.sleep(0)
    await harness.emit(
        ProviderEvent(
            provider="codex",
            event_type="session/model_changed",
            schema_version="1",
            payload={"model": "GLM-5.3"},
        )
    )
    await _fail_auth(harness)
    with pytest.raises(BackendError) as exc:
        await task
    assert exc.value.model is None  # attached by the backend (Task 5), not by the driver
    assert driver.reported_model == "GLM-5.3"
    await driver.stop()
