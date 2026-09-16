from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch import retry as retry_module
from openjiuwen.harness.personal_context.fetch.cursor_selection import record_completed_candidates
from openjiuwen.harness.personal_context.fetch.rss_feed import RssFeedFetchService
from openjiuwen.harness.personal_context.status_codes import StatusCode

FEED_URL = "https://example.com/feed.xml?topic=office"

RSS_FIXTURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>办公资讯</title>
    <item>
      <guid isPermaLink="false">guid-new</guid>
      <title>国内办公</title>
      <link>https://example.com/posts/new</link>
      <pubDate>Sat, 29 Aug 2026 08:00:00 +0800</pubDate>
      <author>作者甲</author>
      <description><![CDATA[<p>摘要</p><script>alert(1)</script>]]></description>
      <content:encoded><![CDATA[<div>正文一</div><style>bad</style>]]></content:encoded>
    </item>
    <item>
      <guid isPermaLink="false">guid-old</guid>
      <title>旧消息</title>
      <link>https://example.com/posts/old</link>
      <pubDate>Fri, 28 Aug 2026 08:00:00 +0800</pubDate>
      <description><![CDATA[<p>旧正文</p>]]></description>
    </item>
  </channel>
</rss>
"""

ATOM_FIXTURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom 办公</title>
  <entry>
    <id>atom-1</id>
    <title>Atom 更新</title>
    <link rel="alternate" href="https://example.com/atom/1" />
    <updated>2026-08-29T08:30:00+08:00</updated>
    <author><name>作者乙</name></author>
    <content type="html"><![CDATA[<p>Atom 正文</p><script>bad()</script>]]></content>
  </entry>
</feed>
"""

RDF_FIXTURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/">
  <channel rdf:about="https://example.com/">
    <title>RDF 办公</title>
    <link>https://example.com/</link>
  </channel>
  <item rdf:about="https://example.com/rdf/1">
    <title>RDF 更新</title>
    <link>https://example.com/rdf/1</link>
    <dc:date xmlns:dc="http://purl.org/dc/elements/1.1/">2026-08-29T08:00:00+08:00</dc:date>
    <description><![CDATA[<p>RDF 正文</p>]]></description>
  </item>
