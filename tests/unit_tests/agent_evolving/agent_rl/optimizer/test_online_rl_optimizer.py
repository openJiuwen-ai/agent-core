# -*- coding: utf-8 -*-

from openjiuwen.agent_evolving.agent_rl.config.offline_config import (
    RLConfig,
    TrainingConfig,
)
from openjiuwen.agent_evolving.agent_rl.optimizer.rl_optimizer import (
    OnlineRLOptimizer,
)


def _minimal_online_config() -> RLConfig:
    return RLConfig(
        training=TrainingConfig(
            experiment_name="exp_test",
            project_name="proj",
            model_path="/tmp/model",
        )
    )


def test_setup_gateway_rejects_legacy_http_gateway_url():
    optimizer = OnlineRLOptimizer(_minimal_online_config())

    try:
        optimizer.setup_gateway("http://127.0.0.1:18080/v1")
    except ValueError as exc:
        assert "setup_gateway() no longer accepts HTTP Gateway URLs" in str(exc)
        assert "setup_redis()" in str(exc)
    else:
        raise AssertionError("expected setup_gateway() to reject HTTP gateway URLs")


def test_setup_gateway_accepts_redis_url_for_transition():
    optimizer = OnlineRLOptimizer(_minimal_online_config())

    returned = optimizer.setup_gateway(
        "redis://127.0.0.1:6379/0",
        poll_interval=15.0,
        min_samples=8,
    )

    assert returned is optimizer
    assert optimizer._redis_url == "redis://127.0.0.1:6379/0"
    assert optimizer._poll_interval == 15.0
    assert optimizer._min_samples == 8
