from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch import browser_bookmarks
from openjiuwen.harness.personal_context.fetch.browser_bookmarks import BrowserBookmarksFetchService
from openjiuwen.harness.personal_context.status_codes import StatusCode


def _config(
    bookmarks_path: Path,
    *,
    folders: list[str] | None = None,
    include_subfolders: bool = True,
    fetch_page_content: bool = False,
    max_items: int | None = None,
) -> PersonalContextFetchServiceConfig:
    return PersonalContextFetchServiceConfig(
        service_id="bookmarks",
        provider="browser_bookmarks",
        enabled=True,
        interval_seconds=60,
        max_items_per_run=max_items,
        source={
            "bookmarks_path": str(bookmarks_path),
            "bookmark_folder_paths": folders or [],
            "include_subfolders": include_subfolders,
            "fetch_page_content": fetch_page_content,
        },
        credentials={},
    )


def _write_bookmarks(path: Path, nodes: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps(
            {
                "roots": {
                    "bookmark_bar": {
                        "type": "folder",
                        "name": "收藏夹栏",
                        "children": nodes,
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _bookmark(
    bookmark_id: str,
    title: str,
    url: str,
    *,
    folder: str | None = None,
    date_added: str = "13200000000000000",
) -> dict[str, object]:
    node: dict[str, object] = {
        "id": bookmark_id,
        "type": "url",
        "name": title,
        "url": url,
        "date_added": date_added,
    }
    if folder is not None:
        return {
            "id": f"folder-{bookmark_id}",
            "type": "folder",
            "name": folder,
            "children": [node],
        }
    return node


async def _batches(service: BrowserBookmarksFetchService, cursor: dict[str, object] | None = None):
    return [batch async for batch in service.fetch(run_id="run-1", cursor=cursor)]


def _items(batches):
    return [item for batch in batches for item in batch.items]


def test_browser_bookmarks_reads_edge_json_and_filters_folders(tmp_path: Path):
    path = tmp_path / "Bookmarks"
    _write_bookmarks(
        path,
        [
            _bookmark("1", "AI", "https://example.com/ai", folder="AI"),
            _bookmark("2", "Nested", "https://example.com/nested", folder="AI/Tools"),
            _bookmark("3", "Work", "https://example.com/work", folder="Work"),
        ],
    )
    service = BrowserBookmarksFetchService(
        _config(path, folders=["收藏夹栏/AI"], include_subfolders=True),
        home=tmp_path / "home",
    )

    batches = asyncio.run(_batches(service))
    items = _items(batches)

    assert {item.title for item in items} == {"AI", "Nested"}
    assert all(item.operation == "upsert" for item in items)
    assert (
        items[0].logical_id == f"browser_bookmarks:edge:{hashlib.sha256('https://example.com/ai'.encode()).hexdigest()}"
    )
    assert (
        items[0].revision_id
        == hashlib.sha256("AI\nhttps://example.com/ai\n收藏夹栏/AI\n13200000000000000".encode()).hexdigest()
    )
    assert items[0].metadata["folder_path"] == "收藏夹栏/AI"


def test_browser_bookmarks_folder_filter_can_exclude_subfolders(tmp_path: Path):
    path = tmp_path / "Bookmarks"
    _write_bookmarks(
        path,
        [
            _bookmark("1", "AI", "https://example.com/ai", folder="AI"),
            _bookmark("2", "Nested", "https://example.com/nested", folder="AI/Tools"),
        ],
    )
    service = BrowserBookmarksFetchService(
        _config(path, folders=["收藏夹栏/AI"], include_subfolders=False),
        home=tmp_path / "home",
    )

    assert [item.title for item in _items(asyncio.run(_batches(service)))] == ["AI"]


@pytest.mark.parametrize(
    "payload",
    [None, {"roots": []}, {"roots": {"bookmark_bar": {"type": "folder"}}}],
)
def test_browser_bookmarks_invalid_json_structure_fails_whole_run(tmp_path: Path, payload: object):
    path = tmp_path / "Bookmarks"
    if payload is None:
        path.write_text("{not-json", encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    service = BrowserBookmarksFetchService(_config(path), home=tmp_path / "home")

    with pytest.raises(BaseError) as caught:
        asyncio.run(_batches(service))
    assert caught.value.status is StatusCode.CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR


def test_browser_bookmarks_detects_update_and_delete_and_advances_only_emitted_ids(tmp_path: Path):
    path = tmp_path / "Bookmarks"
    _write_bookmarks(
        path,
        [
            _bookmark("1", "First", "https://example.com/one"),
            _bookmark("2", "Second", "https://example.com/two"),
        ],
    )
    service = BrowserBookmarksFetchService(_config(path), home=tmp_path / "home")
    first = asyncio.run(_batches(service))
    assert len(first) == 1
    assert len(first[0].items) == 2
    cursor = first[0].next_cursor
    assert isinstance(cursor, dict)

    _write_bookmarks(
        path,
        [
            _bookmark("1", "First changed", "https://example.com/one"),
        ],
    )
    changed = asyncio.run(_batches(service, cursor))
    changed_items = _items(changed)
    assert {(item.operation, item.title) for item in changed_items} == {
        ("upsert", "First changed"),
        ("delete", None),
    }
    assert changed[-1].next_cursor == {
        "items": {item.logical_id: item.revision_id for item in changed_items if item.operation == "upsert"}
    }


def test_browser_bookmarks_default_batches_are_at_most_twenty(tmp_path: Path):
    path = tmp_path / "Bookmarks"
    _write_bookmarks(
        path,
        [
            _bookmark(str(index), str(index), f"https://example.com/{index}", date_added=str(index))
            for index in range(25)
        ],
    )
    service = BrowserBookmarksFetchService(_config(path), home=tmp_path / "home")

    batches = asyncio.run(_batches(service))
    assert [len(batch.items) for batch in batches] == [20]
    assert all(len(batch.items) <= 20 for batch in batches)


def test_browser_bookmarks_page_fetch_failure_is_warning_upsert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "Bookmarks"
    _write_bookmarks(path, [_bookmark("1", "One", "https://example.com/one")])

    async def fail(_url: str):
        return SimpleNamespace(status="warning", title=None, text=None, final_url=None, error="timeout")

    monkeypatch.setattr(browser_bookmarks, "_fetch_page_content", fail)
    service = BrowserBookmarksFetchService(
        _config(path, fetch_page_content=True),
        home=tmp_path / "home",
    )

    item = _items(asyncio.run(_batches(service)))[0]
    assert item.operation == "upsert"
    assert item.metadata["page_fetch_status"] == "warning"
    assert item.metadata["page_fetch_error"] == "timeout"


def test_browser_bookmarks_unsafe_url_is_not_fetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "Bookmarks"
    _write_bookmarks(path, [_bookmark("1", "Danger", "javascript:alert(1)")])
    called = False

    async def unexpected(_url: str):
        nonlocal called
        called = True
        raise AssertionError("unsafe URL must not be fetched")

    monkeypatch.setattr(browser_bookmarks, "_fetch_page_content", unexpected)
    service = BrowserBookmarksFetchService(
        _config(path, fetch_page_content=True),
        home=tmp_path / "home",
    )

    item = _items(asyncio.run(_batches(service)))[0]
    assert called is False
    assert item.metadata["page_fetch_status"] == "skipped"
    assert "unsupported" in str(item.metadata["page_fetch_error"])


def test_browser_bookmarks_follows_safe_page_redirects(monkeypatch: pytest.MonkeyPatch):
    requested_urls: list[str] = []
    session_kwargs: dict[str, object] = {}

    class Response:
        def __init__(
            self,
            *,
            status: int,
            url: str,
            headers: dict[str, str],
            body: str = "",
        ) -> None:
            self.status = status
            self.url = url
            self.headers = headers
            self._body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def text(self) -> str:
            return self._body

    responses = {
        "https://example.com/start": Response(
            status=302,
            url="https://example.com/start",
            headers={"Location": "/article"},
        ),
        "https://example.com/article": Response(
            status=200,
            url="https://example.com/article",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body="<html><title>Redirected</title><main>complete page body</main></html>",
        ),
    }

    class Session:
        def __init__(self, **kwargs) -> None:
            session_kwargs.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, url: str, **_kwargs):
            requested_urls.append(url)
            return responses[url]

    monkeypatch.setattr(browser_bookmarks.aiohttp, "ClientSession", Session)

    page = asyncio.run(browser_bookmarks._fetch_page_content("https://example.com/start"))

    assert requested_urls == ["https://example.com/start", "https://example.com/article"]
    assert page["status"] == "ok"
    assert page["final_url"] == "https://example.com/article"
    assert page["title"] == "Redirected"
    assert page["text"] == "complete page body"
    headers = session_kwargs["headers"]
    assert isinstance(headers, dict)
    assert str(headers["User-Agent"]).startswith("Mozilla/5.0 ")


def test_browser_bookmarks_extracts_structured_body_before_applying_text_limit():
    html_text = (
        "<html><head><title>Structured article</title>"
        f"<template>{'template-junk ' * 1_200}</template></head>"
        "<body><nav>navigation noise</nav><main>"
        "Wiki Memory keeps complete article facts about DeepWiki and Karpathy."
        "</main><footer>footer noise</footer></body></html>"
    )

    page = browser_bookmarks._extract_page(html_text, final_url="https://example.com/article")

    assert page["title"] == "Structured article"
    assert page["text"] == "Wiki Memory keeps complete article facts about DeepWiki and Karpathy."
    assert "template-junk" not in str(page["text"])
    assert "navigation noise" not in str(page["text"])


def test_browser_bookmarks_rejects_credentialed_page_redirect(monkeypatch: pytest.MonkeyPatch):
    requested_urls: list[str] = []

    class Response:
        status = 302
        url = "https://example.com/start"
        headers = {"Location": "https://user:secret@example.net/private"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Session:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, url: str, **_kwargs):
            requested_urls.append(url)
            return Response()

    monkeypatch.setattr(browser_bookmarks.aiohttp, "ClientSession", Session)

    with pytest.raises(RuntimeError, match="redirect target is not allowed"):
        asyncio.run(browser_bookmarks._fetch_page_content("https://example.com/start"))

    assert requested_urls == ["https://example.com/start"]


def test_browser_bookmarks_normalizes_url_for_identity_and_skips_malformed_url(tmp_path: Path):
    path = tmp_path / "Bookmarks"
    _write_bookmarks(
        path,
        [
            _bookmark("1", "Canonical", "HTTPS://EXAMPLE.COM:443/path#fragment"),
            _bookmark("2", "Malformed", "http://[bad"),
        ],
    )
    service = BrowserBookmarksFetchService(_config(path, fetch_page_content=True), home=tmp_path / "home")

    items = _items(asyncio.run(_batches(service)))
    canonical = next(item for item in items if item.title == "Canonical")
    malformed = next(item for item in items if item.title == "Malformed")
    normalized = "https://example.com/path"
    assert canonical.original_ref == normalized
    assert canonical.logical_id == f"browser_bookmarks:edge:{hashlib.sha256(normalized.encode()).hexdigest()}"
    assert malformed.metadata["page_fetch_status"] == "skipped"


def test_browser_bookmarks_rejects_unknown_cursor_fields(tmp_path: Path):
    path = tmp_path / "Bookmarks"
    _write_bookmarks(path, [_bookmark("1", "One", "https://example.com/one")])
    service = BrowserBookmarksFetchService(_config(path), home=tmp_path / "home")

    with pytest.raises(BaseError) as caught:
        asyncio.run(_batches(service, {"items": {}, "bookmarks": {}}))
    assert caught.value.status is StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR


def test_browser_bookmarks_rejects_nested_cursor_values(tmp_path: Path):
    path = tmp_path / "Bookmarks"
    _write_bookmarks(path, [_bookmark("1", "One", "https://example.com/one")])
    service = BrowserBookmarksFetchService(_config(path), home=tmp_path / "home")

    with pytest.raises(BaseError) as caught:
        asyncio.run(_batches(service, {"items": {"bookmark": {"fingerprint": "old"}}}))
    assert caught.value.status is StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR


def test_browser_bookmarks_bounds_fields_and_does_not_leak_userinfo(tmp_path: Path):
    path = tmp_path / "Bookmarks"
    secret_url = "https://user:secret@example.com/private"
    _write_bookmarks(path, [_bookmark("1", "Private", secret_url)])
    service = BrowserBookmarksFetchService(_config(path, fetch_page_content=True), home=tmp_path / "home")

    item = _items(asyncio.run(_batches(service)))[0]
    assert "user:secret" not in item.original_ref
    assert "user:secret" not in item.content
    assert all("user:secret" not in str(value) for value in item.metadata.values())

    _write_bookmarks(path, [_bookmark("1", "x" * 5000, "https://example.com/private")])
    with pytest.raises(BaseError):
        asyncio.run(_batches(service))


def test_browser_bookmarks_missing_file_fails_instead_of_deleting_everything(tmp_path: Path):
    service = BrowserBookmarksFetchService(_config(tmp_path / "missing"), home=tmp_path / "home")

    with pytest.raises(BaseError):
        asyncio.run(_batches(service, {"bookmarks": {"old": "fingerprint"}}))


def test_browser_bookmarks_empty_roots_fails_instead_of_deleting_everything(tmp_path: Path):
    path = tmp_path / "Bookmarks"
    path.write_text(json.dumps({"roots": {}}), encoding="utf-8")
    service = BrowserBookmarksFetchService(_config(path), home=tmp_path / "home")

    with pytest.raises(BaseError) as caught:
        asyncio.run(_batches(service, {"items": {"old": "fingerprint"}}))
    assert caught.value.status is StatusCode.CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR


def test_browser_bookmarks_history_and_new_changes_use_stable_priority(tmp_path: Path):
    path = tmp_path / "Bookmarks"
    expected_urls = {f"https://example.com/bookmark-{index:03}" for index in range(205)}
    _write_bookmarks(
        path,
        [
            _bookmark(
                str(index),
                f"Bookmark {index:03}",
                f"https://example.com/bookmark-{index:03}",
                date_added=str(13_200_000_000_000_000 - index),
            )
            for index in range(205)
        ],
    )
    service = BrowserBookmarksFetchService(
        _config(path, max_items=100),
        home=tmp_path / "home",
    )
    cursor: dict[str, object] | None = None
    rounds = []
    for _round in range(3):
        batches = asyncio.run(_batches(service, cursor))
        rounds.append(_items(batches))
        assert batches
        cursor = batches[-1].next_cursor

    assert [len(items) for items in rounds] == [100, 100, 5]
    logical_ids = [item.logical_id for items in rounds for item in items]
    assert len(logical_ids) == len(set(logical_ids)) == 205
    assert {item.original_ref for items in rounds for item in items} == expected_urls
    assert cursor is not None
    assert set(cursor["items"]) == set(logical_ids)

    priority_path = tmp_path / "PriorityBookmarks"
    original_nodes = [
        _bookmark(
            str(index),
            f"Bookmark {index:03}",
            f"https://example.com/priority-{index:03}",
            date_added=str(13_200_000_000_000_000 - index),
        )
        for index in range(205)
    ]
    _write_bookmarks(priority_path, original_nodes)
    priority_service = BrowserBookmarksFetchService(
        _config(priority_path, max_items=100),
        home=tmp_path / "priority-home",
    )
    first = asyncio.run(_batches(priority_service))
    first_items = _items(first)
    first_cursor = first[-1].next_cursor
    assert first_cursor is not None

    modified_index = 99
    changed_nodes = [
        _bookmark(
            str(index),
            "Modified title" if index == modified_index else f"Bookmark {index:03}",
            f"https://example.com/priority-{index:03}",
            date_added=("13100000000000000" if index == modified_index else str(13_200_000_000_000_000 - index)),
        )
        for index in range(205)
    ]
    changed_nodes.extend(
        [
            _bookmark("new-a", "New A", "https://example.com/priority-new-a", date_added="13200000000000001"),
            _bookmark("new-b", "New B", "https://example.com/priority-new-b", date_added="13200000000000002"),
        ]
    )
    _write_bookmarks(priority_path, changed_nodes)

    changed = _items(asyncio.run(_batches(priority_service, first_cursor)))
    assert len(changed) == 100
    assert [item.title for item in changed[:3]] == ["New B", "New A", "Modified title"]
    assert changed[2].logical_id == first_items[modified_index].logical_id
