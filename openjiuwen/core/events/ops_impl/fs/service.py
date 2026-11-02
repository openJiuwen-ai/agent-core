# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Execute ``fs.*`` events on the session's ``SysOperation``."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.ops_impl.common import OpsError
from openjiuwen.core.events.ops_impl.fs.models import (
    FsAncestor,
    FsAncestorsInput,
    FsAncestorsOutput,
    FsEntry,
    FsExistsInput,
    FsExistsOutput,
    FsKind,
    FsListInput,
    FsListOutput,
    FsReadInput,
    FsReadOutput,
    FsStatInput,
    FsStatOutput,
    FsStatus,
    FsWriteInput,
    FsWriteOutput,
)
from openjiuwen.core.events.ops_impl.runtime import build_context, resolve_agent, resolve_sys_operation
from openjiuwen.core.events.types import SessionContext

_NO_OPERATION = OpsError(
    kind="sys_operation_unavailable",
    message="no SysOperation is available for this session",
)


class _LookupFailed(Exception):
    """A listing needed to resolve one path did not succeed."""

    def __init__(self, error: OpsError) -> None:
        super().__init__(error.message)
        self.error = error


class FsService:
    """Run one ``fs.*`` request against the caller's filesystem backend."""

    def __init__(self, operation: Any | None, context: SessionContext) -> None:
        """Hold the resolved backend and the session snapshot of one call."""

        self._operation = operation
        self._context = context

    @classmethod
    def from_ops(cls, ops: EventHandlerOps) -> FsService:
        """Resolve the session snapshot and the backend it may use."""

        context = build_context(ops)
        return cls(resolve_sys_operation(resolve_agent(context)), context)

    async def read(self, request: FsReadInput) -> FsReadOutput:
        """Read one file."""

        fs = self._fs()
        path = _absolute(request.path, self._context.cwd)
        if fs is None:
            return FsReadOutput(status=FsStatus.DENIED, path=path, error=_NO_OPERATION)
        options: dict[str, Any] = {
            "mode": request.mode,
            "encoding": request.encoding,
            "only_read": request.only_read,
        }
        for name in ("head", "tail", "line_range", "chunk_size"):
            value = getattr(request, name)
            if value is not None:
                options[name] = value
        result = await fs.read_file(path, **options)
        if not _ok(result):
            return FsReadOutput(
                status=FsStatus.ERROR,
                path=path,
                mode=request.mode,
                error=_error(result),
            )
        return FsReadOutput(
            status=FsStatus.OK,
            path=result.data.path,
            content=result.data.content,
            mode=result.data.mode,
        )

    async def write(self, request: FsWriteInput) -> FsWriteOutput:
        """Write or append to one file."""

        fs = self._fs()
        path = _absolute(request.path, self._context.cwd)
        if fs is None:
            return FsWriteOutput(status=FsStatus.DENIED, path=path, error=_NO_OPERATION)
        result = await fs.write_file(
            path,
            request.content,
            mode=request.mode,
            append=request.append,
            prepend_newline=request.prepend_newline,
            append_newline=request.append_newline,
            create_if_not_exist=request.create_if_not_exist,
            permissions=request.permissions,
            encoding=request.encoding,
        )
        if not _ok(result):
            return FsWriteOutput(
                status=FsStatus.ERROR,
                path=path,
                mode=request.mode,
                error=_error(result),
            )
        return FsWriteOutput(
            status=FsStatus.OK,
            path=result.data.path,
            size=result.data.size,
            mode=result.data.mode,
        )

    async def list(self, request: FsListInput) -> FsListOutput:
        """Enumerate one directory, optionally merging subdirectories."""

        fs = self._fs()
        path = _absolute(request.path, self._context.cwd)
        if fs is None:
            return FsListOutput(status=FsStatus.DENIED, root_path=path, error=_NO_OPERATION)
        options: dict[str, Any] = {
            "recursive": request.recursive,
            "sort_by": request.sort_by,
            "sort_descending": request.sort_descending,
        }
        if request.max_depth is not None:
            options["max_depth"] = request.max_depth
        entries: list[FsEntry] = []
        root_path = path
        failure: OpsError | None = None
        for call, keep, is_files in (
            (fs.list_files, request.include_files, True),
            (fs.list_directories, request.include_dirs, False),
        ):
            if not keep:
                continue
            kwargs = dict(options)
            if is_files and request.file_types is not None:
                kwargs["file_types"] = list(request.file_types)
            result = await call(path, **kwargs)
            if not _ok(result):
                failure = failure or _error(result)
                continue
            root_path = result.data.root_path or root_path
            entries.extend(_entry(item) for item in result.data.list_items or ())
        entries.sort(key=lambda entry: _sort_value(entry, request.sort_by), reverse=request.sort_descending)
        total = len(entries)
        truncated = request.max_entries is not None and total > request.max_entries
        if truncated:
            entries = entries[: request.max_entries]
        return FsListOutput(
            status=FsStatus.OK if failure is None else FsStatus.ERROR,
            root_path=root_path,
            entries=tuple(entries),
            total_count=total,
            truncated=truncated,
            error=failure,
        )

    async def stat(self, request: FsStatInput) -> FsStatOutput:
        """Report the metadata of one path."""

        fs = self._fs()
        path = _absolute(request.path, self._context.cwd)
        if fs is None:
            return FsStatOutput(status=FsStatus.DENIED, path=path, error=_NO_OPERATION)
        try:
            entry = await self._lookup(fs, path)
        except _LookupFailed as failure:
            return FsStatOutput(status=FsStatus.ERROR, path=path, error=failure.error)
        if entry is None:
            return FsStatOutput(status=FsStatus.OK, path=path)
        return FsStatOutput(
            status=FsStatus.OK,
            path=entry.path,
            found=True,
            kind=FsKind.DIRECTORY if entry.is_directory else FsKind.FILE,
            size=entry.size,
            modified_time=entry.modified_time or None,
        )

    async def exists(self, request: FsExistsInput) -> FsExistsOutput:
        """Report whether one path exists."""

        fs = self._fs()
        if fs is None:
            return FsExistsOutput(status=FsStatus.DENIED, error=_NO_OPERATION)
        try:
            entry = await self._lookup(fs, request.path)
        except _LookupFailed as failure:
            return FsExistsOutput(status=FsStatus.ERROR, error=failure.error)
        return FsExistsOutput(status=FsStatus.OK, exists=entry is not None)

    async def ancestors(self, request: FsAncestorsInput) -> FsAncestorsOutput:
        """Collect instruction files from the starting directory up to ``stop_at``."""

        fs = self._fs()
        if fs is None:
            return FsAncestorsOutput(status=FsStatus.DENIED, error=_NO_OPERATION)
        names = tuple(request.names)
        if not names:
            return FsAncestorsOutput(status=FsStatus.OK)
        wanted = {os.path.normcase(name) for name in names}
        cwd = self._context.cwd
        current = _absolute(request.start, cwd)
        stop_at = _absolute(request.stop_at, cwd) if request.stop_at else None
        entries: list[FsAncestor] = []
        truncated = False
        skipped = 0
        first = True
        while True:
            if stop_at is not None and not _within(current, stop_at):
                break
            result = await fs.list_files(current)
            if not _ok(result) and first:
                return FsAncestorsOutput(status=FsStatus.ERROR, error=_error(result))
            first = False
            if _ok(result):
                present = {
                    os.path.normcase(item.name): item
                    for item in result.data.list_items or ()
                    if os.path.normcase(item.name) in wanted
                }
                for name in names:
                    item = present.get(os.path.normcase(name))
                    if item is None:
                        continue
                    if len(entries) >= request.max_files:
                        truncated = True
                        break
                    content = await self._read_text(fs, item.path, request)
                    if content is None:
                        skipped += 1
                        continue
                    content, cut = _truncate_bytes(content, request.max_bytes_per_file, request.encoding)
                    truncated = truncated or cut
                    entries.append(FsAncestor(name=name, path=item.path, content=content))
            else:
                skipped += 1
            if len(entries) >= request.max_files:
                break
            if stop_at is not None and _same_path(current, stop_at):
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        entries.reverse()
        return FsAncestorsOutput(
            status=FsStatus.OK,
            entries=tuple(entries),
            truncated=truncated,
            skipped=skipped,
        )

    @staticmethod
    async def _read_text(fs: Any, path: str, request: FsAncestorsInput) -> str | None:
        """Read one instruction file, or ``None`` when it cannot be read."""

        result = await fs.read_file(path, encoding=request.encoding, only_read=True)
        if not _ok(result):
            return None
        content = result.data.content
        return content if isinstance(content, str) else None

    async def _lookup(self, fs: Any, path: str) -> FsEntry | None:
        """Find one path in its parent listing; raise when the listing fails."""

        target = _absolute(path, self._context.cwd)
        parent, name = os.path.split(target)
        if not name:
            result = await fs.list_directories(target)
            if not _ok(result):
                raise _LookupFailed(_error(result))
            return FsEntry(name=target, path=target, is_directory=True)
        listed = await asyncio.gather(
            fs.list_directories(parent),
            fs.list_files(parent),
            return_exceptions=True,
        )
        failure: OpsError | None = None
        for result in listed:
            if isinstance(result, BaseException):
                failure = failure or OpsError(kind="fs_error", message=str(result) or type(result).__name__)
                continue
            if not _ok(result):
                failure = failure or _error(result)
                continue
            for item in result.data.list_items or ():
                if os.path.normcase(item.name) == os.path.normcase(name):
                    return _entry(item)
        if failure is not None:
            raise _LookupFailed(failure)
        return None

    def _fs(self) -> Any | None:
        """Return the filesystem view of the resolved backend."""

        operation = self._operation
        return None if operation is None else operation.fs()


