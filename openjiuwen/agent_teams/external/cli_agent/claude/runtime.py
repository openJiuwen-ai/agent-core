# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""MemberRuntime implementation backed by Claude Agent SDK."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import nullcontext
from dataclasses import dataclass
import json
from typing import Any, AsyncIterator, ContextManager

from openjiuwen.agent_teams.external.cli_agent.claude.options import build_claude_options, load_claude_sdk
from openjiuwen.agent_teams.external.cli_agent.claude.sdk_mcp import (
    ClaudeSdkMcpToolSet,
    build_claude_sdk_mcp_tool_set,
)
from openjiuwen.agent_teams.external.cli_agent.claude.ssh_transport import build_claude_sdk_ssh_transport
from openjiuwen.agent_teams.external.runtime import CliRuntimeBase
from openjiuwen.agent_teams.schema.ssh_transport import SshTransportConfig
from openjiuwen.agent_teams.schema.team import ExternalCliModelConfig
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.stream.base import OutputSchema


@dataclass(frozen=True, slots=True)
class _ClaudeToolMetadata:
    """Metadata retained until the matching Claude tool result arrives."""

    tool_name: str = ""
    is_team_tool: bool = False


class _ClaudeStderrTail:
    """Capture a bounded tail of Claude CLI stderr for crash diagnostics."""

    def __init__(self, *, max_lines: int = 40, max_line_chars: int = 2000, max_chars: int = 8000) -> None:
        """Initialize the bounded stderr tail collector."""
        self._max_line_chars = max_line_chars
        self._max_chars = max_chars
        self._lines: deque[str] = deque(maxlen=max_lines)

    def append(self, line: str) -> None:
        """Append one stderr line after trimming excessive content."""
        text = str(line).strip()
        if not text:
            return
        if len(text) > self._max_line_chars:
            text = text[: self._max_line_chars] + "...[truncated]"
        self._lines.append(text)
        while len(self.render()) > self._max_chars and self._lines:
            self._lines.popleft()

    def render(self) -> str:
        """Return the currently captured stderr tail."""
        return "\n".join(self._lines)


