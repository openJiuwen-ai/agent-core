# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the Claude Agent SDK external member backend."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from typing import Any

import pytest

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.external.cli_agent import spawn as spawn_mod
from openjiuwen.agent_teams.external.cli_agent.claude.options import derive_claude_session_id
from openjiuwen.agent_teams.external.cli_agent.claude.runtime import ClaudeSdkRuntime
from openjiuwen.agent_teams.external.cli_agent.claude.ssh_transport import build_claude_sdk_ssh_transport
from openjiuwen.agent_teams.messager.base import MessagerTransportConfig
from openjiuwen.agent_teams.schema.ssh_transport import SshTransportConfig
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.tools.memory_database import MemoryDatabaseConfig
from openjiuwen.core.common.exception.errors import BaseError


class _FakeOptions:
    def __init__(self, **kwargs: Any) -> None:
        self.tools = None
        self.system_prompt = None
        self.allowed_tools = []
        self.setting_sources = None
        self.skills = None
        self.max_turns = None
        self.max_budget_usd = None
        self.disallowed_tools = []
        self.task_budget = None
        self.model = None
        self.fallback_model = None
        self.betas = []
        self.permission_prompt_tool_name = None
        self.permission_mode = None
        self.continue_conversation = False
        self.resume = None
        self.session_id = None
        self.settings = None
        self.sandbox = None
        self.add_dirs = []
        self.mcp_servers = None
        self.include_partial_messages = False
        self.include_hook_events = False
        self.strict_mcp_config = False
        self.fork_session = False
        self.session_store = None
        self.plugins = []
        self.extra_args = {}
        self.thinking = None
        self.max_thinking_tokens = None
        self.effort = None
        self.output_format = None
        self.max_buffer_size = None
        self.cwd = None
        self.cli_path = None
        self.env = {}
        self.user = None
        self.can_use_tool = None
        self.hooks = {}
        self.agents = None
        self.session_store_flush = None
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeClaudeSdk:
    __version__ = "0.0.0-test"

    CLIConnectionError = RuntimeError

    class ProcessError(RuntimeError):
        def __init__(self, message: str, *, exit_code: int, stderr: str) -> None:
            super().__init__(message)
            self.exit_code = exit_code
            self.stderr = stderr

    class ClaudeAgentOptions(_FakeOptions):
        pass

    class TextBlock:
        def __init__(self, *, text: str) -> None:
            self.text = text

    class ThinkingBlock:
        def __init__(self, *, thinking: str) -> None:
            self.thinking = thinking

    class ToolUseBlock:
        def __init__(self, *, id: str, name: str, input: dict[str, Any]) -> None:
            self.id = id
            self.name = name
            self.input = input

    class ToolResultBlock:
        def __init__(self, *, tool_use_id: str, content: Any) -> None:
            self.tool_use_id = tool_use_id
            self.content = content

    class AssistantMessage:
        def __init__(self, *, content: list[Any]) -> None:
            self.content = content

    class UserMessage:
        def __init__(
            self,
            *,
            content: Any,
            parent_tool_use_id: str | None = None,
            tool_use_result: Any | None = None,
        ) -> None:
            self.content = content
            self.parent_tool_use_id = parent_tool_use_id
            self.tool_use_result = tool_use_result

    class SystemMessage:
        def __init__(self, *, subtype: str) -> None:
            self.subtype = subtype

    class ResultMessage:
        def __init__(self, *, subtype: str) -> None:
            self.subtype = subtype

    class ClaudeSDKClient:
        def __init__(self, *, options: _FakeOptions, transport: Any | None = None) -> None:
            self.options = options
            self.transport = transport
            self.connected = False
            self.queries: list[str] = []
            self.interrupted = False
            self.disconnected = False

        async def connect(self) -> None:
            self.connected = True

        async def query(self, prompt: str, session_id: str = "default") -> None:
            _ = session_id
            self.queries.append(prompt)

        async def receive_response(self):
            yield _FakeClaudeSdk.AssistantMessage(
                content=[
                    _FakeClaudeSdk.TextBlock(text="done"),
                    _FakeClaudeSdk.ThinkingBlock(thinking="reasoning"),
                    _FakeClaudeSdk.ToolUseBlock(id="toolu_1", name="Read", input={"file_path": "a.py"}),
                ],
            )
            yield _FakeClaudeSdk.UserMessage(
                content=[_FakeClaudeSdk.ToolResultBlock(tool_use_id="toolu_1", content="file body")],
            )
            yield _FakeClaudeSdk.UserMessage(
                content="prompt echo",
                parent_tool_use_id="toolu_2",
                tool_use_result={"ok": True},
            )
            yield _FakeClaudeSdk.SystemMessage(subtype="task_started")
            yield _FakeClaudeSdk.ResultMessage(subtype="success")

        async def interrupt(self) -> None:
            self.interrupted = True

        async def disconnect(self) -> None:
            self.disconnected = True


