# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``SubTaskExecutor`` for an SO-101 + RealSense rig: mechanical execution only.

Converts ``report_plan``'s normalized ``start_x``/``start_y`` directly into a
physical IK target (RealSense capture -> depth backprojection -> IK -> move +
gripper). Does its own no keypoint detection or VLM reasoning -- the model's
own vision (via ``VisionPerceptionRail``) already decided *where* to point;
this class only turns that point into a joint-space motion. For ReKep-style
per-sub-task multi-stage geometric reasoning (DINOv2+SAM keypoints + VLM
constraint generation + subgoal solving), see ``rekep.executor.So101RekepExecutor``
(registered as ``"so101_rekep"``) instead.

Requires the ``robotic-arm-so101`` extra (``pip install
'openjiuwen[robotic-arm-so101]'``): ``ikpy``, ``pyrealsense2``, ``scipy``.
``lerobot`` is not pinned as a dependency; install the version matching your
rig separately.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

import numpy as np
from PIL import Image

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ToolError
from openjiuwen.harness.tools.robotic_arm.registry import SubTaskExecutorRegistry
from openjiuwen.harness.tools.robotic_arm.vendors.so101._kinematics import (
    IKSolver,
    backproject_pixel,
    in_workspace,
    pixel_from_normalized,
)

_DEFAULT_GRASP_KEYWORDS = ("grasp", "pick", "grip", "抓取", "夹住", "抓住", "拿起")
_DEFAULT_RELEASE_KEYWORDS = ("release", "place", "drop", "放置", "松开", "放开", "释放")


def infer_gripper_action(
    description: str,
    grasp_keywords: Sequence[str] = _DEFAULT_GRASP_KEYWORDS,
    release_keywords: Sequence[str] = _DEFAULT_RELEASE_KEYWORDS,
) -> Optional[str]:
    """Guess ``"closed"``/``"open"``/``None`` (no change) from a sub_task description.

    ``report_plan``'s schema has no dedicated gripper field, so this is a
    keyword heuristic rather than a model decision.
    """
    text = (description or "").lower()
    if any(k in text for k in grasp_keywords):
        return "closed"
    if any(k in text for k in release_keywords):
        return "open"
    return None


