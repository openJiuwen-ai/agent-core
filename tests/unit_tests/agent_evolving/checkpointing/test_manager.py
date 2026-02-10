# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for checkpointing types and managers via public API."""

from unittest.mock import MagicMock

from openjiuwen.agent_evolving.checkpointing.types import EvolveCheckpoint
from openjiuwen.agent_evolving.checkpointing.manager import DefaultCheckpointManager


def make_checkpoint(**kwargs):
    """Factory for creating test checkpoints."""
    defaults = dict(
        version="v1",
        run_id="test_run",
        step={"epoch": 1},
        best={"best_score": 0.5},
        seed=None,
        operators_state={},
        producer_state={},
        searcher_state={},
        last_metrics={},
    )
    defaults.update(kwargs)
    return EvolveCheckpoint(**defaults)


def make_mock_agent(*operators):
    """Factory for creating mock agents with operators."""
    agent = MagicMock()
    ops_dict = {}
    for op in operators:
        op_id = getattr(op, "operator_id", f"op_{id(op)}")
        ops_dict[op_id] = op
    agent.get_operators.return_value = ops_dict
    return agent


def make_mock_operator(op_id, state=None):
    """Factory for creating mock operators."""
    op = MagicMock()
    op.operator_id = op_id
    op.get_state.return_value = state or {"param": "value"}
    return op


class TestEvolveCheckpoint:
    """Test EvolveCheckpoint dataclass via public API."""

    @staticmethod
    def test_full_creation():
        """Create checkpoint with all fields."""
        checkpoint = make_checkpoint(
            step={"epoch": 5, "batch": 100},
            best={"best_score": 0.9},
            seed=42,
            operators_state={"op1": {"param": "value"}},
            producer_state={"key": "value"},
            last_metrics={"score": 0.85},
        )
        assert checkpoint.version == "v1"
        assert checkpoint.run_id == "test_run"
        assert checkpoint.step["epoch"] == 5
        assert checkpoint.best["best_score"] == 0.9
        assert checkpoint.seed == 42

    @staticmethod
    def test_minimal_creation():
        """Create checkpoint with minimal fields."""
        checkpoint = make_checkpoint(seed=None)
        assert checkpoint.version == "v1"
        assert checkpoint.seed is None

    @staticmethod
    def test_serialization():
        """Checkpoint can be serialized to dict."""
        checkpoint = make_checkpoint()
        data = checkpoint.__dict__
        assert data["version"] == "v1"
        assert data["run_id"] == "test_run"


