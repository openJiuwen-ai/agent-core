# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""MemberRuntime implementation backed by the Codex Python SDK."""

from __future__ import annotations

import asyncio
import contextlib
from enum import Enum
from typing import Any, AsyncIterator

from openjiuwen.agent_teams.external.cli_agent.codex.options import (
    build_codex_config,
    build_codex_thread_options,
    load_codex_sdk,
)
from openjiuwen.agent_teams.external.runtime import CliRuntimeBase
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.session.stream.base import OutputSchema

_INTERRUPT_TIMEOUT_S = 5.0
_DEFAULT_TURN_IDLE_TIMEOUT_S = 180.0
_DEFAULT_TURN_IDLE_RETRIES = 1
_NO_ACTIVE_TURN_ERROR_CODE = -32600
_NO_ACTIVE_TURN_ERROR_MESSAGE = "no active turn to steer"
_TOOL_ITEM_TYPES = {"commandExecution", "dynamicToolCall", "fileChange", "mcpToolCall"}
_REASONING_METHODS = {
    "item/reasoning/summaryTextDelta",
    "item/reasoning/textDelta",
}
_EXTERNAL_RUNTIME_STATE_KEY = "external_runtime"
_EXTERNAL_BACKEND_KEY = "backend"
_EXTERNAL_SESSION_ID_KEY = "external_session_id"
_CODEX_BACKEND = "codex"


class _CodexTurnIdleTimeout(RuntimeError):
    """Signal that one Codex turn stopped producing SDK notifications."""

    def __init__(
        self,
        *,
        member_name: str,
        timeout_s: float,
        notifications_seen: int,
        interrupted: bool,
    ) -> None:
        super().__init__(
            f"Codex SDK member {member_name!r} produced no turn events for "
            f"{timeout_s:g}s; interrupt_succeeded={interrupted}",
        )
        self.notifications_seen = notifications_seen
        self.interrupted = interrupted


