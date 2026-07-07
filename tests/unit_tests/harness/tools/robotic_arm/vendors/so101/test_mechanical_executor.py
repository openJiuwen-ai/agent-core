#!/usr/bin/env python
# coding: utf-8
"""Tests for So101ArmExecutor: control flow around workspace/IK-tolerance checks
and gripper-keyword inference. Hardware I/O (_read_frame/_move_robot) and the
IK solver are faked/monkeypatched so no RealSense/ikpy/lerobot dependency is
needed to run these.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image

from openjiuwen.core.common.exception.errors import ToolError
from openjiuwen.harness.tools.robotic_arm.registry import SubTaskExecutorRegistry
from openjiuwen.harness.tools.robotic_arm.vendors.so101._kinematics import IKResult
from openjiuwen.harness.tools.robotic_arm.vendors.so101.mechanical_executor import (
    So101ArmExecutor,
    infer_gripper_action,
)


class _FakeIKSolver:
    def __init__(self, error_m: float = 0.0) -> None:
        self.error_m = error_m
        self.calls: list = []

    def solve(self, target_m, *, approach_dir=None, roll_override=None):
        self.calls.append(target_m)
        return IKResult(joints=np.array([0, 0.1, 0.2, 0.3, 0.4, 0.5, 0]), error_m=self.error_m)

    def joints_to_action(self, joints):
        return {"shoulder_pan.pos": 0.0}


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


def _make_executor(calibration_files: dict, ik_solver=None, **overrides) -> So101ArmExecutor:
    kwargs = dict(
        **calibration_files,
        workspace_min=[-1.0, -1.0, -1.0],
        workspace_max=[1.0, 1.0, 1.0],
        ik_solver=ik_solver or _FakeIKSolver(error_m=0.0),
        ik_tolerance_cm=5.0,
    )
    kwargs.update(overrides)
    return So101ArmExecutor(**kwargs)


def test_registered_as_so101() -> None:
    assert SubTaskExecutorRegistry._registry.get("so101") is So101ArmExecutor


def test_requires_ik_solver_or_urdf_path(calibration_files: dict) -> None:
    with pytest.raises(ValueError, match="ik_solver or urdf_path"):
        So101ArmExecutor(**calibration_files, workspace_min=[-1, -1, -1], workspace_max=[1, 1, 1])


def test_capture_caches_depth_and_returns_rgb(calibration_files: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    executor = _make_executor(calibration_files)
    rgb = np.zeros((10, 10, 3), dtype=np.uint8)
    depth = np.full((10, 10), 1000, dtype=np.uint16)
    monkeypatch.setattr(executor, "_read_frame", lambda: (rgb, depth))

    frame = executor.capture()

    assert isinstance(frame, Image.Image)
    assert frame.size == (10, 10)
    assert executor._last_depth is depth


def test_execute_without_capture_reports_error(calibration_files: dict) -> None:
    executor = _make_executor(calibration_files)
    frame = Image.new("RGB", (640, 480))

    result = executor.execute(frame, {"description": "grasp the cup", "start_x": 500, "start_y": 500})

    assert "NoDepthFrame" in result


def test_execute_without_target_point_reports_error(calibration_files: dict) -> None:
    executor = _make_executor(calibration_files)
    executor._last_depth = np.full((480, 640), 1000, dtype=np.uint16)
    frame = Image.new("RGB", (640, 480))

    result = executor.execute(frame, {"description": "grasp the cup"})

    assert "NoTargetPoint" in result


def test_execute_reports_invalid_depth(calibration_files: dict) -> None:
    executor = _make_executor(calibration_files)
    executor._last_depth = np.zeros((480, 640), dtype=np.uint16)
    executor._move_robot = MagicMock()
    frame = Image.new("RGB", (640, 480))

    result = executor.execute(frame, {"description": "grasp the cup", "start_x": 500, "start_y": 500})

    assert "invalid depth" in result
    executor._move_robot.assert_not_called()


def test_execute_reports_out_of_workspace_target(calibration_files: dict) -> None:
    executor = _make_executor(calibration_files, workspace_min=[10, 10, 10], workspace_max=[20, 20, 20])
    executor._last_depth = np.full((480, 640), 1000, dtype=np.uint16)
    executor._move_robot = MagicMock()
    frame = Image.new("RGB", (640, 480))

    result = executor.execute(frame, {"description": "grasp the cup", "start_x": 500, "start_y": 500})

    assert "outside the workspace bounds" in result
    executor._move_robot.assert_not_called()


def test_execute_reports_ik_error_over_tolerance(calibration_files: dict) -> None:
    ik_solver = _FakeIKSolver(error_m=0.10)  # 10 cm, over the 5 cm tolerance
    executor = _make_executor(calibration_files, ik_solver=ik_solver, ik_tolerance_cm=5.0)
    executor._last_depth = np.full((480, 640), 1000, dtype=np.uint16)
    executor._move_robot = MagicMock()
    frame = Image.new("RGB", (640, 480))

    result = executor.execute(frame, {"description": "grasp the cup", "start_x": 500, "start_y": 500})

    assert "IK error" in result and "exceeds tolerance" in result
    executor._move_robot.assert_not_called()


def test_execute_moves_robot_and_infers_gripper_on_success(calibration_files: dict) -> None:
    executor = _make_executor(calibration_files)
    executor._last_depth = np.full((480, 640), 1000, dtype=np.uint16)
    executor._move_robot = MagicMock()
    frame = Image.new("RGB", (640, 480))

    result = executor.execute(frame, {"description": "grasp the cup", "start_x": 500, "start_y": 500})

    executor._move_robot.assert_called_once()
    _joints_arg, gripper_arg = executor._move_robot.call_args.args
    assert gripper_arg == executor._gripper_closed_value
    assert "Moved to" in result


def test_execute_wraps_unexpected_exception_as_tool_error(calibration_files: dict) -> None:
    executor = _make_executor(calibration_files)
    executor._last_depth = np.full((480, 640), 1000, dtype=np.uint16)
    executor._ik.solve = MagicMock(side_effect=RuntimeError("boom"))
    frame = Image.new("RGB", (640, 480))

    with pytest.raises(ToolError, match="boom"):
        executor.execute(frame, {"description": "grasp the cup", "start_x": 500, "start_y": 500})


@pytest.mark.parametrize(
    "description,expected",
    [
        ("please grasp the tape", "closed"),
        ("抓取盒子", "closed"),
        ("place it on the table", "open"),
        ("放置到桌子上", "open"),
        ("move to the left", None),
    ],
)
def test_infer_gripper_action(description: str, expected) -> None:
    assert infer_gripper_action(description) == expected
