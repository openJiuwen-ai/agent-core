"""Local Files personal-context fetch provider."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import stat as stat_module
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, cast

import pdfplumber

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch.base import ContextFetchService
from openjiuwen.harness.personal_context.fetch.cursor_selection import (
    candidate_in_time_range,
    select_latest_candidates,
)
from openjiuwen.harness.personal_context.fetch.retry import classify_file_error, retry_provider_read
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem
from openjiuwen.harness.personal_context.status_codes import StatusCode, build_error

_SUPPORTED_EXTENSIONS = {".md", ".txt", ".json", ".pdf"}
_SKIPPED_DIRECTORIES = {".git", ".venv", "node_modules", "__pycache__", ".personal_context"}
_BATCH_SIZE = 20
_DEFAULT_MAX_ITEMS = 100
_TEXT_MAX_SIZE = 1 * 1024 * 1024
_PDF_MAX_SIZE = 20 * 1024 * 1024
_RAW_SNAPSHOT_MAX_SIZE = 2 * 1024 * 1024
_MAX_CONTENT_CHARS = 2_000_000
_READ_CHUNK_SIZE = 64 * 1024


class LocalFilesFetchService(ContextFetchService):
    """Scan one configured local directory and emit file changes."""

    async def prepare_run(
        self,
        *,
        run_id: str,
        run_started_at: datetime,
        cursor: dict[str, object] | None,
    ) -> tuple[dict[str, object], ...]:
        del run_id
        try:
            _validate_selection_cursor(cursor)
            root = await asyncio.to_thread(_root_dir, self._config)
            metadata = await retry_provider_read(
                lambda: asyncio.to_thread(_scan_files, root),
                provider="local_files",
                operation_name="directory_scan",
                classify=classify_file_error,
            )
            candidates: list[dict[str, object]] = []
            for item in metadata:
                mtime_ns = _integer(item.get("mtime_ns"), name="mtime_ns")
                size = _integer(item.get("size"), name="size")
                path = Path(str(item["path"])).resolve()
                candidate_time = (
                    datetime.fromtimestamp(mtime_ns / 1_000_000_000, tz=UTC).isoformat().replace("+00:00", "Z")
                )
                candidate = {
                    **item,
                    "stable_id": os.path.normcase(str(path)),
                    "revision_id": hashlib.sha256(f"{mtime_ns}:{size}".encode()).hexdigest(),
                    "candidate_time": candidate_time,
                    "resource_lane": "file",
                    "locator": str(path),
                    "root_dir": str(root),
                }
                if candidate_in_time_range(candidate_time, self._config.time_range, run_started_at):
                    candidates.append(candidate)
            limit = self._config.max_items_per_run or _DEFAULT_MAX_ITEMS
            return select_latest_candidates(tuple(candidates), cursor, limit)
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            raise build_error(
                StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR,
                error_msg="local files preparation failed",
                cause=exc,
            ) from None

    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
        candidates: tuple[dict[str, object], ...],
    ) -> AsyncIterator[FetchBatch]:
        del run_id
        next_cursor = dict(cursor) if cursor is not None else {}
        try:
            root = await asyncio.to_thread(_root_dir, self._config)
            if not candidates:
                yield FetchBatch(batch_id="batch-0", items=(), next_cursor=next_cursor)
                return
            batch_index = 0
            for start in range(0, len(candidates), _BATCH_SIZE):
                end = start + _BATCH_SIZE
                chunk = candidates[start:end]
                materialized: list[dict[str, Any]] = []
                for candidate in chunk:
                    materialized.append(
                        await retry_provider_read(
                            partial(asyncio.to_thread, _materialize_candidate, candidate),
                            provider="local_files",
                            operation_name="file_read",
                            classify=classify_file_error,
                        )
                    )
                items = tuple(_change_item(root, change) for change in materialized)
                yield FetchBatch(
                    batch_id=f"batch-{batch_index}",
                    items=items,
                    next_cursor=next_cursor,
                )
                batch_index += 1
        except asyncio.CancelledError:
            raise
        except BaseError:
            raise
        except Exception as exc:
            raise build_error(
                StatusCode.CONTEXT_PROACTIVE_FETCH_EXECUTION_ERROR,
                error_msg="local files fetch failed",
                cause=exc,
            ) from None


def _file_error(message: str, cause: BaseException | None = None) -> BaseError:
    return build_error(StatusCode.CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR, error_msg=message, cause=cause)


def _root_dir(config: PersonalContextFetchServiceConfig) -> Path:
    value = config.source.get("root_dir")
    if not isinstance(value, str) or not value.strip():
        raise _file_error("local files root directory is invalid")
    root = Path(value).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise _file_error("local files root directory is unavailable")
    return root


def _validate_selection_cursor(cursor: dict[str, object] | None) -> None:
    if cursor is None:
        return
    if not isinstance(cursor, Mapping) or set(cursor) - {"_selection"}:
        raise ValueError("local files cursor contains unsupported fields")


def _integer(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"cursor {name} is invalid")
    return value


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _scan_error(error: OSError) -> None:
    raise _file_error("local files directory scan failed", error)


def _scan_files(root: Path) -> list[dict[str, Any]]:
    """Return bounded metadata only; file content is materialized later."""

    candidates: list[dict[str, Any]] = []
    try:
        for current_root, dirnames, filenames in os.walk(
            root,
            topdown=True,
            followlinks=False,
            onerror=_scan_error,
        ):
            current = Path(current_root)
            kept_dirs: list[str] = []
            for dirname in sorted(dirnames, key=lambda value: (value.casefold(), value)):
                if dirname.casefold() in _SKIPPED_DIRECTORIES:
                    continue
                path = current / dirname
                if path.is_symlink():
                    continue
                resolved = path.resolve()
                try:
                    is_directory = stat_module.S_ISDIR(resolved.stat().st_mode)
                except OSError as exc:
                    raise _file_error("local files directory metadata read failed", exc) from None
                if _inside(resolved, root) and is_directory:
                    kept_dirs.append(dirname)
            dirnames[:] = kept_dirs

            for filename in sorted(filenames, key=lambda value: (value.casefold(), value)):
                path = current / filename
                if path.is_symlink() or path.suffix.casefold() not in _SUPPORTED_EXTENSIONS:
                    continue
                resolved = path.resolve()
                if not _inside(resolved, root):
                    continue
                try:
                    stat = resolved.stat()
                except OSError as exc:
                    raise _file_error("local files metadata read failed", exc) from None
                if not stat_module.S_ISREG(stat.st_mode):
                    continue
                relative_path = resolved.relative_to(root).as_posix()
                candidates.append(
                    {
                        "relative_path": relative_path,
                        "path": resolved,
                        "mtime_ns": stat.st_mtime_ns,
                        "size": stat.st_size,
                        "extension": resolved.suffix.casefold(),
                    }
                )
    except asyncio.CancelledError:
        raise
    except BaseError:
        raise
    except Exception as exc:
        raise _file_error("local files metadata scan failed", exc) from None
    return candidates


def _max_size(extension: str) -> int:
    return _PDF_MAX_SIZE if extension.casefold() == ".pdf" else _TEXT_MAX_SIZE


def _read_checked(path: Path, *, extension: str) -> bytes:
    """Read one bounded file while checking its metadata before/after."""

    try:
        before = path.stat()
        maximum = _max_size(extension)
        if before.st_size > maximum:
            raise ValueError("file exceeds the provider size limit")

        chunks: list[bytes] = []
        total = 0
        with path.open("rb") as stream:
            while True:
                remaining = maximum - total
                chunk = stream.read(min(_READ_CHUNK_SIZE, remaining + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise ValueError("file exceeds the provider size limit")
                chunks.append(chunk)
        after = path.stat()
        if after.st_mtime_ns != before.st_mtime_ns or after.st_size != before.st_size or total != before.st_size:
            raise OSError(getattr(errno, "ESTALE", 116), "file changed while it was being read")
        return b"".join(chunks)
    except asyncio.CancelledError:
        raise
    except BaseError:
        raise
    except Exception as exc:
        raise _file_error("local file read failed", exc) from None


def _materialize_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(str(candidate["path"]))
    extension = str(candidate["extension"])
    try:
        raw_bytes = _read_checked(path, extension=extension)
        content_revision_id = _revision_id(raw_bytes)
        if extension == ".pdf":
            content, content_truncated = _read_pdf(path)
        else:
            content = raw_bytes.decode("utf-8")
            content_truncated = len(content) > _MAX_CONTENT_CHARS
            if content_truncated:
                content = content[:_MAX_CONTENT_CHARS]
        current = path.stat()
        if current.st_mtime_ns != candidate["mtime_ns"] or current.st_size != candidate["size"]:
            raise ValueError("file changed between fingerprint and materialization")
        return {
            **candidate,
            "content_revision_id": content_revision_id,
            "content": content,
            "raw_snapshot": raw_bytes if len(raw_bytes) <= _RAW_SNAPSHOT_MAX_SIZE else None,
            "content_truncated": content_truncated,
        }
    except asyncio.CancelledError:
        raise
    except BaseError:
        raise
    except Exception as exc:
        raise _file_error("local file materialization failed", exc) from None


def _read_pdf(path: Path) -> tuple[str, bool]:
    try:
        parts: list[str] = []
        total = 0
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if not text:
                    continue
                if parts:
                    text = "\n" + text
                remaining = _MAX_CONTENT_CHARS - total
                if len(text) > remaining:
                    parts.append(text[:remaining])
                    return "".join(parts), True
                parts.append(text)
                total += len(text)
                if total >= _MAX_CONTENT_CHARS:
                    return "".join(parts), True
        return "".join(parts), False
    except asyncio.CancelledError:
        raise
    except BaseError:
        raise
    except Exception as exc:
        raise _file_error("local PDF extraction failed", exc) from None


def _revision_id(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _logical_id(root: Path, relative_path: str) -> str:
    root_digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    return f"local_files:{root_digest}:{relative_path}"


def _change_item(root: Path, change: Mapping[str, object]) -> RawChangeItem:
    path = str(change["relative_path"])
    absolute_path = Path(str(change["path"])).resolve()
    metadata = {
        "path": path,
        "root_dir": str(root),
        "size": _integer(change["size"], name="size"),
        "mtime_ns": _integer(change["mtime_ns"], name="mtime_ns"),
        "raw_snapshot_available": change.get("raw_snapshot") is not None,
        "content_truncated": bool(change.get("content_truncated", False)),
    }
    try:
        raw_snapshot = cast(str | bytes | None, change.get("raw_snapshot"))
        return RawChangeItem(
            logical_id=_logical_id(root, path),
            revision_id=str(change["content_revision_id"]),
            operation="upsert",
            title=path,
            content=str(change["content"]),
            original_ref=str(absolute_path),
            metadata=metadata,
            raw_snapshot=raw_snapshot,
        )
    except asyncio.CancelledError:
        raise
    except BaseError:
        raise
    except Exception as exc:
        raise _file_error("local file change is invalid", exc) from None
