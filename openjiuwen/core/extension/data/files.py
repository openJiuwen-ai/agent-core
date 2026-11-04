# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Cancellation-safe worker execution and atomic JSON file replacement."""

import asyncio
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from portalocker import Lock

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger

_T = TypeVar("_T")


def resolve_data_path(path: Path) -> Path:
    """Resolve paths consistently when Windows returns an extended-length prefix."""
    resolved = path.resolve()
    value = str(resolved)
    if os.name == "nt":
        if value.startswith("\\\\?\\UNC\\"):
            return Path(value.replace("\\\\?\\UNC\\", "\\\\", 1))
        if value.startswith("\\\\?\\"):
            return Path(value.replace("\\\\?\\", "", 1))
    return resolved


def package_data_path(directory: Path, name: str) -> Path:
    """Require an unredirected regular package file inside its canonical directory."""
    path = directory / name
    resolved = resolve_data_path(path)
    if resolve_data_path(directory) != directory or path.is_symlink() or resolved.parent != directory:
        raise build_error(StatusCode.EXTENSION_DATA_PATH_INVALID, reason="package data path leaves its directory")
    if path.exists() and (not path.is_file() or resolved.name != name):
        raise build_error(StatusCode.EXTENSION_DATA_PATH_INVALID, reason="package data path aliases another file")
    return path


def package_file_lock(path: Path, *, timeout: float = 30) -> Lock:
    """Lock a stable sidecar file without unlinking it on release on any platform."""
    return Lock(path, mode="a+b", timeout=timeout)


async def run_file_operation(operation: Callable[[], _T]) -> _T:
    """Keep the worker and its file lock alive until I/O has actually stopped."""
    worker = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError as error:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            worker.result()
        except Exception:
            logger.exception("extension store worker failed while its caller was cancelled")
        raise error


def replace_json(path: Path, data: bytes, previous: bytes | None) -> bool:
    """Flush a temporary file and replace the target, reconciling failed commits."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(temporary, path)
        except OSError as exc:
            return _reconcile_replace(path, data, previous, exc)
        sync_directory(path.parent)
        return True
    except OSError:
        logger.exception("extension store write stopped before committing package file %s", path.name)
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.exception("could not remove extension store temporary file %s", temporary.name)


def sync_directory(directory: Path) -> None:
    """Flush directory metadata where the platform supports directory fsync."""
    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0), 0o644)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise build_error(
            StatusCode.EXTENSION_DATA_COMMIT_UNKNOWN,
            cause=exc,
            reason="file content changed but directory metadata could not be flushed",
        ) from exc


def _reconcile_replace(path: Path, data: bytes, previous: bytes | None, failure: OSError) -> bool:
    try:
        current = path.read_bytes() if path.exists() else None
    except OSError as exc:
        raise build_error(
            StatusCode.EXTENSION_DATA_COMMIT_UNKNOWN,
            cause=exc,
            reason=f"cannot verify replacement of {path.name}",
        ) from exc
    if current == data:
        sync_directory(path.parent)
        return True
    if current == previous:
        logger.warning("extension store replace did not commit %s: %s", path.name, failure)
        return False
    raise build_error(
        StatusCode.EXTENSION_DATA_COMMIT_UNKNOWN,
        cause=failure,
        reason=f"replacement of {path.name} left unexpected content; reread before retrying",
    )
