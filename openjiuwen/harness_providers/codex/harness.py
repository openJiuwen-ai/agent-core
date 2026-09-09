# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HarnessProtocol implementation backed by the Codex Python SDK."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Callable, Mapping

from openjiuwen.harness_protocol import (
    PROTOCOL_VERSION,
    AbortMode,
    CheckpointReason,
    HarnessCapability,
    HarnessCard,
    HarnessContext,
    HarnessInput,
    HarnessProtocolError,
    HarnessStateError,
    HostCapability,
    ProviderEvent,
    ResumePolicy,
    ToolApprovalDecision,
    ToolApprovalRequest,
    TurnError,
    TurnEventKind,
    TurnResult,
)
from openjiuwen.harness_providers.base import (
    PendingTurn,
    ProviderStartupError,
    SerializedTurnHarness,
    TurnTiming,
    interrupted_result,
    logger,
)
from openjiuwen.harness_providers.codex.config import CodexHarnessConfig, CodexModelConfig
from openjiuwen.harness_providers.codex.failure_classifier import classify_codex_exception
from openjiuwen.harness_providers.codex.mapping import PROVIDER_NAME, CodexTurnAccumulator
from openjiuwen.harness_providers.codex.options import (
    build_codex_config,
    build_process_env,
    build_thread_options,
    load_codex_sdk,
    start_thread_with_raw_events,
)
from openjiuwen.harness_providers.inputs import harness_input_text
from openjiuwen.harness_providers.jsonsafe import to_json_object

ADAPTER_VERSION = "0.1.0"
_INTERRUPT_TIMEOUT_S = 5.0
_APPROVAL_WAIT_TIMEOUT_S = 600.0
_NO_ACTIVE_TURN_ERROR_CODE = -32600
_NO_ACTIVE_TURN_ERROR_MESSAGE = "no active turn to steer"
_APPROVAL_METHODS = frozenset({"item/commandExecution/requestApproval", "item/fileChange/requestApproval"})

NotificationObserver = Callable[[Any], None]


class _TurnIdleTimeout(RuntimeError):
    """One Codex turn stopped producing SDK notifications."""

    def __init__(self, *, notifications_seen: int, interrupted: bool) -> None:
        super().__init__("codex turn idle timeout")
        self.notifications_seen = notifications_seen
        self.interrupted = interrupted


class _RetryBudgetExceeded(RuntimeError):
    """Codex kept emitting ``will_retry`` beyond the allowed count."""

    def __init__(self, error: TurnError) -> None:
        super().__init__(f"codex exceeded the will_retry budget ({error.category})")
        self.error = error


class _AuthFallbackRequested(RuntimeError):
    """The current prompt must be retried on the fallback endpoint."""


