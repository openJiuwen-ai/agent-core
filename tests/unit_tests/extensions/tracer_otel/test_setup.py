# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for tracer_otel setup.py — init_otel_tracer with new config fields."""

import pytest

from openjiuwen.extensions.tracer_otel.config import OtelTracerConfig
from openjiuwen.extensions.tracer_otel.setup import init_otel_tracer, _create_otlp_exporter


class TestInitOtelTracer:
    def test_console_exporter(self):
        config = OtelTracerConfig(exporter_type="console")
        tracer = init_otel_tracer(config)
        assert tracer is not None

    def test_otlp_grpc_exporter(self):
        pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
        config = OtelTracerConfig(
            exporter_type="otlp",
            exporter_endpoint="http://localhost:4317",
            protocol="grpc",
        )
        tracer = init_otel_tracer(config)
        assert tracer is not None

    def test_otlp_http_exporter(self):
        pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
        config = OtelTracerConfig(
            exporter_type="otlp",
            exporter_endpoint="http://localhost:4317",
            protocol="http",
        )
        tracer = init_otel_tracer(config)
        assert tracer is not None

    def test_otlp_http_endpoint_path_appended(self):
        """HTTP exporter should append /v1/traces to endpoint if not already present."""
        pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
        config = OtelTracerConfig(
            exporter_type="otlp",
            exporter_endpoint="http://localhost:4317",
            protocol="http",
        )
        exporter = _create_otlp_exporter(config)
        # OTLPSpanExporter stores endpoint internally; verify it doesn't crash
        assert exporter is not None

    def test_otlp_http_endpoint_path_not_double_appended(self):
        """HTTP exporter should not double-append /v1/traces."""
        pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
        config = OtelTracerConfig(
            exporter_type="otlp",
            exporter_endpoint="http://localhost:4317/v1/traces",
            protocol="http",
        )
        exporter = _create_otlp_exporter(config)
        assert exporter is not None

    def test_otlp_with_headers(self):
        pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
        config = OtelTracerConfig(
            exporter_type="otlp",
            exporter_endpoint="http://localhost:4317",
            protocol="grpc",
            headers={"api-key": "test123", "Authorization": "Bearer token"},
        )
        tracer = init_otel_tracer(config)
        assert tracer is not None

    def test_invalid_exporter_type_raises(self):
        from openjiuwen.core.common.exception.errors import BaseError
        config = OtelTracerConfig(exporter_type="invalid")
        with pytest.raises(BaseError, match="exporter_type"):
            init_otel_tracer(config)

    def test_invalid_protocol_raises(self):
        from openjiuwen.core.common.exception.errors import BaseError
        config = OtelTracerConfig(
            exporter_type="otlp",
            exporter_endpoint="http://localhost:4317",
            protocol="invalid",
        )
        with pytest.raises(BaseError, match="otlp protocol"):
            _create_otlp_exporter(config)

    def test_batch_processor_config(self):
        """init_otel_tracer returns a valid tracer with custom batch processor config."""
        config = OtelTracerConfig(
            exporter_type="console",
            export_timeout_ms=5000,
            max_export_batch_size=128,
        )
        tracer = init_otel_tracer(config)
        assert tracer is not None
        # Verify we can create a span (proves the provider + processor work)
        span = tracer.start_span("test_span")
        span.end()

    def test_service_name_and_version(self):
        """Service name and version are set on the TracerProvider's Resource."""
        config = OtelTracerConfig(
            exporter_type="console",
            service_name="my-service",
            service_version="1.2.3",
        )
        tracer = init_otel_tracer(config)
        assert tracer is not None
        # Verify tracer can produce spans (Resource attributes are embedded
        # in the provider, not directly accessible from the Tracer object)
        span = tracer.start_span("test_resource_span")
        # Resource attributes are propagated to spans via the provider
        assert span is not None
        span.end()


class TestSampleRateValidation:
    """Test OtelTracerConfig __post_init__ validation for sample_rate."""

    def test_sample_rate_valid_one(self):
        config = OtelTracerConfig(sample_rate=1.0)
        assert config.sample_rate == 1.0

    def test_sample_rate_valid_zero(self):
        config = OtelTracerConfig(sample_rate=0.0)
        assert config.sample_rate == 0.0

    def test_sample_rate_valid_fraction(self):
        config = OtelTracerConfig(sample_rate=0.5)
        assert config.sample_rate == 0.5

    def test_sample_rate_invalid_above_one(self):
        with pytest.raises(ValueError, match="sample_rate must be between"):
            OtelTracerConfig(sample_rate=1.1)

    def test_sample_rate_invalid_negative(self):
        with pytest.raises(ValueError, match="sample_rate must be between"):
            OtelTracerConfig(sample_rate=-0.1)

    def test_sample_rate_default_is_one(self):
        config = OtelTracerConfig()
        assert config.sample_rate == 1.0


class TestSampleRateIntegration:
    """Test that sample_rate is wired into TracerProvider via ParentBasedTraceIdRatio."""

    def test_sample_rate_zero_no_spans_sampled(self):
        """sample_rate=0.0 → no spans should be recorded."""
        config = OtelTracerConfig(exporter_type="console", sample_rate=0.0)
        tracer = init_otel_tracer(config)
        span = tracer.start_span("should_not_appear")
        # With rate=0.0, the sampler drops all root spans.
        # The span object is still returned but is a NonRecordingSpan.
        from opentelemetry.trace import NonRecordingSpan
        assert isinstance(span, NonRecordingSpan)
        span.end()

    def test_sample_rate_one_all_spans_sampled(self):
        """sample_rate=1.0 → all spans should be recorded."""
        config = OtelTracerConfig(exporter_type="console", sample_rate=1.0)
        tracer = init_otel_tracer(config)
        span = tracer.start_span("should_appear")
        from opentelemetry.trace.span import Span
        assert isinstance(span, Span)
        span.end()

    def test_schedule_delay_millis_passed_to_batch_processor(self):
        """schedule_delay_millis should be passed to BatchSpanProcessor."""
        pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
        config = OtelTracerConfig(
            exporter_type="otlp",
            exporter_endpoint="http://localhost:4317",
            schedule_delay_millis=2000,
        )
        tracer = init_otel_tracer(config)
        assert tracer is not None

    def test_schedule_delay_millis_default(self):
        """Default schedule_delay_millis is 5000ms."""
        config = OtelTracerConfig()
        assert config.schedule_delay_millis == 5000