class _FakeStdin:
    def __init__(self) -> None:
        self.eof_written = False

    def write_eof(self) -> None:
        self.eof_written = True


class _BlockingStdout:
    def __init__(self) -> None:
        self._ready = asyncio.Event()

    async def readline(self) -> bytes:
        await self._ready.wait()
        return b""

    def unblock(self) -> None:
        self._ready.set()


class _FakeRemoteProcess:
    def __init__(self, stdout: _BlockingStdout) -> None:
        self.stdin = _FakeStdin()
        self.stdout = stdout
        self.exit_status = None
        self.terminated = False
        self.wait_count = 0

    def terminate(self) -> None:
        self.terminated = True
        self.exit_status = 0

    def kill(self) -> None:
        self.terminated = True
        self.exit_status = 0

    async def wait(self) -> None:
        self.wait_count += 1


def _ctx(member: str = "claude-1") -> TeamRuntimeContext:
    return TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name=member,
        cli_agent="claude",
        team_spec=TeamSpec(team_name="ext_team", display_name="Ext", language="en"),
        db_config=MemoryDatabaseConfig(),
        messager_config=MessagerTransportConfig(backend="inprocess", team_name="ext_team"),
    )


@pytest.fixture
def fake_claude_sdk(monkeypatch):
    sdk_module = ModuleType("claude_agent_sdk")
    sdk_module.__version__ = _FakeClaudeSdk.__version__
    sdk_module.CLIConnectionError = _FakeClaudeSdk.CLIConnectionError
    sdk_module.ProcessError = _FakeClaudeSdk.ProcessError
    sdk_module.ClaudeAgentOptions = _FakeClaudeSdk.ClaudeAgentOptions
    sdk_module.ClaudeSDKClient = _FakeClaudeSdk.ClaudeSDKClient
    sdk_module.TextBlock = _FakeClaudeSdk.TextBlock
    sdk_module.ThinkingBlock = _FakeClaudeSdk.ThinkingBlock
    sdk_module.ToolUseBlock = _FakeClaudeSdk.ToolUseBlock
    sdk_module.ToolResultBlock = _FakeClaudeSdk.ToolResultBlock
    sdk_module.AssistantMessage = _FakeClaudeSdk.AssistantMessage
    sdk_module.UserMessage = _FakeClaudeSdk.UserMessage
    sdk_module.SystemMessage = _FakeClaudeSdk.SystemMessage
    sdk_module.ResultMessage = _FakeClaudeSdk.ResultMessage

    internal_module = ModuleType("claude_agent_sdk._internal")
    transport_module = ModuleType("claude_agent_sdk._internal.transport")
    subprocess_module = ModuleType("claude_agent_sdk._internal.transport.subprocess_cli")

    class _FakeSubprocessCLITransport:
        def __init__(self, *, prompt: Any, options: _FakeOptions) -> None:
            self._prompt = prompt
            self._options = options
            self._cli_path = str(options.cli_path) if options.cli_path else None
            self._cwd = str(options.cwd) if options.cwd else None
            self._process = None
            self._ready = False

        def _build_command(self) -> list[str]:
            if self._cli_path is None:
                raise RuntimeError("missing cli path")
            cmd = [self._cli_path, "--output-format", "stream-json", "--verbose"]
            if self._options.permission_mode:
                cmd.extend(["--permission-mode", self._options.permission_mode])
            return cmd

    subprocess_module.SubprocessCLITransport = _FakeSubprocessCLITransport
    asyncssh_module = ModuleType("asyncssh")

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk_module)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk._internal", internal_module)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk._internal.transport", transport_module)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk._internal.transport.subprocess_cli", subprocess_module)
    monkeypatch.setitem(sys.modules, "asyncssh", asyncssh_module)
    return sdk_module


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_cli_runtime_uses_claude_sdk_backend(fake_claude_sdk):
    token = set_session_id("sess-1")
    try:
        runtime = await spawn_mod.build_cli_runtime(
            _ctx(),
            mcp_server_command=("openjiuwen-team-mcp",),
            extra_env={"EXTRA": "1"},
            system_prompt="persona",
        )
    finally:
        reset_session_id(token)

    assert isinstance(runtime, ClaudeSdkRuntime)
    options = runtime._options
    assert options.permission_mode == "bypassPermissions"
    assert options.system_prompt == {"type": "preset", "append": "persona"}
    assert options.env["EXTRA"] == "1"
    assert "OPENJIUWEN_TEAM_JOIN" in options.env
    assert options.mcp_servers["openjiuwen-team"]["command"] == "openjiuwen-team-mcp"
    assert options.session_id == derive_claude_session_id(team_session_id="sess-1", member_name="claude-1")
    assert options.resume is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_cli_runtime_resumes_claude_sdk_session(fake_claude_sdk):
    token = set_session_id("session:with:colon")
    try:
        runtime = await spawn_mod.build_cli_runtime(
            _ctx(),
            mcp_server_command=("openjiuwen-team-mcp",),
            resume_external_backend=True,
        )
    finally:
        reset_session_id(token)

    options = runtime._options
    expected = derive_claude_session_id(team_session_id="session:with:colon", member_name="claude-1")
    assert options.session_id is None
    assert options.resume == expected


