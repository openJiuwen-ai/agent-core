# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-12 search of archived Session windows."""

import importlib
import json
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.store import InMemoryKVStore

pytestmark = pytest.mark.level0

PKG = "openjiuwen.harness.persistent_agent"
STATE = importlib.import_module(f"{PKG}.execution.session_state")
RECORD = importlib.import_module(f"{PKG}.schema.session_record")


def archive_payload(window_seq, execution_id, messages):
    return {
        "old_window_id": f"window-{window_seq}",
        "old_window_seq": window_seq,
        "new_window_id": f"window-{window_seq + 1}",
        "new_window_seq": window_seq + 1,
        "trigger_kind": "agent",
        "request_id": f"request-{window_seq}",
        "execution_id": execution_id,
        "spec_fingerprint": "a" * 64,
        "seed": {"objective": "continue the task"},
        "retained_item_refs": [],
        "token_usage": {"input_tokens": 100},
        "messages": RECORD.encode_messages(messages),
        "candidate_fingerprint": "c" * 64,
        "iteration_seq": 1,
        "last_group_message_id": None,
    }


async def add_archive(store, session_id, window_seq, execution_id, messages):
    return await store.archive_window("agent-1", session_id, archive_payload(window_seq, execution_id, messages))


@pytest.mark.asyncio
async def test_search_finds_visible_messages_tool_calls_and_tool_results_but_not_hidden_fields():
    backend = InMemoryKVStore()
    store = STATE.WindowStore(backend)
    await add_archive(
        store,
        "session-1",
        1,
        "execution-1",
        [
            SystemMessage(content="private-system-marker"),
            UserMessage(content=[{"type": "text", "text": "Needle from user"}]),
            AssistantMessage(
                content="Needle in visible reply",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        type="function",
                        name="needle_tool",
                        arguments='{"value": "Needle argument"}',
                    )
                ],
                reasoning_content="private-reasoning-marker",
            ),
            ToolMessage(tool_call_id="call-1", name="needle_tool", content="Needle tool result"),
        ],
    )

    result = await store.search("agent-1", "session-1", "needle")

    assert result.status == "found" and result.query_complete
    assert {(hit.role, hit.matched_field) for hit in result.hits} == {
        ("user", "content[0].text"),
        ("assistant", "content"),
        ("assistant", "tool_calls[0].function.name"),
        ("assistant", "tool_calls[0].function.arguments"),
        ("tool", "tool_result.name"),
        ("tool", "tool_result.content"),
    }
    tool_hit = next(hit for hit in result.hits if hit.role == "tool" and hit.matched_field == "tool_result.content")
    assert tool_hit.tool_name == "needle_tool" and tool_hit.excerpt == "Needle tool result"

    hidden = await store.search("agent-1", "session-1", "private-")
    assert hidden.status == "empty" and hidden.query_complete and not hidden.hits


@pytest.mark.asyncio
async def test_search_is_case_insensitive_newest_first_and_marks_display_truncation():
    store = STATE.WindowStore(InMemoryKVStore())
    await add_archive(store, "session-1", 1, "execution-1", [UserMessage(content="needle older")])
    await add_archive(store, "session-1", 2, "execution-2", [UserMessage(content="NEEDLE newer")])

    result = await store.search("agent-1", "session-1", "Needle", limit=1)

    assert result.status == "found" and result.query_complete
    assert result.results_truncated
    assert [(hit.old_window_seq, hit.excerpt) for hit in result.hits] == [(2, "NEEDLE newer")]


@pytest.mark.asyncio
async def test_search_reports_empty_only_after_a_complete_scan_of_retained_archives():
    store = STATE.WindowStore(InMemoryKVStore())

    result = await store.search("agent-1", "session-with-no-archives", "missing")

    assert result.status == "empty" and result.query_complete and result.hits == []


@pytest.mark.asyncio
async def test_search_excerpt_is_bounded_and_still_contains_the_match():
    store = STATE.WindowStore(InMemoryKVStore())
    long_text = "x" * 3000 + "Needle" + "y" * 3000
    await add_archive(store, "session-1", 1, "execution-1", [UserMessage(content=long_text)])

    result = await store.search("agent-1", "session-1", "needle")

    assert result.status == "found" and len(result.hits) == 1
    assert len(result.hits[0].excerpt) == 2048
    assert "Needle" in result.hits[0].excerpt and result.hits[0].excerpt_truncated


