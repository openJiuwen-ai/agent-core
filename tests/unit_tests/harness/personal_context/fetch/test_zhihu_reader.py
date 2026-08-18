from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch.zhihu_reader import ZhihuReaderFetchService
from openjiuwen.harness.personal_context.status_codes import StatusCode


def _config(*, max_items: int | None = None) -> PersonalContextFetchServiceConfig:
    return PersonalContextFetchServiceConfig(
        service_id="zhihu",
        provider="zhihu_reader",
        enabled=True,
        interval_seconds=60,
        max_items_per_run=max_items,
        source={"column_url": "https://www.zhihu.com/column/example"},
        credentials={},
    )


class Response:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self.url = "https://www.zhihu.com"
        self._body = json.dumps(payload, ensure_ascii=False).encode()
        self.content = self

    async def __aenter__(self) -> "Response":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def iter_chunked(self, _size: int):
        yield self._body


class Session:
    responses: dict[str, list[Response]] = {}
    calls: list[tuple[str, dict[str, object]]] = []
    timeout_total: float | None = None

    def __init__(self, *, timeout: object | None = None, **_kwargs: object) -> None:
        self.timeout_total = getattr(timeout, "total", None)
        type(self).timeout_total = self.timeout_total

    async def __aenter__(self) -> "Session":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> Response:
        params = kwargs.get("params")
        type(self).calls.append((url, dict(params) if isinstance(params, dict) else {}))
        values = type(self).responses.get(url)
        if not values:
            raise AssertionError(f"unexpected URL: {url}")
        return values.pop(0)


def _article(article_id: str, published: int, *, updated: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "id": article_id,
        "title": f"Title {article_id}",
        "published": published,
        "updated": updated if updated is not None else published,
        "url": f"https://zhuanlan.zhihu.com/p/{article_id}",
    }
    return result


def _set_responses(monkeypatch: pytest.MonkeyPatch, responses: dict[str, list[Response]]) -> None:
    import openjiuwen.harness.personal_context.fetch.zhihu_reader as module

    Session.responses = responses
    Session.calls = []
    Session.timeout_total = None
    monkeypatch.setattr(module.aiohttp, "ClientSession", Session)


async def _batches(service: ZhihuReaderFetchService, cursor: dict[str, object] | None = None):
    return [batch async for batch in service.fetch(run_id="run-1", cursor=cursor)]


@pytest.mark.asyncio
async def test_zhihu_paginates_reads_body_sorts_and_builds_cursor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _set_responses(
        monkeypatch,
        {
            "https://www.zhihu.com/api/v4/columns/example/articles": [
                Response({"data": [_article("a", 10), _article("b", 20)], "paging": {"is_end": False}}),
                Response({"data": [_article("c", 30)], "paging": {"is_end": True}}),
            ],
            "https://www.zhihu.com/api/v4/articles/a": [Response({"data": {"content": "<p>A body</p>"}})],
            "https://www.zhihu.com/api/v4/articles/b": [Response({"data": {"content": "<p>B body</p>"}})],
            "https://www.zhihu.com/api/v4/articles/c": [Response({"data": {"content": "<p>C body</p>"}})],
        },
    )
    service = ZhihuReaderFetchService(_config(), home=tmp_path)

    batches = await _batches(service)
    items = [item for batch in batches for item in batch.items]

    assert [item.logical_id for item in items] == [
        "zhihu_reader:article:c",
        "zhihu_reader:article:b",
        "zhihu_reader:article:a",
    ]
    assert items[0].content is not None and "C body" in items[0].content
    assert items[0].revision_id == "30"
    assert batches[-1].next_cursor == {
        "source_url": "https://www.zhihu.com/column/example",
        "latest_timestamp": 30.0,
        "latest_timestamp_ids": ["c"],
        "history_before_timestamp": 10.0,
        "history_boundary_ids": ["a"],
        "history_complete": True,
    }
    assert Session.timeout_total == 30


@pytest.mark.asyncio
async def test_zhihu_updates_same_article_without_inventing_deletes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _set_responses(
        monkeypatch,
        {
            "https://www.zhihu.com/api/v4/columns/example/articles": [
                Response({"data": [_article("a", 10, updated=20)], "paging": {"is_end": True}}),
            ],
            "https://www.zhihu.com/api/v4/articles/a": [Response({"data": {"content": "updated"}})],
        },
    )
    service = ZhihuReaderFetchService(_config(), home=tmp_path)
    batches = await _batches(
        service,
        {
            "source_url": "https://www.zhihu.com/column/example",
            "latest_timestamp": 10.0,
            "latest_timestamp_ids": ["a"],
            "history_before_timestamp": None,
            "history_boundary_ids": [],
            "history_complete": False,
        },
    )
    assert len(batches[0].items) == 1
    assert batches[0].items[0].revision_id == "20"
    assert all(item.operation != "delete" for item in batches[0].items)


