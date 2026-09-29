# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Protocol boundary tests, without a model, persistence backend, or Feishu."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest
from pydantic import ValidationError

from openjiuwen.core.common.exception.errors import BaseError

pytestmark = pytest.mark.level0
WHEN = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)
SCOPE = dict(
    namespace_id="tenant",
    agent_id="agent",
    session_id="session",
    request_id="request",
    execution_id="execution",
    source_ref="source:message-1",
    epoch=3,
)


@pytest.fixture
def api():
    return import_module("openjiuwen.harness.persistent_agent")


def execution(api):
    return api.ExecutionContext(
        **SCOPE,
        config_version="v1",
        config_fingerprint="a" * 64,
        authority_handle="lease-handle-1",
        principal_ref="user",
        startup_scope=frozenset({"read", "write"}),
        source_scope=frozenset({"group"}),
        policy_revision="p1",
        capability_snapshot=frozenset({"construction"}),
    )


def event(api, **changes):
    args = dict(
        event_type="execution.started",
        local_seq=1,
        occurred_at=WHEN,
        payload={"goal": "task", "decision_event_id": "decision-1"},
    )
    args.update(changes)
    return api.AgentEvent.create(execution(api), **args)


def receipt(api, proposal, **changes):
    args = dict(
        **SCOPE,
        event_id=proposal.event_id,
        local_seq=proposal.local_seq,
        payload_digest=proposal.proposal_digest(),
        status="confirmed",
        request_event_seq=7,
    )
    args.update(changes)
    return api.CommitResult(**args)


def test_event_roundtrip_keeps_id_payload_and_digest(api):
    proposal = event(api)
    restored = api.AgentEvent.model_validate_json(proposal.model_dump_json())
    assert restored.payload.goal == "task"
    assert restored.event_id == proposal.event_id
    assert restored.source_ref == "source:message-1"
    assert restored.proposal_digest() == proposal.proposal_digest()
    assert len(proposal.event_id) == 68 and proposal.event_id.startswith("evt_")
    restored.assert_matches(execution(api))


def test_request_source_is_required_and_bound_to_protocol_identity(api):
    with pytest.raises(BaseError):
        replace(execution(api), source_ref=" ")
    with pytest.raises(BaseError):
        replace(execution(api), authority_handle=" ")
    proposal = event(api)
    with pytest.raises(BaseError):
        proposal.assert_matches(replace(execution(api), source_ref="source:message-2"))


def test_context_and_material_contracts_keep_unknown_distinct_from_empty(api):
    empty_head = api.ContextHeadResult(**SCOPE, status="empty", query_complete=True, watermark=0)
    unknown_head = api.ContextHeadResult(**SCOPE, status="unknown", reason="store timed out")
    assert empty_head.status == "empty" and unknown_head.status == "unknown"
    with pytest.raises(ValidationError):
        api.ContextHeadResult(**SCOPE, status="empty")

    missing_window = api.WindowReadResult(
        **SCOPE,
        status="not_found",
        window_ref="window-1",
        window_revision=1,
        query_complete=True,
        watermark=3,
    )
    unknown_window = api.WindowReadResult(
        **SCOPE, status="unknown", window_ref="window-1", window_revision=1, reason="read timed out"
    )
    assert missing_window.status == "not_found" and unknown_window.status == "unknown"
    with pytest.raises(ValidationError):
        api.WindowReadResult(**SCOPE, status="not_found", window_ref="window-1", window_revision=1)

    snapshot = api.MaterialSnapshot(
        material_ref="material-1",
        source_ref="group:chat-1",
        scope_ref="chat-1",
        version_ref="revision-4",
        content_ref="blob-1",
        content_digest="c" * 64,
        policy_revision="policy-7",
        completeness="complete",
    )
    found = api.MaterialReadResult(**SCOPE, status="found", items=(snapshot,))
    empty = api.MaterialReadResult(**SCOPE, status="empty", query_complete=True, watermark=5)
    unknown = api.MaterialReadResult(**SCOPE, status="unknown", reason="material service timed out")
    assert found.items[0].version_ref == "revision-4"
    assert empty.items == () and unknown.status == "unknown"
    with pytest.raises(ValidationError):
        api.MaterialReadResult(**SCOPE, status="empty")