class CodexSdkRuntime(CliRuntimeBase):
    """Keep one SDK client and one isolated Codex thread per Jiuwen member."""

    def __init__(
        self,
        *,
        member_name: str,
        member_agent_id: str,
        sdk: Any,
        config: Any,
        thread_options: dict[str, Any],
        resume_external_backend: bool = False,
        turn_idle_timeout_s: float = _DEFAULT_TURN_IDLE_TIMEOUT_S,
        turn_idle_retries: int = _DEFAULT_TURN_IDLE_RETRIES,
    ) -> None:
        super().__init__(member_name=member_name)
        if turn_idle_timeout_s <= 0:
            raise ValueError("turn_idle_timeout_s must be greater than zero")
        if turn_idle_retries < 0:
            raise ValueError("turn_idle_retries must be non-negative")
        self._member_agent_id = member_agent_id
        self._sdk = sdk
        self._config = config
        self._thread_options = dict(thread_options)
        self._resume_external_backend = resume_external_backend
        self._turn_idle_timeout_s = turn_idle_timeout_s
        self._turn_idle_retries = turn_idle_retries
        self._thread_id: str | None = None
        self._persisted_thread_id: str | None = None
        self._member_session: Any | None = None
        self._client: Any | None = None
        self._thread: Any | None = None
        self._active_turn: Any | None = None
        self._pending: list[str] = []
        self._aborted = False
        self._close_lock = asyncio.Lock()

    @property
    def session_id(self) -> str | None:
        """Return the Codex thread id once the SDK thread is available."""
        return self._thread_id

    async def start(self, *, team_session: Any | None = None) -> None:
        """Restore the member checkpoint, then create or resume its SDK thread."""
        await super().start(team_session=team_session)
        await self._ensure_member_session(team_session)
        await self._ensure_thread()

    async def _ensure_member_session(self, team_session: Any | None) -> Any:
        """Open this external member's stable child AgentSession once."""
        if self._member_session is not None:
            return self._member_session
        if team_session is None:
            raise RuntimeError(
                f"Codex SDK member {self._member_name!r} requires a team_session to restore its member checkpoint",
            )

        member_session = team_session.create_agent_session(
            agent_id=self._member_agent_id,
            share_stream_writer=False,
        )
        await member_session.pre_run()
        self._member_session = member_session

        restored_thread_id = self._read_persisted_thread_id(member_session)
        self._persisted_thread_id = restored_thread_id
        if self._resume_external_backend:
            if restored_thread_id is None:
                raise RuntimeError(
                    f"cannot resume Codex member {self._member_name!r} without a saved "
                    "external_session_id in its member checkpoint; strict resume "
                    "forbids starting a replacement thread",
                )
            self._thread_id = restored_thread_id
        return member_session

    @staticmethod
    def _read_persisted_thread_id(member_session: Any) -> str | None:
        """Read a Codex thread id from one member AgentSession state."""
        state = member_session.get_state(_EXTERNAL_RUNTIME_STATE_KEY)
        if not isinstance(state, dict) or state.get(_EXTERNAL_BACKEND_KEY) != _CODEX_BACKEND:
            return None
        value = state.get(_EXTERNAL_SESSION_ID_KEY)
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    async def _persist_thread_id(self) -> None:
        """Commit a changed Codex thread id to this member's checkpoint."""
        thread_id = self._thread_id
        if not thread_id or thread_id == self._persisted_thread_id:
            return
        member_session = self._member_session
        if member_session is None:
            raise RuntimeError(
                f"cannot persist Codex thread for {self._member_name!r} without a member session",
            )
        member_session.update_state(
            {
                _EXTERNAL_RUNTIME_STATE_KEY: {
                    _EXTERNAL_BACKEND_KEY: _CODEX_BACKEND,
                    _EXTERNAL_SESSION_ID_KEY: thread_id,
                }
            }
        )
        await member_session.commit()
        self._persisted_thread_id = thread_id

    async def _ensure_thread(self) -> Any:
        """Lazily initialize ``AsyncCodex`` and this runtime's single thread."""
        if self._thread is not None:
            await self._persist_thread_id()
            return self._thread
        if self._client is None:
            self._client = self._sdk.AsyncCodex(config=self._config)

        options = dict(self._thread_options)
        if self._thread_id:
            requested_thread_id = self._thread_id
            options.pop("ephemeral", None)
            try:
                resumed_thread = await self._client.thread_resume(requested_thread_id, **options)
            except Exception as exc:  # noqa: BLE001 - SDK errors are optional dependency types
                raise RuntimeError(
                    f"failed to resume Codex SDK thread {requested_thread_id!r}; "
                    "strict resume forbids starting a replacement thread",
                ) from exc
            resumed_thread_id = getattr(resumed_thread, "id", None)
            if resumed_thread_id != requested_thread_id:
                raise RuntimeError(
                    f"Codex SDK resumed unexpected thread {resumed_thread_id!r}; expected {requested_thread_id!r}",
                )
            self._thread = resumed_thread
            activation = "resumed"
        else:
            self._thread = await self._client.thread_start(**options)
            activation = "started"
        self._thread_id = self._thread.id
        await self._persist_thread_id()
        team_logger.info(
            "[external-cli] member {} {} codex SDK thread {}",
            self._member_name,
            activation,
            self._thread_id,
        )
        return self._thread

    async def stop(self) -> None:
        """Stop Codex and finalize this member's child AgentSession once."""
        try:
            await super().stop()
        finally:
            member_session = self._member_session
            if member_session is not None:
                await member_session.post_run()
                if self._member_session is member_session:
                    self._member_session = None

    async def _drive(self, inputs: dict[str, Any]) -> AsyncIterator[Any]:
        """Run queued messages as SDK turns on this member's one thread."""
        query = inputs.get("query")
        prompt: str | None = query if isinstance(query, str) else str(query)
        self._aborted = False
        chunk_index = 0
        thread = await self._ensure_thread()
        while prompt is not None and not self._aborted:
            idle_retries = 0
            while True:
                try:
                    async for chunk in self._run_turn(thread, prompt, chunk_index):
                        yield chunk
                        chunk_index = chunk.index + 1
                except _CodexTurnIdleTimeout as exc:
                    can_retry = (
                        idle_retries < self._turn_idle_retries
                        and exc.notifications_seen == 0
                        and exc.interrupted
                        and not self._aborted
                    )
                    if not can_retry:
                        raise
                    idle_retries += 1
                    team_logger.warning(
                        "[external-cli] member {} codex SDK turn was silent for {}s; "
                        "retrying prompt on the same thread ({}/{})",
                        self._member_name,
                        self._turn_idle_timeout_s,
                        idle_retries,
                        self._turn_idle_retries,
                    )
                    continue
                break
            prompt = None if self._aborted else self._drain_pending()

    async def _run_turn(
        self,
        thread: Any,
        prompt: str,
        start_index: int,
    ) -> AsyncIterator[OutputSchema]:
        """Start one SDK turn and convert its typed notification stream."""
        handle = await thread.turn(prompt)
        self._active_turn = handle
        if self._aborted:
            await self._interrupt_handle(handle)
        index = start_index
        notifications_seen = 0
        try:
            stream = handle.stream().__aiter__()
            while True:
                try:
                    notification = await asyncio.wait_for(
                        anext(stream),
                        timeout=self._turn_idle_timeout_s,
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as exc:
                    interrupted = await self._interrupt_handle(handle)
                    raise _CodexTurnIdleTimeout(
                        member_name=self._member_name,
                        timeout_s=self._turn_idle_timeout_s,
                        notifications_seen=notifications_seen,
                        interrupted=interrupted,
                    ) from exc
                notifications_seen += 1
                chunks = _notification_chunks(notification, index)
                for chunk in chunks:
                    team_logger.debug(
                        "[{}] codex SDK chunk type={}",
                        self._member_name,
                        chunk.type,
                    )
                    yield chunk
                    index = chunk.index + 1
        finally:
            if self._active_turn is handle:
                self._active_turn = None

    def _drain_pending(self) -> str | None:
        """Combine ordinary messages queued while a turn was running."""
        if not self._pending:
            return None
        combined = self._pending[0] if len(self._pending) == 1 else "\n\n---\n\n".join(self._pending)
        self._pending = []
        return combined

    async def steer(self, content: str) -> None:
        """Steer the active SDK turn, or queue while turn creation is racing."""
        handle = self._active_turn
        if handle is None:
            self._pending.append(content)
            return
        try:
            await handle.steer(content)
        except Exception as exc:  # noqa: BLE001 - the optional SDK error type is loaded lazily
            if not _is_no_active_turn_to_steer(exc):
                raise
            # The app-server is authoritative: it may finish the turn before
            # the terminal stream notification reaches this runtime and clears
            # ``_active_turn``. Preserve the input as the next turn on the same
            # thread instead of dropping it at that completion boundary.
            if self._active_turn is handle:
                self._active_turn = None
            self._pending.append(content)
            team_logger.info(
                "[external-cli] member {} codex SDK turn ended before steer; queued input for the next turn",
                self._member_name,
            )

    async def follow_up(self, content: str) -> None:
        """Queue an ordinary message as the next turn on the same thread."""
        self._pending.append(content)

    async def _interrupt_handle(self, handle: Any) -> bool:
        """Interrupt one SDK turn with a bounded wait."""
        try:
            await asyncio.wait_for(handle.interrupt(), timeout=_INTERRUPT_TIMEOUT_S)
            return True
        except Exception as exc:  # noqa: BLE001 - shutdown still closes the SDK client
            team_logger.warning(
                "[external-cli] member {} codex SDK interrupt failed: {}",
                self._member_name,
                exc,
            )
            return False

    async def _abort_turn(self) -> None:
        """Interrupt the active SDK turn and discard queued follow-ups."""
        self._aborted = True
        self._pending.clear()
        handle = self._active_turn
        if handle is not None:
            await self._interrupt_handle(handle)
        if self._phase is HarnessState.TERMINATED:
            task = self._turn_task
            if task is not None and task is not asyncio.current_task() and not task.done():
                task.cancel()

    async def aclose(self) -> None:
        """Interrupt the active turn and close ``AsyncCodex`` idempotently."""
        async with self._close_lock:
            client = self._client
            if client is None:
                return
            handle = self._active_turn
            self._active_turn = None
            if handle is not None:
                await self._interrupt_handle(handle)
            self._thread = None
            self._client = None
            with contextlib.suppress(Exception):
                await client.close()


def _is_no_active_turn_to_steer(exc: Exception) -> bool:
    """Return whether Codex rejected steer because its turn already ended."""
    message = getattr(exc, "message", None)
    return (
        getattr(exc, "code", None) == _NO_ACTIVE_TURN_ERROR_CODE
        and isinstance(message, str)
        and _NO_ACTIVE_TURN_ERROR_MESSAGE in message.lower()
    )


def _notification_chunks(notification: Any, start_index: int) -> list[OutputSchema]:
    """Convert one Codex SDK notification into native team stream chunks."""
    method = getattr(notification, "method", "")
    payload = getattr(notification, "payload", None)
    if method == "item/agentMessage/delta":
        return _delta_chunks("llm_output", payload, start_index)
    if method in _REASONING_METHODS:
        return _delta_chunks("llm_reasoning", payload, start_index)
    if method == "item/started":
        item = _thread_item(payload)
        if _item_type(item) in _TOOL_ITEM_TYPES:
            return [_tool_call_chunk(item, start_index)]
    if method == "item/completed":
        item = _thread_item(payload)
        if _item_type(item) in _TOOL_ITEM_TYPES:
            return [_tool_result_chunk(item, start_index)]
    if method == "error":
        if getattr(payload, "will_retry", False):
            team_logger.warning("Codex SDK turn error will retry: {}", _jsonable(getattr(payload, "error", None)))
            return []
        raise RuntimeError(f"codex SDK turn failed: {_jsonable(getattr(payload, 'error', None))}")
    if method == "turn/completed":
        turn = getattr(payload, "turn", None)
        if _enum_value(getattr(turn, "status", None)) == "failed":
            raise RuntimeError(f"codex SDK turn failed: {_jsonable(getattr(turn, 'error', None))}")
    return []


def _delta_chunks(chunk_type: str, payload: Any, index: int) -> list[OutputSchema]:
    """Convert a non-empty text delta to one stream chunk."""
    delta = getattr(payload, "delta", None)
    if not isinstance(delta, str) or not delta:
        return []
    return [
        OutputSchema(
            type=chunk_type,
            index=index,
            payload={"content": delta, "result_type": "answer"},
        )
    ]


def _tool_call_chunk(item: Any, index: int) -> OutputSchema:
    """Build a tool-call chunk from a started Codex thread item."""
    return OutputSchema(
        type="tool_call",
        index=index,
        payload={
            "tool_name": _tool_name(item),
            "tool_args": _tool_args(item),
            "tool_call_id": getattr(item, "id", ""),
        },
    )


def _tool_result_chunk(item: Any, index: int) -> OutputSchema:
    """Build a tool-result chunk from a completed Codex thread item."""
    return OutputSchema(
        type="tool_result",
        index=index,
        payload={
            "tool_name": _tool_name(item),
            "tool_args": _tool_args(item),
            "tool_result": _tool_result(item),
            "tool_call_id": getattr(item, "id", ""),
        },
    )


def _thread_item(payload: Any) -> Any:
    """Unwrap the SDK's ``ThreadItem`` root model."""
    item = getattr(payload, "item", None)
    return getattr(item, "root", item)


def _item_type(item: Any) -> str:
    return str(_enum_value(getattr(item, "type", "")) or "")


def _tool_name(item: Any) -> str:
    item_type = _item_type(item)
    if item_type == "mcpToolCall":
        return f"{getattr(item, 'server', '')}.{getattr(item, 'tool', '')}".strip(".")
    if item_type == "dynamicToolCall":
        return str(getattr(item, "tool", ""))
    if item_type == "commandExecution":
        return "shell"
    if item_type == "fileChange":
        return "apply_patch"
    return item_type


def _tool_args(item: Any) -> Any:
    item_type = _item_type(item)
    if item_type in {"dynamicToolCall", "mcpToolCall"}:
        return _jsonable(getattr(item, "arguments", None))
    if item_type == "commandExecution":
        return {"command": getattr(item, "command", ""), "cwd": getattr(item, "cwd", "")}
    if item_type == "fileChange":
        return {"changes": _jsonable(getattr(item, "changes", []))}
    return {}


def _tool_result(item: Any) -> Any:
    item_type = _item_type(item)
    if item_type == "mcpToolCall":
        result = getattr(item, "result", None)
        if result is None:
            result = getattr(item, "error", None)
        return _jsonable(result)
    if item_type == "dynamicToolCall":
        return _jsonable(getattr(item, "content_items", None))
    if item_type == "commandExecution":
        return {
            "exit_code": getattr(item, "exit_code", None),
            "output": getattr(item, "aggregated_output", None),
        }
    if item_type == "fileChange":
        return {"status": _enum_value(getattr(item, "status", None))}
    return None


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _jsonable(value: Any) -> Any:
    """Convert SDK Pydantic/enum values to stream-safe Python objects."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


async def build_codex_runtime(
    *,
    member_name: str,
    member_agent_id: str,
    cwd: str | None,
    env: dict[str, str],
    inject_mcp: bool,
    mcp_server_name: str,
    mcp_server_command: tuple[str, ...],
    mcp_default_tools_approval_mode: str | None,
    bypass_approvals_and_sandbox: bool,
    system_prompt: str | None,
    codex_bin: str | None,
    resume_external_backend: bool = False,
    turn_idle_timeout_s: float | None = None,
    turn_idle_retries: int | None = None,
) -> CodexSdkRuntime:
    """Build a Codex Python SDK runtime without starting its thread eagerly."""
    sdk = load_codex_sdk()
    config = build_codex_config(
        cwd=cwd,
        env=env,
        inject_mcp=inject_mcp,
        mcp_server_name=mcp_server_name,
        mcp_server_command=mcp_server_command,
        mcp_default_tools_approval_mode=mcp_default_tools_approval_mode,
        member_name=member_name,
        codex_bin=codex_bin,
        sdk=sdk,
    )
    thread_options = build_codex_thread_options(
        cwd=cwd,
        system_prompt=system_prompt,
        bypass_approvals_and_sandbox=bypass_approvals_and_sandbox,
        sdk=sdk,
    )
    return CodexSdkRuntime(
        member_name=member_name,
        member_agent_id=member_agent_id,
        sdk=sdk,
        config=config,
        thread_options=thread_options,
        resume_external_backend=resume_external_backend,
        turn_idle_timeout_s=(_DEFAULT_TURN_IDLE_TIMEOUT_S if turn_idle_timeout_s is None else turn_idle_timeout_s),
        turn_idle_retries=(_DEFAULT_TURN_IDLE_RETRIES if turn_idle_retries is None else turn_idle_retries),
    )


__all__ = ["CodexSdkRuntime", "build_codex_runtime"]
