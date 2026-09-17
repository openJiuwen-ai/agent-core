from __future__ import annotations

import os
from pathlib import Path

import pytest

from .real_training_harness import RealOnlineRLTrainingSystem

pytestmark = [
    pytest.mark.level1,
    pytest.mark.skipif(
        os.getenv("RUN_ONLINE_RL_TRAINING_ST") != "1",
        reason="set RUN_ONLINE_RL_TRAINING_ST=1 to run the real GPU training ST",
    ),
]


def test_jiuwenswarm_training_improves_rewarded_policy(tmp_path: Path) -> None:
    with RealOnlineRLTrainingSystem(tmp_path) as system:
        effect = system.train_and_measure("jiuwenswarm")

    assert effect.training_run_status == "succeeded"
    assert effect.trained_policy != effect.base_policy
    assert effect.lora_tensor_count > 0
    assert effect.lora_abs_max > 0.0
    assert effect.rewarded_samples > 0
    assert effect.unrewarded_samples > 0
    assert effect.preference_margin_gain > 0.0
    assert effect.post_training_task_passed is True
