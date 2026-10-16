# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-23 host-boundary tests; no model, database or Feishu service."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.persistent_agent.schema.context import GroupMessageQuery
from openjiuwen.harness.persistent_agent.schema.execution import ExecutionContext
from openjiuwen.harness.persistent_agent.schema.protocol import InvocationRef

pytestmark = pytest.mark.level0
PKG = "openjiuwen.harness.persistent_agent"


@pytest.fixture
def execution():
    return ExecutionContext(
        namespace_id="tenant",
        agent_id="agent",
        session_id="private-session",
        request_id="request",
        execution_id="execution",
        source_ref="source",
        config_version="v1",
        config_fingerprint="a" * 64,
        principal_ref="not-a-member-of-group-b",
        startup_scope=frozenset({"read"}),
        source_scope=frozenset({"read"}),
        policy_revision="p1",
        capability_snapshot=frozenset({"construction"}),
    )


def message(number, **changes):
    return (
        dict(
            message_id=f"m{number}",
            sender_id="sender",
            sender_name="张三",
            sender_kind="human",
            received_at=datetime(2026, 10, 16, tzinfo=timezone.utc) + timedelta(seconds=number),
            text=f"决定 {number}",
        )
        | changes
    )


def response(execution, **fields):
    return {**InvocationRef.from_execution(execution).model_dump(), **fields}


def hit(number=1, group="group-b"):
    return dict(group_id=group, group_name="项目群", message_ref=f"{group}:m{number}", message=message(number))


def search_result_type():
    api = import_module(PKG)
    assert hasattr(api, "GroupMessageSearchResult"), "PA-23 search result is not implemented"
    return api.GroupMessageSearchResult


def search_tool(execution, host):
    name = f"{PKG}.tools.group_history"
    try:
        module = import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            pytest.fail("PA-23 search tool is not implemented")
        raise
    return module.PersistentGroupMessageSearchTool(execution=execution, group_chat=host)


def reader():
    name = f"{PKG}.execution.group_history"
    try:
        module = import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            pytest.fail("PA-23 group background reader is not implemented")
        raise
    return module.ExecutionGroupMessageReader()


class SearchHost:
    def __init__(self, execution, **fields):
        self.result = response(execution, status="found", hits=[hit()], query_complete=True)
        self.result.update(fields)
        self.calls = []

    async def search_messages(self, execution, query):
        self.calls.append((execution, query))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def test_search_filters_preserve_exact_values_and_reject_identity():
    query = GroupMessageQuery(query="决定", group_ids=["group-b"], group_names=["项目群"], limit=2)
    assert query.group_ids == ("group-b",) and query.group_names == ("项目群",)
    with pytest.raises(ValidationError):
        GroupMessageQuery(query="决定", agent_id="other")
    with pytest.raises(ValidationError):
        GroupMessageQuery(group_ids=["group-b"])
    with pytest.raises(ValidationError):
        GroupMessageQuery(query="决定", group_names=[" "])


@pytest.mark.parametrize(
    "fields",
    [
        dict(status="found", hits=[hit()], query_complete=False),
        dict(status="found", hits=[], query_complete=True),
        dict(status="empty", hits=[], query_complete=False),
        dict(status="empty", hits=[hit()], query_complete=True),
        dict(status="found", hits=[hit()], query_complete=True, next_cursor="next"),
        dict(status="incomplete", query_complete=False),
        dict(status="failed", query_complete=False, hits=[hit()], reason="failure"),
        dict(status="forbidden", query_complete=False, next_cursor="next", reason="unavailable"),
    ],
)
def test_search_result_rejects_misleading_completeness(execution, fields):
    result_type = search_result_type()
    with pytest.raises(ValidationError):
        result_type.model_validate(response(execution, **fields))


@pytest.mark.asyncio
async def test_cross_group_search_uses_bound_execution_and_renders_sources(execution):
    host = SearchHost(execution)
    tool = search_tool(execution, host)
    result = await tool.invoke({"query": "决定", "group_ids": ["group-b"], "limit": 2})
    shown = json.loads(tool.render_for_llm(result))
    assert result.success and shown["hits"][0]["group_id"] == "group-b"
    assert shown["hits"][0]["message_ref"] == "group-b:m1"
    assert shown["hits"][0]["message"]["text"] == "决定 1"
    assert host.calls[0][0] is execution
    assert host.calls[0][1].group_ids == ("group-b",)
    assert "principal_ref" not in shown and "execution_id" not in shown
    assert "agent_id" not in tool.card.input_params["properties"]


@pytest.mark.asyncio
async def test_invalid_input_never_contacts_host(execution):
    host = SearchHost(execution)
    tool = search_tool(execution, host)
    for args in ({"query": "决定", "agent_id": "other"}, {"query": "决定", "limit": True}, {}):
        result = await tool.invoke(args)
        assert not result.success and result.data["status"] == "invalid_arguments"
    assert not host.calls


