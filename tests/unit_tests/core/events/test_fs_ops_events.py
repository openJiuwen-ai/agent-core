# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``fs.*`` events and their ``ops.fs`` capabilities."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.ops import EventHandlerOps, EventHandlerOpsBuilder
from openjiuwen.core.events.ops_impl.fs.models import (
    FsAncestorsInput,
    FsExistsInput,
    FsListInput,
    FsReadInput,
    FsStatInput,
    FsStatus,
    FsWriteInput,
)
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE, Event, SessionContext
from openjiuwen.core.single_agent.agent_runtime import get_agent_runtime
from openjiuwen.core.sys_operation.result.fs_operation_result import (
    FileSystemData,
    FileSystemItem,
    ListDirsResult,
    ListFilesResult,
    ReadFileData,
    ReadFileResult,
    WriteFileData,
    WriteFileResult,
)

pytestmark = pytest.mark.asyncio


_BOUND: list[tuple[str, object]] = []


@pytest.fixture(autouse=True)
def _reset_agent_runtime():
    """Keep the agent runtime isolated between tests."""

    _BOUND.clear()
    yield
    runtime = get_agent_runtime()
    for session_id, agent in _BOUND:
        runtime.unbind(session_id, agent)
    _BOUND.clear()


class _FakeFs:
    """Filesystem backend over a real directory, returning real result models."""

    async def read_file(self, path: str, **kwargs) -> ReadFileResult:
        target = Path(path)
        try:
            content = await asyncio.to_thread(_read_text, path, kwargs.get("encoding") or "utf-8")
        except OSError as error:
            return _failure(ReadFileResult, str(error))
        return ReadFileResult(
            code=0,
            message="success",
            data=ReadFileData(path=str(target), content=content, mode="text"),
        )

    async def write_file(self, path: str, content, **kwargs) -> WriteFileResult:
        target = Path(path)
        try:
            await asyncio.to_thread(_write_text, path, content, kwargs.get("encoding") or "utf-8")
        except OSError as error:
            return _failure(WriteFileResult, str(error))
        return WriteFileResult(
            code=0,
            message="success",
            data=WriteFileData(path=str(target), size=len(content), mode="text"),
        )

    async def list_files(self, path: str, **kwargs) -> ListFilesResult:
        return _listing(ListFilesResult, path, keep_directory=False)

    async def list_directories(self, path: str, **kwargs) -> ListDirsResult:
        return _listing(ListDirsResult, path, keep_directory=True)


class _FakeSysOperation:
    """Minimal ``SysOperation`` exposing a LOCAL filesystem backend."""

    def __init__(self, sys_operation_id: str = "sys-op", fs: _FakeFs | None = None) -> None:
        self.id = sys_operation_id
        self._fs = fs or _FakeFs()

    def fs(self) -> _FakeFs:
        return self._fs


class _DeepConfig:
    """Minimal ``DeepAgentConfig`` carrying the filesystem backend."""

    def __init__(self, operation: _FakeSysOperation | None, cwd: str) -> None:
        self.sys_operation = operation
        self.cwd = cwd
        self.project_root = cwd
        self.workspace = None


class _Agent:
    def __init__(self, operation: _FakeSysOperation | None, cwd: str) -> None:
        self.deep_config = _DeepConfig(operation, cwd)


def _bind_agent(session: SessionContext, operation: _FakeSysOperation | None) -> None:
    agent = _Agent(operation, session.cwd or "")
    get_agent_runtime().bind(session.session_id, agent)
    _BOUND.append((session.session_id, agent))


def _read_text(path: str, encoding: str) -> str:
    return Path(path).read_text(encoding=encoding)


def _write_text(path: str, content: str, encoding: str) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding=encoding)
    return len(content)


def _failure(result_type, message: str):
    return result_type(code=-1, message=message, data=None)


def _listing(result_type, path: str, *, keep_directory: bool):
    root = Path(path)
    if not root.is_dir():
        return _failure(result_type, f"not a directory: {path}")
    items = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir() != keep_directory:
            continue
        items.append(
            FileSystemItem(
                name=entry.name,
                path=str(entry),
                size=entry.stat().st_size if entry.is_file() else 0,
                modified_time="",
                is_directory=entry.is_dir(),
                type=None if entry.is_dir() else entry.suffix,
            )
        )
    return result_type(
        code=0,
        message="success",
        data=FileSystemData(
            total_count=len(items),
            list_items=items,
            root_path=str(root),
            recursive=False,
        ),
    )


async def _ready_ops(bus: ExtensionEventBus, session: SessionContext) -> EventHandlerOps:
    builder = await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    assert isinstance(builder, EventHandlerOpsBuilder)
    return builder.build(None, session)


