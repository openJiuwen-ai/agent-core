# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end ownership and lazy reads of captured sequence attributes."""

import json
from weakref import ref

import pytest
from opentelemetry.sdk.trace import TracerProvider

from openjiuwen.agent_evolving.trajectory import Trajectory, TrajectorySpanProcessor
from openjiuwen.agent_evolving.trajectory.schema import TRAJECTORY_ID
from openjiuwen.agent_evolving.trajectory.spans import (
    attributes_from_map,
    attributes_to_map,
    encode_otlp_value,
    iter_compact_spans,
    iter_spans,
    read_llm_exchange,
    span_attributes,
    trim_trajectory,
)
from openjiuwen.agent_evolving.trajectory.team import select_team_spans
from openjiuwen.extensions.observability import semconv
from openjiuwen.extensions.observability.callback_handler import OtelCallbackHandler
from openjiuwen.extensions.observability.config import ObservabilityConfig
from openjiuwen.extensions.observability.content_addressing import SequenceHead, build_sequence, sequence_reference
from openjiuwen.extensions.observability.sequence_registry import (
    lookup_sequence_head,
    register_sequence,
    reset_registry,
    unresolvable_placeholder,
)
from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail, _InvokeCapture
from openjiuwen.harness.rails.evolution.team_skill_evolution_rail import infer_team_skill_from_trajectory

_INPUT = semconv.GEN_AI_INPUT_MESSAGES
_TOOLS = semconv.GEN_AI_TOOL_DEFINITIONS
_OUTPUT = semconv.GEN_AI_OUTPUT_MESSAGES
_A = '{"role":"user","parts":[{"type":"text","content":"A"}]}'
_B = '{"role":"user","parts":[{"type":"text","content":"B"}]}'
_C = '{"role":"user","parts":[{"type":"text","content":"C"}]}'
_S = '{"role":"user","parts":[{"type":"text","content":"摘要"}]}'


class _Rail(EvolutionRail):
    async def run_evolution(self, *args, **kwargs):
        raise AssertionError("evolution should not run while draining the test window")


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def _register(*messages, key=_INPUT):
    stated = "[" + ",".join(messages) + "]"
    sequence = build_sequence(key, stated)
    assert sequence is not None and register_sequence(sequence)
    return sequence_reference(sequence.seq_hash, sequence.depth), stated


def _head(trajectory, key=_INPUT):
    span = list(iter_compact_spans(trajectory))[-1]
    return next(item["value"]["stringValue"] for item in span["attributes"] if item["key"] == key)


def test_public_otlp_encoding_emits_plain_strings_for_sequence_heads():
    reference, expected = _register(_A, _B)
    head = lookup_sequence_head(reference.split(":")[2])
    assert head is not None

    encoded = encode_otlp_value([head])
    assert json.loads(json.dumps(encoded)) == {"arrayValue": {"values": [{"stringValue": expected}]}}
    attrs = attributes_from_map({_INPUT: head})
    assert json.loads(json.dumps(attrs)) == [{"key": _INPUT, "value": {"stringValue": expected}}]

    compact = attributes_from_map({_INPUT: head}, preserve_sequences=True)
    assert compact[0]["value"]["stringValue"] is head

    nested = attributes_from_map({_INPUT: {"messages": [head]}}, preserve_sequences=True)
    decoded = attributes_to_map(nested, preserve_sequences=True)
    assert decoded[_INPUT]["messages"][0] is head


