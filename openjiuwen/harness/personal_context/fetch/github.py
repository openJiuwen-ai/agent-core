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
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import aiohttp

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.fetch.base import ContextFetchService
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
_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


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
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "jiuwen-personal-context",
    }
    try:
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
                    raise _fetch_error(f"GitHub request returned HTTP {response.status}")
                return await _read_json_response(response)
    except Exception as exc:
        raise _coerce_fetch_error("GitHub request failed", exc) from None


async def _download_archive(url: str, token: str) -> bytes:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "jiuwen-personal-context",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                if response.status < 200 or response.status >= 300:
                    raise _fetch_error(f"GitHub archive request returned HTTP {response.status}")
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
                return b"".join(chunks)
    except Exception as exc:
        raise _coerce_fetch_error("GitHub archive download failed", exc) from None


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


def _validate_cursor(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _fetch_error("GitHub cursor must be an object")
    allowed = {"readme", "issues", "pull_requests", "commits", "repository"}
    if set(value) - allowed:
        raise _fetch_error("GitHub cursor contains unsupported fields")
    result = deepcopy(dict(value))
    readme = result.get("readme")
    if readme is not None:
        if not isinstance(readme, Mapping) or set(readme) - {"readme_sha256"}:
            raise _fetch_error("GitHub README cursor is invalid")
        readme_hash = readme.get("readme_sha256")
        if readme_hash is not None and (not isinstance(readme_hash, str) or not readme_hash):
            raise _fetch_error("GitHub README cursor is invalid")
    for key in ("issues", "pull_requests", "commits"):
        section = result.get(key)
        if section is None:
            continue
        if not isinstance(section, Mapping) or set(section) != {
            "latest_updated_at",
            "latest_ids",
            "history_before_updated_at",
            "history_boundary_ids",
            "history_complete",
        }:
            raise _fetch_error(f"GitHub {key} cursor is invalid")
        latest_updated_at = section.get("latest_updated_at")
        history_before_updated_at = section.get("history_before_updated_at")
        if latest_updated_at is not None and not isinstance(latest_updated_at, str):
            raise _fetch_error(f"GitHub {key} cursor is invalid")
        if history_before_updated_at is not None and not isinstance(history_before_updated_at, str):
            raise _fetch_error(f"GitHub {key} cursor is invalid")
        latest_ids = section.get("latest_ids")
        history_boundary_ids = section.get("history_boundary_ids")
        if not isinstance(latest_ids, list) or not isinstance(history_boundary_ids, list):
            raise _fetch_error(f"GitHub {key} cursor is invalid")
        if len(latest_ids) > 10_000 or len(history_boundary_ids) > 10_000:
            raise _fetch_error(f"GitHub {key} cursor is invalid")
        if any(not isinstance(item, str) or not item for item in latest_ids) or any(
            not isinstance(item, str) or not item for item in history_boundary_ids
        ):
            raise _fetch_error(f"GitHub {key} cursor is invalid")
        if not isinstance(section.get("history_complete"), bool):
            raise _fetch_error(f"GitHub {key} cursor is invalid")
    repository = result.get("repository")
    if repository is not None:
        if not isinstance(repository, Mapping) or set(repository) - {"default_branch", "head_sha"}:
            raise _fetch_error("GitHub repository cursor is invalid")
        for field in ("default_branch", "head_sha"):
            field_value = repository.get(field)
            if field_value is not None and (not isinstance(field_value, str) or not field_value):
                raise _fetch_error("GitHub repository cursor is invalid")
        head_sha = repository.get("head_sha")
        if head_sha is not None and (not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha)):
            raise _fetch_error("GitHub repository cursor is invalid")
    return result


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


def _resource_cursor_state(cursor: Mapping[str, object] | None, key: str) -> dict[str, object]:
    value = cursor.get(key) if isinstance(cursor, Mapping) else None
    if isinstance(value, Mapping):
        return {
            "latest_updated_at": value.get("latest_updated_at"),
            "latest_ids": list(value.get("latest_ids", [])),
            "history_before_updated_at": value.get("history_before_updated_at"),
            "history_boundary_ids": list(value.get("history_boundary_ids", [])),
            "history_complete": bool(value.get("history_complete", False)),
        }
    return {
        "latest_updated_at": None,
        "latest_ids": [],
        "history_before_updated_at": None,
        "history_boundary_ids": [],
        "history_complete": False,
    }


