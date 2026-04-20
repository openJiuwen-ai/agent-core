# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_edit_check_rail — EditSafetyRail 单元测试。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from openjiuwen.auto_harness.rails.edit_safety_rail import (
    EditSafetyRail,
)
from openjiuwen.core.single_agent.rail.base import (
    ToolCallInputs,
)


@dataclass
class _FakeCtx:
    inputs: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)
    _steerings: List[str] = field(default_factory=list)

    def push_steering(self, msg: str) -> None:
        self._steerings.append(msg)


class _FakeProc:
    def __init__(self, returncode: int, stdout: bytes):
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self):
        return self._stdout, b""


class TestEditSafetyRail(IsolatedAsyncioTestCase):
    async def test_pushes_steering_on_ruff_failure(self):
        rail = EditSafetyRail()
        ctx = _FakeCtx(
            inputs=ToolCallInputs(
                tool_name="write_file",
                tool_args={"file_path": "src/foo.py"},
            ),
        )
        fake_proc = _FakeProc(1, b"E501 line too long")
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=fake_proc,
        ):
            await rail.after_tool_call(ctx)
        assert len(ctx._steerings) == 1
        assert "ruff" in ctx._steerings[0].lower()

    async def test_no_steering_on_ruff_pass(self):
        rail = EditSafetyRail()
        ctx = _FakeCtx(
            inputs=ToolCallInputs(
                tool_name="edit_file",
                tool_args={"file_path": "src/bar.py"},
            ),
        )
        fake_proc = _FakeProc(0, b"")
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=fake_proc,
        ):
            await rail.after_tool_call(ctx)
        assert len(ctx._steerings) == 0
        assert rail.edited_files() == ["src/bar.py"]

    async def test_skips_non_python(self):
        rail = EditSafetyRail()
        ctx = _FakeCtx(
            inputs=ToolCallInputs(
                tool_name="write_file",
                tool_args={"file_path": "README.md"},
            ),
        )
        await rail.after_tool_call(ctx)
        assert len(ctx._steerings) == 0

    async def test_skips_non_write_tool(self):
        rail = EditSafetyRail()
        ctx = _FakeCtx(
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_args={"file_path": "src/foo.py"},
            ),
        )
        await rail.after_tool_call(ctx)
        assert len(ctx._steerings) == 0

    async def test_handles_ruff_not_found(self):
        rail = EditSafetyRail()
        ctx = _FakeCtx(
            inputs=ToolCallInputs(
                tool_name="write_file",
                tool_args={"file_path": "src/foo.py"},
            ),
        )
        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError,
        ):
            await rail.after_tool_call(ctx)
        assert len(ctx._steerings) == 0

    async def test_reset_clears_edited_files(self):
        rail = EditSafetyRail()
        ctx = _FakeCtx(
            inputs=ToolCallInputs(
                tool_name="write_file",
                tool_args={"file_path": "src/foo.py"},
            ),
        )
        await rail.after_tool_call(ctx)
        assert rail.edited_files() == ["src/foo.py"]
        rail.reset()
        assert rail.edited_files() == []
