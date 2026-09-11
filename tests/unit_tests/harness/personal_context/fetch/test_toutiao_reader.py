from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import aiohttp
import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch import retry as retry_module
from openjiuwen.harness.personal_context.fetch.cursor_selection import record_completed_candidates
from openjiuwen.harness.personal_context.fetch.toutiao_reader import ToutiaoReaderFetchService
from openjiuwen.harness.personal_context.status_codes import StatusCode


def _config(
    *,
    max_items: int | None = None,
    time_range: dict[str, object] | None = None,
) -> PersonalContextFetchServiceConfig:
    return PersonalContextFetchServiceConfig(
        service_id="toutiao",
        provider="toutiao_reader",
        enabled=True,
        interval_seconds=60,
        max_items_per_run=max_items,
        time_range=time_range or {"mode": "all"},
        source={"profile_url": "https://www.toutiao.com/c/user/token/demo"},
        credentials={},
    )


def test_toutiao_profile_token_accepts_base64_padding() -> None:
    import openjiuwen.harness.personal_context.fetch.toutiao_reader as module

    assert module._profile_token("https://www.toutiao.com/c/user/token/demo==") == "demo=="


class Response:
    def __init__(self, payload: object, *, status: int = 200, content_type: str = "application/json") -> None:
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.url = "https://www.toutiao.com"
        if isinstance(payload, bytes):
            self._body = payload
        elif content_type == "text/html":
            self._body = str(payload).encode()
        else:
            self._body = json.dumps(payload, ensure_ascii=False).encode()
        self.content = self

    async def __aenter__(self) -> "Response":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def iter_chunked(self, _size: int):
        yield self._body

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=SimpleNamespace(real_url=self.url),
                history=(),
                status=self.status,
            )


class Session:
    responses: dict[str, list[Response]] = {}
    calls: list[tuple[str, dict[str, object]]] = []
    request_kwargs: list[tuple[str, dict[str, object]]] = []
    init_kwargs: list[dict[str, object]] = []

    def __init__(self, *, timeout: object | None = None, **kwargs: object) -> None:
        self.timeout_total = getattr(timeout, "total", None)
        type(self).init_kwargs.append(dict(kwargs))

    async def __aenter__(self) -> "Session":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> Response:
        params = kwargs.get("params")
        type(self).calls.append((url, dict(params) if isinstance(params, dict) else {}))
        type(self).request_kwargs.append((url, dict(kwargs)))
        values = type(self).responses.get(url) or type(self).responses.get(url.split("?", 1)[0])
        if not values:
            raise AssertionError(f"unexpected URL: {url}")
        return values.pop(0)


def _article(article_id: str, published: int) -> dict[str, object]:
    return {
        "item_id": article_id,
        "title": f"Title {article_id}",
        "publish_time": published,
        "abstract": f"Abstract {article_id}",
        "article_url": f"https://www.toutiao.com/article/{article_id}/",
    }


def _set_responses(monkeypatch: pytest.MonkeyPatch, responses: dict[str, list[Response]]) -> None:
    import openjiuwen.harness.personal_context.fetch.toutiao_reader as module

    Session.responses = {key: list(values) for key, values in responses.items()}
    profile_url = "https://www.toutiao.com/c/user/token/demo"
    if len(Session.responses.get(profile_url, [])) == 1:
        Session.responses[profile_url].append(Session.responses[profile_url][0])
    Session.calls = []
    Session.request_kwargs = []
    Session.init_kwargs = []
    monkeypatch.setattr(module.aiohttp, "ClientSession", Session)


async def _no_retry_sleep(_delay: float) -> None:
    return None


async def _batches(
    service: ToutiaoReaderFetchService,
    cursor: dict[str, object] | None = None,
    *,
    run_started_at: datetime | None = None,
):
    candidates = await service.prepare_run(
        run_id="run-1",
        run_started_at=run_started_at or datetime.now(UTC),
        cursor=cursor,
    )
    batches = [
        batch
        async for batch in service.fetch(
            run_id="run-1",
            cursor=cursor,
            candidates=candidates,
        )
    ]
    if batches:
        committed = record_completed_candidates(batches[-1].next_cursor, candidates)
        batches[-1] = batches[-1].model_copy(update={"next_cursor": committed})
    return batches


