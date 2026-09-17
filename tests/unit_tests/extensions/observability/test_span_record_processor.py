# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import threading

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.trace import Status, StatusCode, set_span_in_context

from openjiuwen.extensions.observability.file_exporter import _encode_span_line
from openjiuwen.extensions.observability.span_record_processor import (
    OtlpSpanRecord,
    OtlpSpanSnapshotRecord,
    SpanRecordProcessor,
)


class _Consumer:
    def __init__(self) -> None:
        self.records: list[OtlpSpanRecord] = []

    def consume(self, record: OtlpSpanRecord) -> None:
        self.records.append(record)


class _FailingConsumer:
    def consume(self, record: OtlpSpanRecord) -> None:
        del record
        raise RuntimeError("consumer failed")


class _SnapshotConsumer(_Consumer):
    def __init__(self) -> None:
        super().__init__()
        self.snapshots: list[OtlpSpanSnapshotRecord] = []

    def consume_snapshot(self, record: OtlpSpanSnapshotRecord) -> None:
        self.snapshots.append(record)


class _FailingSnapshotConsumer(_Consumer):
    def consume_snapshot(self, record: OtlpSpanSnapshotRecord) -> None:
        del record
        raise RuntimeError("snapshot consumer failed")


def _finished_child_span() -> ReadableSpan:
    tracer = TracerProvider().get_tracer("span-record-test")
    parent = tracer.start_span("agent.run")
    child = tracer.start_span("llm.call", context=set_span_in_context(parent))
    child.set_attribute("gen_ai.conversation.id", "conversation")
    child.set_attribute("openjiuwen.request.id", "request")
    child.set_attribute("openjiuwen.run.id", "run")
    child.set_attribute("openjiuwen.agent.mode", "agent.plan")
    child.set_attribute("openjiuwen.trace.schema_version", "1")
    child.set_attribute("openjiuwen.execution.subject.id", "subagent:one")
    child.set_attribute("openjiuwen.execution.subject.display_name", "Explore Agent")
    child.set_attribute("openjiuwen.execution.subject.kind", "subagent")
    child.set_attribute("openjiuwen.execution.subject.parent_id", "main")
    child.set_attribute("openjiuwen.execution.subject.session_id", "sub-session")
    child.add_event("test.event", {"answer": 42})
    child.set_status(Status(StatusCode.OK))
    child.end()
    parent.end()
    return child


def test_processor_delivers_exact_file_exporter_bytes_and_hints() -> None:
    processor = SpanRecordProcessor()
    consumer = _Consumer()
    processor.add_consumer(consumer)
    span = _finished_child_span()

    processor.on_end(span)

    assert len(consumer.records) == 1
    record = consumer.records[0]
    assert record.raw_json == _encode_span_line(span).encode("utf-8")
    assert record.session_id == "conversation"
    assert record.request_id == "request"
    assert record.run_id == "run"
    assert record.agent_mode == "agent.plan"
    assert record.schema_version == "1"
    assert record.execution_subject_id == "subagent:one"
    assert record.execution_subject_display_name == "Explore Agent"
    assert record.execution_subject_kind == "subagent"
    assert record.execution_subject_parent_id == "main"
    assert record.execution_subject_session_id == "sub-session"
    assert len(record.trace_id) == 32
    assert len(record.span_id) == 16
    assert len(record.parent_span_id or "") == 16
    assert record.start_time_unix_nano < record.end_time_unix_nano
    payload = json.loads(record.raw_json)
    encoded = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert encoded["traceId"] == record.trace_id
    assert encoded["spanId"] == record.span_id


def test_processor_identity_deduplicates_and_unregisters_consumers() -> None:
    processor = SpanRecordProcessor()
    consumer = _Consumer()
    processor.add_consumer(consumer)
    processor.register_consumer(consumer)

    processor.on_end(_finished_child_span())
    assert len(consumer.records) == 1

    processor.remove_consumer(consumer)
    processor.on_end(_finished_child_span())
    assert len(consumer.records) == 1

    processor.add_consumer(consumer)
    processor.on_end(_finished_child_span())
    assert len(consumer.records) == 2


def test_failing_consumer_does_not_block_following_consumer() -> None:
    processor = SpanRecordProcessor()
    consumer = _Consumer()
    processor.add_consumer(_FailingConsumer())
    processor.add_consumer(consumer)

    processor.on_end(_finished_child_span())

    assert len(consumer.records) == 1


def test_no_consumer_skips_encoding(monkeypatch) -> None:
    import openjiuwen.extensions.observability.span_record_processor as processor_module

    processor = SpanRecordProcessor()

    def fail_encoding(span):
        del span
        raise AssertionError("encoder should not be called")

    monkeypatch.setattr(
        processor_module,
        "encode_span_with_addressed_sequences",
        fail_encoding,
    )
    processor.on_end(_finished_child_span())


