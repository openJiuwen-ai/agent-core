# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tool-local outbound permission routing. No policy/configuration is carried here."""

from contextlib import contextmanager
from contextvars import ContextVar

_permission_key: ContextVar[str | None] = ContextVar("outbound_permission_key", default=None)
OUTBOUND_PERMISSION_KEY = "_outbound_permission_key"


def outbound_permission_key() -> str | None:
    return _permission_key.get()


@contextmanager
def bind_outbound_permission_key(key: str | None):
    """Bind for the actual tool execution, including threads, and always restore."""
    token = _permission_key.set(key)
    try:
        yield
    finally:
        _permission_key.reset(token)
