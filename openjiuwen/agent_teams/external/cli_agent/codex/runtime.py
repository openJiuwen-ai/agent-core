# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""MemberRuntime implementation backed by the Codex Python SDK."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
from enum import Enum
from typing import Any, AsyncIterator

from openjiuwen.agent_teams.external.cli_agent.codex.options import (
    build_codex_config,
    build_codex_thread_options,
    load_codex_sdk,
)
from openjiuwen.agent_teams.external.runtime import CliRuntimeBase
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.schema.team import ExternalCliModelConfig
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


class _NoopCodexSpanBridge:
    """Keep Codex usable when the optional observability package is absent."""

    def start_turn(self, **_: Any) -> None:
        pass

    def append_output(self, _: str) -> None:
        pass

    def append_reasoning(self, _: str) -> None:
        pass

    def record_model_usage(self, **_: Any) -> None:
        pass

    def append_raw_response_item(self, _: Any) -> None:
        pass

    def complete_model_response(self, **_: Any) -> None:
        pass

    def enable_native_api_timing(self) -> None:
        pass

    def enable_native_model_spans(self) -> None:
        pass

    def enable_rollout_trace(self) -> None:
        pass

    @staticmethod
    def native_traceparent() -> str | None:
        return None

    def record_native_api_request(self, _: dict[str, Any]) -> None:
        pass

    def record_native_event(self, _: dict[str, Any]) -> None:
        pass

    def record_native_model_span(self, _: dict[str, Any]) -> None:
        pass

    def record_rollout_event(self, _: dict[str, Any]) -> None:
        pass

    async def wait_for_native_observations(self, **_: Any) -> None:
        pass

    def start_tool(self, **_: Any) -> None:
        pass

    def finish_tool(self, **_: Any) -> None:
        pass

    def record_error(self, _: Any, **__: Any) -> None:
        pass

    def finish_turn(self, **_: Any) -> None:
        pass


