# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Restated attributes state sequence references instead of full content.

A chat span's restated input used to carry the whole conversation inline,
which is what filled exporter queues until the process died. These tests pin
the contract of the new attach-time references: the registry holds the
content before any reference is stated, the storage encode resolves the
reference back into the record, and the readable and Langfuse paths state
the same full content they received before references existed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider

from openjiuwen.extensions.observability.callback_handler import (
    OtelCallbackHandler,
    _set_sequence_addressed_attribute,
)
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.content_addressing import (
    parse_sequence_reference,
)
from openjiuwen.extensions.observability.exporters.langfuse import _generation_input
from openjiuwen.extensions.observability.gen_ai_semconv import (
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_SYSTEM_INSTRUCTIONS,
)
from openjiuwen.extensions.observability.otlp_codec import (
    encode_span_to_otlp_json,
    encode_span_with_addressed_sequences,
)
from openjiuwen.extensions.observability import sequence_registry as sequence_registry_module
from openjiuwen.extensions.observability.sequence_registry import (
    materialize_stated_value,
    registry_stats,
    register_sequence,
    reset_registry,
)

_ROUTED_MODEL_PLACEHOLDER = "<routed-model/>"
# Mirrors the registry's flat per-chain-node charge.
_NODE_OVERHEAD_BYTES = 160


@pytest.fixture(autouse=True)
def _clean_sequence_registry():
    """Isolate every test's registry state; the cap resets with it."""
    reset_registry()
    yield
    reset_registry()


