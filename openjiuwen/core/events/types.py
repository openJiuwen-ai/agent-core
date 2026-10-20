# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public value objects and type aliases for the events contract."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, TypeAlias

EventType: TypeAlias = str
EventInput: TypeAlias = object
EventOutput: TypeAlias = object
HandlerId: TypeAlias = str
OPS_BUILD_EVENT_TYPE: Final[EventType] = "ops.build"


class HandlerLevel(StrEnum):
    """Fixed handler layers available to one event call."""

    USER = "user"
    BUILTIN = "builtin"
    CORE = "core"


def _empty_metadata() -> Mapping[str, object]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Event:
    """One event type and its input for a single call."""

    event_type: EventType
    event_input: EventInput


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Read-only session facts supplied at a call boundary."""

    session_id: str
    cwd: str | None = None
    metadata: Mapping[str, object] = field(default_factory=_empty_metadata)


@dataclass(frozen=True, slots=True)
class PackageMetadata:
    """Package identity supplied when building handler operations."""

    package_id: str
    package_version: str | None = None


MatchPattern: TypeAlias = Mapping[str, object]
MatchPatternFunc: TypeAlias = Callable[[EventInput, MatchPattern], bool]
OpsMethod: TypeAlias = Callable[..., Awaitable[object]]
