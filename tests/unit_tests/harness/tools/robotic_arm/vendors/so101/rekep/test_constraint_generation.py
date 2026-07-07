#!/usr/bin/env python
# coding: utf-8
"""Tests for generate_constraints: prompt assembly + parsing the VLM's code
block through the AST-sandboxed exec path (no real OpenRouter/VLM call).
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pytest

from openjiuwen.harness.tools.robotic_arm.vendors.so101._kinematics import IKResult
from openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep.constraint_generation import generate_constraints


class _FakeIKSolver:
    def solve(self, target_m, *, approach_dir=None, roll_override=None):
        return IKResult(joints=np.zeros(7), error_m=float(np.linalg.norm(target_m)) * 0.01)


class _FakeVlm:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_prompt: Optional[str] = None
        self.last_image = None

    def query(self, prompt: str, image=None, max_tokens: int = 3000) -> str:
        self.last_prompt = prompt
        self.last_image = image
        return self.response


_VALID_RESPONSE = """
Here is my reasoning about the object geometry...

```python
def subgoal_constraint1(end_effector, keypoints):
    \"\"\"EE at keypoint 0.\"\"\"
    return float(np.linalg.norm(end_effector - keypoints[0]))

SUBGOAL_CONSTRAINTS = [subgoal_constraint1]
MOVABLE_MASK = [False]
GRIPPER_ACTION = "closed"
grasp_keypoints = [0]
release_keypoints = []
approach_elevation_deg = 45
gripper_roll_deg = 90
```
"""


def test_generate_constraints_parses_valid_vlm_response() -> None:
    vlm = _FakeVlm(_VALID_RESPONSE)
    keypoints = np.array([[0.1, 0.0, 0.05]])

    result = generate_constraints(
        overlay_rgb=np.zeros((10, 10, 3), dtype=np.uint8),
        task="grasp the tape",
        num_keypoints=1,
        keypoints_3d=keypoints,
        ik_solver=_FakeIKSolver(),
        vlm=vlm,
    )

    assert result["gripper_action"] == "closed"
    assert result["movable_mask"] == [False]
    assert result["approach_elevation_deg"] == 45.0
    assert result["gripper_roll_deg"] == 90.0
    assert result["grasp_keypoints"] == [0]

    fn = result["constraints"][0]
    assert fn(keypoints[0], keypoints) == pytest.approx(0.0, abs=1e-6)


def test_generate_constraints_includes_task_and_hint_in_prompt() -> None:
    vlm = _FakeVlm(_VALID_RESPONSE)
    keypoints = np.array([[0.1, 0.0, 0.05]])

    generate_constraints(
        overlay_rgb=np.zeros((10, 10, 3), dtype=np.uint8),
        task="grasp the tape",
        num_keypoints=1,
        keypoints_3d=keypoints,
        ik_solver=_FakeIKSolver(),
        vlm=vlm,
        hint_pixel=(320, 240),
    )

    assert vlm.last_prompt is not None
    assert "grasp the tape" in vlm.last_prompt
    assert "(320, 240)" in vlm.last_prompt


def test_generate_constraints_includes_dest_keypoint_in_prompt() -> None:
    vlm = _FakeVlm(_VALID_RESPONSE)
    keypoints = np.array([[0.1, 0.0, 0.05], [0.2, 0.1, 0.05]])

    generate_constraints(
        overlay_rgb=np.zeros((10, 10, 3), dtype=np.uint8),
        task="place the tape on the book",
        num_keypoints=2,
        keypoints_3d=keypoints,
        ik_solver=_FakeIKSolver(),
        vlm=vlm,
        dest_keypoint_index=1,
    )

    assert vlm.last_prompt is not None
    assert "Keypoint 1" in vlm.last_prompt
    assert "keypoints[1]" in vlm.last_prompt


def test_generate_constraints_rejects_malicious_vlm_code() -> None:
    vlm = _FakeVlm("```python\nimport os\nos.system('echo pwned')\n```")

    with pytest.raises(ValueError):
        generate_constraints(
            overlay_rgb=np.zeros((10, 10, 3), dtype=np.uint8),
            task="grasp the tape",
            num_keypoints=1,
            keypoints_3d=np.array([[0.1, 0.0, 0.05]]),
            ik_solver=_FakeIKSolver(),
            vlm=vlm,
        )


def test_generate_constraints_requires_subgoal_constraints() -> None:
    vlm = _FakeVlm("```python\nMOVABLE_MASK = []\n```")

    with pytest.raises(ValueError, match="SUBGOAL_CONSTRAINTS"):
        generate_constraints(
            overlay_rgb=np.zeros((10, 10, 3), dtype=np.uint8),
            task="grasp the tape",
            num_keypoints=1,
            keypoints_3d=np.array([[0.1, 0.0, 0.05]]),
            ik_solver=_FakeIKSolver(),
            vlm=vlm,
        )
