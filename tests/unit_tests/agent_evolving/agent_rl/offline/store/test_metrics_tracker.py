# -*- coding: utf-8 -*-
"""Light UT for RLMetricsTracker: log_* and finish with mocked Tracking."""

from openjiuwen.agent_evolving.agent_rl.offline.store.metrics_tracker import (
    RLMetricsTracker,
    TrainingStepMetrics,
)


def test_log_step_and_finish_no_raise():
    tracker = RLMetricsTracker(
        project_name="p",
        experiment_name="e",
        backends=["tensorboard"],
    )
    tracker.log_step(0, {"loss": 0.5})
    tracker.finish()


def test_log_training_step_and_log_rollout_stats_no_raise():
    tracker = RLMetricsTracker(
        project_name="p",
        experiment_name="e",
        backends=["tensorboard"],
    )
    tracker.log_training_step(
        TrainingStepMetrics(
            step=0,
            epoch=0,
            verl_metrics={"actor_loss": 0.1},
            avg_turns=2.0,
            reward_mean=0.5,
            consecutive_zero_reward_steps=0,
        )
    )
    tracker.log_rollout_stats(
        step=0,
        rewards_by_uid={"u1": [{"global": 0.5}]},
        total_positive=1,
        total_negative=0,
    )
