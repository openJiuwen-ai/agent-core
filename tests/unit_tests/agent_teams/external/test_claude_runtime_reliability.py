# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reliability-path tests for ClaudeSdkRuntime.

These tests drive the classification + finalize surface injected via
``bind_reliability_context`` against a minimal fake Claude SDK. They focus on
the failure paths; the normal-stream mapping path is covered by the existing
``test_claude_sdk_runtime`` suite.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

from openjiuwen.agent_teams.schema.status import MemberStatus

import pytest

from openjiuwen.agent_teams.external.cli_agent.claude.runtime import ClaudeSdkRuntime
from openjiuwen.agent_teams.schema.external_runtime_reliability import ExternalRuntimeFailure
from tests.test_logger import logger


def _install_fake_sdk(monkeypatch, *, messages_factory, connect_error=None) -> ModuleType:
    """Install a minimal fake ``claude_agent_sdk`` with failure-bearing types.

    ``messages_factory`` is called lazily inside ``receive_response`` so the
    test can build messages using the fake SDK's own types (resolved after
    this function returns the module).
    """
    sdk = ModuleType("claude_agent_sdk")
    sdk.__version__ = "0.0.0"

    class CLIConnectionError(Exception):
        pass

    class ProcessError(Exception):
        def __init__(self, message, *, exit_code=None, stderr=None):
            super().__init__(message)
            self.exit_code = exit_code
            self.stderr = stderr

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class ThinkingBlock:
        def __init__(self, thinking):
            self.thinking = thinking

    class ToolUseBlock:
        def __init__(self, id, name, input):
            self.id = id
            self.name = name
            self.input = input

    class ToolResultBlock:
        def __init__(self, tool_use_id, content):
            self.tool_use_id = tool_use_id
            self.content = content

    class AssistantMessage:
        def __init__(self, *, content, error=None):
            self.content = content
            self.error = error

    class UserMessage:
        def __init__(self, *, content, parent_tool_use_id=None, tool_use_result=None):
            self.content = content
            self.parent_tool_use_id = parent_tool_use_id
            self.tool_use_result = tool_use_result

    class SystemMessage:
        def __init__(self, *, subtype):
            self.subtype = subtype

    class ResultMessage:
        def __init__(self, *, subtype="success", is_error=False, api_error_status=None, errors=None):
            self.subtype = subtype
            self.is_error = is_error
            self.api_error_status = api_error_status
            self.errors = errors

    class ClaudeSDKClient:
        def __init__(self, *, options, transport=None):
            self.options = options
            self.transport = transport

        async def connect(self):
            if connect_error is not None:
                raise connect_error

        async def query(self, prompt, session_id="default"):
            pass

        async def receive_response(self):
            for message in messages_factory():
                yield message

        async def interrupt(self):
            pass

        async def disconnect(self):
            pass

    sdk.CLIConnectionError = CLIConnectionError
    sdk.CLINotFoundError = CLIConnectionError
    sdk.ProcessError = ProcessError
    sdk.TextBlock = TextBlock
    sdk.ThinkingBlock = ThinkingBlock
    sdk.ToolUseBlock = ToolUseBlock
    sdk.ToolResultBlock = ToolResultBlock
    sdk.AssistantMessage = AssistantMessage
    sdk.UserMessage = UserMessage
    sdk.SystemMessage = SystemMessage
    sdk.ResultMessage = ResultMessage
    sdk.ClaudeSDKClient = ClaudeSDKClient
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr(
        "openjiuwen.agent_teams.external.cli_agent.claude.runtime.load_claude_sdk",
        lambda: sdk,
    )
    return sdk


class _FakeOptions(SimpleNamespace):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tools = None
        self.system_prompt = None
        self.allowed_tools = []
        self.mcp_servers = None
        self.cwd = None
        self.cli_path = None
        self.env = {}
        self.resume = None
        self.session_id = None
        self.stderr = None


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


def _build_ctx(mm, messager, sink):
    from openjiuwen.agent_teams.external.reliability import RuntimeReliabilityContext

    return RuntimeReliabilityContext(
        member_name="worker1",
        team_name="team",
        session_id="session",
        agent_kind="claude",
        message_manager=mm,
        messager=messager,
        leader_name="leader",
        update_status_cb=sink,
    )


def _make_runtime(sdk) -> ClaudeSdkRuntime:
    return ClaudeSdkRuntime(
        member_name="worker1",
        options=_FakeOptions(),
        transport=None,
        inject_mcp=False,
        member_agent_id="agent_worker1",
    )


@pytest.mark.asyncio
async def test_result_message_401_finalizes_auth_required(monkeypatch):
    sdk = _install_fake_sdk(
        monkeypatch,
        messages_factory=lambda: [
            sdk.ResultMessage(is_error=True, api_error_status=401, errors=["unauthorized"]),
        ],
    )
    runtime = _make_runtime(sdk)
    mm = _FakeMessageManager()
    sink = _StatusSink()
    runtime._reliability_ctx = _build_ctx(mm, _FakeMessager(), sink)
    runtime._current_round_id = 7

    async for _chunk in runtime._drive({"query": "hi"}):
        pass

    assert len(mm.sent) == 1
    failure = ExternalRuntimeFailure.model_validate_json(mm.sent[0]["content"])
    assert failure.category == "auth_required"
    assert failure.round_id == 7
    assert failure.user_action_required is True
    logger.info("finalized failure: %s", failure.failure_id)