def test_provider_shutdown_does_not_own_consumer_lifecycle() -> None:
    processor = SpanRecordProcessor()
    consumer = _Consumer()
    processor.add_consumer(consumer)

    processor.shutdown()
    processor.on_end(_finished_child_span())

    assert len(consumer.records) == 1


def test_recording_span_snapshots_are_revisioned_and_final_stays_authoritative() -> None:
    processor = SpanRecordProcessor()
    consumer = _SnapshotConsumer()
    processor.register_consumer(consumer)
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("span-record-live-test")

    span = tracer.start_span(
        "llm.call",
        attributes={"gen_ai.conversation.id": "conversation"},
    )

    assert len(consumer.snapshots) == 1
    started = consumer.snapshots[0]
    assert started.update_kind == "started"
    assert started.record_revision == 1
    assert started.lifecycle == "running"
    assert started.session_id == "conversation"
    started_payload = json.loads(started.raw_json)
    started_span = started_payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert "endTimeUnixNano" not in started_span

    span.set_attribute("gen_ai.request.model", "test-model")
    span.add_event("openjiuwen.stream.chunk", {"openjiuwen.stream.text": "hello"})
    processor.publish_snapshot(span, "stream_chunk")

    updated = consumer.snapshots[-1]
    assert updated.record_revision == 2
    updated_payload = json.loads(updated.raw_json)
    updated_span = updated_payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert updated_span["events"][0]["name"] == "openjiuwen.stream.chunk"
    span.end()

    assert len(consumer.records) == 1
    final = consumer.records[0]
    assert final.trace_id == updated.trace_id
    assert final.span_id == updated.span_id
    assert final.record_revision == 3
    assert final.lifecycle == "final"
    final_payload = json.loads(final.raw_json)
    final_span = final_payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert "endTimeUnixNano" in final_span


def test_ended_span_only_consumer_never_receives_recording_snapshots() -> None:
    processor = SpanRecordProcessor()
    consumer = _Consumer()
    processor.register_consumer(consumer)
    provider = TracerProvider()
    provider.add_span_processor(processor)

    span = provider.get_tracer("span-record-compat-test").start_span("llm.call")
    span.set_attribute("test.value", "changed")
    processor.publish_snapshot(span, "attributes")

    assert consumer.records == []
    span.end()
    assert len(consumer.records) == 1
    assert consumer.records[0].record_revision == 1


def test_failing_snapshot_consumer_does_not_block_following_consumer() -> None:
    processor = SpanRecordProcessor()
    consumer = _SnapshotConsumer()
    processor.register_consumer(_FailingSnapshotConsumer())
    processor.register_consumer(consumer)
    provider = TracerProvider()
    provider.add_span_processor(processor)

    span = provider.get_tracer("span-record-live-failure-test").start_span("llm.call")

    assert len(consumer.snapshots) == 1
    span.end()
    assert len(consumer.records) == 1


def test_delivery_defers_encoding_until_the_payload_is_read(monkeypatch) -> None:
    import openjiuwen.extensions.observability.span_record_processor as processor_module

    encode_calls: list[ReadableSpan] = []
    original_encoder = processor_module.encode_span_with_addressed_sequences

    def counting_encoder(span: ReadableSpan):
        encode_calls.append(span)
        return original_encoder(span)

    monkeypatch.setattr(
        processor_module,
        "encode_span_with_addressed_sequences",
        counting_encoder,
    )
    processor = SpanRecordProcessor()
    consumer = _Consumer()
    processor.register_consumer(consumer)

    processor.on_end(_finished_child_span())

    assert len(consumer.records) == 1
    assert encode_calls == [], "delivery must not encode on the calling thread"

    payload = consumer.records[0].raw_json
    assert len(encode_calls) == 1
    assert consumer.records[0].raw_json is payload, "payload must be cached"
    assert len(encode_calls) == 1


