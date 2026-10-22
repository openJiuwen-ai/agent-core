# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Extension event bus registration, matching, and dispatch."""

from __future__ import annotations

import inspect
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeAlias, cast

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError, raise_error
from openjiuwen.core.events.bootstrap import register_builtins
from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOpsBuilder
from openjiuwen.core.events.protocol import EventHandler
from openjiuwen.core.events.types import (
    OPS_BUILD_EVENT_TYPE,
    Event,
    EventOutput,
    EventType,
    HandlerId,
    HandlerLevel,
    MatchPattern,
    MatchPatternFunc,
    PackageMetadata,
    SessionContext,
)
from openjiuwen.core.runner.callback.decorator import WrapHandler
from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework

if TYPE_CHECKING:
    from openjiuwen.core.single_agent import Session

_EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_PATTERN_SCALAR_TYPES = (bool, int, float, str)


EventDispatcher: TypeAlias = Callable[..., Awaitable[EventOutput | None]]


@dataclass(frozen=True, slots=True)
class _EventRegistration:
    matcher: MatchPatternFunc
    dispatch: EventDispatcher


@dataclass(frozen=True, slots=True)
class _HandlerRegistration:
    event_type: EventType
    match_pattern: MatchPattern
    wrap_handler: WrapHandler
    package_metadata: PackageMetadata | None


def _wrap_registry_key(event_type: EventType) -> str:
    return f"__wrap__:{event_type}"


def _freeze_pattern_value(value: object, active_ids: set[int]) -> object:
    if value is None or isinstance(value, _PATTERN_SCALAR_TYPES):
        return value

    if isinstance(value, Mapping):
        value_id = id(value)
        if value_id in active_ids:
            raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="match pattern contains a cycle")
        active_ids.add(value_id)
        try:
            frozen: dict[str, object] = {}
            for key, nested_value in value.items():
                if not isinstance(key, str):
                    raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="match pattern keys must be strings")
                frozen[key] = _freeze_pattern_value(nested_value, active_ids)
            return MappingProxyType(frozen)
        finally:
            active_ids.remove(value_id)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        value_id = id(value)
        if value_id in active_ids:
            raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="match pattern contains a cycle")
        active_ids.add(value_id)
        try:
            return tuple(_freeze_pattern_value(item, active_ids) for item in value)
        finally:
            active_ids.remove(value_id)

    raise_error(
        StatusCode.EVENTS_REGISTRATION_INVALID,
        reason=f"unsupported match pattern value: {type(value).__name__}",
    )
    return value


def _freeze_match_pattern(match_pattern: MatchPattern) -> MatchPattern:
    if not isinstance(match_pattern, Mapping):
        raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="match pattern must be a mapping")
    frozen = _freeze_pattern_value(match_pattern, set())
    if not isinstance(frozen, Mapping):
        raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="match pattern must be a mapping")
    return cast(MatchPattern, frozen)


def _validate_handler_package_metadata(package_metadata: PackageMetadata | None) -> None:
    if package_metadata is None:
        return
    if not isinstance(package_metadata, PackageMetadata):
        raise_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="package_metadata must be PackageMetadata or None")
    if not isinstance(package_metadata.package_id, str) or not package_metadata.package_id:
        raise_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="package_metadata.package_id must be a non-empty string")
    if package_metadata.package_version is not None and not isinstance(package_metadata.package_version, str):
        raise_error(
            StatusCode.EVENTS_PAYLOAD_INVALID,
            reason="package_metadata.package_version must be a string or None",
        )


def _reject_ops_build_handler_mutation(
    event_type: EventType,
    builder: EventHandlerOpsBuilder | None,
) -> None:
    if event_type == OPS_BUILD_EVENT_TYPE and builder is not None:
        raise_error(StatusCode.EVENTS_STATE_INVALID, reason="ops.build handlers cannot change after build")


