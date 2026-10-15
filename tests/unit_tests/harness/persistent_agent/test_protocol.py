# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Protocol boundary tests, without a model, persistence backend, or Feishu."""

import inspect
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
)


@pytest.fixture
def api():
    return import_module("openjiuwen.harness.persistent_agent")


def execution(api, **changes):
    args = dict(
        **SCOPE,
        config_version="v1",
        config_fingerprint="a" * 64,
        principal_ref="user",
        startup_scope=frozenset({"read", "write"}),
        source_scope=frozenset({"group"}),
        policy_revision="p1",
        capability_snapshot=frozenset({"construction"}),
    )
    args.update(changes)
    return api.ExecutionContext(**args)


def event(api, **changes):
    args = dict(
        event_type="execution.started",
        local_seq=1,
        occurred_at=WHEN,
        payload={"goal": "task", "decision_event_id": "decision-1"},
    )
    args.update(changes)
    return api.AgentEvent.create(execution(api), **args)


def message(api, **changes):
    args = dict(
        message_id="m1",
        sender_id="u1",
        sender_name="张三",
        sender_kind="human",
        received_at=WHEN,
        text="整理发票",
    )
    args.update(changes)
    return api.GroupMessage(**args)


INTERCEPTED = {"tool_call_id": "call-1", "tool_name": "submit_expense", "arguments": "单号=BX-0930"}
FILE_OUTPUT = {
    "output_id": "o2",
    "target_ref": "group-1",
    "share_mode": "upload",
    "file_ref": "sha256:" + "a" * 64,
    "file_path": "reports/q3.md",
    "file_size": 13,
    "text": "第三季度报告",
}


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
    proposal = event(api)
    with pytest.raises(BaseError):
        proposal.assert_matches(replace(execution(api), source_ref="source:message-2"))


@pytest.mark.parametrize("removed", ["epoch", "authority_handle"])
def test_execution_context_has_no_lease_fields(api, removed):
    with pytest.raises(TypeError):
        execution(api, **{removed: 1 if removed == "epoch" else "lease"})
    assert removed not in api.InvocationRef.model_fields


def test_recovery_ref_names_an_earlier_execution(api):
    assert execution(api, recovery_ref="execution-0").recovery_ref == "execution-0"
    for bad in (" ", "execution"):
        with pytest.raises(BaseError):
            execution(api, recovery_ref=bad)