@pytest.mark.asyncio
async def test_toutiao_feed_retries_503_without_duplicate_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_url = "https://www.toutiao.com/c/user/token/demo"
    feed_url = "https://www.toutiao.com/api/pc/feed/"
    article_url = "https://www.toutiao.com/article/a/"
    _set_responses(
        monkeypatch,
        {
            profile_url: [Response("<html>profile</html>", content_type="text/html")],
            feed_url: [Response({}, status=503), Response({"data": [_article("a", 10)]})],
            article_url: [
                Response('<script id="RENDER_DATA">{"data":{"content":"body"}}</script>', content_type="text/html")
            ],
        },
    )
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)

    batches = await _batches(ToutiaoReaderFetchService(_config(), home=tmp_path))

    assert sum(url == feed_url for url, _params in Session.calls) == 2
    assert [item.logical_id for batch in batches for item in batch.items] == ["toutiao_reader:article:a"]


@pytest.mark.asyncio
async def test_toutiao_feed_does_not_retry_http_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile_url = "https://www.toutiao.com/c/user/token/demo"
    feed_url = "https://www.toutiao.com/api/pc/feed/"
    _set_responses(
        monkeypatch,
        {
            profile_url: [Response("<html>profile</html>", content_type="text/html")],
            feed_url: [Response({}, status=404)],
        },
    )
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)

    with pytest.raises(BaseError):
        await _batches(ToutiaoReaderFetchService(_config(), home=tmp_path))

    assert sum(url == feed_url for url, _params in Session.calls) == 1


@pytest.mark.asyncio
async def test_toutiao_reads_public_profile_pages_body_sorts_and_builds_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    article_a = _article("a", 10)
    article_b = _article("b", 20)
    article_a.pop("abstract")
    article_b.pop("abstract")
    _set_responses(
        monkeypatch,
        {
            "https://www.toutiao.com/c/user/token/demo": [
                Response('<script id="RENDER_DATA">{"data":{"name":"Demo"}}</script>', content_type="text/html")
            ],
            "https://www.toutiao.com/api/pc/feed/": [
                Response({"data": [article_a, article_b]}),
            ],
            "https://www.toutiao.com/article/a/": [
                Response(
                    '<script id="RENDER_DATA">{"data":{"content":"<p>A body</p>"}}</script>', content_type="text/html"
                )
            ],
            "https://www.toutiao.com/article/b/": [
                Response(
                    '<script id="RENDER_DATA">{"data":{"content":"<p>B body</p>"}}</script>', content_type="text/html"
                )
            ],
        },
    )
    service = ToutiaoReaderFetchService(_config(), home=tmp_path)
    batches = await _batches(service)
    items = [item for batch in batches for item in batch.items]
    assert [item.logical_id for item in items] == ["toutiao_reader:article:b", "toutiao_reader:article:a"]
    assert items[0].content is not None and "B body" in items[0].content
    assert items[0].revision_id == hashlib.sha256("B body".encode()).hexdigest()
    selection = batches[-1].next_cursor["_selection"]
    assert isinstance(selection, dict)
    assert [receipt["stable_id"] for receipt in selection["completed"]] == ["b", "a"]
    assert selection["latest_seen_time"] == "1970-01-01T00:00:20Z"


@pytest.mark.asyncio
async def test_toutiao_prefers_article_detail_over_inline_feed_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    article = _article("detail-first", 20)
    article["content"] = "one sentence feed preview"
    _set_responses(
        monkeypatch,
        {
            "https://www.toutiao.com/c/user/token/demo": [
                Response('<script id="RENDER_DATA">{"data":{"name":"Demo"}}</script>', content_type="text/html")
            ],
            "https://www.toutiao.com/api/pc/feed/": [Response({"data": [article]})],
            "https://www.toutiao.com/article/detail-first/": [
                Response(
                    '<script id="RENDER_DATA">'
                    + quote('{"data":{"content":"<p>complete article body with technical details</p>"}}')
                    + "</script>",
                    content_type="text/html",
                )
            ],
        },
    )

    item = (await _batches(ToutiaoReaderFetchService(_config(), home=tmp_path)))[0].items[0]

    assert item.content == "complete article body with technical details"
    assert item.metadata["content_fallback"] is False


