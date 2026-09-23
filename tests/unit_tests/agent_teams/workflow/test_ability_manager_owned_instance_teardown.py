# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests: teardown never drops a refreshed owner's tool instance.

A relaunched workflow run re-mints the same session avatar member name
(``_next_member_name``'s counter is per run), so two avatars briefly share one
owner-qualified tool id in the process-global resource manager —
``add_ability``'s refresh path lets the later owner win. The old avatar's late
teardown must not remove the new owner's live instance: that produced
``Tool instance not found in resource_mgr`` for in-flight ``structured_output``
calls (claim-cross-verify 2026-09-22, glm session avatar re-mounted thrice).
"""

import pytest

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.single_agent.ability_manager import AbilityManager


class _StubTool(Tool):
    async def invoke(self, inputs, **kwargs):
        return "ok"

    async def stream(self, inputs, **kwargs):
        yield "ok"


def _make_tool() -> _StubTool:
    return _StubTool(ToolCard(name="structured_output", id="structured_output"))


@pytest.mark.asyncio
@pytest.mark.level0
async def test_teardown_spares_refreshed_owners_instance():
    from openjiuwen.core.runner import Runner

    await Runner.start()
    try:
        owner = "team_wf-sess-glm-5-3-flash-1"
        old_mgr = AbilityManager(owner_id=owner)
        new_mgr = AbilityManager(owner_id=owner)

        old_tool = _make_tool()
        new_tool = _make_tool()
        old_mgr.add_ability(old_tool.card, old_tool)
        # Relaunch mounts over the same qualified id: refresh, later owner wins.
        new_mgr.add_ability(new_tool.card, new_tool)
        assert Runner.resource_mgr.get_tool(tool_id=new_tool.card.id) is new_tool

        # Old avatar's round-end teardown lands late, while the new avatar's
        # turn is still in flight — it must not remove the new owner's instance.
        old_mgr.teardown_tools()
        assert Runner.resource_mgr.get_tool(tool_id=new_tool.card.id) is new_tool

        # The owning manager still tears its own registration down normally.
        new_mgr.teardown_tools()
        assert Runner.resource_mgr.get_tool(tool_id=new_tool.card.id) is None
    finally:
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_remove_ability_spares_refreshed_owners_instance():
    from openjiuwen.core.runner import Runner

    await Runner.start()
    try:
        owner = "team_wf-sess-glm-5-3-flash-1"
        old_mgr = AbilityManager(owner_id=owner)
        new_mgr = AbilityManager(owner_id=owner)

        old_tool = _make_tool()
        new_tool = _make_tool()
        old_mgr.add_ability(old_tool.card, old_tool)
        new_mgr.add_ability(new_tool.card, new_tool)

        # The per-turn unmount (harness.remove_tool → remove_ability) of the
        # old avatar must not drop the new avatar's mounted instance.
        old_mgr.remove_ability("structured_output")
        assert Runner.resource_mgr.get_tool(tool_id=new_tool.card.id) is new_tool

        new_mgr.remove_ability("structured_output")
        assert Runner.resource_mgr.get_tool(tool_id=new_tool.card.id) is None
    finally:
        await Runner.stop()