@pytest.mark.asyncio
async def test_each_page_calls_host_again_and_preserves_revocation(execution):
    host = SearchHost(execution, status="incomplete", query_complete=False, next_cursor="page-2", reason="more pages")
    tool = search_tool(execution, host)
    first = await tool.invoke({"query": "决定"})
    assert first.data["status"] == "incomplete" and first.data["next_cursor"] == "page-2"
    host.result = response(execution, status="unavailable", query_complete=False, reason="target unavailable")
    second = await tool.invoke({"query": "决定", "cursor": "page-2"})
    assert second.data["status"] == "unavailable" and second.data["hits"] == []
    assert len(host.calls) == 2 and host.calls[1][1].cursor == "page-2"
    assert "unavailable" in tool.render_for_llm(second)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["exception", "identity", "legacy", "too_many"])
async def test_failed_or_invalid_host_result_never_becomes_empty(execution, bad):
    host = SearchHost(execution)
    if bad == "exception":
        host.result = RuntimeError("secret database path")
    elif bad == "identity":
        host.result["execution_id"] = "other"
    elif bad == "legacy":
        host.result = response(execution, status="empty", messages=[])
    else:
        host.result["hits"] = [hit(1), hit(2)]
    tool = search_tool(execution, host)
    output = await tool.invoke({"query": "决定", "limit": 1})
    assert not output.success and output.data["status"] == "failed"
    assert "secret" not in tool.render_for_llm(output)


@pytest.mark.asyncio
async def test_search_cancellation_propagates(execution):
    host = SearchHost(execution)
    host.result = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await search_tool(execution, host).invoke({"query": "决定"})


@pytest.mark.asyncio
async def test_search_excerpts_and_omissions_are_explicit(execution):
    hits = [hit(i) for i in range(50)]
    for item in hits:
        item["message"]["text"] = "文" * 65536
    host = SearchHost(execution, hits=hits)
    tool = search_tool(execution, host)
    result = await tool.invoke({"query": "文"})
    shown = json.loads(tool.render_for_llm(result))
    assert shown["display_truncated"] is True and shown["omitted_hits"] > 0
    assert shown["hits"][0]["text_truncated"] is True
    assert len(shown["hits"][0]["message"]["text"]) <= 2048
    assert len(tool.render_for_llm(result)) <= 32768


class RecentHost:
    def __init__(self, execution, pages):
        self.execution, self.pages, self.calls = execution, pages, []

    async def recent_messages(self, execution, cursor=None, limit=20):
        assert execution is self.execution
        self.calls.append(cursor)
        page = self.pages[cursor]
        if isinstance(page, BaseException):
            raise page
        return response(execution, **page)


async def read_background(execution, pages, after="m180", trigger="m120"):
    host = RecentHost(execution, pages)
    context = SimpleNamespace(execution=execution, ports=SimpleNamespace(group_chat=host))
    result = await reader().recent_messages(context, after=after, trigger_message_id=trigger)
    return result, host


@pytest.mark.asyncio
async def test_reader_backfills_queued_trigger_and_deduplicates(execution):
    result, host = await read_background(
        execution,
        {
            "m180": dict(status="found", messages=[message(190)], scanned_through="m190"),
            "m120": dict(
                status="found",
                messages=[message(120), message(130), message(180), message(190), message(200)],
                scanned_through="m200",
            ),
        },
    )
    assert [item.message_id for item in result.messages] == ["m130", "m180", "m190"]
    assert result.status == "found" and result.scanned_through == "m190"
    assert host.calls == ["m180", "m120"]


@pytest.mark.asyncio
async def test_reader_follows_backfill_pages_to_fixed_boundary(execution):
    result, host = await read_background(
        execution,
        {
            "m180": dict(status="found", messages=[message(190)], scanned_through="m190"),
            "m120": dict(status="found", messages=[message(130)], next_cursor="page2", scanned_through="m130"),
            "page2": dict(status="found", messages=[message(180), message(190)], scanned_through="m190"),
        },
    )
    assert result.status == "found" and [m.message_id for m in result.messages] == ["m130", "m180", "m190"]
    assert host.calls == ["m180", "m120", "page2"]


@pytest.mark.asyncio
async def test_reader_does_not_turn_missing_boundary_into_complete_history(execution):
    result, _ = await read_background(
        execution,
        {
            "m180": dict(status="empty", scanned_through="m190"),
            "m120": dict(status="found", messages=[message(200)], scanned_through="m200"),
        },
    )
    assert result.status == "incomplete" and not result.messages


@pytest.mark.asyncio
async def test_reader_does_not_advance_untrusted_scan(execution):
    result, host = await read_background(
        execution,
        {
            "m180": dict(status="incomplete", reason="store unavailable"),
        },
    )
    assert result.status == "incomplete" and result.scanned_through is None
    assert host.calls == ["m180"]


@pytest.mark.asyncio
async def test_reader_propagates_backend_failure(execution):
    with pytest.raises(RuntimeError, match="offline"):
        await read_background(execution, {"m180": RuntimeError("offline")})