@pytest.mark.asyncio
async def test_zhihu_body_or_antibot_failure_aborts_without_cursor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _set_responses(
        monkeypatch,
        {
            "https://www.zhihu.com/api/v4/columns/example/articles": [
                Response({"data": [_article("a", 10)], "paging": {"is_end": True}}),
            ],
            "https://www.zhihu.com/api/v4/articles/a": [Response("captcha challenge")],
        },
    )
    service = ZhihuReaderFetchService(_config(), home=tmp_path)
    with pytest.raises(BaseError) as caught:
        await _batches(service)
    assert caught.value.status is StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR
    assert "zhihu.com" not in str(caught.value)
    assert "captcha" not in str(caught.value).casefold()


@pytest.mark.asyncio
async def test_zhihu_default_limit_is_twenty_and_response_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    articles = [_article(str(index), index) for index in range(25)]
    responses = {
        "https://www.zhihu.com/api/v4/columns/example/articles": [
            Response({"data": articles, "paging": {"is_end": True}}),
        ],
    }
    responses.update(
        {
            f"https://www.zhihu.com/api/v4/articles/{index}": [Response({"data": {"content": "body"}})]
            for index in range(5, 25)
        }
    )
    _set_responses(monkeypatch, responses)
    service = ZhihuReaderFetchService(_config(), home=tmp_path)
    batches = await _batches(service)
    assert sum(len(batch.items) for batch in batches) == 20
    assert all(len(batch.items) <= 20 for batch in batches)


@pytest.mark.asyncio
async def test_zhihu_scans_pagination_beyond_run_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    newest = _article("newest", 20)
    newest["content"] = "newest body"
    older = _article("older", 10)
    _set_responses(
        monkeypatch,
        {
            "https://www.zhihu.com/api/v4/columns/example/articles": [
                Response({"data": [newest, older], "paging": {"is_end": False}}),
                Response({"data": [], "paging": {"is_end": True}}),
            ],
        },
    )
    service = ZhihuReaderFetchService(_config(max_items=1), home=tmp_path)

    batches = await _batches(service)

    assert [item.logical_id for batch in batches for item in batch.items] == ["zhihu_reader:article:newest"]
    assert len(Session.calls) == 2


@pytest.mark.asyncio
async def test_zhihu_batches_each_carry_a_temporary_cursor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    articles = [_article(str(index), index) for index in range(21)]
    responses = {
        "https://www.zhihu.com/api/v4/columns/example/articles": [
            Response({"data": articles, "paging": {"is_end": True}}),
        ],
    }
    responses.update(
        {
            f"https://www.zhihu.com/api/v4/articles/{index}": [Response({"data": {"content": "body"}})]
            for index in range(21)
        }
    )
    _set_responses(monkeypatch, responses)
    service = ZhihuReaderFetchService(_config(max_items=21), home=tmp_path)

    batches = await _batches(service)

    assert [len(batch.items) for batch in batches] == [20, 1]
    assert all(isinstance(batch.next_cursor, dict) for batch in batches)
    assert batches[0].next_cursor != batches[1].next_cursor


@pytest.mark.asyncio
async def test_zhihu_rejects_cursor_from_another_source_or_with_wrong_types(tmp_path: Path):
    service = ZhihuReaderFetchService(_config(), home=tmp_path)
    invalid_cursors: tuple[dict[str, object], ...] = (
        {
            "source_url": "https://www.zhihu.com/column/other",
            "latest_timestamp": 1.0,
            "latest_timestamp_ids": [],
            "history_before_timestamp": None,
            "history_boundary_ids": [],
            "history_complete": False,
        },
        {
            "source_url": "https://www.zhihu.com/column/example",
            "latest_timestamp": "not-a-number",
            "latest_timestamp_ids": [],
            "history_before_timestamp": None,
            "history_boundary_ids": [],
            "history_complete": False,
        },
        {
            "source_url": "https://www.zhihu.com/column/example",
            "latest_timestamp": 1.0,
            "latest_timestamp_ids": [],
            "history_before_timestamp": None,
            "history_boundary_ids": [],
            "history_complete": False,
            "unexpected": True,
        },
    )
    for cursor in invalid_cursors:
        with pytest.raises(BaseError):
            await _batches(service, cursor)


