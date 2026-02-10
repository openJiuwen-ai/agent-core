# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for training progress and callbacks."""

from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_evolving.trainer.progress import Callbacks, Progress


def make_progress(**kwargs):
    """Factory for creating Progress instances."""
    defaults = dict(
        start_epoch=0,
        max_epoch=3,
        best_score=0.0,
        current_epoch=0,
        current_batch_iter=0,
        max_batch_iter=1,
        best_batch_score=0.0,
        current_epoch_score=0.0,
    )
    defaults.update(kwargs)
    return Progress(**defaults)


def make_callbacks():
    """Factory for creating Callbacks instances."""
    return Callbacks()


class TestProgress:
    """Test Progress model."""

    @staticmethod
    def test_default_init():
        """Init with default values."""
        progress = make_progress()
        assert progress.start_epoch == 0
        assert progress.current_epoch == 0
        assert progress.max_epoch == 3
        assert progress.best_score == 0.0

    @staticmethod
    def test_custom_init():
        """Init with custom values."""
        progress = make_progress(max_epoch=10, best_score=0.85)
        assert progress.max_epoch == 10
        assert progress.best_score == 0.85

    @staticmethod
    def test_run_epoch_yields_epochs():
        """run_epoch yields epoch numbers."""
        progress = make_progress(max_epoch=3)
        epochs = list(progress.run_epoch())
        assert epochs == [1, 2, 3]
        assert progress.current_epoch == 3

    @staticmethod
    def test_run_epoch_respects_start_epoch():
        """run_epoch respects start_epoch."""
        progress = make_progress(start_epoch=2, max_epoch=5)
        epochs = list(progress.run_epoch())
        assert epochs == [3, 4, 5]
        assert progress.current_epoch == 5

    @staticmethod
    def test_run_epoch_no_iterations():
        """run_epoch with max_epoch <= start_epoch."""
        progress = make_progress(start_epoch=5, max_epoch=5)
        assert list(progress.run_epoch()) == []
        assert progress.current_epoch == 5

    @staticmethod
    def test_run_batch_yields_iterations():
        """run_batch yields batch iterations."""
        progress = make_progress(max_batch_iter=3)
        batches = list(progress.run_batch())
        assert batches == [0, 1, 2]
        assert progress.current_batch_iter == 2

    @staticmethod
    def test_run_batch_resets_best_score():
        """run_batch resets best_batch_score."""
        progress = make_progress(max_batch_iter=2)
        progress.best_batch_score = 0.9
        list(progress.run_batch())
        assert progress.best_batch_score == 0

    @staticmethod
    def test_run_batch_single_iteration():
        """run_batch with single iteration."""
        progress = make_progress(max_batch_iter=1)
        assert list(progress.run_batch()) == [0]

    @staticmethod
    def test_run_batch_no_iterations():
        """run_batch with zero iterations."""
        progress = make_progress(max_batch_iter=0)
        assert list(progress.run_batch()) == []

    @staticmethod
    def test_score_range():
        """Score validation (0-1 range)."""
        progress = make_progress(best_score=0.5)
        assert progress.best_score == 0.5

    @staticmethod
    def test_epoch_range():
        """Epoch validation (non-negative)."""
        progress = make_progress(start_epoch=1, max_epoch=10)
        assert progress.start_epoch == 1


class TestCallbacks:
    """Test Callbacks class."""

    @staticmethod
    def test_init_is_noop():
        """Callbacks init is no-op."""
        make_callbacks()

    @staticmethod
    def test_on_train_begin_noop():
        """on_train_begin is no-op."""
        callbacks = make_callbacks()
        callbacks.on_train_begin(MagicMock(), make_progress(), [])

    @staticmethod
    def test_on_train_end_noop():
        """on_train_end is no-op."""
        callbacks = make_callbacks()
        callbacks.on_train_end(MagicMock(), make_progress(), [])

    @staticmethod
    def test_on_train_epoch_begin_noop():
        """on_train_epoch_begin is no-op."""
        callbacks = make_callbacks()
        callbacks.on_train_epoch_begin(MagicMock(), make_progress())

    @staticmethod
    def test_on_train_epoch_end_noop():
        """on_train_epoch_end is no-op."""
        callbacks = make_callbacks()
        callbacks.on_train_epoch_end(MagicMock(), make_progress(), [])

    @staticmethod
    def test_subclass_override():
        """Callbacks can be subclassed and overridden."""
        calls = []

        class CustomCallbacks(Callbacks):
            def on_train_begin(self, agent, progress, eval_info):
                calls.append("begin")

            def on_train_epoch_begin(self, agent, progress):
                calls.append("epoch_begin")

        callbacks = CustomCallbacks()
        callbacks.on_train_begin(MagicMock(), make_progress(), [])
        callbacks.on_train_epoch_begin(MagicMock(), make_progress())
        assert calls == ["begin", "epoch_begin"]
