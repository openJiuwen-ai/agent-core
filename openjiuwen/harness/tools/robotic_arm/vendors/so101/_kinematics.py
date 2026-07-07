# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared geometry/IK helpers for the SO-101 executors (mechanical + ReKep).

Ported from a user-supplied ReKep-on-SO101 reference implementation
(``run_record_skills/core/{ik_solver,custom_ik,helper}.py``); the math is
unchanged, only reorganized into typed, dependency-injected functions/classes
so both :class:`So101ArmExecutor` and :class:`So101RekepExecutor` can share
one implementation instead of each re-deriving it.

``ikpy``/``scipy`` are imported lazily inside :class:`IKSolver` so importing
this module (and running its pure-math unit tests) never requires the
``robotic-arm-so101`` extra to be installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

_DEFAULT_BASE_ELEMENTS = [
    "base_link",
    "shoulder_pan",
    "shoulder_link",
    "shoulder_lift",
    "upper_arm_link",
    "elbow_flex",
    "lower_arm_link",
    "wrist_flex",
    "wrist_link",
    "wrist_roll",
    "gripper_link",
    "gripper_frame_joint",
    "gripper_frame_link",
]
_DEFAULT_ACTIVE_MASK = [False, True, True, True, True, True, False]
_WRIST_ROLL_JOINT_INDEX = 5
_DEPTH_INVALID_RAW = 65535


def pixel_from_normalized(x: float, y: float, scale: int, width: int, height: int) -> tuple[int, int]:
    """Convert a ``report_plan`` normalized point in ``[0, scale]`` to a pixel on ``width``x``height``."""
    px = int(round(x / scale * width))
    py = int(round(y / scale * height))
    return min(max(px, 0), width - 1), min(max(py, 0), height - 1)


def backproject_pixel(
    px: int,
    py: int,
    depth_raw: np.ndarray,
    camera_matrix: np.ndarray,
    depth_scale: float,
    extrinsics_cm: np.ndarray,
) -> Optional[np.ndarray]:
    """Backproject one pixel to a 3D point (metres) in the robot base frame.

    ``extrinsics_cm`` is the 4x4 base<-camera transform calibrated in
    centimetres (matches ``T_base_camera.npy``); returns ``None`` for an
    invalid/out-of-range depth reading.
    """
    depth_value = float(depth_raw[py, px])
    if depth_value <= 0 or depth_value >= _DEPTH_INVALID_RAW:
        return None
    fx, fy = camera_matrix[0, 0], camera_matrix[1, 1]
    cx, cy = camera_matrix[0, 2], camera_matrix[1, 2]
    z_cm = depth_value * depth_scale * 100.0
    x_cm = (px - cx) * z_cm / fx
    y_cm = (py - cy) * z_cm / fy
    cam_homo = np.array([x_cm, y_cm, z_cm, 1.0])
    base_homo = extrinsics_cm @ cam_homo
    return base_homo[:3] / 100.0


def backproject_frame(
    depth_raw: np.ndarray,
    camera_matrix: np.ndarray,
    depth_scale: float,
    extrinsics_cm: np.ndarray,
) -> np.ndarray:
    """Backproject a whole depth frame to a ``[H, W, 3]`` point cloud (metres, robot base frame)."""
    h, w = depth_raw.shape
    fx, fy = camera_matrix[0, 0], camera_matrix[1, 1]
    cx, cy = camera_matrix[0, 2], camera_matrix[1, 2]
    z_cm = depth_raw.astype(np.float32) * depth_scale * 100.0
    us, vs = np.meshgrid(np.arange(w), np.arange(h))
    x_cm = (us - cx) * z_cm / fx
    y_cm = (vs - cy) * z_cm / fy
    invalid = (depth_raw <= 0) | (depth_raw >= _DEPTH_INVALID_RAW)
    x_cm[invalid] = np.nan
    y_cm[invalid] = np.nan
    z_cm[invalid] = np.nan
    cam_homo = np.stack([x_cm, y_cm, z_cm, np.ones_like(x_cm)], axis=-1)
    base_homo = cam_homo @ extrinsics_cm.T
    return base_homo[..., :3] / 100.0


def in_workspace(target_m: np.ndarray, workspace_min: Sequence[float], workspace_max: Sequence[float]) -> bool:
    return bool(np.all(target_m >= np.asarray(workspace_min)) and np.all(target_m <= np.asarray(workspace_max)))


