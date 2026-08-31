# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reliability-path tests for CodexSdkRuntime."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.external.cli_agent.codex.runtime import CodexSdkRuntime
from openjiuwen.agent_teams.schema.external_runtime_reliability import (
    ExternalRuntimeFailure,
)
from tests.test_logger import logger


def _notification(method: str, **payload):
    return SimpleNamespace(method=method, payload=SimpleNamespace(**payload))


class _FakeTurnHandle:
    def __init__(self, notifications):
        self.notifications = list(notifications)
        self.interrupted = 0

    async def stream(self):
        for n in self.notifications:
            yield n

    async def interrupt(self):
        self.interrupted += 1

    async def steer(self, content: str) -> None:
        pass


class _FakeThread:
    def __init__(self, turns):
        self.id = "thread-1"
        self._turns = list(turns)

    async def turn(self, prompt: str):
        return _FakeTurnHandle(self._turns.pop(0))


class _FakeAsyncCodex:
    def __init__(self, *, config, thread):
        self.config = config
        self.thread = thread
        self.closed = False
        self.start_calls: list[dict] = []
        self.resume_calls: list[tuple[str, dict]] = []

    async def thread_start(self, **options):
        self.start_calls.append(options)
        return self.thread

    async def thread_resume(self, thread_id, **options):
        self.resume_calls.append((thread_id, options))
        return self.thread

    async def close(self):
        self.closed = True


class _FakeMemberSession:
    def __init__(self, state=None):
        self._state = state
        self.pre_run_count = 0
        self.commit_count = 0
        self.post_run_count = 0

    def get_state(self, key):
        return None

    def update_state(self, state):
        pass

    async def pre_run(self):
        self.pre_run_count += 1

    async def commit(self):
        self.commit_count += 1

    async def post_run(self):
        self.post_run_count += 1


class _FakeTeamSession:
    def __init__(self, member_session):
        self._member_session = member_session

    def create_agent_session(self, *, agent_id, share_stream_writer=False):
        return self._member_session


class _FakeMessageManager:
    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, *, content, to_member_name, from_member_name, protocol="plain", meta=None):
        self.sent.append({"content": content, "to": to_member_name, "protocol": protocol})
        return f"mid-{len(self.sent)}"


class _FakeMessager:
    def __init__(self):
        self.published: list[Any] = []

    async def publish(self, *, topic_id, message):
        self.published.append((topic_id, message))


class _StatusSink:
    def __init__(self):
        self.statuses: list[Any] = []

    async def __call__(self, status):
        self.statuses.append(status)


def _build_runtime(turn_notifications) -> tuple[CodexSdkRuntime, _FakeMessageManager, _FakeMessager, _StatusSink]:
    thread = _FakeThread([turn_notifications])
    client = _FakeAsyncCodex(config=SimpleNamespace(name="config", env={}, cwd=None, codex_bin=None), thread=thread)
    sdk = SimpleNamespace(AsyncCodex=lambda *, config: client)
    member_session = _FakeMemberSession()
    runtime = CodexSdkRuntime(
        member_name="developer",
        member_agent_id="team_developer",
        team_name="team",
        team_session_id="session",
        sdk=sdk,
        config=client.config,
        thread_options={"ephemeral": False, "cwd": "/w", "developer_instructions": "role"},
        turn_idle_timeout_s=30.0,
        turn_idle_retries=0,
    )
    runtime._test_team_session = _FakeTeamSession(member_session)
    mm = _FakeMessageManager()
    messager = _FakeMessager()
    sink = _StatusSink()
    from openjiuwen.agent_teams.external.reliability import RuntimeReliabilityContext

    runtime._reliability_ctx = RuntimeReliabilityContext(
        member_name="developer",
        team_name="team",
        session_id="session",
        agent_kind="codex",
        message_manager=mm,
        messager=messager,
        leader_name="leader",
        update_status_cb=sink,
    )
    return runtime, mm, messager, sink


