# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""MemberRuntime implementation backed by Claude Agent SDK."""

from __future__ import annotations

from typing import Any, AsyncIterator

from openjiuwen.agent_teams.external.cli_agent.claude.options import build_claude_options, load_claude_sdk
from openjiuwen.agent_teams.external.cli_agent.claude.ssh_transport import build_claude_sdk_ssh_transport
from openjiuwen.agent_teams.external.runtime import _CliRuntimeBase
from openjiuwen.agent_teams.schema.ssh_transport import SshTransportConfig
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.stream.base import OutputSchema


class ClaudeSdkRuntime(_CliRuntimeBase):
    """Drive a Claude Code member through Claude Agent SDK."""

    def __init__(
        self,
        *,
        member_name: str,
        options: Any,
        transport: Any | None = None,
    ):
        """Bind SDK options; the SDK client is connected on start."""
        super().__init__(member_name=member_name)
        self._options = options
        self._transport = transport
        self._client: Any | None = None
        self._abort_requested = False

    async def start(self, *, team_session: Any | None = None) -> None:
        """Start the SDK client and initialize Claude's streaming protocol."""
        await super().start(team_session=team_session)
        sdk = load_claude_sdk()
        self._client = sdk.ClaudeSDKClient(options=self._options, transport=self._transport)
        await self._client.connect()

    async def _drive(self, inputs: dict[str, Any]) -> AsyncIterator[Any]:
        client = self._client
        if client is None:
            sdk = load_claude_sdk()
            client = sdk.ClaudeSDKClient(options=self._options, transport=self._transport)
            self._client = client
            await client.connect()
        query = inputs.get("query")
        text = query if isinstance(query, str) else str(query)
        self._abort_requested = False
        await client.query(text)
        chunk_index = 0
        async for message in client.receive_response():
            if self._abort_requested:
                team_logger.debug("[{}] claude sdk turn aborted", self._member_name)
                return
            for chunk in _iter_sdk_chunks(message, chunk_index):
                team_logger.debug("[{}] claude sdk chunk type={}", self._member_name, chunk.type)
                yield chunk
                chunk_index = chunk.index + 1

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
            return
        client = self._client
        self._client = None
        await client.disconnect()


def build_claude_runtime(
    *,
    member_name: str,
    cwd: str | None,
    env: dict[str, str],
    inject_mcp: bool,
    mcp_server_name: str,
    mcp_server_command: tuple[str, ...],
    system_prompt: str | None,
    ssh_transport: SshTransportConfig | None,
) -> ClaudeSdkRuntime:
    """Build a Claude SDK runtime, using an SSH SDK transport when configured."""
    options = build_claude_options(
        cwd=cwd,
        env=env,
        inject_mcp=inject_mcp,
        mcp_server_name=mcp_server_name,
        mcp_server_command=mcp_server_command,
        system_prompt=system_prompt,
    )
    transport = None
    if ssh_transport is not None:
        team_logger.info("[external-cli] using claude sdk ssh transport for member {}", member_name)
        transport = build_claude_sdk_ssh_transport(prompt=_empty_prompt(), options=options, config=ssh_transport)
    return ClaudeSdkRuntime(member_name=member_name, options=options, transport=transport)


async def _empty_prompt() -> AsyncIterator[dict[str, Any]]:
    """Provide an empty streaming prompt for SDK transport construction."""
    return
    yield {}  # type: ignore[unreachable]


def _iter_sdk_chunks(message: Any, start_index: int) -> list[OutputSchema]:
    """Convert one Claude SDK message into native team stream chunks."""
    sdk = load_claude_sdk()
    if isinstance(message, sdk.AssistantMessage):
        return _assistant_chunks(message.content, start_index)
    if isinstance(message, sdk.UserMessage):
        return _user_chunks(message, start_index)
    if isinstance(message, sdk.SystemMessage) or isinstance(message, sdk.ResultMessage):
        return []
    return []


def _assistant_chunks(content: Any, start_index: int) -> list[OutputSchema]:
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
            chunks.append(
                OutputSchema(
                    type="tool_call",
                    index=index,
                    payload={
                        "tool_name": block.name,
                        "tool_args": block.input,
                        "tool_call_id": block.id,
                    },
                ),
            )
            index += 1
    return chunks


def _user_chunks(message: Any, start_index: int) -> list[OutputSchema]:
    """Convert user-side tool results into stream chunks without replaying text."""
    chunks: list[OutputSchema] = []
    index = start_index
    content_chunks = _tool_result_content_chunks(message.content, index)
    chunks.extend(content_chunks)
    index += len(content_chunks)
    if message.tool_use_result is not None:
        chunks.append(
            OutputSchema(
                type="tool_result",
                index=index,
                payload={
                    "tool_name": "",
                    "tool_args": "",
                    "tool_result": message.tool_use_result,
                    "tool_call_id": message.parent_tool_use_id or "",
                },
            ),
        )
    return chunks


def _tool_result_content_chunks(content: Any, start_index: int) -> list[OutputSchema]:
    """Convert tool result content blocks into stream chunks."""
    if not isinstance(content, list):
        return []
    sdk = load_claude_sdk()
    chunks: list[OutputSchema] = []
    index = start_index
    for block in content:
        if isinstance(block, sdk.ToolResultBlock):
            chunks.append(
                OutputSchema(
                    type="tool_result",
                    index=index,
                    payload={
                        "tool_name": "",
                        "tool_args": "",
                        "tool_result": block.content,
                        "tool_call_id": block.tool_use_id,
                    },
                ),
            )
            index += 1
    return chunks


def _text_chunk(chunk_type: str, content: str, index: int) -> OutputSchema:
    """Build a text-like stream chunk."""
    return OutputSchema(
        type=chunk_type,
        index=index,
        payload={"content": content, "result_type": "answer"},
    )


__all__ = ["ClaudeSdkRuntime", "build_claude_runtime"]
