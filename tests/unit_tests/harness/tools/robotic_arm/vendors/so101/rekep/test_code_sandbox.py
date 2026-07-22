#!/usr/bin/env python
# coding: utf-8
"""Tests for the AST whitelist + restricted exec() used on VLM-generated ReKep
constraint code -- the most security-sensitive piece of the ReKep executor.
"""

from __future__ import annotations

import numpy as np
import pytest

from openjiuwen.harness.tools.robotic_arm.vendors.so101.rekep._code_sandbox import (
    safe_exec_constraint_code,
    validate_constraint_code,
)

_VALID_CODE = """
num_stages = 1

def stage1_subgoal_constraint1(end_effector, keypoints):
    \"\"\"EE at keypoint 0.\"\"\"
    target = keypoints[0] + np.array([0.0, 0.0, 0.05])
    return float(np.linalg.norm(end_effector - target))

STAGE_CONSTRAINTS = [[stage1_subgoal_constraint1]]
STAGE_PATH_CONSTRAINTS = [[]]
STAGE_NAMES = ["grasp"]
STAGE_MOVABLE_MASK = [[False]]
STAGE_GRIPPER_ACTION = ["closed"]
grasp_keypoints = [0]
release_keypoints = []
approach_elevation_deg = 90
gripper_roll_deg = 0
"""


def test_valid_constraint_code_passes_validation() -> None:
    validate_constraint_code(_VALID_CODE)  # must not raise


@pytest.mark.parametrize(
    "malicious_code",
    [
        "import os\nos.system('echo pwned')",
        "from os import system\nsystem('echo pwned')",
        "exec('print(1)')",
        "eval('1+1')",
        "open('/etc/passwd').read()",
        "().__class__.__mro__[1].__subclasses__()",
        "def f(ee, kps):\n    return ee.__class__",
        "unknown_function(1, 2)",
        "class Foo:\n    pass",
        "def f(ee, kps):\n    if ee[0] > 0:\n        return 1\n    return 0",
        "def f(ee, kps):\n    for x in kps:\n        pass\n    return 0",
        "def f(ee, kps):\n    with open('x') as fh:\n        pass",
        "def f(ee, kps):\n    try:\n        return 1\n    except Exception:\n        return 0",
        "def f(ee, kps):\n    global num_stages\n    return 0",
        "lambda ee, kps: 0",
        "num_stages = __import__('os')",
        "not python code (((",
    ],
)
def test_malicious_or_invalid_code_is_rejected(malicious_code: str) -> None:
    with pytest.raises(ValueError):
        validate_constraint_code(malicious_code)


def test_safe_exec_runs_valid_code_and_exposes_functions() -> None:
    namespace = safe_exec_constraint_code(_VALID_CODE, {"np": np, "numpy": np})

    assert namespace["num_stages"] == 1
    fn = namespace["STAGE_CONSTRAINTS"][0][0]
    ee = np.array([0.0, 0.0, 0.0])
    keypoints = np.array([[0.0, 0.0, 0.0]])
    assert fn(ee, keypoints) == pytest.approx(0.05, rel=1e-6)


def test_safe_exec_rejects_malicious_code_without_executing() -> None:
    with pytest.raises(ValueError):
        safe_exec_constraint_code("import os\nos.system('echo pwned')", {"np": np})


def test_safe_exec_builtins_do_not_leak_open_or_import() -> None:
    namespace = safe_exec_constraint_code(_VALID_CODE, {"np": np, "numpy": np})

    assert "open" not in namespace["__builtins__"]
    assert "__import__" not in namespace["__builtins__"]
    assert "exec" not in namespace["__builtins__"]