@SubTaskExecutorRegistry.register("so101")
class So101ArmExecutor:
    """Mechanical-only ``SubTaskExecutor``: RealSense capture + backprojection + IK + move."""

    def __init__(
        self,
        *,
        camera_matrix_path: str,
        depth_scale_path: str,
        extrinsics_path: str,
        workspace_min: Sequence[float],
        workspace_max: Sequence[float],
        urdf_path: Optional[str] = None,
        ik_solver: Optional[IKSolver] = None,
        tcp_offset_m: Sequence[float] = (0.0, 0.0, 0.0, 1.0),
        port: Optional[str] = None,
        robot: Optional[Any] = None,
        gripper_open_value: float = 50.0,
        gripper_closed_value: float = 0.0,
        ik_tolerance_cm: float = 5.0,
        coordinate_scale: int = 1000,
        color_width: int = 1280,
        color_height: int = 720,
        fps: int = 6,
        grasp_keywords: Sequence[str] = _DEFAULT_GRASP_KEYWORDS,
        release_keywords: Sequence[str] = _DEFAULT_RELEASE_KEYWORDS,
        pre_capture_hook: Optional[Callable[[], None]] = None,
    ) -> None:
        if ik_solver is not None:
            self._ik = ik_solver
        elif urdf_path is not None:
            self._ik = IKSolver(urdf_path, tcp_offset_m)
        else:
            raise ValueError("either ik_solver or urdf_path is required")

        self._camera_matrix = np.load(camera_matrix_path)
        self._depth_scale = float(np.load(depth_scale_path)[0])
        self._extrinsics_cm = np.load(extrinsics_path)
        self._workspace_min = np.asarray(workspace_min, dtype=float)
        self._workspace_max = np.asarray(workspace_max, dtype=float)

        self._port = port
        self._robot = robot
        self._gripper_open_value = gripper_open_value
        self._gripper_closed_value = gripper_closed_value
        self._ik_tolerance_cm = ik_tolerance_cm
        self._coordinate_scale = coordinate_scale
        self._color_width = color_width
        self._color_height = color_height
        self._fps = fps
        self._grasp_keywords = tuple(grasp_keywords)
        self._release_keywords = tuple(release_keywords)
        self._pre_capture_hook = pre_capture_hook

        self._pipeline: Any = None
        self._last_depth: Optional[np.ndarray] = None

    # -- SubTaskExecutor protocol ---------------------------------------------

    def capture(self) -> Image.Image:
        rgb, depth = self._read_frame()
        self._last_depth = depth
        return Image.fromarray(rgb)

    def execute(self, frame: Any, sub_task: dict) -> str:
        try:
            return self._execute(frame, sub_task)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(StatusCode.TOOL_EXECUTION_ERROR, reason=str(e)) from e

    # -- internal --------------------------------------------------------------

    def _execute(self, frame: Any, sub_task: dict) -> str:
        if self._last_depth is None:
            return "Error: NoDepthFrame: capture() must run before execute()."
        if sub_task.get("start_x") is None or sub_task.get("start_y") is None:
            return "Error: NoTargetPoint: sub_task has no start_x/start_y."

        px, py = pixel_from_normalized(
            sub_task["start_x"], sub_task["start_y"], self._coordinate_scale, frame.width, frame.height
        )
        target_m = backproject_pixel(
            px, py, self._last_depth, self._camera_matrix, self._depth_scale, self._extrinsics_cm
        )
        if target_m is None:
            return f"Failed: invalid depth reading at pixel ({px}, {py})."
        if not in_workspace(target_m, self._workspace_min, self._workspace_max):
            return f"Failed: target {np.round(target_m * 100, 1)} cm is outside the workspace bounds."

        ik_result = self._ik.solve(target_m)
        err_cm = ik_result.error_m * 100.0
        if err_cm > self._ik_tolerance_cm:
            return (
                f"Failed: IK error {err_cm:.1f} cm exceeds tolerance {self._ik_tolerance_cm} cm "
                f"for target {np.round(target_m * 100, 1)} cm."
            )

        gripper_action = infer_gripper_action(
            sub_task.get("description", ""), self._grasp_keywords, self._release_keywords
        )
        gripper_val = {
            "closed": self._gripper_closed_value,
            "open": self._gripper_open_value,
        }.get(gripper_action)
        self._move_robot(ik_result.joints, gripper_val)

        gripper_note = f", gripper={gripper_action}" if gripper_action else ""
        return f"Moved to {np.round(target_m * 100, 1)} cm (IK error {err_cm:.1f} cm){gripper_note}."

    def _read_frame(self) -> tuple[np.ndarray, np.ndarray]:
        if self._pre_capture_hook is not None:
            self._pre_capture_hook()
        if self._pipeline is None:
            self._pipeline = self._open_pipeline()

        import pyrealsense2 as rs

        align = rs.align(rs.stream.color)
        frames = align.process(self._pipeline.wait_for_frames(5000))
        depth = np.asarray(frames.get_depth_frame().get_data())
        color_bgra = np.asarray(frames.get_color_frame().get_data())
        rgb = color_bgra[..., [2, 1, 0]]  # BGRA -> RGB, no opencv dependency needed
        return rgb, depth

    def _open_pipeline(self) -> Any:
        try:
            import pyrealsense2 as rs
        except ImportError as e:
            raise ImportError(
                f"pyrealsense2 is not installed; run `pip install 'openjiuwen[robotic-arm-so101]'` ({e})"
            ) from e
        pipeline = rs.pipeline()
        config = rs.config()
        config.enable_stream(rs.stream.color, self._color_width, self._color_height, rs.format.bgra8, self._fps)
        config.enable_stream(rs.stream.depth, self._color_width, self._color_height, rs.format.z16, self._fps)
        pipeline.start(config)
        return pipeline

    def _ensure_robot(self) -> Any:
        if self._robot is not None:
            return self._robot
        try:
            from lerobot.robots.so101_follower import SO101Follower, SO101FollowerConfig
        except ImportError as e:
            raise ImportError(f"lerobot is not installed ({e})") from e
        if self._port is None:
            raise ValueError("either robot or port is required")
        self._robot = SO101Follower(SO101FollowerConfig(port=self._port, id="agent_core_so101"))
        self._robot.connect(calibrate=False)
        return self._robot

    def _move_robot(self, joints: np.ndarray, gripper_val: Optional[float]) -> None:
        action = self._ik.joints_to_action(joints)
        if gripper_val is not None:
            action["gripper.pos"] = float(gripper_val)
        self._ensure_robot().send_action(action)


__all__ = ["So101ArmExecutor", "infer_gripper_action"]