def _advance_resource_cursor(state: dict[str, object], stable_id: str, updated_at: str | None, category: str) -> None:
    if category == "latest":
        latest = state["latest_updated_at"]
        latest_ids = [str(item) for item in state["latest_ids"]]
        if latest is None or (updated_at is not None and updated_at > str(latest)):
            state["latest_updated_at"] = updated_at
            state["latest_ids"] = [stable_id]
        elif updated_at == latest and stable_id not in latest_ids:
            state["latest_ids"] = sorted([*latest_ids, stable_id])
        return
    boundary = state["history_before_updated_at"]
    boundary_ids = [str(item) for item in state["history_boundary_ids"]]
    if boundary is None or (updated_at is not None and updated_at < str(boundary)):
        state["history_before_updated_at"] = updated_at
        state["history_boundary_ids"] = [stable_id]
    elif updated_at == boundary and stable_id not in boundary_ids:
        state["history_boundary_ids"] = sorted([*boundary_ids, stable_id])


def _finalize_resource_cursor(
    state: dict[str, object],
    payloads: list[tuple[str, str | None]],
    selected: list[tuple[str, str | None, str]],
) -> None:
    if state["history_before_updated_at"] is None and selected:
        timestamps = [timestamp for _, timestamp, _ in selected if timestamp is not None]
        if timestamps:
            oldest = min(timestamps)
            state["history_before_updated_at"] = oldest
            state["history_boundary_ids"] = sorted(
                stable_id for stable_id, timestamp, _ in selected if timestamp == oldest
            )
    boundary = state["history_before_updated_at"]
    if boundary is None:
        state["history_complete"] = not payloads
        return
    boundary_ids = {str(item) for item in state["history_boundary_ids"]}
    latest_ids = {str(item) for item in state["latest_ids"]}
    selected_ids = {stable_id for stable_id, _, _ in selected}
    state["history_complete"] = not any(
        stable_id not in selected_ids
        and (
            (timestamp is not None and timestamp < str(boundary))
            or (timestamp == boundary and stable_id not in boundary_ids and stable_id not in latest_ids)
        )
        for stable_id, timestamp in payloads
    )


def _merge_cursor(cursor: dict[str, object], update: Mapping[str, object]) -> None:
    for key, value in update.items():
        cursor[key] = deepcopy(value)


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
        return json.loads(b"".join(chunks))
    value = await response.json()
    try:
        encoded_size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise _fetch_error("GitHub JSON response is not serializable", exc) from None
    if encoded_size > _MAX_JSON_RESPONSE_BYTES:
        raise _fetch_error("GitHub JSON response exceeds the size limit")
    return value


