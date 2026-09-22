# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bridge the public external-harness SPI to AgentTeam ``MemberRuntime``.

``HarnessIOAdapter`` already turns any ``HarnessProtocol`` into the
DeepAgent-style input/output contract.  This module adds the team-specific
layer on top of it: the member's child ``AgentSession`` (checkpoint sink and
team-context delivery baseline), the ``TeamContextTracker`` piggyback, the
external-runtime reliability loop, the optional trajectory recorder fed from
the protocol event stream and the authentication-fallback promotion hook.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import uuid
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable, Optional, Protocol, Sequence, runtime_checkable

from openjiuwen.agent_teams.external.cli_agent import TEAM_MCP_SERVER_NAME
from openjiuwen.agent_teams.harness.turn import MemberTurn, resolve_member_turn
from openjiuwen.agent_teams.team_context import TeamContextTracker
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.harness_protocol import (
    CheckpointReason,
    CheckpointSaveReceipt,
    DiagnosticEvent,
    HarnessCheckpoint,
    HarnessContext,
    HarnessCapability,
    HarnessEvent,
    HarnessModelControl,
    HarnessProtocol,
    HarnessState,
    HarnessStateError,
    HostCapability,
    InteractionResponseStatus,
    McpServerConfig,
    ModelSelection,
    ProviderEvent,
    ProviderInteractionRequest,
    ProviderInteractionResponse,
    ResumePolicy,
    SendReceipt,
    StateChangedEvent,
    TurnError,
    TurnEventKind,
    TurnLifecycleEvent,
    TurnResult,
    UnsupportedHarnessCapabilityError,
    json_value_to_builtin,
)
from openjiuwen.harness_providers.base import ProviderStartupError
from openjiuwen.harness_providers.inputs import harness_input_text
from openjiuwen.harness_providers.io_adapter import HarnessIOAdapter, to_harness_input

if TYPE_CHECKING:
    from openjiuwen.harness_providers.trajectory import HarnessTrajectoryRecorder

_EVENT_STATE = "harness.state"
_EVENT_ROUND = "harness.round"
_EVENT_NAMESPACE = "external_harness_runtime"
_EXTERNAL_RUNTIME_STATE_KEY = "external_runtime"
_EXTERNAL_BACKEND_KEY = "backend"
_EXTERNAL_CHECKPOINT_KEY = "checkpoint"
_AUTH_FALLBACK_EVENT = "auth_fallback_activated"
_AUTH_FALLBACK_REQUEST = "auth_fallback"
# Provider events carrying the model a member actually runs on, fed into the
# reliability context so failure reports name the endpoint instead of
# ``<unknown>``: Codex harnesses announce the thread's model
# (``session/model_changed``), and the Claude CLI reports the model it serves
# in every ``system/init`` message — the only source with a value when no
# model was configured explicitly.
_MODEL_CHANGED_EVENT = "session/model_changed"
_SYSTEM_INIT_EVENT = "system/init"

ContextFactory = Callable[
    [Any | None],
    HarnessContext | Awaitable[HarnessContext],
]
PromoteFallbackModel = Callable[[], Awaitable[bool]]


@runtime_checkable
class TeamContextAwareRuntime(Protocol):
    """Behavior used by coordination handlers to push roster announcements."""

    @property
    def state(self) -> HarnessState:
        """Return the current harness state."""
        ...

    async def announce_team_context(self) -> None:
        """Push the pending team roster announcement to the member."""
        ...


class _MemberSessionCheckpointSink:
    """Persist provider checkpoints into the member's child AgentSession."""

    def __init__(self, member_session: Any, *, backend: str) -> None:
        self._member_session = member_session
        self._backend = backend

    async def save(
        self,
        checkpoint: HarnessCheckpoint,
        *,
        reason: CheckpointReason,
        expected_storage_revision: str | None = None,
    ) -> CheckpointSaveReceipt:
        _ = reason, expected_storage_revision
        self._member_session.update_state(
            {
                _EXTERNAL_RUNTIME_STATE_KEY: {
                    _EXTERNAL_BACKEND_KEY: self._backend,
                    _EXTERNAL_CHECKPOINT_KEY: checkpoint_to_dict(checkpoint),
                }
            }
        )
        await self._member_session.commit()
        return CheckpointSaveReceipt(
            checkpoint_id=checkpoint.checkpoint_id,
            sequence=checkpoint.sequence,
            storage_revision=checkpoint.checkpoint_id,
        )