def test_window_mutation_receipt_distinguishes_unknown_from_absence(api):
    with pytest.raises(ValidationError):
        api.WindowMutationResult(**SCOPE, operation_id="write-1", status="applied")
    unknown = api.WindowMutationResult(**SCOPE, operation_id="write-1", status="unknown", reason="write timed out")
    assert unknown.status == "unknown"
    missing = api.WindowMutationResult(
        **SCOPE,
        operation_id="write-1",
        status="not_found",
        query_complete=True,
        watermark=4,
    )
    with pytest.raises(BaseError):
        missing.assert_operation(execution(api), "write-1")
    missing.assert_operation(execution(api), "write-1", query=True)
    with pytest.raises(BaseError):
        missing.assert_operation(execution(api), "other-write", query=True)


def test_retransmission_is_same_proposal_but_changed_content_conflicts(api):
    proposal = event(api)
    proposal.assert_same_proposal(event(api))
    changed = event(api, payload={"goal": "other", "decision_event_id": "decision-1"})
    assert changed.event_id == proposal.event_id
    with pytest.raises(BaseError):
        proposal.assert_same_proposal(changed)


@pytest.mark.parametrize(
    "field,value",
    [("namespace_id", "tenant-2"), ("session_id", "session-2"), ("execution_id", "execution-2"), ("epoch", 4)],
)
def test_event_ids_separate_execution_scope(api, field, value):
    old = event(api)
    newer = api.AgentEvent.create(
        replace(execution(api), **{field: value}),
        event_type="execution.started",
        local_seq=1,
        occurred_at=WHEN,
        payload={"goal": "task", "decision_event_id": "decision-1"},
    )
    assert newer.event_id != old.event_id
    with pytest.raises(BaseError):
        old.assert_matches(replace(execution(api), **{field: value}))


@pytest.mark.parametrize(
    "change",
    [
        {"local_seq": 0},
        {"local_seq": True},
        {"event_type": "chat.final"},
        {"occurred_at": datetime(2026, 9, 28)},
        {"visibility": "delivered"},
        {"payload": {"goal": "task"}},
        {"payload": {"text": "not a start event"}},
        {"payload": {"goal": "task", "decision_event_id": "d", "secret": "hidden"}},
    ],
)
def test_malformed_event_rejected(api, change):
    with pytest.raises(ValidationError):
        event(api, **change)


def test_event_and_nested_payload_are_immutable(api):
    proposal = event(api)
    with pytest.raises(ValidationError):
        proposal.local_seq = 2
    with pytest.raises(ValidationError):
        proposal.payload.goal = "changed"


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("participation.decided", {"decision": "ignore", "reason": "unrelated"}),
        ("progress.updated", {"text": "working"}),
        ("reply.delta", {"text": "piece"}),
        ("output.proposed", {"output_id": "o1", "target_ref": "group-1", "share_mode": "message", "text": "hello"}),
        (
            "model.completed",
            {
                "model_call_id": "m1",
                "config_version": "v1",
                "usage": {"status": "known", "input_tokens": 2, "output_tokens": 3},
            },
        ),
        (
            "tool.intent",
            {
                "operation_id": "op1",
                "tool_name": "read",
                "tool_version": "v1",
                "target_ref": "file-1",
                "params_digest": "b" * 64,
                "args_ref": "args-1",
            },
        ),
        ("tool.completed", {"operation_id": "op1", "status": "unknown", "reason": "timed out"}),
        ("checkpoint.proposed", {"checkpoint_ref": "cp1", "covered_request_event_seq": 7}),
        ("control.waiting", {"condition_ref": "approval-1", "recovery_ref": "r1", "reason": "waiting"}),
        ("execution.suspended", {"recovery_ref": "r1", "reason": "unknown commit"}),
        ("window.activated", {"window_ref": "w1", "window_version": "v2", "head_revision": "h2"}),
        ("execution.final", {"status": "completed", "output_refs": ["o1"]}),
    ],
)
def test_typed_payloads_roundtrip_without_claiming_delivery(api, kind, payload):
    proposal = event(api, event_type=kind, payload=payload)
    restored = api.AgentEvent.model_validate_json(proposal.model_dump_json())
    assert restored.model_dump(mode="json")["payload"] == proposal.model_dump(mode="json")["payload"]
    assert "delivered" not in restored.model_dump_json()