class _FakeClock:
    """A controllable monotonic clock for TTL tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def _ttl_clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    """Patch the registry clock and make every registration trigger a sweep.

    The sweep cadence constants shrink to zero so a single new attach runs
    the eviction pass, keeping the TTL tests about age, not cadence.
    """
    fake = _FakeClock()
    monkeypatch.setattr(sequence_registry_module, "_clock", fake)
    monkeypatch.setattr(sequence_registry_module, "_SWEEP_EVERY_REGISTERS", 1)
    monkeypatch.setattr(sequence_registry_module, "_SWEEP_MIN_INTERVAL_SECONDS", 0)
    reset_registry()
    return fake


def _handler(tracer) -> OtelCallbackHandler:
    return OtelCallbackHandler(
        ObservabilityConfig(enabled=True, service_name="sequence-reference-test"),
        tracer=tracer,
    )


def _attach_input(span, handler, messages: list[dict]) -> None:
    handler._record_standard_structured_input(span, messages)


def _referenced(value: str) -> bool:
    return parse_sequence_reference(value) is not None


def test_attaching_input_states_a_reference_and_keeps_content_in_registry() -> None:
    """The span carries a reference; the registry holds the full value."""
    provider = TracerProvider()
    span = provider.get_tracer("attach-test").start_span("llm.call")
    messages = [{"role": "user", "content": "run the migration"}]

    _attach_input(span, _handler(provider.get_tracer("attach-test")), messages)
    span.end()

    stated = span.attributes[GEN_AI_INPUT_MESSAGES]
    assert _referenced(stated)
    materialized = materialize_stated_value(stated)
    assert "run the migration" in materialized
    provider.shutdown()


def test_cap_overflow_falls_back_to_stating_the_full_value_inline() -> None:
    """A registry at its byte cap never accepts content; the attribute inlines."""
    reset_registry(max_bytes=1)
    provider = TracerProvider()
    tracer = provider.get_tracer("cap-test")
    span = tracer.start_span("llm.call")
    messages = [{"role": "user", "content": "inline fallback"}]

    _attach_input(span, _handler(tracer), messages)
    span.end()

    stated = span.attributes[GEN_AI_INPUT_MESSAGES]
    assert not _referenced(stated)
    assert "inline fallback" in stated
    provider.shutdown()


def test_duplicate_registration_charges_content_once() -> None:
    """The same sequence registered twice adds no element and no charge."""
    value = json.dumps([{"role": "user", "content": "same request"}])
    from openjiuwen.extensions.observability.content_addressing import build_sequence

    sequence = build_sequence(GEN_AI_INPUT_MESSAGES, value)
    assert sequence is not None
    assert register_sequence(sequence)
    _, bytes_after_first = registry_stats()
    assert register_sequence(sequence)
    elements, bytes_after_second = registry_stats()
    assert bytes_after_first == bytes_after_second
    assert elements == 1


def test_appending_a_message_costs_its_increment_not_its_window() -> None:
    """A growing conversation registers what it added, not the whole window.

    The registry holds one entry per chain prefix and one per distinct
    element, so extending a window by one message stores that message alone.
    """
    provider = TracerProvider()
    tracer = provider.get_tracer("increment-test")
    handler = _handler(tracer)
    messages = [{"role": "user", "content": f"turn {index}: " + "x" * 400} for index in range(5)]

    first = tracer.start_span("llm.call.first")
    _attach_input(first, handler, messages)
    first_stated = first.attributes[GEN_AI_INPUT_MESSAGES]
    first.end()
    _, bytes_short = registry_stats()

    messages.append({"role": "assistant", "content": "appended answer"})
    second = tracer.start_span("llm.call.second")
    _attach_input(second, handler, messages)
    second_stated = second.attributes[GEN_AI_INPUT_MESSAGES]
    second.end()
    elements, bytes_long = registry_stats()

    assert elements == 6, "five messages plus the appended one, nothing restated"
    appended_bytes = bytes_long - bytes_short
    appended_value = json.dumps(
        [{"role": "assistant", "content": "appended answer"}],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert appended_bytes < len(appended_value.encode("utf-8")) + 4 * _NODE_OVERHEAD_BYTES
    assert "appended answer" in materialize_stated_value(second_stated)
    assert "appended answer" not in materialize_stated_value(first_stated)
    provider.shutdown()


def test_compression_fork_keeps_both_windows_resolvable() -> None:
    """A rewritten prefix forks the chain; either side still rebuilds.

    Compression replaces a window's head with a summary while keeping its
    tail verbatim: the fork is the design's own answer to changed content,
    and the shared tail elements stay a single copy. The summary rides the
    system-instructions attribute, the way an injected context summary is
    recorded -- each attribute addresses its own chain.
    """
    provider = TracerProvider()
    tracer = provider.get_tracer("compression-test")
    handler = _handler(tracer)
    messages = [{"role": "user", "content": f"turn {index}: " + "x" * 400} for index in range(5)]

    before = tracer.start_span("llm.call.before")
    _attach_input(before, handler, messages)
    before_stated = before.attributes[GEN_AI_INPUT_MESSAGES]
    before.end()

    compressed = [{"role": "system", "content": "summary of the earlier turns"}] + messages[-2:]
    after = tracer.start_span("llm.call.after")
    _attach_input(after, handler, compressed)
    after_stated = after.attributes[GEN_AI_INPUT_MESSAGES]
    after_system = after.attributes[GEN_AI_SYSTEM_INSTRUCTIONS]
    after.end()

    elements, _ = registry_stats()
    assert elements == 6, "five original messages plus the summary, tail shared"
    assert before_stated != after_stated, "a rewritten prefix must address a new chain"
    assert "turn 0" in materialize_stated_value(before_stated)
    assert "turn 0" not in materialize_stated_value(after_stated)
    assert "turn 3" in materialize_stated_value(after_stated)
    assert "turn 4" in materialize_stated_value(after_stated)
    assert "summary of the earlier turns" in materialize_stated_value(after_system)
    assert _referenced(after_system), "the injected summary is referenced like any restated value"
    provider.shutdown()


def test_materialize_matches_the_inline_fallback_byte_for_byte() -> None:
    """Rebuilding a stated reference reproduces the inline value exactly.

    Attach uses compact separators so the per-element rebuild joins back to
    the same bytes the inline fallback would state; the readable encode and
    the Langfuse projection both rely on this equality.
    """
    provider = TracerProvider()
    tracer = provider.get_tracer("byte-equality-test")
    handler = _handler(tracer)
    messages = [
        {"role": "system", "content": "sys-prompt 系统提示 with 中文 and \"quotes\""},
        {"role": "user", "content": "turn 0: " + "x" * 300},
    ]

    reset_registry(max_bytes=0)
    inline = tracer.start_span("llm.call.inline")
    _attach_input(inline, handler, messages)
    inline_value = inline.attributes[GEN_AI_INPUT_MESSAGES]
    inline.end()

    reset_registry()
    referenced = tracer.start_span("llm.call.referenced")
    _attach_input(referenced, handler, messages)
    stated = referenced.attributes[GEN_AI_INPUT_MESSAGES]
    referenced.end()

    assert materialize_stated_value(stated) == inline_value
    provider.shutdown()


def test_storage_encode_resolves_references_into_record_sequences() -> None:
    """The storage path returns the sequences a reference states, unchanged."""
    provider = TracerProvider()
    tracer = provider.get_tracer("storage-test")
    span = tracer.start_span("llm.call")
    messages = [{"role": "user", "content": "storage resolve"}]

    _attach_input(span, _handler(tracer), messages)
    span.end()

    encoded, sequences = encode_span_with_addressed_sequences(span)
    assert sequences, "a referenced attribute must resolve to its sequence"
    payload = json.loads(encoded.decode("utf-8"))
    stated = None
    for attribute in payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]:
        if attribute["key"] == GEN_AI_INPUT_MESSAGES:
            stated = attribute["value"]["stringValue"]
    assert stated is not None and _referenced(stated)
    blobs = b"".join(sequences[0].blobs[node.blob_hash] for node in sequences[0].nodes)
    assert "storage resolve" in blobs.decode("utf-8")
    provider.shutdown()


def test_readable_encode_materializes_full_content() -> None:
    """The JSONL and snapshot path keeps stating the full value verbatim."""
    provider = TracerProvider()
    tracer = provider.get_tracer("readable-test")
    span = tracer.start_span("llm.call")
    messages = [{"role": "user", "content": "readable materialization"}]

    _attach_input(span, _handler(tracer), messages)
    span.end()

    encoded = json.loads(encode_span_to_otlp_json(span).decode("utf-8"))
    stated = None
    for attribute in encoded["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]:
        if attribute["key"] == GEN_AI_INPUT_MESSAGES:
            stated = attribute["value"]["stringValue"]
    assert stated is not None and not _referenced(stated)
    assert "readable materialization" in stated
    provider.shutdown()


def test_langfuse_generation_input_reads_content_through_the_reference() -> None:
    """The Langfuse projection renders the same input it did before references."""
    provider = TracerProvider()
    tracer = provider.get_tracer("langfuse-test")
    span = tracer.start_span("llm.call")
    messages = [{"role": "user", "content": "langfuse projection"}]

    _attach_input(span, _handler(tracer), messages)
    span.end()

    rendered = _generation_input(span.attributes)
    assert rendered is not None
    assert "langfuse projection" in rendered
    assert not _referenced(rendered)
    provider.shutdown()


def test_routed_model_fill_materializes_then_states_a_new_reference() -> None:
    """Filling a placeholder rewrites content, so the stated reference changes."""
    provider = TracerProvider()
    tracer = provider.get_tracer("route-fill-test")
    span = tracer.start_span("llm.call")
    messages = [{"role": "user", "content": f"route via {_ROUTED_MODEL_PLACEHOLDER} please"}]

    handler = _handler(tracer)
    _attach_input(span, handler, messages)
    stated_before = span.attributes[GEN_AI_INPUT_MESSAGES]
    assert _referenced(stated_before)

    state = SimpleNamespace(span=span, routed_input_original=None)
    OtelCallbackHandler._fill_routed_model_in_input(state, "routed-model-x")
    span.end()

    stated_after = span.attributes[GEN_AI_INPUT_MESSAGES]
    assert _referenced(stated_after)
    assert stated_after != stated_before, "filled content must address a new chain"
    filled = materialize_stated_value(stated_after)
    assert "routed-model-x" in filled
    assert _ROUTED_MODEL_PLACEHOLDER not in filled
    provider.shutdown()


def test_offprocess_exporters_receive_materialized_content() -> None:
    """Exporters that ship spans off-process resolve references before sending.

    The OTLP gRPC/HTTP and console exporters serialize the span as-is, so
    their wiring wraps a materializing transform: a reference on the wire
    names content nobody off-process can reach. The transform is exercised
    directly; no export call is made.
    """
    from openjiuwen.extensions.observability.config import ObservabilityConfig
    from openjiuwen.extensions.observability.runtime import build_span_exporter

    provider = TracerProvider()
    tracer = provider.get_tracer("boundary-test")
    handler = _handler(tracer)
    span = tracer.start_span("llm.call")
    _attach_input(span, handler, [{"role": "user", "content": "off-process wire"}])
    span.end()

    for exporter_kind in ("console", "otlp_http"):
        config = ObservabilityConfig(
            enabled=True,
            service_name="boundary-test",
            exporter=exporter_kind,
            endpoint="http://127.0.0.1:4318",
        )
        exporter = build_span_exporter(config)
        projected = exporter._transform(span)
        stated = projected.attributes[GEN_AI_INPUT_MESSAGES]
        assert not _referenced(stated), f"{exporter_kind} wire must state full content"
        assert "off-process wire" in stated
    provider.shutdown()


def test_injected_exporter_override_receives_materialized_content() -> None:
    """An exporter injected via ``span_exporter_override`` also gets full content.

    The override is a public entry point: its owner may ship spans anywhere,
    including off-process, so references are resolved for it the same way
    the built-in exporters do. The runtime is exercised with an in-memory
    exporter as the override; the span it received must state content.
    """
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from openjiuwen.extensions.observability.config import ObservabilityConfig
    from openjiuwen.extensions.observability.runtime import ObservabilityRuntime

    override = InMemorySpanExporter()
    runtime = ObservabilityRuntime()
    config = ObservabilityConfig(enabled=True, service_name="override-test", sample_rate=1.0)
    runtime.initialize(config, span_exporter_override=override)
    tracer = runtime.get_tracer("override-test")
    handler = _handler(tracer)
    try:
        span = tracer.start_span("llm.call")
        _attach_input(span, handler, [{"role": "user", "content": "override wire"}])
        span.end()
    finally:
        runtime.shutdown()

    received = override.get_finished_spans()
    assert received, "the injected exporter must receive the span"
    stated = received[0].attributes[GEN_AI_INPUT_MESSAGES]
    assert not _referenced(stated), "an injected exporter must not see a bare reference"
    assert "override wire" in stated


def test_storage_encode_marks_an_unresolvable_reference_explicitly(
    _ttl_clock: _FakeClock,
) -> None:
    """A reference whose content the registry reclaimed degrades loudly.

    The record keeps an OTLP-valid attribute, but its value declares the
    loss instead of stating a reference that parses yet never resolves: a
    reader sees the placeholder and knows the window is gone, rather than
    chasing a hash nothing will ever answer.
    """
    provider = TracerProvider()
    tracer = provider.get_tracer("unresolvable-test")
    handler = _handler(tracer)
    span = tracer.start_span("llm.call")
    _attach_input(span, handler, [{"role": "user", "content": "doomed window"}])
    stated = span.attributes[GEN_AI_INPUT_MESSAGES]
    span.end()

    # Age the content out, then encode: the registry no longer holds it.
    _ttl_clock.now += sequence_registry_module._TTL_SECONDS + 10
    trigger = tracer.start_span("llm.call.trigger")
    _attach_input(trigger, handler, [{"role": "user", "content": "trigger"}])
    trigger.end()

    encoded, sequences = encode_span_with_addressed_sequences(span)
    payload = json.loads(encoded.decode("utf-8"))
    attribute_value = None
    for attribute in payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]:
        if attribute["key"] == GEN_AI_INPUT_MESSAGES:
            attribute_value = attribute["value"]["stringValue"]
    assert attribute_value is not None
    assert "unresolvable_sequence" in attribute_value
    assert "registry_evicted" in attribute_value
    assert not _referenced(attribute_value), "the placeholder is not itself a reference"
    assert sequences == (), "no sequence can be attached for lost content"

    # The readable encode (JSONL, snapshots) states the same placeholder:
    # one declared shape for a lost window on every path out of the process.
    readable = json.loads(encode_span_to_otlp_json(span).decode("utf-8"))
    readable_value = None
    for attribute in readable["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]:
        if attribute["key"] == GEN_AI_INPUT_MESSAGES:
            readable_value = attribute["value"]["stringValue"]
    assert readable_value is not None
    assert readable_value == attribute_value, (
        "storage and readable encodes declare the loss identically"
    )
    provider.shutdown()


def test_idle_content_is_reclaimed_after_the_ttl(_ttl_clock: _FakeClock) -> None:
    """Content nothing has touched for the TTL goes, and its reader degrades.

    A reclaimed reference states the declared placeholder with a warning --
    never a crash on the encode path -- while fresh content keeps its
    reference.
    """
    provider = TracerProvider()
    tracer = provider.get_tracer("ttl-evict-test")
    handler = _handler(tracer)

    stale = tracer.start_span("llm.call.stale")
    _attach_input(stale, handler, [{"role": "user", "content": "aging turn"}])
    stale_stated = stale.attributes[GEN_AI_INPUT_MESSAGES]
    stale.end()
    assert _referenced(stale_stated)

    _ttl_clock.now += sequence_registry_module._TTL_SECONDS + 10
    fresh = tracer.start_span("llm.call.fresh")
    _attach_input(fresh, handler, [{"role": "user", "content": "fresh turn"}])
    fresh_stated = fresh.attributes[GEN_AI_INPUT_MESSAGES]
    fresh.end()

    assert _referenced(fresh_stated), "the sweep runs before the fresh attach, freeing room"
    stale_materialized = materialize_stated_value(stale_stated)
    assert "unresolvable_sequence" in stale_materialized, (
        "a reclaimed reference states the declared placeholder, not a bare reference"
    )
    assert not _referenced(stale_materialized)
    assert "fresh turn" in materialize_stated_value(fresh_stated)
    elements, _ = registry_stats()
    assert elements == 1, "only the fresh element remains"
    provider.shutdown()


def test_resolving_a_reference_refreshes_its_whole_chain(_ttl_clock: _FakeClock) -> None:
    """A chain walk touches every prefix it visits, keeping a live chain resident.

    The kept chain's reference is resolved near the end of its TTL; content
    that no one resolved in the same window ages out in the same sweep.
    """
    provider = TracerProvider()
    tracer = provider.get_tracer("ttl-touch-test")
    handler = _handler(tracer)

    kept = tracer.start_span("llm.call.kept")
    kept_messages = [{"role": "user", "content": f"kept turn {i}"} for i in range(3)]
    _attach_input(kept, handler, kept_messages)
    kept_stated = kept.attributes[GEN_AI_INPUT_MESSAGES]
    kept.end()

    cold = tracer.start_span("llm.call.cold")
    _attach_input(cold, handler, [{"role": "user", "content": "cold turn"}])
    cold_stated = cold.attributes[GEN_AI_INPUT_MESSAGES]
    cold.end()

    _ttl_clock.now += sequence_registry_module._TTL_SECONDS - 100
    assert "kept turn 0" in materialize_stated_value(kept_stated)
    _ttl_clock.now += 200

    trigger = tracer.start_span("llm.call.trigger")
    _attach_input(trigger, handler, [{"role": "user", "content": "trigger turn"}])
    trigger.end()

    assert "kept turn 0" in materialize_stated_value(kept_stated), (
        "the walk refreshed every prefix, so the kept chain survives"
    )
    assert "unresolvable_sequence" in materialize_stated_value(cold_stated), (
        "untouched content aged out; its reference states the placeholder"
    )
    provider.shutdown()


def test_sweep_reclamation_rearms_a_capped_registry(_ttl_clock: _FakeClock) -> None:
    """A sweep before the cap check turns a full registry usable again."""
    reset_registry(max_bytes=8 * 1024)
    provider = TracerProvider()
    tracer = provider.get_tracer("ttl-cap-test")
    handler = _handler(tracer)
    big = "z" * 2048

    first = tracer.start_span("llm.call.first")
    _attach_input(first, handler, [{"role": "user", "content": big}])
    first_stated = first.attributes[GEN_AI_INPUT_MESSAGES]
    first.end()
    assert _referenced(first_stated)

    _ttl_clock.now += sequence_registry_module._TTL_SECONDS + 10
    second = tracer.start_span("llm.call.second")
    _attach_input(second, handler, [{"role": "user", "content": "after reclamation"}])
    second_stated = second.attributes[GEN_AI_INPUT_MESSAGES]
    second.end()

    assert _referenced(second_stated), "the sweep reclaimed the aged batch before the cap check"
    elements, charged = registry_stats()
    assert elements == 1
    assert charged <= 8 * 1024
    provider.shutdown()


def test_set_sequence_addressed_attribute_inlines_non_array_values() -> None:
    """A value that does not address is stated inline, never dropped."""
    provider = TracerProvider()
    span = provider.get_tracer("scalar-test").start_span("llm.call")

    _set_sequence_addressed_attribute(span, "some.scalar.attribute", "not json at all")
    span.end()

    assert span.attributes["some.scalar.attribute"] == "not json at all"
    provider.shutdown()
