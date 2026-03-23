# -*- coding: utf-8 -*-
"""Unit tests for MainTrainer: init creates coordinator and proxy, proxy_url, update_backends."""

import sys
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("torch")

# omegaconf / torchdata / flask (via BackendProxy) may be missing in minimal test env
if "omegaconf" not in sys.modules:
    sys.modules["omegaconf"] = MagicMock()
    sys.modules["omegaconf"].DictConfig = dict
if "flask" not in sys.modules:
    sys.modules["flask"] = MagicMock()
    sys.modules["flask"].Flask = MagicMock()
    sys.modules["flask"].Response = MagicMock()
    sys.modules["flask"].abort = MagicMock()
    sys.modules["flask"].request = MagicMock()
if "requests" not in sys.modules:
    sys.modules["requests"] = MagicMock()
if "torchdata" not in sys.modules:
    sys.modules["torchdata"] = MagicMock()
    sys.modules["torchdata"].stateful_dataloader = MagicMock()
    sys.modules["torchdata.stateful_dataloader"] = MagicMock()
    sys.modules["torchdata.stateful_dataloader"].StatefulDataLoader = MagicMock()
if "tqdm" not in sys.modules:
    def _mock_tqdm_enter(self):
        return self

    def _mock_tqdm_exit(*args):
        return None

    sys.modules["tqdm"] = MagicMock()
    sys.modules["tqdm"].tqdm = MagicMock(
        return_value=MagicMock(__enter__=_mock_tqdm_enter, __exit__=_mock_tqdm_exit)
    )

from openjiuwen.dev_tools.agentrl.rl_trainer.main_trainer import MainTrainer
from openjiuwen.dev_tools.agentrl.coordinator.training_coordinator import TrainingCoordinator
from openjiuwen.dev_tools.agentrl.proxy.backend_proxy import BackendProxy


@pytest.fixture
def mock_rl_trainer():
    m = MagicMock()
    m.train_dataset = MagicMock()
    m.train_dataset.__len__ = MagicMock(return_value=4)
    m.val_dataset = None
    m.tokenizer = MagicMock(pad_token_id=0)
    return m


@pytest.fixture
def trainer_config():
    """Config dict; MainTrainer expects config.data.get(), config.trainer.get(), etc. (DictConfig-like)."""
    data = {
        "max_prompt_length": 32,
        "max_response_length": 16,
        "dataloader_num_workers": 0,
        "gen_batch_size": 2,
        "validation_shuffle": False,
    }
    trainer = {"total_epochs": 1}
    jiuwen = {
        "whole_trajectory": False,
        "custom_fn": {
            "classifier": "default_classify_rollouts",
            "validator": "default_validate_stop",
            "sampler": "default_sampling",
        },
        "llm_timeout_seconds": 30_000,
    }

    def _config_get(k, default=None):
        return {"data": data, "trainer": trainer, "JiuwenRL": jiuwen}.get(k, default)

    config = MagicMock()
    config.data = data
    config.trainer = trainer
    config.get = _config_get
    config.JiuwenRL = jiuwen
    return config


def test_main_trainer_init_creates_coordinator_and_proxy(mock_rl_trainer, trainer_config):
    with patch("openjiuwen.dev_tools.agentrl.rl_trainer.main_trainer.TrainingCoordinator") as m_coord:
        with patch("openjiuwen.dev_tools.agentrl.rl_trainer.main_trainer.BackendProxy") as m_proxy:
            trainer = MainTrainer(
                rl_trainer=mock_rl_trainer,
                config=trainer_config,
                collate_fn=None,
                task_runner=MagicMock(),
            )
            m_coord.assert_called_once()
            m_proxy.assert_called_once()
            assert trainer.training_coordinator is m_coord.return_value


def test_proxy_url_returns_proxy_url_after_start(mock_rl_trainer, trainer_config):
    with patch("openjiuwen.dev_tools.agentrl.rl_trainer.main_trainer.TrainingCoordinator"):
        with patch("openjiuwen.dev_tools.agentrl.rl_trainer.main_trainer.BackendProxy") as m_proxy_cls:
            m_proxy_cls.return_value.url = "http://127.0.0.1:12345"
            m_proxy_cls.return_value.update_backend_servers = MagicMock()
            trainer = MainTrainer(
                rl_trainer=mock_rl_trainer,
                config=trainer_config,
                collate_fn=None,
            )
            assert trainer.proxy_url == "http://127.0.0.1:12345"


def test_update_backends_calls_proxy_update_backend_servers(mock_rl_trainer, trainer_config):
    with patch("openjiuwen.dev_tools.agentrl.rl_trainer.main_trainer.TrainingCoordinator"):
        with patch("openjiuwen.dev_tools.agentrl.rl_trainer.main_trainer.BackendProxy") as m_proxy_cls:
            proxy = m_proxy_cls.return_value
            proxy.url = "http://127.0.0.1:12345"
            proxy.update_backend_servers = MagicMock()
            trainer = MainTrainer(
                rl_trainer=mock_rl_trainer,
                config=trainer_config,
                collate_fn=None,
            )
            trainer.simulate_proxy_already_started()
            trainer.update_backends(["http://a:8000"])
            proxy.update_backend_servers.assert_called_once_with(["http://a:8000"])
