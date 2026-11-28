# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider demand coordination shared by the single-agent and Team runtimes."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

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


def test_capture_only_acquire_closes_the_export_gate(provider) -> None:
    """A capture-only runtime (trajectory store, evolution) silences the exporter."""
    acquire_observability_demand(
        "agent",
        observability_config=_config(),
        initializer=provider.initializer,
        export=False,
    )

    assert demand_module.is_export_enabled() is False


def test_export_demand_defaults_to_open(provider) -> None:
    acquire_observability_demand(
        "agent", observability_config=_config(), initializer=provider.initializer
    )

    assert demand_module.is_export_enabled() is True


def test_every_acquire_refreshes_the_gate_without_reinitializing(provider) -> None:
    """Hot-reload: an already-active runtime flips the gate on its next sync."""
    acquire_observability_demand(
        "agent",
        observability_config=_config(),
        initializer=provider.initializer,
        export=True,
    )

    existed = acquire_observability_demand(
        "agent",
        observability_config=_config(),
        initializer=provider.initializer,
        export=False,
    )

    assert existed is True
    assert provider.init_calls == 1
    assert demand_module.is_export_enabled() is False


def test_one_exporting_runtime_keeps_the_gate_open_for_the_process(provider) -> None:
    acquire_observability_demand(
        "agent",
        observability_config=_config(),
        initializer=provider.initializer,
        export=False,
    )
    acquire_observability_demand(
        "team", observability_config=_config(), initializer=provider.initializer
    )

    assert demand_module.is_export_enabled() is True

    release_observability_demand("team", finalizer=provider.finalizer)

    assert demand_module.is_export_enabled() is False


def test_the_gate_is_open_without_any_coordinated_runtime(provider) -> None:
    """Direct init callers (RL collectors) keep their provider's export behaviour."""
    assert demand_module.is_export_enabled() is True

    provider.initialized = True
    acquire_observability_demand(
        "agent",
        observability_config=_config(),
        initializer=provider.initializer,
        export=False,
    )
    # The external owner's export intent outlives the capture-only demand.
    assert demand_module.is_export_enabled() is True

    release_observability_demand("agent", finalizer=provider.finalizer)

    # The last demand is gone, so the gate no longer speaks for the process.
    assert demand_module.is_export_enabled() is True


def test_set_export_demand_rejects_unknown_runtime(provider) -> None:
    with pytest.raises(ValueError):
        demand_module.set_export_demand("agnet", True)


@pytest.fixture
def real_shared_runtime(monkeypatch):
    """Exercise the real shared runtime, demand coordinator and export gate.

    Only the exporter transport is replaced with an in-memory exporter; the
    provider, demand bookkeeping and gate all run for real, so the regression
    cases below observe the final exported span set instead of a
    monkeypatched gate boolean.
    """
    import openjiuwen.extensions.observability.runtime as runtime_module
    import openjiuwen.extensions.observability.setup as shared_setup

    exporter = InMemorySpanExporter()
    monkeypatch.setattr(runtime_module, "build_span_exporter", lambda config: exporter)
    reset_observability_demands()
    shared_setup.shutdown_observability()
    yield shared_setup, exporter
    shared_setup.shutdown_observability()
    reset_observability_demands()


def _shared_initializer(shared_setup):
    """Coordinator initializer that routes to the real shared runtime."""

    def _init(config, processors):
        shared_setup.init_observability(config, additional_span_processors=processors)

    return _init


def test_capture_only_demand_never_gates_an_externally_owned_provider(
    real_shared_runtime,
) -> None:
    """R2: an external initializer's export intent predates any coordinated demand."""
    shared_setup, exporter = real_shared_runtime

    shared_setup.init_observability(_config())
    existed = acquire_observability_demand(
        "agent",
        observability_config=_config(),
        initializer=lambda config, processors: None,
        export=False,
    )
    assert existed is True

    with shared_setup.get_tracer("external-owner").start_as_current_span("during-demand"):
        pass
    shared_setup.force_flush_provider()

    assert [span.name for span in exporter.get_finished_spans()] == ["during-demand"]


def test_capture_only_teardown_drops_leftover_spans(real_shared_runtime) -> None:
    """R3: spans captured under a closed gate are not exported by provider teardown."""
    shared_setup, exporter = real_shared_runtime

    acquire_observability_demand(
        "agent",
        observability_config=_config(),
        initializer=_shared_initializer(shared_setup),
        export=False,
    )
    shared_setup.get_tracer("ui-only").start_span("ui-only-cancelled-span")

    release_observability_demand("agent", finalizer=shared_setup.shutdown_observability)

    # The finalizer force-ends the leftover span; the closed gate drops it.
    assert exporter.get_finished_spans() == ()


