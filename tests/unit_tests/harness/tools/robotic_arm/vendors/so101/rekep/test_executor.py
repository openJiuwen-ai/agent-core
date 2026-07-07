#!/usr/bin/env python
# coding: utf-8
"""Tests for So101RekepExecutor: the per-sub_task multi-stage control loop.

Hardware I/O and the IK solver are provided by a real So101ArmExecutor wired
up with fakes (mirroring test_mechanical_executor.py's fixtures); keypoint
detection and the VLM are injected fakes. Constraint generation and subgoal
solving run for real (against the fake VLM's canned code and fake IK solver)
so the stage loop is exercised end to end without any real hardware/network/
ML dependency.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image

from openjiuwen.harness.tools.robotic_arm.registry import SubTaskExecutorRegistry
from openjiuwen.harness.tools.robotic_arm.vendors.so101._kinematics import IKResult
from openjiuwen.harness.tools.robotic_arm.vendors.so101.mechanical_executor import So101ArmExecutor
from openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep.executor import So101RekepExecutor


class _FakeIKSolver:
    def __init__(self, error_m: float = 0.0) -> None:
        self.error_m = error_m
        self.solve_calls: list = []

    def solve(self, target_m, *, approach_dir=None, roll_override=None):
        self.solve_calls.append(np.asarray(target_m))
        return IKResult(joints=np.array([0, 0.1, 0.2, 0.3, 0.4, 0.5, 0]), error_m=self.error_m)

    def forward_kinematics(self, joints):
        return np.array([joints[1], joints[2], joints[3]])

    def clamp_joints(self, joints):
        return joints

    def joints_to_action(self, joints):
        return {
            "shoulder_pan.pos": float(joints[1]),
            "shoulder_lift.pos": float(joints[2]),
            "elbow_flex.pos": float(joints[3]),
            "wrist_flex.pos": float(joints[4]),
            "wrist_roll.pos": float(joints[5]),
        }


class _FakeRobot:
    def __init__(self) -> None:
        self.actions: list = []

    def get_observation(self):
        return {
            "shoulder_pan.pos": 0.0,
            "shoulder_lift.pos": 0.0,
            "elbow_flex.pos": 0.0,
            "wrist_flex.pos": 0.0,
            "wrist_roll.pos": 0.0,
        }

    def send_action(self, action):
        self.actions.append(action)


class _FakeKeypointProposer:
    def __init__(self, keypoints: np.ndarray) -> None:
        self.keypoints = keypoints

    def get_keypoints(self, rgb, points, visualize=True):
        return {"keypoints_3d": self.keypoints, "pixels": np.zeros((len(self.keypoints), 2)), "overlay": rgb}


class _FakeVlm:
    def __init__(self, response: str) -> None:
        self.response = response

    def query(self, prompt, image=None, max_tokens=3000):
        return self.response


@pytest.fixture()
def calibration_files(tmp_path: Path) -> dict:
    camera_matrix_path = tmp_path / "camera_matrix.npy"
    np.save(camera_matrix_path, np.array([[500.0, 0, 320.0], [0, 500.0, 240.0], [0, 0, 1]]))
    depth_scale_path = tmp_path / "depth_scale.npy"
    np.save(depth_scale_path, np.array([0.001]))
    extrinsics_path = tmp_path / "T_base_camera.npy"
    np.save(extrinsics_path, np.eye(4))
    return {
        "camera_matrix_path": str(camera_matrix_path),
        "depth_scale_path": str(depth_scale_path),
        "extrinsics_path": str(extrinsics_path),
    }


def _make_mechanical(calibration_files: dict, ik_solver=None, robot=None) -> So101ArmExecutor:
    return So101ArmExecutor(
        **calibration_files,
        workspace_min=[-1.0, -1.0, -1.0],
        workspace_max=[1.0, 1.0, 1.0],
        ik_solver=ik_solver or _FakeIKSolver(),
        robot=robot or _FakeRobot(),
        ik_tolerance_cm=5.0,
    )


_TWO_STAGE_RESPONSE = """
```python
num_stages = 2

def stage1_subgoal_constraint1(end_effector, keypoints):
    return float(np.linalg.norm(end_effector - keypoints[0]))

STAGE_CONSTRAINTS = [[stage1_subgoal_constraint1], [stage1_subgoal_constraint1]]
STAGE_PATH_CONSTRAINTS = [[], []]
STAGE_NAMES = ["approach", "grasp"]
STAGE_MOVABLE_MASK = [[False], [False]]
STAGE_GRIPPER_ACTION = ["open", "closed"]
grasp_keypoints = [0]
release_keypoints = []
approach_elevation_deg = 90
gripper_roll_deg = 0
```
"""


def test_registered_as_so101_rekep() -> None:
    assert SubTaskExecutorRegistry._registry.get("so101_rekep") is So101RekepExecutor


def test_capture_backprojects_full_frame_and_returns_rgb(calibration_files: dict) -> None:
    mechanical = _make_mechanical(calibration_files)
    rgb = np.zeros((480, 640, 3), dtype=np.uint8)
    depth = np.full((480, 640), 1000, dtype=np.uint16)
    mechanical._read_frame = MagicMock(return_value=(rgb, depth))

    executor = So101RekepExecutor(
        workspace_min=[-1.0, -1.0, -1.0],
        workspace_max=[1.0, 1.0, 1.0],
        mechanical_executor=mechanical,
        keypoint_proposer=_FakeKeypointProposer(np.zeros((1, 3))),
        vlm_client=_FakeVlm(_TWO_STAGE_RESPONSE),
    )

    frame = executor.capture()

    assert isinstance(frame, Image.Image)
    assert executor._last_points is not None
    assert executor._last_points.shape == (480, 640, 3)


def test_execute_without_capture_reports_error(calibration_files: dict) -> None:
    executor = So101RekepExecutor(
        workspace_min=[-1.0, -1.0, -1.0],
        workspace_max=[1.0, 1.0, 1.0],
        mechanical_executor=_make_mechanical(calibration_files),
        keypoint_proposer=_FakeKeypointProposer(np.zeros((1, 3))),
        vlm_client=_FakeVlm(_TWO_STAGE_RESPONSE),
    )

    result = executor.execute(Image.new("RGB", (640, 480)), {"description": "grasp the tape"})

    assert "NoDepthFrame" in result


def test_execute_runs_all_stages_on_success(calibration_files: dict) -> None:
    robot = _FakeRobot()
    mechanical = _make_mechanical(calibration_files, robot=robot)
    keypoints = np.array([[0.1, 0.0, 0.05]])
    executor = So101RekepExecutor(
        workspace_min=[-1.0, -1.0, -1.0],
        workspace_max=[1.0, 1.0, 1.0],
        mechanical_executor=mechanical,
        keypoint_proposer=_FakeKeypointProposer(keypoints),
        vlm_client=_FakeVlm(_TWO_STAGE_RESPONSE),
    )
    executor._last_points = np.zeros((480, 640, 3))
    frame = Image.new("RGB", (640, 480))

    result = executor.execute(frame, {"description": "grasp the tape", "start_x": 500, "start_y": 500})

    assert "Completed 2/2 stage(s)" in result
    assert len(robot.actions) == 2
    assert robot.actions[0]["gripper.pos"] == executor._gripper_open_value
    assert robot.actions[1]["gripper.pos"] == executor._gripper_closed_value


def test_execute_no_keypoints_detected_reports_failure(calibration_files: dict) -> None:
    executor = So101RekepExecutor(
        workspace_min=[-1.0, -1.0, -1.0],
        workspace_max=[1.0, 1.0, 1.0],
        mechanical_executor=_make_mechanical(calibration_files),
        keypoint_proposer=_FakeKeypointProposer(np.zeros((0, 3))),
        vlm_client=_FakeVlm(_TWO_STAGE_RESPONSE),
    )
    executor._last_points = np.zeros((480, 640, 3))

    result = executor.execute(Image.new("RGB", (640, 480)), {"description": "grasp the tape"})

    assert "no keypoints detected" in result


def test_execute_stops_early_on_ik_failure(calibration_files: dict) -> None:
    robot = _FakeRobot()
    ik_solver = _FakeIKSolver(error_m=0.10)  # 10 cm > 5 cm tolerance -> first stage already fails
    mechanical = _make_mechanical(calibration_files, ik_solver=ik_solver, robot=robot)
    executor = So101RekepExecutor(
        workspace_min=[-1.0, -1.0, -1.0],
        workspace_max=[1.0, 1.0, 1.0],
        mechanical_executor=mechanical,
        keypoint_proposer=_FakeKeypointProposer(np.array([[0.1, 0.0, 0.05]])),
        vlm_client=_FakeVlm(_TWO_STAGE_RESPONSE),
    )
    executor._last_points = np.zeros((480, 640, 3))

    result = executor.execute(Image.new("RGB", (640, 480)), {"description": "grasp the tape"})

    assert "stopped at stage 1/2" in result
    assert len(robot.actions) == 0


def test_max_stages_caps_the_loop(calibration_files: dict) -> None:
    robot = _FakeRobot()
    mechanical = _make_mechanical(calibration_files, robot=robot)
    executor = So101RekepExecutor(
        workspace_min=[-1.0, -1.0, -1.0],
        workspace_max=[1.0, 1.0, 1.0],
        mechanical_executor=mechanical,
        keypoint_proposer=_FakeKeypointProposer(np.array([[0.1, 0.0, 0.05]])),
        vlm_client=_FakeVlm(_TWO_STAGE_RESPONSE),
        max_stages=1,
    )
    executor._last_points = np.zeros((480, 640, 3))

    result = executor.execute(Image.new("RGB", (640, 480)), {"description": "grasp the tape"})

    assert "Completed 1/1 stage(s)" in result
    assert len(robot.actions) == 1