class CodexHarness(SerializedTurnHarness):
    """Adapt one Codex SDK client and one isolated thread to protocol v1.

    One external Turn is one ``thread.turn()`` streamed to its
    ``turn/completed`` notification.  Steering uses the SDK turn steer
    request; abort maps to a bounded ``interrupt()``.
    """

    card = HarnessCard(
        name=PROVIDER_NAME,
        implementation_version=ADAPTER_VERSION,
        protocol_version=PROTOCOL_VERSION,
        compatible_protocol_versions=frozenset({PROTOCOL_VERSION}),
        capabilities=frozenset(
            {
                HarnessCapability.STEER,
                HarnessCapability.GRACEFUL_ABORT,
                HarnessCapability.PERSISTENT_SESSION,
                HarnessCapability.CHECKPOINT,
                HarnessCapability.MCP_TOOLS,
            }
        ),
        optional_host_capabilities=frozenset(
            {
                HostCapability.TOOL_APPROVAL,
                HostCapability.CHECKPOINT_SINK,
                HostCapability.MCP_SERVERS,
            }
        ),
    )

    def __init__(
        self,
        config: CodexHarnessConfig | None = None,
        *,
        notification_observer: NotificationObserver | None = None,
    ) -> None:
        """Bind the provider configuration; the SDK client starts on ``start``.

        Args:
            config: Provider-owned options; defaults use the local Codex CLI.
            notification_observer: Provider-private hook receiving every raw
                SDK notification (observability bridges).  It must not raise
                and it never reaches the public event stream.
        """
        self._config = config or CodexHarnessConfig()
        super().__init__(event_buffer_capacity=self._config.event_buffer_capacity)
        self._notification_observer = notification_observer
        self._sdk: Any = None
        self._client: Any = None
        self._thread: Any = None
        self._thread_id: str | None = None
        self._active_handle: Any = None
        self._pending_steers: list[str] = []
        self._active_model: CodexModelConfig | None = self._config.model
        self._fallback_activated = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def fallback_activated(self) -> bool:
        """Return whether the authentication fallback endpoint is in use."""
        return self._fallback_activated

    # ------------------------------------------------------------------
    # Provider hooks
    # ------------------------------------------------------------------

    def _validate_context(self, context: HarnessContext) -> None:
        super()._validate_context(context)
        if context.resume_policy is ResumePolicy.REQUIRE_RESUME and context.checkpoint is None:
            raise HarnessProtocolError("Codex cannot resume a thread without a checkpoint")

    async def _open_session(self, context: HarnessContext) -> str | None:
        sdk = load_codex_sdk()
        self._sdk = sdk
        self._loop = asyncio.get_running_loop()
        restored = self._restored_checkpoint_data(context)
        restored_thread = restored.get("thread_id") if restored else None
        resume_thread_id: str | None = None
        if context.resume_policy is not ResumePolicy.NEW and isinstance(restored_thread, str) and restored_thread:
            resume_thread_id = restored_thread
        elif context.resume_policy is ResumePolicy.REQUIRE_RESUME:
            raise HarnessProtocolError("Codex checkpoint does not carry a thread id to resume")
        self._active_model = self._config.model
        self._fallback_activated = False
        try:
            await self._connect(context, model=self._active_model, resume_thread_id=resume_thread_id)
        except asyncio.CancelledError:
            raise
        except ProviderStartupError:
            raise
        except Exception as exc:
            error = classify_codex_exception(exc)
            if resume_thread_id is not None:
                error = TurnError(
                    message=f"failed to resume Codex thread {resume_thread_id!r}: {error.message}",
                    code=error.code,
                    category=error.category,
                    retryable=error.retryable,
                    provider_data=error.provider_data,
                )
            raise ProviderStartupError(f"Codex startup failed: {type(exc).__name__}", error=error) from exc
        await self._publish_checkpoint(
            {"thread_id": self._thread_id, "resumed": resume_thread_id is not None},
            reason=CheckpointReason.SESSION_ACTIVATED,
        )
        return self._thread_id

    async def _connect(self, context: HarnessContext, *, model: CodexModelConfig | None, resume_thread_id: str | None) -> None:
        sdk = self._sdk
        cwd = context.cwd or self._config.cwd
        codex_config = build_codex_config(
            sdk=sdk,
            config=self._config,
            model=model,
            cwd=cwd,
            env=build_process_env(self._config, context.env),
            mcp_servers=context.mcp_servers,
        )
        options = build_thread_options(
            sdk=sdk,
            config=self._config,
            model=model,
            cwd=cwd,
            system_prompt=context.system_prompt,
        )
        client = sdk.AsyncCodex(config=codex_config)
        if HostCapability.TOOL_APPROVAL in context.host_capabilities:
            _install_approval_handler(client, self._approval_handler)
        try:
            if resume_thread_id is not None:
                options.pop("ephemeral", None)
                thread = await client.thread_resume(resume_thread_id, **options)
                resumed_id = getattr(thread, "id", None)
                if resumed_id != resume_thread_id:
                    raise HarnessProtocolError(
                        f"Codex resumed unexpected thread {resumed_id!r}; expected {resume_thread_id!r}"
                    )
            elif self._config.experimental_raw_events:
                thread = await start_thread_with_raw_events(client=client, sdk=sdk, options=options)
            else:
                thread = await client.thread_start(**options)
        except BaseException:
            with contextlib.suppress(Exception):
                await client.close()
            raise
        self._client = client
        self._thread = thread
        self._thread_id = str(thread.id)

    async def _close_session(self) -> None:
        handle = self._active_handle
        self._active_handle = None
        if handle is not None:
            await self._interrupt_handle(handle)
        client = self._client
        self._client = None
        self._thread = None
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()

    async def _execute_turn(self, turn: PendingTurn) -> tuple[TurnEventKind, TurnResult]:
        timing = TurnTiming()
        text = harness_input_text(turn.content)
        accumulator = CodexTurnAccumulator(turn_id=turn.turn_id)
        for _attempt in range(2):
            idle_retries = 0
            try:
                while True:
                    try:
                        await self._run_turn(turn, text, accumulator)
                    except _TurnIdleTimeout as exc:
                        can_retry = (
                            idle_retries < self._config.turn_idle_retries
                            and exc.notifications_seen == 0
                            and exc.interrupted
                            and not turn.abort_requested
                        )
                        if not can_retry:
                            raise
                        idle_retries += 1
                        logger.warning(
                            "[codex] turn was silent for %ss; retrying prompt on the same thread (%s/%s)",
                            self._config.turn_idle_timeout_s,
                            idle_retries,
                            self._config.turn_idle_retries,
                        )
                        continue
                    break
            except _AuthFallbackRequested:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if turn.abort_requested:
                    return TurnEventKind.ABORTED, interrupted_result(
                        turn,
                        provider_name=PROVIDER_NAME,
                        timing=timing,
                        messages=tuple(accumulator.messages),
                        final_output=accumulator.last_text_output,
                        usage=accumulator.total_usage,
                    )
                if isinstance(exc, _RetryBudgetExceeded):
                    error = exc.error
                elif isinstance(exc, _TurnIdleTimeout):
                    error = TurnError(
                        message=f"Codex produced no turn events for {self._config.turn_idle_timeout_s:g}s",
                        code="CODEX_TURN_IDLE_TIMEOUT",
                        category="network_timeout",
                        retryable=True,
                    )
                else:
                    error = classify_codex_exception(exc)
                    if await self._maybe_activate_fallback(error, accumulator, turn):
                        continue
                return TurnEventKind.FAILED, accumulator.build_failed_result(error, timing=timing)
            kind, result = accumulator.build_terminal_result(turn=turn, timing=timing)
            if kind is TurnEventKind.FAILED and await self._maybe_activate_fallback(result.error, accumulator, turn):
                accumulator = CodexTurnAccumulator(turn_id=turn.turn_id)
                continue
            return kind, result
        error = TurnError(
            message="Codex authentication fallback did not recover the turn",
            code="CODEX_FALLBACK_EXHAUSTED",
            category="auth_required",
        )
        return TurnEventKind.FAILED, accumulator.build_failed_result(error, timing=timing)

    async def _run_turn(self, turn: PendingTurn, text: str, accumulator: CodexTurnAccumulator) -> None:
        thread = self._thread
        if thread is None:
            raise HarnessProtocolError("Codex thread disappeared during an active cycle")
        handle = await thread.turn(text)
        self._active_handle = handle
        if turn.abort_requested:
            await self._interrupt_handle(handle)
        # A steer accepted between the external STARTED event and turn/start
        # returning has no handle yet; deliver it now that the turn exists.
        pending_steers, self._pending_steers = self._pending_steers, []
        for steer_text in pending_steers:
            await self._steer_handle(handle, steer_text)
        will_retry_count = 0
        try:
            stream = handle.stream().__aiter__()
            while True:
                try:
                    notification = await asyncio.wait_for(anext(stream), timeout=self._config.turn_idle_timeout_s)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as exc:
                    interrupted = await self._interrupt_handle(handle)
                    raise _TurnIdleTimeout(
                        notifications_seen=accumulator.notifications_seen,
                        interrupted=interrupted,
                    ) from exc
                self._observe(notification)
                mapped_events, retrying = accumulator.consume(notification)
                for mapped in mapped_events:
                    await self._emit(mapped.payload, turn=turn, item_id=mapped.item_id)
                if retrying is not None:
                    if retrying.error.category == "auth_required" and await self._maybe_activate_fallback(
                        retrying.error, accumulator, turn
                    ):
                        raise _AuthFallbackRequested()
                    will_retry_count += 1
                    if will_retry_count > self._config.max_will_retry_count:
                        await self._interrupt_handle(handle)
                        raise _RetryBudgetExceeded(retrying.error)
        finally:
            if self._active_handle is handle:
                self._active_handle = None
            self._pending_steers.clear()

    def _observe(self, notification: Any) -> None:
        observer = self._notification_observer
        if observer is None:
            return
        try:
            observer(notification)
        except Exception:
            logger.exception("[codex] notification observer raised")

    async def _steer(self, turn: PendingTurn, content: HarnessInput) -> None:
        text = harness_input_text(content)
        handle = self._active_handle
        if handle is None:
            if self._active_turn is not turn or turn.abort_requested:
                raise HarnessStateError("there is no active Codex turn to steer")
            # ``thread.turn()`` has not returned yet; ``_run_turn`` flushes the
            # queue as soon as the SDK handle exists.
            self._pending_steers.append(text)
            return
        await self._steer_handle(handle, text)

    async def _steer_handle(self, handle: Any, text: str) -> None:
        try:
            await handle.steer(text)
        except Exception as exc:
            if _is_no_active_turn_to_steer(exc):
                raise HarnessStateError("the Codex turn ended before the steer was accepted") from exc
            raise

    async def _interrupt_turn(self, turn: PendingTurn, mode: AbortMode) -> None:
        _ = turn, mode
        handle = self._active_handle
        if handle is not None:
            await self._interrupt_handle(handle)

    async def _interrupt_handle(self, handle: Any) -> bool:
        try:
            await asyncio.wait_for(handle.interrupt(), timeout=_INTERRUPT_TIMEOUT_S)
            return True
        except Exception as exc:
            logger.warning("[codex] turn interrupt failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Authentication fallback
    # ------------------------------------------------------------------

    async def _maybe_activate_fallback(
        self,
        error: TurnError | None,
        accumulator: CodexTurnAccumulator,
        turn: PendingTurn,
    ) -> bool:
        fallback = self._config.fallback_model
        if (
            error is None
            or error.category != "auth_required"
            or fallback is None
            or self._fallback_activated
            or accumulator.emitted_output
            or turn.abort_requested
        ):
            return False
        context = self._context
        if context is None:
            return False
        thread_id = self._thread_id
        await self._close_session()
        try:
            await self._connect(context, model=fallback, resume_thread_id=thread_id)
        except Exception as exc:
            logger.warning("[codex] authentication fallback activation failed: %s", exc)
            return False
        self._active_model = fallback
        self._fallback_activated = True
        await self._publish_checkpoint(
            {"thread_id": self._thread_id, "resumed": True, "fallback": True},
            reason=CheckpointReason.STATE_CHANGED,
        )
        await self._emit(
            ProviderEvent(
                provider=PROVIDER_NAME,
                event_type="auth_fallback_activated",
                schema_version="1",
                payload={"model": fallback.model, "provider": fallback.provider, "api_base": fallback.api_base},
            ),
            turn=turn,
        )
        return True

    # ------------------------------------------------------------------
    # Approval routing (runs on the SDK reader thread)
    # ------------------------------------------------------------------

    def _approval_handler(self, method: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
        if method not in _APPROVAL_METHODS:
            return {}
        loop = self._loop
        if loop is None or loop.is_closed():
            return {"decision": "decline"}
        future = asyncio.run_coroutine_threadsafe(self._route_approval(method, params or {}), loop)
        try:
            return future.result(timeout=_APPROVAL_WAIT_TIMEOUT_S)
        except Exception as exc:
            logger.warning("[codex] approval routing for %s failed: %s", method, exc)
            return {"decision": "decline"}

    async def _route_approval(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        active = self._active_turn
        item_id = str(params.get("itemId") or params.get("item_id") or "codex-approval")
        arguments = {key: value for key, value in to_json_object(params).items() if key not in {"threadId", "turnId"}}
        request = ToolApprovalRequest(
            request_id=f"codex-approval:{item_id}",
            call_id=item_id,
            tool_name="apply_patch" if method == "item/fileChange/requestApproval" else "shell",
            arguments=arguments,
            provider_session_id=self._thread_id,
            turn_id=active.turn_id if active is not None else None,
            provider_data={"method": method},
        )
        response = await self._request_interaction(request)
        if response is None:
            return {"decision": "decline"}
        if response.decision in (ToolApprovalDecision.ALLOW, ToolApprovalDecision.ALLOW_FOR_SESSION):
            return {"decision": "accept"}
        return {"decision": "decline"}


def _install_approval_handler(client: Any, handler: Callable[[str, Mapping[str, Any] | None], dict[str, Any]]) -> None:
    """Route App Server approval requests to ``handler`` on the low-level client.

    The high-level ``AsyncCodex`` never exposes the approval handler; it lives
    on the wrapped synchronous ``CodexClient``.  When the SDK layout differs,
    the default accept-all behavior stays in place and a warning is logged.
    """
    low_level = getattr(getattr(client, "_client", None), "_sync", None)
    if low_level is None or not hasattr(low_level, "_approval_handler"):
        logger.warning("[codex] SDK does not expose an approval handler; tool approval requests auto-accept")
        return
    low_level._approval_handler = handler


def _is_no_active_turn_to_steer(exc: Exception) -> bool:
    """Return whether Codex rejected steer because its turn already ended."""
    message = getattr(exc, "message", None)
    return (
        getattr(exc, "code", None) == _NO_ACTIVE_TURN_ERROR_CODE
        and isinstance(message, str)
        and _NO_ACTIVE_TURN_ERROR_MESSAGE in message.lower()
    )


__all__ = ["ADAPTER_VERSION", "CodexHarness", "NotificationObserver"]
