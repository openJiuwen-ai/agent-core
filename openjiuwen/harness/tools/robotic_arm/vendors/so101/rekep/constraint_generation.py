# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""VLM constraint-code generation for a single ReKep sub-task.

Ported from a user-supplied ReKep-on-SO101 reference implementation
(``run_record_skills/core/constraint_generation.py``), with two changes:

1. ``task`` is one ``report_plan`` sub_task's ``description`` (e.g. "grasp the
   tape"), not the whole multi-object goal, and it is already atomic -- the
   outer agent-core model owns ALL sequencing/decomposition across sub-tasks
   (see ``rekep/executor.py``), so this only asks the VLM for a single
   subgoal (one set of constraints, one gripper action) rather than further
   splitting the action into its own approach/grasp stages.
2. The generated code is executed through
   :func:`~.rekep._code_sandbox.safe_exec_constraint_code` (AST-whitelisted,
   restricted builtins) instead of a bare ``exec()``.

As in the reference implementation, every grasp/placement location the VLM
needs must be one of the DINOv2/SAM-detected keypoints on ``overlay_rgb`` --
there is no mechanism for pointing at an arbitrary pixel outside that set.
"""

from __future__ import annotations

from typing import Any

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
writing Python constraint functions. This action is already a single step out
of a larger plan tracked by another system -- do NOT split it into further
sub-steps (no separate "approach" then "grasp"); compute ONE target end-effector
position that satisfies this action.

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

1. Write constraint function(s):
   fn(end_effector: np.ndarray[3], keypoints: np.ndarray[K,3]) -> float
   cost <= 0 means satisfied. Lower is better.

   You have access to check_reachability(point_m, elevation_deg, roll_deg)
   which returns {"reachable": bool, "ik_error_cm": float}. Use it to verify
   approach points before committing to them. Report if you cannot use it.

2. MOVABLE_MASK: list of bool length K. True = that keypoint is already held
   by the gripper and moves rigidly with it (e.g. this action moves an
   object grasped by an earlier action).
3. GRIPPER_ACTION: "open" or "closed" -- the gripper state this action should
   end in.
4. approach_elevation_deg: 90 = straight down, 45 = diagonal, 0 = horizontal.
5. gripper_roll_deg: rotation of the gripper fingers around the approach axis
   (0 = default, 90 = good for cylindrical objects).

## Rules
- Each constraint takes end_effector [3] and keypoints [K,3], returns float.
- No if-statements, no loops, no imports. Use np.linalg.norm, abs, etc. only.
- All positions in METRES, robot base frame.
- If this action grasps an object: ONE subgoal constraint aligning EE with the
  grasp keypoint.
- The image shows colored arrows for the robot's coordinate axes:
  RED = +X (forward), GREEN = +Y (left facing the robot), BLUE = +Z (up).
  Origin is the robot base center.

## Output ONLY a Python code block:

```python
def subgoal_constraint1(end_effector, keypoints):
    ...

SUBGOAL_CONSTRAINTS = [...]
MOVABLE_MASK         = [False] * K
GRIPPER_ACTION       = "open"

grasp_keypoints   = [keypoint_index_to_grasp]
release_keypoints = [keypoint_index_to_release]

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
    *,
    num_keypoints: int,
    keypoints_3d: np.ndarray,
    ik_solver: IKSolver,
    vlm: VlmClient,
    max_tokens: int = 3000,
) -> dict[str, Any]:
    """Query the VLM to translate ``task`` (one sub_task's description) into
    a single subgoal's constraint functions, executed via the AST-sandboxed exec path.

    The VLM picks the grasp/placement location itself from ``overlay_rgb`` (the
    numbered-keypoint image) and ``task`` -- there is no outer-planner hint pixel,
    and every location it needs must be one of the ``num_keypoints`` detected
    keypoints (matching the reference implementation's behavior).
    """
    kp_report = check_keypoint_reachability(keypoints_3d, ik_solver)
    report_str = reachability_report_str(kp_report)

    def _check_reachability(point_m: np.ndarray, elevation_deg: float = 90, roll_deg: float = 0) -> dict:
        return check_reachability(point_m, ik_solver, elevation_deg, roll_deg)

    prompt = (
        f"{_CONSTRAINT_PROMPT}\n\n"
        f'Action: "{task}"\n'
        f"Number of keypoints in image: {num_keypoints}\n"
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

    subgoal_constraints = namespace.get("SUBGOAL_CONSTRAINTS")
    if subgoal_constraints is None:
        raise ValueError("VLM output is missing SUBGOAL_CONSTRAINTS")

    return {
        "constraints": [_safe_wrap(fn) for fn in subgoal_constraints],
        "movable_mask": namespace.get("MOVABLE_MASK", []),
        "gripper_action": namespace.get("GRIPPER_ACTION", "open"),
        "grasp_keypoints": namespace.get("grasp_keypoints", []),
        "release_keypoints": namespace.get("release_keypoints", []),
        "approach_elevation_deg": float(namespace.get("approach_elevation_deg", 90)),
        "gripper_roll_deg": float(namespace.get("gripper_roll_deg", 0)),
        "code_str": code,
    }


__all__ = ["generate_constraints"]