def _validate_handler_registration(
    events: Mapping[EventType, _EventRegistration],
    builder: EventHandlerOpsBuilder | None,
    event_type: EventType,
    handler: EventHandler,
    handler_level: HandlerLevel,
) -> None:
    if not isinstance(event_type, str) or event_type not in events:
        raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="event type is not registered")
    if not callable(handler):
        raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="handler must be callable")
    if not isinstance(handler_level, HandlerLevel) or handler_level is HandlerLevel.CORE:
        raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="handler level must be USER or BUILTIN")
    _reject_ops_build_handler_mutation(event_type, builder)


def _normalize_session(session: object) -> SessionContext | None:
    if session is None:
        return None
    if not isinstance(session, SessionContext):
        from openjiuwen.core.single_agent import Session

        if isinstance(session, Session):
            session = SessionContext(session.get_session_id())
    if not isinstance(session, SessionContext):
        raise_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="unsupported session type")
        return None

    if (
        not isinstance(session.session_id, str)
        or not session.session_id
        or not (session.cwd is None or isinstance(session.cwd, str))
    ):
        raise_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="invalid session context")
    try:
        metadata = _freeze_match_pattern(session.metadata)
    except ValidationError as error:
        raise_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="invalid session metadata", cause=error)
    return SessionContext(session.session_id, session.cwd, metadata)