def test_knowledge_material_keeps_unknown_distinct_from_empty(api):
    snapshot = api.MaterialSnapshot(
        material_ref="material-1",
        source_ref="knowledge:policy",
        scope_ref="agent",
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


def test_group_results_distinguish_found_empty_and_incomplete(api):
    found = api.GroupMessagesResult(**SCOPE, status="found", messages=(message(api),), next_cursor="m1")
    empty = api.GroupMessagesResult(**SCOPE, status="empty")
    partial = api.GroupMessagesResult(
        **SCOPE, status="incomplete", messages=(message(api),), reason="search index lagging"
    )
    found.assert_matches(execution(api))
    restored = api.GroupMessagesResult.model_validate_json(partial.model_dump_json())
    assert restored.status == "incomplete" and len(restored.messages) == 1
    assert empty.messages == ()
    for bad in (
        {"status": "found"},
        {"status": "empty", "messages": (message(api),)},
        {"status": "empty", "next_cursor": "m9"},
        {"status": "incomplete"},
    ):
        with pytest.raises(ValidationError):
            api.GroupMessagesResult(**SCOPE, **bad)
    with pytest.raises(BaseError):
        found.assert_matches(replace(execution(api), session_id="other-session"))


def test_group_results_report_trusted_scan_position_apart_from_paging(api):
    found = api.GroupMessagesResult(
        **SCOPE, status="found", messages=(message(api),), next_cursor="m1", scanned_through="m5"
    )
    assert (found.next_cursor, found.scanned_through) == ("m1", "m5")
    nothing_new = api.GroupMessagesResult(**SCOPE, status="empty", scanned_through="m5")
    assert nothing_new.scanned_through == "m5", "a confirmed scan with no new messages still moves the cursor"
    partial = api.GroupMessagesResult(
        **SCOPE, status="incomplete", reason="body store unavailable", scanned_through="m5"
    )
    untrusted = api.GroupMessagesResult(**SCOPE, status="incomplete", reason="query timed out")
    assert partial.scanned_through == "m5" and untrusted.scanned_through is None
    assert api.GroupMessagesResult.model_validate_json(partial.model_dump_json()) == partial
    with pytest.raises(ValidationError):
        api.GroupMessagesResult(**SCOPE, status="empty", scanned_through="has space")


def test_group_message_marks_an_unreadable_body_apart_from_recall(api):
    unavailable = message(api, status="unavailable", text=None)
    assert unavailable.status == "unavailable" and unavailable.text is None
    with pytest.raises(ValidationError):
        message(api, status="unavailable")


def test_group_chat_recent_messages_signature_is_unchanged(api):
    parameters = inspect.signature(api.GroupChatPort.recent_messages).parameters
    assert list(parameters) == ["self", "execution", "cursor", "limit"], "backfill reads after the trigger as cursor"


def test_group_message_hides_recalled_text_and_normalizes_time(api):
    shifted = message(api, received_at=WHEN.astimezone(timezone(timedelta(hours=8))))
    assert shifted.received_at == WHEN and shifted.received_at.tzinfo == timezone.utc
    recalled = message(api, status="recalled", text=None)
    assert recalled.text is None
    with pytest.raises(ValidationError):
        message(api, status="recalled")
    with pytest.raises(ValidationError):
        message(api, sender_kind="bot")
    with pytest.raises(ValidationError):
        message(api, queued_request="yes")


def test_group_search_requires_a_criterion_and_ordered_range(api):
    assert api.GroupMessageQuery(query="审批进度").limit == 50
    assert api.GroupMessageQuery(sender_id="u1").query is None
    with pytest.raises(ValidationError):
        api.GroupMessageQuery()
    with pytest.raises(ValidationError):
        api.GroupMessageQuery(query="x", since=WHEN, until=WHEN - timedelta(days=1))
    with pytest.raises(ValidationError):
        api.GroupMessageQuery(query="x", limit=501)


def test_retransmission_is_same_proposal_but_changed_content_conflicts(api):
    proposal = event(api)
    proposal.assert_same_proposal(event(api))
    changed = event(api, payload={"goal": "other", "decision_event_id": "decision-1"})
    assert changed.event_id == proposal.event_id
    with pytest.raises(BaseError):
        proposal.assert_same_proposal(changed)


@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace_id", "tenant-2"),
        ("session_id", "session-2"),
        ("request_id", "request-2"),
        ("execution_id", "execution-2"),
    ],
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
        {"payload": {"decision_event_id": "d"}},
        {"payload": {"text": "not a start event"}},
        {"payload": {"goal": "task", "decision_event_id": "d", "secret": "hidden"}},
    ],
)
def test_malformed_event_rejected(api, change):
    with pytest.raises(ValidationError):
        event(api, **change)


@pytest.mark.parametrize(
    "removed",
    ["checkpoint.proposed", "control.waiting", "execution.suspended", "window.activated"],
)
def test_event_types_removed_by_v2_are_rejected(api, removed):
    assert len(api.EventType) == 11
    with pytest.raises(ValidationError):
        event(api, event_type=removed, payload={"reason": "old protocol"})


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
        ("execution.started", {"goal": "private task"}),
        ("progress.updated", {"text": "working"}),
        ("reply.delta", {"text": "piece"}),
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
                "tool_call_id": "call-1",
                "tool_name": "write_file",
                "target_ref": "reports/0930.xlsx",
                "params_digest": "b" * 64,
                "args_summary": "path=reports/0930.xlsx",
            },
        ),
        (
            "tool.completed",
            {
                "tool_call_id": "call-1",
                "tool_name": "write_file",
                "args_summary": "path=reports/0930.xlsx",
                "status": "unknown",
                "result_summary": "写入超时，实际文件状态需查证",
                "operation_id": "op1",
                "reason": "timed out",
            },
        ),
        ("window.switched", {"archive_ref": "archive-7", "window_seq": 3}),
        ("output.proposed", {"output_id": "o1", "target_ref": "group-1", "share_mode": "message", "text": "hello"}),
        ("output.proposed", FILE_OUTPUT),
        ("execution.paused", {"reason": "awaiting_confirmation", "intercepted": [INTERCEPTED]}),
        ("execution.paused", {"reason": "error", "detail": "model failed", "output_refs": ["o1"]}),
        (
            "execution.paused",
            {"reason": "awaiting_confirmation", "intercepted": [{**INTERCEPTED, "arguments_truncated": True}]},
        ),
        ("execution.paused", {"reason": "stopped"}),
        ("execution.final", {"status": "completed", "output_refs": ["o1"]}),
        ("execution.final", {"status": "failed", "reason": "task cannot be completed"}),
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