def checkpoint_to_dict(checkpoint: HarnessCheckpoint) -> dict[str, Any]:
    """Serialize a checkpoint envelope into plain JSON data."""
    return {
        "provider": checkpoint.provider,
        "schema_version": checkpoint.schema_version,
        "agent_id": checkpoint.agent_id,
        "host_session_id": checkpoint.host_session_id,
        "checkpoint_id": checkpoint.checkpoint_id,
        "sequence": checkpoint.sequence,
        "data": json_value_to_builtin(checkpoint.data),
        "provider_session_id": checkpoint.provider_session_id,
        "revision": checkpoint.revision,
    }


def checkpoint_from_dict(value: Any) -> HarnessCheckpoint | None:
    """Deserialize a checkpoint envelope written by ``checkpoint_to_dict``."""
    if not isinstance(value, dict):
        return None
    try:
        return HarnessCheckpoint(
            provider=str(value["provider"]),
            schema_version=str(value["schema_version"]),
            agent_id=str(value["agent_id"]),
            host_session_id=str(value["host_session_id"]),
            checkpoint_id=str(value["checkpoint_id"]),
            sequence=int(value["sequence"]),
            data=value.get("data") or {},
            provider_session_id=value.get("provider_session_id"),
            revision=value.get("revision"),
        )
    except (KeyError, TypeError, ValueError):
        team_logger.warning("ignoring malformed external runtime checkpoint: {}", value)
        return None


def read_member_checkpoint(member_session: Any, *, backend: str) -> HarnessCheckpoint | None:
    """Read the checkpoint this backend persisted into the member session."""
    state = member_session.get_state(_EXTERNAL_RUNTIME_STATE_KEY)
    if not isinstance(state, dict) or state.get(_EXTERNAL_BACKEND_KEY) != backend:
        return None
    return checkpoint_from_dict(state.get(_EXTERNAL_CHECKPOINT_KEY))


