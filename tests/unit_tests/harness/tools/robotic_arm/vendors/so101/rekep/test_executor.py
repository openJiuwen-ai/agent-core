#!/usr/bin/env python
# coding: utf-8
"""Tests for So101RekepExecutor: SO-101 hardware I/O + the per-sub_task single-subgoal control step.

The IK solver and robot are injected fakes (``ik_solver=``/``robot=`` -- the
same injection points ``So101ArmExecutor`` used to expose before it was merged
into this class); keypoint detection and the VLM are injected fakes too.
Constraint generation and subgoal solving run for real (against the fake
VLM's canned code and fake IK solver) so one execute() call is exercised end
to end without any real hardware/network/ML dependency.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from openjiuwen.core.common.exception.errors import ToolError
from openjiuwen.harness.tools.robotic_arm.registry import SubTaskExecutorRegistry
from openjiuwen.harness.tools.robotic_arm.vendors.so101._kinematics import IKResult
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
        return {
            "keypoints_3d": self.keypoints,
            "pixels": np.zeros((len(self.keypoints), 2)),
            "overlay": rgb,
            "segmentation_overlay": rgb,
        }


class _FakeVlm:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_prompt: str | None = None

    def query(self, prompt, image=None, max_tokens=3000):
        self.last_prompt = prompt
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


def _make_executor(
    calibration_files: dict,
    *,
    ik_solver=None,
    robot=None,
    keypoint_proposer=None,
    vlm_client=None,
    ik_tolerance_cm: float = 5.0,
) -> So101RekepExecutor:
    return So101RekepExecutor(
        **calibration_files,
        workspace_min=[-1.0, -1.0, -1.0],
        workspace_max=[1.0, 1.0, 1.0],
        ik_solver=ik_solver or _FakeIKSolver(),
        robot=robot or _FakeRobot(),
        ik_tolerance_cm=ik_tolerance_cm,
        keypoint_proposer=keypoint_proposer or _FakeKeypointProposer(np.zeros((1, 3))),
        vlm_client=vlm_client or _FakeVlm(_GRASP_RESPONSE),
    )


_GRASP_RESPONSE = """
```python
def subgoal_constraint1(end_effector, keypoints):
    return float(np.linalg.norm(end_effector - keypoints[0]))

SUBGOAL_CONSTRAINTS = [subgoal_constraint1]
MOVABLE_MASK = [False]
GRIPPER_ACTION = "closed"
grasp_keypoints = [0]
release_keypoints = []
approach_elevation_deg = 90
gripper_roll_deg = 0
```
"""

_PLACE_RESPONSE = """
```python
def subgoal_constraint1(end_effector, keypoints):
    return float(np.linalg.norm(end_effector - keypoints[0]))

SUBGOAL_CONSTRAINTS = [subgoal_constraint1]
MOVABLE_MASK = [True]
GRIPPER_ACTION = "open"
grasp_keypoints = []
release_keypoints = [0]
approach_elevation_deg = 90
gripper_roll_deg = 0
```
"""


def test_registered_as_so101_rekep() -> None:
    assert SubTaskExecutorRegistry._registry.get("so101_rekep") is So101RekepExecutor


def test_requires_ik_solver_or_urdf_path(calibration_files: dict) -> None:
    with pytest.raises(ValueError, match="ik_solver or urdf_path"):
        So101RekepExecutor(
            **calibration_files,
            workspace_min=[-1.0, -1.0, -1.0],
            workspace_max=[1.0, 1.0, 1.0],
            keypoint_proposer=_FakeKeypointProposer(np.zeros((1, 3))),
            vlm_client=_FakeVlm(_GRASP_RESPONSE),
        )


def test_capture_backprojects_full_frame_and_returns_rgb(calibration_files: dict) -> None:
    executor = _make_executor(calibration_files, keypoint_proposer=_FakeKeypointProposer(np.zeros((1, 3))))
    rgb = np.zeros((480, 640, 3), dtype=np.uint8)
    depth = np.full((480, 640), 1000, dtype=np.uint16)
    executor._retry_capture = MagicMock(return_value=(rgb, depth))

    frame = executor.capture()

    assert isinstance(frame, Image.Image)
    assert executor._last_points is not None
    assert executor._last_points.shape == (480, 640, 3)


def test_retry_capture_succeeds_after_one_failed_attempt(calibration_files: dict) -> None:
    executor = _make_executor(calibration_files)
    executor.reset_camera = MagicMock()
    rgb = np.zeros((2, 2, 3), dtype=np.uint8)
    depth = np.zeros((2, 2), dtype=np.uint16)
    executor._read_frame = MagicMock(side_effect=[(None, None), (rgb, depth)])

    with patch("openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep.executor.time.sleep"):
        result_rgb, result_depth = executor._retry_capture(max_retry_attempt=3)

    assert result_rgb is rgb
    assert result_depth is depth
    assert executor.reset_camera.call_count == 2
    assert executor._read_frame.call_count == 2


def test_retry_capture_raises_after_exhausting_attempts(calibration_files: dict) -> None:
    executor = _make_executor(calibration_files)
    executor.reset_camera = MagicMock()
    executor._read_frame = MagicMock(return_value=(None, None))

    with patch("openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep.executor.time.sleep"):
        with pytest.raises(ToolError, match="could not capture"):
            executor._retry_capture(max_retry_attempt=2)

    assert executor.reset_camera.call_count == 2
    assert executor._read_frame.call_count == 2


def test_execute_without_capture_reports_error(calibration_files: dict) -> None:
    executor = _make_executor(calibration_files)

    result = executor.execute(Image.new("RGB", (640, 480)), {"description": "grasp the tape"})

    assert "NoDepthFrame" in result


def test_execute_wraps_unexpected_exception_as_tool_error(calibration_files: dict) -> None:
    executor = _make_executor(calibration_files)
    executor._last_points = np.zeros((480, 640, 3))
    executor._keypoint_proposer.get_keypoints = MagicMock(side_effect=RuntimeError("boom"))

    with pytest.raises(ToolError, match="boom"):
        executor.execute(Image.new("RGB", (640, 480)), {"description": "grasp the tape"})


def test_execute_runs_single_subgoal_on_success(calibration_files: dict) -> None:
    robot = _FakeRobot()
    keypoints = np.array([[0.1, 0.0, 0.05]])
    executor = _make_executor(calibration_files, robot=robot, keypoint_proposer=_FakeKeypointProposer(keypoints))
    executor._last_points = np.zeros((480, 640, 3))
    frame = Image.new("RGB", (640, 480))

    result = executor.execute(frame, {"description": "grasp the tape"})

    assert "Completed 'grasp the tape'" in result
    assert len(robot.actions) == 1
    assert robot.actions[0]["gripper.pos"] == executor._gripper_closed_value


def test_execute_persists_ee_at_grasp_across_calls(calibration_files: dict) -> None:
    """'grasp' and 'move the grasped object' are now separate execute() calls
    (separate report_plan sub_tasks); the rigid-body hold assumption must
    survive between them via instance state rather than a within-call stage loop."""
    robot = _FakeRobot()
    keypoints = np.array([[0.1, 0.0, 0.05]])
    executor = _make_executor(calibration_files, robot=robot, keypoint_proposer=_FakeKeypointProposer(keypoints))
    executor._last_points = np.zeros((480, 640, 3))
    frame = Image.new("RGB", (640, 480))

    executor.execute(frame, {"description": "grasp the tape"})
    assert executor._ee_at_grasp is not None

    executor._vlm.response = _PLACE_RESPONSE
    executor.execute(frame, {"description": "place the tape on the book"})
    assert executor._ee_at_grasp is None
    assert len(robot.actions) == 2
    assert robot.actions[1]["gripper.pos"] == executor._gripper_open_value


def test_execute_no_keypoints_detected_reports_failure(calibration_files: dict) -> None:
    executor = _make_executor(calibration_files, keypoint_proposer=_FakeKeypointProposer(np.zeros((0, 3))))
    executor._last_points = np.zeros((480, 640, 3))

    result = executor.execute(Image.new("RGB", (640, 480)), {"description": "grasp the tape"})

    assert "no keypoints detected" in result


def test_execute_stops_on_ik_failure(calibration_files: dict) -> None:
    robot = _FakeRobot()
    ik_solver = _FakeIKSolver(error_m=0.10)  # 10 cm > 5 cm tolerance -> fails
    executor = _make_executor(
        calibration_files,
        ik_solver=ik_solver,
        robot=robot,
        keypoint_proposer=_FakeKeypointProposer(np.array([[0.1, 0.0, 0.05]])),
    )
    executor._last_points = np.zeros((480, 640, 3))

    result = executor.execute(Image.new("RGB", (640, 480)), {"description": "grasp the tape"})

    assert "IK error" in result
    assert len(robot.actions) == 0


def test_execute_populates_last_debug_on_success(calibration_files: dict) -> None:
    keypoints = np.array([[0.1, 0.0, 0.05]])
    executor = _make_executor(calibration_files, keypoint_proposer=_FakeKeypointProposer(keypoints))
    executor._last_points = np.zeros((480, 640, 3))
    frame = Image.new("RGB", (640, 480))

    executor.execute(frame, {"description": "grasp the tape"})

    assert executor.last_debug is not None
    assert executor.last_debug["gripper_action"] == "closed"
    assert executor.last_debug["keypoints_3d"] == keypoints.tolist()
    assert executor.last_debug["keypoints_pixels"] == np.zeros((1, 2)).tolist()
    assert executor.last_debug["movable_mask"] == [False]
    assert executor.last_debug["ik_error_cm"] == pytest.approx(0.0)
    # the VLM's raw constraint code, plus which keypoint indices it grasped/released.
    assert "def subgoal_constraint1" in executor.last_debug["constraint_code"]
    assert executor.last_debug["grasp_keypoints"] == [0]
    assert executor.last_debug["release_keypoints"] == []
    # overlay must be JPEG-encoded, not the raw numpy array, so it's JSON/wire-safe.
    assert isinstance(executor.last_debug["overlay_image_base64"], str)
    assert executor.last_debug["overlay_image_base64"]
    assert isinstance(executor.last_debug["segmentation_image_base64"], str)
    assert executor.last_debug["segmentation_image_base64"]


def test_execute_populates_last_debug_on_ik_failure(calibration_files: dict) -> None:
    """Grounding data (which keypoint was picked, why) is still useful when the move itself fails."""
    ik_solver = _FakeIKSolver(error_m=0.10)  # 10 cm > 5 cm tolerance -> fails
    executor = _make_executor(
        calibration_files,
        ik_solver=ik_solver,
        keypoint_proposer=_FakeKeypointProposer(np.array([[0.1, 0.0, 0.05]])),
    )
    executor._last_points = np.zeros((480, 640, 3))

    result = executor.execute(Image.new("RGB", (640, 480)), {"description": "grasp the tape"})

    assert "IK error" in result
    assert executor.last_debug is not None
    assert executor.last_debug["ik_error_cm"] == pytest.approx(10.0)


def test_last_debug_is_none_before_first_execute(calibration_files: dict) -> None:
    executor = _make_executor(calibration_files)

    assert executor.last_debug is None