@pytest.mark.asyncio
@pytest.mark.parametrize("broken_index", ["missing", "corrupt"])
async def test_search_rebuilds_a_missing_or_corrupt_index_from_archives_before_returning_hits(broken_index):
    backend = InMemoryKVStore()
    store = STATE.WindowStore(backend)
    await add_archive(store, "session-1", 1, "execution-1", [UserMessage(content="needle survives")])
    index_key = STATE.WindowStore.archive_index_key("agent-1", "session-1")
    if broken_index == "missing":
        await backend.delete(index_key)
    else:
        await backend.set(index_key, "not-json")

    result = await store.search("agent-1", "session-1", "needle")

    assert result.status == "found" and result.query_complete
    rebuilt = json.loads(await backend.get(index_key))
    assert [entry["archive_ref"] for entry in rebuilt["entries"]] == ["archive:1"]


@pytest.mark.asyncio
async def test_search_returns_only_the_exact_agent_and_session_archives():
    backend = InMemoryKVStore()
    store = STATE.WindowStore(backend)
    await add_archive(store, "session-1", 1, "execution-1", [UserMessage(content="needle in scope")])
    await add_archive(store, "session-2", 1, "execution-2", [UserMessage(content="needle other session")])
    await store.archive_window(
        "agent-2", "session-1", archive_payload(1, "execution-3", [UserMessage(content="needle other agent")])
    )

    result = await store.search("agent-1", "session-1", "needle")

    assert result.status == "found" and result.query_complete
    assert [(hit.old_window_seq, hit.excerpt) for hit in result.hits] == [(1, "needle in scope")]


@pytest.mark.asyncio
async def test_search_returns_partial_hits_as_incomplete_when_an_archive_is_corrupt():
    backend = InMemoryKVStore()
    store = STATE.WindowStore(backend)
    await add_archive(store, "session-1", 1, "execution-1", [UserMessage(content="needle readable")])
    await add_archive(store, "session-1", 2, "execution-2", [UserMessage(content="other content")])
    await backend.set(STATE.WindowStore.archive_key("agent-1", "session-1", 2), "not-json")

    result = await store.search("agent-1", "session-1", "needle")

    assert result.status == "incomplete" and not result.query_complete
    assert [hit.excerpt for hit in result.hits] == ["needle readable"]
    assert result.reason


@pytest.mark.asyncio
async def test_search_does_not_treat_an_index_reference_to_a_missing_archive_as_empty():
    backend = InMemoryKVStore()
    store = STATE.WindowStore(backend)
    await add_archive(store, "session-1", 1, "execution-1", [UserMessage(content="unrelated")])
    await add_archive(store, "session-1", 2, "execution-2", [UserMessage(content="also unrelated")])
    await backend.delete(STATE.WindowStore.archive_key("agent-1", "session-1", 2))

    result = await store.search("agent-1", "session-1", "missing")

    assert result.status == "incomplete" and not result.query_complete
    assert result.reason and not result.hits


@pytest.mark.asyncio
async def test_search_reports_failed_when_the_archive_listing_cannot_be_read():
    class FailingListingStore(InMemoryKVStore):
        fail_listing = False

        async def get_by_prefix(self, prefix):
            if self.fail_listing:
                raise RuntimeError("private storage path")
            return await super().get_by_prefix(prefix)

    backend = FailingListingStore()
    store = STATE.WindowStore(backend)
    await add_archive(store, "session-1", 1, "execution-1", [UserMessage(content="needle")])
    backend.fail_listing = True

    result = await store.search("agent-1", "session-1", "needle")

    assert result.status == "failed" and not result.query_complete and not result.hits
    assert result.reason and "private storage path" not in result.reason