def test_snapshot_payload_is_frozen_at_capture_time() -> None:
    processor = SpanRecordProcessor()
    consumer = _SnapshotConsumer()
    processor.register_consumer(consumer)
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("span-record-freeze-test")

    span = tracer.start_span("llm.call")
    span.add_event("first.chunk")
    processor.publish_snapshot(span, "stream_chunk")
    captured = consumer.snapshots[-1]

    # Mutate and end the span before the payload is ever read. A deferred
    # encode must still describe the span as it was at capture time.
    span.add_event("second.chunk")
    span.set_attribute("gen_ai.request.model", "late-model")
    span.end()

    captured_span = json.loads(captured.raw_json)["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert [event["name"] for event in captured_span["events"]] == ["first.chunk"]
    assert "endTimeUnixNano" not in captured_span
    attribute_keys = {item["key"] for item in captured_span.get("attributes", ())}
    assert "gen_ai.request.model" not in attribute_keys


def test_unregister_waits_for_consumer_lease_before_sink_close() -> None:
    consume_started = threading.Event()
    allow_consume = threading.Event()

    class _ClosingSink:
        def __init__(self) -> None:
            self.closed = False
            self.accepted = 0
            self.dropped = 0
            self.events: list[str] = []

        def consume(self, record: OtlpSpanRecord) -> None:
            del record
            consume_started.set()
            if not allow_consume.wait(timeout=1):
                raise TimeoutError("test consumer was not released")
            if self.closed:
                self.dropped += 1
                self.events.append("dropped")
                return
            self.accepted += 1
            self.events.append("accepted")

        def close(self) -> None:
            self.closed = True
            self.events.append("closed")

    processor = SpanRecordProcessor()
    sink = _ClosingSink()
    following_consumer = _Consumer()
    processor.register_consumer(sink)
    processor.register_consumer(following_consumer)
    unregister_started = threading.Event()
    unregister_returned = threading.Event()
    failures: list[BaseException] = []

    def deliver() -> None:
        try:
            processor.on_end(_finished_child_span())
        except BaseException as exc:
            failures.append(exc)

    def unregister_and_close() -> None:
        try:
            unregister_started.set()
            processor.unregister_consumer(sink, timeout_millis=1000)
            sink.close()
        except BaseException as exc:
            failures.append(exc)
        finally:
            unregister_returned.set()

    delivery_thread = threading.Thread(target=deliver)
    unregister_thread = threading.Thread(target=unregister_and_close)
    delivery_thread.start()
    assert consume_started.wait(timeout=1)
    unregister_thread.start()
    assert unregister_started.wait(timeout=1)
    assert not unregister_returned.wait(timeout=0.05)
    with pytest.raises(RuntimeError, match="unregister is still in progress"):
        processor.register_consumer(sink)

    allow_consume.set()
    delivery_thread.join(timeout=1)
    unregister_thread.join(timeout=1)

    assert not delivery_thread.is_alive()
    assert not unregister_thread.is_alive()
    assert failures == []
    assert sink.closed is True
    assert sink.accepted == 1
    assert sink.dropped == 0
    assert sink.events == ["accepted", "closed"]
    assert len(following_consumer.records) == 1


def test_consumer_can_unregister_itself_without_deadlock() -> None:
    processor = SpanRecordProcessor()
    failures: list[BaseException] = []

    class _SelfRemovingConsumer:
        def __init__(self) -> None:
            self.records: list[OtlpSpanRecord] = []

        def consume(self, record: OtlpSpanRecord) -> None:
            self.records.append(record)
            processor.unregister_consumer(self, timeout_millis=100)

    consumer = _SelfRemovingConsumer()
    processor.register_consumer(consumer)

    def deliver() -> None:
        try:
            processor.on_end(_finished_child_span())
        except BaseException as exc:
            failures.append(exc)

    delivery_thread = threading.Thread(target=deliver)
    delivery_thread.start()
    delivery_thread.join(timeout=1)

    assert not delivery_thread.is_alive()
    assert failures == []
    assert len(consumer.records) == 1
    processor.unregister_consumer(consumer)
    processor.on_end(_finished_child_span())
    assert len(consumer.records) == 1


def test_unregister_timeout_disables_new_leases_and_allows_retry() -> None:
    processor = SpanRecordProcessor()
    entered = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []

    class _BlockingConsumer:
        def __init__(self) -> None:
            self.records: list[OtlpSpanRecord] = []

        def consume(self, record: OtlpSpanRecord) -> None:
            self.records.append(record)
            entered.set()
            if not release.wait(timeout=1):
                raise TimeoutError("test consumer was not released")

    consumer = _BlockingConsumer()
    processor.register_consumer(consumer)

    def deliver() -> None:
        try:
            processor.on_end(_finished_child_span())
        except BaseException as exc:
            failures.append(exc)

    delivery_thread = threading.Thread(target=deliver)
    delivery_thread.start()
    assert entered.wait(timeout=1)

    with pytest.raises(TimeoutError, match="waiting for span record consumer"):
        processor.unregister_consumer(consumer, timeout_millis=10)
    processor.on_end(_finished_child_span())
    assert len(consumer.records) == 1

    release.set()
    delivery_thread.join(timeout=1)
    assert not delivery_thread.is_alive()
    assert failures == []
    processor.unregister_consumer(consumer, timeout_millis=10)
