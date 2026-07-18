# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DeepAgentSpec.build() composition (not leaf provider branches)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode
from openjiuwen.harness.manifest import ensure_builtin_elements_registered, get_catalog
from openjiuwen.harness.rails.interrupt.ask_user_rail import AskUserRail
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.rails.task_completion_rail import TaskCompletionRail
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.deep_agent_spec import (
    DeepAgentSpec,
    ModelSpec,
    RailSpec,
    SubAgentSpec,
    SysOperationSpec,
    WorkspaceSpec,
)

pytestmark = pytest.mark.level0


def _fake_model_spec() -> ModelSpec:
    return ModelSpec(
        model_client_config=ModelClientConfig(
            client_provider="openai",
            api_key="fake-key-for-cold-build",
            api_base="http://localhost:0",
            verify_ssl=False,
        ),
        model_request_config=ModelRequestConfig(model="fake-cold-build"),
    )


def _sys_operation_spec(tmp_path: Path, *, suffix: str) -> SysOperationSpec:
    return SysOperationSpec(
        id=f"{suffix}_{tmp_path.name}",
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=str(tmp_path)),
    )


class TestDeepAgentColdBuild:
    """DeepAgentSpec composition: resolve_parts + build + ensure_initialized."""

    def test_resolve_parts_materializes_tools_rails_and_task_loop(self) -> None:
        """resolve_parts materializes rails together and forwards enable_task_loop."""
        spec = DeepAgentSpec(
            card=AgentCard(name="cold_parts"),
            language="en",
            enable_task_loop=False,
            auto_create_workspace=False,
            rails=[
                RailSpec(type="core.ask_user"),
                RailSpec(type="core.task_completion"),
            ],
        )
        parts = spec.resolve_parts()
        assert parts.config.enable_task_loop is False
        assert any(isinstance(r, TaskCompletionRail) for r in parts.rails)
        assert any(isinstance(r, AskUserRail) for r in parts.rails)

    def test_build_publishes_parent_model_without_mutating_caller_extras(self) -> None:
        """build injects core.subagent.* with parent model; caller extras stay untouched."""
        caller = BuildContext(language="en", extras={"marker": "keep"})
        spec = DeepAgentSpec(
            card=AgentCard(name="cold_parent_model"),
            model=_fake_model_spec(),
            language="en",
            auto_create_workspace=False,
            subagents=[
                SubAgentSpec(
                    agent_card=AgentCard(name="explore", description="explore"),
                    system_prompt="",
                    factory_name="core.subagent.explore_agent",
                    factory_kwargs={"language": "en", "max_iterations": 5},
                ),
            ],
        )
        agent = spec.build(caller)
        assert caller.extras == {"marker": "keep"}
        assert "_parent_model" not in caller.extras
        assert agent.deep_config.subagents is not None
        assert len(agent.deep_config.subagents) == 1
        assert agent.deep_config.subagents[0].model is agent.deep_config.model

    @pytest.mark.asyncio
    async def test_sys_operation_rail_registers_fs_tools_after_init(self, tmp_path: Path) -> None:
        """core.sys_operation + core.ask_user rails expose read_file / ask_user."""
        ensure_builtin_elements_registered()
        assert "core.filesystem" not in get_catalog()

        workspace = tmp_path / "workspace"

        spec = DeepAgentSpec(
            card=AgentCard(name="cold_sysop", description="cold"),
            workspace=WorkspaceSpec(root_path=str(workspace), language="en"),
            sys_operation=_sys_operation_spec(tmp_path, suffix="cold_sysop"),
            language="en",
            auto_create_workspace=True,
            rails=[
                RailSpec(type="core.sys_operation"),
                RailSpec(type="core.ask_user"),
            ],
        )
        agent = spec.build()
        assert any(isinstance(r, SysOperationRail) for r in agent._pending_rails)
        await agent.ensure_initialized()
        assert agent.ability_manager.get("read_file") is not None
        assert agent.ability_manager.get("ask_user") is not None
