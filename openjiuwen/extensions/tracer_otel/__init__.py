# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OTel tracer extension — optional integration for OpenTelemetry span export.

Install with: ``pip install openjiuwen[tracer-otel]``
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
    from openjiuwen.extensions.tracer_otel.handler import OtelAgentHandler, OtelWorkflowHandler
    from openjiuwen.extensions.tracer_otel.setup import init_otel_tracer

_EXPORTS = {
    "OtelAgentHandler": ("openjiuwen.extensions.tracer_otel.handler", "OtelAgentHandler"),
    "OtelWorkflowHandler": ("openjiuwen.extensions.tracer_otel.handler", "OtelWorkflowHandler"),
    "OtelTracerConfig": ("openjiuwen.extensions.tracer_otel.config", "OtelTracerConfig"),
    "init_otel_tracer": ("openjiuwen.extensions.tracer_otel.setup", "init_otel_tracer"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


__all__ = [
    "OtelAgentHandler",
    "OtelWorkflowHandler",
    "OtelTracerConfig",
    "init_otel_tracer",
]
