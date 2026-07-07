# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""VLM constraint-code generation for a single ReKep sub-task.

Ported from a user-supplied ReKep-on-SO101 reference implementation
(``run_record_skills/core/constraint_generation.py``), with two changes:

1. ``task`` is one ``report_plan`` sub_task's ``description`` (e.g. "grasp the
   tape"), not the whole multi-object goal -- the outer agent-core model
   already owns sequencing across sub-tasks (see ``rekep/executor.py``), so
   the prompt is told this is a single atomic action and should decompose
   into at most a couple of stages (approach/grasp, or just place), not a
   whole task's worth of stages.
2. The generated code is executed through
   :func:`~.rekep._code_sandbox.safe_exec_constraint_code` (AST-whitelisted,
   restricted builtins) instead of a bare ``exec()``.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from openjiuwen.harness.tools.robotic_arm.vendors.so101._kinematics import (
    IKSolver,
    check_keypoint_reachability,
    check_reachability,
    reachability_report_str,
)
from openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep._code_sandbox import safe_exec_constraint_code
from openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep.vlm_client import VlmClient

_CONSTRAINT_PROMPT = """\
You are controlling a robot arm to perform ONE atomic manipulation action by
writing Python constraint functions. This action is a single step out of a
larger plan tracked by another system -- decompose ONLY this action into
stages (usually 1-2: e.g. "approach" + "grasp", or just "place"), not a
whole multi-object task.

The image shows the scene with numbered red keypoints. The action is given as text.

## Your job

Before writing constraints, analyze the target object's geometry:

1. What is the object involved? (from the action description)
2. What geometric primitive best describes it?
   - thin disk / roll (tape, spool) -> approach from the side,
     fingers wrap around the rim
   - elongated cylinder (pen, bottle) -> approach perpendicular
     to the long axis
   - flat rectangular (book, phone) -> top-down
   - box / cube -> top-down
3. Based on that shape, choose approach_elevation_deg and gripper_roll_deg.

For this action:

1. Decide num_stages (usually 1-2). If this action involves grasping, grasping
   MUST be its own stage.
2. For each stage write constraint functions:
   fn(end_effector: np.ndarray[3], keypoints: np.ndarray[K,3]) -> float
   cost <= 0 means satisfied. Lower is better.

   You have access to check_reachability(point_m, elevation_deg, roll_deg)
   which returns {"reachable": bool, "ik_error_cm": float}. Use it to verify
   approach points before committing to them. Report if you cannot use it.

3. STAGE_MOVABLE_MASK[stage]: list of bool length K. True = that keypoint
   moves with the gripper (object being held).
4. STAGE_GRIPPER_ACTION[i]: "open" or "closed" at END of stage i.
5. approach_elevation_deg: 90 = straight down, 45 = diagonal, 0 = horizontal.
6. gripper_roll_deg: rotation of the gripper fingers around the approach axis
   (0 = default, 90 = good for cylindrical objects).

## Rules
- Each constraint takes end_effector [3] and keypoints [K,3], returns float.
- No if-statements, no loops, no imports. Use np.linalg.norm, abs, etc. only.
- All positions in METRES, robot base frame.
- For a grasp stage: ONE subgoal constraint aligning EE with the grasp keypoint.
- The image shows colored arrows for the robot's coordinate axes:
  RED = +X (forward), GREEN = +Y (left facing the robot), BLUE = +Z (up).
  Origin is the robot base center.

## Output ONLY a Python code block:

```python
num_stages = ...

def stage1_subgoal_constraint1(end_effector, keypoints):
    ...

STAGE_CONSTRAINTS      = [[...], ...]
STAGE_PATH_CONSTRAINTS = [[], ...]
STAGE_NAMES            = [...]
STAGE_MOVABLE_MASK     = [[False]*K, ...]
STAGE_GRIPPER_ACTION   = [...]

grasp_keypoints   = [keypoint_index_to_grasp]
release_keypoints = [stage_index_to_release]

approach_elevation_deg = 90
gripper_roll_deg       = 0
```
"""


def _safe_wrap(fn):
    """Broken constraint -> huge penalty, never a free pass (0 would mean 'satisfied')."""

    def safe(ee, kps):
        try:
            return float(fn(ee, kps))
        except Exception:
            return 1e6

    return safe


def generate_constraints(
    overlay_rgb: np.ndarray,
    task: str,
    num_keypoints: int,
    keypoints_3d: np.ndarray,
    ik_solver: IKSolver,
    vlm: VlmClient,
    *,
    hint_pixel: Optional[tuple[int, int]] = None,
    max_tokens: int = 3000,
) -> dict[str, Any]:
    """Query the VLM to translate ``task`` (one sub_task's description) into
    stage constraint functions, executed via the AST-sandboxed exec path."""
    kp_report = check_keypoint_reachability(keypoints_3d, ik_solver)
    report_str = reachability_report_str(kp_report)

    def _check_reachability(point_m: np.ndarray, elevation_deg: float = 90, roll_deg: float = 0) -> dict:
        return check_reachability(point_m, ik_solver, elevation_deg, roll_deg)

    hint_str = (
        f"\nThe outer planner pointed near pixel {hint_pixel} on this image to hint which "
        "object/location this action refers to.\n"
        if hint_pixel is not None
        else ""
    )

    prompt = (
        f"{_CONSTRAINT_PROMPT}\n\n"
        f'Action: "{task}"\n'
        f"Number of keypoints in image: {num_keypoints}"
        f"{hint_str}\n"
        f"REACHABILITY REPORT (IK error in cm, <3cm = reachable):\n{report_str}\n\n"
        "Choose approach_elevation_deg based on the grasp keypoint's reachability above.\n"
    )

    raw = vlm.query(prompt, image=overlay_rgb, max_tokens=max_tokens)

    code = raw
    if "```python" in code:
        code = code.split("```python", 1)[1].split("```", 1)[0]
    elif "```" in code:
        code = code.split("```", 1)[1].split("```", 1)[0]

    namespace = safe_exec_constraint_code(
        code,
        {
            "np": np,
            "numpy": np,
            "check_reachability": _check_reachability,
            "keypoints_3d": keypoints_3d,
            "keypoints": keypoints_3d,
        },
    )

    num_stages = namespace.get("num_stages")
    if num_stages is None:
        raise ValueError("VLM output is missing num_stages")

    stage_constraints = [[_safe_wrap(fn) for fn in stage] for stage in namespace.get("STAGE_CONSTRAINTS", [])]
    stage_path_constraints = [[_safe_wrap(fn) for fn in stage] for stage in namespace.get("STAGE_PATH_CONSTRAINTS", [])]

    return {
        "num_stages": num_stages,
        "stage_constraints": stage_constraints,
        "stage_path_constraints": stage_path_constraints,
        "stage_names": namespace.get("STAGE_NAMES", []),
        "stage_movable_mask": namespace.get("STAGE_MOVABLE_MASK", []),
        "stage_gripper_action": namespace.get("STAGE_GRIPPER_ACTION", []),
        "grasp_keypoints": namespace.get("grasp_keypoints", []),
        "release_keypoints": namespace.get("release_keypoints", []),
        "approach_elevation_deg": float(namespace.get("approach_elevation_deg", 90)),
        "gripper_roll_deg": float(namespace.get("gripper_roll_deg", 0)),
        "code_str": code,
    }


__all__ = ["generate_constraints"]