class ClaudeSdkRuntime(CliRuntimeBase):
    """Drive a Claude Code member through Claude Agent SDK."""

    def __init__(
        self,
        *,
        member_name: str,
        options: Any,
        transport: Any | None = None,
        inject_mcp: bool = True,
        mcp_server_name: str = "openjiuwen-team",
        member_agent_id: str | None = None,
        team_context_tracker: Any = None,
        span_bridge: Any | None = None,
    ):
        """Bind SDK options; the SDK client is connected on start."""
        super().__init__(
            member_name=member_name,
            member_agent_id=member_agent_id,
            team_context_tracker=team_context_tracker,
        )
        self._options = options
        self._transport = transport
        self._inject_mcp = inject_mcp
        self._mcp_server_name = mcp_server_name
        self._sdk_mcp_tool_set: ClaudeSdkMcpToolSet | None = None
        self._client: Any | None = None
        self._abort_requested = False
        self._tool_metadata_by_id: dict[str, _ClaudeToolMetadata] = {}
        self._span_bridge = span_bridge or _NoopClaudeSpanBridge()
        self._stderr_tail = _ClaudeStderrTail()
        self._install_stderr_callback()

    def _install_stderr_callback(self) -> None:
        """Attach a Claude SDK stderr callback without dropping a caller callback."""
        original = getattr(self._options, "stderr", None)

        def _capture_stderr(line: str) -> None:
            self._stderr_tail.append(line)
            if callable(original):
                original(line)

        self._options.stderr = _capture_stderr

    async def _connect_client(self, client: Any) -> None:
        """Connect a Claude SDK client and log stderr tail on failure."""
        try:
            await client.connect()
        except Exception:
            stderr_tail = self._stderr_tail.render()
            if stderr_tail:
                team_logger.error(
                    "[{}] Claude CLI stderr before connect failure:\n{}",
                    self._member_name,
                    stderr_tail,
                )
            raise

    def bind_team_tools(
        self,
        *,
        team_backend: Any,
        role: str,
        teammate_mode: str,
        dispatch_mode: str,
        lifecycle: str,
        language: str,
        workspace_manager: Any = None,
        on_teammate_created: Any = None,
        model_config_allocator: Any = None,
        parent_agent: Any = None,
        messager: Any = None,
        team_name: str = "default",
        swarmflow_model_resolver: Any = None,
        swarmflow_worker_base_spec: Any = None,
        swarmflow_human_base_spec: Any = None,
        concurrency_governor: Any = None,
        swarmflow_budget: Any = None,
        team_permissions_enabled: bool = False,
    ) -> None:
        """Bind team tools to the owning TeamAgent shell."""
        if not self._inject_mcp:
            return
        if self._sdk_mcp_tool_set is not None:
            return
        tool_set = build_claude_sdk_mcp_tool_set(
            server_name=self._mcp_server_name,
            team_backend=team_backend,
            role=role,
            teammate_mode=teammate_mode,
            dispatch_mode=dispatch_mode,
            lifecycle=lifecycle,
            language=language,
            workspace_manager=workspace_manager,
            on_teammate_created=on_teammate_created,
            model_config_allocator=model_config_allocator,
            parent_agent=parent_agent,
            messager=messager,
            team_name=team_name,
            swarmflow_model_resolver=swarmflow_model_resolver,
            swarmflow_worker_base_spec=swarmflow_worker_base_spec,
            swarmflow_human_base_spec=swarmflow_human_base_spec,
            concurrency_governor=concurrency_governor,
            swarmflow_budget=swarmflow_budget,
            team_permissions_enabled=team_permissions_enabled,
            span_bridge=self._span_bridge,
        )
        self._sdk_mcp_tool_set = tool_set
        self._options.mcp_servers = {self._mcp_server_name: tool_set.server}

    async def start(self, *, team_session: Any | None = None) -> None:
        """Start the SDK client and initialize Claude's streaming protocol."""
        await super().start(team_session=team_session)
        if self._inject_mcp and self._sdk_mcp_tool_set is None:
            team_logger.warning("[{}] Claude SDK MCP is enabled but no team tools were bound", self._member_name)
        sdk = load_claude_sdk()
        self._client = sdk.ClaudeSDKClient(options=self._options, transport=self._transport)
        await self._connect_client(self._client)

    async def _drive(self, inputs: dict[str, Any]) -> AsyncIterator[Any]:
        client = self._client
        if client is None:
            sdk = load_claude_sdk()
            client = sdk.ClaudeSDKClient(options=self._options, transport=self._transport)
            self._client = client
            await self._connect_client(client)
        query = inputs.get("query")
        text = query if isinstance(query, str) else str(query)
        self._abort_requested = False
        self._tool_metadata_by_id.clear()
        self._span_bridge.start_turn(prompt=text)
        status = "ok"
        error: BaseException | None = None
        try:
            await client.query(text)
            chunk_index = 0
            async for message in client.receive_response():
                if self._abort_requested:
                    team_logger.debug("[{}] claude sdk turn aborted", self._member_name)
                    status = "cancelled"
                    return
                for chunk in _iter_sdk_chunks(
                    message,
                    chunk_index,
                    self._tool_metadata_by_id,
                    mcp_server_name=self._mcp_server_name,
                    team_tool_names=set(self._sdk_mcp_tool_set.tools) if self._sdk_mcp_tool_set is not None else set(),
                ):
                    team_logger.debug("[{}] claude sdk chunk type={}", self._member_name, chunk.type)
                    self._span_bridge.record_chunk(chunk)
                    yield chunk
                    chunk_index = chunk.index + 1
            if self._abort_requested:
                status = "cancelled"
        except GeneratorExit:
            status = "cancelled"
            raise
        except BaseException as exc:
            error = exc
            if self._abort_requested or isinstance(exc, asyncio.CancelledError):
                status = "cancelled"
            else:
                status = "failed"
            raise
        finally:
            self._span_bridge.finish_turn(status=status, error=error)

    async def steer(self, content: str) -> None:
        """Send content into the active Claude SDK conversation."""
        if self._client is None:
            return
        await self._client.query(content)

    async def follow_up(self, content: str) -> None:
        """Send follow-up content into the active Claude SDK conversation."""
        await self.steer(content)

    async def _abort_turn(self) -> None:
        """Interrupt the in-flight Claude turn if the SDK client is connected."""
        self._abort_requested = True
        if self._client is not None:
            await self._client.interrupt()

    async def aclose(self) -> None:
        """Disconnect the SDK client. Idempotent."""
        if self._client is None:
            self._sdk_mcp_tool_set = None
            return
        client = self._client
        self._client = None
        try:
            await client.disconnect()
        finally:
            self._sdk_mcp_tool_set = None


