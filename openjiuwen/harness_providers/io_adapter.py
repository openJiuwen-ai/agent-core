# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Project a ``HarnessProtocol`` onto the DeepAgent input/output contract.

DeepAgent hosts speak a narrow dialect: inputs are user text or an
``InteractiveInput`` answering a pending interrupt; outputs are
``OutputSchema`` chunks (``llm_output`` / ``llm_reasoning`` / ``tool_call`` /
``tool_result`` / ``__interaction__``).  :class:`HarnessIOAdapter` translates
that dialect to and from the provider-neutral protocol so any third-party
harness can be driven exactly like an in-process DeepAgent.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any, AsyncIterator, Awaitable, Callable

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.common.logging import LazyLogger, LogManager
from openjiuwen.core.session.interaction.interaction import InteractionOutput
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.harness_protocol import (
    AbortMode,
    DeliveryMode,
    DynamicToolCallRequest,
    DynamicToolCallResponse,
    HarnessCapability,
    HarnessContext,
    HarnessEvent,
    HarnessInput,
    HarnessInteractionRequest,
    HarnessInteractionResponse,
    HarnessProtocol,
    HarnessState,
    HarnessStateError,
    HostCapability,
    InteractionCancelReason,
    InteractionResponseStatus,
    ItemEventKind,
    ItemLifecycleEvent,
    McpElicitationRequest,
    McpElicitationResponse,
    OutputChannel,
    OutputEvent,
    OutputOperation,
    ProviderInteractionRequest,
    ProviderInteractionResponse,
    SendReceipt,
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolApprovalResponse,
    UnsupportedHarnessCapabilityError,
    UserInputRequest,
    UserInputResponse,
    json_value_to_builtin,
)

logger = LazyLogger(lambda: LogManager.get_logger("harness_providers"))

EventObserver = Callable[[HarnessEvent], Awaitable[None]]
ProviderInteractionHandler = Callable[[ProviderInteractionRequest], Awaitable[ProviderInteractionResponse]]
INTERACTIVE_INPUT_KIND = "interactive_input"
_END: Any = object()


class _OutputIterator:
    """Wrap an asyncio queue as a single-consumer async iterator."""

    __slots__ = ("_queue",)

    def __init__(self, queue: asyncio.Queue[Any]) -> None:
        self._queue = queue

    def __aiter__(self) -> "_OutputIterator":
        return self

    async def __anext__(self) -> Any:
        item = await self._queue.get()
        if item is _END:
            raise StopAsyncIteration
        return item


@dataclasses.dataclass(slots=True)
class _PendingInteraction:
    request: HarnessInteractionRequest
    future: asyncio.Future[HarnessInteractionResponse]


