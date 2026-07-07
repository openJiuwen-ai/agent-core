# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``SubTaskExecutor`` running a full ReKep pipeline once per sub_task.

Registered as ``"so101_rekep"``. Reuses :class:`So101ArmExecutor` for all
hardware I/O (RealSense capture, IK solve, robot/gripper drive) and adds, per
``execute()`` call: DINOv2+SAM keypoint detection -> VLM constraint
generation (scoped to *this one* sub_task's description -- see
``constraint_generation.py`` for why) -> per-stage numeric subgoal solving ->
IK + move, looping over however many stages the VLM decided this one action
needs (capped by ``max_stages``).

This treats each ``report_plan`` sub_task as one atomic, fully-blocking
ReKep call: the outer agent-core model keeps sequencing/tracking sub_tasks
exactly as it does today; only the physical execution of *one* in_progress
sub_task is delegated wholesale to ReKep's own keypoint+VLM+solver stack.
``start_x``/``start_y`` are passed through only as a disambiguation hint to
the constraint-generation VLM, not used as a direct IK target the way
``So101ArmExecutor`` uses them.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

import numpy as np
from PIL import Image

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ToolError
from openjiuwen.harness.tools.robotic_arm.registry import SubTaskExecutorRegistry
from openjiuwen.harness.tools.robotic_arm.vendors.so101._kinematics import (
    approach_from_elevation,
    backproject_frame,
    pixel_from_normalized,
)
from openjiuwen.harness.tools.robotic_arm.vendors.so101.mechanical_executor import So101ArmExecutor
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
    """Full ReKep pipeline per sub_task: keypoints + VLM constraints + multi-stage solve/execute."""

    def __init__(
        self,
        *,
        workspace_min: Sequence[float],
        workspace_max: Sequence[float],
        camera_matrix_path: Optional[str] = None,
        depth_scale_path: Optional[str] = None,
        extrinsics_path: Optional[str] = None,
        urdf_path: Optional[str] = None,
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
        sam_checkpoint_path: Optional[str] = None,
        dino_model: str = "facebook/dinov2-with-registers-small",
        k_per_mask: int = 5,
        min_mask_pixels: int = 300,
        meanshift_bandwidth_m: float = 0.03,
        target_long_side: int = 756,
        vlm_api_key: Optional[str] = None,
        vlm_model: Optional[str] = None,
        vlm_base_url: str = _DEFAULT_VLM_BASE_URL,
        max_stages: int = 6,
        mechanical_executor: Optional[So101ArmExecutor] = None,
        keypoint_proposer: Optional[KeypointProposer] = None,
        vlm_client: Optional[VlmClient] = None,
    ) -> None:
        if mechanical_executor is not None:
            self._mechanical = mechanical_executor
        else:
            if camera_matrix_path is None or depth_scale_path is None or extrinsics_path is None:
                raise ValueError(
                    "camera_matrix_path/depth_scale_path/extrinsics_path are required "
                    "unless mechanical_executor is injected"
                )
            self._mechanical = So101ArmExecutor(
                camera_matrix_path=camera_matrix_path,
                depth_scale_path=depth_scale_path,
                extrinsics_path=extrinsics_path,
                workspace_min=workspace_min,
                workspace_max=workspace_max,
                urdf_path=urdf_path,
                tcp_offset_m=tcp_offset_m,
                port=port,
                robot=robot,
                gripper_open_value=gripper_open_value,
                gripper_closed_value=gripper_closed_value,
                ik_tolerance_cm=ik_tolerance_cm,
                coordinate_scale=coordinate_scale,
                color_width=color_width,
                color_height=color_height,
                fps=fps,
            )

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

        self._camera_matrix = self._mechanical._camera_matrix
        self._depth_scale = self._mechanical._depth_scale
        self._extrinsics_cm = self._mechanical._extrinsics_cm
        self._workspace_min = self._mechanical._workspace_min
        self._workspace_max = self._mechanical._workspace_max
        self._coordinate_scale = coordinate_scale
        self._ik_tolerance_cm = ik_tolerance_cm
        self._gripper_open_value = gripper_open_value
        self._gripper_closed_value = gripper_closed_value
        self._max_stages = max_stages

        self._last_points: Optional[np.ndarray] = None

    # -- SubTaskExecutor protocol -------------------------------------------------

    def capture(self) -> Image.Image:
        rgb, depth = self._mechanical._read_frame()
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

        result = self._keypoint_proposer.get_keypoints(rgb, points=self._last_points, visualize=True)
        keypoints = result["keypoints_3d"]
        if keypoints is None or len(keypoints) == 0:
            return "Failed: no keypoints detected in the current frame."

        description = sub_task.get("description", "")
        cg = generate_constraints(
            result["overlay"],
            description,
            num_keypoints=len(keypoints),
            keypoints_3d=keypoints,
            ik_solver=self._mechanical._ik,
            vlm=self._vlm,
            hint_pixel=hint_pixel,
        )

        num_stages = min(int(cg["num_stages"]), self._max_stages)
        ee_now = self._mechanical._ik.forward_kinematics(self._read_current_joints())
        ee_at_grasp: Optional[np.ndarray] = None
        completed_stages = 0

        for stage in range(num_stages):
            mask = (
                cg["stage_movable_mask"][stage] if stage < len(cg["stage_movable_mask"]) else [False] * len(keypoints)
            )
            constraints = cg["stage_constraints"][stage] if stage < len(cg["stage_constraints"]) else []
            gripper_state = cg["stage_gripper_action"][stage] if stage < len(cg["stage_gripper_action"]) else "open"
            stage_name = cg["stage_names"][stage] if stage < len(cg["stage_names"]) else f"stage{stage + 1}"

            kps_now = _update_held_keypoints(keypoints, mask, ee_at_grasp, ee_now)

            target_m, _cost = solve_subgoal(
                constraints,
                kps_now,
                workspace_min=self._workspace_min,
                workspace_max=self._workspace_max,
                initial_ee=ee_now,
                movable_mask=mask,
            )

            # Simplification vs. the reference implementation: apply the VLM's single
            # approach elevation/roll to every stage rather than only grasp/approach
            # stages -- keeps the port simple; revisit if transit/place need free orientation.
            approach = approach_from_elevation(target_m, cg["approach_elevation_deg"])
            roll = np.deg2rad(cg["gripper_roll_deg"])
            ik_result = self._mechanical._ik.solve(target_m, approach_dir=approach, roll_override=roll)
            err_cm = ik_result.error_m * 100.0
            if err_cm > self._ik_tolerance_cm:
                return (
                    f"Failed: stopped at stage {stage + 1}/{num_stages} ('{stage_name}') -- "
                    f"IK error {err_cm:.1f} cm exceeds tolerance {self._ik_tolerance_cm} cm."
                )

            gripper_val = self._gripper_closed_value if gripper_state == "closed" else self._gripper_open_value
            self._mechanical._move_robot(ik_result.joints, gripper_val)

            ee_now = self._mechanical._ik.forward_kinematics(ik_result.joints)
            next_mask = cg["stage_movable_mask"][stage + 1] if stage + 1 < len(cg["stage_movable_mask"]) else mask
            if any(next_mask) and not any(mask) and ee_at_grasp is None:
                ee_at_grasp = ee_now
            completed_stages += 1

        return (
            f"Completed {completed_stages}/{num_stages} stage(s) for '{description}'; "
            f"final EE {np.round(ee_now * 100, 1)} cm."
        )

    def _read_current_joints(self) -> np.ndarray:
        robot = self._mechanical._ensure_robot()
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
        return self._mechanical._ik.clamp_joints(q)


__all__ = ["So101RekepExecutor"]
