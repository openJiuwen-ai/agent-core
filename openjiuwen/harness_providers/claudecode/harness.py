# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HarnessProtocol implementation backed by the Claude Agent SDK."""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import uuid
from collections import deque
from collections.abc import AsyncIterator
from typing import Any, Callable, Mapping

from openjiuwen.harness_protocol import (
    PROTOCOL_VERSION,
    AbortMode,
    CheckpointReason,
    DiagnosticEvent,
    DiagnosticLevel,
    HarnessCapability,
    HarnessCard,
    HarnessContext,
    HarnessInput,
    HarnessProtocolError,
    HostCapability,
    InteractionResponseStatus,
    ModelOption,
    ModelSelection,
    ProviderEvent,
    ResumePolicy,
    ToolApprovalDecision,
    ToolApprovalRequest,
    TurnError,
    TurnEventKind,
    TurnResult,
    UserInputRequest,
    json_value_to_builtin,
)
from openjiuwen.harness_providers.base import (
    PendingTurn,
    ProviderStartupError,
    SerializedTurnHarness,
    TurnTiming,
    interrupted_result,
    logger,
)
from openjiuwen.harness_providers.claudecode.config import ClaudeCodeHarnessConfig, ClaudeModelConfig
from openjiuwen.harness_providers.claudecode.failure_classifier import classify_claude_exception
from openjiuwen.harness_providers.claudecode.lifecycle import SETTLED_SUBTYPE, LifecycleTap, TurnCycleTracker
from openjiuwen.harness_providers.claudecode.mapping import PROVIDER_NAME, ClaudeTurnAccumulator, MappedClaudeEvent
from openjiuwen.harness_providers.claudecode.options import (
    apply_claude_flag_settings,
    build_claude_options,
    build_claude_session_id,
    build_claude_subprocess_transport,
    build_process_env,
    claude_model_options,
    claude_request_log_settings_env,
    load_claude_sdk,
    mcp_servers_to_sdk,
)
from openjiuwen.harness_providers.claudecode.observation import ClaudeRequestObserver
from openjiuwen.harness_providers.inputs import harness_input_text
from openjiuwen.harness_providers.jsonsafe import to_json_object, to_json_safe
from openjiuwen.harness_providers.skills import install_skills

ADAPTER_VERSION = "0.1.0"
ASK_USER_TOOL_NAME = "AskUserQuestion"
# Provider interaction asking the host to ratify (persist) an auth fallback.
AUTH_FALLBACK_REQUEST_TYPE = "auth_fallback"
# Provider event announcing the model / effort the session now runs on.
MODEL_CHANGED_EVENT = "session/model_changed"
_INTERACTIVE_HOST_CAPABILITIES = frozenset({HostCapability.USER_INPUT, HostCapability.TOOL_APPROVAL})

TransportFactory = Callable[[Any], Any]


class _StderrTail:
    """Capture a bounded tail of Claude CLI stderr for startup diagnostics."""

    def __init__(self, *, max_lines: int = 40, max_line_chars: int = 2000, max_chars: int = 8000) -> None:
        self._max_line_chars = max_line_chars
        self._max_chars = max_chars
        self._lines: deque[str] = deque(maxlen=max_lines)

    def append(self, line: str) -> None:
        text = str(line).strip()
        if not text:
            return
        if len(text) > self._max_line_chars:
            text = text[: self._max_line_chars] + "...[truncated]"
        self._lines.append(text)
        while len(self.render()) > self._max_chars and self._lines:
            self._lines.popleft()

    def render(self) -> str:
        return "\n".join(self._lines)


