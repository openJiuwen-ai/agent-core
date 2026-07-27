# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Leaf Spec.build representative cases (Rail / BuiltinTool / SubAgent / BuildContext)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.manifest import ensure_builtin_elements_registered
from openjiuwen.harness.manifest.builtin_elements import ASK_USER
from openjiuwen.harness.manifest.harness_elements import SUBAGENT_EXPLORE, TASK_COMPLETION
from openjiuwen.harness.manifest.meta_elements import RAIL_FILE, TOOL_FILE
from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserRail
from openjiuwen.harness.rails.task_completion_rail import TaskCompletionRail
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.schema.deep_agent_spec import BuiltinToolSpec, RailSpec, SubAgentSpec

pytestmark = pytest.mark.level0


def _write_tiny_rail(path: Path) -> None:
    path.write_text(
        "from openjiuwen.harness.rails.base import DeepAgentRail\n"
        "class TinyRail(DeepAgentRail):\n"
        "    def __init__(self, marker: str = ''):\n"
        "        super().__init__()\n"
        "        self.marker = marker\n",
        encoding="utf-8",
    )


def _write_tiny_tool(path: Path, *, tool_id: str = "tiny_tool", tool_name: str = "tiny_tool") -> None:
    path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "from typing import Any, AsyncIterator",
                "from openjiuwen.core.foundation.tool import Tool, ToolCard",
                "",
                "class TinyTool(Tool):",
                "    def __init__(self) -> None:",
                (
                    "        super().__init__(ToolCard("
                    f"id={tool_id!r}, name={tool_name!r}, description='leaf static tool'))"
                ),
                "",
                "    async def invoke(self, inputs: dict[str, Any], **kwargs: object) -> dict[str, Any]:",
                "        return inputs",
                "",
                "    async def stream(",
                "        self, inputs: dict[str, Any], **kwargs: object",
                "    ) -> AsyncIterator[dict[str, Any]]:",
                "        if False:",
                "            yield inputs",
                "",
            ]
        ),
        encoding="utf-8",
    )


class TestRailSpecBuild:
    """RailSpec.build materializes builtin and package/file providers."""

    def test_builtin_task_completion_rail(self) -> None:
        """Builtin rail core.task_completion builds TaskCompletionRail."""
        ensure_builtin_elements_registered()
        rail = RailSpec(type=TASK_COMPLETION).build(language="en")
        assert isinstance(rail, TaskCompletionRail)

    def test_builtin_ask_user_rail(self) -> None:
        """Builtin rail core.ask_user builds AskUserRail (owns AskUserTool)."""
        ensure_builtin_elements_registered()
        rail = RailSpec(type=ASK_USER).build(language="en")
        assert isinstance(rail, AskUserRail)

    def test_package_rail_file_reads_source_root(self, tmp_path: Path) -> None:
        """harness.rail.file builds against extras['source_root'] and accepts params."""
        ensure_builtin_elements_registered()
        rail_file = tmp_path / "fake_rail.py"
        _write_tiny_rail(rail_file)
        caller = BuildContext(extras={"source_root": str(tmp_path), "marker": "keep"})
        rail = RailSpec(
            type=RAIL_FILE,
            params={"file_path": "fake_rail.py", "class_name": "TinyRail", "marker": "ok"},
        ).build(language="cn", context=caller)
        assert rail.marker == "ok"
        assert caller.extras == {"source_root": str(tmp_path), "marker": "keep"}


class TestBuiltinToolSpecBuild:
    """BuiltinToolSpec.build materializes builtin / file tools; unknown type hard-fails."""

    def test_package_tool_file_builds_instance(self, tmp_path: Path) -> None:
        """harness.tool.file builds a Tool instance from source_root."""
        ensure_builtin_elements_registered()
        tool_file = tmp_path / "fake_tool.py"
        _write_tiny_tool(tool_file)
        tool = BuiltinToolSpec(
            type=TOOL_FILE,
            params={"file_path": "fake_tool.py", "class_name": "TinyTool"},
        ).build(language="en", context=BuildContext(extras={"source_root": str(tmp_path)}))
        assert tool.card.name == "tiny_tool"

    def test_unknown_tool_type_raises(self) -> None:
        """Unknown tool provider type raises ValueError."""
        ensure_builtin_elements_registered()
        with pytest.raises(ValueError, match="Unknown tool type"):
            BuiltinToolSpec(type="core.not_a_real_tool").build(language="en")


class TestSubAgentSpecBuild:
    """SubAgentSpec.build uses providers or falls back to inline SubAgentConfig."""

    def test_explore_reads_parent_model(self) -> None:
        """core.subagent.explore_agent inherits parent_model / factory_kwargs."""
        ensure_builtin_elements_registered()
        parent = object()
        ctx = BuildContext(language="en", extras={"_parent_model": parent, "marker": "keep"})
        built = SubAgentSpec(
            agent_card=AgentCard(name="explore"),
            system_prompt="",
            factory_name=SUBAGENT_EXPLORE,
            factory_kwargs={"language": "en", "max_iterations": 7},
        ).build(parent_model=parent, language="en", context=ctx)  # type: ignore[arg-type]
        assert built is not None
        assert built.model is parent
        assert built.max_iterations == 7
        assert ctx.extras == {"_parent_model": parent, "marker": "keep"}

    def test_unknown_factory_falls_back_to_inline_config(self) -> None:
        """Unknown factory_name keeps field and builds inline SubAgentConfig (current contract)."""
        ensure_builtin_elements_registered()
        parent = object()
        built = SubAgentSpec(
            agent_card=AgentCard(name="custom_inline"),
            system_prompt="inline",
            factory_name="core.subagent.not_registered_yet",
            max_iterations=3,
        ).build(parent_model=parent, language="en")  # type: ignore[arg-type]
        assert isinstance(built, SubAgentConfig)
        assert built.factory_name == "core.subagent.not_registered_yet"
        assert built.system_prompt == "inline"
        assert built.max_iterations == 3
        assert built.model is None