class ExtensionEventBus:
    """In-process extension event bus with USER, BUILTIN, and CORE handler chains.

    Hosts register event types and handlers, run a one-time ``ops.build`` to
    collect platform capabilities, then dispatch ordinary events with per-handler
    Ops views bound to package and session context.
    """

    def __init__(self) -> None:
        """Create a bus and register Core builtin event definitions."""
        self._framework = AsyncCallbackFramework()
        self._events: dict[EventType, _EventRegistration] = {}
        self._handlers: dict[HandlerId, _HandlerRegistration] = {}
        self._builder: EventHandlerOpsBuilder | None = None

        register_builtins(self)

    def _validate_new_event_type(
        self,
        event_type: EventType,
        matcher: MatchPatternFunc,
        core_handler: EventHandler,
    ) -> None:
        if not isinstance(event_type, str) or _EVENT_TYPE_PATTERN.fullmatch(event_type) is None:
            raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="invalid event type")
        if event_type in self._events:
            raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="event type is already registered")
        if not callable(matcher):
            raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="matcher must be callable")
        if inspect.iscoroutinefunction(matcher):
            raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="matcher must be synchronous")
        if not callable(core_handler):
            raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="core handler must be callable")

    def register_event_type(
        self,
        event_type: EventType,
        match_pattern_func: MatchPatternFunc,
        core_handler: EventHandler,
    ) -> None:
        """Register an event type and its CORE handler."""
        self._validate_new_event_type(event_type, match_pattern_func, core_handler)

        # CORE sits at the tail of the framework wrap chain; ``wrap()`` returns the
        # dispatch entry that resolves the current WrapHandlers on every call.
        core_terminal = self._create_core_terminal(core_handler)
        dispatch: EventDispatcher = self._framework.wrap(event_type)(core_terminal)
        self._events[event_type] = _EventRegistration(match_pattern_func, dispatch)

    def register_event_handler(
        self,
        event_type: EventType,
        handler: EventHandler,
        match_pattern: MatchPattern,
        handler_level: HandlerLevel = HandlerLevel.USER,
        *,
        package_metadata: PackageMetadata | None = None,
    ) -> HandlerId:
        """Register a USER or BUILTIN handler and return its handler id."""
        _validate_handler_registration(self._events, self._builder, event_type, handler, handler_level)
        _validate_handler_package_metadata(package_metadata)

        frozen_pattern = _freeze_match_pattern(match_pattern)
        handler_id = uuid.uuid4().hex

        # USER handlers are outermost (higher priority), BUILTIN next; the framework
        # keeps registration order within the same priority.
        adapter = self._create_handler_adapter(handler_id, handler, handler_level, package_metadata)
        priority = 200 if handler_level is HandlerLevel.USER else 100
        self._framework.on_wrap(event_type, priority=priority)(adapter)
        registration = _HandlerRegistration(event_type, frozen_pattern, adapter, package_metadata)
        try:
            self._handlers[handler_id] = registration
        except BaseException:
            # Keep framework and bus registries in sync: never leave an adapter
            # that no handler id can unregister.
            self._framework.unregister_sync(_wrap_registry_key(event_type), adapter)
            raise
        return handler_id

    # Every function in the framework chain (dispatch, adapters, core terminal)
    # shares one keyword contract:
    #   matched_handler_ids - handler ids selected by the matcher for this trigger
    #   target_level        - layer requested by EventNext.to(); None means "next"
    #   session_context     - normalized once at trigger(); read-only afterwards
    # Only ``target_level`` changes between hops; the other two travel unchanged.

    def _create_core_terminal(self, core_handler: EventHandler) -> EventDispatcher:
        """Wrap the CORE handler as the innermost function of the framework chain."""

        async def core_terminal(
            event: Event,
            *,
            matched_handler_ids: frozenset[HandlerId],
            target_level: HandlerLevel | None,
            session_context: SessionContext | None,
        ) -> EventOutput | None:
            del matched_handler_ids, target_level  # the chain ends here; nothing left to skip
            # CORE has no package identity; it still gets its own Ops bound to this session.
            ops = None if self._builder is None else self._builder.build(None, session_context)
            # ``forward=None``: EventNext at the tail returns None instead of advancing.
            with EventNext(event.event_type, HandlerLevel.CORE, None) as event_next:
                result = core_handler(event, ops, event_next)
                if not inspect.isawaitable(result):
                    raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="core handler result must be awaitable")
                return await result

        return core_terminal

    def _create_handler_adapter(
        self,
        handler_id: HandlerId,
        handler: EventHandler,
        handler_level: HandlerLevel,
        package_metadata: PackageMetadata | None,
    ) -> WrapHandler:
        """Adapt a USER / BUILTIN handler to a framework WrapHandler.

        The adapter passes through when this handler is not matched or a layer
        jump targets a layer inward of it; otherwise it builds the handler's Ops,
        hands it an EventNext whose ``forward`` continues the framework chain,
        and awaits the handler.
        """

        async def adapter(
            call_next: Callable[..., Awaitable[EventOutput | None]],
            /,
            event: Event,
            *,
            matched_handler_ids: frozenset[HandlerId],
            target_level: HandlerLevel | None,
            session_context: SessionContext | None,
        ) -> EventOutput | None:
            # Pass-through cases: the handler was not matched, or an upstream
            # ``to()`` asked for a layer inward of this one. Skipped handlers
            # build no Ops and leave the chain arguments untouched.
            not_matched = handler_id not in matched_handler_ids
            skip_to_core = target_level is HandlerLevel.CORE
            skip_user_layer = target_level is HandlerLevel.BUILTIN and handler_level is HandlerLevel.USER
            if not_matched or skip_to_core or skip_user_layer:
                return await call_next(
                    event,
                    matched_handler_ids=matched_handler_ids,
                    target_level=target_level,
                    session_context=session_context,
                )

            async def forward(next_event: Event, next_target: HandlerLevel | None) -> EventOutput | None:
                # Called by EventNext.__call__ / to(): continue the framework chain
                # with the handler-supplied event and jump target.
                return await call_next(
                    next_event,
                    matched_handler_ids=matched_handler_ids,
                    target_level=next_target,
                    session_context=session_context,
                )

            # One Ops per entered handler, bound to its own package and this session.
            ops = None if self._builder is None else self._builder.build(package_metadata, session_context)
            # The ``with`` block scopes EventNext to this handler invocation.
            with EventNext(event.event_type, handler_level, forward) as event_next:
                result = handler(event, ops, event_next)
                if not inspect.isawaitable(result):
                    raise_error(StatusCode.EVENTS_REGISTRATION_INVALID, reason="handler result must be awaitable")
                return await result

        return adapter

    def unregister_event_handler(self, handler_id: HandlerId) -> None:
        """Remove a USER or BUILTIN handler; unknown ids are ignored."""
        registration = self._handlers.get(handler_id)
        if registration is None:
            return
        _reject_ops_build_handler_mutation(registration.event_type, self._builder)
        self._framework.unregister_sync(
            _wrap_registry_key(registration.event_type),
            registration.wrap_handler,
        )
        del self._handlers[handler_id]

    def unregister_event_type(self, event_type: EventType) -> None:
        """Remove an event type and all handlers registered for it."""
        if event_type not in self._events:
            return

        registrations = [
            (handler_id, registration)
            for handler_id, registration in self._handlers.items()
            if registration.event_type == event_type
        ]
        for handler_id, registration in registrations:
            self._framework.unregister_sync(
                _wrap_registry_key(event_type),
                registration.wrap_handler,
            )
            del self._handlers[handler_id]
        del self._events[event_type]

    async def trigger(
        self,
        event: Event,
        session: Session | SessionContext | None = None,
    ) -> EventOutput | None:
        """Run one event through matched handlers and return the final output."""
        if (
            not isinstance(event, Event)
            or not isinstance(event.event_type, str)
            or _EVENT_TYPE_PATTERN.fullmatch(event.event_type) is None
        ):
            raise_error(StatusCode.EVENTS_PAYLOAD_INVALID, reason="invalid event")

        registration = self._events.get(event.event_type)
        if registration is None:
            raise_error(StatusCode.EVENTS_UNKNOWN_EVENT_INVALID, reason=f"unknown event type: {event.event_type}")
        if event.event_type == OPS_BUILD_EVENT_TYPE:
            return await self._build(event, registration)
        if self._builder is None:
            raise_error(StatusCode.EVENTS_STATE_INVALID, reason="ops.build has not succeeded")
        # Session and match results are fixed here, before the first await, and
        # then handed to the framework chain; the chain never re-matches.
        session_context = _normalize_session(session)
        matched_handler_ids = self._match_handlers(event, registration)
        return await registration.dispatch(
            event,
            matched_handler_ids=matched_handler_ids,
            target_level=None,
            session_context=session_context,
        )

    async def _build(self, event: Event, registration: _EventRegistration) -> EventHandlerOpsBuilder:
        if self._builder is not None:
            raise_error(StatusCode.EVENTS_STATE_INVALID, reason="ops.build has already succeeded")
        matched_handler_ids = self._match_handlers(event, registration)
        result = await registration.dispatch(
            event,
            matched_handler_ids=matched_handler_ids,
            target_level=None,
            session_context=None,
        )
        if isinstance(result, EventHandlerOpsBuilder):
            self._builder = result
            return result
        raise_error(StatusCode.EVENTS_OPS_INVALID, reason="ops.build must return an EventHandlerOpsBuilder")
        return None

    def _match_handlers(self, event: Event, registration: _EventRegistration) -> frozenset[HandlerId]:
        candidates = [
            (handler_id, handler_registration)
            for handler_id, handler_registration in self._handlers.items()
            if handler_registration.event_type == event.event_type
        ]
        matched_handler_ids: set[HandlerId] = set()
        for handler_id, handler_registration in candidates:
            try:
                matched = registration.matcher(event.event_input, handler_registration.match_pattern)
            except Exception as error:
                raise_error(
                    StatusCode.EVENTS_MATCH_INVALID,
                    reason="matcher raised an exception",
                    cause=error,
                )
            if not isinstance(matched, bool):
                raise_error(StatusCode.EVENTS_MATCH_INVALID, reason="matcher must return bool")
            if matched:
                matched_handler_ids.add(handler_id)
        return frozenset(matched_handler_ids)