def test_output_file_needs_target_and_reference(api):
    with pytest.raises(ValidationError):
        event(
            api,
            event_type="output.proposed",
            payload={"output_id": "o1", "target_ref": "g1", "share_mode": "attachment"},
        )


def test_commit_confirmation_matches_proposal(api):
    proposal = event(api)
    result = receipt(api, proposal)
    result.assert_event(proposal)
    assert api.CommitResult.model_validate_json(result.model_dump_json()).request_event_seq == 7


@pytest.mark.parametrize(
    "change",
    [
        {"session_id": "wrong"},
        {"epoch": 2},
        {"event_id": "evt_" + "0" * 64},
        {"payload_digest": "0" * 64},
        {"local_seq": 2},
    ],
)
def test_wrong_commit_receipt_cannot_confirm_event(api, change):
    proposal = event(api)
    with pytest.raises(BaseError):
        receipt(api, proposal, **change).assert_event(proposal)


@pytest.mark.parametrize(
    "change",
    [
        {"request_event_seq": None},
        {"request_event_seq": 0},
        {"payload_digest": None},
        {"status": "unknown", "request_event_seq": 7, "reason": "timeout"},
        {"status": "rejected", "request_event_seq": None},
        {"status": "unknown", "request_event_seq": None},
        {"status": "not_found", "request_event_seq": None, "query_complete": False},
        {"status": "not_found", "request_event_seq": None, "query_complete": True},
    ],
)
def test_receipt_cannot_fabricate_confirmation_or_absence(api, change):
    with pytest.raises(ValidationError):
        receipt(api, event(api), **change)


def test_complete_not_found_is_query_only(api):
    proposal = event(api)
    missing = receipt(api, proposal, status="not_found", request_event_seq=None, query_complete=True, watermark=9)
    missing.assert_event(proposal, query=True)
    with pytest.raises(BaseError):
        missing.assert_event(proposal)


def test_unknown_and_conflict_are_not_confirmed(api):
    proposal = event(api)
    unknown = receipt(api, proposal, status="unknown", request_event_seq=None, payload_digest=None, reason="timeout")
    unknown.assert_event(proposal)
    conflict = receipt(
        api,
        proposal,
        status="conflict",
        request_event_seq=None,
        payload_digest="0" * 64,
        reason="same id, different payload",
    )
    conflict.assert_event(proposal)
    assert unknown.status == "unknown" and conflict.status == "conflict"


def test_authority_scope_can_only_shrink(api):
    allowed = api.AuthorityResult(**SCOPE, status="allowed", policy_revision="p2", scope={"read"})
    allowed.assert_execution(execution(api))
    expanded = api.AuthorityResult(**SCOPE, status="allowed", policy_revision="p2", scope={"admin"})
    with pytest.raises(BaseError):
        expanded.assert_execution(execution(api))
    with pytest.raises(ValidationError):
        api.AuthorityResult(**SCOPE, status="unknown")


def test_control_commands_are_bound_and_empty_requires_watermark(api):
    stop = api.ControlCommand(**SCOPE, control_id="c1", kind="stop")
    result = api.ControlResult(**SCOPE, status="available", commands=(stop,))
    result.assert_matches(execution(api))
    with pytest.raises(ValidationError):
        api.ControlResult(**SCOPE, status="empty")
    empty = api.ControlResult(**SCOPE, status="empty", query_complete=True, watermark=3)
    assert empty.commands == ()
    with pytest.raises(ValidationError):
        api.ControlResult(**SCOPE, status="available", commands=(stop, stop))
    with pytest.raises(ValidationError):
        api.ControlResult(**SCOPE, status="available", commands=(stop.model_copy(update={"session_id": "wrong"}),))


def test_approval_requires_interaction_reference(api):
    with pytest.raises(ValidationError):
        api.ControlCommand(**SCOPE, control_id="c1", kind="approve")