class HarnessIOAdapter:
    """DeepAgent-style facade over one ``HarnessProtocol`` instance.

    The adapter owns the single continuous ``events()`` consumer, projects
    provider events onto ``OutputSchema`` chunks, and acts as the host
    ``HarnessInteractionHandler`` so a ``UserInputRequest`` surfaces as an
    ``__interaction__`` chunk that an ``InteractiveInput`` later resolves.

    Args:
        harness: The protocol implementation to drive.
        event_observer: Optional coroutine invoked with every raw event before
            projection (lifecycle callbacks, telemetry bridges).
        auto_approve_tools: When ``True`` provider tool-approval requests are
            allowed without asking; when ``False`` they surface as
            ``__interaction__`` chunks resolved by ``{"approved": bool}``.
        stop_on_unsupported_force_abort: Stop the whole cycle when the host asks
            for an immediate abort the provider cannot deliver.
        provider_interaction_handler: Optional coroutine answering provider
            extension requests. When set the adapter declares
            ``HostCapability.PROVIDER_INTERACTION``; otherwise every
            ``ProviderInteractionRequest`` is declined.
    """

    def __init__(
        self,
        harness: HarnessProtocol,
        *,
        event_observer: EventObserver | None = None,
        auto_approve_tools: bool = True,
        stop_on_unsupported_force_abort: bool = False,
        provider_interaction_handler: ProviderInteractionHandler | None = None,
    ) -> None:
        self._harness = harness
        self._event_observer = event_observer
        self._auto_approve_tools = auto_approve_tools
        self._provider_interaction_handler = provider_interaction_handler
        self._stop_on_unsupported_force_abort = stop_on_unsupported_force_abort
        self._output_queue: asyncio.Queue[Any] = asyncio.Queue()
        self._event_task: asyncio.Task[None] | None = None
        self._stopped = True
        self._output_index = 0
        self._output_text: dict[str, str] = {}
        self._pending: dict[str, _PendingInteraction] = {}
        self._lifecycle_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Read-only surface
    # ------------------------------------------------------------------

    @property
    def harness(self) -> HarnessProtocol:
        return self._harness

    @property
    def state(self) -> HarnessState:
        return self._harness.state

    @property
    def session_id(self) -> str | None:
        return self._harness.provider_session_id

    @property
    def pending_interrupt_ids(self) -> tuple[str, ...]:
        """Return the ids of interactions waiting for an ``InteractiveInput``."""
        return tuple(self._pending)

    def has_pending_interrupt(self) -> bool:
        return bool(self._pending)

    def is_pending_interrupt_resume_valid(self, user_input: Any) -> bool:
        """Return whether ``user_input`` answers at least one pending interaction."""
        if not isinstance(user_input, InteractiveInput) or not self._pending:
            return False
        if user_input.raw_inputs is not None:
            return len(self._pending) == 1
        return any(key in self._pending for key in user_input.user_inputs)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def prepare_context(self, context: HarnessContext) -> HarnessContext:
        """Return ``context`` with this adapter installed as the interaction host."""
        capabilities = set(context.host_capabilities) | {HostCapability.USER_INPUT}
        if not self._auto_approve_tools:
            capabilities.add(HostCapability.TOOL_APPROVAL)
        if self._provider_interaction_handler is not None:
            capabilities.add(HostCapability.PROVIDER_INTERACTION)
        interactions = context.interactions if context.interactions is not None else self
        return dataclasses.replace(
            context,
            host_capabilities=frozenset(capabilities),
            interactions=interactions,
        )

    async def start(self, context: HarnessContext) -> None:
        """Start the provider cycle and its single continuous event pump."""
        async with self._lifecycle_lock:
            if not self._stopped:
                raise HarnessStateError("harness IO adapter is already started")
            self._output_queue = asyncio.Queue()
            self._output_index = 0
            self._output_text.clear()
            prepared = self.prepare_context(context)
            try:
                await self._harness.start(prepared)
                cursor = self._harness.events()
            except BaseException:
                await self._safe_stop_harness()
                raise
            self._stopped = False
            self._event_task = asyncio.create_task(
                self._pump_events(cursor),
                name=f"harness_io_adapter_events[{context.agent_name}]",
            )

    async def stop(self) -> None:
        """Stop the provider and close the projected output stream."""
        if asyncio.current_task() is self._event_task:
            raise HarnessStateError("event observers must schedule adapter stop from a separate task")
        async with self._lifecycle_lock:
            if self._stopped:
                return
            self._fail_pending(InteractionCancelReason.HARNESS_STOPPED)
            await self._harness.stop()
            task = self._event_task
            self._event_task = None
            if task is not None:
                try:
                    await task
                except Exception:
                    logger.exception("harness IO adapter event pump failed during stop")
            self._output_queue.put_nowait(_END)
            self._stopped = True

    async def _safe_stop_harness(self) -> None:
        try:
            await self._harness.stop()
        except Exception:
            logger.exception("harness cleanup failed after start")

    def outputs(self) -> AsyncIterator[OutputSchema]:
        """Return the queue-backed single-consumer output iterator."""
        return _OutputIterator(self._output_queue)

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------

    async def send(self, content: Any, *, immediate: bool = False) -> SendReceipt | None:
        """Deliver user text, a raw ``HarnessInput`` or an interrupt answer.

        Returns the provider receipt for a queued or steered input, and
        ``None`` when an ``InteractiveInput`` merely resolved pending
        interactions without reaching the provider.
        """
        if isinstance(content, InteractiveInput):
            resolved = self._resolve_pending(content)
            if resolved:
                return None
            external_input = HarnessInput(
                content=content.model_dump(mode="json"),
                metadata={"kind": INTERACTIVE_INPUT_KIND},
            )
        else:
            external_input = to_harness_input(content)
        mode = self.delivery_mode(immediate=immediate)
        try:
            return await self._harness.send(external_input, mode=mode)
        except HarnessStateError:
            # A terminal event may win the race after a RUNNING snapshot but
            # before provider STEER acceptance.  Retry only when the provider
            # confirms it is now IDLE; a rejected command was not accepted.
            if mode is not DeliveryMode.STEER or self._harness.state is not HarnessState.IDLE:
                raise
            return await self._harness.send(external_input, mode=DeliveryMode.AUTO)

    def delivery_mode(self, *, immediate: bool) -> DeliveryMode:
        """Pick the delivery mode the provider can honor for ``immediate``."""
        if self._harness.state is not HarnessState.RUNNING:
            return DeliveryMode.AUTO
        if immediate and self._harness.card.supports(HarnessCapability.STEER):
            return DeliveryMode.STEER
        return DeliveryMode.FOLLOW_UP

    async def abort(self, *, immediate: bool = False) -> None:
        capability = HarnessCapability.FORCE_ABORT if immediate else HarnessCapability.GRACEFUL_ABORT
        fallback = HarnessCapability.GRACEFUL_ABORT if immediate else HarnessCapability.FORCE_ABORT
        if self._harness.card.supports(capability):
            mode = AbortMode.FORCE if immediate else AbortMode.GRACEFUL
            await self._harness.abort(mode=mode)
            return
        if self._harness.card.supports(fallback):
            mode = AbortMode.GRACEFUL if immediate else AbortMode.FORCE
            await self._harness.abort(mode=mode)
            return
        if self._harness.state is not HarnessState.RUNNING:
            return
        if immediate and self._stop_on_unsupported_force_abort:
            await self.stop()
            return
        raise UnsupportedHarnessCapabilityError(
            f"harness {self._harness.card.name!r} does not support {capability.value}"
        )

    async def pause(self) -> None:
        if self._harness.state is not HarnessState.RUNNING:
            return
        if not self._harness.card.supports(HarnessCapability.PAUSE_RESUME):
            raise UnsupportedHarnessCapabilityError(
                f"harness {self._harness.card.name!r} does not support pause/resume"
            )
        await self._harness.pause()

    async def resume(self, *, query: Any | None = None) -> None:
        if self._harness.state is not HarnessState.PAUSED and query is None:
            return
        if not self._harness.card.supports(HarnessCapability.PAUSE_RESUME):
            raise UnsupportedHarnessCapabilityError(
                f"harness {self._harness.card.name!r} does not support pause/resume"
            )
        external_query = None if query is None else to_harness_input(query)
        await self._harness.resume(query=external_query)

    # ------------------------------------------------------------------
    # HarnessInteractionHandler
    # ------------------------------------------------------------------

    async def handle(self, request: HarnessInteractionRequest) -> HarnessInteractionResponse:
        if isinstance(request, ToolApprovalRequest) and self._auto_approve_tools:
            return ToolApprovalResponse(request_id=request.request_id, decision=ToolApprovalDecision.ALLOW)
        if isinstance(request, McpElicitationRequest):
            return McpElicitationResponse(request_id=request.request_id, status=InteractionResponseStatus.DECLINED)
        if isinstance(request, DynamicToolCallRequest):
            return DynamicToolCallResponse(
                request_id=request.request_id,
                status=InteractionResponseStatus.DECLINED,
                is_error=True,
                error_message="dynamic tool calls are not routed by the harness IO adapter",
            )
        if isinstance(request, ProviderInteractionRequest):
            handler = self._provider_interaction_handler
            if handler is None:
                return ProviderInteractionResponse(
                    request_id=request.request_id,
                    status=InteractionResponseStatus.DECLINED,
                )
            return await handler(request)
        if request.request_id in self._pending:
            raise HarnessStateError(f"interaction {request.request_id!r} is already pending")
        loop = asyncio.get_running_loop()
        pending = _PendingInteraction(request=request, future=loop.create_future())
        self._pending[request.request_id] = pending
        await self._output_queue.put(self._interaction_chunk(request))
        try:
            return await pending.future
        finally:
            self._pending.pop(request.request_id, None)

    async def cancel(
        self,
        request_id: str,
        *,
        reason: InteractionCancelReason = InteractionCancelReason.PROVIDER_WITHDREW,
    ) -> None:
        pending = self._pending.pop(request_id, None)
        if pending is None or pending.future.done():
            return
        pending.future.set_result(_cancelled_response(pending.request))
        logger.debug("harness interaction %s cancelled: %s", request_id, reason.value)

    def _fail_pending(self, reason: InteractionCancelReason) -> None:
        for request_id in list(self._pending):
            pending = self._pending.pop(request_id)
            if not pending.future.done():
                pending.future.set_result(_cancelled_response(pending.request))
        logger.debug("harness pending interactions released: %s", reason.value)

    def _resolve_pending(self, user_input: InteractiveInput) -> bool:
        """Answer pending interactions from ``user_input``; ``True`` when any matched."""
        answered = False
        if user_input.raw_inputs is not None and len(self._pending) == 1:
            request_id = next(iter(self._pending))
            answered = self._answer(request_id, user_input.raw_inputs)
        for request_id, value in user_input.user_inputs.items():
            if request_id in self._pending:
                answered = self._answer(request_id, value) or answered
        return answered

    def _answer(self, request_id: str, value: Any) -> bool:
        pending = self._pending.get(request_id)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(_response_for(pending.request, value))
        return True

    # ------------------------------------------------------------------
    # Event projection
    # ------------------------------------------------------------------

    async def _pump_events(self, cursor: Any) -> None:
        try:
            async for envelope in cursor:
                observer = self._event_observer
                if observer is not None:
                    await observer(envelope)
                payload = envelope.event
                if isinstance(payload, OutputEvent):
                    chunk = self._project_output(payload)
                elif isinstance(payload, ItemLifecycleEvent):
                    chunk = self._project_item(envelope.item_id, payload)
                else:
                    chunk = None
                if chunk is not None:
                    await self._output_queue.put(chunk)
        finally:
            await cursor.aclose()

    def _project_output(self, output: OutputEvent) -> OutputSchema | None:
        value = json_value_to_builtin(output.content)
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        previous = self._output_text.get(output.output_id, "")
        if output.operation is OutputOperation.DELTA:
            emitted = text
            self._output_text[output.output_id] = previous + text
        elif not previous:
            emitted = text
            self._output_text[output.output_id] = text
        elif text.startswith(previous):
            emitted = text[len(previous):]
            self._output_text[output.output_id] = text
        else:
            # OutputSchema has append-only semantics.  The protocol event stays
            # authoritative; avoid duplicating a FINAL snapshot after its deltas.
            return None
        if not emitted:
            return None
        chunk_type = "llm_reasoning" if output.channel is OutputChannel.REASONING else "llm_output"
        return OutputSchema(
            type=chunk_type,
            index=self._next_output_index(),
            payload={
                "content": emitted,
                "result_type": "answer",
                "output_id": output.output_id,
                "operation": output.operation.value,
            },
        )

    def _project_item(self, item_id: str | None, item: ItemLifecycleEvent) -> OutputSchema | None:
        if item.item_type != "tool":
            return None
        data = json_value_to_builtin(item.data)
        if not isinstance(data, dict):
            return None
        if item.kind is ItemEventKind.STARTED:
            arguments = data.get("arguments")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
            return OutputSchema(
                type="tool_call",
                index=self._next_output_index(),
                payload={
                    "name": data.get("name") or data.get("tool_name") or "unknown",
                    "arguments": arguments,
                    "tool_call_id": item_id or "",
                },
            )
        if item.kind is ItemEventKind.COMPLETED:
            return OutputSchema(
                type="tool_result",
                index=self._next_output_index(),
                payload={
                    "tool_name": data.get("tool_name") or data.get("name") or "unknown",
                    "result": data.get("result"),
                    "tool_call_id": item_id or "",
                },
            )
        return None

    def _interaction_chunk(self, request: HarnessInteractionRequest) -> OutputSchema:
        if isinstance(request, UserInputRequest):
            value: dict[str, Any] = {
                "kind": "user_input",
                "prompt": request.prompt,
                "choices": list(request.choices),
                "provider_data": json_value_to_builtin(request.provider_data),
            }
        else:
            approval = request  # ToolApprovalRequest
            value = {
                "kind": "tool_approval",
                "tool_name": approval.tool_name,
                "arguments": json_value_to_builtin(approval.arguments),
                "reason": approval.reason,
                "provider_data": json_value_to_builtin(approval.provider_data),
            }
        return OutputSchema(
            type=INTERACTION,
            index=self._next_output_index(),
            payload=InteractionOutput(id=request.request_id, value=value),
        )

    def _next_output_index(self) -> int:
        index = self._output_index
        self._output_index += 1
        return index


