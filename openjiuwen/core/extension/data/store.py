# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Package-scoped JSON CRUD with stable interprocess locks."""

import json
from functools import partial
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.extension.data.files import replace_json, run_file_operation, sync_directory
from openjiuwen.core.extension.data.json_values import copy_json, encode_json, require_package_id

STORE_MAX_BYTES = 4 * 1024 * 1024


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise build_error(StatusCode.EXTENSION_DATA_READ_FAILED, reason="store JSON contains duplicate object keys")
        result[key] = value
    return result


class JsonStore:
    """Persist each package in a JSON file under an explicit host directory.

    Files are created lazily. All instances sharing a directory cooperate via
    stable sidecar locks; no cached data can hide another writer's commits.
    """

    def __init__(self, directory: Path) -> None:
        """Remember the directory without creating it or accessing package files."""
        self.directory = Path(directory).expanduser().resolve()

    async def get(self, package_id: str, key: str, default: Any = None) -> Any:
        """Read a copied value, returning default only when the key is absent."""
        self._validate(package_id, key)
        found, value = await run_file_operation(partial(self._operate, package_id, "get", key))
        return value if found else default

    async def set(self, package_id: str, key: str, value: Any) -> bool:
        """Persist one JSON value; False means the new data was not committed."""
        self._validate(package_id, key)
        copied = copy_json(value)
        self._check_size(encode_json({key: copied}))
        return await run_file_operation(partial(self._operate, package_id, "set", key, copied))

    async def delete(self, package_id: str, key: str) -> bool:
        """Delete a key, succeeding also when no such key exists."""
        self._validate(package_id, key)
        return await run_file_operation(partial(self._operate, package_id, "delete", key))

    async def keys(self, package_id: str) -> list[str]:
        """List keys in their insertion order."""
        require_package_id(package_id)
        return await run_file_operation(partial(self._operate, package_id, "keys"))

    async def cleanup_package(self, package_id: str, *, reason: str = "uninstall") -> None:
        """Delete package data on uninstall, preserving it on reload and shutdown."""
        require_package_id(package_id)
        if reason not in ("uninstall", "reload", "shutdown"):
            raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="unknown package cleanup reason")
        if reason == "uninstall":
            await run_file_operation(partial(self._operate, package_id, "cleanup"))

    @staticmethod
    def _validate(package_id: str, key: str) -> None:
        require_package_id(package_id)
        if not isinstance(key, str):
            raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="store key must be a string")

    @staticmethod
    def _check_size(data: bytes) -> None:
        if len(data) > STORE_MAX_BYTES:
            raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="package store exceeds 4 MiB")

    def _path(self, name: str) -> Path:
        path = self.directory / name
        if self.directory.resolve() != self.directory or path.is_symlink() or path.resolve().parent != self.directory:
            raise build_error(
                StatusCode.EXTENSION_DATA_PATH_INVALID, reason="store path is redirected outside its directory"
            )
        if path.exists() and not path.is_file():
            raise build_error(StatusCode.EXTENSION_DATA_PATH_INVALID, reason="store path is not a regular file")
        if path.exists() and path.resolve().name != name:
            raise build_error(StatusCode.EXTENSION_DATA_PATH_INVALID, reason="store filename aliases another package")
        return path

    def _operate(self, package_id: str, operation: str, key: str = "", value: Any = None) -> Any:
        writing = operation in ("set", "delete")
        completed = False
        try:
            path = self._path(f"{package_id}.json")
            # Empty-package reads/deletes must not create the store directory or lock.
            if operation in ("get", "keys", "delete", "cleanup") and not path.exists():
                if operation == "get":
                    return False, None
                if operation == "keys":
                    return []
                if operation == "delete":
                    return True
                return None
            self.directory.mkdir(parents=True, exist_ok=True)
            lock_path = self._path(f"{package_id}.json.lock")
            with FileLock(lock_path, timeout=30):
                if operation == "cleanup":
                    path.unlink(missing_ok=True)
                    sync_directory(path.parent)
                    return None
                result = self._locked_operation(path, operation, key, value)
                completed = True
            return result
        except (OSError, Timeout) as exc:
            if writing and completed:
                raise build_error(
                    StatusCode.EXTENSION_DATA_COMMIT_UNKNOWN,
                    cause=exc,
                    reason="file operation completed but releasing the store lock failed; reread before retrying",
                ) from exc
            if writing:
                logger.warning("extension store %s did not commit for package %s: %s", operation, package_id, exc)
                return False
            status = (
                StatusCode.EXTENSION_DATA_WRITE_FAILED
                if operation == "cleanup"
                else StatusCode.EXTENSION_DATA_READ_FAILED
            )
            raise build_error(status, cause=exc, reason=f"cannot {operation} package store {package_id}") from exc

    def _locked_operation(self, path: Path, operation: str, key: str, value: Any) -> Any:
        contents, previous = self._read(path)
        if operation == "get":
            return key in contents, contents.get(key)
        if operation == "keys":
            return list(contents)
        if operation == "set":
            contents[key] = value
        elif key not in contents:
            return True
        else:
            del contents[key]
        data = encode_json(contents)
        self._check_size(data)
        return replace_json(path, data, previous)

    @staticmethod
    def _read(path: Path) -> tuple[dict[str, Any], bytes | None]:
        try:
            with path.open("rb") as stream:
                data = stream.read(STORE_MAX_BYTES + 1)
        except FileNotFoundError:
            return {}, None
        except OSError as exc:
            raise build_error(
                StatusCode.EXTENSION_DATA_READ_FAILED, cause=exc, reason=f"cannot read {path.name}"
            ) from exc
        try:
            if len(data) > STORE_MAX_BYTES:
                raise build_error(StatusCode.EXTENSION_DATA_READ_FAILED, reason="saved package store exceeds 4 MiB")
            value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
            if not isinstance(value, dict):
                raise build_error(StatusCode.EXTENSION_DATA_READ_FAILED, reason="store JSON must be an object")
            return copy_json(value), data
        except (ValueError, UnicodeError, RecursionError, BaseError) as exc:
            raise build_error(
                StatusCode.EXTENSION_DATA_READ_FAILED, cause=exc, reason=f"invalid store JSON in {path.name}"
            ) from exc
