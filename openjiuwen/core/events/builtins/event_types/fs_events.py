# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``fs.*`` event types and their CORE handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import anyio

from openjiuwen.core.events.event_next import EventNext
from openjiuwen.core.events.ops import EventHandlerOps
from openjiuwen.core.events.ops_impl.common import (
    DEFAULT_OPS_TIMEOUT,
    OpsError,
    no_match_pattern,
    resolve_timeout_ms,
)
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
    validate_ancestors_input,
    validate_exists_input,
    validate_list_input,
    validate_read_input,
    validate_stat_input,
    validate_write_input,
    write_failure,
)
from openjiuwen.core.events.ops_impl.fs.service import FsService
from openjiuwen.core.events.ops_impl.runtime import agent_working_dir, resolve_agent
from openjiuwen.core.events.types import Event

if TYPE_CHECKING:
    from openjiuwen.core.events.bus import ExtensionEventBus

_CORE_HANDLERS = (
    (FS_READ, FsReadInput, "read", FsReadOutput, read_failure, validate_read_input),
    (FS_WRITE, FsWriteInput, "write", FsWriteOutput, write_failure, validate_write_input),
    (FS_LIST, FsListInput, "list", FsListOutput, list_failure, validate_list_input),
    (FS_STAT, FsStatInput, "stat", FsStatOutput, stat_failure, validate_stat_input),
    (FS_EXISTS, FsExistsInput, "exists", FsExistsOutput, exists_failure, validate_exists_input),
    (FS_ANCESTORS, FsAncestorsInput, "ancestors", FsAncestorsOutput, ancestors_failure, validate_ancestors_input),
)


def _make_core(event_type: str, input_type: type, method: str, failure: Callable[..., object]) -> Any:
    """Build the CORE handler that runs one ``fs.*`` request under its budget."""

    async def core(
        event: Event,
        ops: EventHandlerOps | None,
        event_next: EventNext,
    ) -> object:
        del event_next
        request = event.event_input
        if not isinstance(request, input_type):
            raise TypeError(f"{event_type} requires {input_type.__name__}")
        if ops is None or ops.session_context is None:
            raise RuntimeError(f"{event_type} requires EventHandlerOps")
        session = ops.session_context
        budget = resolve_timeout_ms(getattr(request, "timeout_ms", None), DEFAULT_OPS_TIMEOUT) / 1000
        try:
            with agent_working_dir(resolve_agent(session), session):
                with anyio.fail_after(budget):
                    return await getattr(FsService.from_ops(ops), method)(request)
        except TimeoutError:
            return failure(
                request,
                FsStatus.TIMEOUT,
                OpsError(kind="timeout", message=f"{event_type} exceeded its budget"),
            )

    return core


def register(bus: ExtensionEventBus) -> None:
    """Register every ``fs.*`` event type together with its CORE handler."""

    for event_type, input_type, method, output_type, failure, validator in _CORE_HANDLERS:
        bus.register_event_type(
            event_type,
            no_match_pattern,
            _make_core(event_type, input_type, method, failure),
            input_validator=validator,
            output_validator=lambda value, expected=output_type: isinstance(value, expected),
        )