def to_harness_input(content: Any) -> HarnessInput:
    """Wrap DeepAgent-style content as a protocol input."""
    if isinstance(content, HarnessInput):
        return content
    if isinstance(content, (str, int, float, bool, list, tuple, dict)) or content is None:
        return HarnessInput(content=content)
    return HarnessInput(content=str(content))


def _response_for(request: HarnessInteractionRequest, value: Any) -> HarnessInteractionResponse:
    if isinstance(request, ToolApprovalRequest):
        approved = value.get("approved", True) if isinstance(value, dict) else bool(value)
        decision = ToolApprovalDecision.ALLOW if approved else ToolApprovalDecision.DENY
        updated = value.get("updated_arguments") if isinstance(value, dict) else None
        feedback = value.get("feedback") if isinstance(value, dict) else None
        return ToolApprovalResponse(
            request_id=request.request_id,
            decision=decision,
            updated_arguments=updated if isinstance(updated, dict) else None,
            reason=str(feedback) if feedback else None,
        )
    return UserInputResponse(
        request_id=request.request_id,
        status=InteractionResponseStatus.COMPLETED,
        content=_json_ready(value),
    )


def _cancelled_response(request: HarnessInteractionRequest) -> HarnessInteractionResponse:
    if isinstance(request, ToolApprovalRequest):
        return ToolApprovalResponse(
            request_id=request.request_id,
            decision=ToolApprovalDecision.DENY,
            reason="interaction cancelled",
        )
    return UserInputResponse(request_id=request.request_id, status=InteractionResponseStatus.CANCELLED)


def _json_ready(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return value


__all__ = [
    "EventObserver",
    "HarnessIOAdapter",
    "INTERACTIVE_INPUT_KIND",
    "ProviderInteractionHandler",
    "to_harness_input",
]