@pytest.mark.asyncio
async def test_toutiao_reuses_public_profile_session_cookie_and_referer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article = _article("new", 20)
    article["abstract"] = "new abstract"
    _set_responses(
        monkeypatch,
        {
            "https://www.toutiao.com/c/user/token/demo": [
                Response("<!doctype html><html><title>今日头条</title></html>", content_type="text/html")
            ],
            "https://www.toutiao.com/api/pc/feed/": [Response({"data": [article]})],
        },
    )
    service = ToutiaoReaderFetchService(_config(), home=tmp_path)

    await _batches(service)

    assert len(Session.init_kwargs) == 2
    assert all(kwargs["cookie_jar"].__class__.__name__ != "DummyCookieJar" for kwargs in Session.init_kwargs)
    profile_calls = [
        call for call in Session.request_kwargs if call[0].startswith("https://www.toutiao.com/c/user/token/demo?wid=")
    ]
    feed_calls = [call for call in Session.request_kwargs if call[0] == "https://www.toutiao.com/api/pc/feed/"]
    assert len(profile_calls) == 2
    assert len(feed_calls) == 1
    profile_url, profile_kwargs = profile_calls[0]
    feed_url, feed_kwargs = feed_calls[0]
    assert profile_url.startswith("https://www.toutiao.com/c/user/token/demo?wid=")
    # Redirects stay manual so every hop can be restricted to Toutiao HTTPS.
    assert profile_kwargs["allow_redirects"] is False
    assert feed_url == "https://www.toutiao.com/api/pc/feed/"
    assert feed_kwargs["headers"] == {"Referer": "https://www.toutiao.com/c/user/token/demo/"}


@pytest.mark.asyncio
async def test_toutiao_feed_is_authoritative_when_profile_bootstrap_body_is_unparseable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    article = _article("new", 20)
    article["abstract"] = "new abstract"
    _set_responses(
        monkeypatch,
        {
            "https://www.toutiao.com/c/user/token/demo": [Response(b"not-render-data")],
            "https://www.toutiao.com/api/pc/feed/": [Response({"data": [article]})],
        },
    )
    service = ToutiaoReaderFetchService(_config(), home=tmp_path)

    batches = await _batches(service)

    assert [item.logical_id for item in batches[0].items] == ["toutiao_reader:article:new"]


@pytest.mark.asyncio
async def test_toutiao_missing_recent_items_do_not_create_deletes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    profile_url = "https://www.toutiao.com/c/user/token/demo"
    feed_url = "https://www.toutiao.com/api/pc/feed/"
    _set_responses(
        monkeypatch,
        {
            profile_url: [Response({"data": {"name": "Demo"}})],
            feed_url: [Response({"data": [_article("old", 10)]})],
        },
    )
    service = ToutiaoReaderFetchService(_config(), home=tmp_path)
    first = await _batches(service)
    _set_responses(
        monkeypatch,
        {
            profile_url: [
                Response('<script id="RENDER_DATA">{"data":{"name":"Demo"}}</script>', content_type="text/html")
            ],
            feed_url: [Response({"data": [_article("new", 20)]})],
            "https://www.toutiao.com/article/new/": [
                Response('<script id="RENDER_DATA">{"data":{"content":"new body"}}</script>', content_type="text/html")
            ],
        },
    )
    batches = await _batches(service, first[-1].next_cursor)
    assert [item.operation for item in batches[0].items] == ["upsert"]


@pytest.mark.asyncio
async def test_toutiao_generic_profile_html_shell_uses_feed_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    article = _article("new", 20)
    article["abstract"] = "new abstract body"
    _set_responses(
        monkeypatch,
        {
            "https://www.toutiao.com/c/user/token/demo": [
                Response("<!doctype html><html><title>今日头条</title></html>", content_type="text/html")
            ],
            "https://www.toutiao.com/api/pc/feed/": [Response({"data": [article]})],
        },
    )
    service = ToutiaoReaderFetchService(_config(), home=tmp_path)

    batches = await _batches(service)

    assert len(batches[0].items) == 1
    assert batches[0].items[0].logical_id == "toutiao_reader:article:new"
    assert batches[0].items[0].content == "new abstract body"
    assert batches[0].items[0].metadata["content_fallback"] is True


