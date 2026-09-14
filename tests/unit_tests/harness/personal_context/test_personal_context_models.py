from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from openjiuwen.harness.personal_context.models import FetchBatch, PersonalContextStatus, RawChangeItem


def _item(**overrides):
    value = {
        "logical_id": "source/item",
        "revision_id": "revision-1",
        "operation": "upsert",
        "title": "A title",
        "content": "body",
        "original_ref": "https://example.com/item",
        "metadata": {"source": "test"},
        "raw_snapshot": None,
    }
    value.update(overrides)
    return value


def test_raw_change_item_requires_content_for_upsert_and_forbids_content_for_delete():
    with pytest.raises(ValidationError):
        RawChangeItem(**_item(content=None))

    with pytest.raises(ValidationError):
        RawChangeItem(**_item(operation="delete", content="body"))

    item = RawChangeItem(**_item(operation="delete", content=None))
    assert item.content is None


def test_raw_change_item_enforces_content_snapshot_and_metadata_boundaries():
    with pytest.raises(ValidationError):
        RawChangeItem(**_item(content="x" * 2_000_001))

    with pytest.raises(ValidationError):
        RawChangeItem(**_item(raw_snapshot=b"x" * (2 * 1024 * 1024 + 1)))

    with pytest.raises(ValidationError):
        RawChangeItem(**_item(metadata={"large": "x" * (64 * 1024)}))


def test_raw_change_item_metadata_is_json_serializable_and_copied():
    metadata = {"nested": {"value": 1}}
    item = RawChangeItem(**_item(metadata=metadata))
    metadata["nested"]["value"] = 2

    assert item.metadata["nested"]["value"] == 1
    json.dumps(item.model_dump(mode="json"))


def test_fetch_batch_enforces_size_pairing_and_cursor_json_boundary():
    item = RawChangeItem(**_item())
    batch = FetchBatch(batch_id="batch-1", items=[item], next_cursor={"offset": 1})

    assert isinstance(batch.items, tuple)
    with pytest.raises(ValidationError):
        FetchBatch(batch_id="batch-1", items=[item] * 21)
    with pytest.raises(ValidationError):
        FetchBatch(batch_id="batch-1", items=[item], materialized_source_path="candidate")
    with pytest.raises(ValidationError):
        FetchBatch(batch_id="../batch", items=[])
    with pytest.raises(ValidationError):
        FetchBatch(batch_id="a" * 129, items=[])

    bounded_batch = FetchBatch(batch_id="a" * 128, items=[])
    assert bounded_batch.batch_id == "a" * 128

    json.dumps(batch.model_dump(mode="json"))


def test_personal_context_status_is_frozen_and_copies_nested_state():
    states = {"notes": "RUNNING"}
    status = PersonalContextStatus(
        configured=True,
        enabled=True,
        fetching_enabled=True,
        state="RUNNING",
        pipeline_running=True,
        pipeline_queue_size=0,
        fetch_service_states=states,
        fetch_service_errors={},
        context_root="/tmp/context",
        context_ready=True,
        last_error=None,
    )
    states["notes"] = "FAILED"

    assert status.fetch_service_states["notes"] == "RUNNING"
    with pytest.raises(ValidationError):
        status.state = "FAILED"


def test_status_reports_fetching_switch():
    status = PersonalContextStatus(
        configured=True,
        enabled=True,
        fetching_enabled=False,
        state="RUNNING",
        pipeline_running=True,
        pipeline_queue_size=0,
        fetch_service_states={"notes": "STOPPED"},
        fetch_service_errors={},
        context_root="C:/personal_context/workspace/context",
        context_ready=True,
        last_error=None,
    )
    assert status.fetching_enabled is False


def test_personal_context_status_last_error_has_a_bounded_public_shape():
    with pytest.raises(ValidationError):
        PersonalContextStatus(
            configured=True,
            enabled=True,
            fetching_enabled=True,
            state="RUNNING",
            pipeline_running=True,
            pipeline_queue_size=0,
            fetch_service_states={},
            fetch_service_errors={},
            context_root="/tmp/context",
            context_ready=True,
            last_error={
                "code": 154000,
                "status": "ValidationError",
                "message": "bad",
                "operation": "configure",
                "params": {"token": "secret"},
            },
        )


def test_personal_context_status_rejects_unknown_fetch_service_state():
    with pytest.raises(ValidationError):
        PersonalContextStatus(
            configured=True,
            enabled=True,
            fetching_enabled=True,
            state="RUNNING",
            pipeline_running=True,
            pipeline_queue_size=0,
            fetch_service_states={"notes": "BROKEN"},
            fetch_service_errors={},
            context_root="/tmp/context",
            context_ready=True,
            last_error=None,
        )


def test_personal_context_status_rejects_nested_last_error_values():
    with pytest.raises(ValidationError):
        PersonalContextStatus(
            configured=True,
            enabled=True,
            fetching_enabled=True,
            state="RUNNING",
            pipeline_running=True,
            pipeline_queue_size=0,
            fetch_service_states={},
            fetch_service_errors={},
            context_root="/tmp/context",
            context_ready=True,
            last_error={
                "code": 154000,
                "status": "ValidationError",
                "message": {"token": "SECRET"},
                "operation": "configure",
            },
        )


def test_personal_context_status_rejects_non_string_last_error_message():
    with pytest.raises(ValidationError):
        PersonalContextStatus(
            configured=True,
            enabled=True,
            fetching_enabled=True,
            state="RUNNING",
            pipeline_running=True,
            pipeline_queue_size=0,
            fetch_service_states={},
            fetch_service_errors={},
            context_root="/tmp/context",
            context_ready=True,
            last_error={
                "code": 154000,
                "status": "ValidationError",
                "message": 123,
                "operation": "configure",
            },
        )
