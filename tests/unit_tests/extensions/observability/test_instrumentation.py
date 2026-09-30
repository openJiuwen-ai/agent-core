# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the process-wide OTel HTTP instrumentation switch."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider

from openjiuwen.extensions.observability import instrumentation
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.instrumentation import (
    ENV_FLAG,
    ensure_global_http_instrumentation,
    resolve_global_instrument_flag,
)
from openjiuwen.extensions.observability.runtime import ObservabilityRuntime


class _FakeInstrumentor:
    """Stand-in for an OTel instrumentor class, one instance per instrument() call."""

    instances: list[_FakeInstrumentor] = []
    fail_instrument: bool = False

    def __init__(self) -> None:
        self.instrumented = False
        _FakeInstrumentor.instances.append(self)

    def instrument(self) -> None:
        if _FakeInstrumentor.fail_instrument:
            raise RuntimeError("instrument exploded")
        self.instrumented = True


@pytest.fixture(autouse=True)
def _reset_module_state():
    instrumentation.reset_global_http_instrumentation_for_tests()
    _FakeInstrumentor.instances = []
    _FakeInstrumentor.fail_instrument = False
    yield
    instrumentation.reset_global_http_instrumentation_for_tests()
    _FakeInstrumentor.instances = []
    _FakeInstrumentor.fail_instrument = False


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(ENV_FLAG, raising=False)


