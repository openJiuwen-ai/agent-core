# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""read_execution_record paging (F_20 D-08): header and events from one snapshot, parts of long lines, refusals."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.harness.persistent_agent.execution import execution_log as LOG
from openjiuwen.harness.persistent_agent.execution import record_tool as RECORD
from openjiuwen.harness.persistent_agent.schema.events import AgentEvent, EventType
from openjiuwen.harness.persistent_agent.schema.execution import ExecutionContext

pytestmark = pytest.mark.level0

TARGET = "execution-2"
WHEN = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
UNUSED_CONTEXT: Any = None  # Registration lifecycle does not access execution state.
OLD = ExecutionContext(
    namespace_id="tenant",
    agent_id="agent-a",
    session_id="session-a",
    request_id="request-1",
    execution_id=TARGET,
    config_version="v1",
    config_fingerprint="0" * 64,
    source_ref="source:message-1",
    principal_ref="user",
    startup_scope=frozenset({"read"}),
    source_scope=frozenset({"read"}),
    policy_revision="p1",
    capability_snapshot=frozenset({"construction"}),
)


def progress(seq, text):
    return AgentEvent.create(
        OLD, event_type=EventType.PROGRESS_UPDATED, local_seq=seq, occurred_at=WHEN, payload={"text": text}
    )


def snapshot(*events, **fields):
    return LOG.LogSnapshot(TARGET, events=tuple(events), **fields)


SHORT = snapshot(progress(1, "a"), progress(2, "b"), progress(4, "d"), missing=((3, 3),))
LONG = snapshot(progress(1, "a"), progress(2, "长" * 9000), progress(3, "c"))


def pages(taken, max_chars):
    """Every page from the first, each next position passed back as given, with its serialized size."""
    out: list = []
    position: dict[str, int] = {}
    while True:
        result = RECORD.page(taken, max_chars=max_chars, **position)
        out.append((result, len(json.dumps(result, ensure_ascii=False))))
        position = result["next"]
        if position is None:
            return out
        assert len(out) < 500, "pages must advance"


def compact(item) -> str:
    return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


def rebuild(results):
    """The record lines the pages carry, the header as line 0, parts joined back."""
    lines: dict[int, str] = {}
    for result in results:
        if "log" in result:
            lines[0] = compact(result["log"])
        for item in result["events"]:
            lines[item["local_seq"]] = compact(item)
        part = result.get("part")
        if part:
            assert part["from"] == len(lines.get(part["local_seq"], "")), "parts of an event come in order"
            lines[part["local_seq"]] = lines.get(part["local_seq"], "") + part["text"]
    return tuple(sorted(lines.items()))


def test_a_short_record_fits_one_page_with_its_header_and_ends():
    result = RECORD.page(SHORT)
    assert result["status"] == "ok" and result["execution_id"] == TARGET and result["encoding"] == LOG.ENCODING
    assert result["log"] == LOG.header(SHORT) and result["log"]["missing_seq_ranges"] == [[3, 3]]
    assert rebuild([result]) == LOG.record_lines(SHORT) and result["next"] is None


def test_after_seq_zero_means_the_header_was_read():
    result = RECORD.page(SHORT, 0)
    assert "log" not in result and [item["local_seq"] for item in result["events"]] == [1, 2, 4]


def test_a_header_longer_than_a_page_is_read_in_parts():
    """Many gaps or long identities make the header itself longer than a page; it pages like a long event."""
    gaps = tuple((seq, seq) for seq in range(2, 2000, 2))
    taken = snapshot(*(progress(seq, "x") for seq in range(1, 2000, 2)), missing=gaps)
    assert len(LOG.record_lines(taken)[0][1]) > RECORD.PAGE_MAX_CHARS
    found = pages(taken, RECORD.PAGE_MAX_CHARS)
    first = found[0][0]
    assert first["part"]["local_seq"] == 0 and first["next"] == {"offset": first["part"]["to"]}, "no after_seq yet"
    assert all(size <= RECORD.PAGE_MAX_CHARS for _, size in found)
    assert rebuild([result for result, _ in found]) == LOG.record_lines(taken)


@pytest.mark.parametrize("max_chars", [700, 1500, 6000])
def test_pages_rebuild_every_event_and_stay_within_the_page_size(max_chars):
    """The size counts the whole result: events, part, header, position and every other field."""
    found = pages(LONG, max_chars)
    assert len(found) > 1 and all(size <= max_chars for _, size in found)
    assert all(result["events"] or result.get("part") for result, _ in found), "no empty page"
    assert all(result["execution_id"] == TARGET and result["encoding"] == LOG.ENCODING for result, _ in found)
    assert ["log" in result for result, _ in found] == [True] + [False] * (len(found) - 1)
    assert rebuild([result for result, _ in found]) == LOG.record_lines(LONG)


def test_an_event_longer_than_a_page_comes_in_parts_that_say_which_part_they_are():
    found = [result for result, _ in pages(LONG, 1500)]
    parts = [result["part"] for result in found if "part" in result]
    line = dict(LOG.event_lines(LONG))[2]
    assert len(parts) > 1 and all(part["local_seq"] == 2 and part["length"] == len(line) for part in parts)
    assert parts[0]["from"] == 0 and parts[-1]["to"] == len(line)
    assert all(not result["events"] for result in found if "part" in result), "a part has its page alone"


