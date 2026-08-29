"""Contract tests for the embedded Feishu lark-cli provider."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch import retry as retry_module
from openjiuwen.harness.personal_context.fetch.cursor_selection import record_completed_candidates
from openjiuwen.harness.personal_context.fetch.feishu import FeishuFetchService, _fetch_wiki_content
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem


def feishu_config(
    *,
    mode: str = "account",
    resources: list[str] | None = None,
    max_items_per_run: int | None = None,
    time_range: dict[str, object] | None = None,
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
            "time_range": time_range or {"mode": "all"},
            "max_items_per_run": max_items_per_run,
            "source": source_payload,
            "credentials": {},
        }
    )


async def _batches(
    service: FeishuFetchService,
    cursor: object | None = None,
    *,
    run_started_at: datetime | None = None,
) -> list[FetchBatch]:
    normalized_cursor = dict(cursor) if isinstance(cursor, Mapping) else cast(dict[str, object] | None, cursor)
    candidates = await service.prepare_run(
        run_id="run-1",
        run_started_at=run_started_at or datetime.now(UTC),
        cursor=normalized_cursor,
    )
    batches = [
        batch
        async for batch in service.fetch(
            run_id="run-1",
            cursor=normalized_cursor,
            candidates=candidates,
        )
    ]
    if batches:
        committed = record_completed_candidates(batches[-1].next_cursor, candidates)
        batches[-1] = batches[-1].model_copy(update={"next_cursor": committed})
    return batches


def _items(batches: list[FetchBatch]) -> list[RawChangeItem]:
    return [item for batch in batches for item in batch.items]


async def _no_retry_sleep(_delay: float) -> None:
    return None


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
async def test_feishu_read_cli_retries_transient_error_then_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    import openjiuwen.harness.personal_context.fetch.feishu as feishu_module

    calls = 0

    async def run_once(argv: list[str], *, timeout_seconds: float = 30.0, cwd: Path | None = None):
        nonlocal calls
        del timeout_seconds, cwd
        calls += 1
        if calls == 1:
            payload = json.dumps({"error": {"status": 503, "code": "service_unavailable"}}).encode()
            raise subprocess.CalledProcessError(1, argv, output=payload, stderr=b"")
        return '{"data":{"items":[]}}', ""

    monkeypatch.setattr(feishu_module, "_run_lark_cli_once", run_once)
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)

    result = await feishu_module._run_lark_cli_read_json(["wiki", "node", "list"])

    assert result == {"data": {"items": []}}
    assert calls == 2


@pytest.mark.asyncio
async def test_feishu_read_cli_exhaustion_has_one_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    import openjiuwen.harness.personal_context.fetch.feishu as feishu_module

    calls = 0

    async def run_once(argv: list[str], *, timeout_seconds: float = 30.0, cwd: Path | None = None):
        nonlocal calls
        del timeout_seconds, cwd
        calls += 1
        payload = b'{"error":{"status":503,"code":"service_unavailable"}}'
        raise subprocess.CalledProcessError(1, argv, output=payload, stderr=b"")

    monkeypatch.setattr(feishu_module, "_run_lark_cli_once", run_once)
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)

    with pytest.raises(BaseError):
        await feishu_module._run_lark_cli_read_json(["wiki", "node", "list"])

    assert calls == 3


@pytest.mark.asyncio
async def test_feishu_authorization_login_and_finish_do_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    import openjiuwen.harness.personal_context.fetch.feishu as feishu_module

    calls = 0

    async def run_once(argv: list[str], *, timeout_seconds: float = 30.0, cwd: Path | None = None):
        nonlocal calls
        del argv, timeout_seconds, cwd
        calls += 1
        raise TimeoutError("temporary timeout")

    monkeypatch.setattr(feishu_module, "_run_lark_cli_once", run_once)
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)

    with pytest.raises(BaseError):
        await feishu_module._lark_cli_begin_authorization(("docs:document.content:read",))
    assert calls == 1

    with pytest.raises(BaseError):
        await feishu_module._lark_cli_finish_authorization("device-code", timeout_seconds=30.0)

    assert calls == 2


@pytest.mark.asyncio
async def test_feishu_read_cli_does_not_retry_authorization_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import openjiuwen.harness.personal_context.fetch.feishu as feishu_module

    calls = 0

    async def run_once(argv: list[str], *, timeout_seconds: float = 30.0, cwd: Path | None = None):
        nonlocal calls
        del timeout_seconds, cwd
        calls += 1
        payload = b'{"error":{"status":403,"code":"missing_scope"}}'
        raise subprocess.CalledProcessError(1, argv, output=payload, stderr=b"")

    monkeypatch.setattr(feishu_module, "_run_lark_cli_once", run_once)
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)

    with pytest.raises(BaseError):
        await feishu_module._run_lark_cli_read_json(["wiki", "node", "list"])

    assert calls == 1


@pytest.mark.asyncio
async def test_feishu_download_retries_in_independent_temporary_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.feishu as feishu_module

    attempt_directories: list[Path] = []

    async def run_once(argv: list[str], *, timeout_seconds: float = 30.0, cwd: Path | None = None):
        del timeout_seconds
        assert cwd is not None
        attempt_directories.append(cwd)
        target = cwd / argv[argv.index("--output") + 1]
        if len(attempt_directories) == 1:
            target.write_bytes(b"partial")
            payload = b'{"error":{"status":503,"code":"service_unavailable"}}'
            raise subprocess.CalledProcessError(1, argv, output=payload, stderr=b"")
        assert not target.exists()
        target.write_bytes(b"complete")
        return "", ""

    monkeypatch.setattr(feishu_module, "_run_lark_cli_once", run_once)
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)

    _, content = await _fetch_wiki_content(
        {"obj_type": "file", "obj_token": "file-1"},
        home=tmp_path,
    )

    assert content == "complete"
    assert len(attempt_directories) == 2
    assert attempt_directories[0] != attempt_directories[1]
    assert all(not directory.exists() for directory in attempt_directories)


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
            {"ok": True, "data": {"items": [{"guid": "task-1", "summary": "Ship", "notes": "soon"}]}}
        ],
        ("calendar", "+agenda"): [
            {
                "ok": True,
                "data": {
                    "items": [{"event_id": "event-1", "summary": "Demo", "description": "today"}],
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
                    "page_token": "next",
                },
            },
            {"ok": True, "data": {"items": [{"doc_id": "doc-2", "title": "B", "content": "b"}]}},
        ],
    }
    calls = _fake_cli(monkeypatch, responses)
    service = FeishuFetchService(feishu_config(query="knowledge"), home=tmp_path)

    items = _items(await _batches(service))

    assert {item.logical_id for item in items} == {"feishu:doc:doc-1", "feishu:doc:doc-2"}
    search_calls = [argv for argv in calls if argv[:2] == ["docs", "+search"]]
    assert any("--page-token" in argv for argv in search_calls)


@pytest.mark.asyncio
async def test_wiki_cli_scans_children_without_emitting_deletes(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    }
    assert all(item.operation == "upsert" for item in items)


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

    monkeypatch.setattr(feishu_module, "_run_lark_cli_once", run)

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

    def payload(
        document_id: str,
        *,
        revision: int = 1,
        title: str | None = None,
        updated_at: int | None = None,
    ) -> dict[str, object]:
        suffix = int(document_id.rsplit("-", 1)[-1]) if document_id.rsplit("-", 1)[-1].isdigit() else 300
        return {
            "ok": True,
            "data": {
                "document": {
                    "document_id": document_id,
                    "revision_id": revision,
                    "updated_at": updated_at or 1_700_000_000 + suffix,
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
    assert {item.logical_id for item in rounds[-1]} == {f"feishu:doc:doc-{index:03}" for index in range(5)}
    assert cursor is not None
    assert len(cursor["_selection"]["completed"]) == len(document_ids)

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
    first_cursor = first[-1].next_cursor
    assert first_cursor is not None

    changed_documents = dict(initial_documents)
    changed_documents["doc-099"] = payload("doc-099", revision=2, title="Modified", updated_at=1_800_000_001)
    changed_documents["new-a"] = payload("new-a", revision=1, title="New A", updated_at=1_800_000_002)
    changed_documents["new-b"] = payload("new-b", revision=1, title="New B", updated_at=1_800_000_003)
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
    assert changed[2].logical_id == "feishu:doc:doc-099"


@pytest.mark.asyncio
async def test_feishu_docs_new_overflow_stays_ahead_of_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.feishu as feishu_module

    def payload(document_id: str) -> dict[str, object]:
        suffix = int(document_id.rsplit("-", 1)[-1])
        return {
            "ok": True,
            "data": {
                "document": {
                    "document_id": document_id,
                    "revision_id": 1,
                    "updated_at": 1_700_000_000 + suffix,
                    "title": document_id,
                    "content": f"Body {document_id}",
                }
            },
        }

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
        document_id = argv[argv.index("--doc") + 1]
        return payload(document_id)

    monkeypatch.setattr(feishu_module, "_run_lark_cli_json", run)
    initial_ids = ["base-3", "base-2", "base-1"]
    service = FeishuFetchService(
        feishu_config(resources=["docs"], document_ids=initial_ids, max_items_per_run=2),
        home=tmp_path,
    )
    first = await _batches(service)
    cursor = first[-1].next_cursor
    assert cursor is not None

    changed_ids = [*initial_ids, *[f"new-{number}" for number in range(4, 9)]]
    round_titles: list[list[str]] = []
    for _ in range(3):
        batches = await _batches(
            FeishuFetchService(
                feishu_config(resources=["docs"], document_ids=changed_ids, max_items_per_run=2),
                home=tmp_path,
            ),
            cursor,
        )
        round_titles.append([item.title for item in _items(batches)])
        cursor = batches[-1].next_cursor
        assert cursor is not None

    assert round_titles == [
        ["new-8", "new-7"],
        ["new-6", "new-5"],
        ["new-4", "base-1"],
    ]


@pytest.mark.asyncio
async def test_docs_discovery_applies_time_before_fetching_selected_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
                "data": {
                    "items": [
                        {"doc_id": "old", "title": "Old", "updated_at": "2026-08-01T00:00:00Z"},
                        {"doc_id": "new", "title": "New", "updated_at": "2026-08-24T00:00:00Z"},
                    ]
                }
            }
        ],
        ("docs", "+fetch"): [
            {
                "data": {
                    "document": {
                        "document_id": "new",
                        "title": "New",
                        "updated_at": "2026-08-24T00:00:00Z",
                        "content": "new body",
                    }
                }
            }
        ],
    }
    calls = _fake_cli(monkeypatch, responses)
    service = FeishuFetchService(
        feishu_config(
            query="knowledge",
            max_items_per_run=1,
            time_range={"mode": "recent", "recent_days": 3},
        ),
        home=tmp_path,
    )

    items = _items(
        await _batches(
            service,
            run_started_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
    )

    assert [item.logical_id for item in items] == ["feishu:doc:new"]
    fetch_calls = [argv for argv in calls if argv[:2] == ["docs", "+fetch"]]
    assert len(fetch_calls) == 1
    assert fetch_calls[0][fetch_calls[0].index("--doc") + 1] == "new"


@pytest.mark.asyncio
async def test_tasks_and_calendar_use_resource_specific_times(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: dict[tuple[str, ...], list[object]] = {
        ("auth", "status"): [
            {
                "identities": {
                    "user": {
                        "available": True,
                        "tokenStatus": "valid",
                        "scope": "task:task:read calendar:calendar.event:read",
                    }
                }
            }
        ],
        ("task", "+get-my-tasks"): [
            {
                "data": {
                    "items": [
                        {"guid": "old-task", "summary": "Old", "updated_at": "2026-08-01T00:00:00Z"},
                        {"guid": "new-task", "summary": "New", "created_at": "2026-08-24T01:00:00Z"},
                    ]
                }
            }
        ],
        ("calendar", "+agenda"): [
            {
                "data": {
                    "items": [
                        {"event_id": "old-event", "summary": "Old", "start": "2026-08-01T00:00:00Z"},
                        {"event_id": "new-event", "summary": "New", "start": {"date_time": "2026-08-24T02:00:00Z"}},
                    ]
                }
            }
        ],
    }
    _fake_cli(monkeypatch, responses)
    service = FeishuFetchService(
        feishu_config(
            resources=["tasks", "calendar"],
            time_range={"mode": "recent", "recent_days": 3},
        ),
        home=tmp_path,
    )

    items = _items(await _batches(service, run_started_at=datetime(2026, 8, 25, tzinfo=UTC)))

    assert {item.logical_id for item in items} == {"feishu:task:new-task", "feishu:calendar:new-event"}


@pytest.mark.asyncio
async def test_wiki_uses_node_update_time_before_fetching_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
                "data": {
                    "nodes": [
                        {
                            "node_token": "old",
                            "obj_token": "doc-old",
                            "obj_type": "docx",
                            "title": "Old",
                            "updated_at": "2026-08-01T00:00:00Z",
                        },
                        {
                            "node_token": "new",
                            "obj_token": "doc-new",
                            "obj_type": "docx",
                            "title": "New",
                            "updated_at": "2026-08-24T00:00:00Z",
                        },
                    ]
                }
            }
        ],
        ("docs", "+fetch"): [{"data": {"document": {"document_id": "doc-new", "content": "new body"}}}],
    }
    calls = _fake_cli(monkeypatch, responses)
    service = FeishuFetchService(
        feishu_config(
            mode="wiki_space",
            wiki_space_id="space-1",
            time_range={"mode": "recent", "recent_days": 3},
        ),
        home=tmp_path,
    )

    items = _items(await _batches(service, run_started_at=datetime(2026, 8, 25, tzinfo=UTC)))

    assert [item.logical_id for item in items] == ["feishu:wiki:space-1:new"]
    assert len([argv for argv in calls if argv[:2] == ["docs", "+fetch"]]) == 1


@pytest.mark.asyncio
async def test_filtered_resource_with_missing_time_fails_whole_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: dict[tuple[str, ...], list[object]] = {
        ("auth", "status"): [
            {
                "identities": {
                    "user": {
                        "available": True,
                        "tokenStatus": "valid",
                        "scope": "task:task:read",
                    }
                }
            }
        ],
        ("task", "+get-my-tasks"): [{"data": {"items": [{"guid": "task-1", "summary": "No time"}]}}],
    }
    _fake_cli(monkeypatch, responses)
    service = FeishuFetchService(
        feishu_config(resources=["tasks"], time_range={"mode": "recent", "recent_days": 3}),
        home=tmp_path,
    )

    with pytest.raises(BaseError, match="time"):
        await _batches(service, run_started_at=datetime(2026, 8, 25, tzinfo=UTC))


@pytest.mark.asyncio
async def test_empty_accessible_resource_list_is_successful_empty_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: dict[tuple[str, ...], list[object]] = {
        ("auth", "status"): [
            {
                "identities": {
                    "user": {
                        "available": True,
                        "tokenStatus": "valid",
                        "scope": "task:task:read",
                    }
                }
            }
        ],
        ("task", "+get-my-tasks"): [{"data": {"items": []}}],
    }
    _fake_cli(monkeypatch, responses)

    batches = await _batches(FeishuFetchService(feishu_config(resources=["tasks"]), home=tmp_path))

    assert len(batches) == 1
    assert batches[0].items == ()


@pytest.mark.asyncio
async def test_pagination_uses_nonempty_items_and_forward_token_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            {"data": {"items": [{"doc_id": "doc-1", "content": "one"}], "page_token": "next"}},
            {"data": {"items": []}},
        ],
    }
    calls = _fake_cli(monkeypatch, responses)

    items = _items(await _batches(FeishuFetchService(feishu_config(query="x"), home=tmp_path)))

    assert [item.logical_id for item in items] == ["feishu:doc:doc-1"]
    assert len([argv for argv in calls if argv[:2] == ["docs", "+search"]]) == 2


@pytest.mark.asyncio
async def test_pagination_rejects_repeated_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            {"data": {"items": [{"doc_id": "doc-1", "content": "one"}], "page_token": "same"}},
            {"data": {"items": [{"doc_id": "doc-2", "content": "two"}], "page_token": "same"}},
        ],
    }
    _fake_cli(monkeypatch, responses)

    with pytest.raises(BaseError, match="token"):
        await _batches(FeishuFetchService(feishu_config(query="x"), home=tmp_path))
