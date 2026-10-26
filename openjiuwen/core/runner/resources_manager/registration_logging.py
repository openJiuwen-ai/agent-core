# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Opt-in, task-local success detail levels for bulk resource registration."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from openjiuwen.core.common.logging.protocol import LoggerProtocol

_batch_success_details: ContextVar[bool] = ContextVar("batch_registration_success_details", default=False)


@contextmanager
def batch_registration_logging() -> Iterator[None]:
    """Use DEBUG for success details; the caller retains its INFO batch summary.

    No logger configuration is changed. Errors and warnings keep their levels,
    and the previous scope is restored even when registration fails.
    """
    token = _batch_success_details.set(True)
    try:
        yield
    finally:
        _batch_success_details.reset(token)


def registration_success_log(logger: LoggerProtocol) -> Callable[..., None]:
    """Return the bound method so emitting preserves the original caller frame.

    The logging backend checks DEBUG before constructing a structured event.
    Resolve on each call so backend configuration reloads remain effective.
    """
    return logger.debug if _batch_success_details.get() else logger.info
