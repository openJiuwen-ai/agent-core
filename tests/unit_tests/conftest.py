# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared fixtures; load harness dependencies only when MoA tests request them."""

import pytest_asyncio


@pytest_asyncio.fixture
async def deep_factory(tmp_path):
    from uuid import uuid4

    from openjiuwen.core.foundation.llm import AssistantMessage
    from openjiuwen.core.sys_operation import SysOperation, SysOperationCard
    from openjiuwen.harness import Workspace, create_deep_agent
    from openjiuwen.harness.moa import MoAConfig
    from tests.unit_tests.harness.moa.helpers import _REFERENCE_PROPOSAL, _unique_rails
    from tests.unit_tests.harness.moa.helpers import primary as mock_model

    created = []

    def build(
        primary,
        *,
        reference=None,
        rails=None,
        tools=None,
        operation=True,
        mode="aggregator_on_demand",
        task_loop=False,
        skill_dir=None,
    ):
        sys_operation = (
            SysOperation(SysOperationCard(id=f"moa-skill-operation-{uuid4().hex}"))
            if operation is True
            else operation or None
        )
        reference = reference or mock_model(AssistantMessage(content=_REFERENCE_PROPOSAL))
        moa = MoAConfig(
            reference_models=(reference,),
            mode=mode,
            scorer=(lambda *_: 0.5) if mode == "verifier_routed" else None,
            consultation_skill_dir=skill_dir,
        )
        agent = create_deep_agent(
            model=primary,
            moa=moa,
            rails=rails,
            tools=tools,
            workspace=Workspace(root_path=tmp_path),
            sys_operation=sys_operation,
            enable_sys_operation=sys_operation is not None,
            enable_task_loop=task_loop,
            language="en",
            auto_create_workspace=False,
            enable_read_image_multimodal=False,
            enable_security_rail=False,
            enable_model_anomaly_detection_rail=False,
            enable_tool_resilience_rail=False,
        )
        created.append(agent)
        return agent, reference

    yield build
    for agent in reversed(created):
        for rail in reversed(_unique_rails(agent, object)):
            await agent.unregister_rail(rail)
        for card in list(agent.ability_manager.list()):
            agent.ability_manager.remove_ability(card.name)
