# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_immutable_file_rail — SecurityRail 单元测试。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List
from unittest import IsolatedAsyncioTestCase

from openjiuwen.auto_harness.rails.security_rail import (
    SecurityRail,
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


class TestSecurityRail(IsolatedAsyncioTestCase):
    def _make_rail(self):
        return SecurityRail(
            immutable_files=[
                "openjiuwen/auto_harness/prompts/identity.md",
                "openjiuwen/auto_harness/tools/ci_gate.yaml",
            ],
            high_impact_prefixes=["openjiuwen/core/*"],
        )

    async def test_blocks_immutable_write(self):
        rail = self._make_rail()
        ctx = _FakeCtx(
            inputs=ToolCallInputs(
                tool_name="write_file",
                tool_args={
                    "file_path": (
                        "openjiuwen/auto_harness/"
                        "prompts/identity.md"
                    ),
                },
            ),
        )
        await rail.before_tool_call(ctx)
        assert len(ctx._steerings) == 1
        assert "immutable" in ctx._steerings[0].lower()

    async def test_blocks_immutable_edit(self):
        rail = self._make_rail()
        ctx = _FakeCtx(
            inputs=ToolCallInputs(
                tool_name="edit_file",
                tool_args={
                    "file_path": (
                        "openjiuwen/auto_harness/"
                        "tools/ci_gate.yaml"
                    ),
                },
            ),
        )
        await rail.before_tool_call(ctx)
        assert len(ctx._steerings) == 1

    async def test_allows_normal_file(self):
        rail = self._make_rail()
        ctx = _FakeCtx(
            inputs=ToolCallInputs(
                tool_name="write_file",
                tool_args={"file_path": "src/main.py"},
            ),
        )
        await rail.before_tool_call(ctx)
        assert len(ctx._steerings) == 0

    async def test_flags_high_impact(self):
        rail = self._make_rail()
        ctx = _FakeCtx(
            inputs=ToolCallInputs(
                tool_name="edit_file",
                tool_args={
                    "file_path": "openjiuwen/core/runner/base.py",
                },
            ),
        )
        await rail.before_tool_call(ctx)
        assert ctx.extra.get("high_impact") is True
        assert len(ctx._steerings) == 0

    async def test_ignores_non_write_tool(self):
        rail = self._make_rail()
        ctx = _FakeCtx(
            inputs=ToolCallInputs(
                tool_name="read_file",
                tool_args={
                    "file_path": (
                        "openjiuwen/auto_harness/"
                        "prompts/identity.md"
                    ),
                },
            ),
        )
        await rail.before_tool_call(ctx)
        assert len(ctx._steerings) == 0

    async def test_ignores_non_tool_call_inputs(self):
        rail = self._make_rail()
        ctx = _FakeCtx(inputs="plain string")
        await rail.before_tool_call(ctx)
        assert len(ctx._steerings) == 0

    async def test_empty_file_path(self):
        rail = self._make_rail()
        ctx = _FakeCtx(
            inputs=ToolCallInputs(
                tool_name="write_file",
                tool_args={"file_path": ""},
            ),
        )
        await rail.before_tool_call(ctx)
        assert len(ctx._steerings) == 0