@pytest.mark.asyncio
async def test_toutiao_profile_or_body_antibot_failure_does_not_advance_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _set_responses(
        monkeypatch,
        {
            "https://www.toutiao.com/c/user/token/demo": [Response("captcha", content_type="text/html")],
        },
    )
    service = ToutiaoReaderFetchService(_config(), home=tmp_path)
    with pytest.raises(BaseError) as caught:
        await _batches(service)
    assert caught.value.status is StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR
    assert "toutiao.com" not in str(caught.value)
    assert "captcha" not in str(caught.value).casefold()


@pytest.mark.asyncio
async def test_toutiao_default_limit_is_twenty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    articles = [_article(str(index), index) for index in range(25)]
    responses = {
        "https://www.toutiao.com/c/user/token/demo": [
            Response('<script id="RENDER_DATA">{"data":{"name":"Demo"}}</script>', content_type="text/html")
        ],
        "https://www.toutiao.com/api/pc/feed/": [Response({"data": articles})],
    }
    responses.update(
        {
            f"https://www.toutiao.com/article/{index}/": [
                Response('<script id="RENDER_DATA">{"data":{"content":"body"}}</script>', content_type="text/html")
            ]
            for index in range(5, 25)
        }
    )
    _set_responses(monkeypatch, responses)
    service = ToutiaoReaderFetchService(_config(), home=tmp_path)
    batches = await _batches(service)
    assert sum(len(batch.items) for batch in batches) == 20
    assert all(len(batch.items) <= 20 for batch in batches)


@pytest.mark.asyncio
async def test_toutiao_stops_after_enough_candidates_without_a_pagination_boolean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = _article("newest", 20)
    newest["content"] = "newest body"
    older = _article("older", 10)
    _set_responses(
        monkeypatch,
        {
            "https://www.toutiao.com/c/user/token/demo": [
                Response('<script id="RENDER_DATA">{"data":{"name":"Demo"}}</script>', content_type="text/html")
            ],
            "https://www.toutiao.com/api/pc/feed/": [
                Response({"data": [newest, older], "next": {"max_behot_time": "1"}}),
                Response({"data": []}),
            ],
        },
    )
    service = ToutiaoReaderFetchService(_config(max_items=1), home=tmp_path)

    batches = await _batches(service)

    assert [item.logical_id for batch in batches for item in batch.items] == ["toutiao_reader:article:newest"]
    assert sum(url.endswith("/api/pc/feed/") for url, _ in Session.calls) == 1


@pytest.mark.asyncio
async def test_toutiao_empty_page_stops_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_url = "https://www.toutiao.com/c/user/token/demo"
    feed_url = "https://www.toutiao.com/api/pc/feed/"
    _set_responses(
        monkeypatch,
        {
            profile_url: [Response({"data": {"name": "Demo"}})],
            feed_url: [
                Response({"data": [_article("a", 2), _article("b", 1)], "next": {"max_behot_time": "1"}}),
                Response({"data": []}),
            ],
        },
    )
    service = ToutiaoReaderFetchService(_config(max_items=3), home=tmp_path)

    candidates = await service.prepare_run(
        run_id="empty",
        run_started_at=datetime.now(UTC),
        cursor=None,
    )

    assert [candidate["stable_id"] for candidate in candidates] == ["a", "b"]
    assert sum(url.endswith("/api/pc/feed/") for url, _ in Session.calls) == 2


@pytest.mark.asyncio
async def test_toutiao_uses_latest_published_or_updated_time_for_ranges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_started_at = datetime(2026, 8, 10, 12, tzinfo=UTC)
    end = int(run_started_at.timestamp())
    start = int((run_started_at - timedelta(days=3)).timestamp())
    updated = _article("updated", start - 100)
    updated["updated"] = end - 1
    boundary = _article("boundary", start)
    at_end = _article("at-end", end)
    profile_url = "https://www.toutiao.com/c/user/token/demo"
    feed_url = "https://www.toutiao.com/api/pc/feed/"
    _set_responses(
        monkeypatch,
        {
            profile_url: [Response({"data": {"name": "Demo"}})],
            feed_url: [Response({"data": [updated, boundary, at_end]})],
        },
    )
    recent = ToutiaoReaderFetchService(
        _config(max_items=2, time_range={"mode": "recent", "recent_days": 3}),
        home=tmp_path,
    )
    candidates = await recent.prepare_run(
        run_id="recent",
        run_started_at=run_started_at,
        cursor=None,
    )
    assert [candidate["stable_id"] for candidate in candidates] == ["at-end", "updated"]

    _set_responses(
        monkeypatch,
        {
            profile_url: [Response({"data": {"name": "Demo"}})],
            feed_url: [Response({"data": [updated, boundary, at_end]})],
        },
    )
    fixed = ToutiaoReaderFetchService(
        _config(
            time_range={
                "mode": "fixed",
                "start_at": datetime.fromtimestamp(start, tz=UTC).isoformat(),
                "end_at": run_started_at.isoformat(),
            }
        ),
        home=tmp_path,
    )
    fixed_candidates = await fixed.prepare_run(
        run_id="fixed",
        run_started_at=run_started_at,
        cursor=None,
    )
    assert {candidate["stable_id"] for candidate in fixed_candidates} == {"updated", "boundary"}


