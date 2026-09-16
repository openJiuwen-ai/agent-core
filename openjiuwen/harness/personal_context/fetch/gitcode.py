"""GitCode REST/Git provider for the embedded personal-context core.

The provider owns one configured repository. Module helpers keep protocol
decoding and bounded reads outside the sole production provider class.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import ntpath
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

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

_API_ROOT = "https://api.gitcode.com/api/v5"
_WEB_ROOT = "https://gitcode.com"
_BATCH_SIZE = 20
_DEFAULT_MAX_ITEMS = 25
_REQUEST_TIMEOUT_SECONDS = 20 * 60
_MAX_CONTENT_CHARS = 2_000_000
_MAX_JSON_RESPONSE_BYTES = 16 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024
_MAX_GIT_OUTPUT_BYTES = 16 * 1024 * 1024
_MAX_GIT_FILES = 100_000
_MAX_WORKTREE_BYTES = 1024 * 1024 * 1024
_MAX_GIT_PATH_BYTES = 4096
_MAX_GIT_COMPONENT_BYTES = 255
_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_SAFE_BRANCH = re.compile(r"^(?![-/.])(?!.*(?:\.\.|//|@\{|\\))[A-Za-z0-9._/-]{1,255}(?<![/.])$")
_README_INLINE_LINK = re.compile(
    r"(?P<prefix>!?\[[^\]\r\n]*\]\()"
    r"(?P<target><[^<>\r\n]+>|[^\s)\r\n]+)"
    r"(?P<title>\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?"
    r"(?P<suffix>\))"
)
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_WINDOWS_RESERVED = {
    "aux",
    "clock$",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def _fetch_error(message: str, cause: BaseException | None = None) -> BaseError:
    return build_error(StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR, error_msg=message, cause=cause)


def _coerce_fetch_error(message: str, cause: Exception) -> BaseError:
    return cause if isinstance(cause, BaseError) else _fetch_error(message, cause)


def _safe_detail(exc: BaseException, pat: str) -> str:
    detail = str(exc).replace(pat, "<redacted>")
    detail = re.sub(r"https?://\S+", "<redacted-url>", detail)
    return detail[:512] or exc.__class__.__name__


def _service_root(home: Path, service_id: str) -> Path:
    return home / "materialized-sources" / "gitcode" / service_id


def _candidate_path(home: Path, service_id: str) -> Path:
    return _service_root(home, service_id) / "candidate"


def _marker_path(candidate: Path) -> Path:
    return candidate / ".personal-context-marker.json"


def _retry_readonly_removal(
    function: Any,
    path: str,
    exc_info: tuple[type[BaseException], BaseException, Any],
) -> None:
    error = exc_info[1]
    if not isinstance(error, PermissionError):
        raise error
    try:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            raise error
        os.chmod(path, stat.S_IWRITE)
        function(path)
    except OSError:
        raise error from None


def _extended_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    absolute = str(path.absolute())
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path(ntpath.join("\\\\?\\UNC", absolute[2:]))
    drive, tail = ntpath.splitdrive(absolute)
    namespace_root = f"\\\\?\\{drive}\\"
    return Path(ntpath.join(namespace_root, tail.lstrip("\\/")))


def _remove_tree(path: Path) -> None:
    if path.is_symlink() or path.exists():
        if path.is_symlink() or not path.is_dir():
            raise _fetch_error("GitCode materialized path is not a directory")
        shutil.rmtree(_extended_path(path), onerror=_retry_readonly_removal)


def _validate_layout(root: Path) -> None:
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise _fetch_error("GitCode materialized service path is not a directory")
    for name in ("candidate", "credentials"):
        path = root / name
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise _fetch_error(f"GitCode materialized {name} path is not a directory")


def _prune_empty_materialized_roots(service_root: Path) -> None:
    for directory in (service_root, service_root.parent, service_root.parent.parent):
        with contextlib.suppress(OSError):
            directory.rmdir()


def _discard_candidate(root: Path) -> None:
    _validate_layout(root)
    _remove_tree(root / "candidate")
    _prune_empty_materialized_roots(root)


def _discard_credentials(root: Path) -> None:
    _validate_layout(root)
    _remove_tree(root / "credentials")
    _prune_empty_materialized_roots(root)


def _read_marker(candidate: Path) -> dict[str, object] | None:
    marker = _marker_path(candidate)
    if marker.is_symlink():
        raise _fetch_error("GitCode candidate marker is invalid")
    if not marker.is_file():
        return None
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _fetch_error("GitCode candidate marker is invalid", exc) from None
    if not isinstance(value, dict) or not isinstance(value.get("run_id"), str):
        raise _fetch_error("GitCode candidate marker is invalid")
    return value


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
        raise _fetch_error("GitCode candidate marker write failed", exc) from None


def _validate_branch(branch: str) -> str:
    value = branch.strip()
    if not _SAFE_BRANCH.fullmatch(value) or value.endswith(".lock"):
        raise _fetch_error("GitCode default branch is unsafe")
    return value


def _validate_git_path(value: str) -> None:
    encoded = value.encode("utf-8")
    if not value or len(encoded) > _MAX_GIT_PATH_BYTES:
        raise _fetch_error("GitCode tree path exceeds the path limit")
    if "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise _fetch_error("GitCode tree contains an unsafe path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise _fetch_error("GitCode tree contains an unsafe path")
    for part in parts:
        if len(part.encode("utf-8")) > _MAX_GIT_COMPONENT_BYTES:
            raise _fetch_error("GitCode tree path exceeds the path limit")
        if part.endswith((".", " ")) or any(character in part for character in '<>:"|?*'):
            raise _fetch_error("GitCode tree contains an unsafe path")
        if any(ord(character) < 32 for character in part):
            raise _fetch_error("GitCode tree contains an unsafe path")
        if part.casefold() == ".git" or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED:
            raise _fetch_error("GitCode tree contains an unsafe path")


def _validate_git_tree(payload: bytes) -> tuple[int, int]:
    if not isinstance(payload, bytes):
        raise _fetch_error("GitCode tree output is invalid")
    entries = payload.split(b"\0")
    if entries and entries[-1] == b"":
        entries.pop()
    if len(entries) > _MAX_GIT_FILES:
        raise _fetch_error("GitCode tree contains too many files")
    seen: set[str] = set()
    total_size = 0
    for entry in entries:
        try:
            metadata, raw_path = entry.split(b"\t", 1)
            fields = metadata.split()
            if len(fields) != 4:
                raise ValueError
            raw_mode, raw_kind, raw_object_id, raw_size = fields
            mode = raw_mode.decode("ascii")
            kind = raw_kind.decode("ascii")
            object_id = raw_object_id.decode("ascii")
            size = int(raw_size)
            path = raw_path.decode("utf-8")
        except ValueError as exc:
            raise _fetch_error("GitCode tree output is invalid", exc) from None
        if mode not in {"100644", "100755"} or kind != "blob" or not _SHA.fullmatch(object_id):
            raise _fetch_error("GitCode tree contains a link, submodule, or special file")
        if size < 0:
            raise _fetch_error("GitCode tree output is invalid")
        _validate_git_path(path)
        duplicate_key = path.casefold()
        if duplicate_key in seen:
            raise _fetch_error("GitCode tree contains duplicate paths")
        seen.add(duplicate_key)
        total_size += size
        if total_size > _MAX_WORKTREE_BYTES:
            raise _fetch_error("GitCode tree exceeds the size limit")
    return len(entries), total_size


def _validate_worktree(candidate: Path) -> tuple[int, int]:
    files = 0
    total_size = 0
    seen_file_ids: set[tuple[int, int]] = set()
    scan_root = _extended_path(candidate)
    pending = [scan_root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise _fetch_error("GitCode worktree cannot be inspected", exc) from None
        for entry in entries:
            if directory == scan_root and entry.name == ".git":
                continue
            relative = Path(entry.path).relative_to(scan_root).as_posix()
            _validate_git_path(relative)
            try:
                # DirEntry.stat() returns zeroed file IDs/link counts on some
                # Windows Python builds, while os.stat() exposes the real values.
                info = os.stat(entry.path, follow_symlinks=False)
            except OSError as exc:
                raise _fetch_error("GitCode worktree cannot be inspected", exc) from None
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if entry.is_symlink() or (getattr(info, "st_file_attributes", 0) & reparse_flag):
                raise _fetch_error("GitCode worktree contains a link or reparse point")
            if stat.S_ISDIR(info.st_mode):
                pending.append(Path(entry.path))
                continue
            if not stat.S_ISREG(info.st_mode):
                raise _fetch_error("GitCode worktree contains a special file")
            file_id = (int(getattr(info, "st_dev", 0)), int(getattr(info, "st_ino", 0)))
            if getattr(info, "st_nlink", 1) > 1 or (file_id != (0, 0) and file_id in seen_file_ids):
                raise _fetch_error("GitCode worktree contains a hardlink")
            seen_file_ids.add(file_id)
            files += 1
            total_size += info.st_size
            if files > _MAX_GIT_FILES:
                raise _fetch_error("GitCode worktree contains too many files")
            if total_size > _MAX_WORKTREE_BYTES:
                raise _fetch_error("GitCode worktree exceeds the size limit")
    return files, total_size


def _write_askpass(root: Path, *, username: str, pat: str) -> dict[str, str]:
    root.mkdir(parents=True, exist_ok=False)
    with contextlib.suppress(OSError):
        root.chmod(0o700)
    script = root / "askpass.py"
    script.write_text(
        """from __future__ import annotations