def approach_from_elevation(target_m: np.ndarray, elevation_deg: float) -> np.ndarray:
    """Gripper approach direction: horizontal component points from the robot
    base toward ``target_m``, vertical component is set by ``elevation_deg``
    (0 = horizontal approach, 90 = straight down)."""
    elevation = np.deg2rad(np.clip(elevation_deg, 0, 90))
    horizontal = np.array([target_m[0], target_m[1], 0.0])
    norm = np.linalg.norm(horizontal)
    if norm < 1e-6:
        return np.array([0.0, 0.0, -1.0])
    unit = horizontal / norm
    direction = np.array([np.cos(elevation) * unit[0], np.cos(elevation) * unit[1], -np.sin(elevation)])
    return direction / np.linalg.norm(direction)


@dataclass
class IKResult:
    joints: np.ndarray
    error_m: float


def check_reachability(
    point_m: np.ndarray, ik_solver: "IKSolver", elevation_deg: float = 90, roll_deg: float = 0
) -> dict:
    """Used by the ReKep constraint-generation VLM (injected as ``check_reachability`` in
    its sandboxed exec namespace) to verify an approach point before committing to it."""
    approach = approach_from_elevation(point_m, elevation_deg)
    result = ik_solver.solve(point_m, approach_dir=approach, roll_override=np.deg2rad(roll_deg))
    err_cm = result.error_m * 100
    return {"reachable": err_cm < 3.0, "ik_error_cm": round(err_cm, 2)}


def check_keypoint_reachability(
    keypoints_3d: np.ndarray, ik_solver: "IKSolver", elevations: Sequence[float] = (90, 60, 45, 30, 0)
) -> list[dict]:
    """Per-keypoint IK error at a range of approach elevations, for the reachability
    report that gets injected into the constraint-generation prompt."""
    report = []
    for i, kp in enumerate(keypoints_3d):
        errors = {}
        best_err = float("inf")
        for elevation in elevations:
            approach = approach_from_elevation(kp, elevation)
            result = ik_solver.solve(kp, approach_dir=approach)
            err_cm = round(result.error_m * 100, 2)
            errors[elevation] = err_cm
            best_err = min(best_err, err_cm)
        report.append(
            {
                "keypoint": i,
                "position_cm": kp * 100,
                "reachable": best_err < 3.0,
                "elevation_errors_cm": errors,
            }
        )
    return report


def reachability_report_str(report: list[dict]) -> str:
    """Format :func:`check_keypoint_reachability`'s output as text for the VLM prompt."""
    lines = []
    for r in report:
        errs = "  ".join(
            f"{el}deg->{r['elevation_errors_cm'][el]:.1f}cm" for el in sorted(r["elevation_errors_cm"], reverse=True)
        )
        status = "OK" if r["reachable"] else "UNREACHABLE"
        lines.append(f"  Keypoint {r['keypoint']}: [{errs}]  {status}")
    return "\n".join(lines)