@pytest.mark.asyncio
async def test_toutiao_missing_time_fails_filtered_run_but_allows_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    article = {"item_id": "missing", "title": "Missing", "abstract": "body"}
    profile_url = "https://www.toutiao.com/c/user/token/demo"
    feed_url = "https://www.toutiao.com/api/pc/feed/"
    _set_responses(
        monkeypatch,
        {
            profile_url: [Response({"data": {"name": "Demo"}})],
            feed_url: [Response({"data": [article]})],
        },
    )
    filtered = ToutiaoReaderFetchService(
        _config(time_range={"mode": "recent", "recent_days": 3}),
        home=tmp_path,
    )
    with pytest.raises(BaseError):
        await filtered.prepare_run(run_id="filtered", run_started_at=datetime.now(UTC), cursor=None)

    _set_responses(
        monkeypatch,
        {
            profile_url: [Response({"data": {"name": "Demo"}})],
            feed_url: [Response({"data": [article]})],
        },
    )
    all_time = ToutiaoReaderFetchService(_config(), home=tmp_path)
    candidates = await all_time.prepare_run(run_id="all", run_started_at=datetime.now(UTC), cursor=None)
    assert candidates[0]["candidate_time"] == "1970-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_toutiao_nonempty_pages_exceeding_page_limit_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.toutiao_reader as module

    profile_url = "https://www.toutiao.com/c/user/token/demo"
    feed_url = "https://www.toutiao.com/api/pc/feed/"
    _set_responses(
        monkeypatch,
        {
            profile_url: [Response({"data": {"name": "Demo"}})],
            feed_url: [
                Response({"data": [_article("a", 2)], "next": {"max_behot_time": "1"}}),
                Response({"data": [_article("b", 1)], "next": {"max_behot_time": "2"}}),
            ],
        },
    )
    monkeypatch.setattr(module, "_MAX_PAGES", 2)
    service = ToutiaoReaderFetchService(_config(), home=tmp_path)
    with pytest.raises(BaseError):
        await service.prepare_run(run_id="limit", run_started_at=datetime.now(UTC), cursor=None)


@pytest.mark.asyncio
async def test_toutiao_new_overflow_advances_only_a_contiguous_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_url = "https://www.toutiao.com/c/user/token/demo"
    feed_url = "https://www.toutiao.com/api/pc/feed/"

    def install(articles: list[dict[str, object]]) -> None:
        _set_responses(
            monkeypatch,
            {
                profile_url: [Response({"data": {"name": "Demo"}})],
                feed_url: [Response({"data": articles})],
            },
        )

    initial = [_article("base-3", 3), _article("base-2", 2), _article("base-1", 1)]
    install(initial)
    service = ToutiaoReaderFetchService(_config(max_items=2), home=tmp_path)
    first = await _batches(service)
    cursor = first[-1].next_cursor
    assert cursor is not None

    changed = [*[_article(f"new-{timestamp}", timestamp) for timestamp in range(8, 3, -1)], *initial]
    round_ids: list[list[str]] = []
    for _ in range(3):
        install(changed)
        batches = await _batches(service, cursor)
        round_ids.append([item.logical_id for batch in batches for item in batch.items])
        cursor = batches[-1].next_cursor
        assert cursor is not None

    assert round_ids == [
        ["toutiao_reader:article:new-8", "toutiao_reader:article:new-7"],
        ["toutiao_reader:article:new-6", "toutiao_reader:article:new-5"],
        ["toutiao_reader:article:new-4", "toutiao_reader:article:base-1"],
    ]
    assert cursor["_selection"]["latest_seen_time"] == "1970-01-01T00:00:08Z"


