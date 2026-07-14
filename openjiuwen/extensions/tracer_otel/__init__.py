# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""OTel tracer extension — optional integration for OpenTelemetry span export.

Install with: ``pip install openjiuwen[tracer-otel]``
"""

from __future__ import annotations

from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
from openjiuwen.extensions.tracer_otel.handler import OtelAgentHandler, OtelWorkflowHandler
from openjiuwen.extensions.tracer_otel.setup import init_otel_tracer

__all__ = [
    "OtelAgentHandler",
    "OtelWorkflowHandler",
    "OtelTracerConfig",
    "init_otel_tracer",
]
