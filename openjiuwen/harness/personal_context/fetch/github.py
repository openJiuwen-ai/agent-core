"""GitHub REST/archive provider for the embedded personal-context core.

The provider deliberately keeps all GitHub-specific work in one production
class.  Small module functions below it handle request decoding and safe ZIP
materialisation; they do not own state or become additional PersonalContext classes.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import aiohttp

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.fetch.base import ContextFetchService
from openjiuwen.harness.personal_context.fetch.cursor_selection import (
    candidate_in_time_range,
    select_latest_candidates,
)
from openjiuwen.harness.personal_context.fetch.retry import (
    classify_payload_error,
    classify_transport_error,
    retry_provider_read,
)
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem
from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error

_API_ROOT = "https://api.github.com"
_BATCH_SIZE = 20
_DEFAULT_MAX_ITEMS = 25
_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
_MAX_ARCHIVE_FILES = 100_000
_REQUEST_TIMEOUT_SECONDS = 20 * 60
_CHUNK_SIZE = 1024 * 1024
_MAX_CONTENT_CHARS = 2_000_000
_MAX_JSON_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_METADATA_REQUESTS = 100
_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_GITHUB_DATE_FLOOR = datetime(1970, 1, 2, tzinfo=UTC)
_GITHUB_DATE_CEILING = datetime(2099, 12, 30, tzinfo=UTC)

_MetadataRequest = Callable[..., Awaitable[object | None]]


def _fetch_error(message: str, cause: BaseException | None = None) -> BaseError:
    return build_error(StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR, error_msg=message, cause=cause)


def _coerce_fetch_error(message: str, cause: Exception) -> BaseError:
    return cause if isinstance(cause, BaseError) else _fetch_error(message, cause)


def _safe_detail(exc: BaseException, token: str) -> str:
    detail = str(exc).replace(token, "<redacted>")
    detail = re.sub(r"https?://\S+", "<redacted-url>", detail)
    return detail[:512] or exc.__class__.__name__


def _service_root(home: Path, service_id: str) -> Path:
    return home / "materialized-sources" / "github" / service_id


def _candidate_path(home: Path, service_id: str) -> Path:
    return _service_root(home, service_id) / "candidate"


def _marker_path(candidate: Path) -> Path:
    return candidate / ".personal-context-marker.json"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _read_marker(candidate: Path) -> dict[str, object] | None:
    marker = _marker_path(candidate)
    if marker.is_symlink():
        raise _fetch_error("GitHub candidate marker is invalid")
    if not marker.is_file():
        return None
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _fetch_error("GitHub candidate marker is invalid", exc) from None
    if not isinstance(value, dict) or not isinstance(value.get("run_id"), str):
        raise _fetch_error("GitHub candidate marker is invalid")
    return value


def _remove_tree(path: Path) -> None:
    if path.is_symlink() or path.exists():
        if path.is_symlink() or not path.is_dir():
            raise _fetch_error("GitHub materialized path is not a directory")
        shutil.rmtree(path)


def _validate_layout(root: Path) -> None:
    """Reject unsafe candidate paths before recursive deletion."""

    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise _fetch_error("GitHub materialized service path is not a directory")
    candidate = root / "candidate"
    if candidate.is_symlink() or (candidate.exists() and not candidate.is_dir()):
        raise _fetch_error("GitHub materialized candidate path is not a directory")


def _prune_empty_materialized_roots(service_root: Path) -> None:
    for directory in (service_root, service_root.parent, service_root.parent.parent):
        with contextlib.suppress(OSError):
            directory.rmdir()


def _discard_candidate(root: Path) -> None:
    _validate_layout(root)
    candidate = root / "candidate"
    if candidate.exists():
        _remove_tree(candidate)
    _prune_empty_materialized_roots(root)


def _write_marker(candidate: Path, *, run_id: str, owner: str, repo: str, head_sha: str) -> None:
    marker = _marker_path(candidate)
    temporary = marker.with_name(f"{marker.name}.tmp")
    payload = {"run_id": run_id, "owner": owner, "repo": repo, "head_sha": head_sha}
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise _fetch_error("GitHub candidate marker write failed", exc) from None


def _validate_archive_name(name: str) -> tuple[str, ...]:
    if not name:
        raise _fetch_error("GitHub archive contains an unsafe path")
    if "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise _fetch_error("GitHub archive contains an unsafe path")
    parts = tuple(PurePosixPath(name).parts)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise _fetch_error("GitHub archive contains an unsafe path")
    return parts


def _archive_relative_names(infos: list[zipfile.ZipInfo]) -> dict[zipfile.ZipInfo, str]:
    parsed: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
    for info in infos:
        parts = _validate_archive_name(info.filename)
        mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        if file_type == stat.S_IFLNK or (file_type and file_type not in {stat.S_IFREG, stat.S_IFDIR}):
            raise _fetch_error("GitHub archive contains a link or special file")
        parsed.append((info, parts))

    first_parts = {parts[0] for _, parts in parsed if len(parts) > 1}
    strip_prefix = next(iter(first_parts)) if len(first_parts) == 1 else None
    result: dict[zipfile.ZipInfo, str] = {}
    seen: set[str] = set()
    for info, parts in parsed:
        if strip_prefix is not None and parts[0] == strip_prefix and len(parts) > 1:
            parts = parts[1:]
        if not parts:
            continue
        relative = "/".join(parts)
        duplicate_key = relative.casefold()
        if duplicate_key in seen:
            raise _fetch_error("GitHub archive contains duplicate paths")
        seen.add(duplicate_key)
        result[info] = relative
    return result


def _extract_archive(archive_bytes: bytes, candidate: Path) -> None:
    if len(archive_bytes) > _MAX_ARCHIVE_BYTES:
        raise _fetch_error("GitHub archive exceeds the compressed size limit")
    extracted_bytes = 0
    try:
        with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_ARCHIVE_FILES:
                raise _fetch_error("GitHub archive contains too many files")
            names = _archive_relative_names(infos)
            for info, relative in names.items():
                mode = (info.external_attr >> 16) & 0xFFFF
                is_directory = info.is_dir() or stat.S_IFMT(mode) == stat.S_IFDIR
                target = (candidate / PurePosixPath(relative)).resolve()
                if not _inside(target, candidate):
                    raise _fetch_error("GitHub archive escapes the candidate directory")
                if is_directory:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if info.file_size < 0 or extracted_bytes + info.file_size > _MAX_EXTRACTED_BYTES:
                    raise _fetch_error("GitHub archive exceeds the extracted size limit")
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with archive.open(info, "r") as source, target.open("wb") as destination:
                    while True:
                        chunk = source.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        written += len(chunk)
                        extracted_bytes += len(chunk)
                        if extracted_bytes > _MAX_EXTRACTED_BYTES:
                            raise _fetch_error("GitHub archive exceeds the extracted size limit")
                        destination.write(chunk)
                if written != info.file_size:
                    raise _fetch_error("GitHub archive entry size is inconsistent")
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        raise _fetch_error("GitHub archive cannot be safely extracted", exc) from None


async def _request_json(
    url: str,
    token: str,
    *,
    params: Mapping[str, object] | None = None,
    allow_not_found: bool = False,
) -> object | None:
    try:
        return await retry_provider_read(
            lambda: _request_json_once(
                url,
                token,
                params=params,
                allow_not_found=allow_not_found,
            ),
            provider="github",
            operation_name="rest_json",
            classify=_github_read_retry_reason,
        )
    except Exception as exc:
        raise _coerce_fetch_error("GitHub request failed", exc) from None


def _github_read_retry_reason(exc: BaseException) -> str | None:
    return classify_transport_error(exc) or classify_payload_error(exc)


async def _request_json_once(
    url: str,
    token: str,
    *,
    params: Mapping[str, object] | None = None,
    allow_not_found: bool = False,
) -> object | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "jiuwen-personal-context",
    }
    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        request_params: dict[str, str | int | float] | None = None
        if params is not None:
            request_params = {
                key: value
                for key, value in params.items()
                if isinstance(value, (str, int, float)) and not isinstance(value, bool)
            }
        async with session.get(url, headers=headers, params=request_params) as response:
            if response.status == 404 and allow_not_found:
                return None
            if response.status < 200 or response.status >= 300:
                response.raise_for_status()
                raise RuntimeError("GitHub request returned an unsuccessful HTTP status")
            return await _read_json_response(response)


async def _download_archive(url: str, token: str) -> bytes:
    try:
        return await retry_provider_read(
            lambda: _download_archive_once(url, token),
            provider="github",
            operation_name="archive_http_read",
            classify=_github_read_retry_reason,
        )
    except Exception as exc:
        raise _coerce_fetch_error("GitHub archive download failed", exc) from None


async def _download_archive_once(url: str, token: str) -> bytes:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "jiuwen-personal-context",
    }
    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as response:
            if response.status < 200 or response.status >= 300:
                response.raise_for_status()
                raise RuntimeError("GitHub archive request returned an unsuccessful HTTP status")
            header_size = response.headers.get("Content-Length")
            if header_size is not None and int(header_size) > _MAX_ARCHIVE_BYTES:
                raise _fetch_error("GitHub archive exceeds the compressed size limit")
            stream: Any = getattr(response, "content", response)
            chunks: list[bytes] = []
            total = 0
            async for chunk in stream.iter_chunked(_CHUNK_SIZE):
                total += len(chunk)
                if total > _MAX_ARCHIVE_BYTES:
                    raise _fetch_error("GitHub archive exceeds the compressed size limit")
                chunks.append(chunk)
            payload = b"".join(chunks)
            if not payload:
                raise EOFError("GitHub archive response is empty")
            return payload


def _as_object(value: object, *, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _fetch_error(f"GitHub {name} response is not an object")
    return dict(value)


def _as_list(value: object, *, name: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise _fetch_error(f"GitHub {name} response is not a list")
    return [dict(item) for item in value]


def _json_bytes(value: object) -> bytes | None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return None
    return encoded if len(encoded) <= 2 * 1024 * 1024 else None


def _sha256(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _json_digest(value: object) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        encoded = repr(value).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()


def _iso_value(item: Mapping[str, object], *, commit: bool = False) -> str | None:
    if commit:
        nested = item.get("commit")
        if isinstance(nested, Mapping):
            committer = nested.get("committer")
            if isinstance(committer, Mapping) and isinstance(committer.get("date"), str):
                return str(committer["date"])
            author = nested.get("author")
            if isinstance(author, Mapping) and isinstance(author.get("date"), str):
                return str(author["date"])
    value = item.get("updated_at")
    return value if isinstance(value, str) else None


async def _read_json_response(response: Any) -> object:
    """Read a GitHub JSON response without accepting an unbounded body."""

    headers = getattr(response, "headers", {})
    content_length = headers.get("Content-Length") if isinstance(headers, Mapping) else None
    if content_length is not None:
        try:
            if int(content_length) > _MAX_JSON_RESPONSE_BYTES:
                raise _fetch_error("GitHub JSON response exceeds the size limit")
        except ValueError as exc:
            raise _fetch_error("GitHub JSON response has an invalid size") from exc
    stream = getattr(response, "content", None)
    if stream is not None:
        chunks: list[bytes] = []
        total = 0
        async for chunk in stream.iter_chunked(_CHUNK_SIZE):
            total += len(chunk)
            if total > _MAX_JSON_RESPONSE_BYTES:
                raise _fetch_error("GitHub JSON response exceeds the size limit")
            chunks.append(chunk)
        payload = b"".join(chunks)
        if not payload:
            raise EOFError("GitHub JSON response is empty")
        return json.loads(payload)
    value = await response.json()
    try:
        encoded_size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise _fetch_error("GitHub JSON response is not serializable", exc) from None
    if encoded_size > _MAX_JSON_RESPONSE_BYTES:
        raise _fetch_error("GitHub JSON response exceeds the size limit")
    return value


def _validate_selection_cursor(cursor: dict[str, object] | None) -> None:
    if cursor is None:
        return
    if not isinstance(cursor, Mapping) or set(cursor) - {"_selection"}:
        raise _fetch_error("GitHub cursor contains unsupported fields")


def _head_sha(value: Mapping[str, object]) -> str | None:
    direct = value.get("sha") or value.get("head_sha") or value.get("default_branch_sha")
    if isinstance(direct, str) and _SHA.fullmatch(direct.strip()):
        return direct.strip()
    commit = value.get("commit")
    if isinstance(commit, Mapping):
        nested = commit.get("sha")
        if isinstance(nested, str) and _SHA.fullmatch(nested.strip()):
            return nested.strip()
    return None


def _head_time(value: Mapping[str, object]) -> str | None:
    for key in ("head_commit_time", "pushed_at"):
        direct = value.get(key)
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
    commit = value.get("commit")
    if not isinstance(commit, Mapping):
        return None
    nested = commit.get("commit")
    if not isinstance(nested, Mapping):
        nested = commit
    for actor_name in ("committer", "author"):
        actor = nested.get(actor_name)
        if isinstance(actor, Mapping):
            date = actor.get("date")
            if isinstance(date, str) and date.strip():
                return date.strip()
    return None


def _readme_text(readme: Mapping[str, object], *, owner: str, repo: str) -> str:
    content = readme.get("content")
    if not isinstance(content, str):
        raise _fetch_error("GitHub README response has no content")
    if readme.get("encoding") == "base64":
        try:
            text = base64.b64decode("".join(content.split()), validate=True).decode("utf-8")
        except ValueError as exc:
            raise _fetch_error("GitHub README is not valid UTF-8 base64", exc) from None
    else:
        text = content
    return text if text.strip() else f"{owner}/{repo} README"


def _github_resource_content(
    payload: Mapping[str, object],
    *,
    stable_id: str,
    is_commit: bool,
) -> str:
    content = payload.get("body") if not is_commit else None
    if is_commit:
        nested = payload.get("commit")
        content = nested.get("message") if isinstance(nested, Mapping) else None
    if not isinstance(content, str) or not content.strip():
        content = str(payload.get("title") or payload.get("sha") or stable_id)
    return content


def _github_candidate(
    item: RawChangeItem,
    *,
    lane: str,
    candidate_time: str | None,
    time_range: Mapping[str, object],
    run_started_at: datetime,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    if candidate_time is None:
        if time_range.get("mode") != "all":
            raise _fetch_error(f"GitHub {lane} candidate has no usable time")
        normalized_time = "1970-01-01T00:00:00Z"
    else:
        normalized_time = candidate_time
    if not candidate_in_time_range(normalized_time, time_range, run_started_at):
        return None
    return {
        "stable_id": item.logical_id,
        "revision_id": item.revision_id,
        "candidate_time": normalized_time,
        "resource_lane": lane,
        "locator": item.original_ref,
        "item": item,
        **dict(extra or {}),
    }


def _parse_candidate_time(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise _fetch_error(f"GitHub {label} candidate has no usable time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _fetch_error(f"GitHub {label} candidate has an invalid time", exc) from None
    if parsed.tzinfo is None:
        raise _fetch_error(f"GitHub {label} candidate time has no timezone")
    return parsed.astimezone(UTC)


def _format_candidate_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _selection_receipts(cursor: Mapping[str, object] | None) -> tuple[Mapping[str, object], ...]:
    if cursor is None:
        return ()
    selection = cursor.get("_selection")
    if not isinstance(selection, Mapping):
        return ()
    completed = selection.get("completed", ())
    if not isinstance(completed, list):
        return ()
    return tuple(receipt for receipt in completed if isinstance(receipt, Mapping))


def _known_lane_ids(
    cursor: Mapping[str, object] | None,
    *,
    lane: str,
) -> set[str]:
    return {
        str(receipt.get("stable_id", ""))
        for receipt in _selection_receipts(cursor)
        if receipt.get("resource_lane") == lane
    }


def _candidate_priority(candidate: Mapping[str, object], cursor: Mapping[str, object] | None) -> int:
    receipt_revisions = set()
    for matched_receipt in _selection_receipts(cursor):
        if matched_receipt.get("resource_lane") != candidate.get("resource_lane"):
            continue
        if matched_receipt.get("stable_id") != candidate.get("stable_id"):
            continue
        receipt_revisions.add(str(matched_receipt.get("revision_id", "")))
    known_revisions = receipt_revisions
    revision = str(candidate.get("revision_id", ""))
    changed_revision = bool(known_revisions) and revision not in known_revisions
    selection = cursor.get("_selection") if isinstance(cursor, Mapping) else None
    latest = selection.get("latest_seen_time") if isinstance(selection, Mapping) else None
    newly_visible = latest is None or _parse_candidate_time(
        candidate.get("candidate_time"), label=str(candidate.get("resource_lane", "resource"))
    ) > _parse_candidate_time(latest, label="cursor")
    return 0 if changed_revision or newly_visible else 1


def _bounded_lane_candidates(
    candidates: list[dict[str, object]],
    *,
    cursor: dict[str, object] | None,
    limit: int,
) -> None:
    candidates[:] = list(select_latest_candidates(tuple(candidates), cursor, limit))


def _lane_can_stop(
    candidates: list[dict[str, object]],
    *,
    cursor: dict[str, object] | None,
    limit: int,
    unread_time_upper_bound: datetime,
    known_ids: set[str],
    seen_known_ids: set[str],
) -> bool:
    selected = select_latest_candidates(tuple(candidates), cursor, limit)
    if len(selected) < limit:
        return False
    nth = selected[-1]
    nth_time = _parse_candidate_time(nth.get("candidate_time"), label=str(nth.get("resource_lane", "resource")))
    if unread_time_upper_bound >= nth_time:
        return False
    return _candidate_priority(nth, cursor) == 0 or known_ids <= seen_known_ids


def _resource_candidate(
    payload: Mapping[str, object],
    *,
    owner: str,
    repo: str,
    resource: str,
    label: str,
    is_commit: bool,
    time_range: Mapping[str, object],
    run_started_at: datetime,
) -> dict[str, object] | None:
    identifier = payload.get("sha") if is_commit else payload.get("number")
    if identifier is None:
        return None
    stable_id = f"github:{owner}/{repo}:{label}:{identifier}"
    candidate_time = _iso_value(payload, commit=is_commit)
    content = _github_resource_content(payload, stable_id=stable_id, is_commit=is_commit)
    raw_snapshot = _json_bytes(payload)
    item = RawChangeItem(
        logical_id=stable_id,
        revision_id=_json_digest(payload),
        operation="upsert",
        title=str(payload.get("title") or content.splitlines()[0] or stable_id),
        content=content[:_MAX_CONTENT_CHARS],
        original_ref=str(payload.get("html_url") or f"https://github.com/{owner}/{repo}"),
        metadata={
            "resource": resource,
            "repository": f"github:{owner}/{repo}",
            "number": identifier,
            "updated_at": candidate_time,
            "content_truncated": len(content) > _MAX_CONTENT_CHARS,
            "raw_snapshot_omitted": raw_snapshot is None,
        },
        raw_snapshot=raw_snapshot,
    )
    return _github_candidate(
        item,
        lane=label,
        candidate_time=candidate_time,
        time_range=time_range,
        run_started_at=run_started_at,
    )


def _remote_commit_bounds(
    start: datetime | None,
    end: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    since: datetime | None = None
    until: datetime | None = None
    if start is not None:
        if start > _GITHUB_DATE_CEILING:
            since = _GITHUB_DATE_CEILING
        elif start >= _GITHUB_DATE_FLOOR:
            since = start - timedelta(seconds=1)
    if end is not None:
        if end < _GITHUB_DATE_FLOOR:
            until = _GITHUB_DATE_FLOOR
        elif end <= _GITHUB_DATE_CEILING:
            until = end + timedelta(seconds=1)
    return since, until


def _window_contains(candidate_time: datetime, start: datetime | None, end: datetime | None) -> bool:
    return (start is None or candidate_time >= start) and (end is None or candidate_time < end)


async def _discover_resource_list(
    request_json: _MetadataRequest,
    *,
    owner: str,
    repo: str,
    token: str,
    resource: str,
    endpoint_name: str,
    label: str,
    time_range: Mapping[str, object],
    run_started_at: datetime,
    cursor: dict[str, object] | None,
    limit: int,
) -> list[dict[str, object]]:
    requires_complete_listing = resource == "pull_requests"
    candidates: list[dict[str, object]] = []
    known_ids = _known_lane_ids(
        cursor,
        lane=label,
    )
    seen_known_ids: set[str] = set()
    seen_records: set[str] = set()
    seen_payloads: dict[str, str] = {}
    page_fingerprints: set[str] = set()
    previous_time: datetime | None = None
    endpoint = f"{_API_ROOT}/repos/{owner}/{repo}/{endpoint_name}"
    lower_bound: datetime | None = None
    if time_range.get("mode") == "recent":
        recent_days = time_range.get("recent_days")
        if isinstance(recent_days, bool) or not isinstance(recent_days, int):
            raise _fetch_error("GitHub recent time range is invalid")
        lower_bound = run_started_at.astimezone(UTC) - timedelta(days=recent_days)
    elif time_range.get("mode") == "fixed":
        lower_bound = _parse_candidate_time(time_range.get("start_at"), label="fixed range")

    for page in range(1, _MAX_METADATA_REQUESTS + 1):
        payload = await request_json(
            endpoint,
            token,
            params={
                "page": page,
                "per_page": 100,
                "state": "all",
                "sort": "updated",
                "direction": "desc",
            },
        )
        current = _as_list(payload, name=endpoint_name)
        if len(current) > 100:
            raise _fetch_error(f"GitHub {endpoint_name} response exceeds requested page size")
        fingerprint = _json_digest(current)
        if fingerprint in page_fingerprints:
            raise _fetch_error(f"GitHub {endpoint_name} pagination repeated a page")
        page_fingerprints.add(fingerprint)
        if not current:
            return candidates
        advanced = False
        for item in current:
            candidate_time = _parse_candidate_time(_iso_value(item), label=label)
            if not requires_complete_listing:
                if previous_time is not None and candidate_time > previous_time:
                    raise _fetch_error(f"GitHub {endpoint_name} response is not ordered by updated time")
                previous_time = candidate_time
            identifier = item.get("number")
            payload_digest = _json_digest(item)
            record_key = str(identifier) if identifier is not None else payload_digest
            if record_key in seen_records:
                if requires_complete_listing and seen_payloads.get(record_key) != payload_digest:
                    raise _fetch_error(f"GitHub {endpoint_name} payload changed during pagination")
                continue
            seen_records.add(record_key)
            if requires_complete_listing:
                seen_payloads[record_key] = payload_digest
            advanced = True
            if resource == "issues" and "pull_request" in item:
                continue
            if identifier is None:
                continue
            stable_id = f"github:{owner}/{repo}:{label}:{identifier}"
            if stable_id in known_ids:
                seen_known_ids.add(stable_id)
            candidate = _resource_candidate(
                item,
                owner=owner,
                repo=repo,
                resource=resource,
                label=label,
                is_commit=False,
                time_range=time_range,
                run_started_at=run_started_at,
            )
            if candidate is not None:
                candidates.append(candidate)
        _bounded_lane_candidates(candidates, cursor=cursor, limit=limit)
        if not advanced:
            raise _fetch_error(f"GitHub {endpoint_name} pagination did not advance")
        if len(current) < 100:
            return candidates
        if requires_complete_listing:
            continue
        if lower_bound is not None and previous_time is not None and previous_time < lower_bound:
            return candidates
        if previous_time is not None and _lane_can_stop(
            candidates,
            cursor=cursor,
            limit=limit,
            unread_time_upper_bound=previous_time,
            known_ids=known_ids,
            seen_known_ids=seen_known_ids,
        ):
            return candidates
    raise _fetch_error(f"GitHub {endpoint_name} pagination exceeded the metadata request budget")


async def _discover_commits(
    request_json: _MetadataRequest,
    *,
    owner: str,
    repo: str,
    token: str,
    head_sha: str,
    head_time: datetime,
    time_range: Mapping[str, object],
    run_started_at: datetime,
    cursor: dict[str, object] | None,
    limit: int,
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    known_ids = _known_lane_ids(
        cursor,
        lane="commit",
    )
    seen_known_ids: set[str] = set()
    observed_revisions: dict[str, str] = {}
    endpoint = f"{_API_ROOT}/repos/{owner}/{repo}/commits"

    async def scan_window(start: datetime | None, end: datetime | None) -> bool:
        since, until = _remote_commit_bounds(start, end)
        base_params: dict[str, object] = {"sha": head_sha, "per_page": 100}
        if since is not None:
            base_params["since"] = _format_candidate_time(since)
        if until is not None:
            base_params["until"] = _format_candidate_time(until)

        async def read_page(page: int) -> list[dict[str, object]]:
            payload = await request_json(endpoint, token, params={**base_params, "page": page})
            current = _as_list(payload, name="commits")
            if len(current) > 100:
                raise _fetch_error("GitHub commits response exceeds requested page size")
            for item in current:
                candidate_time = _parse_candidate_time(_iso_value(item, commit=True), label="commit")
                if since is not None and candidate_time < since:
                    raise _fetch_error("GitHub commit time is outside the requested window")
                if until is not None and candidate_time > until:
                    raise _fetch_error("GitHub commit time is outside the requested window")
            return current

        first = await read_page(1)
        can_split_window = len(first) == 100 and start is not None and (end is not None)
        if can_split_window and end - start > timedelta(seconds=1):
            midpoint = start + (end - start) / 2
            if await scan_window(midpoint, end):
                return True
            return await scan_window(start, midpoint)

        page = 1
        current = first
        query_seen: set[str] = set()
        page_fingerprints: set[str] = set()
        while True:
            fingerprint = _json_digest(current)
            if fingerprint in page_fingerprints:
                raise _fetch_error("GitHub commits pagination repeated a page")
            page_fingerprints.add(fingerprint)
            advanced = False
            for payload in current:
                sha = payload.get("sha")
                if not isinstance(sha, str) or not sha.strip():
                    raise _fetch_error("GitHub commit response has no SHA")
                sha = sha.strip()
                if sha not in query_seen:
                    query_seen.add(sha)
                    advanced = True
                candidate_time = _parse_candidate_time(_iso_value(payload, commit=True), label="commit")
                if not _window_contains(candidate_time, start, end):
                    continue
                revision = _json_digest(payload)
                previous_revision = observed_revisions.get(sha)
                if previous_revision is not None:
                    if previous_revision != revision:
                        raise _fetch_error("GitHub commit payload changed during discovery")
                    continue
                observed_revisions[sha] = revision
                stable_id = f"github:{owner}/{repo}:commit:{sha}"
                if stable_id in known_ids:
                    seen_known_ids.add(stable_id)
                candidate = _resource_candidate(
                    payload,
                    owner=owner,
                    repo=repo,
                    resource="commits",
                    label="commit",
                    is_commit=True,
                    time_range=time_range,
                    run_started_at=run_started_at,
                )
                if candidate is not None:
                    candidates.append(candidate)
            _bounded_lane_candidates(candidates, cursor=cursor, limit=limit)
            if current and not advanced:
                raise _fetch_error("GitHub commits pagination did not advance")
            if len(current) < 100:
                break
            page += 1
            current = await read_page(page)

        if start is None:
            return True
        return _lane_can_stop(
            candidates,
            cursor=cursor,
            limit=limit,
            unread_time_upper_bound=start,
            known_ids=known_ids,
            seen_known_ids=seen_known_ids,
        )

    mode = time_range.get("mode")
    if mode == "recent":
        recent_days = time_range.get("recent_days")
        if isinstance(recent_days, bool) or not isinstance(recent_days, int):
            raise _fetch_error("GitHub recent time range is invalid")
        await scan_window(
            run_started_at.astimezone(UTC) - timedelta(days=recent_days),
            run_started_at.astimezone(UTC) + timedelta(seconds=1),
        )
        return candidates
    if mode == "fixed":
        start_at = _parse_candidate_time(time_range.get("start_at"), label="fixed range")
        end_at = _parse_candidate_time(time_range.get("end_at"), label="fixed range")
        await scan_window(start_at, end_at)
        return candidates
    if mode != "all":
        raise _fetch_error("GitHub time range is invalid")

    pivot = min(max(head_time, _GITHUB_DATE_FLOOR), _GITHUB_DATE_CEILING)
    if await scan_window(pivot, None):
        return candidates
    upper = pivot
    span = timedelta(days=1)
    while upper > _GITHUB_DATE_FLOOR:
        start = max(_GITHUB_DATE_FLOOR, upper - span)
        if await scan_window(start, upper):
            return candidates
        if start == _GITHUB_DATE_FLOOR:
            break
        upper = start
        span *= 2
    await scan_window(None, _GITHUB_DATE_FLOOR)
    return candidates


class GitHubFetchService(ContextFetchService):
    """Fetch one GitHub repository and optionally materialize its selected code snapshot."""

    async def prepare_run(
        self,
        *,
        run_id: str,
        run_started_at: datetime,
        cursor: dict[str, object] | None,
    ) -> tuple[dict[str, object], ...]:
        del run_id
        try:
            root = _service_root(self._home, self._config.service_id)
            _discard_candidate(root)
            _validate_selection_cursor(cursor)
            token = str(self._config.credentials.get("token", "")).strip()
            if not token:
                raise _fetch_error("GitHub token is required")
            source = dict(self._config.source)
            owner = str(source.get("owner", "")).strip()
            repo = str(source.get("repo", "")).strip()
            raw_resources = source.get("resources", ())
            resources = [str(item) for item in raw_resources] if isinstance(raw_resources, (list, tuple)) else []
            max_items = self._config.max_items_per_run or _DEFAULT_MAX_ITEMS
            metadata_requests = 0

            async def request_json(
                url: str,
                request_token: str,
                *,
                params: Mapping[str, object] | None = None,
                allow_not_found: bool = False,
            ) -> object | None:
                nonlocal metadata_requests
                if metadata_requests >= _MAX_METADATA_REQUESTS:
                    raise _fetch_error("GitHub metadata request budget exceeded")
                metadata_requests += 1
                return await _request_json(
                    url,
                    request_token,
                    params=params,
                    allow_not_found=allow_not_found,
                )

            metadata = _as_object(
                await request_json(f"{_API_ROOT}/repos/{owner}/{repo}", token),
                name="repository metadata",
            )
            default_branch = metadata.get("default_branch")
            if not isinstance(default_branch, str) or not default_branch.strip():
                raise _fetch_error("GitHub repository has no default branch")
            default_branch = default_branch.strip()
            head_sha = _head_sha(metadata)
            head_time = _head_time(metadata)
            needs_exact_head = any(resource in resources for resource in ("readme", "code", "commits"))
            if needs_exact_head:
                branch = _as_object(
                    await request_json(
                        f"{_API_ROOT}/repos/{owner}/{repo}/branches/{quote(default_branch, safe='')}",
                        token,
                    ),
                    name="default branch",
                )
                branch_sha = _head_sha(branch)
                branch_time = _head_time(branch)
                head_sha = branch_sha
                head_time = branch_time
            if needs_exact_head and head_sha is None:
                raise _fetch_error("GitHub default branch has no valid head SHA")
            code_head_sha: str | None = None
            if "code" in resources:
                if head_sha is None:
                    raise _fetch_error("GitHub default branch has no valid head SHA")
                code_head_sha = head_sha

            candidates: list[dict[str, object]] = []
            if "readme" in resources:
                readme = await request_json(
                    f"{_API_ROOT}/repos/{owner}/{repo}/readme",
                    token,
                    params={"ref": head_sha},
                    allow_not_found=True,
                )
                if readme is not None:
                    readme_obj = _as_object(readme, name="README")
                    readme_text = _readme_text(readme_obj, owner=owner, repo=repo)
                    raw_snapshot = _json_bytes(readme_obj)
                    bounded = readme_text[:_MAX_CONTENT_CHARS]
                    item = RawChangeItem(
                        logical_id=f"github:{owner}/{repo}:repository:readme",
                        revision_id=_sha256(readme_text),
                        operation="upsert",
                        title=f"{owner}/{repo} README",
                        content=bounded,
                        original_ref=f"https://github.com/{owner}/{repo}#readme",
                        metadata={
                            "resource": "readme",
                            "repository": f"github:{owner}/{repo}",
                            "default_branch": default_branch,
                            "content_truncated": len(readme_text) > _MAX_CONTENT_CHARS,
                            "raw_snapshot_omitted": raw_snapshot is None,
                        },
                        raw_snapshot=raw_snapshot,
                    )
                    candidate = _github_candidate(
                        item,
                        lane="readme",
                        candidate_time=head_time,
                        time_range=self._config.time_range,
                        run_started_at=run_started_at,
                    )
                    if candidate is not None:
                        candidates.append(candidate)

            for resource, endpoint, label, is_commit in (
                ("issues", "issues", "issue", False),
                ("pull_requests", "pulls", "pull_request", False),
                ("commits", "commits", "commit", True),
            ):
                if resource not in resources:
                    continue
                if is_commit:
                    if head_sha is None or head_time is None:
                        raise _fetch_error("GitHub default branch has no usable head metadata")
                    candidates.extend(
                        await _discover_commits(
                            request_json,
                            owner=owner,
                            repo=repo,
                            token=token,
                            head_sha=head_sha,
                            head_time=_parse_candidate_time(head_time, label="HEAD"),
                            time_range=self._config.time_range,
                            run_started_at=run_started_at,
                            cursor=cursor,
                            limit=max_items,
                        )
                    )
                    continue
                candidates.extend(
                    await _discover_resource_list(
                        request_json,
                        owner=owner,
                        repo=repo,
                        token=token,
                        resource=resource,
                        endpoint_name=endpoint,
                        label=label,
                        time_range=self._config.time_range,
                        run_started_at=run_started_at,
                        cursor=cursor,
                        limit=max_items,
                    )
                )

            if code_head_sha is not None:
                code_path = str(_candidate_path(self._home, self._config.service_id).resolve())
                item = RawChangeItem(
                    logical_id=f"github:{owner}/{repo}:repository:code",
                    revision_id=code_head_sha,
                    operation="upsert",
                    title=f"{owner}/{repo} code",
                    content=f"GitHub repository code snapshot at commit {code_head_sha}.",
                    original_ref=f"https://github.com/{owner}/{repo}/tree/{code_head_sha}",
                    metadata={
                        "resource": "code",
                        "repository": f"github:{owner}/{repo}",
                        "default_branch": default_branch,
                        "head_sha": code_head_sha,
                        "materialized_source_path": code_path,
                    },
                )
                candidate = _github_candidate(
                    item,
                    lane="code",
                    candidate_time=head_time,
                    time_range=self._config.time_range,
                    run_started_at=run_started_at,
                    extra={
                        "owner": owner,
                        "repo": repo,
                        "head_sha": code_head_sha,
                        "materialized_source_path": code_path,
                    },
                )
                if candidate is not None:
                    candidates.append(candidate)

            return select_latest_candidates(tuple(candidates), cursor, max_items)
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            token = str(self._config.credentials.get("token", ""))
            raise _fetch_error(f"GitHub preparation failed: {_safe_detail(exc, token)}", exc) from None

    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
        candidates: tuple[dict[str, object], ...],
    ) -> AsyncIterator[FetchBatch]:
        try:
            token = str(self._config.credentials.get("token", "")).strip()
            next_cursor = dict(cursor) if cursor is not None else {}
            if not candidates:
                yield FetchBatch(batch_id="batch-0", items=(), next_cursor=next_cursor)
                return
            for index in range(0, len(candidates), _BATCH_SIZE):
                end = index + _BATCH_SIZE
                chunk = candidates[index:end]
                items: list[RawChangeItem] = []
                materialized_path: str | None = None
                materialized_revision: str | None = None
                for candidate in chunk:
                    item = candidate.get("item")
                    if not isinstance(item, RawChangeItem):
                        raise _fetch_error("GitHub candidate item is invalid")
                    if candidate.get("resource_lane") == "code":
                        owner = str(candidate.get("owner", ""))
                        repo = str(candidate.get("repo", ""))
                        head_sha = str(candidate.get("head_sha", ""))
                        await self._materialize_code(run_id, owner, repo, head_sha, token)
                        materialized_path = str(candidate.get("materialized_source_path", ""))
                        materialized_revision = head_sha
                    items.append(item)
                yield FetchBatch(
                    batch_id=f"batch-{index // _BATCH_SIZE}",
                    items=tuple(items),
                    next_cursor=next_cursor,
                    materialized_source_path=materialized_path,
                    materialized_revision=materialized_revision,
                )
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            token = str(self._config.credentials.get("token", ""))
            raise _fetch_error(f"GitHub fetch failed: {_safe_detail(exc, token)}", exc) from None

    async def _materialize_code(self, run_id: str, owner: str, repo: str, head_sha: str, token: str) -> None:
        root = _service_root(self._home, self._config.service_id)
        candidate = root / "candidate"
        _validate_layout(root)
        root.mkdir(parents=True, exist_ok=True)
        _remove_tree(candidate)
        try:
            archive = await _download_archive(f"{_API_ROOT}/repos/{owner}/{repo}/zipball/{head_sha}", token)
            candidate.mkdir(parents=True, exist_ok=False)
            _extract_archive(archive, candidate)
            _write_marker(candidate, run_id=run_id, owner=owner, repo=repo, head_sha=head_sha)
        except asyncio.CancelledError:
            _remove_tree(candidate)
            raise
        except BaseError:
            _remove_tree(candidate)
            raise
        except Exception as exc:
            _remove_tree(candidate)
            raise _fetch_error("GitHub code materialization failed", exc) from None

    async def commit_run(self, *, run_id: str) -> None:
        root = _service_root(self._home, self._config.service_id)
        candidate = root / "candidate"
        _validate_layout(root)
        if not candidate.exists():
            _prune_empty_materialized_roots(root)
            return
        marker = _read_marker(candidate)
        if marker is None:
            raise _fetch_error("GitHub candidate marker is missing")
        if marker.get("run_id") != run_id:
            return
        try:
            _remove_tree(candidate)
            _prune_empty_materialized_roots(root)
        except OSError as exc:
            raise _fetch_error("GitHub candidate commit failed", exc) from None

    async def abort_run(self, *, run_id: str) -> None:
        root = _service_root(self._home, self._config.service_id)
        candidate = root / "candidate"
        _validate_layout(root)
        if not candidate.exists():
            _prune_empty_materialized_roots(root)
            return
        marker = _read_marker(candidate)
        if marker is None:
            raise _fetch_error("GitHub candidate marker is missing")
        if marker.get("run_id") != run_id:
            return
        try:
            _remove_tree(candidate)
            _prune_empty_materialized_roots(root)
        except OSError as exc:
            raise _fetch_error("GitHub candidate abort failed", exc) from None
