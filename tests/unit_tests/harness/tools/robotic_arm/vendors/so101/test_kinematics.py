#!/usr/bin/env python
# coding: utf-8
"""Tests for the pure geometry/reachability helpers in _kinematics.py.

These never need ikpy/scipy installed (they're plain numpy); only
constructing :class:`IKSolver` itself does, and that path is covered
separately with a simulated missing-dependency import failure.
"""

from __future__ import annotations

import builtins

import numpy as np
import pytest

from openjiuwen.harness.tools.robotic_arm.vendors.so101 import _kinematics as kinematics


def test_pixel_from_normalized_scales_and_clamps() -> None:
    assert kinematics.pixel_from_normalized(0, 0, 1000, 100, 50) == (0, 0)
    assert kinematics.pixel_from_normalized(1000, 1000, 1000, 100, 50) == (99, 49)
    assert kinematics.pixel_from_normalized(500, 500, 1000, 100, 50) == (50, 25)
    # out-of-range inputs clamp instead of raising/wrapping
    assert kinematics.pixel_from_normalized(-100, 5000, 1000, 100, 50) == (0, 49)


def test_backproject_pixel_matches_frame_backprojection() -> None:
    camera_matrix = np.array([[500.0, 0, 320.0], [0, 500.0, 240.0], [0, 0, 1]])
    depth_scale = 0.001  # metres per raw depth unit
    extrinsics_cm = np.eye(4)
    depth_raw = np.full((480, 640), 1000, dtype=np.uint16)  # 1.0 m everywhere

    frame_points = kinematics.backproject_frame(depth_raw, camera_matrix, depth_scale, extrinsics_cm)
    pixel_point = kinematics.backproject_pixel(
        320,
        240,
        depth_raw,
        camera_matrix=camera_matrix,
        depth_scale=depth_scale,
        extrinsics_cm=extrinsics_cm,
    )

    assert pixel_point is not None
    np.testing.assert_allclose(pixel_point, frame_points[240, 320], atol=1e-9)
    # the principal point (cx, cy) should backproject to (0, 0, depth)
    np.testing.assert_allclose(pixel_point, [0.0, 0.0, 1.0], atol=1e-9)


def test_backproject_pixel_rejects_invalid_depth() -> None:
    camera_matrix = np.array([[500.0, 0, 320.0], [0, 500.0, 240.0], [0, 0, 1]])
    depth_raw = np.zeros((480, 640), dtype=np.uint16)
    assert (
        kinematics.backproject_pixel(
            320, 240, depth_raw, camera_matrix=camera_matrix, depth_scale=0.001, extrinsics_cm=np.eye(4)
        )
        is None
    )


def test_in_workspace() -> None:
    assert kinematics.in_workspace(np.array([0.1, 0.0, 0.05]), [0.0, -0.2, 0.0], [0.2, 0.2, 0.1])
    assert not kinematics.in_workspace(np.array([0.3, 0.0, 0.05]), [0.0, -0.2, 0.0], [0.2, 0.2, 0.1])


def test_approach_from_elevation_top_down() -> None:
    direction = kinematics.approach_from_elevation(np.array([0.2, 0.0, 0.05]), 90)
    np.testing.assert_allclose(direction, [0.0, 0.0, -1.0], atol=1e-9)


def test_approach_from_elevation_horizontal_points_away_from_base() -> None:
    direction = kinematics.approach_from_elevation(np.array([0.2, 0.0, 0.05]), 0)
    np.testing.assert_allclose(direction, [1.0, 0.0, 0.0], atol=1e-9)


def test_approach_from_elevation_degenerate_target_at_base() -> None:
    direction = kinematics.approach_from_elevation(np.array([0.0, 0.0, 0.05]), 45)
    np.testing.assert_allclose(direction, [0.0, 0.0, -1.0], atol=1e-9)


class _FakeIKSolver:
    """Deterministic stand-in: IK error grows with distance from the base."""

    def solve(self, target_m, *, approach_dir=None, roll_override=None):
        error_m = float(np.linalg.norm(target_m)) * 0.01
        return kinematics.IKResult(joints=np.zeros(7), error_m=error_m)


def test_check_reachability_uses_ik_error() -> None:
    result = kinematics.check_reachability(np.array([0.1, 0.0, 0.05]), _FakeIKSolver())
    assert result["reachable"] is True
    expected_cm = round(np.linalg.norm([0.1, 0.0, 0.05]) * 0.01 * 100, 2)
    assert result["ik_error_cm"] == pytest.approx(expected_cm, rel=1e-6)


def test_check_keypoint_reachability_and_report_str() -> None:
    keypoints = np.array([[0.1, 0.0, 0.05], [5.0, 0.0, 0.05]])  # second is far -> unreachable
    report = kinematics.check_keypoint_reachability(keypoints, _FakeIKSolver(), elevations=(90, 0))

    assert report[0]["reachable"] is True
    assert report[1]["reachable"] is False

    text = kinematics.reachability_report_str(report)
    assert "Keypoint 0" in text and "OK" in text
    assert "Keypoint 1" in text and "UNREACHABLE" in text


def test_ik_solver_construction_fails_closed_without_ikpy(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("ikpy", "ikpy.chain"):
            raise ImportError("simulated missing ikpy")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError, match="ikpy is not installed"):
        kinematics.IKSolver("fake.urdf", (0.0, 0.0, 0.0, 1.0))