class ExternalHarnessMemberRuntime:
    """Project ``HarnessProtocol`` onto the internal team runtime seam.

    ``immediate=True`` is capability-aware: it starts normally from IDLE,
    steers only when the provider declares STEER, and otherwise becomes an
    explicit follow-up.  This preserves input delivery for providers such as
    DSH without falsely advertising mid-turn steering.

    Args:
        harness: The protocol implementation driving the member.
        context: A ready context or a factory receiving the team session.
        team_context_tracker: Tracker feeding team state into outbound
            messages; ``None`` disables team-state delivery.
        stop_on_unsupported_force_abort: Stop the whole cycle when the host
            asks for an immediate abort the provider cannot deliver.
        resume_external_backend: Require the provider to resume its persisted
            session from the member checkpoint instead of starting fresh.
        agent_kind: Reliability vocabulary for this member (``claude`` /
            ``codex``); ``None`` disables the external-runtime reliability loop.
        inject_mcp: Whether the spawn path should mount the team MCP server.
        mcp_server_name: Logical name of the team MCP server.
    """

    def __init__(
        self,
        *,
        harness: HarnessProtocol,
        context: HarnessContext | ContextFactory,
        team_context_tracker: TeamContextTracker | None = None,
        stop_on_unsupported_force_abort: bool = False,
        resume_external_backend: bool = False,
        agent_kind: str | None = None,
        cli_path: str | None = None,
        inject_mcp: bool = False,
        mcp_server_name: str = TEAM_MCP_SERVER_NAME,
    ) -> None:
        self._harness = harness
        self._context_source = context
        self._team_context_tracker = team_context_tracker
        self._resume_external_backend = resume_external_backend
        self._agent_kind = agent_kind
        self._cli_path = cli_path
        self.inject_mcp = inject_mcp
        self.mcp_server_name = mcp_server_name
        self._adapter = HarnessIOAdapter(
            harness,
            event_observer=self._on_event,
            auto_approve_tools=True,
            stop_on_unsupported_force_abort=stop_on_unsupported_force_abort,
            provider_interaction_handler=self._on_provider_interaction,
        )
        if isinstance(context, HarnessContext):
            self._member_name = context.agent_name
            self._member_agent_id: str | None = context.agent_id
        else:
            self._member_name = harness.card.name
            self._member_agent_id = None
        self._member_session: Any = None
        self._events = AsyncCallbackFramework(enable_metrics=False, enable_logging=False)
        self._stopped = True
        self._context_delivery_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._extra_mcp_servers: list[McpServerConfig] = []
        self._reliability_ctx: Any = None
        self._trajectory: HarnessTrajectoryRecorder | None = None
        self._promote_fallback_model: PromoteFallbackModel | None = None
        self._teardown_hooks: list[Callable[[], Awaitable[None]]] = []
        self._round_seq = 0
        self._current_round_id: int | None = None

    # ------------------------------------------------------------------
    # Read-only surface
    # ------------------------------------------------------------------

    @property
    def harness(self) -> HarnessProtocol:
        return self._harness

    @property
    def provider_name(self) -> str:
        """Return the provider card name (``claude-code`` / ``codex`` / ...)."""
        return self._harness.card.name

    @property
    def state(self) -> HarnessState:
        return self._harness.state

    @property
    def session_id(self) -> str | None:
        return self._harness.provider_session_id

    @property
    def trajectory_recorder(self) -> "HarnessTrajectoryRecorder | None":
        """Return the recorder turning this member's events into trajectory spans."""
        return self._trajectory

    @property
    def reliability_agent_kind(self) -> str | None:
        return self._agent_kind

    # ------------------------------------------------------------------
    # Pre-start bindings
    # ------------------------------------------------------------------

    def bind_team_context_tracker(self, tracker: TeamContextTracker | None) -> None:
        self._team_context_tracker = tracker

    def bind_mcp_servers(self, servers: Sequence[McpServerConfig]) -> None:
        """Mount additional MCP servers on the next ``start``."""
        self._extra_mcp_servers.extend(servers)

    def bind_trajectory_recorder(self, recorder: "HarnessTrajectoryRecorder | None") -> None:
        """Record this member's protocol event stream as trajectory spans.

        Binding a recorder also makes the host declare
        ``HostCapability.MODEL_REQUEST_OBSERVATION``, so the provider reports
        its model requests from the next ``start`` on.
        """
        self._trajectory = recorder

    def bind_fallback_promotion(self, promote: PromoteFallbackModel | None) -> None:
        """Persist the provider's authentication fallback before it commits.

        The provider asks through an ``auth_fallback`` provider interaction;
        the switch is ratified only when ``promote`` returns ``True``, otherwise
        the provider restores its native endpoint.
        """
        self._promote_fallback_model = promote

    def add_teardown_hook(self, hook: Callable[[], Awaitable[None]]) -> None:
        """Run ``hook`` after the harness stopped (observability receivers...)."""
        self._teardown_hooks.append(hook)

    def bind_reliability_context(
        self,
        *,
        session_id: str,
        team_backend: Any,
        leader_name: str,
        update_status_cb: Any,
        messager: Any,
    ) -> None:
        """Bind the reliability failure/retry delivery surface."""
        if self._agent_kind is None:
            return
        from openjiuwen.agent_teams.external.reliability import RuntimeReliabilityContext

        self._reliability_ctx = RuntimeReliabilityContext(
            member_name=self._member_name,
            team_name=team_backend.team_name if team_backend is not None else "",
            session_id=session_id,
            agent_kind=self._agent_kind,
            message_manager=team_backend.message_manager if team_backend is not None else None,
            messager=messager,
            leader_name=leader_name,
            update_status_cb=update_status_cb,
            trajectory_recorder=self._trajectory,
            cli_path=self._cli_path,
        )

    # ------------------------------------------------------------------
    # MemberRuntime: lifecycle
    # ------------------------------------------------------------------

    async def start(self, *, team_session: Optional[Any] = None) -> None:
        """Start the provider cycle and its single continuous event pump."""

        async with self._lifecycle_lock:
            await self._start(team_session)

    async def _start(self, team_session: Any | None) -> None:
        if not self._stopped:
            raise HarnessStateError("external harness member runtime is already started")
        async with self._context_delivery_lock:
            await self._finalize_member_session()
        context = await self._resolve_context(team_session)
        self._member_name = context.agent_name
        self._member_agent_id = context.agent_id
        await self._ensure_member_session(team_session)
        context = self._host_context(context)
        if self._reliability_ctx is not None:
            self._reliability_ctx.begin_attempt(phase="startup", round_id=None)
        try:
            await self._adapter.start(context)
        except BaseException as exc:
            await self._finalize_startup_failure(exc)
            try:
                async with self._context_delivery_lock:
                    await self._finalize_member_session()
            except Exception:
                team_logger.exception("external harness member session cleanup failed after start")
            raise
        self._stopped = False

    def _host_context(self, context: HarnessContext) -> HarnessContext:
        """Inject the host services this runtime owns into the provider context."""
        capabilities = set(context.host_capabilities)
        checkpoint = context.checkpoint
        checkpoint_sink = context.checkpoint_sink
        resume_policy = context.resume_policy
        member_session = self._member_session
        if member_session is not None:
            backend = self._harness.card.name
            if checkpoint is None:
                checkpoint = read_member_checkpoint(member_session, backend=backend)
            if checkpoint_sink is None:
                checkpoint_sink = _MemberSessionCheckpointSink(member_session, backend=backend)
        if checkpoint_sink is not None:
            capabilities.add(HostCapability.CHECKPOINT_SINK)
        if self._resume_external_backend:
            if checkpoint is None:
                team_logger.warning(
                    "[external-cli] member {} has no saved checkpoint; starting a new session",
                    self._member_name,
                )
            else:
                resume_policy = ResumePolicy.REQUIRE_RESUME
        elif checkpoint is not None and resume_policy is ResumePolicy.NEW:
            resume_policy = ResumePolicy.RESUME_IF_AVAILABLE
        mcp_servers = tuple(context.mcp_servers) + tuple(self._extra_mcp_servers)
        if mcp_servers:
            capabilities.add(HostCapability.MCP_SERVERS)
        if self._trajectory is not None:
            capabilities.add(HostCapability.MODEL_REQUEST_OBSERVATION)
        return dataclasses.replace(
            context,
            host_capabilities=frozenset(capabilities),
            resume_policy=resume_policy,
            checkpoint=checkpoint,
            checkpoint_sink=checkpoint_sink,
            mcp_servers=mcp_servers,
        )

    async def stop(self) -> None:
        """Stop the provider and close the projected MemberRuntime output."""

        async with self._lifecycle_lock:
            await self._stop()

    async def _stop(self) -> None:
        if self._stopped:
            async with self._context_delivery_lock:
                await self._finalize_member_session()
            return
        try:
            await self._adapter.stop()
        finally:
            await self._events.unregister_namespace(_EVENT_NAMESPACE)
            self._stopped = True
            if self._trajectory is not None:
                # A stop mid-turn emits no terminal turn event; end whatever
                # the recorder still holds open.
                self._trajectory.close()
            for hook in self._teardown_hooks:
                try:
                    await hook()
                except Exception:
                    team_logger.exception("external harness teardown hook failed")
            async with self._context_delivery_lock:
                await self._finalize_member_session()

    async def dispose(self) -> None:
        await self.stop()

    def outputs(self) -> AsyncIterator[Any]:
        return self._adapter.outputs()

    # ------------------------------------------------------------------
    # MemberRuntime: interaction
    # ------------------------------------------------------------------

    async def send(self, content: Any, *, immediate: bool = False) -> SendReceipt | None:
        """Send input with capability-aware steer/follow-up selection."""

        async with self._context_delivery_lock:
            if isinstance(content, InteractiveInput):
                return await self._adapter.send(content, immediate=immediate)
            external_input = to_harness_input(content)
            delivered_text = harness_input_text(external_input)
            pending_context = await self._pending_team_context()
            if pending_context:
                external_input = _prepend_context(external_input, pending_context)
            receipt = await self._adapter.send(external_input, immediate=immediate)
            # Both halves are recorded, and the message first so it is the one
            # the turn states as its input. A provider splits the delivery into
            # a message per block, so the combined text would match none of
            # them and the turn would read as if nobody had said anything.
            self._record_input(receipt, delivered_text)
            if pending_context:
                self._record_input(receipt, pending_context)
                await self._commit_team_context()
            return receipt

    async def announce_team_context(self) -> None:
        async with self._context_delivery_lock:
            pending = await self._pending_team_context()
            if not pending:
                return
            receipt = await self._adapter.send(pending, immediate=False)
            self._record_input(receipt, pending)
            await self._commit_team_context()

    async def abort(self, *, immediate: bool = False) -> None:
        await self._adapter.abort(immediate=immediate)

    async def pause(self) -> None:
        await self._adapter.pause()

    async def resume(self, *, query: Any | None = None) -> None:
        await self._adapter.resume(query=query)

    async def set_model_selection(self, selection: ModelSelection) -> bool:
        """Switch the running harness to ``selection`` before its next turn.

        Returns:
            True when the harness switched (or queued the switch for the next
            turn); False when it is not running, so the selection only takes
            effect through the persisted member options at the next start.
        """
        harness = self._harness
        if not harness.card.supports(HarnessCapability.MODEL_SELECTION):
            raise UnsupportedHarnessCapabilityError(f"{harness.card.name} does not support model selection")
        if not isinstance(harness, HarnessModelControl) or harness.state is HarnessState.TERMINATED:
            return False
        await harness.set_model(selection)
        return True

    async def subscribe(
        self,
        *,
        on_state: Callable[..., Any] | None = None,
        on_round: Callable[..., Any] | None = None,
    ) -> None:
        if on_state is not None:
            await self._events.register(_EVENT_STATE, on_state, namespace=_EVENT_NAMESPACE)
        if on_round is not None:
            await self._events.register(_EVENT_ROUND, on_round, namespace=_EVENT_NAMESPACE)

    # ------------------------------------------------------------------
    # Event observation
    # ------------------------------------------------------------------

    async def _on_event(self, envelope: HarnessEvent) -> None:
        await self._record_event(envelope)
        payload = envelope.event
        if isinstance(payload, StateChangedEvent):
            await self._events.trigger(
                _EVENT_STATE,
                old=payload.old,
                new=payload.new,
                session_id=envelope.provider_session_id,
            )
        elif isinstance(payload, TurnLifecycleEvent):
            await self._on_turn_event(envelope, payload)
        elif isinstance(payload, DiagnosticEvent):
            await self._on_diagnostic(payload)
        elif isinstance(payload, ProviderEvent) and payload.event_type == _AUTH_FALLBACK_EVENT:
            team_logger.info(
                "[external-cli] member {} switched to the authentication fallback {}",
                self._member_name,
                payload.payload,
            )
        elif isinstance(payload, ProviderEvent) and (
            payload.event_type == _MODEL_CHANGED_EVENT or payload.event_type == _SYSTEM_INIT_EVENT
        ):
            ctx = self._reliability_ctx
            model = payload.payload.get("model")
            if ctx is not None and isinstance(model, str) and model.strip():
                ctx.update_model(model)

    async def _on_turn_event(self, envelope: HarnessEvent, payload: TurnLifecycleEvent) -> None:
        kind = _member_round_kind(payload.kind)
        if payload.kind is TurnEventKind.STARTED:
            self._round_seq += 1
            self._current_round_id = self._round_seq
            if self._reliability_ctx is not None:
                self._reliability_ctx.begin_attempt(phase="turn", round_id=self._current_round_id)
        elif payload.kind is TurnEventKind.FAILED:
            await self._finalize_turn_failure(payload.result)
        await self._events.trigger(
            _EVENT_ROUND,
            kind=kind,
            round_id=envelope.turn_id,
            result=payload.result,
        )

    async def _on_diagnostic(self, payload: DiagnosticEvent) -> None:
        ctx = self._reliability_ctx
        data = json_value_to_builtin(payload.data)
        if ctx is None or not isinstance(data, dict) or data.get("kind") != "retrying":
            return
        category = str(data.get("category") or "sdk_error")
        await ctx.publish_retrying(
            category=category,
            reason=_failure_reason(message=payload.message, code=data.get("code")),
            summary=f"{self._member_name} {self._agent_kind} SDK retrying: {category}",
        )

    async def _on_provider_interaction(self, request: ProviderInteractionRequest) -> ProviderInteractionResponse:
        """Answer provider extension requests; only ``auth_fallback`` is understood."""
        if request.request_type != _AUTH_FALLBACK_REQUEST:
            return ProviderInteractionResponse(request_id=request.request_id, status=InteractionResponseStatus.DECLINED)
        persisted = await self._persist_fallback()
        status = InteractionResponseStatus.COMPLETED if persisted else InteractionResponseStatus.DECLINED
        return ProviderInteractionResponse(request_id=request.request_id, status=status)

    async def _persist_fallback(self) -> bool:
        promote = self._promote_fallback_model
        if promote is None:
            return True
        try:
            promoted = await promote()
        except Exception:
            team_logger.exception(
                "[external-cli] member {} failed to persist the authentication fallback",
                self._member_name,
            )
            return False
        if not promoted:
            team_logger.warning(
                "[external-cli] member {} could not persist the authentication fallback; "
                "the provider keeps its native endpoint",
                self._member_name,
            )
        return promoted

    async def _finalize_turn_failure(self, result: TurnResult | None) -> None:
        ctx = self._reliability_ctx
        if ctx is None or ctx.has_finalized:
            return
        error = result.error if result is not None else None
        category, reason = _classify(error)
        summary = f"{self._member_name} {self._agent_kind} turn failed: {reason.message or category}"
        await ctx.finalize_failure(category=category, reason=reason, summary=summary)

    async def _finalize_startup_failure(self, exc: BaseException) -> None:
        ctx = self._reliability_ctx
        if ctx is None or ctx.has_finalized:
            return
        error = exc.error if isinstance(exc, ProviderStartupError) else None
        category, reason = _classify(error)
        if error is None:
            reason = _failure_reason(message=str(exc) or type(exc).__name__, sdk_error_type=type(exc).__name__)
        await ctx.finalize_failure(
            category=category,
            reason=reason,
            summary=f"{self._member_name} {self._agent_kind} startup failed: {type(exc).__name__}",
        )
        await ctx.mark_member_error()

    def _record_input(self, receipt: SendReceipt | None, text: str) -> None:
        """Tell the recorder what the host delivered into the member's turn."""
        recorder = self._trajectory
        if recorder is None or receipt is None:
            return
        try:
            recorder.record_input(receipt.turn_id, text)
        except Exception:
            team_logger.debug("[{}] trajectory recorder rejected an input", self._member_name, exc_info=True)

    async def _record_event(self, envelope: HarnessEvent) -> None:
        recorder = self._trajectory
        if recorder is None:
            return
        payload = envelope.event
        try:
            if isinstance(payload, TurnLifecycleEvent) and payload.kind is TurnEventKind.STARTED:
                # Every provider turn is a trajectory turn of this member; the
                # counter lives in the member session so numbering survives a
                # restart.
                turn = await self._open_member_turn()
                recorder.record_turn_identity(
                    envelope.turn_id or "",
                    turn_id=turn.turn_id,
                    turn_number=turn.turn_number,
                )
            recorder.observe(envelope)
        except Exception:
            team_logger.debug("[{}] trajectory recorder failed on an event", self._member_name, exc_info=True)

    async def _open_member_turn(self) -> MemberTurn:
        """Open the next trajectory turn and checkpoint the advanced counter."""
        member_session = self._member_session
        turn, _ = resolve_member_turn(member_session, continues_turn=False)
        if member_session is None:
            return turn
        try:
            await member_session.commit()
        except Exception:
            team_logger.debug("[{}] turn state commit failed", self._member_name, exc_info=True)
        return turn

    # ------------------------------------------------------------------
    # Member session / team context
    # ------------------------------------------------------------------

    async def _resolve_context(self, team_session: Any | None) -> HarnessContext:
        source = self._context_source
        if isinstance(source, HarnessContext):
            return source
        context = source(team_session)
        if inspect.isawaitable(context):
            context = await context
        if not isinstance(context, HarnessContext):
            raise TypeError("external harness context factory must return HarnessContext")
        return context

    async def _ensure_member_session(self, team_session: Any | None) -> Any:
        if self._member_session is not None:
            return self._member_session
        if team_session is None or not self._member_agent_id or not hasattr(team_session, "create_agent_session"):
            return None
        member_session = team_session.create_agent_session(
            agent_id=self._member_agent_id,
            share_stream_writer=False,
        )
        await member_session.pre_run()
        self._member_session = member_session
        return member_session

    async def _finalize_member_session(self) -> None:
        member_session = self._member_session
        if member_session is None:
            return
        await member_session.post_run()
        if self._member_session is member_session:
            self._member_session = None

    async def _pending_team_context(self) -> str | None:
        if self._team_context_tracker is None or self._member_session is None:
            return None
        return await self._team_context_tracker.pending_text(self._member_session)

    async def _commit_team_context(self) -> None:
        if self._team_context_tracker is None or self._member_session is None:
            return
        await self._team_context_tracker.commit(self._member_session)

    # ------------------------------------------------------------------
    # MemberRuntime: interrupt helpers and no-op hooks
    # ------------------------------------------------------------------

    def has_pending_interrupt(self) -> bool:
        return self._adapter.has_pending_interrupt()

    def is_pending_interrupt_resume_valid(self, user_input: Any) -> bool:
        return self._adapter.is_pending_interrupt_resume_valid(user_input)

    # External harnesses own their own rails, memory, workspace, and tools.
    @staticmethod
    def init_cwd_for_round() -> None:
        return None

    @staticmethod
    def find_rails(rail_type: type) -> list[Any]:
        _ = rail_type
        return []

    async def register_rail(self, rail: Any) -> None:
        _ = rail

    async def unregister_rail(self, rail: Any) -> None:
        _ = rail

    @staticmethod
    def register_member_tools(memory_manager: Any) -> None:
        _ = memory_manager

    async def inject_member_memory(self, memory_manager: Any, query: str) -> None:
        _ = memory_manager, query

    @staticmethod
    def set_background_task_controller(controller: Any) -> None:
        _ = controller

    @property
    def workspace(self) -> Optional[Any]:
        return None

    @property
    def sys_operation(self) -> Optional[Any]:
        return None


