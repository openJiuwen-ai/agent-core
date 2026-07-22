# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Find the end-effector position minimizing a stage's VLM-generated constraint costs.

Ported from a user-supplied ReKep-on-SO101 reference implementation
(``run_record_skills/core/subgoal_solver.py``) with only type hints added --
the logic itself is unchanged:

The VLM constraints already encode where the arm should go (e.g.
``np.linalg.norm(end_effector - target)``, zero at the target), so this only
needs to find that minimum -- no extra reachability/regularization cost is
added here (that fights the constraint and can land the solver in the wrong
place; IK feasibility is checked separately by the caller after solving).

For stages where the robot holds an object, held keypoints are projected
forward to the candidate end-effector position so the constraint gradient
reflects "moving the EE also moves what it's holding".
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

import numpy as np
from scipy.optimize import minimize

ConstraintFn = Callable[[np.ndarray, np.ndarray], float]


def _project_keypoints(
    keypoints: np.ndarray,
    movable_mask: Optional[Sequence[bool]],
    initial_ee: np.ndarray,
    candidate_xyz: np.ndarray,
) -> np.ndarray:
    """Held keypoints translate rigidly with the EE; static ones stay put."""
    if not movable_mask or not any(movable_mask):
        return keypoints
    kps = keypoints.copy()
    displacement = candidate_xyz - initial_ee
    for i, held in enumerate(movable_mask):
        if held:
            kps[i] = keypoints[i] + displacement
    return kps


def solve_subgoal(
    constraints: Sequence[ConstraintFn],
    keypoints: np.ndarray,
    *,
    workspace_min: Sequence[float],
    workspace_max: Sequence[float],
    initial_ee: np.ndarray,
    movable_mask: Optional[Sequence[bool]] = None,
    n_restarts: int = 8,
) -> tuple[np.ndarray, float]:
    """Find the EE position (metres) minimizing the given stage's constraint costs.

    Args:
        constraints: ``fn(ee[3], keypoints[K,3]) -> cost`` (<=0 satisfied).
        keypoints: ``[K, 3]`` current keypoint positions.
        workspace_min/workspace_max: workspace bounds, also the search box.
        initial_ee: current EE position, used as one of the optimizer restarts.
        movable_mask: which keypoints move rigidly with a held object.
        n_restarts: number of random restarts (first restart uses ``initial_ee``).

    Returns:
        ``(best_xyz, best_cost)``.
    """
    workspace_min_arr = np.asarray(workspace_min, dtype=float)
    workspace_max_arr = np.asarray(workspace_max, dtype=float)
    initial_ee_arr = np.asarray(initial_ee, dtype=float)
    keypoints_arr = np.asarray(keypoints, dtype=float)

    def objective(xyz: np.ndarray) -> float:
        kps = _project_keypoints(keypoints_arr, movable_mask, initial_ee_arr, xyz)
        cost = 0.0
        for fn in constraints:
            try:
                cost += max(float(fn(xyz, kps)), 0.0)  # only penalize violations
            except Exception:
                cost += 1e6  # broken constraint -> huge penalty, never a free pass
        return cost

    bounds = list(zip(workspace_min_arr, workspace_max_arr))

    rng = np.random.default_rng(42)
    starts = [initial_ee_arr.copy()] + [
        rng.uniform(workspace_min_arr, workspace_max_arr) for _ in range(n_restarts - 1)
    ]

    best_x: Optional[np.ndarray] = None
    best_cost = np.inf
    for x0 in starts:
        x0 = np.clip(x0, workspace_min_arr, workspace_max_arr)
        result = minimize(
            objective,
            x0=x0,
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 500, "ftol": 1e-9, "gtol": 1e-6},
        )
        if result.fun < best_cost:
            best_cost = float(result.fun)
            best_x = result.x.copy()

    return best_x, best_cost


__all__ = ["ConstraintFn", "solve_subgoal"]
