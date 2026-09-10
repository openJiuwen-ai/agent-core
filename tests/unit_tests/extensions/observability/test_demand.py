# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider demand coordination shared by the single-agent and Team runtimes."""

from __future__ import annotations

import pytest

from openjiuwen.extensions.observability import demand as demand_module
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.demand import (
    acquire_observability_demand,
    publish_span_snapshot,
    release_observability_demand,
    reset_observability_demands,
)


class _FakeProvider:
    """Minimal stand-in for the shared runtime's initialized/shutdown state."""

    def __init__(self, *, initialized: bool = False) -> None:
        self.initialized = initialized
        self.init_calls = 0
        self.shutdown_calls = 0

    def initializer(self, config, additional_span_processors) -> None:
        """Match the coordinator's initializer signature."""
        del config, additional_span_processors
        self.init_calls += 1
        self.initialized = True

    def finalizer(self) -> None:
        """Match the coordinator's finalizer signature."""
        self.shutdown_calls += 1
        self.initialized = False


@pytest.fixture
def provider(monkeypatch) -> _FakeProvider:
    """Route the coordinator's provider checks to a fake, isolated per test."""
    fake = _FakeProvider()
    monkeypatch.setattr(
        "openjiuwen.extensions.observability.setup.is_initialized",
        lambda: fake.initialized,
    )
    monkeypatch.setattr(
        "openjiuwen.extensions.observability.setup.shutdown_observability",
        fake.finalizer,
    )
    monkeypatch.setattr(
        demand_module, "get_trajectory_span_processor", lambda: object()
    )
    monkeypatch.setattr(
        demand_module, "get_span_record_processor", lambda: object()
    )
    reset_observability_demands()
    yield fake
    reset_observability_demands()


def _config() -> ObservabilityConfig:
    return ObservabilityConfig(enabled=True, service_name="test")


def test_first_acquire_initializes_and_reports_no_existing_provider(provider) -> None:
    existed = acquire_observability_demand(
        "agent", observability_config=_config(), initializer=provider.initializer
    )

    assert existed is False
    assert provider.init_calls == 1


def test_second_runtime_reuses_the_provider_the_first_created(provider) -> None:
    """OTel keeps the first provider, so the second runtime is told it reused one."""
    acquire_observability_demand(
        "agent", observability_config=_config(), initializer=provider.initializer
    )
    existed = acquire_observability_demand(
        "team", observability_config=_config(), initializer=provider.initializer
    )

    assert existed is True


def test_release_keeps_the_provider_while_another_runtime_holds_it(provider) -> None:
    """A single-agent shutdown must not blind a Team run in the same process."""
    acquire_observability_demand(
        "agent", observability_config=_config(), initializer=provider.initializer
    )
    acquire_observability_demand(
        "team", observability_config=_config(), initializer=provider.initializer
    )

    release_observability_demand("agent", finalizer=provider.finalizer)

    assert provider.shutdown_calls == 0
    assert provider.initialized is True

    release_observability_demand("team", finalizer=provider.finalizer)

    assert provider.shutdown_calls == 1


def test_a_provider_this_coordinator_did_not_create_is_never_shut_down(provider) -> None:
    """An RL collector may own the provider; releasing a demand must not kill it."""
    provider.initialized = True

    acquire_observability_demand(
        "agent", observability_config=_config(), initializer=provider.initializer
    )
    release_observability_demand("agent", finalizer=provider.finalizer)

    assert provider.shutdown_calls == 0
    assert provider.initialized is True


def test_initialization_that_creates_no_provider_is_an_error(provider) -> None:
    """Silently returning without a provider would leave tracing dead but "on"."""
    with pytest.raises(RuntimeError):
        acquire_observability_demand(
            "agent",
            observability_config=_config(),
            initializer=lambda config, processors: None,
        )


def test_unknown_runtime_is_rejected_on_both_sides(provider) -> None:
    """A typo would otherwise pin the provider for the life of the process."""
    with pytest.raises(ValueError):
        acquire_observability_demand(
            "agnet", observability_config=_config(), initializer=provider.initializer
        )
    with pytest.raises(ValueError):
        release_observability_demand("agnet")


def test_the_shared_trajectory_processor_is_created_once() -> None:
    """A second processor would duplicate every captured trajectory."""
    first = demand_module.get_trajectory_span_processor()
    second = demand_module.get_trajectory_span_processor()

    assert first is second


def test_the_shared_span_record_processor_is_created_once() -> None:
    first = demand_module.get_span_record_processor()
    second = demand_module.get_span_record_processor()

    assert first is second


def test_snapshot_publish_is_a_noop_until_the_shared_processor_exists(monkeypatch) -> None:
    monkeypatch.setattr(demand_module, "_SPAN_RECORD_PROCESSOR", None)

    publish_span_snapshot(object(), "attributes")


def test_snapshot_publish_delegates_to_the_existing_shared_processor(monkeypatch) -> None:
    calls: list[tuple[object, str]] = []
    processor = type(
        "SnapshotProcessor",
        (),
        {"publish_snapshot": lambda self, span, kind: calls.append((span, kind))},
    )()
    span = object()
    monkeypatch.setattr(demand_module, "_SPAN_RECORD_PROCESSOR", processor)

    publish_span_snapshot(span, "stream_chunk")

    assert calls == [(span, "stream_chunk")]