@pytest.mark.parametrize(
    "change",
    [
        {"file_ref": None},
        {"file_path": None},
        {"file_size": None},
        {"file_ref": "a" * 64},
        {"file_ref": "sha256:" + "A" * 64},
        {"file_size": -1},
        {"file_path": "/reports/q3.md"},
        {"file_path": "reports/../q3.md"},
        {"file_path": "reports//q3.md"},
        {"file_path": "./q3.md"},
        {"file_path": "reports\\q3.md"},
        {"file_path": "reports/q3\x01.md"},
        {"file_path": "r" * 1025},
        {"text": "x" * 4097},
    ],
)
def test_file_outputs_carry_a_digest_a_logical_path_and_a_size(api, change):
    """PA-08-V-03: what the host reads, checks and shows."""
    with pytest.raises(ValidationError):
        api.OutputPayload.model_validate({**FILE_OUTPUT, **change})


def test_file_output_size_has_no_schema_limit_and_messages_carry_no_file(api):
    """The size limit is a runtime setting shared with the host, not part of the event schema."""
    assert api.OutputPayload.model_validate({**FILE_OUTPUT, "file_size": 10**15}).file_size == 10**15
    assert api.OutputPayload.model_validate({**FILE_OUTPUT, "text": "x" * 4096}).text == "x" * 4096
    message = {"output_id": "o1", "target_ref": "g1", "share_mode": "message", "text": "hello"}
    for field in ("file_ref", "file_path", "file_size"):
        with pytest.raises(ValidationError):
            api.OutputPayload.model_validate({**message, field: FILE_OUTPUT[field]})
    link = {"output_id": "o1", "target_ref": "g1", "share_mode": "link", "file_ref": "ref-1"}
    assert api.OutputPayload.model_validate(link).file_path is None, "link keeps its controlled reference"


@pytest.mark.parametrize("reason", ["awaiting_confirmation", "stopped", "budget_denied", "context_unfit", "error"])
def test_pause_reasons_are_the_fixed_five(api, reason):
    payload = {"reason": reason}
    if reason == "awaiting_confirmation":
        payload["intercepted"] = [INTERCEPTED]
    elif reason != "stopped":
        payload["detail"] = "condition must recover first"
    assert event(api, event_type="execution.paused", payload=payload).payload.reason == reason


@pytest.mark.parametrize(
    "payload",
    [
        {"reason": "waiting"},
        {"reason": "awaiting_confirmation"},
        {"reason": "stopped", "intercepted": [INTERCEPTED]},
        {"reason": "budget_denied"},
        {"reason": "context_unfit"},
        {"reason": "error"},
        {"reason": "awaiting_confirmation", "intercepted": [INTERCEPTED, INTERCEPTED]},
        {"reason": "awaiting_confirmation", "intercepted": [{**INTERCEPTED, "arguments": " "}]},
        {"reason": "stopped", "output_refs": ["o1"]},
        {"reason": "error", "detail": "failed", "output_refs": ["o1"] * 129},
    ],
)
def test_pause_payload_rules(api, payload):
    with pytest.raises(ValidationError):
        api.PausedPayload.model_validate(payload)


def test_parallel_intercepted_calls_are_kept_per_tool_call(api):
    second = {**INTERCEPTED, "tool_call_id": "call-2", "tool_name": "send_mail"}
    paused = api.PausedPayload(reason="awaiting_confirmation", intercepted=[INTERCEPTED, second])
    assert [call.tool_call_id for call in paused.intercepted] == ["call-1", "call-2"]
    assert isinstance(paused.intercepted[0], api.InterceptedToolCall)


def test_final_uses_failed_not_failed_final(api):
    with pytest.raises(ValidationError):
        api.FinalPayload(status="failed-final", reason="old name")
    with pytest.raises(ValidationError):
        api.FinalPayload(status="failed")
    assert api.FinalPayload(status="failed", reason="cannot finish").status == "failed"


def test_tool_intent_is_redacted_summary_without_old_recovery_fields(api):
    intent = dict(
        operation_id="op1",
        tool_call_id="call-1",
        tool_name="submit_expense",
        params_digest="b" * 64,
        args_summary="单号=BX-0930, token=***",
    )
    assert api.ToolIntentPayload(**intent).target_ref is None
    for old in ({"tool_version": "v1"}, {"args_ref": "args-1"}, {"authorization_ref": "a1"}):
        with pytest.raises(ValidationError):
            api.ToolIntentPayload(**intent, **old)
    with pytest.raises(ValidationError):
        api.ToolIntentPayload(**{**intent, "args_summary": " "})