class IKSolver:
    """Wraps an ``ikpy`` kinematic chain with orientation-aware IK for the SO-101.

    5-DOF orientation model: ``approach_dir`` fixes the gripper Z-axis direction
    (3 position + 2 direction = 5 constraints); ``roll_override`` then directly
    sets the wrist_roll joint to spin the fingers (it sits near the roll axis so
    this doesn't move the tip). Assumes the default 7-link SO-101 chain layout
    (base + 5 active joints + tip), matching ``joints_to_action``.
    """

    def __init__(
        self,
        urdf_path: str,
        tcp_offset_m: Sequence[float],
        *,
        base_elements: Optional[Sequence[str]] = None,
        active_mask: Optional[Sequence[bool]] = None,
        n_restarts: int = 8,
    ) -> None:
        try:
            from ikpy.chain import Chain
        except ImportError as e:
            raise ImportError(
                f"ikpy is not installed; run `pip install 'openjiuwen[robotic-arm-so101]'` ({e})"
            ) from e
        self._chain = Chain.from_urdf_file(
            urdf_path,
            base_elements=list(base_elements or _DEFAULT_BASE_ELEMENTS),
            base_element_type="link",
            active_links_mask=list(active_mask or _DEFAULT_ACTIVE_MASK),
        )
        self._tcp_offset = np.asarray(tcp_offset_m, dtype=float)
        self._n_restarts = n_restarts

    def forward_kinematics(self, joints: np.ndarray) -> np.ndarray:
        return (self._chain.forward_kinematics(joints) @ self._tcp_offset)[:3]

    def clamp_joints(self, joints: np.ndarray) -> np.ndarray:
        q = np.array(joints, dtype=float)
        for i, link in enumerate(self._chain.links):
            bounds = getattr(link, "bounds", None)
            if bounds is not None and bounds[0] is not None and bounds[1] is not None:
                q[i] = np.clip(q[i], bounds[0], bounds[1])
        return q

    def solve(
        self,
        target_m: np.ndarray,
        *,
        approach_dir: Optional[np.ndarray] = None,
        roll_override: Optional[float] = None,
    ) -> IKResult:
        """Levenberg-Marquardt-style IK via ``scipy.optimize.minimize`` with soft
        joint-limit penalties and multiple random restarts (more robust near
        joint limits than a single ``ikpy`` analytic solve)."""
        from scipy.optimize import minimize

        target_m = np.asarray(target_m, dtype=float)

        def fk_tip(full_joints: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            fk = self._chain.forward_kinematics(full_joints)
            return (fk @ self._tcp_offset)[:3], fk[:3, :3]

        def objective(active_joints: np.ndarray) -> float:
            full_joints = np.zeros(7)
            full_joints[1:6] = active_joints
            tip, rotation = fk_tip(full_joints)

            pos_err = float(np.sum((tip - target_m) ** 2))

            orient_err = 0.0
            if approach_dir is not None:
                gripper_z = rotation[:, 2]
                orient_err = 1.0 - float(np.dot(gripper_z, approach_dir))

            limit_err = 0.0
            margin = 0.05
            for i, link in enumerate(self._chain.links):
                if i < 1 or i > 5:
                    continue
                bounds = getattr(link, "bounds", None)
                if bounds is None or bounds[0] is None or bounds[1] is None:
                    continue
                qi = active_joints[i - 1]
                if qi > bounds[1] - margin:
                    limit_err += (qi - (bounds[1] - margin)) ** 2
                if qi < bounds[0] + margin:
                    limit_err += ((bounds[0] + margin) - qi) ** 2

            return 100.0 * pos_err + 3.0 * orient_err + 10.0 * limit_err

        bounds = []
        for i, link in enumerate(self._chain.links):
            if 1 <= i <= 5:
                link_bounds = getattr(link, "bounds", None)
                if link_bounds is not None and link_bounds[0] is not None and link_bounds[1] is not None:
                    bounds.append((link_bounds[0], link_bounds[1]))
                else:
                    bounds.append((-np.pi, np.pi))

        rng = np.random.default_rng(42)
        best_joints: Optional[np.ndarray] = None
        best_error = np.inf

        for restart in range(self._n_restarts):
            x0 = np.zeros(5) if restart == 0 else rng.uniform([b[0] for b in bounds], [b[1] for b in bounds])
            result = minimize(
                objective,
                x0,
                method="L-BFGS-B",
                bounds=bounds,
                options={"maxiter": 500, "ftol": 1e-10, "gtol": 1e-7},
            )
            full_joints = np.zeros(7)
            full_joints[1:6] = result.x
            if roll_override is not None:
                full_joints[_WRIST_ROLL_JOINT_INDEX] = roll_override

            tip, _ = fk_tip(full_joints)
            error = float(np.linalg.norm(tip - target_m))
            if error < best_error:
                best_error = error
                best_joints = full_joints.copy()

        return IKResult(joints=best_joints, error_m=best_error)

    @staticmethod
    def joints_to_action(joints: np.ndarray) -> dict:
        """Convert the 7-element joint vector (index 0/6 are the fixed base/tip
        links) into the ``lerobot`` degree-based action dict for SO-101's 5 servos."""
        deg = np.rad2deg(joints[1:6])
        return {
            "shoulder_pan.pos": float(deg[0]),
            "shoulder_lift.pos": float(deg[1]),
            "elbow_flex.pos": float(deg[2]),
            "wrist_flex.pos": float(deg[3]),
            "wrist_roll.pos": float(deg[4]),
        }


__all__ = [
    "IKResult",
    "IKSolver",
    "approach_from_elevation",
    "backproject_frame",
    "backproject_pixel",
    "check_keypoint_reachability",
    "check_reachability",
    "in_workspace",
    "pixel_from_normalized",
    "reachability_report_str",
]
