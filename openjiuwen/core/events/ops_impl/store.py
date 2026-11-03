# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bind explicitly configured persistence to one event bus."""

import threading
import weakref
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.protocol import ExtensionEventBusProtocol
from openjiuwen.core.extension.data import JsonStore


@dataclass(frozen=True, slots=True)
class StoreBinding:
    """A store service and optional Runtime call accounting hooks."""

    store: JsonStore
    begin_calls: Callable[[Sequence[str], EventHandlerOps], None] | None = None
    end_calls: Callable[[Sequence[str]], None] | None = None


_LOCK = threading.Lock()
_BINDINGS: weakref.WeakKeyDictionary[object, StoreBinding] = weakref.WeakKeyDictionary()


def configure_store(
    bus: ExtensionEventBusProtocol,
    store: JsonStore,
    *,
    begin_calls: Callable[[Sequence[str], EventHandlerOps], None] | None = None,
    end_calls: Callable[[Sequence[str]], None] | None = None,
) -> None:
    """Bind a host-owned store before Build; no directory is created here."""
    if not isinstance(store, JsonStore) or (begin_calls is None) != (end_calls is None):
        raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="store configuration or call hooks are invalid")
    with _LOCK:
        if bus in _BINDINGS:
            raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="store is already configured for this bus")
        _BINDINGS[bus] = StoreBinding(store, begin_calls, end_calls)


def unconfigure_store(bus: ExtensionEventBusProtocol) -> None:
    """Release a host binding after initialization failure or shutdown."""
    with _LOCK:
        _BINDINGS.pop(bus, None)


def store_for(bus: ExtensionEventBusProtocol) -> JsonStore:
    """Return the store bound to this bus, failing explicitly if absent."""
    return _binding_for(bus).store


@contextmanager
def store_call(bus: ExtensionEventBusProtocol, package_id: str, ops: EventHandlerOps) -> Iterator[None]:
    """Count nested or detached store calls until their worker has exited."""
    binding = _binding_for(bus)
    if binding.begin_calls is not None:
        binding.begin_calls((package_id,), ops)
    try:
        yield
    finally:
        if binding.end_calls is not None:
            binding.end_calls((package_id,))


def _binding_for(bus: ExtensionEventBusProtocol) -> StoreBinding:
    with _LOCK:
        binding = _BINDINGS.get(bus)
    if binding is None:
        raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="no store service is configured for this bus")
    return binding