class GitHubFetchService(ContextFetchService):
    """Fetch one GitHub repository and optionally materialize its code."""

    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
    ) -> AsyncIterator[FetchBatch]:
        try:
            batches = await self._fetch_impl(run_id=run_id, cursor=cursor)
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            token = str(self._config.credentials.get("token", ""))
            raise _fetch_error(f"GitHub fetch failed: {_safe_detail(exc, token)}", exc) from None
        for batch in batches:
            yield batch

    async def _fetch_impl(self, *, run_id: str, cursor: dict[str, object] | None) -> list[FetchBatch]:
        _discard_candidate(_service_root(self._home, self._config.service_id))
        token = str(self._config.credentials.get("token", "")).strip()
        if not token:
            raise _fetch_error("GitHub token is required")
        if cursor is not None and not isinstance(cursor, Mapping):
            raise _fetch_error("GitHub cursor must be an object")
        old_cursor = _validate_cursor(cursor)
        source = dict(self._config.source)
        owner = str(source.get("owner", ""))
        repo = str(source.get("repo", ""))
        raw_resources = source.get("resources", ())
        resources = [str(item) for item in raw_resources] if isinstance(raw_resources, (list, tuple)) else []
        max_items = self._config.max_items_per_run or _DEFAULT_MAX_ITEMS

        metadata = _as_object(
            await _request_json(f"{_API_ROOT}/repos/{owner}/{repo}", token), name="repository metadata"
        )
        default_branch = metadata.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch.strip():
            raise _fetch_error("GitHub repository has no default branch")
        default_branch = default_branch.strip()
        head_sha: str | None = None

        pending: list[tuple[RawChangeItem, dict[str, object], str | None, str | None]] = []
        # Tuple fields are item, cursor update, candidate path, candidate revision.
        remaining = max_items

        if "readme" in resources and remaining:
            readme_cursor = old_cursor.get("readme") if isinstance(old_cursor.get("readme"), Mapping) else {}
            readme = await _request_json(
                f"{_API_ROOT}/repos/{owner}/{repo}/readme",
                token,
                params={"ref": default_branch},
                allow_not_found=True,
            )
            old_hash = readme_cursor.get("readme_sha256") if isinstance(readme_cursor, Mapping) else None
            if readme is None:
                if isinstance(old_hash, str):
                    item = RawChangeItem(
                        logical_id=f"github:{owner}/{repo}:repository:readme",
                        revision_id=f"deleted:{old_hash}",
                        operation="delete",
                        title=f"{owner}/{repo} README",
                        original_ref=f"https://github.com/{owner}/{repo}#readme",
                        metadata={"resource": "readme", "repository": f"github:{owner}/{repo}"},
                    )
                    pending.append((item, {"readme": {"readme_sha256": None}}, None, None))
                    remaining -= 1
            else:
                readme_obj = _as_object(readme, name="README")
                content = readme_obj.get("content")
                if not isinstance(content, str):
                    raise _fetch_error("GitHub README response has no content")
                if readme_obj.get("encoding") == "base64":
                    try:
                        readme_text = base64.b64decode("".join(content.split()), validate=True).decode("utf-8")
                    except ValueError as exc:
                        raise _fetch_error("GitHub README is not valid UTF-8 base64", exc) from None
                else:
                    readme_text = content
                if not readme_text.strip():
                    readme_text = f"{owner}/{repo} README"
                readme_hash = _sha256(readme_text)
                if readme_hash != old_hash:
                    raw_snapshot = _json_bytes(readme_obj)
                    content_truncated = len(readme_text) > _MAX_CONTENT_CHARS
                    bounded_readme = readme_text[:_MAX_CONTENT_CHARS]
                    item = RawChangeItem(
                        logical_id=f"github:{owner}/{repo}:repository:readme",
                        revision_id=readme_hash,
                        operation="upsert",
                        title=f"{owner}/{repo} README",
                        content=bounded_readme,
                        original_ref=f"https://github.com/{owner}/{repo}#readme",
                        metadata={
                            "resource": "readme",
                            "repository": f"github:{owner}/{repo}",
                            "default_branch": default_branch,
                            "content_truncated": content_truncated,
                            "raw_snapshot_omitted": raw_snapshot is None,
                        },
                        raw_snapshot=raw_snapshot,
                    )
                    pending.append((item, {"readme": {"readme_sha256": readme_hash}}, None, None))
                    remaining -= 1

        resource_specs = (
            ("issues", "issues", "issue", False),
            ("pull_requests", "pulls", "pull_request", False),
            ("commits", "commits", "commit", True),
        )
        enabled_specs = [spec for spec in resource_specs if spec[0] in resources]
        reserved_code_items = 1 if "code" in resources else 0
        list_budget = max(0, remaining - reserved_code_items)
        base_limit, extra_limits = divmod(list_budget, len(enabled_specs)) if enabled_specs else (0, 0)
        resource_limits = {
            resource: base_limit + (1 if index < extra_limits else 0)
            for index, (resource, _endpoint, _label, _is_commit) in enumerate(enabled_specs)
        }

        for resource, endpoint, label, is_commit in resource_specs:
            resource_limit = resource_limits.get(resource, 0)
            if resource_limit <= 0 or remaining <= 0:
                continue
            resource_limit = min(resource_limit, remaining)
            resource_state = _resource_cursor_state(old_cursor, resource)
            latest_watermark = resource_state["latest_updated_at"]
            latest_ids = {str(item) for item in resource_state["latest_ids"]}
            history_watermark = resource_state["history_before_updated_at"]
            history_ids = {str(item) for item in resource_state["history_boundary_ids"]}
            payloads = await self._list_resource(
                owner,
                repo,
                endpoint,
                token,
                is_commit=is_commit,
                max_items=resource_limit,
                since=None,
                initial_scan=latest_watermark is None and history_watermark is None,
            )
            latest_candidates: list[tuple[RawChangeItem, str | None, str, str]] = []
            history_candidates: list[tuple[RawChangeItem, str | None, str, str]] = []
            payload_ids: list[tuple[str, str | None]] = []
            for payload in payloads:
                if resource == "issues" and "pull_request" in payload:
                    continue
                identifier = payload.get("number") if resource != "commits" else payload.get("sha")
                if identifier is None:
                    continue
                stable_id = f"github:{owner}/{repo}:{label}:{identifier}"
                updated_at = _iso_value(payload, commit=is_commit)
                payload_ids.append((stable_id, updated_at))
                is_latest = latest_watermark is None or (
                    updated_at is not None
                    and (
                        updated_at > str(latest_watermark)
                        or (updated_at == latest_watermark and stable_id not in latest_ids)
                    )
                )
                if is_latest:
                    category = "latest"
                else:
                    is_history = history_watermark is None or (
                        updated_at is not None
                        and (
                            updated_at < str(history_watermark)
                            or (
                                updated_at == history_watermark
                                and stable_id not in history_ids
                                and stable_id not in latest_ids
                            )
                        )
                    )
                    if not is_history:
                        continue
                    category = "history"
                content = payload.get("body") if resource != "commits" else None
                if resource == "commits":
                    nested = payload.get("commit")
                    message = nested.get("message") if isinstance(nested, Mapping) else None
                    content = message
                if not isinstance(content, str) or not content.strip():
                    content = str(payload.get("title") or payload.get("sha") or stable_id)
                content_truncated = len(content) > _MAX_CONTENT_CHARS
                bounded_content = content[:_MAX_CONTENT_CHARS]
                raw_snapshot = _json_bytes(payload)
                title = str(payload.get("title") or bounded_content.splitlines()[0] or stable_id)
                item = RawChangeItem(
                    logical_id=stable_id,
                    revision_id=_json_digest(payload),
                    operation="upsert",
                    title=title,
                    content=bounded_content,
                    original_ref=str(payload.get("html_url") or f"https://github.com/{owner}/{repo}"),
                    metadata={
                        "resource": resource,
                        "repository": f"github:{owner}/{repo}",
                        "number": identifier,
                        "updated_at": updated_at,
                        "content_truncated": content_truncated,
                        "raw_snapshot_omitted": raw_snapshot is None,
                    },
                    raw_snapshot=raw_snapshot,
                )
                candidate = (item, updated_at, stable_id, category)
                if category == "latest":
                    latest_candidates.append(candidate)
                else:
                    history_candidates.append(candidate)
            selected_candidates = [*latest_candidates, *history_candidates][:resource_limit]
            selected_ids = [
                (stable_id, updated_at, category) for _item, updated_at, stable_id, category in selected_candidates
            ]
            running_state = deepcopy(resource_state)
            for item, updated_at, stable_id, category in selected_candidates:
                _advance_resource_cursor(running_state, stable_id, updated_at, category)
                pending.append((item, {resource: deepcopy(running_state)}, None, None))
                remaining -= 1
            _finalize_resource_cursor(running_state, payload_ids, selected_ids)
            if selected_candidates:
                pending[-1] = (pending[-1][0], {resource: deepcopy(running_state)}, None, None)

        code_path: str | None = None
        if "code" in resources and remaining:
            head_sha_value = metadata.get("sha") or metadata.get("head_sha") or metadata.get("default_branch_sha")
            if not isinstance(head_sha_value, str) or not head_sha_value.strip():
                # GitHub's repository endpoint normally supplies no SHA; resolve it
                # through the branch endpoint only when code is enabled.
                branch = _as_object(
                    await _request_json(
                        f"{_API_ROOT}/repos/{owner}/{repo}/branches/{quote(default_branch, safe='')}", token
                    ),
                    name="default branch",
                )
                commit = branch.get("commit")
                head_sha_value = commit.get("sha") if isinstance(commit, Mapping) else None
            if not isinstance(head_sha_value, str) or not _SHA.fullmatch(head_sha_value.strip()):
                raise _fetch_error("GitHub default branch has no valid head SHA")
            head_sha = head_sha_value.strip()
            repository_cursor = old_cursor.get("repository")
            old_head = repository_cursor.get("head_sha") if isinstance(repository_cursor, Mapping) else None
            old_branch = repository_cursor.get("default_branch") if isinstance(repository_cursor, Mapping) else None
            if old_head != head_sha or old_branch != default_branch:
                code_path = str(_candidate_path(self._home, self._config.service_id).resolve())
                await self._materialize_code(run_id, owner, repo, head_sha, token)
                item = RawChangeItem(
                    logical_id=f"github:{owner}/{repo}:repository:code",
                    revision_id=head_sha,
                    operation="upsert",
                    title=f"{owner}/{repo} code",
                    content=f"GitHub repository code snapshot at commit {head_sha}.",
                    original_ref=f"https://github.com/{owner}/{repo}/tree/{head_sha}",
                    metadata={
                        "resource": "code",
                        "repository": f"github:{owner}/{repo}",
                        "default_branch": default_branch,
                        "head_sha": head_sha,
                        "materialized_source_path": code_path,
                    },
                )
                pending.append(
                    (
                        item,
                        {"repository": {"default_branch": default_branch, "head_sha": head_sha}},
                        code_path,
                        head_sha,
                    )
                )

        if not pending:
            return [FetchBatch(batch_id="batch-0", items=(), next_cursor=old_cursor)]

        batches: list[FetchBatch] = []
        temporary_cursor = deepcopy(old_cursor)
        for index in range(0, len(pending), _BATCH_SIZE):
            end = index + _BATCH_SIZE
            chunk = pending[index:end]
            for _, update, _, _ in chunk:
                _merge_cursor(temporary_cursor, update)
            materialized = next(((path, revision) for _, _, path, revision in chunk if path and revision), (None, None))
            batches.append(
                FetchBatch(
                    batch_id=f"batch-{index // _BATCH_SIZE}",
                    items=tuple(item for item, _, _, _ in chunk),
                    next_cursor=deepcopy(temporary_cursor),
                    materialized_source_path=materialized[0],
                    materialized_revision=materialized[1],
                )
            )
        return batches

    async def _list_resource(
        self,
        owner: str,
        repo: str,
        endpoint: str,
        token: str,
        *,
        is_commit: bool,
        max_items: int,
        since: str | None,
        initial_scan: bool,
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        seen_page_ids: dict[str, int] = {}
        for page in range(1, 101):
            params: dict[str, object] = {"page": page, "per_page": 100}
            if endpoint != "commits":
                params.update({"state": "all", "sort": "updated", "direction": "desc"})
            if since is not None and endpoint in {"issues", "commits"}:
                params["since"] = since
            payload = await _request_json(f"{_API_ROOT}/repos/{owner}/{repo}/{endpoint}", token, params=params)
            current = _as_list(payload, name=endpoint)
            if not current:
                break
            changed_items = False
            for item in current:
                identifier = item.get("sha") or item.get("number")
                key = str(identifier) if identifier is not None else _json_digest(item)
                existing_index = seen_page_ids.get(key)
                if existing_index is None:
                    seen_page_ids[key] = len(result)
                    result.append(item)
                    changed_items = True
                    continue
                existing = result[existing_index]
                old_timestamp = _iso_value(existing, commit=is_commit)
                new_timestamp = _iso_value(item, commit=is_commit)
                if new_timestamp is not None and (old_timestamp is None or new_timestamp > old_timestamp):
                    result[existing_index] = item
                    changed_items = True
            if not changed_items:
                break
            if len(current) > 100 or (initial_scan and len(result) >= max_items):
                break
            if not initial_scan and len(current) < 100:
                break
        result.sort(
            key=lambda item: (
                _iso_value(item, commit=is_commit) or "",
                str(item.get("sha") or item.get("number") or ""),
            ),
            reverse=True,
        )
        return result

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