async def _start(runtime):
    await runtime.start(team_session=runtime._test_team_session)


@pytest.mark.asyncio
async def test_codex_will_retry_publishes_retrying_event():
    notifications = [
        _notification(
            "error",
            error=SimpleNamespace(message="overloaded", codex_error_info="serverOverloaded"),
            will_retry=True,
        ),
        _notification("turn/completed", turn=SimpleNamespace(status="completed")),
    ]
    runtime, mm, messager, sink = _build_runtime(notifications)
    await _start(runtime)
    async for _ in runtime._drive({"query": "hi"}):
        pass
    # No failed message — only a retrying event.
    assert len(mm.sent) == 0
    assert len(messager.published) == 1
    logger.info("retrying published, no failure message")


@pytest.mark.asyncio
async def test_codex_turn_final_401_finalizes_auth_required():
    notifications = [
        _notification(
            "turn/completed",
            turn=SimpleNamespace(
                status="failed",
                error=SimpleNamespace(message="unauthorized", codex_error_info="unauthorized"),
            ),
        ),
    ]
    runtime, mm, messager, sink = _build_runtime(notifications)
    await _start(runtime)
    runtime._current_round_id = 5
    async for _chunk in runtime._drive({"query": "hi"}):
        pass
    assert len(mm.sent) == 1
    failure = ExternalRuntimeFailure.model_validate_json(mm.sent[0]["content"])
    assert failure.category == "auth_required"
    assert failure.round_id == 5
    # In-turn failure does NOT mark ERROR; member returns to READY.
    assert sink.statuses == []
    logger.info("codex turn 401 finalized, no ERROR (returns to READY)")


@pytest.mark.asyncio
async def test_codex_error_notification_pending_then_turn_error_drops_pending():
    # will_retry=False records pending; turn.error (unauthorized) is authoritative.
    notifications = [
        _notification(
            "error",
            error=SimpleNamespace(message="stream down", codex_error_info="responseStreamDisconnected"),
            will_retry=False,
        ),
        _notification(
            "turn/completed",
            turn=SimpleNamespace(
                status="failed",
                error=SimpleNamespace(message="401", codex_error_info="unauthorized"),
            ),
        ),
    ]
    runtime, mm, messager, sink = _build_runtime(notifications)
    await _start(runtime)
    async for _chunk in runtime._drive({"query": "hi"}):
        pass
    assert len(mm.sent) == 1
    failure = ExternalRuntimeFailure.model_validate_json(mm.sent[0]["content"])
    # turn.error (auth_required) wins over the pending network_timeout.
    assert failure.category == "auth_required"
    logger.info("pending dropped in favor of turn.error: %s", failure.category)


@pytest.mark.asyncio
async def test_codex_pending_only_when_no_turn_error():
    notifications = [
        _notification(
            "error",
            error=SimpleNamespace(message="no auth", codex_error_info="unauthorized"),
            will_retry=False,
        ),
        _notification("turn/completed", turn=SimpleNamespace(status="failed", error=None)),
    ]
    runtime, mm, messager, sink = _build_runtime(notifications)
    await _start(runtime)
    async for _chunk in runtime._drive({"query": "hi"}):
        pass
    assert len(mm.sent) == 1
    failure = ExternalRuntimeFailure.model_validate_json(mm.sent[0]["content"])
    # No turn.error → pending (unauthorized) is used.
    assert failure.category == "auth_required"
    logger.info("pending used as fallback: %s", failure.category)


@pytest.mark.asyncio
async def test_codex_bad_request_is_sdk_error():
    notifications = [
        _notification(
            "turn/completed",
            turn=SimpleNamespace(
                status="failed",
                error=SimpleNamespace(message="bad", codex_error_info="badRequest"),
            ),
        ),
    ]
    runtime, mm, messager, sink = _build_runtime(notifications)
    await _start(runtime)
    async for _chunk in runtime._drive({"query": "hi"}):
        pass
    failure = ExternalRuntimeFailure.model_validate_json(mm.sent[0]["content"])
    assert failure.category == "sdk_error"
    logger.info("badRequest -> sdk_error")


