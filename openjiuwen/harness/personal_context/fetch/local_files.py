"""Local Files personal-context fetch provider."""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat as stat_module
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any, Literal, cast

import pdfplumber

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextFetchServiceConfig
from openjiuwen.harness.personal_context.fetch.base import ContextFetchService
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

    async def fetch(
        self,
        *,
        run_id: str,
        cursor: dict[str, object] | None,
    ) -> AsyncIterator[FetchBatch]:
        del run_id
        try:
            previous = _cursor_files(cursor)
            root = await asyncio.to_thread(_root_dir, self._config)
            metadata_candidates = await asyncio.to_thread(_scan_files, root)
            candidates: list[dict[str, Any]] = []
            for candidate in metadata_candidates:
                candidates.append(await asyncio.to_thread(_fingerprint_candidate, candidate))

            current = {candidate["relative_path"]: candidate for candidate in candidates}
            limit = self._config.max_items_per_run or _DEFAULT_MAX_ITEMS
            changes = _changes(
                root,
                candidates,
                current,
                previous,
                initial=cursor is None or cursor == {},
                max_items=limit,
            )
            selected = changes[:limit]

            if not selected:
                yield FetchBatch(batch_id="batch-0", items=(), next_cursor={"files": previous})
                return

            temporary_cursor = dict(previous)
            batch_index = 0
            for start in range(0, len(selected), _BATCH_SIZE):
                end = start + _BATCH_SIZE
                chunk = selected[start:end]
                materialized: list[dict[str, Any]] = []
                for change in chunk:
                    if change["operation"] == "upsert":
                        materialized.append(await asyncio.to_thread(_materialize_candidate, change))
                    else:
                        materialized.append(change)
                items = tuple(_change_item(root, change) for change in materialized)
                for change in materialized:
                    path = str(change["relative_path"])
                    if change["operation"] == "delete":
                        temporary_cursor.pop(path, None)
                    else:
                        temporary_cursor[path] = _summary(change)
                yield FetchBatch(
                    batch_id=f"batch-{batch_index}",
                    items=items,
                    next_cursor={"files": temporary_cursor},
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


def _cursor_files(cursor: dict[str, object] | None) -> dict[str, dict[str, object]]:
    if cursor is None:
        return {}
    if not isinstance(cursor, Mapping):
        raise ValueError("cursor must be an object")
    raw_files: object = cursor.get("files", {})
    if not isinstance(raw_files, Mapping):
        raise ValueError("cursor files must be an object")
    result: dict[str, dict[str, object]] = {}
    for raw_path, raw_summary in raw_files.items():
        path = _validate_relative_path(raw_path)
        if not isinstance(raw_summary, Mapping):
            raise ValueError("cursor file summary must be an object")
        summary = dict(raw_summary)
        revision_id = summary.get("revision_id")
        if not isinstance(revision_id, str) or not revision_id:
            raise ValueError("cursor file summary revision_id is invalid")
        result[path] = {
            "mtime_ns": _integer(summary.get("mtime_ns"), name="mtime_ns"),
            "size": _integer(summary.get("size"), name="size"),
            "revision_id": revision_id,
        }
    return result


def _integer(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"cursor {name} is invalid")
    return value


def _validate_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("cursor path is invalid")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("cursor path is invalid")
    if any(":" in part for part in path.parts):
        raise ValueError("cursor path is invalid")
    normalized = path.as_posix()
    if normalized != value:
        raise ValueError("cursor path is invalid")
    return normalized


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


def _read_checked(path: Path, *, extension: str, collect: bool) -> bytes | str:
    """Read or hash one bounded file while checking its metadata before/after."""

    try:
        before = path.stat()
        maximum = _max_size(extension)
        if before.st_size > maximum:
            raise ValueError("file exceeds the provider size limit")

        digest = hashlib.sha256()
        chunks: list[bytes] | None = [] if collect else None
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
                digest.update(chunk)
                if chunks is not None:
                    chunks.append(chunk)
        after = path.stat()
        if after.st_mtime_ns != before.st_mtime_ns or after.st_size != before.st_size or total != before.st_size:
            raise ValueError("file changed while it was being read")
        if chunks is None:
            return digest.hexdigest()
        return b"".join(chunks)
    except asyncio.CancelledError:
        raise
    except BaseError:
        raise
    except Exception as exc:
        raise _file_error("local file read failed", exc) from None


def _fingerprint_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    revision_id = _read_checked(
        Path(str(candidate["path"])),
        extension=str(candidate["extension"]),
        collect=False,
    )
    return {**candidate, "revision_id": str(revision_id)}


def _materialize_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(str(candidate["path"]))
    extension = str(candidate["extension"])
    try:
        raw_bytes = _read_checked(path, extension=extension, collect=True)
        if not isinstance(raw_bytes, bytes):
            raise TypeError("local file materialization did not return bytes")
        revision_id = _revision_id(raw_bytes)
        if revision_id != candidate["revision_id"]:
            raise ValueError("file changed between fingerprint and materialization")
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


def _summary(change: Mapping[str, object]) -> dict[str, object]:
    return {
        "mtime_ns": _integer(change["mtime_ns"], name="mtime_ns"),
        "size": _integer(change["size"], name="size"),
        "revision_id": str(change["revision_id"]),
    }


def _changes(
    root: Path,
    candidates: list[dict[str, Any]],
    current: dict[str, dict[str, Any]],
    previous: dict[str, dict[str, object]],
    *,
    initial: bool,
    max_items: int,
) -> list[dict[str, Any]]:
    del max_items
    new_or_changed: list[dict[str, Any]] = []
    historical: list[dict[str, Any]] = []
    latest_known_mtime = max(
        (_integer(summary["mtime_ns"], name="mtime_ns") for summary in previous.values()),
        default=None,
    )
    for candidate in candidates:
        path = str(candidate["relative_path"])
        previous_summary = previous.get(path)
        change = {**candidate, "operation": "upsert"}
        if previous_summary is not None and previous_summary.get("revision_id") != candidate["revision_id"]:
            new_or_changed.append(change)
        elif previous_summary is None:
            candidate_mtime = _integer(candidate["mtime_ns"], name="mtime_ns")
            if not initial and latest_known_mtime is not None and candidate_mtime > latest_known_mtime:
                new_or_changed.append(change)
            else:
                historical.append(change)

    for path, summary in previous.items():
        if path not in current:
            new_or_changed.append(
                {
                    "relative_path": path,
                    "path": root / Path(path),
                    "content": None,
                    "mtime_ns": _integer(summary["mtime_ns"], name="mtime_ns"),
                    "size": _integer(summary["size"], name="size"),
                    "revision_id": str(summary["revision_id"]),
                    "operation": "delete",
                }
            )

    def _sort_key(change: Mapping[str, object]) -> tuple[int, str, str]:
        relative_path = change.get("relative_path")
        return (
            -_integer(change.get("mtime_ns"), name="mtime_ns"),
            str(relative_path).casefold(),
            str(relative_path),
        )

    new_or_changed.sort(key=_sort_key)
    historical.sort(key=_sort_key)
    return [*new_or_changed, *historical]


def _logical_id(root: Path, relative_path: str) -> str:
    root_digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    return f"local_files:{root_digest}:{relative_path}"


def _change_item(root: Path, change: Mapping[str, object]) -> RawChangeItem:
    path = str(change["relative_path"])
    operation = str(change["operation"])
    absolute_path = root / Path(path) if operation == "delete" else Path(str(change["path"])).resolve()
    metadata = {
        "path": path,
        "root_dir": str(root),
        "size": _integer(change["size"], name="size"),
        "mtime_ns": _integer(change["mtime_ns"], name="mtime_ns"),
        "raw_snapshot_available": change.get("raw_snapshot") is not None,
        "content_truncated": bool(change.get("content_truncated", False)),
    }
    try:
        operation = cast(Literal["upsert", "delete"], operation)
        raw_snapshot = cast(str | bytes | None, change.get("raw_snapshot"))
        return RawChangeItem(
            logical_id=_logical_id(root, path),
            revision_id=str(change["revision_id"]),
            operation=operation,
            title=path,
            content=None if operation == "delete" else str(change["content"]),
            original_ref=str(absolute_path),
            metadata=metadata,
            raw_snapshot=None if operation == "delete" else raw_snapshot,
        )
    except asyncio.CancelledError:
        raise
    except BaseError:
        raise
    except Exception as exc:
        raise _file_error("local file change is invalid", exc) from None
