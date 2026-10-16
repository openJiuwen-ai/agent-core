# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""read_execution_record paging (F_20 D-08): cursors, lossless pieces, page size, resets and refusals."""

import base64
import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.foundation.tool.function.function import LocalFunction
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.harness.persistent_agent.execution import record_tool as RECORD

pytestmark = pytest.mark.level0

TARGET = "execution-2"
UNUSED_CONTEXT: Any = None  # Registration lifecycle does not access execution state.
BLOCKS = (
    "上一次执行 execution-2（请求 request-1）的执行日志整理：",
    "1. [结论] " + "长" * 900,
    "2. [结论] 短",
    "结局：已完成",
)


def pages(blocks, max_chars, execution_id=TARGET):
    """Every page from the first, with its serialized size."""
    out, cursor = [], None
    while True:
        result = RECORD.page(execution_id, blocks, cursor, max_chars)
        out.append((result, len(json.dumps(result, ensure_ascii=False))))
        cursor = result["next_cursor"]
        if cursor is None:
            return out
        assert len(out) < 500, "pages must advance"


def rebuild(results):
    items: dict[int, str] = {}
    for result in results:
        for segment in result["segments"]:
            assert segment["from"] == len(items.get(segment["item"], "")), "pieces of an item come in order"
            items[segment["item"]] = items.get(segment["item"], "") + segment["text"]
    return "\n".join(items[index] for index in sorted(items))


def test_a_short_record_fits_one_page_and_ends_without_a_cursor():
    result = RECORD.page(TARGET, BLOCKS, None)
    assert result["status"] == "ok" and result["reset"] is False and result["next_cursor"] is None
    assert rebuild([result]) == "\n".join(BLOCKS)


@pytest.mark.parametrize("max_chars", [400, 700, 1000])
def test_pages_advance_rebuild_the_record_losslessly_and_stay_within_the_page_size(max_chars):
    """The size counts the whole result: text, cursor, status and every other field."""
    found = pages(BLOCKS, max_chars)
    assert len(found) > 1
    assert all(size <= max_chars for _, size in found)
    assert all(result["segments"] for result, _ in found), "no empty page"
    assert rebuild([result for result, _ in found]) == "\n".join(BLOCKS)


def test_a_long_item_is_read_on_in_pieces_that_say_which_part_they_are():
    found = [result for result, _ in pages(BLOCKS, 500)]
    long_pieces = [segment for result in found for segment in result["segments"] if segment["item"] == 1]
    assert len(long_pieces) > 1
    assert all(piece["length"] == len(BLOCKS[1]) for piece in long_pieces)
    assert long_pieces[0]["from"] == 0 and long_pieces[-1]["to"] == len(BLOCKS[1])


def test_a_changed_record_restarts_from_its_first_page_and_says_so():
    first = RECORD.page(TARGET, BLOCKS, None, 500)
    changed = (*BLOCKS[:-1], "结局：暂停（error）")
    again = RECORD.page(TARGET, changed, first["next_cursor"], 500)
    assert again["reset"] is True and again["notice"] == "记录已变化，已从头返回"
    assert again["segments"][0]["item"] == 0 and again["segments"][0]["from"] == 0


def cursor_of(*parts):
    return base64.urlsafe_b64encode(json.dumps(list(parts)).encode()).decode()


@pytest.mark.parametrize(
    "cursor",
    [
        pytest.param("not a cursor", id="not_base64"),
        pytest.param(cursor_of(1, TARGET, "v"), id="too_short"),
        pytest.param(cursor_of(2, TARGET, "v", 0, 0), id="other_version"),
        pytest.param(cursor_of(1, TARGET, "v", True, 0), id="bool_position"),
        pytest.param(cursor_of(1, TARGET, "v", 0, -1), id="negative_position"),
        pytest.param(base64.urlsafe_b64encode(b"\xff\xfe").decode(), id="not_utf8"),
    ],
)
def test_a_malformed_cursor_is_refused_not_reset(cursor):
    with pytest.raises(RECORD.PageError) as exc_info:
        RECORD.page(TARGET, BLOCKS, cursor)
    assert exc_info.value.status == "invalid_cursor"


def test_another_executions_cursor_is_refused():
    other = RECORD.page("execution-1", BLOCKS, None, 500)["next_cursor"]
    with pytest.raises(RECORD.PageError) as exc_info:
        RECORD.page(TARGET, BLOCKS, other, 500)
    assert exc_info.value.status == "cursor_target"


@pytest.mark.parametrize("position", [(len(BLOCKS), 0), (1, len(BLOCKS[1])), (0, 999)])
def test_a_position_that_does_not_exist_is_refused(position):
    version = RECORD.page(TARGET, BLOCKS, None, 500)["next_cursor"]
    made_for = json.loads(base64.urlsafe_b64decode(version))[2]
    with pytest.raises(RECORD.PageError) as exc_info:
        RECORD.page(TARGET, BLOCKS, RECORD.encode_cursor(TARGET, made_for, *position), 500)
    assert exc_info.value.status == "cursor_position"


def test_a_page_too_small_for_any_piece_is_an_error_not_an_empty_page():
    with pytest.raises(RECORD.PageError) as exc_info:
        RECORD.page(TARGET, BLOCKS, None, 120)
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


@pytest.mark.asyncio
async def test_record_tool_stream_keeps_the_read_result_and_rejects_invalid_arguments(monkeypatch):
    context: Any = SimpleNamespace(binding=SimpleNamespace(control=SimpleNamespace(verified_chain=(TARGET,))))
    reads = []

    async def read_blocks(actual_context, execution_id):
        assert actual_context is context
        reads.append(execution_id)
        return BLOCKS, False

    monkeypatch.setattr(RECORD, "record_blocks", read_blocks)
    tool = RECORD.ExecutionRecordTool(context)
    result = await tool.invoke({"execution_id": TARGET})
    streamed = [part async for part in tool.stream({"execution_id": TARGET})]
    assert streamed == [result] and result.success
    assert reads == [TARGET, TARGET]
    invalid = [part async for part in tool.stream({"execution_id": 42})]
    assert len(invalid) == 1 and not invalid[0].success
    assert reads == [TARGET, TARGET], "invalid arguments must not read logs"
