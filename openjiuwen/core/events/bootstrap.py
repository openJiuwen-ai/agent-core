# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Register Core builtin event types, handler ops and event handlers on a new bus."""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus

_BUILTIN_PACKAGES = ("event_types", "handler_ops", "event_handlers")


def register_builtins(bus: ExtensionEventBus) -> None:
    """Call ``register(bus)`` of every builtin module; never triggers events."""
    for package_name in _BUILTIN_PACKAGES:
        package = importlib.import_module(f"openjiuwen.core.events.builtins.{package_name}")
        module_names = sorted(
            info.name
            for info in pkgutil.iter_modules(package.__path__)
            if not info.ispkg and not info.name.startswith("_")
        )
        for module_name in module_names:
            module = importlib.import_module(f"{package.__name__}.{module_name}")
            register = getattr(module, "register", None)
            if not callable(register):
                raise_error(
                    StatusCode.EVENTS_REGISTRATION_INVALID,
                    reason=f"builtin module has no callable register: {module.__name__}",
                )
            register(bus)