def test_pending_window_snapshots_own_only_reachable_sequences(monkeypatch):
    processor = TrajectorySpanProcessor()
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("trajectory-sequence-store")
    main = processor.subscribe(include_span_categories=["llm"])
    second = processor.subscribe(include_span_categories=["llm"])
    rail = _Rail(trajectory_span_processor=processor, max_trajectory_spans=1)
    capture = _InvokeCapture(main, ("agent", "session", ""), "session", None)

    original_materialize = SequenceHead.materialize
    reads = []

    def record_read(head):
        reads.append(head.seq_hash)
        return original_materialize(head)

    monkeypatch.setattr(SequenceHead, "materialize", record_read)
    ab_ref, ab_value = _register(_A, _B)
    ab = lookup_sequence_head(ab_ref.split(":")[2])
    assert ab is not None
    ab_lifetime = ref(ab)
    a_lifetime = ref(ab.previous.message)
    with tracer.start_as_current_span("llm.call", attributes={_INPUT: ab_ref, _OUTPUT: "[]"}):
        pass
    reset_registry()  # The pending records already own AB.
    old, first, issues = rail._drain_for_hook(None, capture=capture, required_category="llm")
    duplicate, duplicate_issues = processor.drain(second)
    assert not issues and not duplicate_issues
    assert _head(old) is _head(first) is _head(duplicate) is ab
    del ab, first, duplicate

    sc_ref, sc_value = _register(_S, _C)
    tools, tools_value = _register('{"name":"lookup"}', key=_TOOLS)
    with tracer.start_as_current_span("llm.call", attributes={_INPUT: sc_ref, _TOOLS: tools}):
        pass
    processor.unsubscribe(second)
    reset_registry()
    current, increment, issues = rail._drain_for_hook(None, capture=capture, required_category="llm")
    assert not issues
    detached = rail.get_trajectory(session_id="session").with_resource_attributes({"test": True})
    assert _head(current) is _head(increment) is _head(detached)
    sc_lifetime = ref(_head(current))
    c_lifetime = ref(_head(current).message)
    tools_lifetime = ref(_head(current, _TOOLS))
    assert reads == []

    (target,) = iter_compact_spans(detached)
    prompt, output = read_llm_exchange(target)
    assert [message["content"] for message in prompt] == ["摘要", "C"]
    assert output == []
    assert reads == [_head(detached).seq_hash]
    reads.clear()
    assert span_attributes(target, keys=(_TOOLS,)) == {_TOOLS: tools_value}
    assert reads == [_head(detached, _TOOLS).seq_hash]

    assert span_attributes(next(iter_spans(old))) == {_INPUT: ab_value, _OUTPUT: "[]"}
    payload = current.to_otlp()
    assert span_attributes(next(iter_spans(payload))) == {_INPUT: sc_value, _TOOLS: tools_value}
    assert Trajectory.from_otlp(payload) == current

    def is_uncounted(span):
        item = next(item for item in span["attributes"] if item["key"] == _INPUT)
        assert item["value"]["stringValue"] == sc_value
        return False

    assert trim_trajectory(current, 1, uncounted=is_uncounted) == current
    del target, current, detached, increment
    rail._scope_windows.clear()
    assert sc_lifetime() is c_lifetime() is tools_lifetime() is None
    assert ab_lifetime() is not None and a_lifetime() is not None
    assert span_attributes(next(iter_spans(old)))[_INPUT] == ab_value
    del old
    assert ab_lifetime() is a_lifetime() is None
    provider.shutdown()


def test_capture_keeps_inline_values_and_missing_reference_placeholder():
    reference, _ = _register(_A)
    _, _, seq_hash, depth = reference.split(":")
    reset_registry()
    processor = TrajectorySpanProcessor()
    provider = TracerProvider()
    provider.add_span_processor(processor)
    subscription = processor.subscribe(include_span_categories=["llm"])
    tracer = provider.get_tracer("trajectory")
    with tracer.start_as_current_span(
        "llm.call", attributes={_INPUT: reference, _OUTPUT: "already inline", "other.key": reference}
    ):
        pass
    trajectory, issues = processor.drain(subscription)
    assert not issues
    assert span_attributes(next(iter_spans(trajectory))) == {
        _INPUT: unresolvable_placeholder(seq_hash, int(depth)),
        _OUTPUT: "already inline",
        "other.key": reference,
    }
    provider.shutdown()


