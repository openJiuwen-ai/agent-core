# coding: utf-8
"""Tests for ComposedCallbacks."""

from unittest.mock import MagicMock

from openjiuwen.agent_evolving.callbacks.composed_callbacks import ComposedCallbacks
from openjiuwen.agent_evolving.trainer.progress import Callbacks


class TestComposedCallbacks:
    @staticmethod
    def test_calls_in_order():
        """All 4 hooks called in registration order."""
        call_order = []
        cb1 = MagicMock(spec=Callbacks)
        cb1.on_train_begin.side_effect = lambda *a: call_order.append("cb1.begin")
        cb1.on_train_end.side_effect = lambda *a: call_order.append("cb1.end")
        cb1.on_train_epoch_begin.side_effect = lambda *a: call_order.append("cb1.epoch_begin")
        cb1.on_train_epoch_end.side_effect = lambda *a: call_order.append("cb1.epoch_end")

        cb2 = MagicMock(spec=Callbacks)
        cb2.on_train_begin.side_effect = lambda *a: call_order.append("cb2.begin")
        cb2.on_train_end.side_effect = lambda *a: call_order.append("cb2.end")
        cb2.on_train_epoch_begin.side_effect = lambda *a: call_order.append("cb2.epoch_begin")
        cb2.on_train_epoch_end.side_effect = lambda *a: call_order.append("cb2.epoch_end")

        composed = ComposedCallbacks(cb1, cb2)
        agent, progress, eval_info = MagicMock(), MagicMock(), []

        composed.on_train_begin(agent, progress, eval_info)
        composed.on_train_epoch_begin(agent, progress)
        composed.on_train_epoch_end(agent, progress, eval_info)
        composed.on_train_end(agent, progress, eval_info)

        assert call_order == [
            "cb1.begin",
            "cb2.begin",
            "cb1.epoch_begin",
            "cb2.epoch_begin",
            "cb1.epoch_end",
            "cb2.epoch_end",
            "cb1.end",
            "cb2.end",
        ]

    @staticmethod
    def test_exception_isolation():
        """Exception in first callback does not prevent second from running."""
        cb1 = MagicMock(spec=Callbacks)
        cb1.on_train_epoch_end.side_effect = RuntimeError("boom")
        cb2 = MagicMock(spec=Callbacks)

        composed = ComposedCallbacks(cb1, cb2)
        # Should not raise
        composed.on_train_epoch_end(MagicMock(), MagicMock(), [])

        cb2.on_train_epoch_end.assert_called_once()

    @staticmethod
    def test_empty_composition():
        """Zero callbacks → no errors."""
        composed = ComposedCallbacks()
        agent, progress, eval_info = MagicMock(), MagicMock(), []
        composed.on_train_begin(agent, progress, eval_info)
        composed.on_train_end(agent, progress, eval_info)
        composed.on_train_epoch_begin(agent, progress)
        composed.on_train_epoch_end(agent, progress, eval_info)

    @staticmethod
    def test_single_callback():
        """Single callback works correctly."""
        cb = MagicMock(spec=Callbacks)
        composed = ComposedCallbacks(cb)
        composed.on_train_begin(MagicMock(), MagicMock(), [])
        cb.on_train_begin.assert_called_once()

    @staticmethod
    def test_exception_in_all_hooks():
        """All 4 hooks isolate exceptions."""
        cb1 = MagicMock(spec=Callbacks)
        cb1.on_train_begin.side_effect = RuntimeError("begin")
        cb1.on_train_end.side_effect = RuntimeError("end")
        cb1.on_train_epoch_begin.side_effect = RuntimeError("epoch_begin")
        cb1.on_train_epoch_end.side_effect = RuntimeError("epoch_end")

        cb2 = MagicMock(spec=Callbacks)
        composed = ComposedCallbacks(cb1, cb2)

        agent, progress, eval_info = MagicMock(), MagicMock(), []
        composed.on_train_begin(agent, progress, eval_info)
        composed.on_train_end(agent, progress, eval_info)
        composed.on_train_epoch_begin(agent, progress)
        composed.on_train_epoch_end(agent, progress, eval_info)

        cb2.on_train_begin.assert_called_once()
        cb2.on_train_end.assert_called_once()
        cb2.on_train_epoch_begin.assert_called_once()
        cb2.on_train_epoch_end.assert_called_once()
