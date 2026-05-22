#!/usr/bin/env python3
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Run a coordinator DeepAgent with browser, code, and mobile GUI subagents.

The parent delegates via ``task_tool`` when a specialized subagent fits; otherwise it
answers or plans directly. Routing hints come from subagent card descriptions and, by
default, a short coordinator system prompt.

Coordinator prompt controls (``examples/mobile_gui/.env``):
- ``MOBILE_COORDINATOR_SYSTEM_PROMPT`` — full override; empty value disables extra text
- ``MOBILE_COORDINATOR_DEFAULT_HINT=0`` — disable the built-in default hint

Prerequisites match ``run_mobile_gui_agent.py``. ``browser_agent`` also needs Playwright
/MCP configured like other browser examples.

Run from repository root::

    uv run python examples/mobile_gui/run_mobile_gui_subagent.py
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

from example_utils import (
    bootstrap_runtime,
    build_chat_model,
    configure_import_paths,
    default_task,
    device_serial,
    env_int,
    print_agent_result,
    print_run_banner,
    run_agent_with_runner,
)

configure_import_paths()

from openjiuwen.core.single_agent.schema.agent_card import AgentCard  # noqa: E402
from openjiuwen.harness.factory import create_deep_agent  # noqa: E402
from openjiuwen.harness.subagents.browser_agent import build_browser_agent_config  # noqa: E402
from openjiuwen.harness.subagents.code_agent import build_code_agent_config  # noqa: E402
from openjiuwen.harness.subagents.mobile_gui_agent import build_mobile_gui_agent_config  # noqa: E402

_DEFAULT_COORDINATOR_HINT = (
    "You are the coordinator. Handle straightforward requests yourself: answer, clarify, or lay "
    "out a plan when no specialized subagent is needed. When a task clearly matches one of the "
    "subagent types listed in task_tool (each has a distinct role), delegate with task_tool: "
    "pick the right subagent_type and pass a clear task_description. Prefer not to delegate "
    "when splitting work would add latency without benefit."
)


def coordinator_system_prompt() -> str | None:
    """Extra coordinator identity text. Env overrides; empty env disables even the default hint."""
    raw = os.getenv("MOBILE_COORDINATOR_SYSTEM_PROMPT")
    if raw is not None:
        return raw.strip() or None
    if os.getenv("MOBILE_COORDINATOR_DEFAULT_HINT", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return None
    return _DEFAULT_COORDINATOR_HINT


async def main() -> None:
    bootstrap_runtime()
    model = build_chat_model()
    task = default_task()
    serial = device_serial()
    print_run_banner(serial=serial, task=task)

    sub_max_iter = env_int("OTHER_SUBAGENT_MAX_ITERATIONS", 25)
    subagents = [
        build_browser_agent_config(
            model=model,
            workspace=None,
            max_iterations=sub_max_iter,
            language="en",
        ),
        build_code_agent_config(
            model=model,
            workspace=None,
            max_iterations=sub_max_iter,
            language="en",
        ),
        build_mobile_gui_agent_config(
            model=model,
            workspace=None,
            max_iterations=env_int("MOBILE_SUBAGENT_MAX_ITERATIONS", 30),
            language="en",
        ),
    ]

    conversation_id = f"mobile_parent_{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="mobile-gui-parent-ws-") as workspace_root:
        parent = create_deep_agent(
            model=model,
            card=AgentCard(
                name="coordinator_with_mobile",
                description="DeepAgent with browser, code, and mobile GUI subagents.",
            ),
            workspace=workspace_root,
            system_prompt=coordinator_system_prompt(),
            subagents=subagents,
            max_iterations=env_int("MAX_ITERATIONS", 20),
            language="en",
        )
        result = await run_agent_with_runner(
            parent,
            query=task,
            conversation_id=conversation_id,
        )

    print_agent_result(result)


if __name__ == "__main__":
    asyncio.run(main())
