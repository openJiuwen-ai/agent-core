# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Disabled-by-default OTel metrics for the observability subsystem."""

from __future__ import annotations

import threading

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)

from openjiuwen.core.common.logging import logger
from openjiuwen.extensions.observability.config import ObservabilityConfig

_LABEL_MODEL = "model"
_LABEL_AGENT_ID = "agent_id"
_LABEL_KIND = "kind"
_LABEL_TOOL_NAME = "tool_name"
_LABEL_TEAM_ID = "team_id"

_METER_NAME = "openjiuwen.extensions.observability"

_recorder: MetricsRecorder | None = None
_recorder_lock = threading.RLock()


def set_metrics_recorder(rec: MetricsRecorder | None) -> None:
    global _recorder
    with _recorder_lock:
        _recorder = rec


def get_metrics_recorder() -> MetricsRecorder | None:
    with _recorder_lock:
        return _recorder


def is_metrics_enabled() -> bool:
    return get_metrics_recorder() is not None


def _build_meter_provider(config: ObservabilityConfig) -> MeterProvider:
    if config.metrics_exporter == "console":
        exporter = ConsoleMetricExporter()
    elif config.metrics_exporter == "otlp_http":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        exporter = OTLPMetricExporter(endpoint=config.metrics_endpoint or config.endpoint)
    else:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

        exporter = OTLPMetricExporter(
            endpoint=config.metrics_endpoint or config.endpoint,
            insecure=True,
        )
    reader = PeriodicExportingMetricReader(exporter)
    return MeterProvider(metric_readers=[reader])


class MetricsRecorder:
    """Own one ``MeterProvider`` and the high-level record API."""

    def __init__(self, config: ObservabilityConfig) -> None:
        self._provider = _build_meter_provider(config)
        # Do NOT call ``metrics.set_meter_provider``: this recorder is
        # self-contained (it uses ``self._provider.get_meter`` directly), and
        # touching the API-level global would override any coexisting meter
        # provider and break tests that build two recorders.
        meter = self._provider.get_meter(_METER_NAME)
        self._llm_token_usage = meter.create_counter(
            "llm.token_usage",
            unit="1",
            description="Prompt and completion tokens per LLM call",
        )
        self._llm_call_duration = meter.create_histogram(
            "llm.call.duration",
            unit="ms",
            description="End-to-end LLM call latency",
        )
        self._tool_call_duration = meter.create_histogram(
            "tool.call.duration",
            unit="ms",
            description="Tool execution latency",
        )
        self._tool_call_errors = meter.create_counter(
            "tool.call.errors",
            unit="1",
            description="Tool calls that raised or reported failure",
        )
        self._iteration_duration = meter.create_histogram(
            "deepagent.task.iteration.duration",
            unit="ms",
            description="Task-loop iteration latency",
        )
        self._iteration_errors = meter.create_counter(
            "deepagent.task.iteration.errors",
            unit="1",
            description="Task-loop iterations that ended in error",
        )

    @staticmethod
    def _guarded(fn) -> None:
        try:
            fn()
        except Exception as exc:
            logger.warning("otel: metrics record failed - {}", exc)

    def record_llm_usage(self, agent_id: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        self._guarded(
            lambda: self._llm_token_usage.add(
                int(prompt_tokens),
                {_LABEL_MODEL: model, _LABEL_AGENT_ID: agent_id, _LABEL_KIND: "prompt"},
            )
        )
        self._guarded(
            lambda: self._llm_token_usage.add(
                int(completion_tokens),
                {_LABEL_MODEL: model, _LABEL_AGENT_ID: agent_id, _LABEL_KIND: "completion"},
            )
        )

    def record_llm_duration(self, agent_id: str, model: str, duration_ms: float) -> None:
        self._guarded(
            lambda: self._llm_call_duration.record(
                float(duration_ms),
                {_LABEL_MODEL: model, _LABEL_AGENT_ID: agent_id},
            )
        )

    def record_tool_duration(self, tool_name: str, agent_id: str, duration_ms: float) -> None:
        self._guarded(
            lambda: self._tool_call_duration.record(
                float(duration_ms),
                {_LABEL_TOOL_NAME: tool_name, _LABEL_AGENT_ID: agent_id},
            )
        )

    def record_tool_error(self, tool_name: str, agent_id: str) -> None:
        self._guarded(
            lambda: self._tool_call_errors.add(
                1,
                {_LABEL_TOOL_NAME: tool_name, _LABEL_AGENT_ID: agent_id},
            )
        )

    def record_iteration_duration(self, agent_id: str, team_id: str, duration_ms: float) -> None:
        self._guarded(
            lambda: self._iteration_duration.record(
                float(duration_ms),
                {_LABEL_AGENT_ID: agent_id, _LABEL_TEAM_ID: team_id},
            )
        )

    def record_iteration_error(self, agent_id: str, team_id: str) -> None:
        self._guarded(
            lambda: self._iteration_errors.add(
                1,
                {_LABEL_AGENT_ID: agent_id, _LABEL_TEAM_ID: team_id},
            )
        )

    def shutdown(self) -> None:
        with _recorder_lock:
            try:
                self._provider.shutdown()
            except Exception as exc:
                logger.warning("otel: metrics shutdown failed - {}", exc)
