# coding: utf-8

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
