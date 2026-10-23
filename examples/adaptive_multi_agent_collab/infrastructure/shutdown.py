"""Explicit operator cancellation and bounded cleanup for command-line stages.

The stop flag is inherited by child tasks. It gates dispatch independently of
whether an SDK coroutine cooperates with cancellation. Pending tasks retain
their resource ownership until they actually finish; they are never detached
and replaced by new calls.
"""

from __future__ import annotations

import asyncio
import contextvars
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

SHUTDOWN_PROTOCOL = "explicit_signal_bounded_stage_drain_v2"
CLEANUP_SECONDS = 5.0
T = TypeVar("T")


@dataclass
class ShutdownState:
    reason: str | None = None
    signum: int | None = None
    cause: str | None = None
    pending_cleanup_tasks: int = 0
    signals: list[int] = field(default_factory=list)
    _notify: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def request(
        self, reason: str, signum: int | None = None, *, cause: str | None = None
    ) -> bool:
        first = self.reason is None
        if first:
            self.reason, self.signum = reason, signum
            self.cause = cause or (
                "operator-cancellation"
                if signum is not None or reason == "external-cancellation"
                else "shutdown-cleanup"
            )
        if signum is not None:
            self.signals.append(signum)
        # Internal requests and signal callbacks run on the owning event loop.
        # Notify even for a repeated request; the supervisor owns idempotency.
        if self._notify is not None:
            self._notify()
        return first


_state: contextvars.ContextVar[ShutdownState | None] = contextvars.ContextVar(
    "stage_shutdown", default=None
)


def current_shutdown() -> ShutdownState | None:
    return _state.get()


def raise_if_stopping() -> None:
    state = current_shutdown()
    if state is not None and state.reason is not None:
        error = asyncio.CancelledError(state.reason)
        setattr(error, "cancellation_cause", state.cause)
        setattr(error, "timing_phase", "pre-dispatch-shutdown")
        raise error


def note_cleanup_failure(
    original: BaseException, operation: str, error: Exception
) -> None:
    """Retain an original cancellation when best-effort publication also fails."""
    failures = list(getattr(original, "cleanup_errors", ()))
    failures.append(
        {"operation": operation, "type": type(error).__name__, "error": str(error)}
    )
    setattr(original, "cleanup_errors", failures)
    original.add_note(
        f"{operation} failed during cleanup: {type(error).__name__}: {error}"
    )


async def bounded_cleanup(
    tasks: list[asyncio.Task[Any]], *, timeout: float = CLEANUP_SECONDS
) -> tuple[int, int]:
    """Wait once without wait_for's unbounded cancellation acknowledgement.

    This does not cancel tasks a second time or pretend unfinished operations
    stopped. Their reservations/permits remain owned by the original tasks.
    """
    if not tasks:
        return 0, 0
    done, pending = await asyncio.wait(tasks, timeout=max(0.0, timeout))
    for task in done:
        if not task.cancelled():
            task.exception()  # consume errors already captured in their receipts
    state = current_shutdown()
    if state is not None:
        state.pending_cleanup_tasks = len(pending)
    return len(done), len(pending)


class StageInterrupted(RuntimeError):
    def __init__(
        self, state: ShutdownState, cleanup_errors: list[dict[str, Any]] | None = None
    ) -> None:
        self.state = state
        self.cleanup_errors = cleanup_errors or []
        super().__init__(state.reason or "external-cancellation")


def run_stage(
    awaitable: Awaitable[T], *, cleanup_seconds: float = CLEANUP_SECONDS
) -> T:
    """Run an authorized CLI stage with SIGINT/SIGTERM stop and bounded close.

    One administrative allowance covers the main task's finalization and the
    remaining task drain. Unlike asyncio.run(), neither waits indefinitely for
    cancellation acknowledgement. Unfinished work retains its durable holds.
    """
    from .failure_policy import cancel_with_cause

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    state = ShutdownState()
    token = _state.set(state)

    async def execute() -> T:
        return await awaitable

    main: asyncio.Task[T] = loop.create_task(execute())
    previous: dict[int, Any] = {}
    stopping: asyncio.Future[None] = loop.create_future()
    cleanup_deadline: float | None = None

    def remaining_cleanup() -> float:
        nonlocal cleanup_deadline
        if cleanup_deadline is None:
            cleanup_deadline = loop.time() + max(0.0, cleanup_seconds)
        return max(0.0, cleanup_deadline - loop.time())

    def notify_stop() -> None:
        if stopping.done():
            return
        remaining_cleanup()
        stopping.set_result(None)
        # A self-request is already on the runtime's cleanup/stop path. Injecting
        # cancellation there can land in its next persistence/finalization await.
        # The notified supervisor bounds that path even if it never returns.
        if (
            not main.done()
            and main is not asyncio.current_task(loop=loop)
            and not main.cancelling()
        ):
            cancel_with_cause(main, state.cause or "shutdown-cleanup")

    state._notify = notify_stop

    def stop(signum: int) -> None:
        state.request(f"operator-{signal.Signals(signum).name.lower()}", signum)

    async def supervise() -> T:
        # Waiting for a separate stop future avoids making main's cancellation
        # cooperation a prerequisite for reaching bounded cleanup.
        await asyncio.wait((main, stopping), return_when=asyncio.FIRST_COMPLETED)
        if state.reason is None:
            return await main
        if not main.done():
            await bounded_cleanup([main], timeout=remaining_cleanup())
        cleanup_errors: list[dict[str, Any]] = []
        if main.done():
            try:
                main.result()
            except BaseException as exc:  # retain cancellation/finalization evidence
                cleanup_errors.extend(getattr(exc, "cleanup_errors", ()))
                if not isinstance(exc, asyncio.CancelledError):
                    cleanup_errors.append(
                        {
                            "operation": "stage finalization",
                            "type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
        else:
            cleanup_errors.append(
                {
                    "operation": "stage finalization",
                    "type": "AdministrativeCleanupExpired",
                    "error": "main task remains unfinished; persistence and resume safety are unconfirmed",
                }
            )
        raise StageInterrupted(state, cleanup_errors)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            loop.add_signal_handler(signum, stop, signum)
        try:
            result = loop.run_until_complete(supervise())
            if state.reason is not None:
                raise StageInterrupted(state)
            return result
        except asyncio.CancelledError as exc:
            state.request("external-cancellation")
            raise StageInterrupted(
                state, list(getattr(exc, "cleanup_errors", ()))
            ) from exc
    finally:
        # Stop new admission before touching pending cleanup tasks. A signal
        # arriving during teardown records the same stop rather than resetting it.
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        if pending:
            state.request("shutdown-cleanup")
            for task in pending:
                # In particular, do not interrupt main's cleanup a second time
                # or give it another administrative allowance after expiry.
                if task is not main and not task.cancelling():
                    cancel_with_cause(task, "shutdown-cleanup")
            loop.run_until_complete(
                bounded_cleanup(pending, timeout=remaining_cleanup())
            )
        if not stopping.done():
            stopping.cancel()
        for prior_signal, handler in previous.items():
            loop.remove_signal_handler(prior_signal)
            signal.signal(prior_signal, handler)
        state._notify = None
        _state.reset(token)
        asyncio.set_event_loop(None)
        loop.close()