class TestDefaultCheckpointManager:
    """Test DefaultCheckpointManager class via public API."""

    @staticmethod
    def test_default_init():
        """Init with default values (verified through behavior)."""
        manager = DefaultCheckpointManager()
        assert manager.run_id is not None
        # save_every_n_epochs=1, save_on_improve=True by default
        # Behavior: should always save on epoch 0
        assert manager.should_save(epoch=0, improved=False) is True

    @staticmethod
    def test_custom_init():
        """Init with custom values (verified through behavior)."""
        manager = DefaultCheckpointManager(
            run_id="custom_run",
            checkpoint_version="v2",
            save_every_n_epochs=5,
            save_on_improve=False,
        )
        assert manager.run_id == "custom_run"
        # Verify save_every_n_epochs=5 through behavior
        assert manager.should_save(epoch=5, improved=False) is True
        assert manager.should_save(epoch=1, improved=False) is False

    @staticmethod
    def test_should_save_on_improve_enabled():
        """Save when improved and save_on_improve is True."""
        manager = DefaultCheckpointManager(save_on_improve=True, save_every_n_epochs=10)
        assert manager.should_save(epoch=0, improved=True) is True
        assert manager.should_save(epoch=5, improved=True) is True
        assert manager.should_save(epoch=5, improved=False) is False

    @staticmethod
    def test_should_save_on_improve_disabled():
        """Don't save on improve when save_on_improve is False."""
        manager = DefaultCheckpointManager(save_on_improve=False, save_every_n_epochs=10)
        assert manager.should_save(epoch=0, improved=True) is True
        assert manager.should_save(epoch=10, improved=False) is True

    @staticmethod
    def test_should_save_periodic():
        """Save every N epochs."""
        manager = DefaultCheckpointManager(save_on_improve=False, save_every_n_epochs=3)
        assert manager.should_save(epoch=0, improved=False) is True
        assert manager.should_save(epoch=1, improved=False) is False
        assert manager.should_save(epoch=3, improved=False) is True

    @staticmethod
    def test_should_save_combined_strategy():
        """Combined save on improve + every N epochs."""
        manager = DefaultCheckpointManager(save_on_improve=True, save_every_n_epochs=5)
        assert manager.should_save(epoch=2, improved=True) is True
        assert manager.should_save(epoch=5, improved=False) is True
        assert manager.should_save(epoch=3, improved=False) is False

    @staticmethod
    def test_build_checkpoint_no_operators():
        """Build checkpoint captures empty operators_state when agent has no operators."""
        agent = MagicMock()
        agent.get_operators.return_value = {}

        progress = MagicMock()
        progress.current_epoch = 5
        progress.current_batch_iter = 100
        progress.best_score = 0.95
        progress.seed = 42
        progress.current_epoch_score = 0.90

        manager = DefaultCheckpointManager(run_id="test_run")
        checkpoint = manager.build_checkpoint(agent=agent, progress=progress)

        assert checkpoint.run_id == "test_run"
        assert checkpoint.step["epoch"] == 5
        assert checkpoint.best["best_score"] == 0.95
        assert checkpoint.seed == 42
        assert checkpoint.operators_state == {}

    @staticmethod
    def test_build_checkpoint_with_operators():
        """Build checkpoint captures operators_state correctly."""
        op1 = make_mock_operator("llm_op", {"system_prompt": "new prompt"})
        op2 = make_mock_operator("tool_op", {"enabled": True})
        agent = make_mock_agent(op1, op2)

        progress = MagicMock()
        progress.current_epoch = 3
        progress.current_batch_iter = 50
        progress.best_score = 0.85
        progress.seed = 123
        progress.current_epoch_score = 0.80

        manager = DefaultCheckpointManager(run_id="test_run")
        checkpoint = manager.build_checkpoint(agent=agent, progress=progress)

        assert checkpoint.run_id == "test_run"
        assert checkpoint.operators_state["llm_op"]["system_prompt"] == "new prompt"
        assert checkpoint.operators_state["tool_op"]["enabled"] is True

    @staticmethod
    def test_build_checkpoint_with_producer_state():
        """Build checkpoint includes producer_state."""
        agent = MagicMock()
        agent.get_operators.return_value = {}

        progress = MagicMock()
        progress.current_epoch = 1
        progress.current_batch_iter = 10
        progress.best_score = 0.5
        progress.seed = None
        progress.current_epoch_score = 0.6

        manager = DefaultCheckpointManager(run_id="test_run")
        checkpoint = manager.build_checkpoint(
            agent=agent, progress=progress, producer_state={"optimizier_step": 5}
        )

        assert checkpoint.producer_state == {"optimizier_step": 5}

    @staticmethod
    def test_build_checkpoint_agent_without_get_operators():
        """Build checkpoint handles agent without get_operators method."""
        agent = MagicMock()
        del agent.get_operators

        progress = MagicMock()
        progress.current_epoch = 2
        progress.current_batch_iter = 20
        progress.best_score = 0.6
        progress.seed = None
        progress.current_epoch_score = 0.7

        manager = DefaultCheckpointManager(run_id="test_run")
        checkpoint = manager.build_checkpoint(agent=agent, progress=progress)

        assert checkpoint.operators_state == {}

    @staticmethod
    def test_restore_no_operators():
        """Restore with agent that has no operators."""
        agent = MagicMock()
        agent.get_operators.return_value = {}

        checkpoint = make_checkpoint(
            step={"epoch": 5, "batch": 100},
            best={"best_score": 0.9},
            seed=42,
        )

        manager = DefaultCheckpointManager()
        result = manager.restore(agent=agent, checkpoint=checkpoint)

        assert result["start_epoch"] == 5
        assert result["best_score"] == 0.9
        assert result["run_id"] == "test_run"

    @staticmethod
    def test_restore_restores_operators():
        """Restore loads operators_state onto agent."""
        op = make_mock_operator("llm_op")
        agent = make_mock_agent(op)

        checkpoint = make_checkpoint(
            step={"epoch": 3},
            best={"best_score": 0.7},
            seed=42,
            operators_state={"llm_op": {"prompt": "restored_value"}},
        )

        manager = DefaultCheckpointManager()
        result = manager.restore(agent=agent, checkpoint=checkpoint)

        assert result["start_epoch"] == 3
        op.load_state.assert_called_once_with({"prompt": "restored_value"})

    @staticmethod
    def test_restore_skips_missing_operators():
        """Restore skips operators not in checkpoint."""
        op = make_mock_operator("llm_op")
        agent = make_mock_agent(op)

        checkpoint = make_checkpoint(
            step={"epoch": 2},
            best={"best_score": 0.6},
            operators_state={"missing_op": {}},
        )

        manager = DefaultCheckpointManager()
        manager.restore(agent=agent, checkpoint=checkpoint)

        op.load_state.assert_not_called()

    @staticmethod
    def test_restore_agent_without_get_operators():
        """Restore handles agent without get_operators method."""
        agent = MagicMock()
        del agent.get_operators

        checkpoint = make_checkpoint(
            step={"epoch": 4},
            best={"best_score": 0.8},
            operators_state={"op1": {"param": "value"}},
        )

        manager = DefaultCheckpointManager()
        result = manager.restore(agent=agent, checkpoint=checkpoint)

        assert result["start_epoch"] == 4

    @staticmethod
    def test_restore_returns_progress_state():
        """Restore returns progress state for Trainer."""
        checkpoint = make_checkpoint(
            step={"epoch": 5, "batch": 100},
            best={"best_score": 0.9},
            seed=42,
        )
        agent = MagicMock()

        manager = DefaultCheckpointManager()
        result = manager.restore(agent=agent, checkpoint=checkpoint)

        assert result["start_epoch"] == 5
        assert result["best_score"] == 0.9
        assert result["run_id"] == "test_run"

    @staticmethod
    def test_should_save_every_n_epochs_minimum_one():
        """save_every_n_epochs minimum is 1 (verified through should_save behavior)."""
        # Test that save_every_n_epochs=0 behaves like save_every_n_epochs=1
        manager_zero = DefaultCheckpointManager(save_every_n_epochs=0, save_on_improve=False)
        manager_one = DefaultCheckpointManager(save_every_n_epochs=1, save_on_improve=False)

        # Both should save on epoch 0
        assert manager_zero.should_save(epoch=0, improved=False) is True
        assert manager_one.should_save(epoch=0, improved=False) is True

        # Both should save on epoch 1 (since minimum is 1)
        assert manager_zero.should_save(epoch=1, improved=False) is True
        assert manager_one.should_save(epoch=1, improved=False) is True
