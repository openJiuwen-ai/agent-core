# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``SubTaskExecutor`` running a full ReKep pipeline once per sub_task.

Registered as ``"so101_rekep"``. Owns all SO-101 hardware I/O directly
(RealSense capture, IK solve, robot/gripper drive -- formerly split out into a
separate ``So101ArmExecutor``, merged in here since nothing else used that
class independently) and adds, per ``execute()`` call: DINOv2+SAM keypoint
detection -> VLM constraint generation (scoped to *this one* sub_task's
description -- see ``constraint_generation.py`` for why) -> one numeric
subgoal solve -> IK + move. There is no further per-sub_task stage
decomposition: the outer agent-core model already emits one atomic action per
sub_task via ``report_plan``, so each ``execute()`` call computes exactly one
target and performs exactly one move.

This treats each ``report_plan`` sub_task as one atomic, fully-blocking
ReKep call: the outer agent-core model keeps sequencing/tracking sub_tasks
exactly as it does today; only the physical execution of *one* in_progress
sub_task is delegated wholesale to ReKep's own keypoint+VLM+solver stack.
``start_x``/``start_y`` are passed through only as a disambiguation hint to
the constraint-generation VLM, not used as a direct IK target. ``end_x``/
``end_y``, if present, are backprojected to 3D and appended as one more
numbered keypoint on the same overlay the VLM sees (see
``dest_keypoint_index`` in ``constraint_generation.py``) -- a destination the
outer planner pointed at, not one DINOv2/SAM detected, that the VLM can align
a held keypoint to.

Because "grasp" and "move the grasped object" are now separate sub_tasks
(and therefore separate ``execute()`` calls), the rigid-body assumption for
held keypoints (``_update_held_keypoints``) is tracked as instance state
(``self._ee_at_grasp``) across calls rather than within a single call: it is
set when a sub_task's ``gripper_action`` closes the gripper and cleared when
a later sub_task opens it.

Requires the ``robotic-arm-so101-rekep`` extra (``pip install
'openjiuwen[robotic-arm-so101-rekep]'``): ``ikpy``, ``pyrealsense2``,
``scipy``, plus the DINOv2/MobileSAM/VLM dependencies. ``lerobot`` is not
pinned as a dependency; install the version matching your rig separately.
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
    approach_from_elevation,
    backproject_frame,
    pixel_from_normalized,
)
from openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep.constraint_generation import generate_constraints
from openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep.keypoint_proposal import KeypointProposer
from openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep.subgoal_solver import solve_subgoal
from openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep.vlm_client import VlmClient

_DEFAULT_VLM_BASE_URL = "https://openrouter.ai/api/v1"


def _update_held_keypoints(
    initial_kps: np.ndarray,
    movable_mask: Sequence[bool],
    ee_at_grasp: Optional[np.ndarray],
    ee_now: np.ndarray,
) -> np.ndarray:
    """Rigidity assumption: keypoints held since ``ee_at_grasp`` translate rigidly with the EE."""
    kps = initial_kps.copy()
    if ee_at_grasp is None:
        return kps
    displacement = ee_now - ee_at_grasp
    for i, held in enumerate(movable_mask):
        if held:
            kps[i] = initial_kps[i] + displacement
    return kps


