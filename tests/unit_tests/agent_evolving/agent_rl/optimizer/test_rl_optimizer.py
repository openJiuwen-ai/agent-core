# -*- coding: utf-8 -*-
"""Unit tests for OfflineRLOptimizer (run_name, persistence, metrics, agent_factory, reward, config)."""


from unittest.mock import patch, MagicMock

import pytest
from pydantic import ValidationError

pytest.importorskip("ray")
pytest.importorskip("hydra")
pytest.importorskip("omegaconf")

from openjiuwen.agent_evolving.agent_rl.config.offline_config import (
    RLConfig,
    TrainingConfig,
    PersistenceConfig,
)
from openjiuwen.agent_evolving.agent_rl.optimizer.rl_optimizer import OfflineRLOptimizer
from openjiuwen.agent_evolving.agent_rl.offline.store.null_store import NullRolloutStore
from openjiuwen.agent_evolving.agent_rl.offline.store.file_store import FileRolloutStore
from openjiuwen.agent_evolving.agent_rl.offline.store.metrics_tracker import RLMetricsTracker


@pytest.fixture
def minimal_config():
    return RLConfig(training=TrainingConfig(experiment_name="exp_test", project_name="proj"))


def test_init_run_name_contains_experiment_and_timestamp(minimal_config):
    with patch("openjiuwen.agent_evolving.agent_rl.optimizer.rl_optimizer.datetime") as mdt:
        mdt.now.return_value.strftime.return_value = "20260304_120000"
        opt = OfflineRLOptimizer(minimal_config)
        assert "exp_test" in opt.run_name
        assert "20260304_120000" in opt.run_name


def test_build_persistence_disabled_returns_null_store(minimal_config):
    opt = OfflineRLOptimizer(minimal_config)
    opt.config.persistence.enabled = False
    store = opt.build_persistence(opt.config)
    assert isinstance(store, NullRolloutStore)


def test_build_persistence_enabled_returns_file_store(minimal_config, tmp_path):
    opt = OfflineRLOptimizer(minimal_config)
    opt.config.persistence.enabled = True
    opt.config.persistence.save_path = str(tmp_path)
    store = opt.build_persistence(opt.config)
    assert isinstance(store, FileRolloutStore)


def test_build_metrics_tracker_uses_project_and_run_name(minimal_config):
    with patch("openjiuwen.agent_evolving.agent_rl.optimizer.rl_optimizer.datetime") as mdt:
        mdt.now.return_value.strftime.return_value = "20260304_120000"
        opt = OfflineRLOptimizer(minimal_config)
    tracker = opt.build_metrics_tracker(opt.config)
    assert isinstance(tracker, RLMetricsTracker)
    assert tracker.init_kwargs["project_name"] == minimal_config.training.project_name
    assert opt.run_name in tracker.init_kwargs["experiment_name"]


def test_set_tools_and_resolve_agent_factory_returns_factory(minimal_config):
    opt = OfflineRLOptimizer(minimal_config)
    opt.set_tools([MagicMock(name="tool1")])
    with patch(
        "openjiuwen.agent_evolving.agent_rl.optimizer.rl_optimizer.build_agent_factory"
    ) as m:
        m.return_value = MagicMock()
        factory = opt.resolve_agent_factory()
        assert factory is m.return_value
        m.assert_called_once()


def test_resolve_agent_factory_custom_takes_precedence(minimal_config):
    opt = OfflineRLOptimizer(minimal_config)
    custom = MagicMock()
    opt.set_agent_factory(custom)
    assert opt.resolve_agent_factory() is custom


def test_register_reward_empty_name_raises(minimal_config):
    opt = OfflineRLOptimizer(minimal_config)
    with pytest.raises(Exception):
        opt.register_reward(lambda x: 0, name="")


def test_rlconfig_training_required_raises_validation_error():
    with pytest.raises(ValidationError):
        RLConfig(training=None)