class ClaudeCodeHarness(SerializedTurnHarness):
    """Adapt one Claude Code SDK client session to protocol v1.

    One external Turn is one submitted message plus every message steered into
    it, and it ends when the CLI has answered them all -- see
    ``claudecode.lifecycle``.  Steering re-enters ``query()`` on the active
    session; abort maps to ``interrupt()``.
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
                HarnessCapability.MODEL_SELECTION,
                HarnessCapability.MODEL_DISCOVERY,
            }
        ),
        optional_host_capabilities=frozenset(
            {
                HostCapability.TOOL_APPROVAL,
                HostCapability.USER_INPUT,
                HostCapability.CHECKPOINT_SINK,
                HostCapability.MCP_SERVERS,
                HostCapability.PROVIDER_INTERACTION,
                HostCapability.MODEL_REQUEST_OBSERVATION,
            }
        ),
    )

    def __init__(
        self,
        config: ClaudeCodeHarnessConfig | None = None,
        *,
        transport_factory: TransportFactory | None = None,
    ) -> None:
        """Bind the provider configuration; the SDK client connects on ``start``.

        Args:
            config: Provider-owned options; defaults connect to the local CLI.
            transport_factory: Optional ``options -> Transport`` builder (for
                example an SSH transport); ``None`` uses the SDK subprocess.
        """
        self._config = config or ClaudeCodeHarnessConfig()
        super().__init__(event_buffer_capacity=self._config.event_buffer_capacity)
        self._transport_factory = transport_factory
        self._sdk: Any = None
        self._client: Any = None
        self._stderr_tail = _StderrTail()
        self._active_model: ClaudeModelConfig | None = self._config.model
        # The native endpoint with any runtime model selection applied; the
        # endpoint a declined fallback goes back to.
        self._primary_model: ClaudeModelConfig | None = self._config.model
        self._fallback_activated = False
        self._claude_session_id: str | None = None
        self._request_observer: ClaudeRequestObserver | None = None
        self._request_log_env: dict[str, str] = {}
        self._cycle_tracker: TurnCycleTracker | None = None
        # The CLI reports cost per session; a turn reports what it added.
        self._session_cost_usd = 0.0

    @property
    def fallback_activated(self) -> bool:
        """Return whether the authentication fallback endpoint is in use."""
        return self._fallback_activated

    # ------------------------------------------------------------------
    # Provider hooks
    # ------------------------------------------------------------------

    async def _open_session(self, context: HarnessContext) -> str | None:
        if self._config.skills and self._transport_factory is not None:
            raise HarnessProtocolError("skill copying requires a local Claude transport")
        await asyncio.to_thread(install_skills, self._config.skills, provider="claudecode",
                                cwd=context.cwd or self._config.cwd, conflict=self._config.skill_conflict)
        sdk = load_claude_sdk()
        self._sdk = sdk
        await self._attach_request_observer(context)
        restored = self._restored_checkpoint_data(context)
        restored_session = restored.get("session_id") if restored else None
        derived = self._config.session_id or build_claude_session_id(
            host_session_id=context.host_session_id,
            agent_name=context.agent_name,
        )
        resume: str | None = None
        session_id: str | None = derived
        if context.resume_policy is ResumePolicy.RESUME_IF_AVAILABLE and isinstance(restored_session, str):
            resume, session_id = restored_session, None
        elif context.resume_policy is ResumePolicy.REQUIRE_RESUME:
            candidate = restored_session if isinstance(restored_session, str) else derived
            if candidate is None:
                raise HarnessProtocolError("Claude Code cannot resume without a checkpoint or derived session id")
            resume, session_id = candidate, None
        self._claude_session_id = resume or session_id
        self._active_model = self._config.model
        self._primary_model = self._config.model
        self._fallback_activated = False
        self._client = await self._connect(context, model=self._active_model, resume=resume, session_id=session_id)
        await self._publish_checkpoint(
            {"session_id": self._claude_session_id, "resumed": resume is not None},
            reason=CheckpointReason.SESSION_ACTIVATED,
        )
        return self._claude_session_id

    async def _attach_request_observer(self, context: HarnessContext) -> None:
        """Observe model requests when the host consumes them.

        Request logs reach the loopback receiver only from a local CLI; a
        remote transport still gets its requests reported from the SDK stream.
        """
        self._request_log_env = {}
        if HostCapability.MODEL_REQUEST_OBSERVATION not in context.host_capabilities:
            self._request_observer = None
            return
        observer = ClaudeRequestObserver(sdk=self._sdk, wait_s=float(self._config.request_observation_wait_s))
        self._request_observer = observer
        if self._transport_factory is not None:
            logger.info("[claude-code] request logs need a local CLI; reporting requests from the SDK stream")
            return
        process_env = build_process_env(self._config, context.env)
        self._request_log_env = await observer.attach(
            resource_attributes=process_env.get("OTEL_RESOURCE_ATTRIBUTES", ""),
        )

    async def _connect(
        self,
        context: HarnessContext,
        *,
        model: ClaudeModelConfig | None,
        resume: str | None,
        session_id: str | None,
    ) -> Any:
        interactive = bool(context.host_capabilities & _INTERACTIVE_HOST_CAPABILITIES)
        options = build_claude_options(
            sdk=self._sdk,
            config=self._config,
            model=model,
            cwd=context.cwd or self._config.cwd,
            env={**build_process_env(self._config, context.env), **self._request_log_env},
            system_prompt=context.system_prompt,
            session_id=session_id,
            resume=resume,
            mcp_servers=mcp_servers_to_sdk(context.mcp_servers),
            can_use_tool=self._can_use_tool if interactive else None,
            stderr=self._stderr_tail.append,
            settings_env=claude_request_log_settings_env(self._request_log_env),
        )
        if interactive:
            # ``bypassPermissions`` never consults ``can_use_tool``; the host
            # asked for user-input / approval routing, so permission requests
            # must reach the callback.
            options.permission_mode = "default"
        tracker = TurnCycleTracker(ack_timeout_s=float(self._config.lifecycle_ack_timeout_s))
        # The transport is built here, not by the SDK, so the tap can read the
        # delivery receipts the SDK parser drops. Supplying a transport also
        # skips the SDK's resume materialization, which is a no-op for this
        # provider: it only runs for options that carry a ``session_store``.
        # The SDK routes permission prompts to ``can_use_tool`` by rewriting the
        # options it hands its own transport; a transport built here has to
        # carry that rewrite itself, or the CLI launches without
        # ``--permission-prompt-tool`` and every approval stalls. It stays off
        # the client's own options, which reject having both.
        transport_options = options
        if getattr(options, "can_use_tool", None) is not None:
            transport_options = copy.copy(options)
            transport_options.permission_prompt_tool_name = "stdio"
        inner = (
            self._transport_factory(transport_options)
            if self._transport_factory is not None
            else build_claude_subprocess_transport(transport_options, _empty_prompt())
        )
        client = self._sdk.ClaudeSDKClient(options=options, transport=LifecycleTap(inner, tracker))
        try:
            await client.connect()
        except Exception as exc:
            error = classify_claude_exception(exc, phase="startup")
            stderr_tail = self._stderr_tail.render()
            message = f"{error.message}\n{stderr_tail}" if stderr_tail else error.message
            raise ProviderStartupError(
                f"Claude Code startup failed: {type(exc).__name__}",
                error=TurnError(
                    message=message,
                    code=error.code,
                    category=error.category,
                    retryable=error.retryable,
                    provider_data=error.provider_data,
                ),
            ) from exc
        self._cycle_tracker = tracker
        # A reconnected CLI counts its session cost from zero again.
        self._session_cost_usd = 0.0
        return client

    async def _close_session(self) -> None:
        try:
            await self._disconnect_client()
        finally:
            observer = self._request_observer
            self._request_observer = None
            self._request_log_env = {}
            if observer is not None:
                await observer.close()

    async def _disconnect_client(self) -> None:
        """Drop the SDK client; the next turn reconnects the same session."""
        client = self._client
        self._client = None
        self._cycle_tracker = None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception as exc:
            logger.debug("[claude-code] disconnect failed during teardown: %s", exc)

    async def _execute_turn(self, turn: PendingTurn) -> tuple[TurnEventKind, TurnResult]:
        try:
            return await self._observed_turn(turn)
        finally:
            tracker = self._cycle_tracker
            if tracker is not None:
                tracker.end_turn()

    async def _observed_turn(self, turn: PendingTurn) -> tuple[TurnEventKind, TurnResult]:
        observer = self._request_observer
        if observer is None:
            return await self._run_turn(turn)

        async def emit(payload: Any, item_id: str | None, causation_ids: tuple[str, ...], timestamp: float | None) -> None:
            await self._emit(payload, turn=turn, item_id=item_id, causation_ids=causation_ids, timestamp=timestamp)

        observer.begin_turn(emit)
        kind: TurnEventKind | None = None
        try:
            kind, result = await self._run_turn(turn)
            return kind, result
        finally:
            try:
                # Every model request of the turn is reported before its
                # terminal event; only a completed turn waits for late logs.
                await observer.end_turn(wait=kind is TurnEventKind.FINISHED)
            except Exception:
                logger.debug("[claude-code] model request observation did not settle", exc_info=True)

    async def _submit(self, client: Any, *, message_id: str, text: str) -> None:
        """Write one user message to the CLI, labelled with ``message_id``.

        The CLI echoes the label back as the ``command_uuid`` of its delivery
        receipts, which is what ties a receipt to the message it answers. A
        plain string would make the SDK mint the frame itself, without a label.

        Args:
            client: The connected SDK client.
            message_id: The protocol message id the host was handed.
            text: The message body.
        """

        async def stream() -> AsyncIterator[dict[str, Any]]:
            yield {
                "type": "user",
                "message": {"role": "user", "content": text},
                "parent_tool_use_id": None,
                "uuid": message_id,
            }

        await client.query(stream())

    def _turn_settled(self, message: Any) -> bool:
        """Return whether ``message`` is the tap's end-of-turn sentinel."""
        return isinstance(message, self._sdk.SystemMessage) and getattr(message, "subtype", None) == SETTLED_SUBTYPE

    async def _report_cycle_diagnostics(self, turn: PendingTurn) -> None:
        """Report messages the CLI left unanswered when the turn settled.

        An abort leaves the running message unanswered by design, and the host
        asked for that, so it is not reported back as a fault.
        """
        tracker = self._cycle_tracker
        if tracker is None:
            return
        reported_all = tracker.drain_diagnostics()
        if turn.abort_requested:
            return
        for reported in reported_all:
            await self._emit(
                DiagnosticEvent(level=DiagnosticLevel.WARNING, message=f"Claude Code {reported}"),
                turn=turn,
            )

    async def _publish(self, message: Any, accumulator: ClaudeTurnAccumulator, turn: PendingTurn) -> None:
        """Emit the events one SDK message maps to, through the observer when present."""
        mapped: list[MappedClaudeEvent] = accumulator.consume(message)
        observer = self._request_observer
        if observer is not None:
            await observer.observe(message, mapped, accumulator)
            return
        for event in mapped:
            await self._emit(event.payload, turn=turn, item_id=event.item_id)

    async def _run_turn(self, turn: PendingTurn) -> tuple[TurnEventKind, TurnResult]:
        timing = TurnTiming()
        accumulator = ClaudeTurnAccumulator(
            turn_id=turn.turn_id,
            sdk=self._sdk,
            cost_baseline_usd=self._session_cost_usd,
        )
        text = harness_input_text(turn.content)
        # A failed rollback leaves no usable client. Retry only when a new
        # accepted input arrives; never replay a failed turn in the background.
        if self._client is None and not turn.abort_requested:
            try:
                self._client = await self._connect(
                    self._context,
                    model=self._active_model,
                    resume=self._claude_session_id,
                    session_id=None,
                )
            except Exception as exc:
                if turn.abort_requested:
                    return TurnEventKind.ABORTED, interrupted_result(turn, provider_name=PROVIDER_NAME, timing=timing)
                if isinstance(exc, ProviderStartupError):
                    error = exc.error
                else:
                    error = classify_claude_exception(exc, phase="startup")
                return TurnEventKind.FAILED, accumulator.build_failed_result(error, timing=timing)
        if turn.abort_requested:
            await self._disconnect_client()
            return TurnEventKind.ABORTED, interrupted_result(turn, provider_name=PROVIDER_NAME, timing=timing)
        for _attempt in range(2):
            client = self._client
            if client is None:
                raise HarnessProtocolError("Claude Code client disappeared during an active cycle")
            try:
                tracker = self._cycle_tracker
                if tracker is not None:
                    tracker.begin_turn(turn.turn_id)
                await self._submit(client, message_id=turn.message_id, text=text)
                # The stream runs to the tap's settled sentinel, not to the
                # first result: a steered message the CLI answers as a new
                # cycle carries a result of its own, and its output belongs to
                # this turn.
                retry = False
                async for message in client.receive_messages():
                    if not self._turn_settled(message):
                        await self._publish(message, accumulator, turn)
                        continue
                    await self._report_cycle_diagnostics(turn)
                    if not accumulator.has_result:
                        break
                    kind, result = accumulator.build_terminal_result(turn=turn, timing=timing)
                    self._session_cost_usd = accumulator.session_cost_usd
                    if kind is TurnEventKind.FAILED and await self._maybe_activate_fallback(
                        result.error, accumulator, turn
                    ):
                        retry = True
                        break
                    return kind, result
                if retry:
                    continue
                error = _incomplete_turn_error(settled=accumulator.has_result)
                # A stream that ends before the turn does is the signature of a
                # message channel that already died (transport error, CLI
                # exit): ``query`` writes to a still-open stdin while the
                # reader drains a closed stream. The client cannot recover in
                # place, so drop it; the next turn reconnects.
                await self._disconnect_client()
                return TurnEventKind.FAILED, accumulator.build_failed_result(error, timing=timing)
            except Exception as exc:
                if turn.abort_requested:
                    return TurnEventKind.ABORTED, interrupted_result(
                        turn,
                        provider_name=PROVIDER_NAME,
                        timing=timing,
                        messages=tuple(accumulator.messages),
                        final_output=accumulator.last_text_output,
                    )
                error = classify_claude_exception(exc, phase="turn")
                if await self._maybe_activate_fallback(error, accumulator, turn):
                    continue
                # A transport/decode exception kills the SDK read task and its
                # message stream for good; a reused client accepts the next
                # ``query`` (stdin is fine) but returns an empty stream, so the
                # member would look READY while silently producing nothing.
                # Drop the client here so the next turn reconnects cleanly.
                await self._disconnect_client()
                return TurnEventKind.FAILED, accumulator.build_failed_result(error, timing=timing)
        error = TurnError(
            message="Claude Code authentication fallback did not recover the turn",
            code="CLAUDE_FALLBACK_EXHAUSTED",
            category="auth_required",
        )
        return TurnEventKind.FAILED, accumulator.build_failed_result(error, timing=timing)

    async def _steer(self, turn: PendingTurn, content: HarnessInput, *, message_id: str) -> None:
        _ = turn
        client = self._client
        if client is None:
            raise HarnessProtocolError("Claude Code client disappeared during an active cycle")
        await self._submit(client, message_id=message_id, text=harness_input_text(content))

    async def _interrupt_turn(self, turn: PendingTurn, mode: AbortMode) -> None:
        _ = turn, mode
        client = self._client
        if client is None:
            return
        await client.interrupt()

    # ------------------------------------------------------------------
    # Model control
    # ------------------------------------------------------------------

    async def _list_models(self) -> tuple[ModelOption, ...]:
        client = self._client
        if client is not None:
            return claude_model_options(await client.get_server_info())
        return await self._probe_models()

    async def _probe_models(self) -> tuple[ModelOption, ...]:
        """Read the catalog from a throwaway CLI handshake; sends no model request."""
        sdk = self._sdk or load_claude_sdk()
        context = self._context
        options = build_claude_options(
            sdk=sdk,
            config=self._config,
            model=self._primary_model,
            cwd=(context.cwd if context is not None else None) or self._config.cwd,
            env=build_process_env(self._config, context.env if context is not None else {}),
            system_prompt="",
            session_id=None,
            resume=None,
            mcp_servers={},
            can_use_tool=None,
            stderr=None,
        )
        transport = self._transport_factory(options) if self._transport_factory is not None else None
        client = sdk.ClaudeSDKClient(options=options, transport=transport)
        await client.connect()
        try:
            return claude_model_options(await client.get_server_info())
        finally:
            try:
                await client.disconnect()
            except Exception as exc:
                logger.debug("[claude-code] disconnect after model probe failed: %s", exc)

    async def _apply_model_selection(self, selection: ModelSelection) -> None:
        updated = _with_selection(self._active_model, selection)
        client = self._client
        if client is not None:
            if selection.model is not None:
                await client.set_model(selection.model)
            applied = selection.effort is None or await apply_claude_flag_settings(
                client, {"effortLevel": selection.effort}
            )
            if not applied:
                # This SDK cannot hot-apply effort; the next turn reconnects the
                # same session with ``--effort`` from the updated config.
                await self._disconnect_client()
        self._active_model = updated
        if not self._fallback_activated:
            self._primary_model = updated
        await self._emit(
            ProviderEvent(
                provider=PROVIDER_NAME,
                event_type=MODEL_CHANGED_EVENT,
                schema_version="1",
                payload={"model": updated.model, "effort": updated.effort},
            )
        )

    # ------------------------------------------------------------------
    # Authentication fallback
    # ------------------------------------------------------------------

    def _fallback_applies(
        self,
        error: TurnError | None,
        fallback: ClaudeModelConfig | None,
        turn: PendingTurn,
    ) -> bool:
        """Report whether the auth fallback may still replace this turn.

        The category itself is the gate: an ``auth_required`` failure means the
        request never reached the model on the native endpoint, so replaying
        the turn on the fallback cannot duplicate meaningful work — even when
        the CLI reported the failure as synthetic assistant text, which would
        otherwise look like consumed output. A mid-turn token expiry may have
        emitted partial output before failing; replaying it is still preferred
        over failing the turn, and no worse than the manual retry the caller
        would perform anyway.

        Args:
            error: The failure classified so far, when there is one.
            fallback: The configured fallback endpoint, when there is one.
            turn: The turn being considered for a restart.

        Returns:
            True when every precondition for activating the fallback holds.
        """
        if error is None or fallback is None:
            return False
        if error.category != "auth_required" or self._fallback_activated:
            return False
        return not turn.abort_requested

    async def _maybe_activate_fallback(
        self,
        error: TurnError | None,
        accumulator: ClaudeTurnAccumulator,
        turn: PendingTurn,
    ) -> bool:
        """Switch to the fallback endpoint once when native auth fails early."""

        fallback = self._config.fallback_model
        if not self._fallback_applies(error, fallback, turn):
            return False
        context = self._context
        if context is None:
            return False
        old_client = self._client
        self._client = None
        if old_client is not None:
            try:
                await old_client.disconnect()
            except Exception as exc:
                logger.debug("[claude-code] disconnect before auth fallback failed: %s", exc)
        try:
            self._client = await self._connect(
                context,
                model=fallback,
                resume=self._claude_session_id,
                session_id=None,
            )
        except ProviderStartupError as exc:
            logger.warning("[claude-code] authentication fallback activation failed: %s", exc)
            return False
        ratified = await self._confirm_provider_extension(
            AUTH_FALLBACK_REQUEST_TYPE,
            {"model": fallback.model, "api_base": fallback.api_base},
        )
        if not ratified:
            # The host could not persist the switch; go back to the native
            # endpoint so the member does not silently run on an unrecorded one.
            logger.warning("[claude-code] host declined the authentication fallback; restoring the native endpoint")
            await self._reconnect_native_endpoint(context)
            return False
        self._active_model = fallback
        self._fallback_activated = True
        await self._emit(
            ProviderEvent(
                provider=PROVIDER_NAME,
                event_type="auth_fallback_activated",
                schema_version="1",
                payload={"model": fallback.model, "api_base": fallback.api_base},
            ),
            turn=turn,
        )
        return True

    async def _reconnect_native_endpoint(self, context: HarnessContext) -> None:
        """Drop the fallback client and resume the session on the native endpoint."""
        fallback_client = self._client
        self._client = None
        if fallback_client is not None:
            try:
                await fallback_client.disconnect()
            except Exception as exc:
                logger.debug("[claude-code] disconnect after declined fallback failed: %s", exc)
        try:
            self._client = await self._connect(
                context,
                model=self._primary_model,
                resume=self._claude_session_id,
                session_id=None,
            )
        except ProviderStartupError as exc:
            logger.warning("[claude-code] restoring the native endpoint failed: %s", exc)
            return
        self._active_model = self._primary_model
        self._fallback_activated = False

    # ------------------------------------------------------------------
    # Permission / user-input routing
    # ------------------------------------------------------------------

    async def _can_use_tool(self, tool_name: str, tool_input: dict[str, Any], permission_context: Any) -> Any:
        sdk = self._sdk
        try:
            return await self._route_permission(tool_name, tool_input, permission_context)
        except asyncio.CancelledError:
            return sdk.PermissionResultDeny(message="harness interaction cancelled", interrupt=False)
        except Exception as exc:
            logger.exception("[claude-code] permission routing for %s failed", tool_name)
            return sdk.PermissionResultDeny(message=f"harness interaction failed: {type(exc).__name__}")

    async def _route_permission(self, tool_name: str, tool_input: dict[str, Any], permission_context: Any) -> Any:
        sdk = self._sdk
        context = self._context
        capabilities = context.host_capabilities if context is not None else frozenset()
        active = self._active_turn
        turn_id = active.turn_id if active is not None else None
        tool_use_id = getattr(permission_context, "tool_use_id", None) or uuid.uuid4().hex
        arguments = to_json_object(tool_input)
        if tool_name == ASK_USER_TOOL_NAME and HostCapability.USER_INPUT in capabilities:
            questions = _questions(tool_input)
            request = UserInputRequest(
                request_id=f"claude-ask:{tool_use_id}",
                prompt=_render_questions(questions),
                provider_session_id=self._session_id,
                turn_id=turn_id,
                choices=_first_choices(questions),
                provider_data={"tool_name": tool_name, "call_id": tool_use_id, "input": arguments},
            )
            response = await self._request_interaction(request)
            if response is None or response.status is not InteractionResponseStatus.COMPLETED:
                return sdk.PermissionResultDeny(message="user input was not provided", interrupt=False)
            answers = _answers_from_response(json_value_to_builtin(response.content), questions)
            return sdk.PermissionResultAllow(updated_input={**tool_input, "answers": answers})
        if HostCapability.TOOL_APPROVAL in capabilities:
            request = ToolApprovalRequest(
                request_id=f"claude-approval:{tool_use_id}",
                call_id=tool_use_id,
                tool_name=tool_name,
                arguments=arguments,
                provider_session_id=self._session_id,
                turn_id=turn_id,
                provider_data={"suggestions": to_json_safe(getattr(permission_context, "suggestions", []))},
            )
            response = await self._request_interaction(request)
            if response is None:
                return sdk.PermissionResultDeny(message="no host approval handler", interrupt=False)
            if response.decision in (ToolApprovalDecision.ALLOW, ToolApprovalDecision.ALLOW_FOR_SESSION):
                updated = response.updated_arguments
                return sdk.PermissionResultAllow(
                    updated_input=json_value_to_builtin(updated) if updated is not None else None,
                )
            return sdk.PermissionResultDeny(
                message=response.reason or "denied by host",
                interrupt=response.interrupt or response.decision is ToolApprovalDecision.ABORT,
            )
        return sdk.PermissionResultAllow()