def _bound_bus(tmp_path: Path, operation: _FakeSysOperation | None = None) -> tuple[ExtensionEventBus, SessionContext]:
    bus = ExtensionEventBus()
    session = SessionContext("session-fs", cwd=str(tmp_path))
    _bind_agent(session, operation)
    return bus, session


async def test_ops_build_exposes_fs_namespace() -> None:
    bus = ExtensionEventBus()
    ops = await _ready_ops(bus, SessionContext("session-fs"))

    for name in ("read", "write", "list", "stat", "exists", "ancestors"):
        assert callable(getattr(ops.fs, name))


async def test_read_returns_file_content(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    bus, session = _bound_bus(tmp_path, _FakeSysOperation())
    ops = await _ready_ops(bus, session)

    output = await ops.fs.read(FsReadInput(path=str(tmp_path / "a.txt")))

    assert output.status is FsStatus.OK
    assert output.content == "hello"


async def test_read_is_denied_without_backend(tmp_path: Path) -> None:
    bus, session = _bound_bus(tmp_path)
    ops = await _ready_ops(bus, session)

    output = await ops.fs.read(FsReadInput(path=str(tmp_path / "missing.txt")))

    assert output.status is FsStatus.DENIED
    assert output.error is not None


async def test_write_then_stat_and_exists(tmp_path: Path) -> None:
    bus, session = _bound_bus(tmp_path, _FakeSysOperation())
    ops = await _ready_ops(bus, session)
    target = tmp_path / "nested" / "b.txt"

    written = await ops.fs.write(FsWriteInput(path=str(target), content="abc"))
    stats = await ops.fs.stat(FsStatInput(path=str(target)))
    present = await ops.fs.exists(FsExistsInput(path=str(target)))
    absent = await ops.fs.exists(FsExistsInput(path=str(tmp_path / "nope.txt")))

    assert written.status is FsStatus.OK
    assert written.size == 3
    assert stats.found is True
    assert stats.kind == "file"
    assert stats.size == 3
    assert present.exists is True
    assert absent.exists is False


async def test_list_merges_files_and_directories(tmp_path: Path) -> None:
    (tmp_path / "one.txt").write_text("1", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    bus, session = _bound_bus(tmp_path, _FakeSysOperation())
    ops = await _ready_ops(bus, session)

    listed = await ops.fs.list(FsListInput(path=str(tmp_path), include_dirs=True))

    names = {entry.name for entry in listed.entries}
    assert names == {"one.txt", "sub"}
    assert listed.total_count == 2
    assert listed.truncated is False


async def test_list_truncates_at_max_entries(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"f{index}.txt").write_text("x", encoding="utf-8")
    bus, session = _bound_bus(tmp_path, _FakeSysOperation())
    ops = await _ready_ops(bus, session)

    listed = await ops.fs.list(FsListInput(path=str(tmp_path), max_entries=2))

    assert len(listed.entries) == 2
    assert listed.total_count == 3
    assert listed.truncated is True


async def test_stat_reports_missing_path(tmp_path: Path) -> None:
    bus, session = _bound_bus(tmp_path, _FakeSysOperation())
    ops = await _ready_ops(bus, session)

    stats = await ops.fs.stat(FsStatInput(path=str(tmp_path / "missing.txt")))

    assert stats.status is FsStatus.OK
    assert stats.found is False
    assert stats.kind is None


class _DeniedFs(_FakeFs):
    """Backend that refuses every listing, as the sandbox does for outside paths."""

    async def list_files(self, path: str, **kwargs) -> ListFilesResult:
        return _failure(ListFilesResult, f"Access denied: Path {path} outside sandbox")

    async def list_directories(self, path: str, **kwargs) -> ListDirsResult:
        return _failure(ListDirsResult, f"Access denied: Path {path} outside sandbox")


async def test_stat_and_exists_report_listing_failure(tmp_path: Path) -> None:
    bus, session = _bound_bus(tmp_path, _FakeSysOperation(fs=_DeniedFs()))
    ops = await _ready_ops(bus, session)
    target = str(tmp_path / "a.txt")

    stats = await ops.fs.stat(FsStatInput(path=target))
    present = await ops.fs.exists(FsExistsInput(path=target))

    assert stats.status is FsStatus.ERROR
    assert stats.found is False
    assert stats.error is not None and "Access denied" in stats.error.message
    assert present.status is FsStatus.ERROR
    assert present.exists is False
    assert present.error is not None and "Access denied" in present.error.message


async def test_ancestors_orders_outermost_first(tmp_path: Path) -> None:
    (tmp_path / "claude.md").write_text("root", encoding="utf-8")
    middle = tmp_path / "mid"
    middle.mkdir()
    (middle / "claude.md").write_text("middle", encoding="utf-8")
    deepest = middle / "deep"
    deepest.mkdir()
    bus, session = _bound_bus(tmp_path, _FakeSysOperation())
    ops = await _ready_ops(bus, session)

    output = await ops.fs.ancestors(
        FsAncestorsInput(names=("claude.md",), start=str(deepest), stop_at=str(tmp_path))
    )

    assert [entry.content for entry in output.entries] == ["root", "middle"]
    assert output.truncated is False


async def test_ancestors_limits_content_by_bytes(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("你好世界", encoding="utf-8")
    bus, session = _bound_bus(tmp_path, _FakeSysOperation())
    ops = await _ready_ops(bus, session)

    output = await ops.fs.ancestors(
        FsAncestorsInput(
            names=("AGENTS.md",),
            start=str(tmp_path),
            stop_at=str(tmp_path),
            max_bytes_per_file=4,
        )
    )

    assert output.truncated is True
    assert output.entries[0].content == "你"


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
async def test_ancestors_matches_names_case_on_windows(tmp_path: Path) -> None:
    (tmp_path / "agents.md").write_text("lower", encoding="utf-8")
    bus, session = _bound_bus(tmp_path, _FakeSysOperation())
    ops = await _ready_ops(bus, session)

    output = await ops.fs.ancestors(
        FsAncestorsInput(names=("AGENTS.md",), start=str(tmp_path), stop_at=str(tmp_path))
    )

    assert [entry.content for entry in output.entries] == ["lower"]


async def test_ancestors_stays_inside_stop_boundary(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("outside", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    bus, session = _bound_bus(tmp_path, _FakeSysOperation())
    ops = await _ready_ops(bus, session)

    output = await ops.fs.ancestors(
        FsAncestorsInput(names=("AGENTS.md",), start=str(tmp_path), stop_at=str(project))
    )

    assert output.status is FsStatus.OK
    assert output.entries == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
async def test_lookup_matches_path_case_on_windows(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    bus, session = _bound_bus(tmp_path, _FakeSysOperation())
    ops = await _ready_ops(bus, session)

    exists = await ops.fs.exists(FsExistsInput(path=str(tmp_path / "readme.md")))
    stat = await ops.fs.stat(FsStatInput(path=str(tmp_path / "readme.md")))

    assert exists.exists is True
    assert stat.found is True


class _SlowFs(_FakeFs):
    """Backend that answers after a fixed delay."""

    async def read_file(self, path: str, **kwargs) -> ReadFileResult:
        await asyncio.sleep(0.2)
        return await super().read_file(path, **kwargs)



class _PartialListFs(_FakeFs):
    """Return a failed file listing while directory listing still works."""

    async def list_files(self, path: str, **kwargs) -> ListFilesResult:
        return _failure(ListFilesResult, f"cannot list files: {path}")


async def test_read_times_out_after_its_budget(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    bus, session = _bound_bus(tmp_path, _FakeSysOperation(fs=_SlowFs()))
    ops = await _ready_ops(bus, session)

    output = await ops.fs.read(FsReadInput(path=str(tmp_path / "a.txt"), timeout_ms=50))

    assert output.status is FsStatus.TIMEOUT
    assert output.error is not None and output.error.kind == "timeout"


async def test_direct_trigger_uses_the_core_timeout(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    bus, session = _bound_bus(tmp_path, _FakeSysOperation(fs=_SlowFs()))
    await _ready_ops(bus, session)

    output = await bus.trigger(Event("fs.read", FsReadInput(path=str(tmp_path / "a.txt"), timeout_ms=50)), session)

    assert output.status is FsStatus.TIMEOUT
    assert output.error is not None and output.error.kind == "timeout"


async def test_invalid_input_is_rejected_before_dispatch(tmp_path: Path) -> None:
    bus, session = _bound_bus(tmp_path, _FakeSysOperation())
    ops = await _ready_ops(bus, session)

    requests = (
        lambda: ops.fs.read(FsReadInput(path="")),
        lambda: ops.fs.read(FsReadInput(path=str(tmp_path), head=1, tail=1)),
        lambda: ops.fs.read(FsReadInput(path=str(tmp_path), mode="bytes", head=1)),
        lambda: ops.fs.read(FsReadInput(path=str(tmp_path), line_range=(0, 2))),
        lambda: ops.fs.read(FsReadInput(path=str(tmp_path), line_range=(3, 2))),
        lambda: ops.fs.list(FsListInput(path=str(tmp_path), timeout_ms=0)),
        lambda: ops.fs.list(FsListInput(path=str(tmp_path), sort_by="unknown")),
        lambda: ops.fs.list(FsListInput(path=str(tmp_path), max_depth=0)),
        lambda: ops.fs.ancestors(FsAncestorsInput(names=())),
    )

    for request in requests:
        with pytest.raises(ValidationError):
            await request()


async def test_list_reports_partial_failure_and_keeps_entries(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    bus, session = _bound_bus(tmp_path, _FakeSysOperation(fs=_PartialListFs()))
    ops = await _ready_ops(bus, session)

    output = await ops.fs.list(FsListInput(path=str(tmp_path), include_dirs=True))

    assert output.status is FsStatus.ERROR
    assert output.error is not None and output.error.kind == "fs_error"
    assert [entry.name for entry in output.entries] == ["sub"]


async def test_ancestors_fails_when_start_directory_is_unreadable(tmp_path: Path) -> None:
    bus, session = _bound_bus(tmp_path, _FakeSysOperation())
    ops = await _ready_ops(bus, session)

    output = await ops.fs.ancestors(
        FsAncestorsInput(names=("AGENTS.md",), start=str(tmp_path / "missing"))
    )

    assert output.status is FsStatus.ERROR
    assert output.error is not None and output.error.kind == "fs_error"


async def test_reentrant_call_is_denied(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    bus, session = _bound_bus(tmp_path, _FakeSysOperation())
    ops = await _ready_ops(bus, session)
    nested: list[FsStatus] = []

    async def handler(event: Event, handler_ops: EventHandlerOps, event_next) -> object:
        inner = await handler_ops.fs.read(FsReadInput(path=str(tmp_path / "a.txt")))
        nested.append(inner.status)
        return inner

    bus.register_event_handler("fs.read", handler, {})
    output = await ops.fs.read(FsReadInput(path=str(tmp_path / "a.txt")))

    assert nested == [FsStatus.DENIED]
    assert output.status is FsStatus.DENIED
    assert output.error is not None and output.error.kind == "reentrant_call"



async def test_list_skips_directories_by_default(tmp_path: Path) -> None:
    (tmp_path / "one.txt").write_text("1", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    bus, session = _bound_bus(tmp_path, _FakeSysOperation())
    ops = await _ready_ops(bus, session)

    listed = await ops.fs.list(FsListInput(path=str(tmp_path)))

    assert [entry.name for entry in listed.entries] == ["one.txt"]


class _FailAfterFirstListFs(_FakeFs):
    """Answer the first listing, then report the parent as unreadable."""

    def __init__(self) -> None:
        self.calls = 0

    async def list_files(self, path: str, **kwargs) -> ListFilesResult:
        self.calls += 1
        if self.calls > 1:
            return _failure(ListFilesResult, f"cannot list files: {path}")
        return await super().list_files(path, **kwargs)


async def test_ancestors_counts_skipped_layers(tmp_path: Path) -> None:
    start = tmp_path / "deep"
    start.mkdir()
    (start / "AGENTS.md").write_text("deep", encoding="utf-8")
    bus, session = _bound_bus(tmp_path, _FakeSysOperation(fs=_FailAfterFirstListFs()))
    ops = await _ready_ops(bus, session)

    output = await ops.fs.ancestors(
        FsAncestorsInput(names=("AGENTS.md",), start=str(start), stop_at=str(tmp_path))
    )

    assert [entry.content for entry in output.entries] == ["deep"]
    assert output.skipped == 1


async def test_read_timeout_keeps_the_requested_mode(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"hello")
    bus, session = _bound_bus(tmp_path, _FakeSysOperation(fs=_SlowFs()))
    ops = await _ready_ops(bus, session)

    output = await ops.fs.read(FsReadInput(path=str(tmp_path / "a.bin"), mode="bytes", timeout_ms=50))

    assert output.status is FsStatus.TIMEOUT
    assert output.mode == "bytes"


async def test_working_dir_restores_original_cwd(tmp_path: Path) -> None:
    from openjiuwen.core.events.ops_impl.runtime import agent_working_dir
    from openjiuwen.core.sys_operation import cwd

    main = tmp_path / "main"
    work = tmp_path / "work"
    main.mkdir()
    work.mkdir()
    cwd.init_cwd(str(main))
    cwd.set_cwd(str(work))
    try:
        with agent_working_dir(_Agent(None, str(work)), SessionContext("cwd", cwd=str(work))):
            assert cwd.get_cwd() == str(work.resolve())
        assert cwd.get_cwd() == str(work.resolve())
        assert cwd.get_original_cwd() == str(main.resolve())
    finally:
        cwd.init_cwd(os.getcwd())
