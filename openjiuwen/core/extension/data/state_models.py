# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Portable references, resolved addresses and results for persistent state."""

from dataclasses import dataclass
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.extension.data.json_values import require_package_id


@dataclass(frozen=True, slots=True)
class StateRef:
    """Identify public package state, selecting a scope at the call boundary."""

    package: str
    key: str
    scope: str = "auto"


@dataclass(frozen=True, slots=True)
class StateAddress:
    """A fully resolved state location, independent of connection routing."""

    package: str
    key: str
    scope: str
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    """An independent JSON value and its durable per-address version."""

    exists: bool
    value: Any = None
    version: int = 0


@dataclass(frozen=True, slots=True)
class StateSetResult:
    """Report persistence separately from downstream presentation delivery."""

    is_set: bool
    changed: bool
    version: int | None
    reason: str | None = None


def require_address(address: StateAddress) -> None:
    """Validate a resolved address before any filesystem operation."""
    if not isinstance(address, StateAddress):
        raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="state address must be StateAddress")
    require_package_id(address.package)
    if not isinstance(address.key, str) or address.scope not in ("session", "application"):
        raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="invalid state key or scope")
    if address.scope == "session":
        if not isinstance(address.session_id, str) or not address.session_id.strip():
            raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="session state requires a session identifier")
    elif address.session_id is not None:
        raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="application state cannot name a session")


def require_version(version: int | None) -> None:
    """Reject booleans and negative values as version conditions."""
    if version is None:
        return
    if isinstance(version, bool) or not isinstance(version, int):
        raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="expected_version must be a non-negative integer")
    if version < 0:
        raise build_error(StatusCode.EXTENSION_DATA_INVALID, reason="expected_version must be a non-negative integer")