def _prepend_context(content: Any, prefix: str) -> Any:
    from openjiuwen.harness_protocol import HarnessInput

    value = json_value_to_builtin(content.content)
    if isinstance(value, str):
        combined: Any = f"{prefix}\n\n{value}" if value else prefix
    elif isinstance(value, list) and all(isinstance(block, dict) for block in value):
        combined = [{"type": "text", "text": prefix}, *value]
    else:
        combined = f"{prefix}\n\n{json.dumps(value, ensure_ascii=False)}"
    return HarnessInput(content=combined, metadata=content.metadata)


def _member_round_kind(kind: TurnEventKind) -> str:
    return {
        TurnEventKind.STARTED: "started",
        TurnEventKind.PAUSED: "paused",
        TurnEventKind.RESUMED: "started",
        TurnEventKind.FINISHED: "finished",
        TurnEventKind.ABORTED: "aborted",
        TurnEventKind.FAILED: "failed",
    }[kind]


def _failure_reason(*, message: str, code: Any = None, sdk_error_type: Any = None, http_status: Any = None) -> Any:
    from openjiuwen.agent_teams.schema.external_runtime_reliability import ExternalRuntimeFailureReason

    return ExternalRuntimeFailureReason(
        message=message or "",
        sdk_error_type=str(sdk_error_type or ""),
        sdk_error_code=str(code or ""),
        http_status=http_status if isinstance(http_status, int) else None,
    )


def _classify(error: TurnError | None) -> tuple[str, Any]:
    """Translate a protocol ``TurnError`` into the team reliability vocabulary."""
    from openjiuwen.agent_teams.schema.external_runtime_reliability import USER_ACTION_REQUIRED

    if error is None:
        return "sdk_error", _failure_reason(message="external harness turn failed without a structured error")
    category = error.category if error.category in USER_ACTION_REQUIRED else "sdk_error"
    provider_data = json_value_to_builtin(error.provider_data)
    data = provider_data if isinstance(provider_data, dict) else {}
    return category, _failure_reason(
        message=error.message,
        code=error.code,
        sdk_error_type=data.get("sdk_error_type"),
        http_status=data.get("http_status"),
    )


__all__ = [
    "ContextFactory",
    "ExternalHarnessMemberRuntime",
    "TeamContextAwareRuntime",
    "checkpoint_from_dict",
    "checkpoint_to_dict",
    "read_member_checkpoint",
]