@pytest.mark.asyncio
async def test_codex_http_status_overrides_error_info():
    # responseStreamDisconnected normally → network_timeout; with http 401
    # carried on the structured info → auth_required.
    from openai_codex.generated.v2_all import (
        CodexErrorInfo,
        ResponseStreamDisconnected,
        ResponseStreamDisconnectedCodexErrorInfo,
    )

    variant = ResponseStreamDisconnectedCodexErrorInfo(
        response_stream_disconnected=ResponseStreamDisconnected(http_status_code=401),
    )
    notifications = [
        _notification(
            "turn/completed",
            turn=SimpleNamespace(
                status="failed",
                error=SimpleNamespace(
                    message="stream",
                    codex_error_info=CodexErrorInfo(root=variant),
                ),
            ),
        ),
    ]
    runtime, mm, messager, sink = _build_runtime(notifications)
    await _start(runtime)
    async for _chunk in runtime._drive({"query": "hi"}):
        pass
    failure = ExternalRuntimeFailure.model_validate_json(mm.sent[0]["content"])
    assert failure.category == "auth_required"
    assert failure.reason.http_status == 401
    logger.info("http 401 overrode responseStreamDisconnected")


@pytest.mark.asyncio
async def test_codex_startup_auth_exception_does_not_activate_fallback():
    """Startup failures stay startup failures because authentication occurs during turns."""

    class _StartupAuthError(RuntimeError):
        http_status_code = 401

    class _FailingStartupClient(_FakeAsyncCodex):
        async def thread_start(self, **options: Any) -> Any:
            raise _StartupAuthError("startup unauthorized")

    native_config = SimpleNamespace(name="native", env={}, cwd=None, codex_bin=None)
    fallback_config = SimpleNamespace(name="fallback", env={}, cwd=None, codex_bin=None)
    native_client = _FailingStartupClient(config=native_config, thread=_FakeThread([]))
    fallback_client = _FakeAsyncCodex(config=fallback_config, thread=_FakeThread([]))
    clients = {"native": native_client, "fallback": fallback_client}
    sdk = SimpleNamespace(AsyncCodex=lambda *, config: clients[config.name])
    promotions = 0

    async def promote() -> bool:
        nonlocal promotions
        promotions += 1
        return True

    runtime = CodexSdkRuntime(
        member_name="developer",
        member_agent_id="team_developer",
        team_name="team",
        team_session_id="session",
        sdk=sdk,
        config=native_config,
        thread_options={"ephemeral": False},
        fallback_config=fallback_config,
        fallback_thread_options={"ephemeral": False, "model": "fallback"},
        promote_fallback_model=promote,
    )
    runtime._test_team_session = _FakeTeamSession(_FakeMemberSession())

    with pytest.raises(_StartupAuthError, match="startup unauthorized"):
        await _start(runtime)

    assert promotions == 0
    assert fallback_client.start_calls == []
    assert fallback_client.resume_calls == []
    assert runtime._fallback_activated is False


