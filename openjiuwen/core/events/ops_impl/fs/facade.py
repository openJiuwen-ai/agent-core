# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Entry point plugins use for ``ops.fs.*``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from openjiuwen.core.events.bus import ExtensionEventBus
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.ops_impl.common import OpsError
from openjiuwen.core.events.ops_impl.fs import (
    FS_ANCESTORS,
    FS_EXISTS,
    FS_LIST,
    FS_READ,
    FS_STAT,
    FS_WRITE,
)
from openjiuwen.core.events.ops_impl.fs.models import (
    FsAncestorsInput,
    FsAncestorsOutput,
    FsExistsInput,
    FsExistsOutput,
    FsListInput,
    FsListOutput,
    FsReadInput,
    FsReadOutput,
    FsStatInput,
    FsStatOutput,
    FsStatus,
    FsWriteInput,
    FsWriteOutput,
    ancestors_failure,
    exists_failure,
    list_failure,
    read_failure,
    stat_failure,
    write_failure,
)
from openjiuwen.core.events.ops_impl.guard import OpsCallGuard
from openjiuwen.core.events.ops_impl.runtime import build_context
from openjiuwen.core.events.types import Event, SessionContext

_Request = FsReadInput | FsWriteInput | FsListInput | FsStatInput | FsExistsInput | FsAncestorsInput
_Output = TypeVar("_Output")
_Failure = Callable[..., object]


@dataclass(frozen=True, slots=True)
class FsOpsFacade:
    """Trigger one ``fs.*`` event on behalf of a Handler."""

    bus: ExtensionEventBus
    session: SessionContext

    @classmethod
    def from_ops(cls, bus: ExtensionEventBus, ops: EventHandlerOps) -> FsOpsFacade:
        """Build the facade from the calling handler's ops view."""

        return cls(bus=bus, session=build_context(ops))

    async def read(self, request: FsReadInput) -> FsReadOutput:
        """Trigger ``fs.read``."""

        return await self._trigger(FS_READ, request, FsReadOutput, read_failure)

    async def write(self, request: FsWriteInput) -> FsWriteOutput:
        """Trigger ``fs.write``."""

        return await self._trigger(FS_WRITE, request, FsWriteOutput, write_failure)

    async def list(self, request: FsListInput) -> FsListOutput:
        """Trigger ``fs.list``."""

        return await self._trigger(FS_LIST, request, FsListOutput, list_failure)

    async def stat(self, request: FsStatInput) -> FsStatOutput:
        """Trigger ``fs.stat``."""

        return await self._trigger(FS_STAT, request, FsStatOutput, stat_failure)

    async def exists(self, request: FsExistsInput) -> FsExistsOutput:
        """Trigger ``fs.exists``."""

        return await self._trigger(FS_EXISTS, request, FsExistsOutput, exists_failure)

    async def ancestors(self, request: FsAncestorsInput) -> FsAncestorsOutput:
        """Trigger ``fs.ancestors``."""

        return await self._trigger(FS_ANCESTORS, request, FsAncestorsOutput, ancestors_failure)

    async def _trigger(
        self,
        event_type: str,
        request: _Request,
        output_type: type[_Output],
        on_failure: _Failure,
    ) -> _Output:
        """Run the event chain, refusing a nested call of the same event."""

        with OpsCallGuard.enter(event_type) as allowed:
            if not allowed:
                return on_failure(
                    request,
                    FsStatus.DENIED,
                    OpsError(kind="reentrant_call", message=f"{event_type} is already active"),
                )
            output = await self.bus.trigger(Event(event_type, request), self.session)
        if not isinstance(output, output_type):
            raise TypeError(f"{event_type} returned {type(output).__name__}")
        return output