@SubTaskExecutorRegistry.register("so101_rekep")
class So101RekepExecutor:
    """Full ReKep pipeline per sub_task: SO-101 hardware I/O + keypoints + VLM constraints + single-subgoal solve/execute."""

    def __init__(
        self,
        *,
        workspace_min: Sequence[float],
        workspace_max: Sequence[float],
        camera_matrix_path: Optional[str] = None,
        depth_scale_path: Optional[str] = None,
        extrinsics_path: Optional[str] = None,
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
        pre_capture_hook: Optional[Callable[[], None]] = None,
        sam_checkpoint_path: Optional[str] = None,
        dino_model: str = "facebook/dinov2-with-registers-small",
        k_per_mask: int = 5,
        min_mask_pixels: int = 300,
        meanshift_bandwidth_m: float = 0.03,
        target_long_side: int = 756,
        vlm_api_key: Optional[str] = None,
        vlm_model: Optional[str] = None,
        vlm_base_url: str = _DEFAULT_VLM_BASE_URL,
        keypoint_proposer: Optional[KeypointProposer] = None,
        vlm_client: Optional[VlmClient] = None,
    ) -> None:
        if ik_solver is not None:
            self._ik = ik_solver
        elif urdf_path is not None:
            self._ik = IKSolver(urdf_path, tcp_offset_m)
        else:
            raise ValueError("either ik_solver or urdf_path is required")

        if camera_matrix_path is None or depth_scale_path is None or extrinsics_path is None:
            raise ValueError("camera_matrix_path/depth_scale_path/extrinsics_path are required")
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
        self._pre_capture_hook = pre_capture_hook

        self._pipeline: Any = None

        if keypoint_proposer is not None:
            self._keypoint_proposer = keypoint_proposer
        else:
            if sam_checkpoint_path is None:
                raise ValueError("sam_checkpoint_path is required unless keypoint_proposer is injected")
            self._keypoint_proposer = KeypointProposer(
                sam_checkpoint_path=sam_checkpoint_path,
                dino_model=dino_model,
                k_per_mask=k_per_mask,
                min_mask_pixels=min_mask_pixels,
                meanshift_bandwidth_m=meanshift_bandwidth_m,
                target_long_side=target_long_side,
                workspace_bounds=(workspace_min, workspace_max),
            )

        if vlm_client is not None:
            self._vlm = vlm_client
        else:
            if not vlm_api_key or not vlm_model:
                raise ValueError("vlm_api_key/vlm_model are required unless vlm_client is injected")
            self._vlm = VlmClient(api_key=vlm_api_key, model=vlm_model, base_url=vlm_base_url)

        self._last_points: Optional[np.ndarray] = None
        self._ee_at_grasp: Optional[np.ndarray] = None

    # -- SubTaskExecutor protocol -------------------------------------------------

    def capture(self) -> Image.Image:
        rgb, depth = self._read_frame()
        self._last_points = backproject_frame(depth, self._camera_matrix, self._depth_scale, self._extrinsics_cm)
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
        if self._last_points is None:
            return "Error: NoDepthFrame: capture() must run before execute()."

        rgb = np.asarray(frame)
        hint_pixel = None
        if sub_task.get("start_x") is not None and sub_task.get("start_y") is not None:
            hint_pixel = pixel_from_normalized(
                sub_task["start_x"], sub_task["start_y"], self._coordinate_scale, frame.width, frame.height
            )
        end_pixel = None
        if sub_task.get("end_x") is not None and sub_task.get("end_y") is not None:
            end_pixel = pixel_from_normalized(
                sub_task["end_x"], sub_task["end_y"], self._coordinate_scale, frame.width, frame.height
            )

        result = self._keypoint_proposer.get_keypoints(rgb, points=self._last_points, visualize=True)
        keypoints = result["keypoints_3d"]
        if keypoints is None or len(keypoints) == 0:
            return "Failed: no keypoints detected in the current frame."

        overlay = result["overlay"]
        dest_keypoint_index: Optional[int] = None
        if end_pixel is not None:
            dest_point = self._last_points[end_pixel[1], end_pixel[0]]
            if np.all(np.isfinite(dest_point)):
                keypoints = np.vstack([keypoints, dest_point[None, :]])
                pixels = np.vstack([result["pixels"], np.array([end_pixel])])
                overlay = KeypointProposer._overlay(rgb, pixels)
                dest_keypoint_index = len(keypoints) - 1

        description = sub_task.get("description", "")
        cg = generate_constraints(
            overlay,
            description,
            num_keypoints=len(keypoints),
            keypoints_3d=keypoints,
            ik_solver=self._ik,
            vlm=self._vlm,
            hint_pixel=hint_pixel,
            dest_keypoint_index=dest_keypoint_index,
        )

        mask = cg["movable_mask"] or [False] * len(keypoints)
        ee_now = self._ik.forward_kinematics(self._read_current_joints())
        kps_now = _update_held_keypoints(keypoints, mask, self._ee_at_grasp, ee_now)

        target_m, _cost = solve_subgoal(
            cg["constraints"],
            kps_now,
            workspace_min=self._workspace_min,
            workspace_max=self._workspace_max,
            initial_ee=ee_now,
            movable_mask=mask,
        )

        approach = approach_from_elevation(target_m, cg["approach_elevation_deg"])
        roll = np.deg2rad(cg["gripper_roll_deg"])
        ik_result = self._ik.solve(target_m, approach_dir=approach, roll_override=roll)
        err_cm = ik_result.error_m * 100.0
        if err_cm > self._ik_tolerance_cm:
            return f"Failed: IK error {err_cm:.1f} cm exceeds tolerance {self._ik_tolerance_cm} cm for '{description}'."

        gripper_state = cg["gripper_action"]
        gripper_val = self._gripper_closed_value if gripper_state == "closed" else self._gripper_open_value
        self._move_robot(ik_result.joints, gripper_val)

        ee_now = self._ik.forward_kinematics(ik_result.joints)
        if gripper_state == "closed":
            if self._ee_at_grasp is None:
                self._ee_at_grasp = ee_now
        else:
            self._ee_at_grasp = None

        return f"Completed '{description}'; final EE {np.round(ee_now * 100, 1)} cm."

    def _read_current_joints(self) -> np.ndarray:
        robot = self._ensure_robot()
        obs = robot.get_observation()
        q = np.zeros(7)
        q[1:6] = np.deg2rad(
            [
                obs["shoulder_pan.pos"],
                obs["shoulder_lift.pos"],
                obs["elbow_flex.pos"],
                obs["wrist_flex.pos"],
                obs["wrist_roll.pos"],
            ]
        )
        return self._ik.clamp_joints(q)

    # -- SO-101 hardware I/O (RealSense capture + robot/gripper drive) ----------

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


__all__ = ["So101RekepExecutor"]