import os
import sys

prompt = sys.argv[1].casefold() if len(sys.argv) > 1 else ""
key = "PERSONAL_CONTEXT_GIT_USERNAME" if "username" in prompt else "PERSONAL_CONTEXT_GIT_PASSWORD"
print(os.environ.get(key, ""))
""",
        encoding="utf-8",
        newline="\n",
    )
    wrapper = root / ("askpass.cmd" if os.name == "nt" else "askpass.sh")
    if os.name == "nt":
        wrapper.write_text(
            '@echo off\r\n"%PERSONAL_CONTEXT_GIT_PYTHON%" "%PERSONAL_CONTEXT_GIT_ASKPASS_SCRIPT%" %*\r\n',
            encoding="utf-8",
            newline="",
        )
    else:
        wrapper.write_text(
            '#!/bin/sh\nexec "$PERSONAL_CONTEXT_GIT_PYTHON" "$PERSONAL_CONTEXT_GIT_ASKPASS_SCRIPT" "$@"\n',
            encoding="utf-8",
            newline="\n",
        )
        wrapper.chmod(0o700)
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS_REQUIRE": "force",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_ASKPASS": str(wrapper),
            "PERSONAL_CONTEXT_GIT_ASKPASS_SCRIPT": str(script),
            "PERSONAL_CONTEXT_GIT_PYTHON": sys.executable,
            "PERSONAL_CONTEXT_GIT_USERNAME": username,
            "PERSONAL_CONTEXT_GIT_PASSWORD": pat,
        }
    )
    return environment


def _git_command(hooks: Path, *args: str) -> tuple[str, ...]:
    command = (
        "git",
        "-c",
        "credential.helper=",
        "-c",
        f"core.hooksPath={hooks}",
        "-c",
        "core.longpaths=true",
        *args,
    )
    return command


async def _read_subprocess_stream(stream: asyncio.StreamReader | None) -> bytes:
    if stream is None:
        return b""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(_CHUNK_SIZE)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > _MAX_GIT_OUTPUT_BYTES:
            raise _fetch_error("GitCode Git command output exceeds the size limit")
        chunks.append(chunk)


async def _run_git_command(
    args: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
) -> bytes:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
    except OSError as exc:
        raise _fetch_error("GitCode Git command could not start") from exc
    try:
        stdout, stderr = await asyncio.wait_for(
            asyncio.gather(
                _read_subprocess_stream(process.stdout),
                _read_subprocess_stream(process.stderr),
            ),
            timeout=timeout,
        )
        return_code = await process.wait()
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        raise _fetch_error("GitCode Git command timed out") from None
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        raise
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        raise
    if return_code != 0:
        detail = stderr.decode("utf-8", errors="replace")
        detail = re.sub(r"https?://\S+", "<redacted-url>", detail).strip()[:512]
        suffix = f": {detail}" if detail else ""
        raise _fetch_error(f"GitCode Git command failed with exit code {return_code}{suffix}")
    return stdout


async def _request_json(
    url: str,
    pat: str,
    *,
    params: Mapping[str, object] | None = None,
    allow_not_found: bool = False,
) -> object | None:
    try:
        return await retry_provider_read(
            lambda: _request_json_once(
                url,
                pat,
                params=params,
                allow_not_found=allow_not_found,
            ),
            provider="gitcode",
            operation_name="rest_json",
            classify=_gitcode_read_retry_reason,
        )
    except Exception as exc:
        raise _coerce_fetch_error("GitCode request failed", exc) from None


def _gitcode_read_retry_reason(exc: BaseException) -> str | None:
    return classify_transport_error(exc) or classify_payload_error(exc)


async def _request_json_once(
    url: str,
    pat: str,
    *,
    params: Mapping[str, object] | None = None,
    allow_not_found: bool = False,
) -> object | None:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {pat}",
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
                raise RuntimeError("GitCode request returned an unsuccessful HTTP status")
            return await _read_json_response(response)


async def _read_json_response(response: Any) -> object:
    headers = getattr(response, "headers", {})
    content_length = headers.get("Content-Length") if isinstance(headers, Mapping) else None
    if content_length is not None:
        try:
            if int(content_length) > _MAX_JSON_RESPONSE_BYTES:
                raise _fetch_error("GitCode JSON response exceeds the size limit")
        except ValueError as exc:
            raise _fetch_error("GitCode JSON response has an invalid size", exc) from None
    stream = getattr(response, "content", None)
    if stream is not None:
        chunks: list[bytes] = []
        total = 0
        async for chunk in stream.iter_chunked(_CHUNK_SIZE):
            total += len(chunk)
            if total > _MAX_JSON_RESPONSE_BYTES:
                raise _fetch_error("GitCode JSON response exceeds the size limit")
            chunks.append(chunk)
        payload = b"".join(chunks)
        if not payload:
            raise EOFError("GitCode JSON response is empty")
        return json.loads(payload)
    value = await response.json()
    try:
        encoded_size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise _fetch_error("GitCode JSON response is not serializable", exc) from None
    if encoded_size > _MAX_JSON_RESPONSE_BYTES:
        raise _fetch_error("GitCode JSON response exceeds the size limit")
    return value


def _as_object(value: object, *, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _fetch_error(f"GitCode {name} response is not an object")
    return dict(value)


def _as_list(value: object, *, name: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise _fetch_error(f"GitCode {name} response is not a list")
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


def _validate_selection_cursor(cursor: dict[str, object] | None) -> None:
    if cursor is None:
        return
    if not isinstance(cursor, Mapping) or set(cursor) - {"_selection"}:
        raise _fetch_error("GitCode cursor contains unsupported fields")


def _head_sha(value: Mapping[str, object]) -> str | None:
    direct = value.get("default_branch_sha") or value.get("sha") or value.get("id")
    if isinstance(direct, str) and _SHA.fullmatch(direct.strip()):
        return direct.strip()
    commit = value.get("commit")
    if isinstance(commit, Mapping):
        nested = commit.get("id") or commit.get("sha")
        if isinstance(nested, str) and _SHA.fullmatch(nested.strip()):
            return nested.strip()
    return None


def _head_time(value: Mapping[str, object]) -> str | None:
    for key in ("head_commit_time", "pushed_at", "committed_date", "created_at"):
        direct = value.get(key)
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
    commit = value.get("commit")
    if isinstance(commit, Mapping):
        for key in ("committed_date", "created_at"):
            nested = commit.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


def _resource_time(item: Mapping[str, object], *, is_commit: bool) -> str | None:
    keys = ("committed_date", "created_at", "authored_date") if is_commit else ("updated_at", "created_at")
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = item.get("commit")
    if is_commit and isinstance(nested, Mapping):
        for key in ("committed_date", "created_at", "authored_date"):
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _readme_text(readme: Mapping[str, object]) -> str:
    content = readme.get("content")
    if not isinstance(content, str):
        raise _fetch_error("GitCode README response has no content")
    if readme.get("encoding") == "base64":
        try:
            return base64.b64decode("".join(content.split()), validate=True).decode("utf-8")
        except ValueError as exc:
            raise _fetch_error("GitCode README is not valid UTF-8 base64", exc) from None
    return content


def _rewrite_readme_links(markdown: str, *, owner: str, repo: str, revision: str) -> str:
    """Resolve repository-relative README links against one immutable revision."""

    def replace(match: re.Match[str]) -> str:
        wrapped_target = match.group("target")
        target = wrapped_target[1:-1] if wrapped_target.startswith("<") else wrapped_target
        if not target or target.startswith(("#", "?")) or _URI_SCHEME.match(target):
            return match.group(0)
        is_image = match.group("prefix").startswith("!")
        view = "raw" if is_image else "blob"
        base = f"{_WEB_ROOT}/{owner}/{repo}/{view}/{revision}/README.md"
        if target.startswith("/") and not target.startswith("//"):
            target = target.lstrip("/")
            base = f"{_WEB_ROOT}/{owner}/{repo}/{view}/{revision}/"
        absolute = quote(
            urljoin(base, target),
            safe=":/?#[]@!$&'()*+,;=%",
        )
        return f"{match.group('prefix')}{absolute}{match.group('title') or ''}{match.group('suffix')}"

    return _README_INLINE_LINK.sub(replace, markdown)


def _resource_content(payload: Mapping[str, object], *, stable_id: str, is_commit: bool) -> str:
    content = payload.get("message") if is_commit else payload.get("body")
    if is_commit and not isinstance(content, str):
        nested = payload.get("commit")
        content = nested.get("message") if isinstance(nested, Mapping) else None
    if not isinstance(content, str) or not content.strip():
        content = str(payload.get("title") or payload.get("id") or payload.get("sha") or stable_id)
    return content


def _candidate(
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
            raise _fetch_error(f"GitCode {lane} candidate has no usable time")
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


def _commit_identifier(payload: Mapping[str, object]) -> str | None:
    value = payload.get("id") or payload.get("sha")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _is_pull_request_issue(payload: Mapping[str, object]) -> bool:
    if "pull_request" in payload:
        return True
    kind = payload.get("type")
    return isinstance(kind, str) and kind.replace("_", " ").casefold() in {"pull request", "merge request"}


def _select_fair_candidates(
    candidates: tuple[dict[str, object], ...],
    cursor: dict[str, object] | None,
    limit: int,
) -> tuple[dict[str, object], ...]:
    """Keep singleton lanes and share the remaining quota across repeatable lanes."""

    pending = list(select_latest_candidates(candidates, cursor, max(1, len(candidates))))
    if len(pending) <= limit:
        return tuple(pending)

    selected = [candidate for candidate in pending if candidate["resource_lane"] in {"readme", "code"}][:limit]
    remaining = limit - len(selected)
    repeatable_lanes = ("issue", "pull_request", "commit")
    queues = {
        lane: [candidate for candidate in pending if candidate["resource_lane"] == lane] for lane in repeatable_lanes
    }
    while remaining > 0 and any(queues.values()):
        for lane in repeatable_lanes:
            if remaining == 0:
                break
            if queues[lane]:
                selected.append(queues[lane].pop(0))
                remaining -= 1

    selected_keys = {
        (
            str(candidate["resource_lane"]),
            str(candidate["stable_id"]),
            str(candidate["revision_id"]),
        )
        for candidate in selected
    }
    if remaining:
        for candidate in pending:
            key = (
                str(candidate["resource_lane"]),
                str(candidate["stable_id"]),
                str(candidate["revision_id"]),
            )
            if key not in selected_keys:
                selected.append(candidate)
                selected_keys.add(key)
                remaining -= 1
                if remaining == 0:
                    break
    order = {
        (
            str(candidate["resource_lane"]),
            str(candidate["stable_id"]),
            str(candidate["revision_id"]),
        ): index
        for index, candidate in enumerate(pending)
    }
    selected.sort(
        key=lambda candidate: order[
            (
                str(candidate["resource_lane"]),
                str(candidate["stable_id"]),
                str(candidate["revision_id"]),
            )
        ]
    )
    return tuple(selected)


class GitCodeFetchService(ContextFetchService):
    """Fetch one GitCode repository and optionally materialize its selected code snapshot."""

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
            _discard_credentials(root)
            _validate_selection_cursor(cursor)
            pat = str(self._config.credentials.get("pat", "")).strip()
            if not pat:
                raise _fetch_error("GitCode PAT is required")
            source = dict(self._config.source)
            owner = str(source.get("owner", "")).strip()
            repo = str(source.get("repo", "")).strip()
            raw_resources = source.get("resources", ())
            resources = [str(item) for item in raw_resources] if isinstance(raw_resources, (list, tuple)) else []
            repository_url = f"{_API_ROOT}/repos/{owner}/{repo}"
            metadata = _as_object(
                await _request_json(repository_url, pat),
                name="repository metadata",
            )
            default_branch = metadata.get("default_branch")
            if not isinstance(default_branch, str) or not default_branch.strip():
                raise _fetch_error("GitCode repository has no default branch")
            default_branch = _validate_branch(default_branch)
            head_sha = _head_sha(metadata)
            head_time = _head_time(metadata)
            needs_exact_head = any(resource in resources for resource in ("readme", "code"))
            needs_head_time = head_time is None and self._config.time_range.get("mode") != "all"
            if needs_exact_head and (head_sha is None or needs_head_time):
                branch = _as_object(
                    await _request_json(
                        f"{repository_url}/branches/{quote(default_branch, safe='')}",
                        pat,
                    ),
                    name="default branch",
                )
                head_sha = head_sha or _head_sha(branch)
                head_time = head_time or _head_time(branch)

            candidates: list[dict[str, object]] = []
            if "readme" in resources:
                readme: object | None = None
                for readme_name in ("README.md", "README_CN.md", "README_zh.md"):
                    readme = await _request_json(
                        f"{repository_url}/contents/{quote(readme_name, safe='')}",
                        pat,
                        params={"ref": default_branch},
                        allow_not_found=True,
                    )
                    if readme is not None:
                        break
                if readme is not None:
                    readme_obj = _as_object(readme, name="README")
                    if head_sha is None:
                        raise _fetch_error("GitCode default branch has no valid head SHA")
                    readme_text = _rewrite_readme_links(
                        _readme_text(readme_obj),
                        owner=owner,
                        repo=repo,
                        revision=head_sha,
                    )
                    raw_snapshot = _json_bytes(readme_obj)
                    item = RawChangeItem(
                        logical_id=f"gitcode:{owner}/{repo}:repository:readme",
                        revision_id=_sha256(readme_text),
                        operation="upsert",
                        title=f"{owner}/{repo} README",
                        content=readme_text[:_MAX_CONTENT_CHARS],
                        original_ref=str(
                            readme_obj.get("html_url")
                            or f"{_WEB_ROOT}/{owner}/{repo}/blob/{quote(default_branch, safe='')}/README.md"
                        ),
                        metadata={
                            "resource": "readme",
                            "repository": f"gitcode:{owner}/{repo}",
                            "default_branch": default_branch,
                            "content_truncated": len(readme_text) > _MAX_CONTENT_CHARS,
                            "raw_snapshot_omitted": raw_snapshot is None,
                        },
                        raw_snapshot=raw_snapshot,
                    )
                    readme_candidate = _candidate(
                        item,
                        lane="readme",
                        candidate_time=head_time,
                        time_range=self._config.time_range,
                        run_started_at=run_started_at,
                    )
                    if readme_candidate is not None:
                        candidates.append(readme_candidate)

            for resource, endpoint, label, is_commit in (
                ("issues", "issues", "issue", False),
                ("pull_requests", "pulls", "pull_request", False),
                ("commits", "commits", "commit", True),
            ):
                if resource not in resources:
                    continue
                payloads = await self._list_resource(repository_url, endpoint, pat)
                for payload in payloads:
                    if resource == "issues" and _is_pull_request_issue(payload):
                        continue
                    identifier = _commit_identifier(payload) if is_commit else payload.get("number")
                    if identifier is None or not str(identifier).strip():
                        continue
                    identifier = str(identifier).strip()
                    stable_id = f"gitcode:{owner}/{repo}:{label}:{identifier}"
                    updated_at = _resource_time(payload, is_commit=is_commit)
                    content = _resource_content(payload, stable_id=stable_id, is_commit=is_commit)
                    raw_snapshot = _json_bytes(payload)
                    item = RawChangeItem(
                        logical_id=stable_id,
                        revision_id=_json_digest(payload),
                        operation="upsert",
                        title=str(payload.get("title") or content.splitlines()[0] or stable_id),
                        content=content[:_MAX_CONTENT_CHARS],
                        original_ref=str(
                            payload.get("html_url") or payload.get("web_url") or f"{_WEB_ROOT}/{owner}/{repo}"
                        ),
                        metadata={
                            "resource": resource,
                            "repository": f"gitcode:{owner}/{repo}",
                            "number": identifier,
                            "updated_at": updated_at,
                            "content_truncated": len(content) > _MAX_CONTENT_CHARS,
                            "raw_snapshot_omitted": raw_snapshot is None,
                        },
                        raw_snapshot=raw_snapshot,
                    )
                    resource_candidate = _candidate(
                        item,
                        lane=label,
                        candidate_time=updated_at,
                        time_range=self._config.time_range,
                        run_started_at=run_started_at,
                    )
                    if resource_candidate is not None:
                        candidates.append(resource_candidate)

            if "code" in resources:
                if head_sha is None:
                    raise _fetch_error("GitCode default branch has no valid head SHA")
                code_path = str(_candidate_path(self._home, self._config.service_id).resolve())
                item = RawChangeItem(
                    logical_id=f"gitcode:{owner}/{repo}:repository:code",
                    revision_id=head_sha,
                    operation="upsert",
                    title=f"{owner}/{repo} code",
                    content=f"GitCode repository code snapshot at commit {head_sha}.",
                    original_ref=f"{_WEB_ROOT}/{owner}/{repo}/tree/{head_sha}",
                    metadata={
                        "resource": "code",
                        "repository": f"gitcode:{owner}/{repo}",
                        "default_branch": default_branch,
                        "head_sha": head_sha,
                        "materialized_source_path": code_path,
                    },
                )
                code_candidate = _candidate(
                    item,
                    lane="code",
                    candidate_time=head_time,
                    time_range=self._config.time_range,
                    run_started_at=run_started_at,
                    extra={
                        "owner": owner,
                        "repo": repo,
                        "default_branch": default_branch,
                        "head_sha": head_sha,
                        "materialized_source_path": code_path,
                    },
                )
                if code_candidate is not None:
                    candidates.append(code_candidate)

            max_items = self._config.max_items_per_run or _DEFAULT_MAX_ITEMS
            return _select_fair_candidates(tuple(candidates), cursor, max_items)
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            pat = str(self._config.credentials.get("pat", ""))
            raise _fetch_error(f"GitCode preparation failed: {_safe_detail(exc, pat)}", exc) from None

    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
        candidates: tuple[dict[str, object], ...],
    ) -> AsyncIterator[FetchBatch]:
        try:
            pat = str(self._config.credentials.get("pat", "")).strip()
            next_cursor = dict(cursor) if cursor is not None else {}
            if not candidates:
                yield FetchBatch(batch_id="batch-0", items=(), next_cursor=next_cursor)
                return
            for index in range(0, len(candidates), _BATCH_SIZE):
                chunk = candidates[slice(index, index + _BATCH_SIZE)]
                items: list[RawChangeItem] = []
                materialized_path: str | None = None
                materialized_revision: str | None = None
                for candidate in chunk:
                    item = candidate.get("item")
                    if not isinstance(item, RawChangeItem):
                        raise _fetch_error("GitCode candidate item is invalid")
                    if candidate.get("resource_lane") == "code":
                        owner = str(candidate.get("owner", ""))
                        repo = str(candidate.get("repo", ""))
                        default_branch = str(candidate.get("default_branch", ""))
                        head_sha = str(candidate.get("head_sha", ""))
                        await self._materialize_code(run_id, (owner, repo), default_branch, head_sha, pat)
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
            pat = str(self._config.credentials.get("pat", ""))
            raise _fetch_error(f"GitCode fetch failed: {_safe_detail(exc, pat)}", exc) from None

    async def _list_resource(
        self,
        repository_url: str,
        endpoint: str,
        pat: str,
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        for page in range(1, 101):
            params: dict[str, object] = {"page": page, "per_page": 100}
            if endpoint != "commits":
                params.update({"state": "all", "sort": "updated", "direction": "desc"})
            payload = await _request_json(
                f"{repository_url}/{endpoint}",
                pat,
                params=params,
            )
            current = _as_list(payload, name=endpoint)
            if not current:
                return result
            advanced = False
            for item in current:
                identifier = _commit_identifier(item) or item.get("number")
                stable = str(identifier) if identifier is not None else _json_digest(item)
                if stable in seen_ids:
                    continue
                seen_ids.add(stable)
                result.append(item)
                advanced = True
            if not advanced:
                raise _fetch_error(f"GitCode {endpoint} pagination did not advance")
            if len(current) != 100:
                return result
        raise _fetch_error(f"GitCode {endpoint} pagination exceeded the limit")

    async def _materialize_code(
        self,
        run_id: str,
        repository: tuple[str, str],
        default_branch: str,
        head_sha: str,
        pat: str,
    ) -> None:
        owner, repo = repository
        user = _as_object(await _request_json(f"{_API_ROOT}/user", pat), name="current user")
        login = user.get("login") or user.get("username")
        if not isinstance(login, str) or not login.strip() or any(ord(character) < 32 for character in login):
            raise _fetch_error("GitCode current user has no safe login")
        branch = _validate_branch(default_branch)
        if not _SHA.fullmatch(head_sha):
            raise _fetch_error("GitCode target head SHA is invalid")

        root = _service_root(self._home, self._config.service_id)
        candidate = root / "candidate"
        credential_run = root / "credentials" / _sha256(run_id)[:24]
        _validate_layout(root)
        _discard_candidate(root)
        _discard_credentials(root)
        root.mkdir(parents=True, exist_ok=True)
        candidate.mkdir(parents=True, exist_ok=False)
        try:
            environment = _write_askpass(credential_run, username=login.strip(), pat=pat)
            hooks = credential_run / "empty-hooks"
            hooks.mkdir()
            remote_url = f"https://gitcode.com/{owner}/{repo}.git"
            timeout = float(_REQUEST_TIMEOUT_SECONDS)
            await _run_git_command(
                _git_command(hooks, "init", "--quiet"),
                cwd=candidate,
                env=environment,
                timeout=timeout,
            )
            await _run_git_command(
                _git_command(hooks, "remote", "add", "origin", remote_url),
                cwd=candidate,
                env=environment,
                timeout=timeout,
            )
            await _run_git_command(
                _git_command(
                    hooks,
                    "fetch",
                    "--depth=1",
                    "--no-tags",
                    "--no-recurse-submodules",
                    "origin",
                    f"refs/heads/{branch}",
                ),
                cwd=candidate,
                env=environment,
                timeout=timeout,
            )
            fetched_head = (
                (
                    await _run_git_command(
                        _git_command(hooks, "rev-parse", "FETCH_HEAD"),
                        cwd=candidate,
                        env=environment,
                        timeout=timeout,
                    )
                )
                .decode("ascii", errors="strict")
                .strip()
            )
            if fetched_head != head_sha:
                raise _fetch_error("GitCode fetched HEAD does not match the REST target SHA")
            tree = await _run_git_command(
                _git_command(hooks, "ls-tree", "-r", "-z", "-l", "FETCH_HEAD"),
                cwd=candidate,
                env=environment,
                timeout=timeout,
            )
            expected_files, _expected_bytes = _validate_git_tree(tree)
            await _run_git_command(
                _git_command(hooks, "checkout", "--detach", "--force", "FETCH_HEAD"),
                cwd=candidate,
                env=environment,
                timeout=timeout,
            )
            actual_files, _actual_bytes = _validate_worktree(candidate)
            if actual_files != expected_files:
                raise _fetch_error("GitCode worktree file count does not match the Git tree")
            _remove_tree(candidate / ".git")
            _write_marker(candidate, run_id=run_id, owner=owner, repo=repo, head_sha=head_sha)
        except asyncio.CancelledError:
            _remove_tree(candidate)
            raise
        except BaseError:
            _remove_tree(candidate)
            raise
        except Exception as exc:
            _remove_tree(candidate)
            raise _fetch_error(f"GitCode code materialization failed: {_safe_detail(exc, pat)}") from None
        finally:
            _discard_credentials(root)

    async def commit_run(self, *, run_id: str) -> None:
        root = _service_root(self._home, self._config.service_id)
        candidate = root / "candidate"
        _validate_layout(root)
        if not candidate.exists():
            _prune_empty_materialized_roots(root)
            return
        marker = _read_marker(candidate)
        if marker is None:
            raise _fetch_error("GitCode candidate marker is missing")
        if marker.get("run_id") != run_id:
            return
        try:
            _remove_tree(candidate)
            _prune_empty_materialized_roots(root)
        except OSError as exc:
            raise _fetch_error("GitCode candidate commit failed", exc) from None

    async def abort_run(self, *, run_id: str) -> None:
        root = _service_root(self._home, self._config.service_id)
        candidate = root / "candidate"
        _validate_layout(root)
        if not candidate.exists():
            _prune_empty_materialized_roots(root)
            return
        marker = _read_marker(candidate)
        if marker is None:
            raise _fetch_error("GitCode candidate marker is missing")
        if marker.get("run_id") != run_id:
            return
        try:
            _remove_tree(candidate)
            _prune_empty_materialized_roots(root)
        except OSError as exc:
            raise _fetch_error("GitCode candidate abort failed", exc) from None