@pytest.mark.asyncio
async def test_toutiao_same_timestamp_overflow_merges_only_processed_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_url = "https://www.toutiao.com/c/user/token/demo"
    feed_url = "https://www.toutiao.com/api/pc/feed/"

    def install(articles: list[dict[str, object]]) -> None:
        _set_responses(
            monkeypatch,
            {
                profile_url: [Response({"data": {"name": "Demo"}})],
                feed_url: [Response({"data": articles})],
            },
        )

    initial = [_article("base-3", 3), _article("base-2", 2), _article("base-1", 1)]
    install(initial)
    service = ToutiaoReaderFetchService(_config(max_items=2), home=tmp_path)
    first = await _batches(service)
    cursor = first[-1].next_cursor
    assert cursor is not None

    changed = [
        _article("new-c", 4),
        _article("new-b", 4),
        _article("new-a", 4),
        *initial,
    ]
    install(changed)
    second = await _batches(service, cursor)
    second_cursor = second[-1].next_cursor
    assert second_cursor is not None
    assert [item.logical_id for batch in second for item in batch.items] == [
        "toutiao_reader:article:new-a",
        "toutiao_reader:article:new-b",
    ]
    second_receipts = second_cursor["_selection"]["completed"]
    assert [receipt["stable_id"] for receipt in second_receipts[:2]] == ["new-a", "new-b"]

    install(changed)
    third = await _batches(service, second_cursor)
    third_cursor = third[-1].next_cursor
    assert third_cursor is not None
    assert [item.logical_id for batch in third for item in batch.items] == [
        "toutiao_reader:article:new-c",
        "toutiao_reader:article:base-1",
    ]
    same_time_ids = {
        receipt["stable_id"]
        for receipt in third_cursor["_selection"]["completed"]
        if receipt["candidate_time"] == "1970-01-01T00:00:04Z"
    }
    assert same_time_ids == {"new-a", "new-b", "new-c"}


@pytest.mark.asyncio
async def test_toutiao_batches_each_carry_a_temporary_cursor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    articles = [_article(str(index), index) for index in range(21)]
    responses = {
        "https://www.toutiao.com/c/user/token/demo": [
            Response('<script id="RENDER_DATA">{"data":{"name":"Demo"}}</script>', content_type="text/html")
        ],
        "https://www.toutiao.com/api/pc/feed/": [Response({"data": articles})],
    }
    responses.update(
        {
            f"https://www.toutiao.com/article/{index}/": [
                Response('<script id="RENDER_DATA">{"data":{"content":"body"}}</script>', content_type="text/html")
            ]
            for index in range(21)
        }
    )
    _set_responses(monkeypatch, responses)
    service = ToutiaoReaderFetchService(_config(max_items=21), home=tmp_path)

    batches = await _batches(service)

    assert [len(batch.items) for batch in batches] == [20, 1]
    assert all(isinstance(batch.next_cursor, dict) for batch in batches)
    assert batches[0].next_cursor != batches[1].next_cursor


