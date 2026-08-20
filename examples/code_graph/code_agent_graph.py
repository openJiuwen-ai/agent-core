# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Enable Code Graph find_* tools on a Code Agent.

Product default is off (grep / read / edit only). Pass
``code_graph_profile="graph"`` to index the workspace and expose resolve /
find / relation tools. The same agent then edits and tests.

ContextBench eval uses ``code_graph_prompt_mode="locate"`` instead, which
also registers ``submit_code_context``. Do not use that mode in product.
"""

from __future__ import annotations

from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.harness.subagents.code_agent import create_code_agent


def build_graph_code_agent(workspace: str, model: Model):
    return create_code_agent(
        model,
        workspace=workspace,
        language="en",
        code_graph_profile="graph",
    )


if __name__ == "__main__":
    # Replace with a real model config before running.
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider="openai",
            api_key="replace-me",
            api_base="https://api.openai.com/v1",
        ),
        model_config=ModelRequestConfig(model="gpt-4o"),
    )
    agent = build_graph_code_agent(".", model)
    print("created", agent.card.name, "with profile=graph")