@pytest.mark.asyncio
@pytest.mark.level0
async def test_claude_sdk_runtime_emits_native_team_chunks(fake_claude_sdk):
    runtime = ClaudeSdkRuntime(member_name="claude-1", options=_FakeOptions())
    await runtime.start()

    try:
        chunks = [chunk async for chunk in runtime._drive({"query": "run"})]
    finally:
        await runtime.aclose()

    assert [chunk.type for chunk in chunks] == [
        "llm_output",
        "llm_reasoning",
        "tool_call",
        "tool_result",
        "tool_result",
    ]
    assert chunks[0].payload == {"content": "done", "result_type": "answer"}
    assert chunks[1].payload == {"content": "reasoning", "result_type": "answer"}
    assert chunks[2].payload == {
        "tool_name": "Read",
        "tool_args": {"file_path": "a.py"},
        "tool_call_id": "toolu_1",
    }
    assert chunks[3].payload == {
        "tool_name": "",
        "tool_args": "",
        "tool_result": "file body",
        "tool_call_id": "toolu_1",
    }
    assert chunks[4].payload == {
        "tool_name": "",
        "tool_args": "",
        "tool_result": {"ok": True},
        "tool_call_id": "toolu_2",
    }


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_cli_runtime_claude_rejects_command_override(fake_claude_sdk):
    with pytest.raises(BaseError):
        await spawn_mod.build_cli_runtime(_ctx(), command_override=("claude", "--version"))


@pytest.mark.asyncio
@pytest.mark.level0
async def test_build_cli_runtime_requires_session_context(fake_claude_sdk):
    with pytest.raises(BaseError):
        await spawn_mod.build_cli_runtime(_ctx())


@pytest.mark.level0
def test_claude_sdk_missing_dependency_reports_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)

    with pytest.raises(BaseError):
        spawn_mod.build_claude_runtime(
            member_name="claude-1",
            cwd=None,
            env={"OPENJIUWEN_TEAM_JOIN": "{}"},
            inject_mcp=True,
            mcp_server_name="openjiuwen-team",
            mcp_server_command=("openjiuwen-team-mcp",),
            system_prompt=None,
            ssh_transport=None,
            team_session_id="sess-1",
            resume_external_backend=False,
        )


@pytest.mark.level0
def test_claude_sdk_ssh_transport_builds_remote_claude_command(fake_claude_sdk):
    config = SshTransportConfig(host="127.0.0.1", username="u", password="pw")
    options = _FakeOptions(
        cwd="/remote/project",
        env={"OPENJIUWEN_TEAM_JOIN": "{}"},
        permission_mode="bypassPermissions",
    )
    transport = build_claude_sdk_ssh_transport(prompt=[], options=options, config=config)

    transport._cli_path = "claude"
    command = transport._build_command()

    assert command[0] == "claude"
    assert "--permission-mode" in command
    assert "bypassPermissions" in command


@pytest.mark.level0
def test_claude_sdk_ssh_transport_uses_options_cli_path(fake_claude_sdk):
    config = SshTransportConfig(host="127.0.0.1", username="u", password="pw")
    options = _FakeOptions(
        cli_path="/remote/bin/claude",
        env={"OPENJIUWEN_TEAM_JOIN": "{}"},
    )
    transport = build_claude_sdk_ssh_transport(prompt=[], options=options, config=config)

    transport._cli_path = str(options.cli_path)
    command = transport._build_command()

    assert command[0] == "/remote/bin/claude"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_claude_sdk_ssh_read_messages_survives_concurrent_close(fake_claude_sdk):
    config = SshTransportConfig(host="127.0.0.1", username="u", password="pw")
    options = _FakeOptions(env={"OPENJIUWEN_TEAM_JOIN": "{}"})
    transport = build_claude_sdk_ssh_transport(prompt=[], options=options, config=config)
    stdout = _BlockingStdout()
    process = _FakeRemoteProcess(stdout)
    transport._process = process
    transport._ready = True

    reader = transport.read_messages()
    read_task = asyncio.create_task(_collect_messages(reader))
    await asyncio.sleep(0)

    await transport.close()
    stdout.unblock()
    messages = await read_task

    assert messages == []
    assert transport._process is None
    assert process.stdin.eof_written
    assert process.terminated
    assert process.wait_count >= 1


async def _collect_messages(reader: Any) -> list[dict[str, Any]]:
    """Collect all messages from an SDK transport reader."""
    messages: list[dict[str, Any]] = []
    async for message in reader:
        messages.append(message)
    return messages
