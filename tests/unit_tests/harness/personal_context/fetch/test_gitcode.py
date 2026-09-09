"""Contract tests for the embedded GitCode provider."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import stat
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
from openjiuwen.harness.personal_context.fetch.gitcode import GitCodeFetchService


def gitcode_config(
    tmp_path: Path,
    *,
    resources: list[str] | None = None,
    max_items_per_run: int | None = None,
    time_range: dict[str, object] | None = None,
) -> PersonalContextFetchServiceConfig:
    return PersonalContextFetchServiceConfig.model_validate(
        {
            "service_id": "gitcode-demo",
            "provider": "gitcode",
            "enabled": True,
            "interval_seconds": 60,
            "max_items_per_run": max_items_per_run,
            "time_range": time_range or {"mode": "all"},
            "source": {
                "owner": "acme",
                "repo": "demo",
                **({"resources": resources} if resources is not None else {}),
            },
            "credentials": {"pat": "secret-pat"},
        }
    )


class FakeResponse:
    def __init__(self, payload: Any, *, status: int = 200) -> None:
        self.status = status
        self._payload = payload
        self.headers: dict[str, str] = {}
        self.url = "https://api.gitcode.com/fake"

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=SimpleNamespace(real_url=self.url),
                history=(),
                status=self.status,
            )


class FakeSession:
    responses: dict[str, list[FakeResponse]] = {}
    requests: list[tuple[str, dict[str, object]]] = []

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.requests.append((url, kwargs))
        values = self.responses.get(url)
        if not values:
            raise AssertionError(f"unexpected URL: {url}")
        return values.pop(0)


async def _batches(
    provider: GitCodeFetchService,
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


async def _no_retry_sleep(_delay: float) -> None:
    return None


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only cleanup regression")
def test_gitcode_remove_tree_clears_windows_readonly_git_files(tmp_path: Path) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    candidate = tmp_path / "candidate"
    pack = candidate / ".git" / "objects" / "pack" / "pack-test.idx"
    pack.parent.mkdir(parents=True)
    pack.write_bytes(b"pack-index")
    pack.chmod(stat.S_IREAD)

    try:
        gitcode_module._remove_tree(candidate)
    finally:
        if pack.exists():
            pack.chmod(stat.S_IWRITE)
        if candidate.exists():
            shutil.rmtree(candidate)

    assert not candidate.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows long-path cleanup regression")
def test_gitcode_remove_tree_clears_windows_long_paths(tmp_path: Path) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    candidate = tmp_path / "candidate"
    extended_candidate = Path("\\\\?\\" + str(candidate.resolve()))
    nested = extended_candidate.joinpath(*(f"segment-{index}-" + "x" * 70 for index in range(4)))
    nested.mkdir(parents=True)
    content = nested / "content.txt"
    content.write_text("content", encoding="utf-8")
    assert len(str(content)) > 260

    try:
        gitcode_module._remove_tree(candidate)
    finally:
        if extended_candidate.exists():
            shutil.rmtree(extended_candidate)

    assert not candidate.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows long-path inspection regression")
def test_gitcode_validates_windows_long_path_worktree(tmp_path: Path) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    candidate = tmp_path / "candidate"
    extended_candidate = Path("\\\\?\\" + str(candidate.resolve()))
    nested = extended_candidate.joinpath(*(f"segment-{index}-" + "x" * 70 for index in range(4)))
    nested.mkdir(parents=True)
    content = nested / "content.txt"
    content.write_text("content", encoding="utf-8")

    try:
        assert gitcode_module._validate_worktree(candidate) == (1, 7)
    finally:
        shutil.rmtree(extended_candidate)


@pytest.mark.asyncio
async def test_gitcode_request_uses_bearer_and_retries_transient_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    url = "https://api.gitcode.com/api/v5/repos/acme/demo"
    FakeSession.responses = {
        url: [FakeResponse({}, status=503), FakeResponse({"default_branch": "main"})],
    }
    FakeSession.requests = []
    monkeypatch.setattr(gitcode_module.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)
    monkeypatch.setattr(retry_module, "_jitter_seconds", lambda: 0.0)

    payload = await gitcode_module._request_json(url, "secret-pat")

    assert payload == {"default_branch": "main"}
    assert [request[0] for request in FakeSession.requests] == [url, url]
    assert all(request[1]["headers"]["Authorization"] == "Bearer secret-pat" for request in FakeSession.requests)


@pytest.mark.asyncio
async def test_gitcode_request_does_not_retry_authentication_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    url = "https://api.gitcode.com/api/v5/repos/acme/demo"
    FakeSession.responses = {url: [FakeResponse({}, status=401)]}
    FakeSession.requests = []
    monkeypatch.setattr(gitcode_module.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(retry_module, "_sleep", _no_retry_sleep)

    with pytest.raises(BaseError) as caught:
        await gitcode_module._request_json(url, "secret-pat")

    assert [request[0] for request in FakeSession.requests] == [url]
    assert "secret-pat" not in str(caught.value)


@pytest.mark.asyncio
async def test_gitcode_rejects_oversized_json_response_before_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    url = "https://api.gitcode.com/api/v5/repos/acme/demo"
    response = FakeResponse({"default_branch": "main"})
    response.headers = {"Content-Length": str(16 * 1024 * 1024 + 1)}
    FakeSession.responses = {url: [response]}
    monkeypatch.setattr(gitcode_module.aiohttp, "ClientSession", FakeSession)

    with pytest.raises(BaseError, match="size limit"):
        await gitcode_module._request_json(url, "secret-pat")


def test_gitcode_rewrites_repository_relative_readme_links_to_exact_revision() -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    revision = "a" * 40
    markdown = (
        "[Guide](docs/guide.md#intro)\n"
        '![Logo](images/logo.png "Logo")\n'
        "[Root](/docs/root.md)\n"
        "[Anchor](#section)\n"
        "[External](https://example.com/readme.md)\n"
    )

    rewritten = gitcode_module._rewrite_readme_links(
        markdown,
        owner="acme",
        repo="demo",
        revision=revision,
    )

    assert f"[Guide](https://gitcode.com/acme/demo/blob/{revision}/docs/guide.md#intro)" in rewritten
    assert f'![Logo](https://gitcode.com/acme/demo/raw/{revision}/images/logo.png "Logo")' in rewritten
    assert f"[Root](https://gitcode.com/acme/demo/blob/{revision}/docs/root.md)" in rewritten
    assert "[Anchor](#section)" in rewritten
    assert "[External](https://example.com/readme.md)" in rewritten


@pytest.mark.asyncio
async def test_gitcode_fetches_enabled_rest_resources_and_normalizes_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    head = "a" * 40
    timestamp = "2026-01-01T00:00:00Z"
    base = "https://api.gitcode.com/api/v5/repos/acme/demo"
    FakeSession.responses = {
        base: [
            FakeResponse(
                {
                    "default_branch": "main",
                    "default_branch_sha": head,
                    "pushed_at": timestamp,
                }
            )
        ],
        f"{base}/contents/README.md": [
            FakeResponse(
                {
                    "encoding": "base64",
                    "content": base64.b64encode(b"# Demo").decode(),
                    "html_url": "https://gitcode.com/acme/demo/blob/main/README.md",
                }
            )
        ],
        f"{base}/issues": [
            FakeResponse(
                [
                    {
                        "number": "7",
                        "title": "Issue",
                        "body": "issue body",
                        "updated_at": timestamp,
                        "html_url": "https://gitcode.com/acme/demo/issues/7",
                    },
                    {
                        "number": 99,
                        "title": "PR duplicate",
                        "updated_at": timestamp,
                        "pull_request": {"url": "duplicate"},
                    },
                ]
            )
        ],
        f"{base}/pulls": [
            FakeResponse(
                [
                    {
                        "number": 8,
                        "title": "PR",
                        "body": "pr body",
                        "updated_at": timestamp,
                        "html_url": "https://gitcode.com/acme/demo/pulls/8",
                    }
                ]
            )
        ],
        f"{base}/commits": [
            FakeResponse(
                [
                    {
                        "id": head,
                        "message": "Commit",
                        "committed_date": timestamp,
                        "web_url": f"https://gitcode.com/acme/demo/commit/{head}",
                    }
                ]
            )
        ],
    }
    FakeSession.requests = []
    monkeypatch.setattr(gitcode_module.aiohttp, "ClientSession", FakeSession)
    provider = GitCodeFetchService(
        gitcode_config(tmp_path, resources=["readme", "issues", "pull_requests", "commits"]),
        home=tmp_path,
    )

    batches = await _batches(provider, run_id="run-a", cursor=None)
    items = [item for batch in batches for item in batch.items]

    assert {item.logical_id for item in items} == {
        "gitcode:acme/demo:repository:readme",
        "gitcode:acme/demo:issue:7",
        "gitcode:acme/demo:pull_request:8",
        f"gitcode:acme/demo:commit:{head}",
    }
    assert {item.metadata["resource"] for item in items} == {
        "readme",
        "issues",
        "pull_requests",
        "commits",
    }
    assert all(item.metadata["repository"] == "gitcode:acme/demo" for item in items)
    assert not (tmp_path / "materialized-sources").exists()


@pytest.mark.asyncio
async def test_gitcode_readme_falls_back_to_readme_md_and_rejects_invalid_base64(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    base = "https://api.gitcode.com/api/v5/repos/acme/demo"
    FakeSession.responses = {
        base: [FakeResponse({"default_branch": "main", "pushed_at": "2026-01-01T00:00:00Z"})],
        f"{base}/branches/main": [FakeResponse({"id": "b" * 40})],
        f"{base}/contents/README.md": [FakeResponse({}, status=404)],
        f"{base}/contents/README_CN.md": [FakeResponse({"encoding": "base64", "content": "not base64!"})],
    }
    FakeSession.requests = []
    monkeypatch.setattr(gitcode_module.aiohttp, "ClientSession", FakeSession)
    provider = GitCodeFetchService(gitcode_config(tmp_path, resources=["readme"]), home=tmp_path)

    with pytest.raises(BaseError):
        await _batches(provider, run_id="run-a", cursor=None)

    assert [request[0] for request in FakeSession.requests] == [
        base,
        f"{base}/branches/main",
        f"{base}/contents/README.md",
        f"{base}/contents/README_CN.md",
    ]


@pytest.mark.asyncio
async def test_gitcode_resolves_exact_default_branch_head_for_code_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    head = "b" * 40
    timestamp = "2026-01-01T00:00:00Z"
    base = "https://api.gitcode.com/api/v5/repos/acme/demo"
    FakeSession.responses = {
        base: [FakeResponse({"default_branch": "main"})],
        f"{base}/branches/main": [FakeResponse({"commit": {"id": head, "committed_date": timestamp}})],
    }
    FakeSession.requests = []
    monkeypatch.setattr(gitcode_module.aiohttp, "ClientSession", FakeSession)
    provider = GitCodeFetchService(gitcode_config(tmp_path, resources=["code"]), home=tmp_path)

    candidates = await provider.prepare_run(
        run_id="run-a",
        run_started_at=datetime.now(UTC),
        cursor=None,
    )

    assert len(candidates) == 1
    assert candidates[0]["head_sha"] == head
    assert candidates[0]["candidate_time"] == timestamp
    assert candidates[0]["stable_id"] == "gitcode:acme/demo:repository:code"
    assert [request[0] for request in FakeSession.requests] == [base, f"{base}/branches/main"]


@pytest.mark.asyncio
async def test_gitcode_resource_lanes_apply_time_range_and_default_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    run_started_at = datetime(2026, 1, 10, tzinfo=UTC)
    recent = (run_started_at - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    old = (run_started_at - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    issues = [
        {
            "number": index,
            "title": f"Issue {index}",
            "body": "body",
            "updated_at": recent if index <= 30 else old,
        }
        for index in range(1, 32)
    ]
    base = "https://api.gitcode.com/api/v5/repos/acme/demo"
    FakeSession.responses = {
        base: [FakeResponse({"default_branch": "main"})],
        f"{base}/issues": [FakeResponse(issues)],
    }
    monkeypatch.setattr(gitcode_module.aiohttp, "ClientSession", FakeSession)
    provider = GitCodeFetchService(
        gitcode_config(
            tmp_path,
            resources=["issues"],
            time_range={"mode": "recent", "recent_days": 3},
        ),
        home=tmp_path,
    )

    batches = await _batches(
        provider,
        run_id="run-a",
        cursor=None,
        run_started_at=run_started_at,
    )

    assert [len(batch.items) for batch in batches] == [20, 5]
    assert all(not item.logical_id.endswith(":31") for batch in batches for item in batch.items)


@pytest.mark.asyncio
async def test_gitcode_quota_balances_repeatable_lanes_and_bounds_singletons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    head = "c" * 40
    timestamp = "2026-01-01T00:00:00Z"
    base = "https://api.gitcode.com/api/v5/repos/acme/demo"
    FakeSession.responses = {
        base: [
            FakeResponse(
                {
                    "default_branch": "main",
                    "default_branch_sha": head,
                    "pushed_at": timestamp,
                }
            )
        ],
        f"{base}/contents/README.md": [
            FakeResponse(
                {
                    "encoding": "base64",
                    "content": base64.b64encode(b"# Demo").decode(),
                }
            )
        ],
        f"{base}/issues": [
            FakeResponse([{"number": index, "title": str(index), "updated_at": timestamp} for index in range(1, 19)])
        ],
        f"{base}/pulls": [
            FakeResponse([{"number": index, "title": str(index), "updated_at": timestamp} for index in range(101, 119)])
        ],
        f"{base}/commits": [
            FakeResponse(
                [
                    {"id": f"{index:040x}", "message": str(index), "committed_date": timestamp}
                    for index in range(201, 219)
                ]
            )
        ],
    }
    monkeypatch.setattr(gitcode_module.aiohttp, "ClientSession", FakeSession)
    provider = GitCodeFetchService(
        gitcode_config(
            tmp_path,
            resources=["readme", "issues", "pull_requests", "commits", "code"],
            max_items_per_run=8,
        ),
        home=tmp_path,
    )

    candidates = await provider.prepare_run(
        run_id="run-a",
        run_started_at=datetime.now(UTC),
        cursor=None,
    )
    counts = {
        lane: sum(candidate["resource_lane"] == lane for candidate in candidates)
        for lane in ("readme", "issue", "pull_request", "commit", "code")
    }

    assert counts == {"readme": 1, "issue": 2, "pull_request": 2, "commit": 2, "code": 1}


@pytest.mark.asyncio
async def test_gitcode_cursor_merges_same_timestamp_and_rejects_malformed_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    timestamp = "2026-01-01T00:00:00Z"
    base = "https://api.gitcode.com/api/v5/repos/acme/demo"
    provider = GitCodeFetchService(gitcode_config(tmp_path, resources=["issues"]), home=tmp_path)
    monkeypatch.setattr(gitcode_module.aiohttp, "ClientSession", FakeSession)

    def install(*numbers: int) -> None:
        FakeSession.responses = {
            base: [FakeResponse({"default_branch": "main"})],
            f"{base}/issues": [
                FakeResponse(
                    [
                        {
                            "number": number,
                            "title": str(number),
                            "body": str(number),
                            "updated_at": timestamp,
                        }
                        for number in numbers
                    ]
                )
            ],
        }

    install(1)
    first = await _batches(provider, run_id="run-a", cursor=None)
    cursor = first[-1].next_cursor
    assert cursor is not None

    install(1, 2)
    second = await _batches(provider, run_id="run-b", cursor=cursor)
    assert [item.logical_id for item in second[0].items] == ["gitcode:acme/demo:issue:2"]
    completed = second[-1].next_cursor["_selection"]["completed"]
    assert [receipt["stable_id"] for receipt in completed] == [
        "gitcode:acme/demo:issue:1",
        "gitcode:acme/demo:issue:2",
    ]

    with pytest.raises(BaseError):
        await _batches(provider, run_id="run-c", cursor={"issues": "bad"})


@pytest.mark.asyncio
async def test_gitcode_pagination_rejects_repeated_page_without_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    timestamp = "2026-01-01T00:00:00Z"
    page = [{"number": index, "title": str(index), "updated_at": timestamp} for index in range(100)]
    base = "https://api.gitcode.com/api/v5/repos/acme/demo"
    FakeSession.responses = {
        base: [FakeResponse({"default_branch": "main"})],
        f"{base}/issues": [FakeResponse(page), FakeResponse(page)],
    }
    monkeypatch.setattr(gitcode_module.aiohttp, "ClientSession", FakeSession)
    provider = GitCodeFetchService(gitcode_config(tmp_path, resources=["issues"]), home=tmp_path)

    with pytest.raises(BaseError):
        await _batches(provider, run_id="run-a", cursor=None)


@pytest.mark.asyncio
async def test_gitcode_bounds_content_and_keeps_full_payload_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    body = "x" * 2_100_100
    issue = {"number": 7, "title": "Large", "body": body, "updated_at": "2026-01-01T00:00:00Z"}
    base = "https://api.gitcode.com/api/v5/repos/acme/demo"
    FakeSession.responses = {
        base: [FakeResponse({"default_branch": "main"})],
        f"{base}/issues": [FakeResponse([issue])],
    }
    monkeypatch.setattr(gitcode_module.aiohttp, "ClientSession", FakeSession)
    provider = GitCodeFetchService(gitcode_config(tmp_path, resources=["issues"]), home=tmp_path)

    batches = await _batches(provider, run_id="run-a", cursor=None)
    item = batches[0].items[0]

    assert item.content is not None and len(item.content) == 2_000_000
    assert item.metadata["content_truncated"] is True
    assert item.metadata["raw_snapshot_omitted"] is True
    assert (
        item.revision_id
        == hashlib.sha256(
            json.dumps(issue, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )


def _install_code_responses(*, head: str, timestamp: str = "2026-01-01T00:00:00Z") -> None:
    base = "https://api.gitcode.com/api/v5/repos/acme/demo"
    FakeSession.responses = {
        base: [
            FakeResponse(
                {
                    "default_branch": "main",
                    "default_branch_sha": head,
                    "pushed_at": timestamp,
                }
            )
        ],
        "https://api.gitcode.com/api/v5/user": [FakeResponse({"login": "gitcode-user"})],
    }
    FakeSession.requests = []


@pytest.mark.asyncio
async def test_gitcode_materializes_exact_head_with_secret_free_git_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    head = "d" * 40
    _install_code_responses(head=head)
    monkeypatch.setattr(gitcode_module.aiohttp, "ClientSession", FakeSession)
    calls: list[tuple[tuple[str, ...], Path, dict[str, str], float]] = []

    async def fake_run_git_command(
        args: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> bytes:
        calls.append((tuple(args), cwd, dict(env), timeout))
        assert "secret-pat" not in repr(args)
        assert "secret-pat" not in str(cwd)
        assert env["PERSONAL_CONTEXT_GIT_USERNAME"] == "gitcode-user"
        assert env["PERSONAL_CONTEXT_GIT_PASSWORD"] == "secret-pat"
        askpass = Path(env["GIT_ASKPASS"])
        assert askpass.is_file()
        assert all(
            "secret-pat" not in path.read_text(encoding="utf-8") for path in askpass.parent.rglob("*") if path.is_file()
        )
        if "init" in args:
            (cwd / ".git").mkdir()
        if "rev-parse" in args:
            return f"{head}\n".encode()
        if "ls-tree" in args:
            return f"100644 blob {'1' * 40} 4\tREADME.md\0".encode()
        if "checkout" in args:
            (cwd / "README.md").write_text("code", encoding="utf-8")
        return b""

    monkeypatch.setattr(gitcode_module, "_run_git_command", fake_run_git_command, raising=False)
    provider = GitCodeFetchService(gitcode_config(tmp_path, resources=["code"]), home=tmp_path)

    batches = await _batches(provider, run_id="run-a", cursor=None)
    batch = batches[0]
    candidate = tmp_path / "materialized-sources" / "gitcode" / "gitcode-demo" / "candidate"

    assert batch.materialized_source_path == str(candidate.resolve())
    assert batch.materialized_revision == head
    assert (candidate / "README.md").read_text(encoding="utf-8") == "code"
    assert not (candidate / ".git").exists()
    assert not (candidate.parent / "credentials").exists()
    assert all(call[3] == 20 * 60 for call in calls)
    assert all(call[2]["GIT_CONFIG_NOSYSTEM"] == "1" for call in calls)
    assert all(call[2]["GIT_CONFIG_GLOBAL"] == os.devnull for call in calls)
    assert all(call[2]["GIT_TERMINAL_PROMPT"] == "0" for call in calls)
    assert all(call[2]["GIT_ASKPASS_REQUIRE"] == "force" for call in calls)
    assert all(call[2]["GIT_LFS_SKIP_SMUDGE"] == "1" for call in calls)
    assert all("credential.helper=" in call[0] for call in calls)
    assert all("core.longpaths=true" in call[0] for call in calls)
    remote_call = next(call[0] for call in calls if "remote" in call[0])
    assert remote_call[-1] == "https://gitcode.com/acme/demo.git"
    fetch_call = next(call[0] for call in calls if "fetch" in call[0])
    assert "--depth=1" in fetch_call
    assert "--no-tags" in fetch_call
    assert fetch_call[-1] == "refs/heads/main"
    for args, cwd, env, timeout in calls:
        public_env = {key: value for key, value in env.items() if key != "PERSONAL_CONTEXT_GIT_PASSWORD"}
        assert "secret-pat" not in repr((args, cwd, public_env, timeout))

    await provider.abort_run(run_id="different-run")
    await provider.commit_run(run_id="different-run")
    assert candidate.exists()
    await provider.commit_run(run_id="run-a")
    assert not candidate.exists()
    assert not (tmp_path / "materialized-sources").exists()


@pytest.mark.asyncio
async def test_gitcode_sha_mismatch_removes_candidate_and_redacts_pat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    head = "e" * 40
    _install_code_responses(head=head)
    monkeypatch.setattr(gitcode_module.aiohttp, "ClientSession", FakeSession)

    async def fake_run_git_command(
        args: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> bytes:
        del env, timeout
        if "init" in args:
            (cwd / ".git").mkdir()
        if "rev-parse" in args:
            return f"{'f' * 40}\n".encode()
        return b""

    monkeypatch.setattr(gitcode_module, "_run_git_command", fake_run_git_command, raising=False)
    provider = GitCodeFetchService(gitcode_config(tmp_path, resources=["code"]), home=tmp_path)

    with pytest.raises(BaseError) as caught:
        await _batches(provider, run_id="run-a", cursor=None)

    assert "secret-pat" not in str(caught.value)
    assert not (tmp_path / "materialized-sources").exists()


@pytest.mark.asyncio
async def test_gitcode_cancellation_removes_candidate_and_askpass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    head = "a" * 40
    _install_code_responses(head=head)
    monkeypatch.setattr(gitcode_module.aiohttp, "ClientSession", FakeSession)

    async def cancel_fetch(
        args: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float,
    ) -> bytes:
        del env, timeout
        if "init" in args:
            (cwd / ".git").mkdir()
        if "fetch" in args:
            raise asyncio.CancelledError
        return b""

    monkeypatch.setattr(gitcode_module, "_run_git_command", cancel_fetch, raising=False)
    provider = GitCodeFetchService(gitcode_config(tmp_path, resources=["code"]), home=tmp_path)

    with pytest.raises(asyncio.CancelledError):
        await _batches(provider, run_id="run-a", cursor=None)

    assert not (tmp_path / "materialized-sources").exists()


@pytest.mark.parametrize(
    "tree",
    [
        f"120000 blob {'1' * 40} 4\tlink\0".encode(),
        f"160000 commit {'1' * 40} -\tsubmodule\0".encode(),
        f"100644 blob {'1' * 40} 4\t../escape\0".encode(),
        f"100644 blob {'1' * 40} 4\tdir\\file\0".encode(),
        (f"100644 blob {'1' * 40} 4\tREADME.md\0100644 blob {'2' * 40} 4\treadme.md\0").encode(),
    ],
)
def test_gitcode_rejects_unsafe_git_tree(tree: bytes) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    with pytest.raises(BaseError):
        gitcode_module._validate_git_tree(tree)


def test_gitcode_accepts_git_ls_tree_size_padding() -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    tree = f"100644 blob {'1' * 40}      4\tREADME.md\0".encode()

    assert gitcode_module._validate_git_tree(tree) == (1, 4)


def test_gitcode_git_tree_enforces_file_count_size_and_path_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    one = f"100644 blob {'1' * 40} 2\ta.txt\0".encode()
    two = one + f"100644 blob {'2' * 40} 2\tb.txt\0".encode()
    monkeypatch.setattr(gitcode_module, "_MAX_GIT_FILES", 1, raising=False)
    with pytest.raises(BaseError, match="too many files"):
        gitcode_module._validate_git_tree(two)

    monkeypatch.setattr(gitcode_module, "_MAX_GIT_FILES", 100_000, raising=False)
    monkeypatch.setattr(gitcode_module, "_MAX_WORKTREE_BYTES", 1, raising=False)
    with pytest.raises(BaseError, match="size limit"):
        gitcode_module._validate_git_tree(one)

    monkeypatch.setattr(gitcode_module, "_MAX_WORKTREE_BYTES", 1024, raising=False)
    monkeypatch.setattr(gitcode_module, "_MAX_GIT_PATH_BYTES", 3, raising=False)
    with pytest.raises(BaseError, match="path limit"):
        gitcode_module._validate_git_tree(one)


def test_gitcode_rejects_hardlinked_worktree(tmp_path: Path) -> None:
    import openjiuwen.harness.personal_context.fetch.gitcode as gitcode_module

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    first = candidate / "a.txt"
    first.write_text("data", encoding="utf-8")
    second = candidate / "b.txt"
    try:
        second.hardlink_to(first)
    except OSError:
        pytest.skip("hardlink creation is unavailable on this host")

    with pytest.raises(BaseError, match="hardlink"):
        gitcode_module._validate_worktree(candidate)