@pytest.mark.asyncio
async def test_toutiao_rejects_cursor_from_another_source_or_with_wrong_types(tmp_path: Path):
    service = ToutiaoReaderFetchService(_config(), home=tmp_path)
    invalid_cursors: tuple[dict[str, object], ...] = (
        {
            "source_url": "https://www.toutiao.com/c/user/token/other",
            "latest_timestamp": 1.0,
            "latest_timestamp_ids": [],
            "history_before_timestamp": None,
            "history_boundary_ids": [],
            "history_complete": False,
        },
        {
            "source_url": "https://www.toutiao.com/c/user/token/demo",
            "latest_timestamp": "not-a-number",
            "latest_timestamp_ids": [],
            "history_before_timestamp": None,
            "history_boundary_ids": [],
            "history_complete": False,
        },
        {
            "source_url": "https://www.toutiao.com/c/user/token/demo",
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


@pytest.mark.asyncio
async def test_toutiao_profile_error_token_loop_and_missing_token_handling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_responses(
        monkeypatch,
        {
            "https://www.toutiao.com/c/user/token/demo": [Response({"error": "captcha"})],
        },
    )
    service = ToutiaoReaderFetchService(_config(), home=tmp_path)
    with pytest.raises(BaseError):
        await _batches(service)

    _set_responses(
        monkeypatch,
        {
            "https://www.toutiao.com/c/user/token/demo": [
                Response({"data": {"name": "Demo"}}),
            ],
            "https://www.toutiao.com/api/pc/feed/": [
                Response({"data": [_article("a", 10)], "next": {"max_behot_time": "1"}}),
                Response({"data": [_article("b", 9)], "next": {"max_behot_time": "1"}}),
            ],
        },
    )
    with pytest.raises(BaseError):
        await _batches(service)

    _set_responses(
        monkeypatch,
        {
            "https://www.toutiao.com/c/user/token/demo": [
                Response({"data": {"name": "Demo"}}),
            ],
            "https://www.toutiao.com/api/pc/feed/": [
                Response({"data": [_article("a", 10)]}),
            ],
        },
    )
    batches = await _batches(service)
    assert [item.logical_id for batch in batches for item in batch.items] == ["toutiao_reader:article:a"]


def test_toutiao_timestamp_number_rejects_non_finite_values() -> None:
    import openjiuwen.harness.personal_context.fetch.toutiao_reader as module

    assert module._timestamp_number(float("inf")) == 0.0
    assert module._timestamp_number("1e9999") == 0.0


@pytest.mark.asyncio
async def test_toutiao_marks_content_and_raw_snapshot_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import openjiuwen.harness.personal_context.fetch.toutiao_reader as module

    article = _article("huge", 1)
    article["content"] = "x" * (module._MAX_RAW_BYTES + 1)
    _set_responses(
        monkeypatch,
        {
            "https://www.toutiao.com/c/user/token/demo": [
                Response({"data": {"name": "Demo"}}),
            ],
            "https://www.toutiao.com/api/pc/feed/": [
                Response({"data": [article]}),
            ],
        },
    )
    service = ToutiaoReaderFetchService(_config(), home=tmp_path)
    batches = await _batches(service)
    item = batches[0].items[0]
    assert item.metadata["content_truncated"] is True
    assert item.metadata["raw_snapshot_omitted"] is True


@pytest.mark.asyncio
async def test_toutiao_articles_continue_history_and_prioritize_new_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_url = "https://www.toutiao.com/c/user/token/demo"
    feed_url = "https://www.toutiao.com/api/pc/feed/"

    def article(index: int, timestamp: int, *, updated: int | None = None) -> dict[str, object]:
        value = _article(f"article-{index:03}", timestamp)
        if updated is not None:
            value["updated"] = updated
        return value

    def install_responses(articles: list[dict[str, object]], *, repeats: int = 4) -> None:
        responses = {
            profile_url: [Response({"data": {"name": "Demo"}}) for _ in range(repeats)],
            feed_url: [Response({"data": articles}) for _ in range(repeats)],
        }
        for record in articles:
            article_id = str(record["item_id"])
            responses[f"https://www.toutiao.com/article/{article_id}/"] = [
                Response({"data": {"content": f"Body {article_id}"}}) for _ in range(repeats)
            ]
        _set_responses(monkeypatch, responses)

    initial = [article(index, 205 - index) for index in range(205)]
    install_responses(initial, repeats=8)
    service = ToutiaoReaderFetchService(_config(max_items=100), home=tmp_path / "home")
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
    assert set(logical_ids) == {f"toutiao_reader:article:article-{index:03}" for index in range(205)}
    assert cursor is not None
    assert set(cursor) == {"_selection"}
    assert len(cursor["_selection"]["completed"]) == 205

    install_responses(initial, repeats=4)
    priority_service = ToutiaoReaderFetchService(_config(max_items=100), home=tmp_path / "priority-home")
    first = await _batches(priority_service)
    first_items = [item for batch in first for item in batch.items]
    first_cursor = first[-1].next_cursor
    assert first_cursor is not None

    changed = [article(index, 205 - index) for index in range(205)]
    changed[99] = article(99, 106, updated=1_000)
    changed.extend([article(1000, 1_001), article(1001, 1_002)])
    install_responses(changed, repeats=4)
    second = await _batches(priority_service, first_cursor)
    second_items = [item for batch in second for item in batch.items]
    assert len(second_items) == 100
    assert [item.logical_id for item in second_items[:3]] == [
        "toutiao_reader:article:article-1001",
        "toutiao_reader:article:article-1000",
        "toutiao_reader:article:article-099",
    ]
    assert second_items[2].logical_id == first_items[99].logical_id