def _build_span_bridge(
    *,
    member_name: str,
    member_agent_id: str,
    team_name: str,
    session_id: str,
    role: str | None = None,
) -> Any:
    """Load the OTel bridge only when its optional dependencies are installed."""
    try:
        from openjiuwen.agent_teams.observability.codex import CodexSpanBridge
    except ImportError:
        return _NoopCodexSpanBridge()
    return CodexSpanBridge(
        member_name=member_name,
        member_agent_id=member_agent_id,
        team_name=team_name,
        session_id=session_id,
        role=role,
    )


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
        team_name: str,
        team_session_id: str,
        sdk: Any,
        config: Any,
        thread_options: dict[str, Any],
        resume_external_backend: bool = False,
        turn_idle_timeout_s: float = _DEFAULT_TURN_IDLE_TIMEOUT_S,
        turn_idle_retries: int = _DEFAULT_TURN_IDLE_RETRIES,
        team_context_tracker: Any = None,
        span_bridge: Any | None = None,
        native_otel_receiver: Any | None = None,
        rollout_trace_reader: Any | None = None,
    ) -> None:
        super().__init__(
            member_name=member_name,
            member_agent_id=member_agent_id,
            team_context_tracker=team_context_tracker,
        )
        if turn_idle_timeout_s <= 0:
            raise ValueError("turn_idle_timeout_s must be greater than zero")
        if turn_idle_retries < 0:
            raise ValueError("turn_idle_retries must be non-negative")
        self._member_agent_id = member_agent_id
        self._span_bridge = span_bridge or _build_span_bridge(
            member_name=member_name,
            member_agent_id=member_agent_id,
            team_name=team_name,
            session_id=team_session_id,
        )
        self._native_otel_receiver = native_otel_receiver
        self._rollout_trace_reader = rollout_trace_reader
        self._sdk = sdk
        self._config = config
        self._thread_options = dict(thread_options)
        self._resume_external_backend = resume_external_backend
        self._turn_idle_timeout_s = turn_idle_timeout_s
        self._turn_idle_retries = turn_idle_retries
        self._thread_id: str | None = None
        self._persisted_thread_id: str | None = None
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
        self._restore_thread_id()
        await self._ensure_thread()

    def _restore_thread_id(self) -> None:
        """Pick the Codex thread id back up from this member's checkpoint.

        The member AgentSession itself is opened by ``CliRuntimeBase.start``;
        Codex only reads its own slice back out. It is stricter than the base
        about that session existing, because without it a resume cannot tell an
        interrupted thread from a fresh one.
        """
        member_session = self._member_session
        if member_session is None:
            raise RuntimeError(
                f"Codex SDK member {self._member_name!r} requires a team_session to restore its member checkpoint",
            )
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
        config_env = getattr(self._config, "env", None)
        team_logger.info(
            "[external-cli] ensuring codex thread for member {} resume_thread={} cwd={} codex_bin={} "
            "team_join_env_present={} config_overrides={}",
            self._member_name,
            self._thread_id is not None,
            getattr(self._config, "cwd", None),
            getattr(self._config, "codex_bin", None),
            isinstance(config_env, dict) and "OPENJIUWEN_TEAM_JOIN" in config_env,
            getattr(self._config, "config_overrides", None),
        )
        if self._thread_id:
            requested_thread_id = self._thread_id
            options.pop("ephemeral", None)
            try:
                resumed_thread = await self._client.thread_resume(requested_thread_id, **options)
            except Exception as exc:  # noqa: BLE001 - SDK errors are optional dependency types
                team_logger.exception(
                    "[external-cli] failed to resume codex SDK thread {} for member {}",
                    requested_thread_id,
                    self._member_name,
                )
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
            try:
                self._thread = await _start_thread_with_raw_events(
                    client=self._client,
                    sdk=self._sdk,
                    options=options,
                )
            except Exception:
                team_logger.exception(
                    "[external-cli] failed to start codex SDK thread for member {}",
                    self._member_name,
                )
                raise
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
        self._span_bridge.start_turn(
            prompt=prompt,
            thread_id=self._thread_id,
            developer_instructions=self._thread_options.get("developer_instructions"),
            model=self._thread_options.get("model"),
        )
        try:
            handle = await thread.turn(prompt)
        except BaseException as exc:
            self._span_bridge.finish_turn(status="failed", error=exc)
            raise
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
                self._trace_notification(notification)
                chunks = _notification_chunks(notification, index)
                for chunk in chunks:
                    team_logger.debug(
                        "[{}] codex SDK chunk type={}",
                        self._member_name,
                        chunk.type,
                    )
                    yield chunk
                    index = chunk.index + 1
        except BaseException as exc:
            # A transport failure can race with the App Server's batched OTel
            # trace export. Preserve any native sampling spans that were
            # already completed instead of clearing the bridge immediately.
            with contextlib.suppress(Exception):
                await self._span_bridge.wait_for_native_observations()
            self._span_bridge.finish_turn(status="failed", error=exc)
            raise
        else:
            # Native sampling spans can arrive shortly after the SDK emits
            # ``turn/completed``. Give the batch span processor a brief flush
            # window; no SDK/native event pairing happens here.
            with contextlib.suppress(Exception):
                await self._span_bridge.wait_for_native_observations()
        finally:
            if self._active_turn is handle:
                self._active_turn = None
            self._span_bridge.finish_turn(
                status="cancelled" if self._aborted else "completed",
            )

    def _trace_notification(self, notification: Any) -> None:
        """Feed one typed SDK notification into the optional OTel span bridge."""
        method = getattr(notification, "method", "")
        payload = getattr(notification, "payload", None)
        if method == "rawResponseItem/completed":
            self._span_bridge.append_raw_response_item(
                _raw_notification_param(payload, "item"),
            )
            return
        if method == "rawResponse/completed":
            self._span_bridge.complete_model_response(
                response_id=_raw_notification_param(payload, "responseId"),
                usage=_raw_notification_param(payload, "usage"),
            )
            return
        if method == "item/agentMessage/delta":
            self._span_bridge.append_output(str(getattr(payload, "delta", "") or ""))
            return
        if method in _REASONING_METHODS:
            self._span_bridge.append_reasoning(str(getattr(payload, "delta", "") or ""))
            return
        if method == "thread/tokenUsage/updated":
            usage = getattr(payload, "token_usage", None)
            last = getattr(usage, "last", None)
            total = getattr(usage, "total", None)
            if last is not None:
                self._span_bridge.record_model_usage(
                    input_tokens=int(getattr(last, "input_tokens", 0) or 0),
                    cached_input_tokens=int(getattr(last, "cached_input_tokens", 0) or 0),
                    output_tokens=int(getattr(last, "output_tokens", 0) or 0),
                    reasoning_output_tokens=int(
                        getattr(last, "reasoning_output_tokens", 0) or 0,
                    ),
                    total_tokens=int(getattr(last, "total_tokens", 0) or 0),
                    thread_total_tokens=int(getattr(total, "total_tokens", 0) or 0),
                )
            return
        if method in {"item/started", "item/completed"}:
            item = _thread_item(payload)
            item_type = _item_type(item)
            if item_type not in _TOOL_ITEM_TYPES:
                return
            call_id = str(getattr(item, "id", "") or "")
            tool_name = _tool_name(item)
            tool_args = _tool_args(item)
            server_name = str(getattr(item, "server", "") or "") if item_type == "mcpToolCall" else None
            if method == "item/started":
                self._span_bridge.start_tool(
                    call_id=call_id,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    item_type=item_type,
                    server_name=server_name,
                )
                return
            item_error = getattr(item, "error", None)
            item_status = _enum_value(getattr(item, "status", None))
            if item_error is None and item_status in {"failed", "declined"}:
                item_error = {"status": item_status}
            self._span_bridge.finish_tool(
                call_id=call_id,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=_tool_result(item),
                item_type=item_type,
                server_name=server_name,
                error=_jsonable(item_error) if item_error is not None else None,
            )
            return
        if method == "error":
            self._span_bridge.record_error(
                _jsonable(getattr(payload, "error", None)),
                will_retry=bool(getattr(payload, "will_retry", False)),
            )
            return
        if method == "turn/completed":
            # Do not close the bridge here. The App Server's batched native
            # model span may not have reached the local receiver yet.
            return

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
            receiver = self._native_otel_receiver
            rollout_reader = self._rollout_trace_reader
            if client is None and receiver is None and rollout_reader is None:
                return
            self._native_otel_receiver = None
            self._rollout_trace_reader = None
            handle = self._active_turn
            self._active_turn = None
            if handle is not None:
                await self._interrupt_handle(handle)
            self._span_bridge.finish_turn(status="cancelled")
            self._thread = None
            self._client = None
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.close()
            if receiver is not None:
                with contextlib.suppress(Exception):
                    await receiver.aclose()
            if rollout_reader is not None:
                with contextlib.suppress(Exception):
                    await rollout_reader.aclose()