def test_exporting_teardown_still_drains_leftover_spans(real_shared_runtime) -> None:
    """R3 companion: a runtime that asked for export keeps draining at teardown."""
    shared_setup, exporter = real_shared_runtime

    acquire_observability_demand(
        "agent",
        observability_config=_config(),
        initializer=_shared_initializer(shared_setup),
    )
    shared_setup.get_tracer("exporting").start_span("exporting-leftover-span")

    release_observability_demand("agent", finalizer=shared_setup.shutdown_observability)

    assert [
        span.name for span in exporter.get_finished_spans()
    ] == ["exporting-leftover-span"]


def test_failed_acquire_rolls_back_its_export_demand(real_shared_runtime) -> None:
    """R4: an acquire that raised must not open the gate for other runtimes."""
    shared_setup, exporter = real_shared_runtime

    acquire_observability_demand(
        "team",
        observability_config=_config(),
        initializer=_shared_initializer(shared_setup),
        export=False,
    )

    def _failing_init(config, processors):
        raise RuntimeError("initializer failed")

    with pytest.raises(RuntimeError):
        acquire_observability_demand(
            "agent", observability_config=_config(), initializer=_failing_init
        )

    assert demand_module.is_export_enabled() is False
    with shared_setup.get_tracer("capture-only").start_as_current_span(
        "after-failed-acquire"
    ):
        pass
    shared_setup.force_flush_provider()
    assert exporter.get_finished_spans() == ()


@pytest.mark.parametrize("runtime,other_runtime", [("agent", "team"), ("team", "agent")])
@pytest.mark.parametrize("export", [False, True])
def test_failed_first_acquire_cleans_up_its_provider_and_allows_retry(
    real_shared_runtime, monkeypatch, runtime, other_runtime, export
) -> None:
    """A partially initialized provider must never become an external owner."""
    shared_setup, failed_exporter = real_shared_runtime
    initialize = _shared_initializer(shared_setup)
    leftover = []

    def failing_init(config, processors):
        initialize(config, processors)
        leftover.append(shared_setup.get_tracer("failed-init").start_span("failed-init-leftover"))
        raise RuntimeError("runtime wiring failed after provider creation")

    with pytest.raises(RuntimeError, match="runtime wiring failed after provider creation"):
        acquire_observability_demand(
            runtime, observability_config=_config(), initializer=failing_init, export=export
        )

    assert shared_setup.is_initialized() is False
    assert demand_module._ACTIVE_RUNTIMES == set()
    assert demand_module._EXPORT_RUNTIMES == set()
    assert demand_module._PROVIDER_OWNED is False
    assert demand_module._EXTERNAL_EXPORT_OWNER is False
    assert demand_module._TEARDOWN is False
    assert leftover[0].is_recording() is False
    assert failed_exporter.get_finished_spans() == ()

    # A different capture-only runtime must create its own provider, rather
    # than treating the failed acquire's leftovers as an external export owner.
    capture_exporter = InMemorySpanExporter()
    monkeypatch.setattr(
        "openjiuwen.extensions.observability.runtime.build_span_exporter",
        lambda config: capture_exporter,
    )
    existed = acquire_observability_demand(
        other_runtime, observability_config=_config(), initializer=initialize, export=False
    )
    assert existed is False
    assert demand_module._PROVIDER_OWNED is True
    assert demand_module._EXTERNAL_EXPORT_OWNER is False
    assert demand_module.is_export_enabled() is False
    with shared_setup.get_tracer("capture-only").start_as_current_span("after-first-failure"):
        pass
    shared_setup.force_flush_provider()
    assert capture_exporter.get_finished_spans() == ()
    release_observability_demand(other_runtime)
    assert shared_setup.is_initialized() is False

    retry_exporter = InMemorySpanExporter()
    monkeypatch.setattr(
        "openjiuwen.extensions.observability.runtime.build_span_exporter",
        lambda config: retry_exporter,
    )
    acquire_observability_demand(
        runtime, observability_config=_config(), initializer=initialize, export=True
    )
    with shared_setup.get_tracer("retry").start_as_current_span("successful-retry"):
        pass
    shared_setup.force_flush_provider()
    assert [span.name for span in retry_exporter.get_finished_spans()] == ["successful-retry"]
    release_observability_demand(runtime)


