# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime signals shared by one Execution's entry, model rail and abort call."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import asyncio

    from .emitter import EventEmitter

AbortReason = Literal["cancel", "stop"]


@dataclass
class ExecutionControl:
    """Created with the construction context, so a control can arrive before the agent exists."""

    abort_reason: AbortReason | None = None
    # Critical failures caught inside native code must still stop the Execution and raise.
    fatal: BaseException | None = None
    emitter: "EventEmitter | None" = field(default=None, repr=False)
    # Resolved when the native stop request returns; cleanup must not overlap it.
    stop_settled: "asyncio.Future[None] | None" = field(default=None, repr=False)

    def request_abort(self, reason: AbortReason) -> bool:
        """Record the first control reason; later requests keep it and report False."""
        if self.abort_reason is not None:
            return False
        self.abort_reason = reason
        return True

    def mark_fatal(self, error: BaseException) -> None:
        """Keep the first critical failure."""
        if self.fatal is None:
            self.fatal = error
