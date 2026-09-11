"""Contract tests for the embedded GitHub provider."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch import retry as retry_module
from openjiuwen.harness.personal_context.fetch.cursor_selection import record_completed_candidates
from openjiuwen.harness.personal_context.fetch.github import GitHubFetchService


def github_config(
    tmp_path: Path,
    *,
    resources: list[str] | None = None,
    max_items_per_run: int | None = None,
    time_range: dict[str, object] | None = None,
) -> PersonalContextFetchServiceConfig:
    return PersonalContextFetchServiceConfig.model_validate(
        {
            "service_id": "github-demo",
            "provider": "github",
            "enabled": True,
            "interval_seconds": 60,
            "max_items_per_run": max_items_per_run,
            "time_range": time_range or {"mode": "all"},
            "source": {
                "owner": "acme",
                "repo": "demo",
                **({"resources": resources} if resources is not None else {}),
            },
            "credentials": {"token": "secret-token"},
        }
    )


class FakeResponse:
    def __init__(self, payload: Any, *, status: int = 200, content: bytes | None = None) -> None:
        self.status = status
        self._payload = payload
        self._content = content
        self.headers = {"Content-Length": str(len(content))} if content is not None else {}
        self.url = "https://api.github.com/fake"

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self) -> Any:
        return self._payload

    async def read(self) -> bytes:
        return self._content or b""

    async def iter_chunked(self, _size: int):
        yield self._content or b""

    async def text(self) -> str:
        return json.dumps(self._payload)

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=SimpleNamespace(real_url=self.url),
                history=(),
                status=self.status,
            )


class FakeSession:
    responses: dict[str, list[FakeResponse]] = {}
    timeout_totals: list[float | None] = []
    requests: list[str] = []

    def __init__(self, *, timeout: object | None = None, **_kwargs: object) -> None:
        self.timeout_totals.append(getattr(timeout, "total", None))

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, **_kwargs: object) -> FakeResponse:
        self.requests.append(url)
        values = self.responses.get(url)
        if not values:
            raise AssertionError(f"unexpected URL: {url}")
        return values.pop(0)


async def _batches(
    provider: GitHubFetchService,
    *,
    run_id: str,
    cursor: dict[str, object] | None,
    run_started_at: datetime | None = None,
):
    candidates = await provider.prepare_run(
        run_id=run_id,
        run_started_at=run_started_at or datetime.now(UTC),
        cursor=cursor,
    )
    batches = [
        batch
        async for batch in provider.fetch(
            run_id=run_id,
            cursor=cursor,
            candidates=candidates,
        )
    ]
    if batches:
        committed = record_completed_candidates(batches[-1].next_cursor, candidates)
        batches[-1] = batches[-1].model_copy(update={"next_cursor": committed})
    return batches


def zip_bytes(*entries: tuple[str, bytes, int | None]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content, mode in entries:
            info = zipfile.ZipInfo(name)
            if mode is not None:
                info.external_attr = mode << 16
            archive.writestr(info, content)
    return stream.getvalue()


async def _no_retry_sleep(_delay: float) -> None:
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_github_request_retries_transient_http_status(
    status: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    url = "https://api.github.com/repos/acme/demo"
    FakeSession.responses = {
        url: [FakeResponse({}, status=status), FakeResponse({"default_branch": "main"})],
    }
    FakeSession.requests = []
    monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)

    payload = await github_module._request_json(url, "secret")

    assert payload == {"default_branch": "main"}
    assert FakeSession.requests == [url, url]


@pytest.mark.asyncio
async def test_github_request_does_not_retry_401(monkeypatch: pytest.MonkeyPatch) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    url = "https://api.github.com/repos/acme/demo"
    FakeSession.responses = {url: [FakeResponse({}, status=401)]}
    FakeSession.requests = []
    monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)

    with pytest.raises(BaseError):
        await github_module._request_json(url, "secret")

    assert FakeSession.requests == [url]


@pytest.mark.asyncio
async def test_github_allow_not_found_does_not_retry_404(monkeypatch: pytest.MonkeyPatch) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    url = "https://api.github.com/repos/acme/demo/readme"
    FakeSession.responses = {url: [FakeResponse({}, status=404)]}
    FakeSession.requests = []
    monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)

    payload = await github_module._request_json(url, "secret", allow_not_found=True)

    assert payload is None
    assert FakeSession.requests == [url]


@pytest.mark.asyncio
async def test_github_archive_retry_extracts_only_successful_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    head = "a" * 40
    archive_url = f"https://api.github.com/repos/acme/demo/zipball/{head}"
    archive = zip_bytes((f"acme-demo-{head}/README.md", b"code", None))
    FakeSession.responses = {
        "https://api.github.com/repos/acme/demo": [
            FakeResponse({"default_branch": "main", "sha": head, "pushed_at": "2026-01-01T00:00:00Z"})
        ],
        archive_url: [FakeResponse({}, status=503), FakeResponse({}, content=archive)],
    }
    FakeSession.requests = []
    extract_calls = 0
    original_extract = github_module._extract_archive

    def tracking_extract(archive_bytes: bytes, candidate: Path) -> None:
        nonlocal extract_calls
        extract_calls += 1
        original_extract(archive_bytes, candidate)

    monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(github_module, "_extract_archive", tracking_extract)
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)

    batches = await _batches(
        GitHubFetchService(github_config(tmp_path, resources=["code"]), home=tmp_path),
        run_id="run-a",
        cursor=None,
    )

    assert FakeSession.requests.count(archive_url) == 2
    assert extract_calls == 1
    assert sum(len(batch.items) for batch in batches) == 1


@pytest.mark.asyncio
async def test_github_fetches_enabled_resources_and_materializes_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    head = "a" * 40
    archive = zip_bytes((f"acme-demo-{head}/README.md", b"code", None))
    FakeSession.responses = {
        "https://api.github.com/repos/acme/demo": [
            FakeResponse({"default_branch": "main", "sha": head, "pushed_at": "2026-01-01T00:00:00Z"})
        ],
        "https://api.github.com/repos/acme/demo/readme": [
            FakeResponse({"encoding": "base64", "content": base64.b64encode(b"# Demo").decode()})
        ],
        "https://api.github.com/repos/acme/demo/issues": [
            FakeResponse([{"number": 1, "title": "Bug", "body": "body", "updated_at": "2026-01-01T00:00:00Z"}]),
            FakeResponse([]),
        ],
        "https://api.github.com/repos/acme/demo/pulls": [FakeResponse([])],
        "https://api.github.com/repos/acme/demo/commits": [FakeResponse([])],
        f"https://api.github.com/repos/acme/demo/zipball/{head}": [FakeResponse({}, content=archive)],
    }
    FakeSession.timeout_totals = []
    monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)

    provider = GitHubFetchService(github_config(tmp_path), home=tmp_path)
    batches = await _batches(provider, run_id="run-a", cursor=None)
    items = [item for batch in batches for item in batch.items]

    assert {item.logical_id.rsplit(":", 1)[-1] for item in items} == {"1", "readme", "code"}
    assert (
        tmp_path / "materialized-sources" / "github" / "github-demo" / "candidate" / "README.md"
    ).read_text() == "code"
    assert any(total == 1200 for total in FakeSession.timeout_totals)
    assert next(item for item in items if item.logical_id.endswith(":code")).revision_id == head
    await provider.commit_run(run_id="run-a")
    materialized_root = tmp_path / "materialized-sources"
    assert not (materialized_root / "github" / "github-demo" / "candidate").exists()
    assert not (materialized_root / "github" / "github-demo" / "current").exists()
    assert not (materialized_root / "github" / "github-demo" / ".previous").exists()
    assert not materialized_root.exists()


@pytest.mark.asyncio
async def test_github_default_limit_is_25_and_resources_are_switchable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    issues = [
        {"number": index, "title": f"Issue {index}", "body": "body", "updated_at": "2026-01-01T00:00:00Z"}
        for index in range(1, 31)
    ]
    FakeSession.responses = {
        "https://api.github.com/repos/acme/demo": [FakeResponse({"default_branch": "main", "sha": "a" * 40})],
        "https://api.github.com/repos/acme/demo/issues": [FakeResponse(issues)],
    }
    monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)
    provider = GitHubFetchService(github_config(tmp_path, resources=["issues"]), home=tmp_path)

    batches = await _batches(provider, run_id="run-a", cursor=None)
    assert sum(len(batch.items) for batch in batches) == 25
    assert [len(batch.items) for batch in batches] == [20, 5]
    assert all(
        item.logical_id.endswith(tuple(f":issue:{index}" for index in range(1, 31)))
        for batch in batches
        for item in batch.items
    )
    assert batches[0].next_cursor is not None
    assert batches[1].next_cursor is not None
    assert batches[0].next_cursor == {}
    assert len(batches[1].next_cursor["_selection"]["completed"]) == 25


@pytest.mark.asyncio
async def test_github_resource_lanes_apply_their_defined_times(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    run_started_at = datetime(2026, 1, 10, tzinfo=UTC)
    recent = (run_started_at - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    old = (run_started_at - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    commit_sha = "b" * 40
    FakeSession.responses = {
        "https://api.github.com/repos/acme/demo": [FakeResponse({"default_branch": "main"})],
        "https://api.github.com/repos/acme/demo/issues": [
            FakeResponse(
                [
                    {"number": 1, "title": "Recent issue", "body": "body", "updated_at": recent},
                    {"number": 2, "title": "Old issue", "body": "body", "updated_at": old},
                ]
            )
        ],
        "https://api.github.com/repos/acme/demo/pulls": [
            FakeResponse([{"number": 3, "title": "Recent PR", "body": "body", "updated_at": recent}])
        ],
        "https://api.github.com/repos/acme/demo/commits": [
            FakeResponse(
                [
                    {
                        "sha": commit_sha,
                        "commit": {"message": "Recent commit", "committer": {"date": recent}},
                    }
                ]
            )
        ],
    }
    monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)
    provider = GitHubFetchService(
        github_config(
            tmp_path,
            resources=["issues", "pull_requests", "commits"],
            time_range={"mode": "recent", "recent_days": 3},
        ),
        home=tmp_path,
    )

    candidates = await provider.prepare_run(
        run_id="recent",
        run_started_at=run_started_at,
        cursor=None,
    )

    assert {candidate["stable_id"] for candidate in candidates} == {
        "github:acme/demo:issue:1",
        "github:acme/demo:pull_request:3",
        f"github:acme/demo:commit:{commit_sha}",
    }
    assert {candidate["resource_lane"] for candidate in candidates} == {
        "issue",
        "pull_request",
        "commit",
    }


@pytest.mark.asyncio
async def test_github_readme_and_code_use_head_commit_time_before_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    head = "a" * 40
    run_started_at = datetime(2026, 1, 10, tzinfo=UTC)
    head_time = (run_started_at - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    FakeSession.responses = {
        "https://api.github.com/repos/acme/demo": [
            FakeResponse({"default_branch": "main", "sha": head, "head_commit_time": head_time})
        ],
        "https://api.github.com/repos/acme/demo/readme": [
            FakeResponse({"encoding": "base64", "content": base64.b64encode(b"# Demo").decode()})
        ],
    }
    monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)
    provider = GitHubFetchService(
        github_config(
            tmp_path,
            resources=["readme", "code"],
            time_range={"mode": "recent", "recent_days": 3},
        ),
        home=tmp_path,
    )

    candidates = await provider.prepare_run(
        run_id="head",
        run_started_at=run_started_at,
        cursor=None,
    )

    assert {candidate["resource_lane"] for candidate in candidates} == {"readme", "code"}
    assert {candidate["candidate_time"] for candidate in candidates} == {head_time}
    assert not (tmp_path / "materialized-sources").exists()


@pytest.mark.asyncio
async def test_github_fetch_removes_same_service_crash_candidate_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    candidate = tmp_path / "materialized-sources" / "github" / "github-demo" / "candidate"
    candidate.mkdir(parents=True)
    (candidate / ".personal-context-marker.json").write_text(json.dumps({"run_id": "crashed-run"}), encoding="utf-8")
    (candidate / "README.md").write_text("stale", encoding="utf-8")
    FakeSession.responses = {
        "https://api.github.com/repos/acme/demo": [FakeResponse({"default_branch": "main"})],
        "https://api.github.com/repos/acme/demo/issues": [FakeResponse([])],
    }
    monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)
    provider = GitHubFetchService(github_config(tmp_path, resources=["issues"]), home=tmp_path)

    batches = await _batches(provider, run_id="run-new", cursor=None)

    assert len(batches) == 1 and not batches[0].items
    assert not candidate.exists()
    assert not (tmp_path / "materialized-sources").exists()


@pytest.mark.asyncio
async def test_github_total_limit_selects_latest_across_enabled_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    timestamp = "2026-01-01T00:00:00Z"
    issues = [
        {"number": index, "title": f"Issue {index}", "body": "body", "updated_at": timestamp} for index in range(1, 19)
    ]
    pulls = [
        {"number": index, "title": f"PR {index}", "body": "body", "updated_at": timestamp} for index in range(101, 119)
    ]
    commits = [
        {
            "sha": f"{index:040x}",
            "html_url": f"https://github.com/acme/demo/commit/{index:040x}",
            "commit": {"message": f"Commit {index}", "author": {"date": timestamp}},
        }
        for index in range(201, 219)
    ]
    FakeSession.responses = {
        "https://api.github.com/repos/acme/demo": [FakeResponse({"default_branch": "main"})],
        "https://api.github.com/repos/acme/demo/readme": [
            FakeResponse({"encoding": "base64", "content": base64.b64encode(b"# Demo").decode()})
        ],
        "https://api.github.com/repos/acme/demo/issues": [FakeResponse(issues)],
        "https://api.github.com/repos/acme/demo/pulls": [FakeResponse(pulls)],
        "https://api.github.com/repos/acme/demo/commits": [FakeResponse(commits)],
    }
    monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)
    provider = GitHubFetchService(
        github_config(
            tmp_path,
            resources=["readme", "issues", "pull_requests", "commits"],
            max_items_per_run=19,
        ),
        home=tmp_path,
    )

    batches = await _batches(provider, run_id="run-a", cursor=None)
    items = [item for batch in batches for item in batch.items]
    counts = {
        resource: sum(item.metadata.get("resource") == resource for item in items)
        for resource in ("readme", "issues", "pull_requests", "commits")
    }

    assert counts == {"readme": 0, "issues": 1, "pull_requests": 0, "commits": 18}
    assert len(items) == 19


@pytest.mark.asyncio
async def test_github_rejects_unsafe_archive_and_wrong_run_cannot_commit_or_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    archive = zip_bytes(("../escape.txt", b"no", None))
    archive_url = f"https://api.github.com/repos/acme/demo/zipball/{'a' * 40}"
    FakeSession.responses = {
        "https://api.github.com/repos/acme/demo": [FakeResponse({"default_branch": "main", "sha": "a" * 40})],
        archive_url: [FakeResponse({}, content=archive)],
    }
    FakeSession.requests = []
    monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)
    provider = GitHubFetchService(github_config(tmp_path, resources=["code"]), home=tmp_path)
    with pytest.raises(Exception):
        _ = await _batches(provider, run_id="run-a", cursor=None)
    assert FakeSession.requests.count(archive_url) == 1

    candidate = tmp_path / "materialized-sources" / "github" / "github-demo" / "candidate"
    candidate.mkdir(parents=True)
    (candidate / ".personal-context-marker.json").write_text(json.dumps({"run_id": "run-a"}))
    (candidate / "README.md").write_text("new")
    await provider.abort_run(run_id="run-b")
    assert candidate.exists()
    await provider.commit_run(run_id="run-b")
    assert candidate.exists()
    await provider.abort_run(run_id="run-a")
    assert not candidate.exists()
    assert not (tmp_path / "materialized-sources").exists()


@pytest.mark.asyncio
async def test_github_rejects_casefold_duplicate_archive_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    head = "a" * 40
    prefix = f"acme-demo-{head}"
    archive = zip_bytes(
        (f"{prefix}/README.md", b"one", None),
        (f"{prefix}/readme.md", b"two", None),
    )
    FakeSession.responses = {
        "https://api.github.com/repos/acme/demo": [FakeResponse({"default_branch": "main", "sha": head})],
        f"https://api.github.com/repos/acme/demo/zipball/{head}": [FakeResponse({}, content=archive)],
    }
    monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)
    provider = GitHubFetchService(github_config(tmp_path, resources=["code"]), home=tmp_path)

    with pytest.raises(Exception):
        _ = await _batches(provider, run_id="run-a", cursor=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("layout_name", ["candidate"])
async def test_github_rejects_symlinked_materialized_layout(tmp_path: Path, layout_name: str) -> None:
    root = tmp_path / "materialized-sources" / "github" / "github-demo"
    root.mkdir(parents=True)
    target = tmp_path / "outside"
    target.mkdir()
    try:
        os.symlink(target, root / layout_name, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")

    provider = GitHubFetchService(github_config(tmp_path, resources=["issues"]), home=tmp_path)
    with pytest.raises(Exception):
        _ = await _batches(provider, run_id="run-a", cursor=None)
    with pytest.raises(Exception):
        await provider.commit_run(run_id="run-a")
    with pytest.raises(Exception):
        await provider.abort_run(run_id="run-a")


@pytest.mark.asyncio
async def test_github_bounds_large_content_and_keeps_full_payload_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    body = "x" * 2_100_100
    issue = {"number": 7, "title": "Large", "body": body, "updated_at": "2026-01-01T00:00:00Z"}
    FakeSession.responses = {
        "https://api.github.com/repos/acme/demo": [FakeResponse({"default_branch": "main"})],
        "https://api.github.com/repos/acme/demo/issues": [FakeResponse([issue]), FakeResponse([])],
    }
    monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)
    provider = GitHubFetchService(github_config(tmp_path, resources=["issues"]), home=tmp_path)

    batches = await _batches(provider, run_id="run-a", cursor=None)
    item = batches[0].items[0]
    assert item.content is not None and len(item.content) == 2_000_000
    assert item.metadata["content_truncated"] is True
    assert item.metadata["raw_snapshot_omitted"] is True
    expected_revision = hashlib.sha256(
        json.dumps(issue, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert item.revision_id == expected_revision


@pytest.mark.asyncio
async def test_github_cursor_merges_same_timestamp_latest_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    timestamp = "2026-01-01T00:00:00Z"
    issue_one = {"number": 1, "title": "One", "body": "one", "updated_at": timestamp}
    issue_two = {"number": 2, "title": "Two", "body": "two", "updated_at": timestamp}
    monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)
    provider = GitHubFetchService(github_config(tmp_path, resources=["issues"]), home=tmp_path)

    FakeSession.responses = {
        "https://api.github.com/repos/acme/demo": [FakeResponse({"default_branch": "main"})],
        "https://api.github.com/repos/acme/demo/issues": [FakeResponse([issue_one]), FakeResponse([])],
    }
    first = await _batches(provider, run_id="run-a", cursor=None)
    first_cursor = first[-1].next_cursor
    assert first_cursor is not None

    FakeSession.responses = {
        "https://api.github.com/repos/acme/demo": [FakeResponse({"default_branch": "main"})],
        "https://api.github.com/repos/acme/demo/issues": [FakeResponse([issue_one, issue_two]), FakeResponse([])],
    }
    second = await _batches(provider, run_id="run-b", cursor=first_cursor)
    second_cursor = second[-1].next_cursor
    assert second_cursor is not None
    assert [item.logical_id for item in second[0].items] == ["github:acme/demo:issue:2"]
    same_time_ids = [
        receipt["stable_id"]
        for receipt in second_cursor["_selection"]["completed"]
        if receipt["candidate_time"] == timestamp
    ]
    assert same_time_ids == [
        "github:acme/demo:issue:1",
        "github:acme/demo:issue:2",
    ]

    FakeSession.responses = {
        "https://api.github.com/repos/acme/demo": [FakeResponse({"default_branch": "main"})],
        "https://api.github.com/repos/acme/demo/issues": [FakeResponse([issue_one, issue_two]), FakeResponse([])],
    }
    third = await _batches(provider, run_id="run-c", cursor=second_cursor)
    assert not third[0].items


@pytest.mark.asyncio
async def test_github_rejects_malformed_resource_cursor(tmp_path: Path) -> None:
    provider = GitHubFetchService(github_config(tmp_path, resources=["issues"]), home=tmp_path)
    with pytest.raises(Exception):
        _ = await _batches(provider, run_id="run-a", cursor={"issues": "bad"})


@pytest.mark.asyncio
async def test_github_new_overflow_advances_only_a_contiguous_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    def issue(number: int, second: int) -> dict[str, object]:
        return {
            "number": number,
            "title": f"Issue {number}",
            "body": f"Body {number}",
            "updated_at": f"2026-01-01T00:00:{second:02}Z",
        }

    def install(pages: list[list[dict[str, object]]]) -> None:
        FakeSession.responses = {
            "https://api.github.com/repos/acme/demo": [FakeResponse({"default_branch": "main"})],
            "https://api.github.com/repos/acme/demo/issues": [FakeResponse(page) for page in pages],
        }
        FakeSession.timeout_totals = []
        monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)

    initial = [issue(3, 3), issue(2, 2), issue(1, 1)]
    install([initial])
    service = GitHubFetchService(
        github_config(tmp_path, resources=["issues"], max_items_per_run=2),
        home=tmp_path,
    )
    first = await _batches(service, run_id="run-a", cursor=None)
    cursor = first[-1].next_cursor
    assert cursor is not None

    changed = [*[issue(number, number) for number in range(8, 3, -1)], *initial]
    round_ids: list[list[str]] = []
    for run_id in ("run-b", "run-c", "run-d"):
        install([changed])
        batches = await _batches(service, run_id=run_id, cursor=cursor)
        round_ids.append([item.logical_id for batch in batches for item in batch.items])
        cursor = batches[-1].next_cursor
        assert cursor is not None

    assert round_ids == [
        ["github:acme/demo:issue:8", "github:acme/demo:issue:7"],
        ["github:acme/demo:issue:6", "github:acme/demo:issue:5"],
        ["github:acme/demo:issue:4", "github:acme/demo:issue:1"],
    ]
    assert cursor["_selection"]["latest_seen_time"] == "2026-01-01T00:00:08Z"


@pytest.mark.asyncio
async def test_github_issues_continue_history_and_prioritize_new_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.github as github_module

    def issue(number: int, updated_at: str, *, title: str | None = None) -> dict[str, object]:
        return {
            "number": number,
            "title": title or f"Issue {number}",
            "body": f"Body {number}",
            "updated_at": updated_at,
        }

    initial = [
        issue(
            index,
            f"2026-01-01T00:{(204 - index) // 60:02}:{(204 - index) % 60:02}Z",
        )
        for index in range(205)
    ]
    metadata = {"default_branch": "main"}

    def install_responses(pages: list[list[dict[str, object]]]) -> None:
        FakeSession.responses = {
            "https://api.github.com/repos/acme/demo": [FakeResponse(metadata) for _ in range(10)],
            "https://api.github.com/repos/acme/demo/issues": [FakeResponse(page) for page in pages],
        }
        FakeSession.timeout_totals = []
        monkeypatch.setattr(github_module.aiohttp, "ClientSession", FakeSession)

    install_responses([initial, initial, initial])
    service = GitHubFetchService(
        github_config(tmp_path, resources=["issues"], max_items_per_run=100),
        home=tmp_path / "home",
    )
    cursor: dict[str, object] | None = None
    rounds = []
    for run_id in ("run-a", "run-b", "run-c"):
        batches = await _batches(service, run_id=run_id, cursor=cursor)
        rounds.append([item for batch in batches for item in batch.items])
        assert batches
        cursor = batches[-1].next_cursor

    assert [len(items) for items in rounds] == [100, 100, 5]
    logical_ids = [item.logical_id for items in rounds for item in items]
    assert len(logical_ids) == len(set(logical_ids)) == 205
    assert {item.logical_id for items in rounds for item in items} == {
        f"github:acme/demo:issue:{index}" for index in range(205)
    }
    assert cursor is not None
    assert set(cursor) == {"_selection"}
    assert len(cursor["_selection"]["completed"]) == 205

    priority_initial = initial
    install_responses([priority_initial])
    priority_service = GitHubFetchService(
        github_config(tmp_path, resources=["issues"], max_items_per_run=100),
        home=tmp_path / "priority-home",
    )
    first = await _batches(priority_service, run_id="priority-a", cursor=None)
    first_items = [item for batch in first for item in batch.items]
    first_cursor = first[-1].next_cursor
    assert first_cursor is not None

    changed = [
        issue(
            index,
            f"2026-01-01T00:{(204 - index) // 60:02}:{(204 - index) % 60:02}Z",
            title="Modified" if index == 99 else None,
        )
        for index in range(205)
    ]
    changed[99]["updated_at"] = "2026-01-01T01:00:00Z"
    changed.extend(
        [
            issue(1000, "2026-01-01T01:00:02Z", title="New A"),
            issue(1001, "2026-01-01T01:00:03Z", title="New B"),
        ]
    )
    install_responses([changed])
    second = await _batches(priority_service, run_id="priority-b", cursor=first_cursor)
    second_items = [item for batch in second for item in batch.items]
    assert len(second_items) == 100
    assert [item.title for item in second_items[:3]] == ["New B", "New A", "Modified"]
    assert second_items[2].logical_id == first_items[99].logical_id