@pytest.mark.parametrize("runtime", ["agent", "team"])
def test_failed_acquire_preserves_an_existing_external_provider(real_shared_runtime, runtime) -> None:
    """Failure cleanup must only shut down a provider the acquire created."""
    shared_setup, exporter = real_shared_runtime
    shared_setup.init_observability(_config())

    def failing_init(config, processors):
        _shared_initializer(shared_setup)(config, processors)
        raise RuntimeError("runtime wiring failed on external provider")

    with pytest.raises(RuntimeError, match="runtime wiring failed on external provider"):
        acquire_observability_demand(
            runtime, observability_config=_config(), initializer=failing_init, export=False
        )

    assert shared_setup.is_initialized() is True
    assert runtime not in demand_module._ACTIVE_RUNTIMES
    assert runtime not in demand_module._EXPORT_RUNTIMES
    with shared_setup.get_tracer("external-owner").start_as_current_span("after-failed-acquire"):
        pass
    shared_setup.force_flush_provider()
    assert [span.name for span in exporter.get_finished_spans()] == ["after-failed-acquire"]


def test_span_end_is_not_blocked_by_a_teardown_holding_the_demand_lock(
    real_shared_runtime, monkeypatch
) -> None:
    """Business threads keep ending spans while the last release drains the exporter.

    The releasing thread holds the demand lock through the finalizer, which
    includes exporter shutdown; the gate inside ``span.end`` must not wait
    for it.
    """
    import threading

    shared_setup, exporter = real_shared_runtime
    in_exporter_shutdown = threading.Event()
    business_done = threading.Event()
    original_shutdown = exporter.shutdown

    def slow_shutdown() -> None:
        in_exporter_shutdown.set()
        # Bounded wait so a regression fails the assertion instead of hanging.
        business_done.wait(timeout=5)
        original_shutdown()

    monkeypatch.setattr(exporter, "shutdown", slow_shutdown)
    acquire_observability_demand(
        "agent",
        observability_config=_config(),
        initializer=_shared_initializer(shared_setup),
        export=False,
    )
    tracer = shared_setup.get_tracer("business")
    releaser = threading.Thread(
        target=release_observability_demand,
        args=("agent",),
        kwargs={"finalizer": shared_setup.shutdown_observability},
        daemon=True,
    )
    releaser.start()
    assert in_exporter_shutdown.wait(timeout=5)

    def business() -> None:
        tracer.start_span("business-span-during-teardown").end()
        business_done.set()

    worker = threading.Thread(target=business, daemon=True)
    worker.start()
    worker.join(timeout=2)
    finished_while_tearing_down = business_done.is_set()
    business_done.set()
    releaser.join(timeout=5)

    assert finished_while_tearing_down, "span.end waited for the provider teardown"
    assert not releaser.is_alive()


def test_direct_shutdown_does_not_deadlock_against_a_demand_lock_holder(
    real_shared_runtime,
) -> None:
    """Lock order: shutdown ends spans under the runtime lock, acquire takes demand first.

    A direct ``shutdown_observability`` force-ends leftover spans while it
    holds the runtime lock; meanwhile a coordinated acquire holds the demand
    lock and asks the runtime whether it is initialized. The gate must not
    need the demand lock, or the two threads wait on each other.
    """
    import threading

    shared_setup, exporter = real_shared_runtime
    shared_setup.init_observability(_config())
    shared_setup.get_tracer("external-owner").start_span("leftover-at-shutdown")
    runtime = shared_setup.get_observability_runtime()
    tracker = runtime.get_tracker()
    original_flush = tracker.flush_all_spans
    shutdown_holds_runtime_lock = threading.Event()
    acquirer_holds_demand_lock = threading.Event()
    acquirer_got_runtime_lock: list[bool] = []

    def flush_after_acquirer_locks(**kwargs):
        shutdown_holds_runtime_lock.set()
        acquirer_holds_demand_lock.wait(timeout=5)
        return original_flush(**kwargs)

    tracker.flush_all_spans = flush_after_acquirer_locks

    def acquirer() -> None:
        shutdown_holds_runtime_lock.wait(timeout=5)
        with demand_module._DEMAND_LOCK:
            acquirer_holds_demand_lock.set()
            # Same lock ``is_initialized`` takes; bounded so a regression
            # reports instead of deadlocking the test process.
            got = runtime._lock.acquire(timeout=3)
            acquirer_got_runtime_lock.append(got)
            if got:
                runtime._lock.release()

    acquiring = threading.Thread(target=acquirer, daemon=True)
    shutting_down = threading.Thread(target=shared_setup.shutdown_observability, daemon=True)
    acquiring.start()
    shutting_down.start()
    shutting_down.join(timeout=10)
    acquiring.join(timeout=10)

    assert acquirer_got_runtime_lock == [True], "shutdown and acquire deadlocked"
    assert not shutting_down.is_alive()
    # No coordinated demand: the external owner's leftover is still exported.
    assert [span.name for span in exporter.get_finished_spans()] == ["leftover-at-shutdown"]
