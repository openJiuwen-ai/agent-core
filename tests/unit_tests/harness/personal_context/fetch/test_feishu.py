"""Contract tests for the embedded Feishu lark-cli provider."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch.feishu import FeishuFetchService, _fetch_wiki_content
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem


def feishu_config(
    *,
    mode: str = "account",
    resources: list[str] | None = None,
    max_items_per_run: int | None = None,
    **source: object,
) -> PersonalContextFetchServiceConfig:
    source_payload: dict[str, object] = {"mode": mode}
    if mode == "account":
        source_payload["resources"] = resources or ["docs"]
    source_payload.update(source)
    return PersonalContextFetchServiceConfig.model_validate(
        {
            "service_id": "feishu-demo",
            "provider": "feishu",
            "enabled": True,
            "interval_seconds": 60,
            "max_items_per_run": max_items_per_run,
            "source": source_payload,
            "credentials": {},
        }
    )


async def _batches(
    service: FeishuFetchService,
    cursor: object | None = None,
) -> list[FetchBatch]:
    normalized_cursor = dict(cursor) if isinstance(cursor, Mapping) else cast(dict[str, object] | None, cursor)
    return [batch async for batch in service.fetch(run_id="run-1", cursor=normalized_cursor)]


def _items(batches: list[FetchBatch]) -> list[RawChangeItem]:
    return [item for batch in batches for item in batch.items]


def _fake_cli(monkeypatch: pytest.MonkeyPatch, responses: dict[tuple[str, ...], list[object]]) -> list[list[str]]:
    import openjiuwen.harness.personal_context.fetch.feishu as feishu_module

    calls: list[list[str]] = []

    async def run(argv: list[str], *, timeout_seconds: float = 30.0) -> object:
        del timeout_seconds
        calls.append(list(argv))
        key = tuple(argv[:2])
        values = responses.get(key)
        if not values:
            raise AssertionError(f"unexpected lark-cli argv: {argv}")
        return values.pop(0)

    monkeypatch.setattr(feishu_module, "_run_lark_cli_json", run)
    return calls


@pytest.mark.asyncio
async def test_account_uses_user_cli_for_selected_docs_tasks_and_calendar(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses: dict[tuple[str, ...], list[object]] = {
        ("auth", "status"): [
            {
                "identities": {
                    "user": {
                        "available": True,
                        "verified": True,
                        "tokenStatus": "valid",
                        "scope": "docs:document.content:read task:task:read calendar:calendar.event:read",
                    }
                }
            }
        ],
        ("docs", "+fetch"): [
            {"ok": True, "data": {"document": {"document_id": "doc-1", "revision_id": 2, "content": "# Plan\nbody"}}}
        ],
        ("task", "+get-my-tasks"): [
            {"ok": True, "data": {"items": [{"guid": "task-1", "summary": "Ship", "notes": "soon"}], "has_more": False}}
        ],
        ("calendar", "+agenda"): [
            {
                "ok": True,
                "data": {
                    "items": [{"event_id": "event-1", "summary": "Demo", "description": "today"}],
                    "has_more": False,
                },
            }
        ],
    }
    calls = _fake_cli(monkeypatch, responses)
    service = FeishuFetchService(
        feishu_config(
            resources=["docs", "tasks", "calendar"],
            document_ids=["doc-1"],
            start="2026-08-01",
            end="2026-08-02",
        ),
        home=tmp_path,
    )

    items = _items(await _batches(service))

    assert {item.logical_id for item in items} == {
        "feishu:doc:doc-1",
        "feishu:task:task-1",
        "feishu:calendar:event-1",
    }
    assert all("access_token" not in argv for argv in calls)
    assert all(
        argv[argv.index("--as") : argv.index("--as") + 2] == ["--as", "user"]
        for argv in calls
        if argv[:2] != ["auth", "status"]
    )


@pytest.mark.asyncio
async def test_search_docs_follows_cli_page_token_without_openapi(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    responses: dict[tuple[str, ...], list[object]] = {
        ("auth", "status"): [
            {
                "identities": {
                    "user": {
                        "available": True,
                        "tokenStatus": "valid",
                        "scope": "docs:document.content:read search:docs:read",
                    }
                }
            }
        ],
        ("docs", "+search"): [
            {
                "ok": True,
                "data": {
                    "items": [{"doc_id": "doc-1", "title": "A", "content": "a"}],
                    "has_more": True,
                    "page_token": "next",
                },
            },
            {"ok": True, "data": {"items": [{"doc_id": "doc-2", "title": "B", "content": "b"}], "has_more": False}},
        ],
    }
    calls = _fake_cli(monkeypatch, responses)
    service = FeishuFetchService(feishu_config(query="knowledge"), home=tmp_path)

    items = _items(await _batches(service))

    assert {item.logical_id for item in items} == {"feishu:doc:doc-1", "feishu:doc:doc-2"}
    search_calls = [argv for argv in calls if argv[:2] == ["docs", "+search"]]
    assert any("--page-token" in argv for argv in search_calls)


@pytest.mark.asyncio
async def test_wiki_cli_scans_children_and_emits_deletes(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    responses: dict[tuple[str, ...], list[object]] = {
        ("auth", "status"): [
            {
                "identities": {
                    "user": {
                        "available": True,
                        "tokenStatus": "valid",
                        "scope": "wiki:node:retrieve docs:document.content:read",
                    }
                }
            }
        ],
        ("wiki", "+node-list"): [
            {
                "ok": True,
                "data": {
                    "nodes": [
                        {
                            "node_token": "root",
                            "obj_token": "doc-root",
                            "obj_type": "docx",
                            "title": "Root",
                            "has_child": True,
                        }
                    ],
                    "has_more": False,
                },
            },
            {
                "ok": True,
                "data": {
                    "nodes": [
                        {
                            "node_token": "child",
                            "obj_token": "doc-child",
                            "obj_type": "docx",
                            "title": "Child",
                            "has_child": False,
                        }
                    ],
                    "has_more": False,
                },
            },
        ],
        ("docs", "+fetch"): [
            {"ok": True, "data": {"document": {"document_id": "doc-root", "revision_id": 1, "content": "Root body"}}},
            {"ok": True, "data": {"document": {"document_id": "doc-child", "revision_id": 1, "content": "Child body"}}},
        ],
    }
    _fake_cli(monkeypatch, responses)
    service = FeishuFetchService(feishu_config(mode="wiki_space", wiki_space_id="space-1", max_depth=2), home=tmp_path)

    batches = await _batches(
        service,
        {"wiki_space": {"nodes": {"old": {"node_token": "old", "title": "Old", "obj_token": "doc-old"}}}},
    )
    items = _items(batches)

    assert {item.logical_id for item in items} == {
        "feishu:wiki:space-1:root",
        "feishu:wiki:space-1:child",
        "feishu:wiki:space-1:old",
    }
    assert next(item for item in items if item.logical_id.endswith(":old")).operation == "delete"


@pytest.mark.asyncio
async def test_wiki_initial_limit_selects_most_recent_nodes_not_api_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses: dict[tuple[str, ...], list[object]] = {
        ("auth", "status"): [
            {
                "identities": {
                    "user": {
                        "available": True,
                        "tokenStatus": "valid",
                        "scope": "wiki:node:retrieve docs:document.content:read",
                    }
                }
            }
        ],
        ("wiki", "+node-list"): [
            {
                "ok": True,
                "data": {
                    "nodes": [
                        {
                            "node_token": "old",
                            "obj_token": "doc-old",
                            "obj_type": "docx",
                            "title": "Old",
                            "updated_at": "2026-01-01T00:00:00Z",
                        },
                        {
                            "node_token": "new",
                            "obj_token": "doc-new",
                            "obj_type": "docx",
                            "title": "New",
                            "updated_at": "2026-03-01T00:00:00Z",
                        },
                        {
                            "node_token": "middle",
                            "obj_token": "doc-middle",
                            "obj_type": "docx",
                            "title": "Middle",
                            "updated_at": "2026-02-01T00:00:00Z",
                        },
                    ],
                    "has_more": False,
                },
            }
        ],
        ("docs", "+fetch"): [
            {"ok": True, "data": {"document": {"document_id": "doc-new", "content": "new"}}},
            {"ok": True, "data": {"document": {"document_id": "doc-middle", "content": "middle"}}},
        ],
    }
    _fake_cli(monkeypatch, responses)
    service = FeishuFetchService(
        feishu_config(mode="wiki_space", wiki_space_id="space-1", max_items_per_run=2),
        home=tmp_path,
    )

    items = _items(await _batches(service))

    assert [item.logical_id for item in items] == [
        "feishu:wiki:space-1:new",
        "feishu:wiki:space-1:middle",
    ]


@pytest.mark.asyncio
async def test_missing_cli_authorization_fails_without_starting_login(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openjiuwen.harness.personal_context.fetch.feishu as feishu_module

    calls: list[list[str]] = []

    async def run(argv: list[str], *, timeout_seconds: float = 30.0) -> object:
        del timeout_seconds
        calls.append(argv)
        return {"identities": {"user": {"available": False, "tokenStatus": "missing", "scope": ""}}}

    monkeypatch.setattr(feishu_module, "_run_lark_cli_json", run)
    service = FeishuFetchService(feishu_config(mode="wiki_space", wiki_space_id="space-1"), home=tmp_path)

    with pytest.raises(BaseError):
        await _batches(service)

    assert [argv[:2] for argv in calls] == [["auth", "status"]]


@pytest.mark.asyncio
async def test_wiki_file_download_uses_relative_output_inside_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openjiuwen.harness.personal_context.fetch.feishu as feishu_module

    calls: list[tuple[list[str], Path | None]] = []

    async def run(argv: list[str], *, timeout_seconds: float = 30.0, cwd: Path | None = None) -> tuple[str, str]:
        del timeout_seconds
        calls.append((list(argv), cwd))
        output = argv[argv.index("--output") + 1]
        target = Path(cwd, output) if cwd is not None else Path(output)
        target.write_bytes(b"downloaded file")
        return "", ""

    monkeypatch.setattr(feishu_module, "_run_lark_cli", run)

    _, content = await _fetch_wiki_content(
        {"obj_type": "file", "obj_token": "file-1"},
        home=tmp_path,
    )

    assert content == "downloaded file"
    assert len(calls) == 1
    argv, cwd = calls[0]
    assert argv[argv.index("--output") + 1] == "./download"
    assert cwd is not None
    assert cwd.parent == tmp_path / "workspace" / "sandboxes"


@pytest.mark.asyncio
async def test_feishu_docs_continue_history_and_prioritize_new_revisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.feishu as feishu_module

    document_ids = [f"doc-{index:03}" for index in range(205)]

    def payload(document_id: str, *, revision: int = 1, title: str | None = None) -> dict[str, object]:
        return {
            "ok": True,
            "data": {
                "document": {
                    "document_id": document_id,
                    "revision_id": revision,
                    "title": title or f"Document {document_id}",
                    "content": f"Body {document_id} revision {revision}",
                }
            },
        }

    def install_cli(documents: dict[str, dict[str, object]]) -> None:
        async def run(argv: list[str], *, timeout_seconds: float = 30.0) -> object:
            del timeout_seconds
            if argv[:2] == ["auth", "status"]:
                return {
                    "identities": {
                        "user": {
                            "available": True,
                            "verified": True,
                            "tokenStatus": "valid",
                            "scope": "docs:document.content:read",
                        }
                    }
                }
            assert argv[:2] == ["docs", "+fetch"]
            document_id = argv[argv.index("--doc") + 1]
            return documents[document_id]

        monkeypatch.setattr(feishu_module, "_run_lark_cli_json", run)

    initial_documents = {document_id: payload(document_id) for document_id in document_ids}
    install_cli(initial_documents)
    service = FeishuFetchService(
        feishu_config(
            resources=["docs"],
            document_ids=document_ids,
            max_items_per_run=100,
        ),
        home=tmp_path / "home",
    )

    cursor: dict[str, object] | None = None
    rounds: list[list[RawChangeItem]] = []
    for _round in range(3):
        batches = await _batches(service, cursor)
        rounds.append(_items(batches))
        assert batches
        cursor = batches[-1].next_cursor

    assert [len(items) for items in rounds] == [100, 100, 5]
    logical_ids = [item.logical_id for items in rounds for item in items]
    assert len(logical_ids) == len(set(logical_ids)) == 205
    assert {item.logical_id for item in rounds[-1]} == {f"feishu:doc:doc-{index:03}" for index in range(200, 205)}
    assert cursor is not None
    assert set(cursor["docs"]["items"]) == set(document_ids)

    first_documents = dict(initial_documents)
    install_cli(first_documents)
    priority_service = FeishuFetchService(
        feishu_config(
            resources=["docs"],
            document_ids=document_ids,
            max_items_per_run=100,
        ),
        home=tmp_path / "priority-home",
    )
    first = await _batches(priority_service)
    first_items = _items(first)
    first_cursor = first[-1].next_cursor
    assert first_cursor is not None

    changed_documents = dict(initial_documents)
    changed_documents["doc-099"] = payload("doc-099", revision=2, title="Modified")
    changed_documents["new-a"] = payload("new-a", revision=1, title="New A")
    changed_documents["new-b"] = payload("new-b", revision=1, title="New B")
    install_cli(changed_documents)
    changed = _items(
        await _batches(
            FeishuFetchService(
                feishu_config(
                    resources=["docs"],
                    document_ids=[*document_ids, "new-a", "new-b"],
                    max_items_per_run=100,
                ),
                home=tmp_path / "priority-home",
            ),
            first_cursor,
        )
    )
    assert len(changed) == 100
    assert [item.title for item in changed[:3]] == ["New B", "New A", "Modified"]
    assert changed[2].logical_id == first_items[99].logical_id
