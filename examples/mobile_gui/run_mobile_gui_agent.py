#!/usr/bin/env python3
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Run the mobile GUI agent end-to-end (VLM grounding, no skill discovery).

Prerequisites:
- ``pip install 'openjiuwen[mobile-gui]'`` (uiautomator2 + Pillow)
- Android emulator or device visible to ``adb`` (``DEVICE_SERIAL``, default ``emulator-5554``)
- Vision-capable LLM configured in ``examples/mobile_gui/.env`` (see ``.env.example``)

Run from repository root::

    uv run python examples/mobile_gui/run_mobile_gui_agent.py
"""

from __future__ import annotations

import asyncio
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

from openjiuwen.harness.subagents.mobile_gui_agent import create_mobile_gui_agent  # noqa: E402


async def main() -> None:
    bootstrap_runtime()
    model = build_chat_model()
    task = default_task()
    serial = device_serial()
    print_run_banner(serial=serial, task=task)

    conversation_id = f"mobile_direct_{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="mobile-gui-ws-") as workspace_root:
        agent = create_mobile_gui_agent(
            model=model,
            workspace=workspace_root,
            max_iterations=env_int("MAX_ITERATIONS", 30),
            language="en",
        )
        result = await run_agent_with_runner(
            agent,
            query=task,
            conversation_id=conversation_id,
        )

    print_agent_result(result)


if __name__ == "__main__":
    asyncio.run(main())