def _truncate_bytes(content: str, limit: int, encoding: str) -> tuple[str, bool]:
    """Trim one text to ``limit`` encoded bytes without returning a split character."""

    raw = content.encode(encoding, errors="ignore")
    if len(raw) <= limit:
        return content, False
    return raw[:limit].decode(encoding, errors="ignore"), True


def _same_path(left: str, right: str) -> bool:
    """Compare two normalized paths the way the platform does."""

    return os.path.normcase(left) == os.path.normcase(right)


def _within(path: str, root: str) -> bool:
    """Return whether one path equals or sits below a root."""

    path = os.path.normcase(path)
    root = os.path.normcase(root).rstrip(os.sep)
    return path == root or path.startswith(root + os.sep)


def _absolute(path: str | None, cwd: str | None) -> str:
    """Resolve one path against the session cwd before it reaches the backend."""

    if not path:
        return os.path.normpath(cwd or os.getcwd())
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(cwd or os.getcwd(), path))


def _sort_value(entry: FsEntry, sort_by: str) -> object:
    """Return the sort key of one merged entry."""

    if sort_by == "size":
        return entry.size
    if sort_by == "modified_time":
        return entry.modified_time
    return entry.name


def _ok(result: Any) -> bool:
    """Return whether one backend result reports success."""

    return getattr(result, "code", None) == StatusCode.SUCCESS.code


def _error(result: Any) -> OpsError:
    """Map one failed backend result onto an ops error."""

    message = str(getattr(result, "message", "") or "filesystem operation failed")
    return OpsError(kind="fs_error", message=message)


def _entry(item: Any) -> FsEntry:
    """Copy one backend listing item into the event's own value object."""

    return FsEntry(
        name=str(item.name),
        path=str(item.path),
        is_directory=bool(item.is_directory),
        size=int(item.size or 0),
        modified_time=str(item.modified_time or ""),
        suffix=getattr(item, "type", None),
    )
