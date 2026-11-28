# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import json
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.tools import TodoWriteTool


def _session(session_id="session"):
    return SimpleNamespace(get_session_id=lambda: session_id)


def _todo(todo_id="verify", status="pending", **kwargs):
    return {"id": todo_id, "content": f"Task {todo_id}", "status": status, **kwargs}


@pytest.mark.asyncio
async def test_todo_write_reopens_legacy_plan_and_preserves_metadata(tmp_path):
    path = tmp_path / "session" / "todo.json"
    path.parent.mkdir()
    path.write_text(json.dumps([_todo(activeForm="Verifying", description="Details",
                                      selected_model_id="fast", meta_data={"source": "existing"})]))
    tool = TodoWriteTool(workspace=tmp_path, language="en")
    snapshot = await tool.load_snapshot("session")

    result = await tool.invoke(
        {"todos": [_todo(status="completed"), _todo("next")], "revision": snapshot["revision"]},
        session=_session(),
    )

    saved = await tool.load_todos("session")
    assert [todo.id for todo in saved] == ["verify", "next"]
    assert saved[0].status.value == "completed"
    assert saved[0].activeForm == "Verifying"
    assert saved[0].description == "Details"
    assert saved[0].meta_data == {"source": "existing"}
    assert saved[0].selected_model_id == "fast"
    assert result["revision"] != snapshot["revision"]


@pytest.mark.asyncio
async def test_todo_write_only_one_concurrent_update_can_use_a_revision(tmp_path):
    first = TodoWriteTool(workspace=tmp_path)
    second = TodoWriteTool(workspace=tmp_path)
    start = await first.invoke({"todos": [_todo()]}, session=_session())

    results = await asyncio.gather(
        first.invoke({"todos": [_todo("one")], "revision": start["revision"]}, session=_session()),
        second.invoke({"todos": [_todo("two")], "revision": start["revision"]}, session=_session()),
    )
    conflict = next(result for result in results if result.get("conflict"))
    current = await first.load_snapshot("session")
    assert sum(bool(result.get("conflict")) for result in results) == 1
    assert conflict["revision"] == current["revision"]
    assert conflict["todos"] == current["todos"]


@pytest.mark.asyncio
async def test_todo_write_clear_requires_latest_revision(tmp_path):
    tool = TodoWriteTool(workspace=tmp_path)
    created = await tool.invoke({"todos": [_todo()]}, session=_session())
    conflict = await tool.invoke({"todos": []}, session=_session())
    assert conflict["conflict"]
    assert (await tool.load_todos("session"))[0].id == "verify"

    cleared = await tool.invoke({"todos": [], "revision": created["revision"]}, session=_session())
    assert cleared["todos"] == []
    assert (await tool.load_snapshot("session"))["revision"] == cleared["revision"]
    assert cleared["revision"] != created["revision"]


@pytest.mark.asyncio
async def test_cancel_updates_version_without_replacing_completed_todos(tmp_path):
    tool = TodoWriteTool(workspace=tmp_path)
    created = await tool.invoke(
        {"todos": [_todo("done", status="completed"), _todo("active", status="in_progress")]},
        session=_session(),
    )
    cancelled = await TodoWriteTool(workspace=tmp_path).cancel_pending("session")
    assert [todo.status.value for todo in cancelled] == ["completed", "cancelled"]
    assert (await tool.load_snapshot("session"))["revision"] != created["revision"]


@pytest.mark.asyncio
@pytest.mark.parametrize("items", [
    [_todo(), _todo()],
    [_todo(status="in_progress"), _todo("another", status="in_progress")],
    [_todo(status="unknown")],
])
async def test_todo_write_rejects_invalid_list_without_saving(tmp_path, items):
    tool = TodoWriteTool(workspace=tmp_path)
    with pytest.raises(BaseError):
        await tool.invoke({"todos": items}, session=_session())
    assert (await tool.load_snapshot("session")) is None


@pytest.mark.parametrize("language", ["cn", "en"])
def test_todo_write_schema_exposes_shared_optional_model_selection(tmp_path, language):
    schema = TodoWriteTool(workspace=tmp_path, language=language).card.input_params
    assert set(schema["properties"]) == {"todos", "revision"}
    assert schema["required"] == ["todos"]
    item = schema["properties"]["todos"]["items"]
    assert set(item["required"]) == {"id", "content", "status"}
    assert item["properties"]["selected_model_id"]["type"] == "string"

@pytest.mark.asyncio
async def test_failed_atomic_replace_keeps_existing_plan(tmp_path, monkeypatch):
    tool = TodoWriteTool(workspace=tmp_path)
    before = await tool.invoke({"todos": [_todo()]}, session=_session())

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr("openjiuwen.harness.tools.todo.os.replace", fail_replace)
    with pytest.raises(BaseError, match="replace failed"):
        await tool.invoke({"todos": [], "revision": before["revision"]}, session=_session())

    assert await tool.load_snapshot("session") == before
    assert {path.name for path in (tmp_path / "session").iterdir()} - {"todo.lock"} == {"todo.json"}


@pytest.mark.asyncio
async def test_todo_write_keeps_valid_dependencies_and_rejects_dangling_ids(tmp_path):
    path = tmp_path / "session" / "todo.json"
    path.parent.mkdir()
    path.write_text(json.dumps([_todo("source"), _todo("target", depends_on=["source"])]))
    tool = TodoWriteTool(workspace=tmp_path)
    before = await tool.load_snapshot("session")
    with pytest.raises(BaseError, match="missing dependency"):
        await tool.invoke({"todos": [_todo("target")], "revision": before["revision"]}, session=_session())
    assert await tool.load_snapshot("session") == before


@pytest.mark.parametrize(("language", "rules", "list_rules", "model_rule"), [
    (
        "cn",
        ("多步", "简单单步", "开始前", "可验证的里程碑", "首次提交", "最多一项", "验证后", "同一次整表提交"),
        ("完整有序列表", "原 ID", "省略已有 ID 即删除", "[]"),
        "省略时保留",
    ),
    (
        "en",
        ("multi-step", "simple single-step", "before starting", "verifiable milestones", "after verification",
         "first write", "at most one", "one full-list write"),
        ("full ordered list", "original IDs", "Omitting an existing ID deletes", "[]"),
        "Omitting preserves",
    ),
])
def test_todo_card_explains_when_to_plan_and_how_to_replace_safely(
    tmp_path, language, rules, list_rules, model_rule,
):
    card = TodoWriteTool(workspace=tmp_path, language=language).card
    for rule in rules:
        assert rule.casefold() in card.description.casefold()
    properties = card.input_params["properties"]
    for rule in list_rules:
        assert rule.casefold() in properties["todos"]["description"].casefold()
    for rule in ("todo_snapshot", "conflict=true"):
        assert rule in properties["revision"]["description"]
    item_fields = properties["todos"]["items"]["properties"]
    assert model_rule in item_fields["selected_model_id"]["description"]
    for status in item_fields["status"]["enum"]:
        assert status in item_fields["status"]["description"]