</rdf:RDF>
"""


class _Response:
    def __init__(
        self,
        payload: str | bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        url: str = FEED_URL,
    ) -> None:
        self.status = status
        self.headers = headers or {"Content-Type": "application/rss+xml"}
        self.url = url
        self._body = payload.encode("utf-8") if isinstance(payload, str) else payload
        self.content = self

    async def __aenter__(self) -> "_Response":
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


class _DisconnectResponse(_Response):
    async def __aenter__(self) -> "_Response":
        raise aiohttp.ClientConnectionError("connection reset")


class _Session:
    responses: list[_Response] = []
    calls: list[tuple[str, dict[str, object]]] = []
    timeout_total: float | None = None

    def __init__(self, *, timeout: object | None = None, **_kwargs: object) -> None:
        type(self).timeout_total = getattr(timeout, "total", None)

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> _Response:
        type(self).calls.append((url, kwargs))
        if not type(self).responses:
            raise AssertionError(f"unexpected URL: {url}")
        return type(self).responses.pop(0)


def _config(
    *,
    max_items: int | None = None,
    time_range: dict[str, object] | None = None,
) -> PersonalContextFetchServiceConfig:
    return PersonalContextFetchServiceConfig(
        service_id="rss",
        provider="rss_feed",
        enabled=True,
        interval_seconds=60,
        max_items_per_run=max_items,
        time_range=time_range or {"mode": "all"},
        source={"feed_url": FEED_URL},
        credentials={},
    )


def _set_responses(monkeypatch: pytest.MonkeyPatch, responses: list[_Response]) -> None:
    import openjiuwen.harness.personal_context.fetch.rss_feed as module

    _Session.responses = list(responses)
    _Session.calls = []
    _Session.timeout_total = None
    monkeypatch.setattr(module.aiohttp, "ClientSession", _Session)


async def _no_retry_sleep(_delay: float) -> None:
    return None


async def _batches(
    service: RssFeedFetchService,
    cursor: dict[str, object] | None = None,
    *,
    run_started_at: datetime | None = None,
):
    candidates = await service.prepare_run(
        run_id="run-1",
        run_started_at=run_started_at or datetime(2026, 8, 30, tzinfo=UTC),
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
    assert batches
    committed = record_completed_candidates(batches[-1].next_cursor, candidates)
    batches[-1] = batches[-1].model_copy(update={"next_cursor": committed})
    return batches


@pytest.mark.asyncio
async def test_rss_feed_parses_rss_and_sanitizes_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_responses(monkeypatch, [_Response(RSS_FIXTURE)])

    batches = await _batches(RssFeedFetchService(_config(max_items=25), home=tmp_path))
    items = [item for batch in batches for item in batch.items]

    assert [item.logical_id for item in items] == [
        "rss_feed:entry:guid-new",
        "rss_feed:entry:guid-old",
    ]
    assert items[0].title == "国内办公"
    assert "正文一" in (items[0].content or "")
    assert "alert(" not in (items[0].content or "")
    assert "bad" not in (items[0].content or "")
    assert items[0].metadata["content_type"] == "rss"
    assert items[0].metadata["author"] == "作者甲"
    assert len(_Session.calls) == 1
    assert all(url == FEED_URL for url, _kwargs in _Session.calls)


@pytest.mark.asyncio
async def test_atom_feed_uses_entry_id_updated_time_and_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_responses(monkeypatch, [_Response(ATOM_FIXTURE)])

    batches = await _batches(RssFeedFetchService(_config(max_items=25), home=tmp_path))

    item = batches[0].items[0]
    assert item.logical_id == "rss_feed:entry:atom-1"
    assert item.original_ref == "https://example.com/atom/1"
    assert item.metadata["content_type"] == "atom"
    assert item.metadata["author"] == "作者乙"
    assert batches[-1].next_cursor is not None


@pytest.mark.asyncio
async def test_rdf_feed_reads_items_sibling_to_channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_responses(monkeypatch, [_Response(RDF_FIXTURE)])

    batches = await _batches(RssFeedFetchService(_config(), home=tmp_path))

    assert len(batches[0].items) == 1
    assert batches[0].items[0].title == "RDF 更新"
    assert batches[0].items[0].original_ref == "https://example.com/rdf/1"


@pytest.mark.asyncio
async def test_rss_feed_falls_back_to_link_or_feed_url_and_epoch_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = """\
    <rss version="2.0"><channel><title>Fallback</title>
      <item><title>有链接</title><link>https://example.com/fallback</link></item>
      <item><title>无链接</title></item>
    </channel></rss>
    """
    _set_responses(monkeypatch, [_Response(fixture)])

    batches = await _batches(RssFeedFetchService(_config(), home=tmp_path))
    items = [item for batch in batches for item in batch.items]

    assert items[0].original_ref == "https://example.com/fallback"
    assert items[1].original_ref == FEED_URL
    assert all(
        "1970-01-01" in str(receipt["candidate_time"]) for receipt in batches[-1].next_cursor["_selection"]["completed"]
    )


@pytest.mark.asyncio
async def test_rss_feed_missing_time_fails_filtered_run_but_allows_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = '<rss version="2.0"><channel><item><title>无时间</title></item></channel></rss>'
    _set_responses(monkeypatch, [_Response(fixture)])
    all_time = await _batches(RssFeedFetchService(_config(), home=tmp_path))
    assert all_time[0].items

    _set_responses(monkeypatch, [_Response(fixture)])
    filtered = RssFeedFetchService(
        _config(time_range={"mode": "recent", "recent_days": 3}),
        home=tmp_path,
    )
    with pytest.raises(BaseError):
        await filtered.prepare_run(
            run_id="filtered",
            run_started_at=datetime(2026, 8, 30, tzinfo=UTC),
            cursor=None,
        )


@pytest.mark.asyncio
async def test_rss_feed_deduplicates_same_stable_id_by_latest_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = """\
    <rss version="2.0"><channel>
      <item><guid>same</guid><title>旧</title><pubDate>Fri, 28 Aug 2026 08:00:00 +0800</pubDate><description>old</description></item>
      <item><guid>same</guid><title>新</title><pubDate>Sat, 29 Aug 2026 08:00:00 +0800</pubDate><description>new</description></item>
    </channel></rss>
    """
    _set_responses(monkeypatch, [_Response(fixture)])

    batches = await _batches(RssFeedFetchService(_config(), home=tmp_path))
    items = [item for batch in batches for item in batch.items]

    assert len(items) == 1
    assert items[0].title == "新"
    assert items[0].content is not None and "new" in items[0].content


@pytest.mark.asyncio
async def test_rss_feed_repeated_round_is_empty_and_updated_entry_has_new_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = '<rss version="2.0"><channel><item><guid>same</guid><title>旧</title><pubDate>Fri, 28 Aug 2026 08:00:00 +0800</pubDate><description>old</description></item></channel></rss>'
    _set_responses(monkeypatch, [_Response(initial)])
    service = RssFeedFetchService(_config(), home=tmp_path)
    first = await _batches(service)

    _set_responses(monkeypatch, [_Response(initial)])
    repeated = await _batches(service, first[-1].next_cursor)
    assert repeated[0].items == ()

    updated = '<rss version="2.0"><channel><item><guid>same</guid><title>新</title><pubDate>Sat, 29 Aug 2026 08:00:00 +0800</pubDate><description>new</description></item></channel></rss>'
    _set_responses(monkeypatch, [_Response(updated)])
    changed = await _batches(service, first[-1].next_cursor)
    assert len(changed[0].items) == 1
    assert changed[0].items[0].revision_id != first[0].items[0].revision_id


@pytest.mark.asyncio
async def test_rss_feed_limits_each_batch_to_twenty_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entries = "".join(
        f"<item><guid>id-{index}</guid><title>Title {index}</title><pubDate>Sat, 29 Aug 2026 08:{index:02d}:00 +0800</pubDate><description>Body {index}</description></item>"
        for index in range(25)
    )
    fixture = f'<rss version="2.0"><channel>{entries}</channel></rss>'
    _set_responses(monkeypatch, [_Response(fixture)])

    batches = await _batches(RssFeedFetchService(_config(max_items=25), home=tmp_path))

    assert [len(batch.items) for batch in batches] == [20, 5]
    assert all(len(batch.items) <= 20 for batch in batches)


@pytest.mark.asyncio
@pytest.mark.parametrize("first_response", [_Response("", status=503), _DisconnectResponse("")])
async def test_rss_feed_retries_transient_read_without_duplicate_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_response: _Response,
) -> None:
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)
    _set_responses(monkeypatch, [first_response, _Response(RSS_FIXTURE)])

    batches = await _batches(RssFeedFetchService(_config(max_items=1), home=tmp_path))

    assert len(batches[0].items) == 1
    assert len(_Session.calls) == 2


@pytest.mark.asyncio
async def test_rss_feed_does_not_retry_http_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_responses(monkeypatch, [_Response("not found", status=404)])
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)

    with pytest.raises(BaseError) as caught:
        await RssFeedFetchService(_config(), home=tmp_path).prepare_run(
            run_id="not-found",
            run_started_at=datetime(2026, 8, 30, tzinfo=UTC),
            cursor=None,
        )

    assert caught.value.status is StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR
    assert len(_Session.calls) == 1


@pytest.mark.asyncio
async def test_rss_feed_malformed_xml_fails_without_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_responses(monkeypatch, [_Response("<rss><channel>")])
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)

    with pytest.raises(BaseError):
        await RssFeedFetchService(_config(), home=tmp_path).prepare_run(
            run_id="malformed",
            run_started_at=datetime(2026, 8, 30, tzinfo=UTC),
            cursor=None,
        )

    assert len(_Session.calls) == 1


@pytest.mark.asyncio
async def test_rss_feed_rejects_dtd_and_entities_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = """\
<!DOCTYPE rss [<!ENTITY injected "expanded">]>
<rss><channel><title>&injected;</title></channel></rss>
"""
    _set_responses(monkeypatch, [_Response(payload)])
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)

    with pytest.raises(BaseError):
        await RssFeedFetchService(_config(), home=tmp_path).prepare_run(
            run_id="unsafe-xml",
            run_started_at=datetime(2026, 8, 30, tzinfo=UTC),
            cursor=None,
        )

    assert len(_Session.calls) == 1


@pytest.mark.asyncio
async def test_rss_feed_rejects_response_over_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_responses(
        monkeypatch,
        [_Response("small", headers={"Content-Length": str(16 * 1024 * 1024 + 1)})],
    )

    with pytest.raises(BaseError):
        await RssFeedFetchService(_config(), home=tmp_path).prepare_run(
            run_id="large",
            run_started_at=datetime(2026, 8, 30, tzinfo=UTC),
            cursor=None,
        )

    assert len(_Session.calls) == 1