async def _start_thread_with_raw_events(
    *,
    client: Any,
    sdk: Any,
    options: dict[str, Any],
) -> Any:
    """Start a thread with App Server model-response notifications enabled.

    Newer SDKs may expose ``experimental_raw_events`` directly. The currently
    supported SDK can still send the App Server field through its low-level
    JSON-RPC client, so keep that compatibility code isolated here.
    """
    thread_start = client.thread_start
    signature = inspect.signature(thread_start)
    parameters = signature.parameters.values()
    accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters)
    if "experimental_raw_events" in signature.parameters or accepts_kwargs:
        return await thread_start(
            experimental_raw_events=True,
            **options,
        )

    ensure_initialized = getattr(client, "_ensure_initialized", None)
    low_level_client = getattr(client, "_client", None)
    async_thread_type = getattr(sdk, "AsyncThread", None)
    if not callable(ensure_initialized) or low_level_client is None or async_thread_type is None:
        team_logger.warning(
            "[external-cli] Codex SDK does not expose experimental raw events; "
            "observability will use one llm.call proxy per turn",
        )
        return await thread_start(**options)

    try:
        from openai_codex._approval_mode import _approval_mode_settings
        from openai_codex._sandbox import _sandbox_mode
        from openai_codex.generated.v2_all import ThreadStartParams

        wire_options = dict(options)
        approval_mode = wire_options.pop(
            "approval_mode",
            sdk.ApprovalMode.auto_review,
        )
        sandbox = wire_options.pop("sandbox", None)
        approval_policy, approvals_reviewer = _approval_mode_settings(approval_mode)
        params = ThreadStartParams(
            approval_policy=approval_policy,
            approvals_reviewer=approvals_reviewer,
            sandbox=_sandbox_mode(sandbox),
            **wire_options,
        )
        request = params.model_dump(
            by_alias=True,
            exclude_none=True,
            mode="json",
        )
        request["experimentalRawEvents"] = True
        await ensure_initialized()
        started = await low_level_client.thread_start(request)
        return async_thread_type(client, started.thread.id)
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        team_logger.warning(
            "[external-cli] Codex SDK raw-event compatibility path is unavailable ({}); "
            "observability will use one llm.call proxy per turn",
            exc,
        )
        return await thread_start(**options)


