# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bind host-configured state and opaque dependencies to an event bus."""

import threading
import weakref
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.protocol import ExtensionEventBusProtocol
from openjiuwen.core.extension.data import JsonState, StateDependencies


@dataclass(frozen=True, slots=True)
class StateBinding:
    """A state service, dependency collector and optional package call hooks."""

    states: JsonState
    dependencies: StateDependencies
    begin_calls: Callable[[Sequence[str], EventHandlerOps], None] | None = None
    end_calls: Callable[[Sequence[str]], None] | None = None


_LOCK = threading.Lock()
_BINDINGS: weakref.WeakKeyDictionary[object, StateBinding] = weakref.WeakKeyDictionary()


def configure_state(
    bus: ExtensionEventBusProtocol,
    states: JsonState,
    *,
    dependencies: StateDependencies | None = None,
    begin_calls: Callable[[Sequence[str], EventHandlerOps], None] | None = None,
    end_calls: Callable[[Sequence[str], EventHandlerOps], None] | None = None,
) -> None:
    """Bind state before Build without accessing any package files."""
    if not isinstance(states, JsonState) or (begin_calls is None) != (end_calls is None):
        raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="invalid state service or call hooks")
    if dependencies is not None and not isinstance(dependencies, StateDependencies):
        raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="invalid state dependencies")
    with _LOCK:
        if bus in _BINDINGS:
            raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="state is already configured for this bus")
        _BINDINGS[bus] = StateBinding(states, dependencies or StateDependencies(), begin_calls, end_calls)


def unconfigure_state(bus: ExtensionEventBusProtocol) -> None:
    """Release a host binding after failed initialization or shutdown."""
    with _LOCK:
        _BINDINGS.pop(bus, None)


def state_for(bus: ExtensionEventBusProtocol) -> StateBinding:
    """Return the configured services, failing explicitly when none were provided."""
    with _LOCK:
        binding = _BINDINGS.get(bus)
    if binding is None:
        raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="no state service is configured for this bus")
    return binding


@contextmanager
def state_call(bus: ExtensionEventBusProtocol, package_id: str, ops: EventHandlerOps) -> Iterator[None]:
    """Account for a nested state operation until its file worker has exited."""
    binding = state_for(bus)
    if binding.begin_calls is not None:
        binding.begin_calls((package_id,), ops)
    try:
        yield
    finally:
        if binding.end_calls is not None:
            binding.end_calls((package_id,))
