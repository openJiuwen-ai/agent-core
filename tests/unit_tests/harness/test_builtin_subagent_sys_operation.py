# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Auto-injected sub-agents share the parent agent's filesystem boundary.

Two factories inject sub-agent specs on their own: ``create_code_agent`` adds
the built-in explore / plan agents, and ``create_deep_agent`` adds the
general-purpose agent. When those specs carry no workspace / sys_operation,
``create_subagent`` drops the parent's boundary and ``create_deep_agent`` mints
a fresh LOCAL sys_operation for them -- restricted to their own narrower
workspace in local mode, and outside the sandbox entirely in sandbox mode.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.subagents.code_agent import create_code_agent

pytestmark = pytest.mark.level0


def _fake_model() -> Model:
    return Model(
        model_client_config=ModelClientConfig(
            client_provider="openai",
            api_key="fake-key-for-builtin-subagents",
            api_base="http://localhost:0",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="fake-builtin-subagents"),
    )


def _spec_by_name(subagents: list, name: str) -> SubAgentConfig | None:
    """Return the injected spec carrying ``name``."""
    for spec in subagents or []:
        if isinstance(spec, SubAgentConfig) and spec.agent_card.name == name:
            return spec
    return None


@pytest.mark.parametrize("subagent_name", ["explore_agent", "plan_agent"])
def test_injected_builtin_subagents_carry_parent_sys_operation(tmp_path, subagent_name: str) -> None:
    sys_operation = MagicMock(spec=SysOperation)
    sys_operation.id = "code_agent_parent_sysop"

    agent = create_code_agent(
        _fake_model(),
        card=AgentCard(name="code_agent", description="code agent under test"),
        workspace=str(tmp_path),
        sys_operation=sys_operation,
        language="en",
        auto_create_workspace=False,
    )

    spec = _spec_by_name(agent.deep_config.subagents, subagent_name)
    assert spec is not None
    assert spec.sys_operation is sys_operation
    # create_subagent only adopts spec.sys_operation when workspace is set too.
    assert spec.workspace == str(tmp_path)


def test_injected_general_purpose_subagent_carries_parent_sys_operation(tmp_path) -> None:
    sys_operation = MagicMock(spec=SysOperation)
    sys_operation.id = "deep_agent_parent_sysop"

    agent = create_deep_agent(
        model=_fake_model(),
        card=AgentCard(name="deep_agent", description="deep agent under test"),
        workspace=str(tmp_path),
        sys_operation=sys_operation,
        add_general_purpose_agent=True,
        language="en",
        auto_create_workspace=False,
    )

    spec = _spec_by_name(agent.deep_config.subagents, "general-purpose")
    assert spec is not None
    assert spec.sys_operation is sys_operation
    assert spec.workspace is agent.deep_config.workspace