def _raw_notification_param(payload: Any, name: str) -> Any:
    """Read one field from an SDK raw-event payload or UnknownNotification."""
    params = getattr(payload, "params", payload)
    if isinstance(params, dict):
        return params.get(name)
    value = getattr(params, name, None)
    if value is not None:
        return value
    snake_name = "".join(f"_{char.lower()}" if char.isupper() else char for char in name)
    return getattr(params, snake_name, None)


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
            "name": _tool_name(item),
            "arguments": _json_arguments(_tool_args(item)),
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
            "result": _tool_result(item),
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
        return _normalize_tool_result(result)
    if item_type == "dynamicToolCall":
        return _normalize_tool_result(getattr(item, "content_items", None))
    if item_type == "commandExecution":
        output = getattr(item, "aggregated_output", None)
        if isinstance(output, str) and output:
            return output
        return f"exit_code={getattr(item, 'exit_code', None)}"
    if item_type == "fileChange":
        return _normalize_tool_result({"status": _enum_value(getattr(item, "status", None))})
    return None


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _jsonable(value: Any) -> Any:
    """Convert SDK Pydantic/enum values to stream-safe Python objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json", by_alias=True))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    try:
        json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)
    return value


def _json_arguments(value: Any) -> str:
    """Serialize external tool arguments into the native tool-call shape."""
    return json.dumps(_jsonable(value) if value is not None else {}, ensure_ascii=False)


def _normalize_tool_result(value: Any) -> str:
    """Convert SDK tool results into the native string result shape."""
    jsonable = _jsonable(value)
    if isinstance(jsonable, str):
        if not jsonable:
            return json.dumps(jsonable, ensure_ascii=False)
        return jsonable
    if isinstance(jsonable, list):
        if not jsonable:
            return json.dumps(jsonable, ensure_ascii=False)
        text_parts: list[str] = []
        for item in jsonable:
            if not isinstance(item, dict):
                return json.dumps(jsonable, ensure_ascii=False)
            if item.get("type") != "text":
                return json.dumps(jsonable, ensure_ascii=False)
            text = item.get("text")
            if not isinstance(text, str):
                return json.dumps(jsonable, ensure_ascii=False)
            text_parts.append(text)
        return "\n".join(text_parts)
    return json.dumps(jsonable, ensure_ascii=False)


async def build_codex_runtime(
    *,
    member_name: str,
    member_agent_id: str,
    team_name: str,
    team_session_id: str,
    cwd: str | None,
    env: dict[str, str],
    inject_mcp: bool,
    mcp_server_name: str,
    mcp_server_command: tuple[str, ...],
    mcp_default_tools_approval_mode: str | None,
    bypass_approvals_and_sandbox: bool,
    system_prompt: str | None,
    codex_bin: str | None,
    external_model_config: ExternalCliModelConfig | None = None,
    resume_external_backend: bool = False,
    turn_idle_timeout_s: float | None = None,
    turn_idle_retries: int | None = None,
    team_context_tracker: Any = None,
    role: str | None = None,
) -> CodexSdkRuntime:
    """Build a Codex Python SDK runtime without starting its thread eagerly."""
    sdk = load_codex_sdk()
    span_bridge = _build_span_bridge(
        member_name=member_name,
        member_agent_id=member_agent_id,
        team_name=team_name,
        session_id=team_session_id,
        role=role,
    )
    native_otel_receiver = None
    rollout_trace_reader = None
    try:
        try:
            from openjiuwen.agent_teams.observability.setup import is_initialized
        except ImportError:
            observability_initialized = False
        else:
            observability_initialized = is_initialized()
        team_logger.info(
            "[external-cli] building codex runtime for member {} observability_initialized={} "
            "span_bridge_enabled={} cwd={} codex_bin_configured={} inject_mcp={} mcp_server_command={} "
            "team_join_env_present={} external_model_configured={}",
            member_name,
            observability_initialized,
            not isinstance(span_bridge, _NoopCodexSpanBridge),
            cwd,
            codex_bin is not None,
            inject_mcp,
            mcp_server_command,
            "OPENJIUWEN_TEAM_JOIN" in env,
            external_model_config is not None,
        )

        if observability_initialized and not isinstance(
            span_bridge,
            _NoopCodexSpanBridge,
        ):
            from openjiuwen.agent_teams.observability.codex import (
                CodexOtelTraceReceiver,
                CodexRolloutTraceReader,
            )

            team_logger.info("[external-cli] starting codex rollout trace reader for member {}", member_name)
            rollout_trace_reader = await CodexRolloutTraceReader.start(
                span_bridge.record_rollout_event,
            )
            span_bridge.enable_rollout_trace()
            team_logger.info("[external-cli] starting codex native otel receiver for member {}", member_name)
            native_otel_receiver = await CodexOtelTraceReceiver.start(
                span_bridge.record_native_model_span,
            )
            if native_otel_receiver is not None:
                span_bridge.enable_native_model_spans()

        process_env = dict(env)
        traceparent = span_bridge.native_traceparent()
        if traceparent:
            # Codex reads TRACEPARENT when its App Server subprocess starts.
            # The native trace therefore belongs to the same Jiuwen team trace.
            process_env.setdefault("TRACEPARENT", traceparent)

        config = build_codex_config(
            cwd=cwd,
            env=process_env,
            inject_mcp=inject_mcp,
            mcp_server_name=mcp_server_name,
            mcp_server_command=mcp_server_command,
            mcp_default_tools_approval_mode=mcp_default_tools_approval_mode,
            member_name=member_name,
            codex_bin=codex_bin,
            external_model_config=external_model_config,
            native_otel_trace_endpoint=(native_otel_receiver.endpoint if native_otel_receiver is not None else None),
            rollout_trace_root=(str(rollout_trace_reader.root) if rollout_trace_reader is not None else None),
            sdk=sdk,
        )
        thread_options = build_codex_thread_options(
            cwd=cwd,
            system_prompt=system_prompt,
            external_model_config=external_model_config,
            bypass_approvals_and_sandbox=bypass_approvals_and_sandbox,
            sdk=sdk,
        )
        return CodexSdkRuntime(
            member_name=member_name,
            member_agent_id=member_agent_id,
            team_name=team_name,
            team_session_id=team_session_id,
            sdk=sdk,
            config=config,
            thread_options=thread_options,
            resume_external_backend=resume_external_backend,
            turn_idle_timeout_s=(_DEFAULT_TURN_IDLE_TIMEOUT_S if turn_idle_timeout_s is None else turn_idle_timeout_s),
            turn_idle_retries=(_DEFAULT_TURN_IDLE_RETRIES if turn_idle_retries is None else turn_idle_retries),
            team_context_tracker=team_context_tracker,
            span_bridge=span_bridge,
            native_otel_receiver=native_otel_receiver,
            rollout_trace_reader=rollout_trace_reader,
        )
    except BaseException:
        cleanup = []
        if native_otel_receiver is not None:
            cleanup.append(native_otel_receiver.aclose())
        if rollout_trace_reader is not None:
            cleanup.append(rollout_trace_reader.aclose())
        if cleanup:
            await asyncio.gather(*cleanup, return_exceptions=True)
        raise


__all__ = ["CodexSdkRuntime", "build_codex_runtime"]