def test_zhihu_timestamp_number_rejects_non_finite_values() -> None:
    import openjiuwen.harness.personal_context.fetch.zhihu_reader as module

    assert module._timestamp_number(float("inf")) == 0.0
    assert module._timestamp_number("1e9999") == 0.0


@pytest.mark.asyncio
async def test_zhihu_marks_content_and_raw_snapshot_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import openjiuwen.harness.personal_context.fetch.zhihu_reader as module

    article = _article("huge", 1)
    article["content"] = "x" * (module._MAX_RAW_BYTES + 1)
    _set_responses(
        monkeypatch,
        {
            "https://www.zhihu.com/api/v4/columns/example/articles": [
                Response({"data": [article], "paging": {"is_end": True}}),
            ],
        },
    )
    service = ZhihuReaderFetchService(_config(), home=tmp_path)
    batches = await _batches(service)
    item = batches[0].items[0]
    assert item.metadata["content_truncated"] is True
    assert item.metadata["raw_snapshot_omitted"] is True


@pytest.mark.asyncio
async def test_zhihu_articles_continue_history_and_prioritize_new_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def article(index: int, timestamp: int, *, updated: int | None = None) -> dict[str, object]:
        value = _article(f"article-{index:03}", timestamp, updated=updated)
        value["content"] = f"Body article-{index:03}"
        return value

    initial = [article(index, 205 - index) for index in range(205)]
    _set_responses(
        monkeypatch,
        {
            "https://www.zhihu.com/api/v4/columns/example/articles": [
                Response({"data": initial, "paging": {"is_end": True}}),
                Response({"data": initial, "paging": {"is_end": True}}),
                Response({"data": initial, "paging": {"is_end": True}}),
            ]
        },
    )
    service = ZhihuReaderFetchService(_config(max_items=100), home=tmp_path / "home")
    cursor: dict[str, object] | None = None
    rounds = []
    for _run_id in ("run-a", "run-b", "run-c"):
        batches = await _batches(service, cursor)
        rounds.append([item for batch in batches for item in batch.items])
        assert batches
        cursor = batches[-1].next_cursor

    assert [len(items) for items in rounds] == [100, 100, 5]
    logical_ids = [item.logical_id for items in rounds for item in items]
    assert len(logical_ids) == len(set(logical_ids)) == 205
    assert set(logical_ids) == {f"zhihu_reader:article:article-{index:03}" for index in range(205)}
    assert cursor is not None
    assert set(cursor) == {
        "source_url",
        "latest_timestamp",
        "latest_timestamp_ids",
        "history_before_timestamp",
        "history_boundary_ids",
        "history_complete",
    }
    assert cursor["source_url"] == "https://www.zhihu.com/column/example"
    assert isinstance(cursor["latest_timestamp"], float)
    assert isinstance(cursor["latest_timestamp_ids"], list)
    assert cursor["history_before_timestamp"] is None or isinstance(cursor["history_before_timestamp"], float)
    assert isinstance(cursor["history_boundary_ids"], list)
    assert cursor["history_complete"] is True

    priority_initial = initial
    _set_responses(
        monkeypatch,
        {
            "https://www.zhihu.com/api/v4/columns/example/articles": [
                Response({"data": priority_initial, "paging": {"is_end": True}}),
            ]
        },
    )
    priority_service = ZhihuReaderFetchService(_config(max_items=100), home=tmp_path / "priority-home")
    first = await _batches(priority_service)
    first_items = [item for batch in first for item in batch.items]
    first_cursor = first[-1].next_cursor
    assert first_cursor is not None

    changed = [article(index, 205 - index) for index in range(205)]
    changed[99] = article(99, 106, updated=1_000)
    changed.extend(
        [
            article(1000, 1_001),
            article(1001, 1_002),
        ]
    )
    _set_responses(
        monkeypatch,
        {
            "https://www.zhihu.com/api/v4/columns/example/articles": [
                Response({"data": changed, "paging": {"is_end": True}}),
            ]
        },
    )
    second = await _batches(priority_service, first_cursor)
    second_items = [item for batch in second for item in batch.items]
    assert len(second_items) == 100
    assert [item.logical_id for item in second_items[:3]] == [
        "zhihu_reader:article:article-1001",
        "zhihu_reader:article:article-1000",
        "zhihu_reader:article:article-099",
    ]
    assert second_items[2].logical_id == first_items[99].logical_id
