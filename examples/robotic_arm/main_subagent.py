#!/usr/bin/env python3
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Run robotic_arm_agent as a subagent behind a coordinator, to exercise the
on_frame_captured / on_step_result runtime-observability callbacks.

Unlike ``main.py`` (robotic_arm_agent as the top-level agent), this script
builds a small coordinator DeepAgent with robotic_arm_agent registered as a
subagent via ``task_tool`` -- the same shape a UI-facing wrapper would use to
delegate manipulation goals (see ``build_robotic_arm_agent_config`` +
``create_deep_agent(subagents=[...])``). This also happens to be the shape
that made ``ctx.session`` end up ``None`` inside the subagent (TaskTool
doesn't forward the parent session) -- ``on_frame_captured``/``on_step_result``
are wired via closures on ``RoboticArmRuntimeSettings`` instead of relying on
that session, specifically so they still fire through this delegated path.
``_CaptureLogger`` below saves every photo and grounding overlay to disk and
appends a full-detail JSON record of every callback (complete sub_tasks list,
result_text, and debug dict -- keypoints, movable_mask, gripper_action,
target, IK error) to ``events.jsonl``, so the whole run's intermediate
process is available afterwards, not just what scrolled past on the console --
every console line (this script's own prints, plus stdout/stderr from
anything else in the process) is also mirrored into ``console.log`` in the
same run folder, via ``_TeeStream``.

Same ``.env`` as ``main.py`` -- see that file's docstring for setup
(``cp examples/robotic_arm/.env.example examples/robotic_arm/.env`` and fill
in real values for your rig).

Requires: pip install 'openjiuwen[robotic-arm-so101-rekep]'
Also needs MobileSAM weights (see keypoint_proposal.py) and a physical SO-101
connected at ``ROBOT_PORT``.

Run from the repository root::

    uv run python examples/robotic_arm/main_subagent.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from openjiuwen.core.foundation.llm import init_model  # noqa: E402
from openjiuwen.core.runner import Runner  # noqa: E402
from openjiuwen.core.single_agent.schema.agent_card import AgentCard  # noqa: E402
from openjiuwen.harness.factory import create_deep_agent  # noqa: E402
from openjiuwen.harness.subagents.robotic_arm_agent import build_robotic_arm_agent_config  # noqa: E402
from openjiuwen.harness.tools.robotic_arm.config import RoboticArmRuntimeSettings  # noqa: E402
from openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep.executor import So101RekepExecutor  # noqa: E402

# The manipulation goal handed to the coordinator -- set in .env, no silent default.
QUERY = os.environ["ROBOTIC_ARM_QUERY"]

_COORDINATOR_SYSTEM_PROMPT = (
    "You are a coordinator with exactly one specialized subagent, robotic_arm_agent, for any "
    "physical manipulation goal. Delegate the whole goal to it via task_tool in a single call; "
    "do not try to plan or ground the manipulation yourself."
)


def build_model():
    """Shared model config for both the coordinator and the robotic-arm subagent.

    In production these would typically be two different models (a cheap
    text-only model for the coordinator, a vision-capable one for the arm) --
    kept identical here to keep this test script to one required model config.
    """
    return init_model(
        provider=os.environ["MODEL_PROVIDER"],
        model_name=os.environ["MODEL_NAME"],
        api_key=os.environ["API_KEY"],
        api_base=os.environ["API_BASE"],
    )


def build_step_executor() -> So101RekepExecutor:
    """Real SO-101 + RealSense + ReKep pipeline -- see config.py's SubTaskExecutor protocol."""
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


class _TeeStream:
    """Mirror every write to this stream into a log file as well as the original stream."""

    def __init__(self, original: Any, log_file: Any) -> None:
        self._original = original
        self._log_file = log_file

    def write(self, text: str) -> None:
        self._original.write(text)
        self._log_file.write(text)
        self._log_file.flush()

    def flush(self) -> None:
        self._original.flush()
        self._log_file.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


class _CaptureLogger:
    """Records the full detail of every on_frame_captured/on_step_result callback.

    Images (photo, numbered-keypoint overlay, and SAM segmentation overlay) are
    written to ``out_dir`` as individual JPEGs; every other field -- full
    sub_tasks list, result_text, and the full debug dict (2D/3D keypoints,
    movable_mask, the VLM-generated constraint_code, grasp/release keypoint
    indices, gripper_action, target, IK error) -- is both printed in full and
    appended to ``events.jsonl`` (one JSON object per line, image bytes
    replaced by the file path) so the whole run's intermediate process can be
    inspected afterwards, not just skimmed live off the console.
    """

    def __init__(self, out_dir: Path) -> None:
        self._out_dir = out_dir
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._events_path = self._out_dir / "events.jsonl"
        self._frame_count = 0
        self._step_count = 0
        print(f"[capture] writing photos/overlays/events.jsonl under {self._out_dir}")

    def _append_event(self, event: dict) -> None:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        with self._events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    async def on_frame_captured(self, payload: dict) -> None:
        """Fires from VisionPerceptionRail right after every photo capture."""
        self._frame_count += 1
        image_path = self._out_dir / f"frame_{self._frame_count:03d}.jpg"
        image_path.write_bytes(base64.b64decode(payload["image_base64"]))

        print(
            f"[callback:on_frame_captured] #{self._frame_count} {payload['width']}x{payload['height']} -> {image_path}"
        )

        self._append_event(
            {
                "type": "frame_captured",
                "seq": self._frame_count,
                "width": payload["width"],
                "height": payload["height"],
                "image_path": str(image_path),
            }
        )

    async def on_step_result(self, payload: dict) -> None:
        """Fires from StepExecutorRail right after every report_plan-triggered execute() call."""
        self._step_count += 1
        current = payload["current"]
        sub_tasks = payload["sub_tasks"]
        debug = dict(payload.get("debug") or {})

        image_paths = {}
        for debug_key, filename in (
            ("overlay_image_base64", f"step_{self._step_count:03d}_keypoints.jpg"),
            ("segmentation_image_base64", f"step_{self._step_count:03d}_segmentation.jpg"),
        ):
            b64 = debug.pop(debug_key, None)
            if b64:
                path = self._out_dir / filename
                path.write_bytes(base64.b64decode(b64))
                image_paths[debug_key.replace("_base64", "_path")] = path

        constraint_code = debug.pop("constraint_code", None)

        print(f"[callback:on_step_result] #{self._step_count} full plan:")
        for t in sub_tasks:
            print(f"    [{t.get('id')}] {t.get('status')}: {t.get('description')}")
        print(f"[callback:on_step_result] executed '{current.get('id')}': {current.get('description')}")
        print(f"[callback:on_step_result] result_text: {payload['result_text']}")
        for key, value in debug.items():
            print(f"[callback:on_step_result] debug.{key} = {value}")
        for debug_key, path in image_paths.items():
            print(f"[callback:on_step_result] {debug_key} -> {path}")
        if constraint_code:
            print(f"[callback:on_step_result] constraint_code (VLM-generated):\n{constraint_code}")

        self._append_event(
            {
                "type": "step_result",
                "seq": self._step_count,
                "sub_tasks": sub_tasks,
                "current": current,
                "result_text": payload["result_text"],
                "debug": {**debug, "constraint_code": constraint_code} if (debug or constraint_code) else None,
                **{key: str(path) for key, path in image_paths.items()},
            }
        )


async def main() -> None:
    run_dir = Path(__file__).resolve().parent / "_captures" / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    console_log = (run_dir / "console.log").open("a", encoding="utf-8")
    sys.stdout = _TeeStream(sys.stdout, console_log)
    sys.stderr = _TeeStream(sys.stderr, console_log)

    capture_logger = _CaptureLogger(run_dir)

    settings = RoboticArmRuntimeSettings(
        step_executor=build_step_executor(),
        context_default_window_round_num=1,
        on_frame_captured=capture_logger.on_frame_captured,
        on_step_result=capture_logger.on_step_result,
    )

    subagents = [
        build_robotic_arm_agent_config(
            model=build_model(),
            settings=settings,
            max_iterations=30,
            language="en",
        ),
    ]

    coordinator = create_deep_agent(
        model=build_model(),
        card=AgentCard(
            name="robotic_arm_coordinator",
            description="Coordinator that delegates physical manipulation goals to robotic_arm_agent.",
        ),
        system_prompt=_COORDINATOR_SYSTEM_PROMPT,
        subagents=subagents,
        max_iterations=10,
        language="en",
    )

    print(f"query={QUERY!r}")

    await Runner.start()
    try:
        await coordinator.ensure_initialized()
        result = await Runner.run_agent(
            coordinator,
            {"query": QUERY, "conversation_id": f"robotic_arm_coordinator_{uuid.uuid4().hex[:12]}"},
        )
    finally:
        await Runner.stop()

    print(result)


if __name__ == "__main__":
    asyncio.run(main())
