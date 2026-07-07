#!/usr/bin/env python
# coding: utf-8
"""Tests for solve_subgoal: numeric convergence, broken-constraint penalty,
and held-keypoint rigidity propagation."""

from __future__ import annotations

import numpy as np

from openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep.subgoal_solver import solve_subgoal


def test_solve_subgoal_converges_to_target_point() -> None:
    target = np.array([0.2, 0.1, 0.05])

    def constraint(ee, kps):
        return float(np.linalg.norm(ee - target))

    best_xyz, best_cost = solve_subgoal(
        [constraint],
        keypoints=np.zeros((1, 3)),
        workspace_min=[-0.5, -0.5, -0.5],
        workspace_max=[0.5, 0.5, 0.5],
        initial_ee=np.array([0.0, 0.0, 0.0]),
    )

    np.testing.assert_allclose(best_xyz, target, atol=1e-3)
    assert best_cost < 1e-3


def test_solve_subgoal_penalizes_broken_constraints_instead_of_ignoring_them() -> None:
    def broken(ee, kps):
        raise ValueError("constraint bug")

    _best_xyz, cost = solve_subgoal(
        [broken],
        keypoints=np.zeros((1, 3)),
        workspace_min=[-0.5, -0.5, -0.5],
        workspace_max=[0.5, 0.5, 0.5],
        initial_ee=np.array([0.0, 0.0, 0.0]),
        n_restarts=2,
    )

    assert cost >= 1e6


def test_solve_subgoal_propagates_held_keypoints() -> None:
    # keypoint 0 is "held" and starts coincident with the EE, so it should track
    # the EE 1:1; keypoint 1 is a static reference point.
    initial_ee = np.array([0.0, 0.0, 0.0])
    keypoints = np.array([[0.0, 0.0, 0.0], [0.2, 0.1, 0.0]])

    def constraint(ee, kps):
        held, static = kps[0], kps[1]
        return float(np.linalg.norm(held[:2] - static[:2]) + abs(held[2] - static[2] - 0.1))

    best_xyz, best_cost = solve_subgoal(
        [constraint],
        keypoints=keypoints,
        workspace_min=[-0.5, -0.5, -0.5],
        workspace_max=[0.5, 0.5, 0.5],
        initial_ee=initial_ee,
        movable_mask=[True, False],
    )

    np.testing.assert_allclose(best_xyz, [0.2, 0.1, 0.1], atol=1e-3)
    assert best_cost < 1e-3
