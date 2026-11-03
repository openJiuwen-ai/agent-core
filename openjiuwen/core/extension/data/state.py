# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Durable package state with per-address CAS and interprocess file locking."""

import json
import re
import shutil
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any

from portalocker.exceptions import LockException

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.extension.data.dependencies import StateChanges
from openjiuwen.core.extension.data.files import (
    package_data_path, package_file_lock, replace_json, resolve_data_path, run_file_operation, sync_directory,
)
from openjiuwen.core.extension.data.json_values import copy_json, encode_json, require_package_id, valid_package_id
from openjiuwen.core.extension.data.state_models import (
    StateAddress,
    StateSetResult,
    StateSnapshot,
    require_address,
    require_version,
)

STATE_MAX_BYTES = 4 * 1024 * 1024
_SESSION_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}")
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("state JSON contains duplicate object keys")
        result[key] = value
    return result


def _same_value(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(right, sort_keys=True, ensure_ascii=False)


class _UnknownCommit(Exception):
    """The file was replaced, then releasing its lock failed."""

    def __init__(self, address: StateAddress, version: int) -> None:
        self.address = address
        self.version = version


class JsonState:
    """Persist values and versions under an explicit host-owned directory.

    File creation is lazy. Separate instances and processes sharing a directory
    cooperate through stable sidecar locks; reads never rely on a stale cache.
    """

    def __init__(self, directory: Path, *, max_bytes: int = STATE_MAX_BYTES) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="state max_bytes must be positive")
        self.directory = resolve_data_path(Path(directory).expanduser())
        self.max_bytes = max_bytes
        self.changes = StateChanges()

    async def get(self, address: StateAddress) -> StateSnapshot:
        """Read one durable snapshot, preserving absence tombstone versions."""
        require_address(address)
        return await run_file_operation(partial(self._operate, address, "get"))

    async def set(
        self, address: StateAddress, value: Any, *, expected_version: int | None = None,
    ) -> StateSetResult:
        """Compare, update and commit under the lock of one state file."""
        require_address(address)
        require_version(expected_version)
        copied = copy_json(value)
        self._check_size(encode_json(copied))
        try:
            result = await run_file_operation(partial(self._operate, address, "set", copied, expected_version))
        except _UnknownCommit as exc:
            self.changes.publish(exc.address, exc.version)
            raise build_error(
                StatusCode.EXTENSION_DATA_COMMIT_UNKNOWN,
                cause=exc.__cause__,
                reason="state operation completed but lock release failed; reread before retrying",
            ) from exc
        if result.changed:
            self.changes.publish(address, result.version)
        return result

    async def changed_since(self, reads: Mapping[StateAddress, int]) -> bool:
        """Check host-observed versions, including commits by another process."""
        for address, version in reads.items():
            if (await self.get(address)).version != version:
                return True
        return False

    async def clear_session(self, session_id: str, *, package_id: str | None = None) -> None:
        """Explicitly clear durable session values while advancing their versions."""
        if not isinstance(session_id, str) or not session_id.strip():
            raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="session_id must be non-empty")
        if package_id is not None:
            require_package_id(package_id)
        packages = (package_id,) if package_id is not None else await run_file_operation(self._packages)
        for package in packages:
            require_package_id(package)
            address = StateAddress(package, "", "session", session_id)
            changed = await run_file_operation(partial(self._operate, address, "clear"))
            for key, version in changed.items():
                self.changes.publish(StateAddress(package, key, "session", session_id), version)

    async def cleanup_package(self, package_id: str, *, reason: str = "uninstall") -> None:
        """Delete durable data only for explicit package uninstall."""
        require_package_id(package_id)
        if reason not in ("uninstall", "reload", "shutdown"):
            raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="unknown package cleanup reason")
        if reason == "uninstall":
            address = StateAddress(package_id, "", "application")
            await run_file_operation(partial(self._operate, address, "cleanup"))
            self.changes.drop_package(package_id)

    def file_stamp(self, address: StateAddress) -> tuple[int, int] | None:
        """Return one state file's mtime and size without reading or creating it."""
        require_address(address)
        _require_session_name(address.session_id, address.scope)
        if not self.directory.is_dir():
            return None
        package_dir = self.directory / address.package
        if not package_dir.is_dir():
            return None
        if address.scope == "session" and not (package_dir / "sessions").is_dir():
            return None
        try:
            stat = self._data_file(package_dir, address).stat()
        except FileNotFoundError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _packages(self) -> tuple[str, ...]:
        if not self.directory.is_dir():
            return ()
        return tuple(sorted(
            path.name for path in self.directory.iterdir() if path.is_dir() and valid_package_id(path.name)
        ))

    def _root_gate(self) -> Path:
        return self.directory.parent / f".{self.directory.name}.lock"

    def _package_gate(self, package_id: str) -> Path:
        require_package_id(package_id)
        return package_data_path(self.directory, f".{package_id}.lock")

    def _package_dir(self, package_id: str) -> Path:
        require_package_id(package_id)
        path = self.directory / package_id
        resolved = resolve_data_path(path)
        if resolve_data_path(self.directory) != self.directory or path.is_symlink() or resolved.parent != self.directory:
            raise build_error(StatusCode.EXTENSION_DATA_PATH_INVALID, reason="state package path leaves its directory")
        if resolved.exists() and not resolved.is_dir():
            raise build_error(StatusCode.EXTENSION_DATA_PATH_INVALID, reason="state package path is not a directory")
        return resolved

    def _data_file(self, package_dir: Path, address: StateAddress) -> Path:
        if address.scope == "application":
            return package_data_path(package_dir, "application.json")
        _require_session_name(address.session_id, address.scope)
        return package_data_path(resolve_data_path(package_dir / "sessions"), f"{address.session_id}.json")

    @staticmethod
    def _absent(operation: str) -> Any:
        if operation == "get":
            return StateSnapshot(False)
        if operation == "clear":
            return {}
        return None

    def _operate(
        self,
        address: StateAddress,
        operation: str,
        value: Any = None,
        expected_version: int | None = None,
    ) -> Any:
        try:
            if operation == "cleanup":
                return self._remove_package(address.package)
            _require_session_name(address.session_id, address.scope)
            if not self.directory.exists():
                parent = self.directory.parent
                if not parent.is_dir():
                    if operation != "set":
                        return self._absent(operation)
                    parent.mkdir(parents=True, exist_ok=True)
                with package_file_lock(self._root_gate()):
                    if not self.directory.exists():
                        if operation != "set":
                            return self._absent(operation)
                        self.directory.mkdir(parents=True, exist_ok=True)
                    return self._with_package_gate(address, operation, value, expected_version)
            return self._with_package_gate(address, operation, value, expected_version)
        except _UnknownCommit:
            raise
        except (OSError, LockException) as exc:
            if operation == "set":
                logger.warning("extension state did not commit for package %s: %s", address.package, exc)
                return StateSetResult(False, False, None, "storage_error")
            status = (
                StatusCode.EXTENSION_DATA_READ_FAILED if operation == "get" else StatusCode.EXTENSION_DATA_WRITE_FAILED
            )
            raise build_error(status, cause=exc, reason=f"cannot {operation} state for {address.package}") from exc

    def _with_package_gate(
        self,
        address: StateAddress,
        operation: str,
        value: Any,
        expected_version: int | None,
    ) -> Any:
        package_dir = self._package_dir(address.package)
        with package_file_lock(self._package_gate(address.package)):
            if not package_dir.exists():
                if operation != "set":
                    return self._absent(operation)
                package_dir.mkdir()
            if address.scope == "session":
                sessions = package_dir / "sessions"
                if not sessions.exists():
                    if operation != "set":
                        return self._absent(operation)
                    sessions.mkdir()
            return self._under_file_lock(package_dir, address, operation, value, expected_version)

    def _remove_package(self, package_id: str) -> None:
        if not self.directory.exists():
            parent = self.directory.parent
            if not parent.is_dir():
                return
            with package_file_lock(self._root_gate()):
                if not self.directory.exists():
                    return
                self._remove_package_locked(package_id)
            return
        self._remove_package_locked(package_id)

    def _remove_package_locked(self, package_id: str) -> None:
        package_dir = self._package_dir(package_id)
        gate = self._package_gate(package_id)
        with package_file_lock(gate):
            if package_dir.exists():
                if package_dir.is_symlink() or not package_dir.is_dir():
                    raise build_error(
                        StatusCode.EXTENSION_DATA_PATH_INVALID, reason="state package path is not a directory",
                    )
                shutil.rmtree(package_dir)
                sync_directory(self.directory)
        gate.unlink(missing_ok=True)

    def _under_file_lock(
        self,
        package_dir: Path,
        address: StateAddress,
        operation: str,
        value: Any,
        expected_version: int | None,
    ) -> Any:
        current: int | None = None
        committed_version: int | None = None
        scope_dir = package_dir if address.scope == "application" else resolve_data_path(package_dir / "sessions")
        name = "application.json" if address.scope == "application" else f"{address.session_id}.json"
        try:
            with package_file_lock(package_data_path(scope_dir, f"{name}.lock")):
                path = package_data_path(scope_dir, name)
                if not path.exists() and operation in ("get", "clear"):
                    return self._absent(operation)
                contents, previous = self._read(path)
                entries = contents["entries"]
                if operation == "clear":
                    return self._clear(path, contents, previous)
                entry = entries.get(address.key)
                snapshot = StateSnapshot(False) if entry is None else StateSnapshot(
                    entry.get("exists", True), entry["value"], entry["version"],
                )
                current = snapshot.version
                if operation == "get":
                    return snapshot
                if expected_version is not None and expected_version != current:
                    return StateSetResult(False, False, current, "version_conflict")
                if snapshot.exists and _same_value(snapshot.value, value):
                    return StateSetResult(True, False, current)
                entries[address.key] = {"value": value, "version": current + 1}
                data = encode_json(contents)
                self._check_size(data)
                saved = replace_json(path, data, previous)
                if saved:
                    committed_version = current + 1
                return StateSetResult(
                    saved, saved, current + 1 if saved else current, None if saved else "storage_error",
                )
        except (OSError, LockException) as exc:
            if committed_version is not None:
                raise _UnknownCommit(address, committed_version) from exc
            if operation == "set":
                logger.warning("extension state did not commit for package %s: %s", address.package, exc)
                return StateSetResult(False, False, current, "storage_error")
            raise

    def _clear(self, path: Path, contents: dict[str, Any], previous: bytes | None) -> dict[str, int]:
        changed: dict[str, int] = {}
        for key, entry in contents["entries"].items():
            if entry.get("exists", True):
                entry.update(exists=False, value=None, version=entry["version"] + 1)
                changed[key] = entry["version"]
        if changed:
            data = encode_json(contents)
            self._check_size(data)
            if not replace_json(path, data, previous):
                raise build_error(StatusCode.EXTENSION_DATA_WRITE_FAILED, reason="session state clear did not commit")
        return changed

    def _check_size(self, data: bytes) -> None:
        if len(data) > self.max_bytes:
            raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="state file exceeds configured capacity")

    def _read(self, path: Path) -> tuple[dict[str, Any], bytes | None]:
        try:
            with path.open("rb") as stream:
                data = stream.read(self.max_bytes + 1)
        except FileNotFoundError:
            return {"format_version": 1, "entries": {}}, None
        except OSError as exc:
            raise build_error(
                StatusCode.EXTENSION_DATA_READ_FAILED, cause=exc, reason=f"cannot read {path.name}",
            ) from exc
        try:
            self._check_size(data)
            contents = copy_json(json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object))
            self._validate_contents(contents)
            return contents, data
        except (ValueError, UnicodeError, RecursionError, BaseError) as exc:
            raise build_error(
                StatusCode.EXTENSION_DATA_READ_FAILED, cause=exc, reason=f"invalid state JSON in {path.name}",
            ) from exc

    @staticmethod
    def _validate_contents(contents: Any) -> None:
        if not isinstance(contents, dict) or contents.get("format_version") != 1:
            raise ValueError("unsupported state format")
        if isinstance(contents["format_version"], bool) or not isinstance(contents["format_version"], int):
            raise ValueError("state format version must be an integer")
        entries = contents.get("entries")
        if not isinstance(entries, dict):
            raise ValueError("state entries must be an object")
        for entry in entries.values():
            if not isinstance(entry, dict) or "value" not in entry:
                raise ValueError("state entry requires value and version")
            version = entry.get("version")
            require_version(version)
            if version is None or version == 0 or not isinstance(entry.get("exists", True), bool):
                raise ValueError("saved state version or existence is invalid")
            if not entry.get("exists", True) and entry["value"] is not None:
                raise ValueError("absent state cannot retain a value")


def _require_session_name(session_id: str | None, scope: str) -> None:
    """Keep a session file inside its package directory."""
    if scope != "session":
        return
    if (
        not isinstance(session_id, str)
        or _SESSION_NAME.fullmatch(session_id) is None
        or ".." in session_id
        or session_id.endswith(".")
        or session_id.split(".", 1)[0].upper() in _RESERVED_NAMES
    ):
        raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="session_id is not a safe state filename")
