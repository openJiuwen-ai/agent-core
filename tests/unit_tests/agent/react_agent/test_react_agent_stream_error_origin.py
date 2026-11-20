# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Stream error chunks carry a structured origin class for host-side staging.

Host delegation boundaries classify storage-layer failures (checkpoint save)
by exception type. The stream conversion flattens exceptions into
``{"output": str(e), "result_type": "error"}``, so the origin class name is
propagated through the error chunk payload instead.
"""

import pytest

from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent


class _FakeSession:
    def __init__(self) -> None:
        self.schemas: list[OutputSchema] = []

    async def write_stream(self, schema: OutputSchema) -> None:
        self.schemas.append(schema)


class _StubAgent:
    """Duck-typed ReActAgent carrying only the two stream-write methods.

    Class attributes keep descriptor binding so ``self.method(...)`` works
    as on the real class, without constructing a full agent.
    """

    _hitl_handler = None
    write_invoke_result_to_stream = ReActAgent.write_invoke_result_to_stream
    _write_invoke_result_to_stream = ReActAgent._write_invoke_result_to_stream


@pytest.mark.asyncio
async def test_error_result_payload_keeps_origin_class():
    session = _FakeSession()
    result = {
        "output": "boom",
        "result_type": "error",
        "error_origin_class": "sqlalchemy.exc.OperationalError",
    }
    await _StubAgent().write_invoke_result_to_stream(result, session)
    assert len(session.schemas) == 1
    assert session.schemas[0].payload["error_origin_class"] == "sqlalchemy.exc.OperationalError"
    assert session.schemas[0].payload["result_type"] == "error"


@pytest.mark.asyncio
async def test_payload_without_origin_class_stays_clean():
    session = _FakeSession()
    result = {"output": "no origin", "result_type": "error"}
    await _StubAgent().write_invoke_result_to_stream(result, session)
    assert len(session.schemas) == 1
    assert "error_origin_class" not in session.schemas[0].payload