def _install_fake_instrumentors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every instrumentor import path at the fake class."""

    for import_path in instrumentation._INSTRUMENTORS:
        module_path, _, class_name = import_path.rpartition(".")
        fake_module = types.ModuleType(module_path)
        setattr(fake_module, class_name, _FakeInstrumentor)
        monkeypatch.setitem(sys.modules, module_path, fake_module)


def _stub_global_provider(
    monkeypatch: pytest.MonkeyPatch,
    provider: Any = None,
    calls: list[Any] | None = None,
) -> None:
    """Replace the global OTel provider accessors; no real global state is touched."""

    monkeypatch.setattr(instrumentation.trace, "get_tracer_provider", lambda: provider)
    if calls is None:
        calls = []
    monkeypatch.setattr(
        instrumentation.trace,
        "set_tracer_provider",
        lambda new_provider: calls.append(new_provider),
    )


# --- flag resolution: env wins over the config value -----------------------


def test_flag_env_unset_falls_back_to_config() -> None:
    assert resolve_global_instrument_flag(True) is True
    assert resolve_global_instrument_flag(False) is False


def test_flag_env_true_overrides_config_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_FLAG, "true")
    assert resolve_global_instrument_flag(False) is True


def test_flag_env_false_overrides_config_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_FLAG, "0")
    assert resolve_global_instrument_flag(True) is False
    monkeypatch.setenv(ENV_FLAG, "off")
    assert resolve_global_instrument_flag(True) is False


def test_flag_env_is_case_and_whitespace_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_FLAG, "  TRUE ")
    assert resolve_global_instrument_flag(False) is True


# --- ensure_global_http_instrumentation -------------------------------------


def test_ensure_skips_without_sdk_provider_or_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_instrumentors(monkeypatch)
    set_calls: list[Any] = []
    _stub_global_provider(monkeypatch, provider=object(), calls=set_calls)

    assert ensure_global_http_instrumentation() is False
    assert _FakeInstrumentor.instances == []
    assert set_calls == []


def test_ensure_factory_installs_provider_then_instruments(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_instrumentors(monkeypatch)
    set_calls: list[Any] = []
    _stub_global_provider(monkeypatch, provider=None, calls=set_calls)
    factory_provider = TracerProvider()

    result = ensure_global_http_instrumentation(provider_factory=lambda: factory_provider)

    assert result is True
    # Provider first, instruments second.
    assert set_calls == [factory_provider]
    assert len(_FakeInstrumentor.instances) == len(instrumentation._INSTRUMENTORS)
    assert all(instance.instrumented for instance in _FakeInstrumentor.instances)


def test_ensure_existing_sdk_provider_skips_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_instrumentors(monkeypatch)
    factory_calls: list[Any] = []

    def _factory() -> TracerProvider:
        factory_calls.append(1)
        return TracerProvider()

    _stub_global_provider(monkeypatch, provider=TracerProvider())

    assert ensure_global_http_instrumentation(provider_factory=_factory) is True
    assert factory_calls == []
    assert all(instance.instrumented for instance in _FakeInstrumentor.instances)


def test_ensure_is_idempotent_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_instrumentors(monkeypatch)
    _stub_global_provider(monkeypatch, provider=TracerProvider())

    assert ensure_global_http_instrumentation() is True
    assert ensure_global_http_instrumentation() is True
    assert len(_FakeInstrumentor.instances) == len(instrumentation._INSTRUMENTORS)


def test_ensure_factory_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_instrumentors(monkeypatch)
    _stub_global_provider(monkeypatch, provider=None)

    def _exploding_factory() -> TracerProvider:
        raise RuntimeError("collector unreachable")

    assert ensure_global_http_instrumentation(provider_factory=_exploding_factory) is False
    assert _FakeInstrumentor.instances == []


def test_missing_dependency_warns_but_other_libraries_proceed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_instrumentors(monkeypatch)
    # Simulate one optional dependency not installed.
    monkeypatch.setitem(sys.modules, "opentelemetry.instrumentation.requests", None)
    _stub_global_provider(monkeypatch, provider=TracerProvider())

    with pytest.warns(RuntimeWarning, match="requests"):
        result = ensure_global_http_instrumentation()

    assert result is True
    # httpx + aiohttp still instrumented; the requests instance never ran.
    instrumented_count = sum(1 for instance in _FakeInstrumentor.instances if instance.instrumented)
    assert instrumented_count == len(instrumentation._INSTRUMENTORS) - 1


def test_instrumentor_failure_does_not_break_remaining_libraries(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_instrumentors(monkeypatch)
    _FakeInstrumentor.fail_instrument = True
    _stub_global_provider(monkeypatch, provider=TracerProvider())

    result = ensure_global_http_instrumentation()  # must not raise

    assert result is True
    assert len(_FakeInstrumentor.instances) == len(instrumentation._INSTRUMENTORS)


# --- FastAPI server-side (inbound trace extraction) --------------------------


def test_fastapi_missing_dependency_warns_but_clients_proceed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_instrumentors(monkeypatch)
    # fastapi itself is a test dependency; block only the OTel instrumentor.
    monkeypatch.setitem(sys.modules, "opentelemetry.instrumentation.fastapi", None)
    _stub_global_provider(monkeypatch, provider=TracerProvider())

    with pytest.warns(RuntimeWarning, match="fastapi"):
        result = ensure_global_http_instrumentation()

    assert result is True
    assert all(instance.instrumented for instance in _FakeInstrumentor.instances)


def test_fastapi_apps_created_after_instrumentation_are_covered(monkeypatch: pytest.MonkeyPatch) -> None:
    fastapi_module = pytest.importorskip("fastapi")
    pytest.importorskip("opentelemetry.instrumentation.fastapi")
    _install_fake_instrumentors(monkeypatch)
    _stub_global_provider(monkeypatch, provider=TracerProvider())

    # Service modules bind the class like this before the switch runs; the
    # patched __init__ must still catch apps they build afterwards.
    FastAPI = fastapi_module.FastAPI  # noqa: N813 - bound before instrumentation
    early_app = FastAPI()

    assert ensure_global_http_instrumentation() is True
    late_app = FastAPI()

    # Only apps constructed after the switch runs extract inbound trace
    # context (documented limitation). The instrumentor marks covered apps
    # itself via ``_is_instrumented_by_opentelemetry``.
    assert not getattr(early_app, "_is_instrumented_by_opentelemetry", False)
    assert late_app._is_instrumented_by_opentelemetry is True


# --- ObservabilityRuntime.initialize integration ----------------------------


def test_runtime_initialize_with_flag_off_does_not_instrument(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    from openjiuwen.extensions.observability import runtime as runtime_module

    ensure_mock = MagicMock()
    monkeypatch.setattr(runtime_module, "ensure_global_http_instrumentation", ensure_mock)
    runtime = ObservabilityRuntime()
    try:
        runtime.initialize(ObservabilityConfig(enabled=True, service_name="flag-off", global_instrument_enable=False))
        ensure_mock.assert_not_called()
    finally:
        runtime.shutdown()


def test_runtime_initialize_with_flag_on_instruments(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from openjiuwen.extensions.observability import runtime as runtime_module

    ensure_mock = MagicMock(return_value=True)
    monkeypatch.setattr(runtime_module, "ensure_global_http_instrumentation", ensure_mock)
    runtime = ObservabilityRuntime()
    try:
        runtime.initialize(
            ObservabilityConfig(enabled=True, service_name="flag-on", global_instrument_enable=True),
            span_exporter_override=InMemorySpanExporter(),
        )
        ensure_mock.assert_called_once_with()
    finally:
        runtime.shutdown()


def test_runtime_initialize_env_flag_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from openjiuwen.extensions.observability import runtime as runtime_module

    monkeypatch.setenv(ENV_FLAG, "true")
    ensure_mock = MagicMock(return_value=True)
    monkeypatch.setattr(runtime_module, "ensure_global_http_instrumentation", ensure_mock)
    runtime = ObservabilityRuntime()
    try:
        runtime.initialize(
            ObservabilityConfig(enabled=True, service_name="env-on", global_instrument_enable=False),
            span_exporter_override=InMemorySpanExporter(),
        )
        ensure_mock.assert_called_once_with()
    finally:
        runtime.shutdown()