def build_claude_runtime(
    *,
    member_name: str,
    cwd: str | None,
    add_dirs: tuple[str, ...],
    env: dict[str, str],
    cli_path: str | None = None,
    external_model_config: ExternalCliModelConfig | None = None,
    inject_mcp: bool,
    mcp_server_name: str,
    mcp_server_command: tuple[str, ...],
    system_prompt: str | None,
    ssh_transport: SshTransportConfig | None,
    team_session_id: str | None,
    resume_external_backend: bool,
    member_agent_id: str | None = None,
    team_context_tracker: Any = None,
    team_name: str | None = None,
    role: str | None = None,
) -> ClaudeSdkRuntime:
    """Build a Claude SDK runtime, using an SSH SDK transport when configured."""
    _ = mcp_server_command
    options = build_claude_options(
        cwd=cwd,
        add_dirs=add_dirs,
        env=env,
        cli_path=cli_path,
        external_model_config=external_model_config,
        system_prompt=system_prompt,
        team_session_id=team_session_id,
        member_name=member_name,
        resume_external_backend=resume_external_backend,
    )
    transport = None
    if ssh_transport is not None:
        team_logger.info("[external-cli] using claude sdk ssh transport for member {}", member_name)
        transport = build_claude_sdk_ssh_transport(prompt=_empty_prompt(), options=options, config=ssh_transport)
    span_bridge = _build_claude_span_bridge(
        member_name=member_name,
        member_agent_id=member_agent_id,
        team_name=team_name,
        session_id=team_session_id,
        role=role,
    )
    return ClaudeSdkRuntime(
        member_name=member_name,
        options=options,
        transport=transport,
        inject_mcp=inject_mcp,
        mcp_server_name=mcp_server_name,
        member_agent_id=member_agent_id,
        team_context_tracker=team_context_tracker,
        span_bridge=span_bridge,
    )


async def _empty_prompt() -> AsyncIterator[dict[str, Any]]:
    """Provide an empty streaming prompt for SDK transport construction."""
    return
    yield {}  # type: ignore[unreachable]


class _NoopClaudeSpanBridge:
    """Runtime-local no-op bridge for optional observability dependencies."""

    @staticmethod
    def start_turn(**_: Any) -> None:
        """Ignore turn start."""

    @staticmethod
    def record_chunk(_: OutputSchema) -> None:
        """Ignore one Claude stream chunk."""

    @staticmethod
    def finish_turn(*, status: str, error: Any | None = None) -> None:
        """Ignore turn completion."""

    @staticmethod
    def tool_execution_context() -> ContextManager[None]:
        """Return a no-op context for local tool execution."""
        return nullcontext()


def _build_claude_span_bridge(
    *,
    member_name: str,
    member_agent_id: str | None,
    team_name: str | None,
    session_id: str | None,
    role: str | None = None,
) -> Any:
    """Build the optional Claude OTel bridge without making runtime import depend on OTel."""
    try:
        from openjiuwen.agent_teams.observability.setup import is_initialized
    except ImportError:
        return _NoopClaudeSpanBridge()
    if not is_initialized():
        return _NoopClaudeSpanBridge()
    try:
        from openjiuwen.agent_teams.observability.claude import ClaudeSpanBridge
    except ImportError as exc:
        team_logger.warning("[{}] Claude observability bridge unavailable: {}", member_name, exc)
        return _NoopClaudeSpanBridge()
    return ClaudeSpanBridge.build(
        member_name=member_name,
        member_agent_id=member_agent_id,
        team_name=team_name,
        session_id=session_id,
        role=role,
    )


def _iter_sdk_chunks(
    message: Any,
    start_index: int,
    tool_metadata_by_id: dict[str, _ClaudeToolMetadata],
    *,
    mcp_server_name: str,
    team_tool_names: set[str],
) -> list[OutputSchema]:
    """Convert one Claude SDK message into native team stream chunks."""
    sdk = load_claude_sdk()
    if isinstance(message, sdk.AssistantMessage):
        return _assistant_chunks(
            message.content,
            start_index,
            tool_metadata_by_id,
            mcp_server_name=mcp_server_name,
            team_tool_names=team_tool_names,
        )
    if isinstance(message, sdk.UserMessage):
        return _user_chunks(message, start_index, tool_metadata_by_id)
    if isinstance(message, sdk.SystemMessage) or isinstance(message, sdk.ResultMessage):
        return []
    return []


