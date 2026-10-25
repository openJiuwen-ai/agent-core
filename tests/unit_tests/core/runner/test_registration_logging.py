# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bulk registration only changes success detail levels, not resources or errors."""

import asyncio
import logging
import uuid
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from openjiuwen.core.common.logging.default.default_impl import DefaultLogger
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner.resources_manager import resource_manager, tag_manager
from openjiuwen.core.runner.resources_manager.registration_logging import batch_registration_logging
from openjiuwen.core.runner.resources_manager.resource_manager import ResourceMgr


class EchoTool(Tool):
    def __init__(self, name):
        super().__init__(ToolCard(id=name, name=name, description="echo", input_params={}))

    async def invoke(self, inputs, **kwargs):
        return inputs

    async def stream(self, inputs, **kwargs):
        yield inputs


class Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.items = []

    def emit(self, record):
        self.items.append(record)


@pytest.fixture
def captured(monkeypatch, tmp_path):
    wrapped = DefaultLogger(
        "registration-test-" + uuid.uuid4().hex,
        {
            "level": "INFO",
            "output": [],
            "propagate": False,
            "log_file": str(tmp_path / "unused.log"),
        },
    )
    records = Records()
    wrapped.add_handler(records)
    builder = Mock(wraps=wrapped._build_structured_event_dict)
    monkeypatch.setattr(wrapped, "_build_structured_event_dict", builder)
    monkeypatch.setattr(resource_manager, "logger", wrapped)
    monkeypatch.setattr(tag_manager, "logger", wrapped)
    try:
        yield wrapped, records.items, builder
    finally:
        wrapped.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("tag", [None, "office-claw"])
@pytest.mark.parametrize("batch", [False, True])
async def test_real_registration_and_invocation_preserved(captured, tag, batch):
    _, records, builder = captured
    manager = ResourceMgr()
    tool = EchoTool("echo")
    with batch_registration_logging() if batch else nullcontext():
        result = manager.add_tool(tool, tag=tag)
    assert result.is_ok()
    if batch:
        assert records == []
        builder.assert_not_called()  # Disabled DEBUG skips structured event construction.
    else:
        assert len(records) == 2
        assert all(record.levelno == logging.INFO for record in records)
    assert manager.get_tool(tool.card.id) is tool
    if tag is not None:
        assert manager.resource_has_tag(tool.card.id, tag)
    assert await tool.invoke({"value": 42}) == {"value": 42}
    assert manager.remove_tool(tool.card.id).is_ok()


def test_debug_retains_details_and_original_callers(captured):
    wrapped, records, _ = captured
    wrapped.set_level(logging.DEBUG)
    with batch_registration_logging():
        assert ResourceMgr().add_tool(EchoTool("detail"), tag="office-claw").is_ok()
    assert len(records) == 2
    assert all(record.levelno == logging.DEBUG for record in records)
    assert {record.filename for record in records} == {"resource_manager.py", "tag_manager.py"}
    assert any('"resource_id": "detail"' in record.getMessage() for record in records)
    assert any("Added tags to resource" in record.getMessage() for record in records)


def test_duplicate_error_and_original_resource_preserved(captured):
    _, records, _ = captured
    manager = ResourceMgr()
    original = EchoTool("duplicate")
    with batch_registration_logging():
        assert manager.add_tool(original, tag="office-claw").is_ok()
        assert not manager.add_tool(EchoTool("duplicate"), tag="office-claw").is_ok()
    assert any(record.levelno == logging.ERROR and "add resource failed" in record.getMessage() for record in records)
    assert manager.get_tool("duplicate") is original


def test_nested_scope_restored_after_exception(captured):
    _, records, _ = captured
    manager = ResourceMgr()
    with batch_registration_logging():
        with pytest.raises(RuntimeError, match="stop"):
            with batch_registration_logging():
                raise RuntimeError("stop")
        assert manager.add_tool(EchoTool("still-batch"), tag="office-claw").is_ok()
    assert records == []
    assert manager.add_tool(EchoTool("ordinary"), tag="office-claw").is_ok()
    assert len(records) == 2
    assert all(record.levelno == logging.INFO for record in records)


@pytest.mark.asyncio
async def test_concurrent_ordinary_registration_keeps_info(captured):
    _, records, _ = captured
    entered, release = asyncio.Event(), asyncio.Event()
    manager = ResourceMgr()

    async def batch_request():
        with batch_registration_logging():
            entered.set()
            await release.wait()
            assert manager.add_tool(EchoTool("batch"), tag="office-claw").is_ok()

    task = asyncio.create_task(batch_request())
    try:
        await entered.wait()
        assert manager.add_tool(EchoTool("outside"), tag="office-claw").is_ok()
    finally:
        release.set()
        await task
    assert len(records) == 2
    assert all(record.levelno == logging.INFO and "outside" in record.getMessage() for record in records)