@pytest.mark.asyncio
async def test_reader_detects_repeated_cursor(execution):
    result, host = await read_background(
        execution,
        {
            "m180": dict(status="found", messages=[message(190)], scanned_through="m190"),
            "m120": dict(status="found", messages=[message(130)], next_cursor="m120", scanned_through="m130"),
        },
    )
    assert result.status == "incomplete" and len(host.calls) == 2


@pytest.mark.asyncio
async def test_reader_newer_recall_replaces_delta_body(execution):
    result, _ = await read_background(
        execution,
        {
            "m180": dict(status="found", messages=[message(190)], scanned_through="m190"),
            "m120": dict(status="found", messages=[message(190, status="recalled", text=None)], scanned_through="m190"),
        },
    )
    assert result.messages[0].status == "recalled" and result.messages[0].text is None


@pytest.mark.asyncio
async def test_reader_backfill_is_bounded(execution):
    pages = {"m180": dict(status="found", messages=[message(190)], scanned_through="m190")}
    for index in range(40):
        cursor = "m120" if index == 0 else f"page{index}"
        pages[cursor] = dict(status="found", messages=[message(index)], next_cursor=f"page{index + 1}")
    result, host = await read_background(execution, pages)
    assert len(host.calls) == 33 and result.status == "incomplete"
    assert [item.message_id for item in result.messages] == ["m190"]


@pytest.mark.asyncio
async def test_reader_delta_paging_does_not_claim_completeness(execution):
    result, host = await read_background(
        execution,
        {
            "m180": dict(status="found", messages=[message(190)], scanned_through="m190", next_cursor="page2"),
        },
        trigger=None,
    )
    assert result.status == "incomplete" and result.next_cursor == "page2"
    assert result.scanned_through == "m190" and host.calls == ["m180"]


@pytest.mark.asyncio
async def test_search_empty_is_distinct_from_unavailable(execution):
    host = SearchHost(execution, status="empty", hits=[])
    output = await search_tool(execution, host).invoke({"sender_id": "sender"})
    assert output.success and output.data["query_complete"] and output.data["status"] == "empty"


@pytest.mark.asyncio
async def test_search_rejects_recalled_message_body(execution):
    item = hit()
    item["message"]["status"] = "recalled"
    host = SearchHost(execution, hits=[item])
    output = await search_tool(execution, host).invoke({"query": "决定"})
    assert output.data["status"] == "failed" and not output.data["hits"]


@pytest.mark.asyncio
async def test_reader_rejects_wrong_execution(execution):
    with pytest.raises(BaseError, match="protocol invocation mismatch"):
        await read_background(
            execution,
            {
                "m180": dict(status="empty", execution_id="other"),
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["recalled", "unavailable"])
async def test_incomplete_backfill_cannot_restore_a_known_hidden_body(execution, status):
    result, _ = await read_background(
        execution,
        {
            "m180": dict(status="found", messages=[message(185), message(190)], scanned_through="m190"),
            "m120": dict(status="found", messages=[message(185, status=status, text=None)], next_cursor="page2"),
            "page2": dict(status="incomplete", reason="scan interrupted"),
        },
    )
    assert result.status == "incomplete"
    observed = next(item for item in result.messages if item.message_id == "m185")
    assert observed.status == status and observed.text is None


@pytest.mark.asyncio
async def test_single_large_metadata_hit_remains_readable(execution):
    item = hit()
    item["message"]["attachment_refs"] = ["a" * 510 + str(index) for index in range(65)]
    host = SearchHost(execution, hits=[item])
    tool = search_tool(execution, host)
    output = await tool.invoke({"query": "决定", "limit": 1})
    shown = json.loads(tool.render_for_llm(output))
    assert shown["hits"][0]["message_ref"] == item["message_ref"]
    assert shown["hits"][0]["message"]["text"] == "决定 1"
    assert shown["hits"][0]["metadata_truncated"] is True
    assert shown["display_truncated"] is True and shown["omitted_hits"] == 0
    assert len(tool.render_for_llm(output)) <= 32768


@pytest.mark.asyncio
async def test_escaped_text_and_reason_still_fit_with_one_source(execution):
    item = hit()
    item["group_name"] = "\x01" * 512
    item["message"]["sender_name"] = "\x01" * 512
    item["message"]["text"] = "\x01" * 65536
    item["message"]["mention_ids"] = ["a" * 508 + str(index) for index in range(128)]
    item["message"]["attachment_refs"] = list(item["message"]["mention_ids"])
    host = SearchHost(execution, hits=[item], status="incomplete", query_complete=False, reason="x" + "\x01" * 4095)
    tool = search_tool(execution, host)
    output = await tool.invoke({"query": "决定", "limit": 1})
    shown = json.loads(tool.render_for_llm(output))
    assert output.success and shown["hits"][0]["message_ref"] == item["message_ref"]
    assert shown["reason_truncated"] is True and shown["hits"][0]["metadata_truncated"] is True
    assert len(tool.render_for_llm(output)) <= 32768
