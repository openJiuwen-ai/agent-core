# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime signals shared by one Execution's entry, model rail and abort call."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import asyncio

    from .emitter import EventEmitter
    from .handoff import Registration

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
    # Set once the native call has exited and the outcome is determined; not a persistence receipt.
    outcome_locked: bool = False
    # Listings share_file accepted, with a confirmation chain's, for the merge and the 64-file limit at call
    # time. The ending hands off only listings whose result reached the final window (entries._finish).
    registrations: "dict[tuple[str, str], Registration]" = field(default_factory=dict, repr=False)

    def request_abort(self, reason: AbortReason) -> bool:
        """Record the first control reason before the outcome is locked; otherwise report False."""
        if self.outcome_locked or self.abort_reason is not None:
            return False
        self.abort_reason = reason
        return True

    def lock_outcome(self) -> AbortReason | None:
        """Lock the outcome; return the control reason accepted before the lock, if any."""
        self.outcome_locked = True
        return self.abort_reason

    def mark_fatal(self, error: BaseException) -> None:
        """Keep the first critical failure."""
        if self.fatal is None:
            self.fatal = error