def test_after_seq_is_the_last_event_read_in_full_and_offset_the_place_inside_the_next():
    first = RECORD.page(LONG, max_chars=1500)
    assert [item["local_seq"] for item in first["events"]] == [1] and first["next"] == {"after_seq": 1, "offset": 0}
    second = RECORD.page(LONG, 1, 0, 1500)
    assert second["events"] == [] and second["part"]["from"] == 0
    assert second["next"] == {"after_seq": 1, "offset": second["part"]["to"]}
    nowhere = RECORD.page(SHORT, 2)
    assert [item["local_seq"] for item in nowhere["events"]] == [4], "after_seq need not be a seq of the log"


@pytest.mark.parametrize("position", [(None, 99999), (0, 99999), (4, 1), (9, 3)])
def test_an_offset_outside_the_next_event_is_refused(position):
    with pytest.raises(RECORD.PageError) as exc_info:
        RECORD.page(SHORT, *position)
    assert exc_info.value.status == "cursor_position"


def test_reading_past_the_end_returns_no_events_and_no_next_position():
    result = RECORD.page(SHORT, 4)
    assert result["events"] == [] and result["next"] is None and "log" not in result


def test_an_unusable_snapshot_has_only_its_header():
    result = RECORD.page(snapshot(unusable="read_failed"))
    assert result["log"]["usable"] is False and result["log"]["unusable_reason"] == "read_failed"
    assert result["events"] == [] and result["next"] is None


def test_a_page_too_small_for_any_part_is_an_error_not_an_empty_page():
    with pytest.raises(RECORD.PageError) as exc_info:
        RECORD.page(LONG, 1, 0, 120)
    assert exc_info.value.status == "page_too_small"


@pytest.fixture
def record_agent():
    manager = AbilityManager(owner_id=f"record-test-{uuid4().hex}")
    try:
        yield SimpleNamespace(ability_manager=manager)
    finally:
        manager.teardown_tools()


def test_record_rail_initialization_and_own_tool_cleanup_are_idempotent(record_agent):
    rail = RECORD.ExecutionRecordRail(UNUSED_CONTEXT)
    rail.init(record_agent)
    original = rail.tools[0]
    rail.init(record_agent)
    assert rail.tools == [original]
    assert record_agent.ability_manager.get(original.card.name) is original.card
    rail.uninit(record_agent)
    rail.uninit(record_agent)
    assert record_agent.ability_manager.get(original.card.name) is None


def test_record_rail_does_not_remove_a_host_replacement(record_agent):
    rail = RECORD.ExecutionRecordRail(UNUSED_CONTEXT)
    rail.init(record_agent)
    name = rail.tools[0].card.name
    replacement = LocalFunction(
        ToolCard(id="replacement", name=name, input_params={"type": "object", "properties": {}}),
        lambda: "host tool",
    )
    record_agent.ability_manager.add_ability(replacement.card, replacement)
    rail.uninit(record_agent)
    assert record_agent.ability_manager.get(name) is replacement.card
    assert rail.tools == []


def tool_with(snapshots) -> RECORD.ExecutionRecordTool:
    context: Any = SimpleNamespace(binding=SimpleNamespace(control=SimpleNamespace(log_snapshots=snapshots)))
    return RECORD.ExecutionRecordTool(context)


@pytest.mark.asyncio
async def test_record_tool_reads_the_snapshot_and_streams_the_same_result():
    tool = tool_with({TARGET: SHORT})
    result = await tool.invoke({"execution_id": TARGET})
    streamed = [part async for part in tool.stream({"execution_id": TARGET})]
    assert streamed == [result] and result.success
    assert json.loads(result.data["content"]) == RECORD.page(SHORT)
    later = await tool.invoke({"execution_id": TARGET, "after_seq": 2})
    assert [item["local_seq"] for item in json.loads(later.data["content"])["events"]] == [4]


@pytest.mark.parametrize(
    "inputs",
    [
        {"execution_id": 42},
        {"execution_id": ""},
        {"execution_id": TARGET, "after_seq": -1},
        {"execution_id": TARGET, "after_seq": "2"},
        {"execution_id": TARGET, "offset": True},
        {"execution_id": TARGET, "after_seq": None},
        {"execution_id": TARGET, "offset": None},
        {"execution_id": TARGET, "cursor": "old-page-2"},
        {"execution_id": TARGET, "limit": 5},
        "execution-2",
    ],
)
@pytest.mark.asyncio
async def test_record_tool_rejects_invalid_arguments(inputs):
    """Null is not a default and an old cursor is not ignored: either would silently return the first page."""
    result = await tool_with({TARGET: SHORT}).invoke(inputs)
    assert not result.success and result.data == {"status": "invalid_arguments"}
    assert "cursor" in result.error and "null" in result.error


def test_the_accepted_arguments_are_the_declared_ones():
    properties: Any = RECORD._INPUT_PARAMS["properties"]
    assert RECORD._ARGUMENTS == set(properties)
    assert RECORD._INPUT_PARAMS["additionalProperties"] is False


@pytest.mark.asyncio
async def test_record_tool_answers_only_for_the_snapshots_of_this_continuation():
    unavailable = await tool_with({}).invoke({"execution_id": TARGET})
    assert not unavailable.success and unavailable.data == {"status": "unavailable"}
    elsewhere = await tool_with({TARGET: SHORT}).invoke({"execution_id": "execution-9"})
    assert not elsewhere.success and elsewhere.data == {"status": "not_in_chain"}
    refused = await tool_with({TARGET: SHORT}).invoke({"execution_id": TARGET, "after_seq": 9, "offset": 3})
    assert refused.data == {"status": "cursor_position"}
