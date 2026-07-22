#!/usr/bin/env python3
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Minimal end-to-end test script for the robotic-arm agent (SO-101 + ReKep).

Runs ``create_robotic_arm_agent`` as the top-level agent against a real
``So101RekepExecutor`` (real RealSense camera + real SO-101 arm). There are
TWO separate models involved -- don't confuse them:

- ``build_model()``: the main, vision-capable planning LLM that runs the
  outer ``report_plan`` loop (agent-core's own model).
- ``build_step_executor()``'s ``vlm_api_key``/``vlm_model``: ReKep's own
  constraint-generation VLM, called once per sub_task inside
  ``So101RekepExecutor.execute()`` -- independent of, and billed separately
  from, the main model above.

All configuration lives in ``.env`` (copy ``.env.example`` -> ``.env`` and
fill in real values for your rig; nothing is hardcoded here or defaulted
silently -- a missing variable raises ``KeyError`` immediately).

Requires: pip install 'openjiuwen[robotic-arm-so101-rekep]'
Also needs MobileSAM weights (see keypoint_proposal.py) and a physical SO-101
connected at ``ROBOT_PORT``.

Run from the repository root::

    cp examples/robotic_arm/.env.example examples/robotic_arm/.env
    # edit examples/robotic_arm/.env with your rig's real values
    uv run python examples/robotic_arm/main.py
"""

from __future__ import annotations

import asyncio
import os
import uuid

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from openjiuwen.core.foundation.llm import init_model  # noqa: E402
from openjiuwen.core.runner import Runner  # noqa: E402
from openjiuwen.harness.subagents.robotic_arm_agent import create_robotic_arm_agent  # noqa: E402
from openjiuwen.harness.tools.robotic_arm.config import RoboticArmRuntimeSettings  # noqa: E402
from openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep.executor import So101RekepExecutor  # noqa: E402

# The manipulation goal to hand to the agent -- set in .env, no silent default.
QUERY = os.environ["ROBOTIC_ARM_QUERY"]


def build_model():
    """Main, vision-capable planning LLM (the outer report_plan loop) -- NOT the ReKep VLM below."""
    return init_model(
        provider=os.environ["MODEL_PROVIDER"],
        model_name=os.environ["MODEL_NAME"],
        api_key=os.environ["API_KEY"],
        api_base=os.environ["API_BASE"],
    )


def build_step_executor() -> So101RekepExecutor:
    """Real SO-101 + RealSense + ReKep pipeline -- see config.py's SubTaskExecutor protocol.

    ``vlm_api_key``/``vlm_model`` here are ReKep's own constraint-generation
    VLM -- a separate model from ``build_model()``'s main planning LLM.
    """
    workspace_min = [float(x) for x in os.environ["ARM_WORKSPACE_MIN"].split(",")]
    workspace_max = [float(x) for x in os.environ["ARM_WORKSPACE_MAX"].split(",")]
    return So101RekepExecutor(
        workspace_min=workspace_min,
        workspace_max=workspace_max,
        camera_matrix_path=os.environ["CAMERA_MATRIX_PATH"],
        depth_scale_path=os.environ["DEPTH_SCALE_PATH"],
        extrinsics_path=os.environ["EXTRINSICS_PATH"],
        urdf_path=os.environ["URDF_PATH"],
        port=os.environ["ROBOT_PORT"],
        sam_checkpoint_path=os.environ["SAM_CHECKPOINT_PATH"],
        vlm_api_key=os.environ["VLM_API_KEY"],
        vlm_model=os.environ["VLM_MODEL"],
    )


async def main() -> None:
    model = build_model()
    settings = RoboticArmRuntimeSettings(step_executor=build_step_executor(), context_default_window_round_num=1)
    agent = create_robotic_arm_agent(model=model, settings=settings, max_iterations=30, language="en")

    print(f"query={QUERY!r}")

    await Runner.start()
    try:
        await agent.ensure_initialized()
        result = await Runner.run_agent(
            agent,
            {"query": QUERY, "conversation_id": f"robotic_arm_{uuid.uuid4().hex[:12]}"},
        )
    finally:
        await Runner.stop()

    print(result)


if __name__ == "__main__":
    asyncio.run(main())