def test_structured_input_producer_recovers_system_instructions_after_source_reset():
    processor = TrajectorySpanProcessor()
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("evolution-materialization")
    handler = OtelCallbackHandler(
        ObservabilityConfig(enabled=True, service_name="evolution-materialization"), tracer=tracer
    )
    subscription = processor.subscribe(include_span_categories=["llm"])
    with tracer.start_as_current_span("llm.call") as span:
        handler._record_standard_structured_input(
            span,
            [
                {"role": "system", "content": "answer briefly"},
                {"role": "user", "content": "hello evolution"},
            ],
        )
    reset_registry()
    trajectory, issues = processor.drain(subscription)
    assert not issues
    assert isinstance(_head(trajectory, semconv.GEN_AI_SYSTEM_INSTRUCTIONS), SequenceHead)
    prompts, _ = read_llm_exchange(next(iter_compact_spans(trajectory)))
    assert prompts == [
        {"role": "system", "content": "answer briefly"},
        {"role": "user", "content": "hello evolution"},
    ]
    provider.shutdown()


@pytest.fixture
def _captured_model_trajectories():
    provider = TracerProvider()
    processors = [TrajectorySpanProcessor(), TrajectorySpanProcessor()]
    subscriptions = []
    for processor in processors:
        provider.add_span_processor(processor)
        subscriptions.append(processor.subscribe(include_span_categories=["llm"]))
    reference, expected = _register(_A, _B)
    source_head = lookup_sequence_head(reference.split(":")[2])
    tracer = provider.get_tracer("compact-model-only")
    with tracer.start_as_current_span("llm.call", attributes={_INPUT: reference, semconv.AT_TEAM_ID: "team"}):
        pass

    trajectories = []
    for processor, subscription in zip(processors, subscriptions):
        trajectory, issues = processor.drain(subscription)
        processor.unsubscribe(subscription)
        assert trajectory is not None and not issues
        assert _head(trajectory) is source_head
        trajectories.append(trajectory)
    reset_registry()
    provider.shutdown()
    return trajectories[0], trajectories[1], expected


@pytest.fixture
def _materialized_sequences(monkeypatch):
    original_materialize = SequenceHead.materialize
    reads = []

    def record_read(head):
        reads.append(head.seq_hash)
        return original_materialize(head)

    monkeypatch.setattr(SequenceHead, "materialize", record_read)
    return reads


def test_team_selection_and_skill_attribution_skip_model_sequences(
    _captured_model_trajectories, _materialized_sequences
):
    trajectory, _, expected = _captured_model_trajectories
    assert select_team_spans(trajectory, categories={"tool"}) == []
    assert select_team_spans(trajectory, categories={"tool"}, team_id="team") == []
    assert select_team_spans(trajectory, categories={"llm"}, trace_id="other-trace") == []
    assert infer_team_skill_from_trajectory(trajectory, {"known-skill"}) is None
    assert _materialized_sequences == []

    (selected,) = select_team_spans(trajectory, categories={"llm"}, team_id="team")
    assert span_attributes(selected)[_INPUT] == expected
    assert _materialized_sequences == [_head(trajectory).seq_hash]


def test_trajectory_equality_only_materializes_when_comparing_inline_values(
    _captured_model_trajectories, _materialized_sequences
):
    left, right, _ = _captured_model_trajectories
    assert _head(left) is _head(right)
    assert left == right
    assert left != right.with_resource_attributes({TRAJECTORY_ID: "different"})
    assert _materialized_sequences == []

    payload = left.to_otlp()
    resource_span = payload["resourceSpans"][0]
    # A late metadata difference must still short-circuit mixed comparisons.
    resource_span["resource"] = resource_span.pop("resource")
    inline = Trajectory.from_otlp(payload)
    _materialized_sequences.clear()
    assert inline.with_resource_attributes({TRAJECTORY_ID: "different"}) != left
    assert _materialized_sequences == []

    assert left == inline
    assert inline == left
    assert _materialized_sequences == [_head(left).seq_hash] * 2