@pytest.mark.asyncio
async def test_codex_auth_failure_retries_once_on_promoted_fallback():
    """An output-free auth failure resumes the same thread on fallback."""
    native_notifications = [
        _notification(
            "turn/completed",
            turn=SimpleNamespace(
                status="failed",
                error=SimpleNamespace(message="unauthorized", codex_error_info="unauthorized"),
            ),
        ),
    ]
    fallback_notifications = [
        _notification("turn/completed", turn=SimpleNamespace(status="completed")),
    ]
    native_config = SimpleNamespace(name="native", env={}, cwd=None, codex_bin=None)
    fallback_config = SimpleNamespace(name="fallback", env={}, cwd=None, codex_bin=None)
    native_client = _FakeAsyncCodex(config=native_config, thread=_FakeThread([native_notifications]))
    fallback_client = _FakeAsyncCodex(config=fallback_config, thread=_FakeThread([fallback_notifications]))
    clients = {"native": native_client, "fallback": fallback_client}
    sdk = SimpleNamespace(AsyncCodex=lambda *, config: clients[config.name])
    promotions = 0

    async def promote() -> bool:
        nonlocal promotions
        promotions += 1
        return True

    runtime = CodexSdkRuntime(
        member_name="developer",
        member_agent_id="team_developer",
        team_name="team",
        team_session_id="session",
        sdk=sdk,
        config=native_config,
        thread_options={"ephemeral": False},
        fallback_config=fallback_config,
        fallback_thread_options={"ephemeral": False, "model": "fallback"},
        promote_fallback_model=promote,
        turn_idle_timeout_s=30.0,
        turn_idle_retries=0,
    )
    runtime._test_team_session = _FakeTeamSession(_FakeMemberSession())
    await _start(runtime)
    async for _chunk in runtime._drive({"query": "hi"}):
        pass
    assert native_client.closed is True
    assert runtime._fallback_activated is True
    assert promotions == 1
    assert fallback_client.start_calls == []
    assert fallback_client.resume_calls == [
        ("thread-1", {"model": "fallback"}),
    ]
    assert runtime._thread_id == "thread-1"


@pytest.mark.asyncio
async def test_codex_auth_will_retry_switches_to_fallback_immediately():
    """A structured retryable authentication failure bypasses the native retry budget."""
    native_notifications = [
        _notification(
            "error",
            error=SimpleNamespace(message="unauthorized", codex_error_info="unauthorized"),
            will_retry=True,
        ),
    ]
    fallback_notifications = [
        _notification("turn/completed", turn=SimpleNamespace(status="completed")),
    ]
    native_config = SimpleNamespace(name="native", env={}, cwd=None, codex_bin=None)
    fallback_config = SimpleNamespace(name="fallback", env={}, cwd=None, codex_bin=None)
    native_client = _FakeAsyncCodex(config=native_config, thread=_FakeThread([native_notifications]))
    fallback_client = _FakeAsyncCodex(config=fallback_config, thread=_FakeThread([fallback_notifications]))
    clients = {"native": native_client, "fallback": fallback_client}
    sdk = SimpleNamespace(AsyncCodex=lambda *, config: clients[config.name])
    promotions = 0

    async def promote() -> bool:
        nonlocal promotions
        promotions += 1
        return True

    runtime = CodexSdkRuntime(
        member_name="developer",
        member_agent_id="team_developer",
        team_name="team",
        team_session_id="session",
        sdk=sdk,
        config=native_config,
        thread_options={"ephemeral": False},
        fallback_config=fallback_config,
        fallback_thread_options={"ephemeral": False, "model": "fallback"},
        promote_fallback_model=promote,
        turn_idle_timeout_s=30.0,
        turn_idle_retries=0,
    )
    runtime._test_team_session = _FakeTeamSession(_FakeMemberSession())
    mm = _FakeMessageManager()
    messager = _FakeMessager()
    sink = _StatusSink()
    from openjiuwen.agent_teams.external.reliability import RuntimeReliabilityContext

    runtime._reliability_ctx = RuntimeReliabilityContext(
        member_name="developer",
        team_name="team",
        session_id="session",
        agent_kind="codex",
        message_manager=mm,
        messager=messager,
        leader_name="leader",
        update_status_cb=sink,
    )
    await _start(runtime)
    async for _chunk in runtime._drive({"query": "hi"}):
        pass

    assert native_client.closed is True
    assert runtime._fallback_activated is True
    assert runtime._will_retry_count == 0
    assert promotions == 1
    assert messager.published == []
    assert fallback_client.resume_calls == [
        ("thread-1", {"model": "fallback"}),
    ]
    assert runtime._thread_id == "thread-1"