def test_unknown_usage_is_not_zero(api):
    unknown = api.ModelUsage(status="unknown", reason="usage unavailable")
    assert unknown.input_tokens is None and unknown.output_tokens is None
    with pytest.raises(ValidationError):
        api.ModelUsage(status="unknown", input_tokens=0, output_tokens=0, reason="unavailable")
    with pytest.raises(ValidationError):
        api.ModelUsage(status="known", input_tokens=-1, output_tokens=2)


def test_budget_receipts_bind_model_call_and_require_reservation(api):
    with pytest.raises(ValidationError):
        api.BudgetAdmission(**SCOPE, model_call_id="m1", status="admitted")
    admission = api.BudgetAdmission(**SCOPE, model_call_id="m1", status="admitted", reservation_ref="reservation-1")
    admission.assert_call(execution(api), "m1")
    with pytest.raises(BaseError):
        admission.assert_call(execution(api), "m2")
    report = api.BudgetReport(**SCOPE, model_call_id="m1", status="unknown", reason="report timeout")
    assert report.status == "unknown"


@pytest.mark.parametrize("change", [{"schema_version": True}, {"schema_version": 2}, {"event_id": "wrong"}])
def test_invalid_envelope_version_or_id_is_rejected(api, change):
    with pytest.raises(ValidationError):
        event(api, **change)


def test_equivalent_timezones_have_identical_proposal_digest(api):
    shifted = WHEN.astimezone(timezone(timedelta(hours=8)))
    assert event(api, occurred_at=shifted).proposal_digest() == event(api).proposal_digest()


def test_large_event_requires_references_instead_of_inline_content(api):
    with pytest.raises(ValidationError, match="256 KiB"):
        event(api, payload={"goal": "中" * 65536, "decision_event_id": "d"}, correlation_refs=("r" * 512,) * 128)


def test_event_rejects_invalid_utf8(api):
    with pytest.raises(ValidationError):
        event(api, payload={"goal": "bad\ud800", "decision_event_id": "d"})


def test_event_does_not_retain_mutable_payload_input(api):
    source = {"goal": "task", "decision_event_id": "d"}
    proposal = event(api, payload=source)
    source["goal"] = "changed"
    assert proposal.payload.goal == "task"


def test_reference_and_control_limits(api):
    with pytest.raises(ValidationError):
        event(api, correlation_refs=("r",) * 129)
    commands = tuple(api.ControlCommand(**SCOPE, control_id=f"c{i}", kind="stop") for i in range(65))
    with pytest.raises(ValidationError):
        api.ControlResult(**SCOPE, status="available", commands=commands)


def test_control_unknown_is_not_complete_absence(api):
    result = api.ControlResult(**SCOPE, status="unknown", reason="timeout")
    restored = api.ControlResult.model_validate_json(result.model_dump_json())
    assert restored.status == "unknown" and not restored.query_complete
    with pytest.raises(ValidationError):
        api.ControlCommand(**SCOPE, control_id="c1", kind="stop", approval_ref="approval")


def test_tool_success_requires_result_evidence(api):
    with pytest.raises(ValidationError):
        api.ToolCompletedPayload(operation_id="op1", status="succeeded")
    assert api.ToolCompletedPayload(operation_id="op1", status="succeeded", receipt_ref="receipt").receipt_ref


def test_budget_report_cannot_claim_success_without_receipt(api):
    with pytest.raises(ValidationError):
        api.BudgetReport(**SCOPE, model_call_id="m1", status="confirmed")
    report = api.BudgetReport(**SCOPE, model_call_id="m1", status="confirmed", receipt_ref="receipt")
    restored = api.BudgetReport.model_validate_json(report.model_dump_json())
    restored.assert_call(execution(api), "m1")


@pytest.mark.parametrize("text", [" ", "\n", "\t"])
def test_reply_delta_preserves_whitespace_chunks(api, text):
    proposal = event(api, event_type="reply.delta", payload={"text": text})
    restored = api.AgentEvent.model_validate_json(proposal.model_dump_json())
    assert restored.payload.text == text
