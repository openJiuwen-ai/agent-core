# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Optional host context around an actual, permission-checked tool invocation."""

from contextlib import contextmanager
from contextvars import ContextVar

_factory = ContextVar("tool_execution_context_factory", default=None)


@contextmanager
def bind_tool_execution_context(factory):
    """Bind a host async context factory to this agent execution task."""
    token = _factory.set(factory)
    try:
        yield
    finally:
        _factory.reset(token)


async def invoke_with_context(tool, inputs, *, session, tool_call_id, tool_name):
    """Keep tool identity in SDK-owned parameters, outside model arguments."""
    factory = _factory.get()
    if factory is None:
        return await tool.invoke(inputs, session=session, tool_call_id=tool_call_id)
    async with factory(tool_name=tool_name, inputs=inputs, session=session, tool_call_id=tool_call_id):
        return await tool.invoke(inputs, session=session, tool_call_id=tool_call_id)
