# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json

from opentelemetry.sdk.trace import TracerProvider

from openjiuwen.extensions.observability.content_addressing import (
    build_sequence,
    parse_sequence_reference,
    rebuild_value,
    sequence_reference,
    split_elements,
)
from openjiuwen.extensions.observability.otlp_codec import (
    encode_span_with_addressed_sequences,
)
from tests.test_logger import logger


def _messages(count: int) -> str:
    return json.dumps(
        [{"role": "user", "content": f"turn {index}"} for index in range(count)],
        ensure_ascii=False,
    )


def test_the_same_content_always_builds_the_same_chain() -> None:
    """Nothing is remembered between calls; the content decides the chain.

    This is what lets any thread encode any span in any order and still let
    storage deduplicate by simply inserting a node it already holds.
    """
    first = build_sequence("gen_ai.input.messages", _messages(5))
    second = build_sequence("gen_ai.input.messages", _messages(5))

    assert first is not None and second is not None
    assert first.seq_hash == second.seq_hash
    assert [node.seq_hash for node in first.nodes] == [node.seq_hash for node in second.nodes]


def test_a_shared_prefix_shares_its_chain_nodes() -> None:
    """A longer conversation reuses every node of the shorter one it extends."""
    short = build_sequence("gen_ai.input.messages", _messages(5))
    long = build_sequence("gen_ai.input.messages", _messages(8))

    assert short is not None and long is not None
    shared = [node.seq_hash for node in long.nodes[:5]]
    assert shared == [node.seq_hash for node in short.nodes]
    # The head of the shorter sequence is an ancestor of the longer one, which
    # is what makes "has the prefix changed" answerable without reading content.
    assert long.nodes[5].prev_hash == short.seq_hash
    assert long.depth == 8
    logger.info("prefix reuse: {} of {} nodes shared", len(shared), long.depth)


def test_changing_an_early_element_forks_the_chain() -> None:
    """A rewritten prefix produces a different chain from the point it changed."""
    original = build_sequence("gen_ai.input.messages", _messages(5))
    edited = json.loads(_messages(5))
    edited[1]["content"] = "compacted"
    forked = build_sequence("gen_ai.input.messages", json.dumps(edited, ensure_ascii=False))

    assert original is not None and forked is not None
    assert original.nodes[0].seq_hash == forked.nodes[0].seq_hash
    assert original.nodes[1].seq_hash != forked.nodes[1].seq_hash
    assert original.seq_hash != forked.seq_hash


def test_only_an_array_is_addressed() -> None:
    """A chain states an array, so nothing else becomes one.

    Addressing a scalar made it indistinguishable from a single-element
    array, and both rebuild paths then had to guess which they held.
    """
    assert build_sequence("gen_ai.system_instructions", "you are an agent") is None
    assert build_sequence("gen_ai.output.messages", '{"role":"assistant"}') is None
    assert build_sequence("gen_ai.input.messages", "not json at all") is None


def test_a_single_element_array_rebuilds_as_an_array() -> None:
    """One assistant message is the ordinary case, and it must stay an array.

    A model turn states one message, so this chain is always depth 1. Losing
    the brackets here left every finished answer unreadable.
    """
    value = '[{"role":"assistant","parts":[{"type":"text","content":"hi"}]}]'
    sequence = build_sequence("gen_ai.output.messages", value)

    assert sequence is not None
    assert sequence.depth == 1
    assert sequence.nodes[0].prev_hash is None
    assert json.loads(rebuild_value(split_elements(value))) == json.loads(value)


def test_an_empty_value_addresses_nothing() -> None:
    assert build_sequence("gen_ai.input.messages", "") is None
    assert build_sequence("gen_ai.input.messages", "[]") is None


def test_a_reference_round_trips() -> None:
    assert parse_sequence_reference(sequence_reference("abc", 7)) == ("abc", 7)
    assert parse_sequence_reference("not a reference") is None
    assert parse_sequence_reference("@oj-seq:99:abc:7") is None
    assert parse_sequence_reference("@oj-seq:1:abc:not-a-number") is None
    assert parse_sequence_reference(None) is None


def test_elements_rebuild_the_value_they_came_from() -> None:
    value = _messages(3)
    elements = split_elements(value)

    assert len(elements) == 3
    assert json.loads(rebuild_value(elements)) == json.loads(value)


def test_encoding_replaces_restated_attributes_with_references() -> None:
    """The storage path carries references; the sequences state the content."""
    provider = TracerProvider()
    tracer = provider.get_tracer("addressing-test")
    instructions = '[{"type":"text","content":"be brief"}]'
    span = tracer.start_span("chat model")
    span.set_attribute("gen_ai.input.messages", _messages(4))
    span.set_attribute("gen_ai.system_instructions", instructions)
    # An addressed key whose value is not an array: it stays as it is rather
    # than becoming a chain that would rebuild as one.
    span.set_attribute("gen_ai.tool.definitions", "no tools")
    span.set_attribute("gen_ai.request.model", "model-x")
    span.end()

    try:
        encoded, sequences = encode_span_with_addressed_sequences(span)
    finally:
        provider.shutdown()

    document = json.loads(encoded)
    attributes = {
        attribute["key"]: attribute["value"].get("stringValue")
        for attribute in document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    }
    by_key = {sequence.key: sequence for sequence in sequences}

    assert set(by_key) == {"gen_ai.input.messages", "gen_ai.system_instructions"}
    for key, sequence in by_key.items():
        assert parse_sequence_reference(attributes[key]) == (sequence.seq_hash, sequence.depth)
    assert attributes["gen_ai.tool.definitions"] == "no tools"
    # An attribute that states no restated content is left exactly as it was.
    assert attributes["gen_ai.request.model"] == "model-x"


def test_logical_size_measures_the_rebuilt_span_not_the_references() -> None:
    """A page is budgeted by what a reader receives, which is the rebuilt span.

    Measuring the reference-carrying bytes would let one page promise its
    budget and deliver many times it.
    """
    provider = TracerProvider()
    tracer = provider.get_tracer("addressing-size-test")
    span = tracer.start_span("chat model")
    span.set_attribute("gen_ai.input.messages", _messages(40))
    span.end()

    try:
        encoded, sequences = encode_span_with_addressed_sequences(span)
    finally:
        provider.shutdown()

    stated = sum(sequence.logical_bytes for sequence in sequences)
    assert stated > len(encoded)
    logger.info("references {} bytes, rebuilt {} bytes", len(encoded), stated)