@pytest.mark.asyncio
async def test_plain_textblock_does_not_trigger_failure(monkeypatch):
    sdk = _install_fake_sdk(
        monkeypatch,
        messages_factory=lambda: [
            sdk.AssistantMessage(content=[sdk.TextBlock("Not logged in · Please run /login")]),
            sdk.ResultMessage(subtype="success"),
        ],
    )
    runtime = _make_runtime(sdk)
    mm = _FakeMessageManager()
    runtime._reliability_ctx = _build_ctx(mm, _FakeMessager(), _StatusSink())
    chunks = []
    async for chunk in runtime._drive({"query": "hi"}):
        chunks.append(chunk)
    assert len(mm.sent) == 0
    assert chunks
    logger.info("text surfaced as %d chunks, no failure", len(chunks))


@pytest.mark.asyncio
async def test_assistant_error_then_result_combines_into_one_failure(monkeypatch):
    sdk = _install_fake_sdk(
        monkeypatch,
        messages_factory=lambda: [
            sdk.AssistantMessage(content=[], error="rate_limit"),
            sdk.ResultMessage(is_error=True, api_error_status=429, errors=["too many"]),
        ],
    )
    runtime = _make_runtime(sdk)
    mm = _FakeMessageManager()
    runtime._reliability_ctx = _build_ctx(mm, _FakeMessager(), _StatusSink())
    async for _chunk in runtime._drive({"query": "hi"}):
        pass
    assert len(mm.sent) == 1
    failure = ExternalRuntimeFailure.model_validate_json(mm.sent[0]["content"])
    assert failure.category == "rate_limited"
    assert failure.reason.http_status == 429
    logger.info("combined failure category=%s http=%s", failure.category, failure.reason.http_status)


@pytest.mark.asyncio
async def test_startup_connect_failure_marks_member_error(monkeypatch):
    # Use the real SDK's CLIConnectionError so classify_claude_exception can
    # match it as a process_start_failed startup failure. The fake SDK re-uses
    # the real exception classes so isinstance checks hold.
    from openjiuwen.agent_teams.external.cli_agent.claude.options import load_claude_sdk as _real_load

    real_sdk = _real_load()
    connect_err = real_sdk.CLIConnectionError("no cli")

    def factory():
        return []

    sdk = _install_fake_sdk(monkeypatch, messages_factory=factory, connect_error=connect_err)
    # Make the fake SDK expose the real exception classes so classifier
    # isinstance checks match the raised exception.
    sdk.CLIConnectionError = real_sdk.CLIConnectionError
    sdk.CLINotFoundError = real_sdk.CLINotFoundError
    sdk.ProcessError = real_sdk.ProcessError
    runtime = _make_runtime(sdk)
    mm = _FakeMessageManager()
    sink = _StatusSink()
    runtime._reliability_ctx = _build_ctx(mm, _FakeMessager(), sink)

    with pytest.raises(real_sdk.CLIConnectionError):
        await runtime.start()

    assert len(mm.sent) == 1
    failure = ExternalRuntimeFailure.model_validate_json(mm.sent[0]["content"])
    assert failure.category == "process_start_failed"
    assert failure.phase == "startup"
    assert sink.statuses == [MemberStatus.ERROR]
    logger.info("startup failure -> ERROR, category=%s", failure.category)


@pytest.mark.asyncio
async def test_pending_auth_failure_activates_and_promotes_fallback(monkeypatch):
    """A structured auth signal followed by stream failure activates fallback."""
    attempts = 0

    def messages():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            yield sdk.AssistantMessage(content=[], error="authentication_failed")
            raise RuntimeError("stream closed")
        yield sdk.ResultMessage(subtype="success")

    sdk = _install_fake_sdk(monkeypatch, messages_factory=messages)
    promotions = 0

    async def promote() -> bool:
        nonlocal promotions
        promotions += 1
        return True

    runtime = ClaudeSdkRuntime(
        member_name="worker1",
        options=_FakeOptions(),
        fallback_options=_FakeOptions(),
        promote_fallback_model=promote,
        inject_mcp=False,
    )
    runtime._reliability_ctx = _build_ctx(
        _FakeMessageManager(),
        _FakeMessager(),
        _StatusSink(),
    )
    async for _chunk in runtime._drive({"query": "hi"}):
        pass
    assert runtime._fallback_activated is True
    assert promotions == 1
    assert attempts == 2


@pytest.mark.asyncio
async def test_auth_diagnostic_text_is_discarded_after_fallback(monkeypatch):
    """A structured login diagnostic does not block or leak into a successful fallback."""
    attempts = 0

    def messages():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            yield sdk.AssistantMessage(
                content=[sdk.TextBlock("Not logged in · Please run /login")],
                error="authentication_failed",
            )
            yield sdk.ResultMessage(is_error=True, errors=["authentication_failed"])
            return
        yield sdk.AssistantMessage(content=[sdk.TextBlock("fallback answer")])
        yield sdk.ResultMessage(subtype="success")

    sdk = _install_fake_sdk(monkeypatch, messages_factory=messages)
    promotions = 0

    async def promote() -> bool:
        nonlocal promotions
        promotions += 1
        return True

    runtime = ClaudeSdkRuntime(
        member_name="worker1",
        options=_FakeOptions(),
        fallback_options=_FakeOptions(),
        promote_fallback_model=promote,
        inject_mcp=False,
    )
    runtime._reliability_ctx = _build_ctx(
        _FakeMessageManager(),
        _FakeMessager(),
        _StatusSink(),
    )
    chunks = []
    async for chunk in runtime._drive({"query": "hi"}):
        chunks.append(chunk)

    assert runtime._fallback_activated is True
    assert promotions == 1
    assert attempts == 2
    assert [chunk.payload["content"] for chunk in chunks] == ["fallback answer"]