@pytest.mark.asyncio
async def test_list_archives_can_filter_by_the_execution_that_triggered_rollover():
    store = STATE.WindowStore(InMemoryKVStore())
    await add_archive(store, "session-1", 1, "execution-1", [UserMessage(content="older")])
    await add_archive(store, "session-1", 2, "execution-2", [UserMessage(content="newer")])

    entries = await store.list_archives("agent-1", "session-1", execution_id="execution-2")

    assert [(entry.archive_ref, entry.execution_id, entry.old_window_id) for entry in entries] == [
        ("archive:2", "execution-2", "window-2")
    ]


@pytest.mark.asyncio
async def test_search_tool_uses_bound_session_and_keeps_completeness_visible_to_the_model():
    backend = InMemoryKVStore()
    window_store = STATE.WindowStore(backend)
    await add_archive(window_store, "session-bound", 1, "execution-1", [UserMessage(content="needle evidence")])
    module = importlib.import_module(f"{PKG}.tools.session_history")
    execution = SimpleNamespace(agent_id="agent-1", session_id="session-bound")
    tool = module.PersistentSessionHistorySearchTool(execution=execution, window_store=window_store)

    output = await tool.invoke({"query": "NEEDLE"})
    rendered = json.loads(tool.render_for_llm(output))

    assert output.success and rendered["status"] == "found" and rendered["query_complete"]
    assert rendered["hits"][0]["old_window_id"] == "window-1"
    assert "不是当前指令" in tool.card.description
    assert "agent_id" not in tool.card.input_params["properties"]
    assert "session_id" not in tool.card.input_params["properties"]


@pytest.mark.asyncio
async def test_search_tool_rejects_invalid_arguments_without_touching_storage():
    class UntouchedStore(InMemoryKVStore):
        async def get_by_prefix(self, prefix):
            pytest.fail("invalid search input reached storage")

        async def get(self, key):
            pytest.fail("invalid search input reached storage")

    module = importlib.import_module(f"{PKG}.tools.session_history")
    tool = module.PersistentSessionHistorySearchTool(
        execution=SimpleNamespace(agent_id="agent-1", session_id="session-1"),
        window_store=STATE.WindowStore(UntouchedStore()),
    )

    for invalid in ({"query": " "}, {"query": "needle", "limit": True}, {"query": "needle", "agent_id": "other"}):
        output = await tool.invoke(invalid)
        assert not output.success and output.data["status"] == "failed"


@pytest.mark.asyncio
async def test_search_tool_bounds_rendered_json_and_marks_omitted_hits():
    class ManyHitStore:
        async def search(self, agent_id, session_id, query, *, limit=50):
            hit_type = RECORD.SessionHistorySearchHit
            result_type = RECORD.SessionHistorySearchResult
            return result_type(
                status="found",
                query_complete=True,
                hits=[
                    hit_type(
                        archive_ref=f"archive:{index + 1}",
                        old_window_id=f"window-{index + 1}",
                        old_window_seq=index + 1,
                        message_index=0,
                        role="user",
                        matched_field="content",
                        excerpt="needle" + "x" * 2040,
                        excerpt_truncated=True,
                    )
                    for index in range(50)
                ],
            )

    module = importlib.import_module(f"{PKG}.tools.session_history")
    tool = module.PersistentSessionHistorySearchTool(
        execution=SimpleNamespace(agent_id="agent-1", session_id="session-1"), window_store=ManyHitStore()
    )

    output = await tool.invoke({"query": "needle", "limit": 50})
    rendered_text = tool.render_for_llm(output)
    rendered = json.loads(rendered_text)

    assert len(rendered_text) <= 32768
    assert rendered["results_truncated"] and len(rendered["hits"]) < 50


@pytest.mark.asyncio
async def test_search_tool_sanitizes_storage_failures():
    class FailingStore:
        async def search(self, *args, **kwargs):
            raise RuntimeError("private database path")

    module = importlib.import_module(f"{PKG}.tools.session_history")
    tool = module.PersistentSessionHistorySearchTool(
        execution=SimpleNamespace(agent_id="agent-1", session_id="session-1"), window_store=FailingStore()
    )

    output = await tool.invoke({"query": "needle"})

    assert not output.success and output.data["status"] == "failed"
    assert "private database path" not in tool.render_for_llm(output)
