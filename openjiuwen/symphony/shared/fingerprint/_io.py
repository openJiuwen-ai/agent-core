# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Low-level JSON I/O used by versioned Symphony artifacts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, NoReturn

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error, raise_error


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Durably replace *path* with strict JSON from the same directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        temporary_prefix = f".{path.name}."
        descriptor, temporary_name = tempfile.mkstemp(
            suffix=".tmp", prefix=temporary_prefix, dir=path.parent, text=True
        )
        temporary_path = Path(temporary_name)
        stream = os.fdopen(descriptor, mode="w", encoding="utf-8")
        descriptor = None  # Ownership is transferred to the text stream.
        with stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        _fsync_directory(path.parent)
    finally:
        try:
            if descriptor is not None:
                os.close(descriptor)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object without accepting non-object top-level payloads."""

    with path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream, parse_constant=_reject_non_finite)
    if not isinstance(payload, dict):
        raise_error(StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID, reason="JSON payload must be an object")
    return payload


def _reject_non_finite(value: str) -> NoReturn:
    raise build_error(
        StatusCode.COMPONENT_SYMPHONY_SCHEMA_INVALID,
        reason=f"non-finite JSON number is not supported: {value}",
    )


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        directory_fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        # Some platforms do not support fsync on directory handles. The file
        # itself has already been flushed and atomically replaced.
        pass
    finally:
        os.close(directory_fd)


__all__ = ["atomic_write_json", "read_json"]
