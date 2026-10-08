# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from types import SimpleNamespace

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.build_context import BuildContext
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec


def test_tool_context_gets_final_operation_without_mutating_parent(monkeypatch):
    from openjiuwen.harness import factory

    contexts = []

    def capture_tools(self, *args, context=None, **kwargs):
        contexts.append(context)
        return []

    operations = [object(), object()]
    pending = iter(operations)
    monkeypatch.setattr(DeepAgentSpec, "_resolve_tools", capture_tools)
    monkeypatch.setattr(factory, "resolve_deep_agent_parts", lambda *a, **kw: SimpleNamespace(
        config=SimpleNamespace(sys_operation=next(pending))))
    parent = BuildContext()
    for name in ["first", "second"]:
        DeepAgentSpec(card=AgentCard(id=name, name=name)).resolve_parts(parent)
    assert contexts[0].extras["sys_operation"] is operations[0]
    assert contexts[1].extras["sys_operation"] is operations[1]
    assert "sys_operation" not in parent.extras