def _assistant_chunks(
    content: Any,
    start_index: int,
    tool_metadata_by_id: dict[str, _ClaudeToolMetadata],
    *,
    mcp_server_name: str,
    team_tool_names: set[str],
) -> list[OutputSchema]:
    """Convert assistant content blocks into stream chunks."""
    if not isinstance(content, list):
        return []
    sdk = load_claude_sdk()
    chunks: list[OutputSchema] = []
    index = start_index
    for block in content:
        if isinstance(block, sdk.TextBlock):
            if block.text:
                chunks.append(_text_chunk("llm_output", block.text, index))
                index += 1
        elif isinstance(block, sdk.ThinkingBlock):
            if block.thinking:
                chunks.append(_text_chunk("llm_reasoning", block.thinking, index))
                index += 1
        elif isinstance(block, sdk.ToolUseBlock):
            is_team_tool = _is_team_mcp_tool(
                block.name,
                mcp_server_name=mcp_server_name,
                team_tool_names=team_tool_names,
            )
            if block.id:
                tool_metadata_by_id[block.id] = _ClaudeToolMetadata(
                    tool_name=block.name,
                    is_team_tool=is_team_tool,
                )
            chunks.append(
                OutputSchema(
                    type="tool_call",
                    index=index,
                    payload={
                        "name": block.name,
                        "arguments": _json_arguments(block.input),
                        "tool_call_id": block.id,
                        "is_team_tool": is_team_tool,
                    },
                ),
            )
            index += 1
    return chunks


def _user_chunks(
    message: Any,
    start_index: int,
    tool_metadata_by_id: dict[str, _ClaudeToolMetadata],
) -> list[OutputSchema]:
    """Convert user-side tool results into stream chunks without replaying text."""
    chunks: list[OutputSchema] = []
    index = start_index
    content_chunks = _tool_result_content_chunks(message.content, index, tool_metadata_by_id)
    chunks.extend(content_chunks)
    index += len(content_chunks)
    if not content_chunks and message.tool_use_result is not None:
        tool_call_id = message.parent_tool_use_id or ""
        tool_metadata = _pop_tool_metadata(tool_metadata_by_id, tool_call_id)
        chunks.append(
            OutputSchema(
                type="tool_result",
                index=index,
                payload={
                    "tool_name": tool_metadata.tool_name,
                    "result": _normalize_tool_result(message.tool_use_result),
                    "tool_call_id": tool_call_id,
                    "is_team_tool": tool_metadata.is_team_tool,
                },
            ),
        )
    return chunks


def _tool_result_content_chunks(
    content: Any,
    start_index: int,
    tool_metadata_by_id: dict[str, _ClaudeToolMetadata],
) -> list[OutputSchema]:
    """Convert tool result content blocks into stream chunks."""
    if not isinstance(content, list):
        return []
    sdk = load_claude_sdk()
    chunks: list[OutputSchema] = []
    index = start_index
    for block in content:
        if isinstance(block, sdk.ToolResultBlock):
            tool_metadata = _pop_tool_metadata(tool_metadata_by_id, block.tool_use_id)
            chunks.append(
                OutputSchema(
                    type="tool_result",
                    index=index,
                    payload={
                        "tool_name": tool_metadata.tool_name,
                        "result": _normalize_tool_result(block.content),
                        "tool_call_id": block.tool_use_id,
                        "is_team_tool": tool_metadata.is_team_tool,
                    },
                ),
            )
            index += 1
    return chunks


def _pop_tool_metadata(
    tool_metadata_by_id: dict[str, _ClaudeToolMetadata],
    tool_call_id: str,
) -> _ClaudeToolMetadata:
    """Return and forget metadata matched by a Claude tool-use id."""
    if not tool_call_id:
        return _ClaudeToolMetadata()
    return tool_metadata_by_id.pop(tool_call_id, _ClaudeToolMetadata())


def _is_team_mcp_tool(tool_name: str, *, mcp_server_name: str, team_tool_names: set[str]) -> bool:
    """Return whether the SDK tool label belongs to the local team MCP server."""
    prefix = f"mcp__{mcp_server_name}__"
    if not tool_name.startswith(prefix):
        return False
    real_tool_name = tool_name[len(prefix):]
    return real_tool_name in team_tool_names


def _text_chunk(chunk_type: str, content: str, index: int) -> OutputSchema:
    """Build a text-like stream chunk."""
    return OutputSchema(
        type=chunk_type,
        index=index,
        payload={"content": content, "result_type": "answer"},
    )


def _json_arguments(value: Any) -> str:
    """Serialize external tool arguments into the native tool-call shape."""
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _normalize_tool_result(value: Any) -> Any:
    """Convert SDK text content blocks into the native string result shape."""
    if not isinstance(value, list):
        return value
    text_parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            return value
        if item.get("type") != "text":
            return value
        text = item.get("text")
        if not isinstance(text, str):
            return value
        text_parts.append(text)
    return "\n".join(text_parts)


__all__ = ["ClaudeSdkRuntime", "build_claude_runtime"]
