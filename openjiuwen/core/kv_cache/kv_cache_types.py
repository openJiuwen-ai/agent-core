# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class KVCacheIdentity:
    """Provider-facing KV cache lineage identity."""

    cache_id: str
    parent_cache_id: str


@dataclass(frozen=True, slots=True)
class KVCacheControlDomain:
    provider: str
    api_base: str
    model_name: str
    cache_namespace: str = ""


@dataclass(frozen=True, slots=True)
class BindingKey:
    cache_id: str
    control_domain: KVCacheControlDomain


@dataclass(frozen=True, slots=True)
class RootKey:
    parent_cache_id: str
    control_domain: KVCacheControlDomain


class ActionScope(StrEnum):
    BINDING = "binding"
    ROOT = "root"


@dataclass(frozen=True, slots=True)
class ActionKey:
    scope: ActionScope
    cache_id: str
    control_domain: KVCacheControlDomain


@dataclass(frozen=True, slots=True)
class KVCacheBinding:
    identity: KVCacheIdentity
    model: Any
    control_domain: KVCacheControlDomain


class Residency(StrEnum):
    RESIDENT = "resident"
    OFFLOADED = "offloaded"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class BindingState:
    binding: KVCacheBinding
    residency: Residency = Residency.UNKNOWN
    revision: int = 0
    fallback: bool = False


class Admission(StrEnum):
    OPEN = "open"
    BLOCKED = "blocked"
    TERMINAL = "terminal"


class ActionKind(StrEnum):
    PREFETCH = "prefetch"
    OFFLOAD = "offload"
    EVICT = "evict"


@dataclass(slots=True)
class PendingAction:
    kind: ActionKind
    task: asyncio.Task[bool]
    provider_call_started: asyncio.Event


@dataclass(slots=True)
class ActionState:
    active_inference_count: int = 0
    admission: Admission = Admission.OPEN
    pending_action: PendingAction | None = None
    action_tail: asyncio.Task[bool] | None = None
    fail_open: bool = False


@dataclass(slots=True)
class InferenceLease:
    child_key: ActionKey
    root_key: ActionKey
    released: bool = False


class KVCacheRuntimeProtocol(Protocol):
    """Narrow runtime contract used by Session and model-call integration."""

    async def prepare(self, identity: KVCacheIdentity) -> bool:
        ...

    async def suspend(self, identity: KVCacheIdentity) -> bool:
        ...

    async def release(self, identity: KVCacheIdentity) -> bool:
        ...

    async def begin_inference(
        self,
        identity: KVCacheIdentity,
        model: Any,
        *,
        model_name: str | None = None,
    ) -> InferenceLease | None:
        ...

    async def end_inference(
        self,
        lease: InferenceLease | None,
        *,
        succeeded: bool,
    ) -> None:
        ...


__all__ = [
    "ActionKey",
    "ActionKind",
    "ActionScope",
    "ActionState",
    "Admission",
    "BindingKey",
    "BindingState",
    "InferenceLease",
    "KVCacheBinding",
    "KVCacheControlDomain",
    "KVCacheIdentity",
    "KVCacheRuntimeProtocol",
    "PendingAction",
    "Residency",
    "RootKey",
]