def test_tool_success_requires_a_summary_even_with_references(api):
    done = dict(tool_call_id="call-1", tool_name="read_file", args_summary="path=a.txt", status="succeeded")
    with pytest.raises(ValidationError):
        api.ToolCompletedPayload(**done)
    read = api.ToolCompletedPayload(**done, result_summary="12 行")
    assert read.operation_id is None and read.result_summary == "12 行"
    with pytest.raises(ValidationError):
        api.ToolCompletedPayload(**done, result_refs=("blob-1",))
    referenced = api.ToolCompletedPayload(**done, result_summary="12 行", result_refs=("blob-1",))
    assert referenced.result_refs == ("blob-1",) and referenced.result_summary == "12 行"
    with pytest.raises(ValidationError):
        api.ToolCompletedPayload(**done, result_summary="12 行", receipt_ref="receipt")
    with pytest.raises(ValidationError):
        api.ToolCompletedPayload(**{**done, "status": "failed"}, result_summary="读取失败")


@pytest.mark.parametrize("status", ["succeeded", "failed", "unknown"])
@pytest.mark.parametrize("summary", [None, "", " \n\t"])
def test_all_tool_outcomes_reject_empty_summaries_even_with_references(api, status, summary):
    payload = dict(
        tool_call_id="call-1",
        tool_name="read_file",
        args_summary="path=a.txt",
        status=status,
        result_summary=summary,
        result_refs=("blob-1",),
        reason="读取超时" if status != "succeeded" else None,
    )
    with pytest.raises(ValidationError):
        api.ToolCompletedPayload(**payload)


@pytest.mark.parametrize("status", ["succeeded", "failed", "unknown"])
def test_all_tool_outcomes_require_and_roundtrip_a_readable_summary(api, status):
    payload = dict(
        tool_call_id="call-1",
        tool_name="read_file",
        args_summary="path=a.txt",
        status=status,
        result_refs=("blob-1",),
        reason="读取超时" if status != "succeeded" else None,
    )
    with pytest.raises(ValidationError):
        api.ToolCompletedPayload(**payload)
    outcome = api.ToolCompletedPayload(
        **payload, result_summary="读取 12 行" if status == "succeeded" else "读取未完成"
    )
    restored = api.ToolCompletedPayload.model_validate_json(outcome.model_dump_json())
    assert restored.result_summary == outcome.result_summary
    assert restored.status == status


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
    denied = api.BudgetAdmission(**SCOPE, model_call_id="m1", status="denied", reason="group budget exhausted")
    assert denied.reservation_ref is None


@pytest.mark.parametrize("model", ["BudgetAdmission", "BudgetReport"])
def test_budget_results_have_no_unknown_status(api, model):
    with pytest.raises(ValidationError):
        getattr(api, model)(**SCOPE, model_call_id="m1", status="unknown", reason="timeout")


def test_budget_report_cannot_claim_success_without_receipt(api):
    with pytest.raises(ValidationError):
        api.BudgetReport(**SCOPE, model_call_id="m1", status="confirmed")
    report = api.BudgetReport(**SCOPE, model_call_id="m1", status="confirmed", receipt_ref="receipt")
    restored = api.BudgetReport.model_validate_json(report.model_dump_json())
    restored.assert_call(execution(api), "m1")
    assert api.BudgetReport(**SCOPE, model_call_id="m1", status="rejected", reason="unknown call").reason


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


def test_reference_limits(api):
    with pytest.raises(ValidationError):
        event(api, correlation_refs=("r",) * 129)


@pytest.mark.parametrize("text", [" ", "\n", "\t"])
def test_reply_delta_preserves_whitespace_chunks(api, text):
    proposal = event(api, event_type="reply.delta", payload={"text": text})
    restored = api.AgentEvent.model_validate_json(proposal.model_dump_json())
    assert restored.payload.text == text


@pytest.mark.parametrize(
    "name",
    [
        "ContextStore",
        "HistoryPort",
        "GroupMaterialPort",
        "WindowHead",
        "WindowMutationResult",
        "CheckpointPayload",
        "WaitingPayload",
        "SuspendedPayload",
        "WindowPayload",
        "CommitResult",
        "AuthorityResult",
        "ControlCommand",
        "ControlResult",
    ],
)
def test_contracts_removed_by_v2_are_not_exported(api, name):
    assert name not in api.__all__
    with pytest.raises(AttributeError):
        getattr(api, name)
