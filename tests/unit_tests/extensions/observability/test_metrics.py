# coding: utf-8

from openjiuwen.extensions.observability import metrics as metrics_mod
from openjiuwen.extensions.observability.config import ObservabilityConfig


def test_metrics_disabled_by_default():
    cfg = ObservabilityConfig()
    assert cfg.metrics_enabled is False


def test_metrics_fields_defaults():
    cfg = ObservabilityConfig(metrics_enabled=True)
    assert cfg.metrics_endpoint == ""
    assert cfg.metrics_exporter == "otlp_grpc"


def test_metrics_exporter_rejects_unknown_value():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ObservabilityConfig(metrics_exporter="bogus")


def test_recorder_builds_instruments():
    cfg = ObservabilityConfig(metrics_enabled=True, metrics_exporter="console")
    rec = metrics_mod.MetricsRecorder(cfg)
    try:
        assert rec._llm_token_usage.name == "llm.token_usage"
        assert rec._llm_call_duration.name == "llm.call.duration"
        assert rec._tool_call_duration.name == "tool.call.duration"
        assert rec._tool_call_errors.name == "tool.call.errors"
        assert rec._iteration_duration.name == "deepagent.task.iteration.duration"
        assert rec._iteration_errors.name == "deepagent.task.iteration.errors"
    finally:
        rec.shutdown()


def test_recorder_record_methods_do_not_raise(monkeypatch):
    cfg = ObservabilityConfig(metrics_enabled=True, metrics_exporter="console")
    rec = metrics_mod.MetricsRecorder(cfg)
    try:
        rec.record_llm_usage("agent-1", "gpt-4o", 10, 20)
        rec.record_llm_duration("agent-1", "gpt-4o", 123.0)
        rec.record_tool_duration("bash", "agent-1", 50.0)
        rec.record_tool_error("bash", "agent-1")
        rec.record_iteration_duration("agent-1", "team-1", 400.0)
        rec.record_iteration_error("agent-1", "team-1")
    finally:
        rec.shutdown()

