"""Offline contracts for GitHub bounded candidate discovery."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random
from typing import Mapping

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch import github as github_module
from openjiuwen.harness.personal_context.fetch.cursor_selection import (
    record_completed_candidates,
    select_latest_candidates,
)
from openjiuwen.harness.personal_context.fetch.github import GitHubFetchService


def _config(
    tmp_path: Path,
    *,
    resources: list[str],
    max_items: int = 19,
    time_range: Mapping[str, object] | None = None,
) -> PersonalContextFetchServiceConfig:
    return PersonalContextFetchServiceConfig.model_validate(
        {
            "service_id": "github-bounded",
            "provider": "github",
            "enabled": True,
            "interval_seconds": 60,
            "max_items_per_run": max_items,
            "time_range": dict(time_range or {"mode": "all"}),
            "source": {"owner": "acme", "repo": "demo", "resources": resources},
            "credentials": {"token": "secret-token"},
        }
    )


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: object) -> datetime:
    assert isinstance(value, str)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _commit(index: int, candidate_time: datetime, *, message: str | None = None) -> dict[str, object]:
    sha = f"{index:040x}"
    return {
        "sha": sha,
        "html_url": f"https://github.com/acme/demo/commit/{sha}",
        "commit": {
            "message": message or f"Commit {index}",
            "committer": {"date": _stamp(candidate_time)},
            "author": {"date": _stamp(candidate_time - timedelta(seconds=1))},
        },
    }


def _revision(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _commit_candidate(payload: Mapping[str, object]) -> dict[str, object]:
    sha = str(payload["sha"])
    nested = payload["commit"]
    assert isinstance(nested, Mapping)
    committer = nested["committer"]
    assert isinstance(committer, Mapping)
    return {
        "stable_id": f"github:acme/demo:commit:{sha}",
        "revision_id": _revision(payload),
        "candidate_time": str(committer["date"]),
        "resource_lane": "commit",
        "locator": str(payload["html_url"]),
    }


def _candidate_fingerprint(candidate: Mapping[str, object]) -> tuple[str, str, str, str]:
    return (
        str(candidate["resource_lane"]),
        str(candidate["stable_id"]),
        str(candidate["revision_id"]),
        str(candidate["candidate_time"]),
    )


class CommitApi:
    """Immutable, parameter-aware GitHub REST stub with deliberately unordered commits."""

    def __init__(
        self,
        commits: list[dict[str, object]],
        *,
        head_sha: str,
        head_time: datetime,
        endpoint_inclusive: bool = True,
        expose_repository_head: bool = False,
        branch_payload: Mapping[str, object] | None = None,
    ) -> None:
        self.commits = tuple(commits)
        self.head_sha = head_sha
        self.head_time = head_time
        self.endpoint_inclusive = endpoint_inclusive
        self.expose_repository_head = expose_repository_head
        self.branch_payload = dict(branch_payload) if branch_payload is not None else None
        self.requests: list[tuple[str, dict[str, object], bool]] = []

    async def __call__(
        self,
        url: str,
        _token: str,
        *,
        params: Mapping[str, object] | None = None,
        allow_not_found: bool = False,
    ) -> object | None:
        request_params = dict(params or {})
        self.requests.append((url, request_params, allow_not_found))
        if url.endswith("/repos/acme/demo"):
            # pushed_at is only a partition hint and intentionally differs from fixed HEAD time.
            return {
                "default_branch": "main",
                "pushed_at": _stamp(self.head_time + timedelta(days=5)),
                **({"sha": "e" * 40} if self.expose_repository_head else {}),
            }
        if url.endswith("/repos/acme/demo/branches/main"):
            if self.branch_payload is not None:
                return dict(self.branch_payload)
            return {
                "name": "main",
                "commit": {
                    "sha": self.head_sha,
                    "commit": {"committer": {"date": _stamp(self.head_time)}},
                },
            }
        if url.endswith("/repos/acme/demo/readme"):
            return {"encoding": "base64", "content": base64.b64encode(b"# Demo").decode()}
        if not url.endswith("/repos/acme/demo/commits"):
            raise AssertionError(f"unexpected URL: {url}")

        values = list(self.commits)
        since = request_params.get("since")
        until = request_params.get("until")
        if since is not None:
            lower = _parse(since)
            values = [
                item
                for item in values
                if (
                    _parse(github_module._iso_value(item, commit=True)) >= lower
                    if self.endpoint_inclusive
                    else _parse(github_module._iso_value(item, commit=True)) > lower
                )
            ]
        if until is not None:
            upper = _parse(until)
            values = [
                item
                for item in values
                if (
                    _parse(github_module._iso_value(item, commit=True)) <= upper
                    if self.endpoint_inclusive
                    else _parse(github_module._iso_value(item, commit=True)) < upper
                )
            ]
        # SHA order is stable for pagination but intentionally unrelated to commit time.
        values.sort(key=lambda item: str(item["sha"]), reverse=True)
        page = int(request_params.get("page", 1))
        per_page = int(request_params.get("per_page", 100))
        return values[(page - 1) * per_page : page * per_page]

    @property
    def commit_requests(self) -> list[dict[str, object]]:
        return [params for url, params, _allow in self.requests if url.endswith("/commits")]


def _issue(number: int, candidate_time: datetime, *, pull_noise: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "number": number,
        "title": f"Issue {number}",
        "body": f"Body {number}",
        "updated_at": _stamp(candidate_time),
        "html_url": f"https://github.com/acme/demo/issues/{number}",
    }
    if pull_noise:
        payload["pull_request"] = {"url": f"https://api.github.com/repos/acme/demo/pulls/{number}"}
    return payload


def _pull(number: int, candidate_time: datetime) -> dict[str, object]:
    return {
        "number": number,
        "title": f"Pull {number}",
        "body": f"Body {number}",
        "updated_at": _stamp(candidate_time),
        "html_url": f"https://github.com/acme/demo/pull/{number}",
    }


def _issue_candidate(payload: Mapping[str, object]) -> dict[str, object]:
    number = str(payload["number"])
    return {
        "stable_id": f"github:acme/demo:issue:{number}",
        "revision_id": _revision(payload),
        "candidate_time": str(payload["updated_at"]),
        "resource_lane": "issue",
        "locator": str(payload["html_url"]),
    }


def _pull_candidate(payload: Mapping[str, object]) -> dict[str, object]:
    candidate = _issue_candidate(payload)
    candidate["stable_id"] = str(candidate["stable_id"]).replace(":issue:", ":pull_request:")
    candidate["resource_lane"] = "pull_request"
    return candidate


class OrderedIssueApi:
    def __init__(self, pages: list[list[dict[str, object]]]) -> None:
        self.pages = tuple(tuple(page) for page in pages)
        self.requests: list[tuple[str, dict[str, object]]] = []

    async def __call__(
        self,
        url: str,
        _token: str,
        *,
        params: Mapping[str, object] | None = None,
        allow_not_found: bool = False,
    ) -> object | None:
        del allow_not_found
        request_params = dict(params or {})
        self.requests.append((url, request_params))
        if url.endswith("/repos/acme/demo"):
            return {"default_branch": "main"}
        if not url.endswith("/repos/acme/demo/issues"):
            raise AssertionError(f"unexpected URL: {url}")
        assert request_params["state"] == "all"
        assert request_params["sort"] == "updated"
        assert request_params["direction"] == "desc"
        page = int(request_params["page"])
        return list(self.pages[page - 1]) if page <= len(self.pages) else []

    @property
    def issue_requests(self) -> list[dict[str, object]]:
        return [params for url, params in self.requests if url.endswith("/issues")]


class ScriptedPullApi:
    def __init__(self, pages: list[list[dict[str, object]]]) -> None:
        self.pages = tuple(tuple(page) for page in pages)
        self.requests: list[tuple[str, dict[str, object]]] = []

    async def __call__(
        self,
        url: str,
        _token: str,
        *,
        params: Mapping[str, object] | None = None,
        allow_not_found: bool = False,
    ) -> object | None:
        del allow_not_found
        request_params = dict(params or {})
        self.requests.append((url, request_params))
        if url.endswith("/repos/acme/demo"):
            return {"default_branch": "main"}
        if not url.endswith("/repos/acme/demo/pulls"):
            raise AssertionError(f"unexpected URL: {url}")
        assert request_params["state"] == "all"
        assert request_params["sort"] == "updated"
        assert request_params["direction"] == "desc"
        assert request_params["per_page"] == 100
        page = int(request_params["page"])
        return list(self.pages[page - 1]) if page <= len(self.pages) else []

    @property
    def pull_requests(self) -> list[dict[str, object]]:
        return [params for url, params in self.requests if url.endswith("/pulls")]


class MixedApi(CommitApi):
    def __init__(
        self,
        commits: list[dict[str, object]],
        issues: list[dict[str, object]],
        pulls: list[dict[str, object]],
        *,
        head_sha: str,
        head_time: datetime,
    ) -> None:
        super().__init__(commits, head_sha=head_sha, head_time=head_time)
        self.ordered = {
            "issues": tuple(
                sorted(issues, key=lambda item: (str(item["updated_at"]), int(item["number"])), reverse=True)
            ),
            "pulls": tuple(
                sorted(pulls, key=lambda item: (str(item["updated_at"]), int(item["number"])), reverse=True)
            ),
        }

    async def __call__(
        self,
        url: str,
        token: str,
        *,
        params: Mapping[str, object] | None = None,
        allow_not_found: bool = False,
    ) -> object | None:
        endpoint = url.rsplit("/", 1)[-1]
        if endpoint not in self.ordered:
            return await super().__call__(
                url,
                token,
                params=params,
                allow_not_found=allow_not_found,
            )
        request_params = dict(params or {})
        self.requests.append((url, request_params, allow_not_found))
        assert request_params["state"] == "all"
        assert request_params["sort"] == "updated"
        assert request_params["direction"] == "desc"
        page = int(request_params["page"])
        per_page = int(request_params["per_page"])
        values = self.ordered[endpoint]
        return list(values[(page - 1) * per_page : page * per_page])


class ScriptedCommitApi(CommitApi):
    def __init__(
        self,
        pages: list[list[dict[str, object]]],
        *,
        head_sha: str,
        head_time: datetime,
    ) -> None:
        super().__init__([], head_sha=head_sha, head_time=head_time)
        self.pages = tuple(tuple(page) for page in pages)

    async def __call__(
        self,
        url: str,
        token: str,
        *,
        params: Mapping[str, object] | None = None,
        allow_not_found: bool = False,
    ) -> object | None:
        if not url.endswith("/repos/acme/demo/commits"):
            return await super().__call__(
                url,
                token,
                params=params,
                allow_not_found=allow_not_found,
            )
        request_params = dict(params or {})
        self.requests.append((url, request_params, allow_not_found))
        page = int(request_params.get("page", 1))
        return list(self.pages[page - 1]) if page <= len(self.pages) else []


@pytest.mark.asyncio
async def test_large_commit_history_two_rounds_match_full_oracle_with_bounded_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        Path(github_module.__file__).resolve()
        == (Path(__file__).resolve().parents[4] / "openjiuwen/harness/personal_context/fetch/github.py").resolve()
    )
    head_time = datetime(2026, 6, 1, 12, tzinfo=UTC)
    commits = [_commit(index, head_time - timedelta(minutes=index)) for index in range(10_900)]
    api = CommitApi(commits, head_sha=str(commits[0]["sha"]), head_time=head_time)
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["commits"]), home=tmp_path)
    oracle = tuple(_commit_candidate(payload) for payload in commits)

    first = await provider.prepare_run(run_id="first", run_started_at=head_time, cursor=None)

    assert tuple(map(_candidate_fingerprint, first)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, None, 19))
    )
    assert len(api.commit_requests) < 30
    assert api.commit_requests
    assert {request.get("sha") for request in api.commit_requests} == {api.head_sha}

    cursor = record_completed_candidates(None, first)
    api.requests.clear()
    second = await provider.prepare_run(run_id="second", run_started_at=head_time, cursor=cursor)

    assert tuple(map(_candidate_fingerprint, second)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, cursor, 19))
    )
    assert len(api.commit_requests) < 30


@pytest.mark.asyncio
async def test_seeded_mixed_lanes_two_rounds_match_full_oracle_and_refresh_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    random = Random(20260906)
    head_time = datetime(2026, 6, 1, 12, tzinfo=UTC)
    times = [head_time - timedelta(minutes=random.randrange(0, 40)) for _ in range(72)]
    issues = [_issue(1_000 + index, times[index]) for index in range(24)]
    pulls = [_issue(2_000 + index, times[24 + index]) for index in range(24)]
    commits = [_commit(3_000 + index, times[48 + index]) for index in range(24)]
    api = MixedApi(
        commits,
        issues,
        pulls,
        head_sha=str(commits[0]["sha"]),
        head_time=head_time,
    )
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(
        _config(tmp_path, resources=["issues", "pull_requests", "commits"], max_items=17),
        home=tmp_path,
    )
    first_oracle = tuple(
        [*map(_issue_candidate, issues), *map(_pull_candidate, pulls), *map(_commit_candidate, commits)]
    )

    first = await provider.prepare_run(run_id="mixed-first", run_started_at=head_time, cursor=None)

    assert tuple(map(_candidate_fingerprint, first)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(first_oracle, None, 17))
    )
    cursor = record_completed_candidates(None, first)

    changed_issue = json.loads(json.dumps(issues[0]))
    changed_issue["body"] = "changed while keeping its candidate time"
    second_issues = [changed_issue, *issues[1:]]
    new_head_time = head_time + timedelta(minutes=1)
    new_head = _commit(4_000, new_head_time)
    second_commits = [new_head, *commits]
    second_api = MixedApi(
        second_commits,
        second_issues,
        pulls,
        head_sha=str(new_head["sha"]),
        head_time=new_head_time,
    )
    monkeypatch.setattr(github_module, "_request_json", second_api)
    second_oracle = tuple(
        [
            *map(_issue_candidate, second_issues),
            *map(_pull_candidate, pulls),
            *map(_commit_candidate, second_commits),
        ]
    )

    second = await provider.prepare_run(run_id="mixed-second", run_started_at=new_head_time, cursor=cursor)

    assert tuple(map(_candidate_fingerprint, second)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(second_oracle, cursor, 17))
    )
    assert second_api.head_sha == new_head["sha"]
    assert {request["sha"] for request in second_api.commit_requests} == {new_head["sha"]}


@pytest.mark.asyncio
async def test_readme_and_code_use_one_exact_head_not_repository_pushed_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head_time = datetime(2026, 5, 20, 8, tzinfo=UTC)
    head_sha = "a" * 40
    api = CommitApi([], head_sha=head_sha, head_time=head_time)
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["readme", "code"]), home=tmp_path)

    candidates = await provider.prepare_run(run_id="fixed-head", run_started_at=head_time, cursor=None)

    branch_calls = [request for request in api.requests if request[0].endswith("/branches/main")]
    readme_call = next(request for request in api.requests if request[0].endswith("/readme"))
    assert len(branch_calls) == 1
    assert readme_call[1]["ref"] == head_sha
    assert {candidate["resource_lane"] for candidate in candidates} == {"readme", "code"}
    assert {candidate["candidate_time"] for candidate in candidates} == {_stamp(head_time)}
    assert (
        next(candidate for candidate in candidates if candidate["resource_lane"] == "code")["revision_id"] == head_sha
    )


@pytest.mark.asyncio
async def test_issue_lane_stops_after_reliable_first_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)
    issues = [_issue(index, newest - timedelta(minutes=index)) for index in range(1000)]
    api = OrderedIssueApi([issues[index : index + 100] for index in range(0, len(issues), 100)])
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["issues"]), home=tmp_path)
    oracle = tuple(_issue_candidate(payload) for payload in issues)

    candidates = await provider.prepare_run(run_id="issues", run_started_at=newest, cursor=None)

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, None, 19))
    )
    assert len(api.issue_requests) == 1


@pytest.mark.asyncio
async def test_issue_lane_counts_pull_request_noise_for_progress_but_not_quota(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)
    noise = [_issue(index, newest - timedelta(seconds=index), pull_noise=True) for index in range(100)]
    issues = [_issue(1000 + index, newest - timedelta(minutes=10 + index)) for index in range(20)]
    api = OrderedIssueApi([noise, issues])
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["issues"]), home=tmp_path)

    candidates = await provider.prepare_run(run_id="noise", run_started_at=newest, cursor=None)

    assert len(candidates) == 19
    assert all(":issue:1" in str(candidate["stable_id"]) for candidate in candidates)
    assert len(api.issue_requests) == 2


@pytest.mark.asyncio
async def test_issue_lane_reads_complete_same_time_group_across_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tied_time = datetime(2026, 6, 1, 12, tzinfo=UTC)
    issues = [_issue(1000 - index, tied_time) for index in range(101)]
    api = OrderedIssueApi([issues[:100], issues[100:]])
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["issues"]), home=tmp_path)
    oracle = tuple(_issue_candidate(payload) for payload in issues)

    candidates = await provider.prepare_run(run_id="ties", run_started_at=tied_time, cursor=None)

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, None, 19))
    )
    assert len(api.issue_requests) == 2


@pytest.mark.asyncio
async def test_pull_short_out_of_order_page_matches_full_oracle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)
    pulls = [
        _pull(1, newest),
        _pull(2, newest - timedelta(minutes=2)),
        _pull(3, newest - timedelta(minutes=1)),
    ]
    api = ScriptedPullApi([pulls])
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["pull_requests"], max_items=2), home=tmp_path)
    oracle = tuple(_pull_candidate(payload) for payload in pulls)

    candidates = await provider.prepare_run(run_id="pull-short-reversal", run_started_at=newest, cursor=None)

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, None, 2))
    )
    assert api.pull_requests == [{"page": 1, "per_page": 100, "state": "all", "sort": "updated", "direction": "desc"}]


@pytest.mark.asyncio
async def test_pull_first_ordered_full_page_does_not_hide_newer_second_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)
    first_page = [_pull(index, newest - timedelta(seconds=index)) for index in range(100)]
    late_newer = _pull(1_000, newest + timedelta(minutes=1))
    pulls = [*first_page, late_newer]
    api = ScriptedPullApi([first_page, [late_newer]])
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["pull_requests"], max_items=1), home=tmp_path)
    oracle = tuple(_pull_candidate(payload) for payload in pulls)

    candidates = await provider.prepare_run(
        run_id="pull-later-page-newer",
        run_started_at=newest + timedelta(minutes=2),
        cursor=None,
    )

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, None, 1))
    )
    assert [params["page"] for params in api.pull_requests] == [1, 2]


@pytest.mark.asyncio
async def test_pull_full_out_of_order_page_reads_to_tail_and_matches_full_oracle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)
    pulls = [_pull(index, newest - timedelta(seconds=index)) for index in range(100)]
    pulls[40], pulls[41] = pulls[41], pulls[40]
    api = ScriptedPullApi([pulls])
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["pull_requests"], max_items=5), home=tmp_path)
    oracle = tuple(_pull_candidate(payload) for payload in pulls)

    candidates = await provider.prepare_run(run_id="pull-full-reversal", run_started_at=newest, cursor=None)

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, None, 5))
    )
    assert [params["page"] for params in api.pull_requests] == [1, 2]


@pytest.mark.asyncio
async def test_pull_same_id_payload_drift_during_pagination_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)
    first_page = [_pull(index, newest - timedelta(seconds=index)) for index in range(100)]
    drifted = json.loads(json.dumps(first_page[0]))
    drifted["body"] = "payload changed while the same listing was paginated"
    api = ScriptedPullApi([first_page, [drifted, _pull(1_000, newest - timedelta(days=1))]])
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["pull_requests"], max_items=1), home=tmp_path)

    with pytest.raises(BaseError, match="pulls payload changed during pagination"):
        await provider.prepare_run(run_id="pull-payload-drift", run_started_at=newest, cursor=None)


@pytest.mark.asyncio
async def test_pull_identical_overlap_and_same_time_group_use_stable_selector_across_rounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tied_time = datetime(2026, 6, 1, 12, tzinfo=UTC)
    first_page = [_pull(1_000 + index, tied_time) for index in range(100)]
    late_stable_winner = _pull(1, tied_time)
    pages = [first_page, [first_page[0], late_stable_winner]]
    oracle = tuple(_pull_candidate(payload) for payload in [*first_page, late_stable_winner])
    first_api = ScriptedPullApi(pages)
    monkeypatch.setattr(github_module, "_request_json", first_api)
    provider = GitHubFetchService(_config(tmp_path, resources=["pull_requests"], max_items=1), home=tmp_path)

    first = await provider.prepare_run(run_id="pull-tied-first", run_started_at=tied_time, cursor=None)

    assert tuple(map(_candidate_fingerprint, first)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, None, 1))
    )
    assert first[0]["stable_id"] == _pull_candidate(late_stable_winner)["stable_id"]
    assert [params["page"] for params in first_api.pull_requests] == [1, 2]

    cursor = record_completed_candidates(None, first)
    second_api = ScriptedPullApi(pages)
    monkeypatch.setattr(github_module, "_request_json", second_api)
    second = await provider.prepare_run(run_id="pull-tied-second", run_started_at=tied_time, cursor=cursor)

    assert tuple(map(_candidate_fingerprint, second)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, cursor, 1))
    )
    assert second[0]["stable_id"] != first[0]["stable_id"]
    assert [params["page"] for params in second_api.pull_requests] == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["recent", "fixed"])
async def test_pull_time_range_does_not_stop_before_later_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    run_started_at = datetime(2026, 6, 10, 12, tzinfo=UTC)
    old_page = [_pull(index, run_started_at - timedelta(days=10, seconds=index)) for index in range(100)]
    in_range = _pull(1_000, run_started_at - timedelta(days=1))
    api = ScriptedPullApi([old_page, [in_range]])
    monkeypatch.setattr(github_module, "_request_json", api)
    time_range: Mapping[str, object]
    if mode == "recent":
        time_range = {"mode": "recent", "recent_days": 2}
    else:
        time_range = {
            "mode": "fixed",
            "start_at": _stamp(run_started_at - timedelta(days=2)),
            "end_at": _stamp(run_started_at + timedelta(days=1)),
        }
    provider = GitHubFetchService(
        _config(tmp_path, resources=["pull_requests"], max_items=1, time_range=time_range),
        home=tmp_path,
    )
    oracle = (_pull_candidate(in_range),)

    candidates = await provider.prepare_run(run_id=f"pull-{mode}", run_started_at=run_started_at, cursor=None)

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, None, 1))
    )
    assert [params["page"] for params in api.pull_requests] == [1, 2]


@pytest.mark.asyncio
async def test_pull_old_known_payload_change_on_later_page_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = datetime(2026, 6, 1, tzinfo=UTC)
    original = _pull(999, origin + timedelta(minutes=50))
    changed = _pull(999, origin + timedelta(minutes=70))
    changed["body"] = "known payload changed"
    watermark = {
        "stable_id": "github:acme/demo:repository:readme",
        "revision_id": "old-readme",
        "candidate_time": _stamp(origin + timedelta(minutes=100)),
        "resource_lane": "readme",
        "locator": "https://github.com/acme/demo#readme",
    }
    cursor = record_completed_candidates(None, (_pull_candidate(original), watermark))
    history = [_pull(index, origin + timedelta(minutes=90) - timedelta(seconds=index)) for index in range(100)]
    api = ScriptedPullApi([history, [changed]])
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["pull_requests"], max_items=1), home=tmp_path)
    oracle = tuple([*map(_pull_candidate, history), _pull_candidate(changed)])

    candidates = await provider.prepare_run(run_id="pull-known-change", run_started_at=origin, cursor=cursor)

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, cursor, 1))
    )
    assert candidates[0]["stable_id"] == _pull_candidate(changed)["stable_id"]
    assert [params["page"] for params in api.pull_requests] == [1, 2]


@pytest.mark.asyncio
async def test_pull_invisible_known_id_requires_real_tail_without_emitting_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = datetime(2026, 6, 1, tzinfo=UTC)
    invisible = _pull(999, origin + timedelta(minutes=50))
    watermark = {
        "stable_id": "github:acme/demo:repository:readme",
        "revision_id": "old-readme",
        "candidate_time": _stamp(origin + timedelta(minutes=100)),
        "resource_lane": "readme",
        "locator": "https://github.com/acme/demo#readme",
    }
    cursor = record_completed_candidates(None, (_pull_candidate(invisible), watermark))
    current = [_pull(index, origin + timedelta(minutes=90) - timedelta(seconds=index)) for index in range(100)]
    api = ScriptedPullApi([current])
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["pull_requests"], max_items=1), home=tmp_path)
    oracle = tuple(_pull_candidate(payload) for payload in current)

    candidates = await provider.prepare_run(run_id="pull-invisible-known", run_started_at=origin, cursor=cursor)

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, cursor, 1))
    )
    assert all(candidate["stable_id"] != _pull_candidate(invisible)["stable_id"] for candidate in candidates)
    assert all(candidate["item"].operation == "upsert" for candidate in candidates)
    assert [params["page"] for params in api.pull_requests] == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "error_match"),
    [("repeated", "repeated a page"), ("short_no_progress", "did not advance")],
)
async def test_pull_repeated_or_nonadvancing_page_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    error_match: str,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)
    first_page = [_pull(index, newest - timedelta(seconds=index)) for index in range(100)]
    second_page = first_page if case == "repeated" else [first_page[0]]
    api = ScriptedPullApi([first_page, second_page])
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["pull_requests"], max_items=1), home=tmp_path)
    cursor = record_completed_candidates(None, (_pull_candidate(_pull(9_999, newest + timedelta(hours=1))),))
    before = json.loads(json.dumps(cursor))

    with pytest.raises(BaseError, match=error_match):
        await provider.prepare_run(run_id=f"pull-{case}", run_started_at=newest, cursor=cursor)

    assert cursor == before


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_time", [None, "not-a-date"])
async def test_pull_invalid_updated_time_fails_without_mutating_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_time: str | None,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)
    invalid = _pull(1, newest)
    if invalid_time is None:
        invalid.pop("updated_at")
    else:
        invalid["updated_at"] = invalid_time
    api = ScriptedPullApi([[invalid]])
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["pull_requests"]), home=tmp_path)
    cursor = record_completed_candidates(None, (_pull_candidate(_pull(9_999, newest)),))
    before = json.loads(json.dumps(cursor))

    with pytest.raises(BaseError, match="candidate has (no usable|an invalid) time"):
        await provider.prepare_run(run_id="pull-invalid-time", run_started_at=newest, cursor=cursor)

    assert cursor == before


@pytest.mark.asyncio
async def test_pull_discovery_shares_request_100_and_blocks_request_101(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)

    def full_pages(count: int) -> list[list[dict[str, object]]]:
        return [
            [_pull(page * 100 + index, newest - timedelta(seconds=page * 100 + index)) for index in range(100)]
            for page in range(count)
        ]

    success_pages = [*full_pages(98), [_pull(9_800, newest - timedelta(seconds=9_800))]]
    success_api = ScriptedPullApi(success_pages)
    monkeypatch.setattr(github_module, "_request_json", success_api)
    success_provider = GitHubFetchService(
        _config(tmp_path / "success", resources=["pull_requests"], max_items=1),
        home=tmp_path / "success",
    )

    candidates = await success_provider.prepare_run(run_id="pull-request-100", run_started_at=newest, cursor=None)

    assert tuple(map(_candidate_fingerprint, candidates)) == (
        _candidate_fingerprint(_pull_candidate(success_pages[0][0])),
    )
    assert len(success_api.requests) == 100
    assert len(success_api.pull_requests) == 99

    failure_api = ScriptedPullApi(full_pages(99))
    monkeypatch.setattr(github_module, "_request_json", failure_api)
    failure_provider = GitHubFetchService(
        _config(tmp_path / "failure", resources=["pull_requests"], max_items=1),
        home=tmp_path / "failure",
    )
    cursor = record_completed_candidates(None, (_pull_candidate(_pull(20_000, newest)),))
    before = json.loads(json.dumps(cursor))

    with pytest.raises(BaseError, match="metadata request budget"):
        await failure_provider.prepare_run(run_id="pull-request-101", run_started_at=newest, cursor=cursor)

    assert len(failure_api.requests) == 100
    assert len(failure_api.pull_requests) == 99
    assert cursor == before


@pytest.mark.asyncio
async def test_pull_discovery_propagates_cancellation_without_mutating_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)
    cursor = record_completed_candidates(None, (_pull_candidate(_pull(1, newest)),))
    before = json.loads(json.dumps(cursor))

    async def cancelling_api(
        url: str,
        _token: str,
        *,
        params: Mapping[str, object] | None = None,
        allow_not_found: bool = False,
    ) -> object | None:
        del params, allow_not_found
        if url.endswith("/repos/acme/demo"):
            return {"default_branch": "main"}
        if url.endswith("/repos/acme/demo/pulls"):
            raise asyncio.CancelledError
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(github_module, "_request_json", cancelling_api)
    provider = GitHubFetchService(_config(tmp_path, resources=["pull_requests"]), home=tmp_path)

    with pytest.raises(asyncio.CancelledError):
        await provider.prepare_run(run_id="pull-cancelled", run_started_at=newest, cursor=cursor)

    assert cursor == before


@pytest.mark.asyncio
async def test_pull_discovery_retains_only_lane_limit_between_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)
    pulls = [_pull(index, newest - timedelta(seconds=index)) for index in range(250)]
    api = ScriptedPullApi([pulls[:100], pulls[100:200], pulls[200:]])
    monkeypatch.setattr(github_module, "_request_json", api)
    original_bounded = github_module._bounded_lane_candidates
    before_lengths: list[int] = []
    after_lengths: list[int] = []

    def tracking_bounded(
        candidates: list[dict[str, object]],
        *,
        cursor: dict[str, object] | None,
        limit: int,
    ) -> None:
        before_lengths.append(len(candidates))
        original_bounded(candidates, cursor=cursor, limit=limit)
        after_lengths.append(len(candidates))

    monkeypatch.setattr(github_module, "_bounded_lane_candidates", tracking_bounded)
    provider = GitHubFetchService(_config(tmp_path, resources=["pull_requests"], max_items=3), home=tmp_path)

    candidates = await provider.prepare_run(run_id="pull-bounded-memory", run_started_at=newest, cursor=None)

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(
            _candidate_fingerprint,
            select_latest_candidates(tuple(_pull_candidate(payload) for payload in pulls), None, 3),
        )
    )
    assert before_lengths == [100, 103, 53]
    assert after_lengths == [3, 3, 3]


@pytest.mark.asyncio
async def test_commits_resolve_branch_even_when_repository_has_sha_and_pushed_at_hints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head_time = datetime(2026, 6, 1, 12, tzinfo=UTC)
    head = _commit(7, head_time)
    api = CommitApi(
        [head],
        head_sha=str(head["sha"]),
        head_time=head_time,
        expose_repository_head=True,
    )
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["commits"], max_items=1), home=tmp_path)

    candidates = await provider.prepare_run(run_id="hints", run_started_at=head_time, cursor=None)

    assert len([request for request in api.requests if request[0].endswith("/branches/main")]) == 1
    assert candidates[0]["stable_id"] == f"github:acme/demo:commit:{head['sha']}"
    assert {request["sha"] for request in api.commit_requests} == {head["sha"]}


@pytest.mark.asyncio
async def test_commit_discovery_rejects_branch_without_exact_head_even_when_repository_has_hints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head_time = datetime(2026, 6, 1, 12, tzinfo=UTC)
    head = _commit(8, head_time)
    api = CommitApi(
        [head],
        head_sha=str(head["sha"]),
        head_time=head_time,
        expose_repository_head=True,
        branch_payload={"name": "main"},
    )
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["commits"], max_items=1), home=tmp_path)

    with pytest.raises(BaseError, match="head SHA"):
        await provider.prepare_run(run_id="missing-head", run_started_at=head_time, cursor=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint_inclusive", [True, False])
async def test_commit_fixed_range_widening_matches_oracle_for_both_endpoint_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint_inclusive: bool,
) -> None:
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    commits = [
        _commit(1, start - timedelta(seconds=1)),
        _commit(2, start),
        _commit(3, start + timedelta(minutes=30)),
        _commit(4, end - timedelta(seconds=1)),
        _commit(5, end),
    ]
    api = CommitApi(
        commits,
        head_sha=str(commits[-1]["sha"]),
        head_time=end,
        endpoint_inclusive=endpoint_inclusive,
    )
    monkeypatch.setattr(github_module, "_request_json", api)
    time_range = {"mode": "fixed", "start_at": _stamp(start), "end_at": _stamp(end)}
    provider = GitHubFetchService(
        _config(tmp_path, resources=["commits"], max_items=10, time_range=time_range),
        home=tmp_path,
    )
    oracle = tuple(
        _commit_candidate(payload)
        for payload in commits
        if start <= _parse(github_module._iso_value(payload, commit=True)) < end
    )

    candidates = await provider.prepare_run(run_id="fixed", run_started_at=end, cursor=None)

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, None, 10))
    )
    assert all("sha" in request for request in api.commit_requests)


@pytest.mark.asyncio
async def test_commit_all_keeps_future_and_pre_1970_tails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head_time = datetime(2026, 6, 1, tzinfo=UTC)
    commits = [
        _commit(1, datetime(2201, 1, 1, tzinfo=UTC)),
        _commit(2, head_time),
        _commit(3, datetime(1960, 1, 1, tzinfo=UTC)),
        _commit(4, datetime(1800, 1, 1, tzinfo=UTC)),
    ]
    api = CommitApi(commits, head_sha=str(commits[1]["sha"]), head_time=head_time)
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["commits"], max_items=5), home=tmp_path)
    oracle = tuple(_commit_candidate(payload) for payload in commits)

    candidates = await provider.prepare_run(run_id="tails", run_started_at=head_time, cursor=None)

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, None, 5))
    )
    for request in api.commit_requests:
        for field_name in ("since", "until"):
            if field_name in request:
                year = _parse(request[field_name]).year
                assert 1970 <= year <= 2099


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint_inclusive", [True, False])
async def test_commit_fixed_range_spanning_supported_dates_does_not_narrow_remote_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint_inclusive: bool,
) -> None:
    head_time = datetime(2026, 6, 1, tzinfo=UTC)
    commits = [
        _commit(1, datetime(2201, 1, 1, tzinfo=UTC)),
        _commit(2, head_time),
        _commit(3, datetime(1960, 1, 1, tzinfo=UTC)),
    ]
    api = CommitApi(
        commits,
        head_sha=str(commits[1]["sha"]),
        head_time=head_time,
        endpoint_inclusive=endpoint_inclusive,
    )
    monkeypatch.setattr(github_module, "_request_json", api)
    time_range = {
        "mode": "fixed",
        "start_at": "1950-01-01T00:00:00Z",
        "end_at": "2202-01-01T00:00:00Z",
    }
    provider = GitHubFetchService(
        _config(tmp_path, resources=["commits"], max_items=5, time_range=time_range),
        home=tmp_path,
    )
    oracle = tuple(_commit_candidate(payload) for payload in commits)

    candidates = await provider.prepare_run(run_id="fixed-wide", run_started_at=head_time, cursor=None)

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, None, 5))
    )
    assert all("since" not in request and "until" not in request for request in api.commit_requests)


@pytest.mark.asyncio
async def test_old_known_commit_payload_change_wins_over_newer_unfinished_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head_time = datetime(2026, 6, 1, 12, tzinfo=UTC)
    original = [_commit(index, head_time - timedelta(minutes=index)) for index in range(800)]
    current = [dict(payload) for payload in original]
    changed = json.loads(json.dumps(current[500]))
    changed["extra_full_payload_field"] = "changed"
    current[500] = changed
    api = CommitApi(current, head_sha=str(current[0]["sha"]), head_time=head_time)
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["commits"]), home=tmp_path)
    initial_receipts = (_commit_candidate(original[0]), _commit_candidate(original[500]))
    cursor = record_completed_candidates(None, initial_receipts)
    oracle = tuple(_commit_candidate(payload) for payload in current)

    candidates = await provider.prepare_run(run_id="changed", run_started_at=head_time, cursor=cursor)

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, cursor, 19))
    )
    assert candidates[0]["stable_id"] == _commit_candidate(changed)["stable_id"]
    assert candidates[0]["revision_id"] == _revision(changed)


@pytest.mark.asyncio
async def test_global_metadata_budget_allows_request_100_and_blocks_request_101(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)
    noise = [_issue(index, newest - timedelta(seconds=index), pull_noise=True) for index in range(9_900)]
    successful_pages = [noise[index : index + 100] for index in range(0, 9_800, 100)]
    successful_pages.append([_issue(20_000, newest - timedelta(days=2))])
    success_api = OrderedIssueApi(successful_pages)
    monkeypatch.setattr(github_module, "_request_json", success_api)
    success_provider = GitHubFetchService(
        _config(tmp_path / "success", resources=["issues"], max_items=1),
        home=tmp_path / "success",
    )

    candidates = await success_provider.prepare_run(run_id="request-100", run_started_at=newest, cursor=None)

    assert len(candidates) == 1
    assert len(success_api.requests) == 100

    failing_pages = [noise[index : index + 100] for index in range(0, 9_900, 100)]
    failing_api = OrderedIssueApi(failing_pages)
    monkeypatch.setattr(github_module, "_request_json", failing_api)
    failing_provider = GitHubFetchService(
        _config(tmp_path / "failure", resources=["issues"], max_items=1),
        home=tmp_path / "failure",
    )

    with pytest.raises(BaseError, match="metadata request budget"):
        await failing_provider.prepare_run(run_id="request-101", run_started_at=newest, cursor=None)
    assert len(failing_api.requests) == 100


@pytest.mark.asyncio
async def test_ordered_lane_rejects_response_larger_than_requested_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)
    api = OrderedIssueApi([[_issue(index, newest - timedelta(seconds=index)) for index in range(101)]])
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["issues"]), home=tmp_path)

    with pytest.raises(BaseError, match="page size"):
        await provider.prepare_run(run_id="oversized-page", run_started_at=newest, cursor=None)


@pytest.mark.asyncio
async def test_ordered_lane_rejects_nonempty_short_page_without_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_time = datetime(2026, 6, 1, 12, tzinfo=UTC)
    first_page = [_issue(index, candidate_time) for index in range(100)]
    api = OrderedIssueApi([first_page, [first_page[0]]])
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["issues"]), home=tmp_path)

    with pytest.raises(BaseError, match="did not advance"):
        await provider.prepare_run(run_id="short-no-progress", run_started_at=candidate_time, cursor=None)


@pytest.mark.asyncio
async def test_issue_old_known_payload_change_is_found_and_deleted_known_id_needs_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)
    original = [_issue(index, newest - timedelta(minutes=index)) for index in range(201)]
    changed = json.loads(json.dumps(original[-1]))
    changed["extra_full_payload_field"] = "changed"
    current = [*original[:-1], changed]
    cursor = record_completed_candidates(
        None,
        (_issue_candidate(original[0]), _issue_candidate(original[-1])),
    )
    api = OrderedIssueApi([current[:100], current[100:200], current[200:]])
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["issues"]), home=tmp_path)
    oracle = tuple(_issue_candidate(payload) for payload in current)

    candidates = await provider.prepare_run(run_id="changed-issue", run_started_at=newest, cursor=cursor)

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, cursor, 19))
    )
    assert candidates[0]["stable_id"] == _issue_candidate(changed)["stable_id"]
    assert len(api.issue_requests) == 3

    deleted_api = OrderedIssueApi([original[:100], original[100:200]])
    monkeypatch.setattr(github_module, "_request_json", deleted_api)
    deleted_provider = GitHubFetchService(
        _config(tmp_path / "deleted", resources=["issues"]),
        home=tmp_path / "deleted",
    )
    deleted = await deleted_provider.prepare_run(run_id="deleted-issue", run_started_at=newest, cursor=cursor)
    assert tuple(map(_candidate_fingerprint, deleted)) == tuple(
        map(
            _candidate_fingerprint,
            select_latest_candidates(tuple(_issue_candidate(payload) for payload in original[:-1]), cursor, 19),
        )
    )
    assert len(deleted_api.issue_requests) == 3


@pytest.mark.asyncio
async def test_issue_known_id_outside_current_range_still_blocks_priority_one_early_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = datetime(2026, 6, 1, tzinfo=UTC)
    original = _issue(999, origin + timedelta(minutes=40))
    watermark = {
        "stable_id": "github:acme/demo:repository:readme",
        "revision_id": "old-readme",
        "candidate_time": _stamp(origin + timedelta(minutes=100)),
        "resource_lane": "readme",
        "locator": "https://github.com/acme/demo#readme",
    }
    cursor = record_completed_candidates(None, (_issue_candidate(original), watermark))
    history = [_issue(index, origin + timedelta(minutes=90) - timedelta(seconds=index)) for index in range(100)]
    changed = _issue(999, origin + timedelta(minutes=70))
    changed["body"] = "known payload changed after entering the configured range"
    api = OrderedIssueApi([history, [changed]])
    monkeypatch.setattr(github_module, "_request_json", api)
    time_range = {
        "mode": "fixed",
        "start_at": _stamp(origin + timedelta(minutes=50)),
        "end_at": _stamp(origin + timedelta(minutes=120)),
    }
    provider = GitHubFetchService(
        _config(tmp_path, resources=["issues"], max_items=1, time_range=time_range),
        home=tmp_path,
    )
    oracle = tuple([*map(_issue_candidate, history), _issue_candidate(changed)])

    candidates = await provider.prepare_run(
        run_id="known-outside-range",
        run_started_at=origin + timedelta(minutes=120),
        cursor=cursor,
    )

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, cursor, 1))
    )
    assert candidates[0]["stable_id"] == _issue_candidate(changed)["stable_id"]
    assert len(api.issue_requests) == 2


@pytest.mark.asyncio
async def test_ordered_lane_rejects_time_reversal_and_missing_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest = datetime(2026, 6, 1, 12, tzinfo=UTC)
    reversed_api = OrderedIssueApi([[_issue(1, newest - timedelta(hours=1)), _issue(2, newest)]])
    monkeypatch.setattr(github_module, "_request_json", reversed_api)
    provider = GitHubFetchService(_config(tmp_path / "reversed", resources=["issues"]), home=tmp_path / "reversed")
    with pytest.raises(BaseError, match="not ordered"):
        await provider.prepare_run(run_id="reversed", run_started_at=newest, cursor=None)

    missing = _issue(3, newest)
    missing.pop("updated_at")
    missing_api = OrderedIssueApi([[missing]])
    monkeypatch.setattr(github_module, "_request_json", missing_api)
    provider = GitHubFetchService(_config(tmp_path / "missing", resources=["issues"]), home=tmp_path / "missing")
    with pytest.raises(BaseError, match="no usable time"):
        await provider.prepare_run(run_id="missing", run_started_at=newest, cursor=None)


@pytest.mark.asyncio
async def test_commit_same_second_group_is_complete_and_repeated_page_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_time = datetime(2026, 6, 1, 12, tzinfo=UTC)
    commits = [_commit(index, candidate_time) for index in range(101)]
    api = CommitApi(commits, head_sha=str(commits[0]["sha"]), head_time=candidate_time)
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path / "complete", resources=["commits"]), home=tmp_path / "complete")
    oracle = tuple(_commit_candidate(payload) for payload in commits)

    candidates = await provider.prepare_run(run_id="same-second", run_started_at=candidate_time, cursor=None)

    assert tuple(map(_candidate_fingerprint, candidates)) == tuple(
        map(_candidate_fingerprint, select_latest_candidates(oracle, None, 19))
    )
    assert any(int(request["page"]) == 2 for request in api.commit_requests)

    repeated_api = ScriptedCommitApi(
        [commits[:100], commits[:100]],
        head_sha=str(commits[0]["sha"]),
        head_time=candidate_time,
    )
    monkeypatch.setattr(github_module, "_request_json", repeated_api)
    provider = GitHubFetchService(_config(tmp_path / "repeated", resources=["commits"]), home=tmp_path / "repeated")
    with pytest.raises(BaseError, match="repeated a page"):
        await provider.prepare_run(run_id="repeated", run_started_at=candidate_time, cursor=None)


@pytest.mark.asyncio
async def test_commit_lane_rejects_nonempty_short_page_without_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_time = datetime(2026, 6, 1, 12, tzinfo=UTC)
    first_page = [_commit(index, candidate_time) for index in range(100)]
    api = ScriptedCommitApi(
        [first_page, [first_page[0]]],
        head_sha=str(first_page[0]["sha"]),
        head_time=candidate_time,
    )
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["commits"]), home=tmp_path)

    with pytest.raises(BaseError, match="did not advance"):
        await provider.prepare_run(run_id="short-no-progress", run_started_at=candidate_time, cursor=None)


@pytest.mark.asyncio
async def test_commit_window_rejects_invalid_and_out_of_query_dates_without_mutating_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head_time = datetime(2026, 6, 1, 12, tzinfo=UTC)
    head_sha = "a" * 40
    invalid = _commit(1, head_time)
    nested = invalid["commit"]
    assert isinstance(nested, dict)
    nested["committer"] = {"date": "not-a-date"}
    cursor: dict[str, object] = {"_selection": {"completed": [], "latest_seen_time": None, "earliest_considered": None}}
    original_cursor = json.loads(json.dumps(cursor))
    invalid_api = ScriptedCommitApi([[invalid]], head_sha=head_sha, head_time=head_time)
    monkeypatch.setattr(github_module, "_request_json", invalid_api)
    provider = GitHubFetchService(_config(tmp_path / "invalid", resources=["commits"]), home=tmp_path / "invalid")
    with pytest.raises(BaseError, match="invalid time"):
        await provider.prepare_run(run_id="invalid", run_started_at=head_time, cursor=cursor)
    assert cursor == original_cursor

    outside = _commit(2, head_time - timedelta(days=2))
    outside_api = ScriptedCommitApi([[outside]], head_sha=head_sha, head_time=head_time)
    monkeypatch.setattr(github_module, "_request_json", outside_api)
    provider = GitHubFetchService(_config(tmp_path / "outside", resources=["commits"]), home=tmp_path / "outside")
    with pytest.raises(BaseError, match="outside the requested window"):
        await provider.prepare_run(run_id="outside", run_started_at=head_time, cursor=cursor)
    assert cursor == original_cursor


@pytest.mark.asyncio
async def test_single_second_commit_density_exhausts_shared_budget_without_partial_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_time = datetime(2026, 6, 1, 12, tzinfo=UTC)
    commits = [_commit(index, candidate_time) for index in range(10_001)]
    api = CommitApi(commits, head_sha=str(commits[0]["sha"]), head_time=candidate_time)
    monkeypatch.setattr(github_module, "_request_json", api)
    provider = GitHubFetchService(_config(tmp_path, resources=["commits"]), home=tmp_path)

    with pytest.raises(BaseError, match="metadata request budget"):
        await provider.prepare_run(run_id="dense", run_started_at=candidate_time, cursor=None)
    assert len(api.requests) == 100