def _with_selection(model: ClaudeModelConfig | None, selection: ModelSelection) -> ClaudeModelConfig:
    """Return ``model`` with the fields ``selection`` sets replaced."""
    changes = {name: value for name, value in (("model", selection.model), ("effort", selection.effort)) if value}
    return dataclasses.replace(model or ClaudeModelConfig(), **changes)


def _questions(tool_input: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = tool_input.get("questions")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _render_questions(questions: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for question in questions:
        text = str(question.get("question") or "").strip()
        if not text:
            continue
        options = question.get("options")
        items = options if isinstance(options, list) else []
        labels = [str(item.get("label")) for item in items if isinstance(item, Mapping)]
        lines.append(f"{text} (options: {', '.join(labels)})" if labels else text)
    return "\n".join(lines) or "The agent is asking for your input."


async def _empty_prompt() -> AsyncIterator[dict[str, Any]]:
    """Provide the streaming prompt a transport keeps its session open with."""
    return
    yield {}  # type: ignore[unreachable]


def _incomplete_turn_error(*, settled: bool) -> TurnError:
    """Describe a message stream that ended before the turn did."""
    if settled:
        return TurnError(
            message="Claude Code ended the message stream before the turn finished",
            code="CLAUDE_INCOMPLETE_TURN",
            category="sdk_error",
        )
    return TurnError(
        message="Claude Code ended the response stream without a result",
        code="CLAUDE_MISSING_RESULT",
        category="sdk_error",
    )


def _first_choices(questions: list[dict[str, Any]]) -> tuple[str, ...]:
    if not questions:
        return ()
    options = questions[0].get("options")
    if not isinstance(options, list):
        return ()
    return tuple(str(item.get("label")) for item in options if isinstance(item, Mapping) and item.get("label"))


def _answers_from_response(content: Any, questions: list[dict[str, Any]]) -> dict[str, str]:
    """Normalize a host user-input response into Claude's ``answers`` mapping."""

    question_texts = [str(item.get("question") or "") for item in questions]
    if isinstance(content, Mapping):
        answers = content.get("answers")
        if isinstance(answers, Mapping):
            return {str(key): str(value) for key, value in answers.items()}
        if "answer" in content and question_texts:
            return {question_texts[0]: str(content["answer"])}
        return {str(key): str(value) for key, value in content.items()}
    if isinstance(content, list):
        return {question: str(answer) for question, answer in zip(question_texts, content)}
    if content is None:
        return {}
    if question_texts:
        return {question_texts[0]: str(content)}
    return {"answer": str(content)}


__all__ = ["ADAPTER_VERSION", "ASK_USER_TOOL_NAME", "MODEL_CHANGED_EVENT", "ClaudeCodeHarness", "TransportFactory"]
