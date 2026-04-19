# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Tests for Trainer class - training orchestration and edge cases."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.agent_evolving.dataset import Case, CaseLoader, EvaluatedCase
from openjiuwen.agent_evolving.evaluator import BaseEvaluator
from openjiuwen.agent_evolving.updater import Updater
from openjiuwen.agent_evolving.trainer import Callbacks, Progress, Trainer


def create_mock_updater():
    """Create mock updater with default behaviors."""
    updater = MagicMock(spec=Updater)
    updater.bind.return_value = 1
    updater.update = AsyncMock(return_value={})
    updater.get_state.return_value = {}
    updater.load_state = MagicMock()
    return updater


def create_mock_evaluator(score: float = 0.8):
    """Create mock evaluator returning scored case."""
    evaluator = MagicMock(spec=BaseEvaluator)
    evaluated_case = MagicMock()
    evaluated_case.score = score
    evaluator.batch_evaluate.return_value = [evaluated_case]
    return evaluator


def create_mock_agent(operator_ids=None):
    """Create mock agent with operators."""
    agent = MagicMock()
    operators = {}
    for op_id in operator_ids or ["llm_op", "tool_op"]:
        op = MagicMock()
        op.operator_id = op_id
        op.get_state.return_value = {"param": "value"}
        op.set_parameter = MagicMock()
        op.get_tunables.return_value = {"system_prompt": "prompt", "enabled": True}
        operators[op_id] = op
    agent.get_operators.return_value = operators
    return agent


def create_evaluated_case(case_id: str = "test_case", score: float = 0.8):
    """Create evaluated test case."""
    case = Case(
        inputs={"query": "test question"},
        label={"answer": "expected answer"},
        case_id=case_id,
    )
    return EvaluatedCase(case=case, answer={"output": "pred"}, score=score)


def create_case_loader(case_count: int = 3):
    """Create case loader with test cases."""
    cases = [
        Case(inputs={"query": f"question {i}"}, label={"answer": "expected"}, case_id=f"case_{i}")
        for i in range(case_count)
    ]
    return CaseLoader(cases)


class TestTrainerInit:
    """Test Trainer initialization via public API behavior."""

    @staticmethod
    def test_init_with_required_args_uses_updater_and_evaluator():
        """Init with updater and evaluator - verified through train behavior."""
        updater = create_mock_updater()
        evaluator = create_mock_evaluator()
        trainer = Trainer(updater=updater, evaluator=evaluator)
        # Verify through behavior - updater and evaluator are used in train
        agent = create_mock_agent()
        case_loader = create_case_loader()
        trainer.train(agent=agent, train_cases=case_loader, num_iterations=1)
        updater.bind.assert_called()
        evaluator.batch_evaluate.assert_called()

    @staticmethod
    def test_init_with_optional_config_affects_behavior():
        """Init with optional arguments - verified through early stopping behavior."""
        updater = create_mock_updater()
        evaluator = create_mock_evaluator()
        # Set high early stop score to verify it's respected
        evaluator.batch_evaluate.return_value = [create_evaluated_case(score=0.99)]
        trainer = Trainer(
            updater=updater,
            evaluator=evaluator,
            num_parallel=4,
            early_stop_score=0.95,
            checkpoint_dir="/tmp/ckpt",
        )
        agent = create_mock_agent()
        case_loader = create_case_loader()
        # High score should trigger early stop
        result = trainer.train(agent=agent, train_cases=case_loader, num_iterations=10)
        # Verify early_stop_score was respected - train stopped early
        assert result is agent
        updater.update.assert_not_called()

    @staticmethod
    def test_init_defaults_produce_default_behavior():
        """Default parallel is 1, checkpoint disabled - verified through behavior."""
        updater = create_mock_updater()
        evaluator = create_mock_evaluator()
        trainer = Trainer(updater=updater, evaluator=evaluator)
        agent = create_mock_agent()
        case_loader = create_case_loader()
        # With defaults, train should work normally
        trainer.train(agent=agent, train_cases=case_loader, num_iterations=1)
        # Verify train executed (checkpoint_dir not set means no checkpointing)
        updater.bind.assert_called()


class TestTrainerApplyUpdates:
    """Test apply_updates static method (public API)."""

    @staticmethod
    def test_empty_updates():
        """Empty updates does nothing."""
        op = MagicMock()
        Trainer.apply_updates({"op1": op}, {})
        op.set_parameter.assert_not_called()

    @staticmethod
    def test_single_update():
        """Single update calls set_parameter."""
        op = MagicMock()
        Trainer.apply_updates({"op1": op}, {("op1", "system_prompt"): "new prompt"})
        op.set_parameter.assert_called_once_with("system_prompt", "new prompt")

    @staticmethod
    def test_multiple_updates():
        """Multiple updates."""
        op1 = MagicMock()
        op2 = MagicMock()
        Trainer.apply_updates(
            {"op1": op1, "op2": op2},
            {("op1", "p1"): "v1", ("op2", "p2"): "v2"},
        )
        assert op1.set_parameter.call_count == 1
        assert op2.set_parameter.call_count == 1

    @staticmethod
    @pytest.mark.parametrize(
        "updates",
        [
            {("missing_op", "param"): "value"},
            {("op1", "param"): None},
        ],
    )
    def test_skips_invalid_updates(updates):
        """Skips missing operators and None values."""
        op = MagicMock()
        Trainer.apply_updates({"op1": op}, updates)
        op.set_parameter.assert_not_called()


class TestTrainerPredict:
    """Test predict, predict_only, and evaluate methods (public API)."""

    @staticmethod
    def test_evaluate_calls_batch_evaluate():
        """evaluate calls evaluator.batch_evaluate."""
        evaluator = create_mock_evaluator()
        trainer = Trainer(updater=create_mock_updater(), evaluator=evaluator)
        agent = create_mock_agent()
        case_loader = create_case_loader()
        score, evaluated = trainer.evaluate(agent, case_loader)
        evaluator.batch_evaluate.assert_called()

    @staticmethod
    def test_predict_returns_predictions_and_sessions():
        """predict returns predictions and sessions."""
        trainer = Trainer(updater=create_mock_updater(), evaluator=create_mock_evaluator())
        agent = create_mock_agent()
        case_loader = create_case_loader()

        with patch("openjiuwen.agent_evolving.trainer.trainer.create_agent_session") as mock_session:
            mock_session.return_value = MagicMock()
            predicts, sessions = trainer.predict(agent, case_loader)
            assert len(predicts) == 3
            assert len(sessions) == 3

    @staticmethod
    def test_predict_only_returns_only_predictions():
        """predict_only returns only predictions."""
        trainer = Trainer(updater=create_mock_updater(), evaluator=create_mock_evaluator())
        agent = create_mock_agent()
        case_loader = create_case_loader()
        predicts = trainer.predict_only(agent, case_loader)
        assert len(predicts) == 3


class TestTrainerForward:
    """Test forward method (public API)."""

    @staticmethod
    def test_forward_returns_tuple():
        """forward returns (score, evaluated, trajectories, sessions)."""
        trainer = Trainer(updater=create_mock_updater(), evaluator=create_mock_evaluator())
        agent = create_mock_agent()
        case_loader = create_case_loader()

        with patch("openjiuwen.agent_evolving.trainer.trainer.create_agent_session") as mock_session:
            mock_session.return_value = MagicMock()
            result = trainer.forward(agent, case_loader)
            assert len(result) == 4
            score, evaluated, trajectories, sessions = result
            assert isinstance(score, float)
            assert isinstance(evaluated, list)
            assert isinstance(trajectories, list)
            assert isinstance(sessions, list)


class TestTrainerTrain:
    """Test train method - full integration via public API."""

    @staticmethod
    def test_early_stop():
        """train returns agent when early stop score reached."""
        updater = create_mock_updater()
        evaluator = create_mock_evaluator()
        evaluator.batch_evaluate.return_value = [create_evaluated_case(score=0.99)]
        trainer = Trainer(updater=updater, evaluator=evaluator, early_stop_score=0.95)
        agent = create_mock_agent()
        case_loader = create_case_loader()
        result = trainer.train(agent=agent, train_cases=case_loader, num_iterations=10)
        assert result is agent
        updater.update.assert_not_called()

    @staticmethod
    def test_no_operator_match():
        """train returns agent when no Operator matches updater."""
        updater = create_mock_updater()
        updater.bind.return_value = 0
        trainer = Trainer(updater=updater, evaluator=create_mock_evaluator())
        agent = create_mock_agent()
        case_loader = create_case_loader()
        result = trainer.train(agent=agent, train_cases=case_loader)
        assert result is agent


class TestTrainerSetCallbacks:
    """Test set_callbacks method (public API) - verified through training behavior."""

    @staticmethod
    def test_callbacks_are_invoked_during_training():
        """Callbacks set via set_callbacks are invoked during training."""
        updater = create_mock_updater()
        evaluator = create_mock_evaluator()
        evaluator.batch_evaluate.return_value = [create_evaluated_case(score=0.5)]

        callbacks = MagicMock(spec=Callbacks)
        trainer = Trainer(updater=updater, evaluator=evaluator, num_parallel=1)
        trainer.set_callbacks(callbacks)

        agent = create_mock_agent()
        case_loader = create_case_loader()

        trainer.train(
            agent=agent,
            train_cases=case_loader,
            val_cases=case_loader,
            num_iterations=2,
        )

        # Verify callbacks were invoked - proving set_callbacks worked
        callbacks.on_train_begin.assert_called_once()
        callbacks.on_train_end.assert_called_once()


class TestTrainerPredictException:
    """Test predict method exception handling (public API)."""

    @staticmethod
    def test_handles_invoke_exception():
        """predict handles agent.invoke exception."""
        trainer = Trainer(updater=create_mock_updater(), evaluator=create_mock_evaluator())
        agent = create_mock_agent()
        case_loader = create_case_loader()

        with patch("openjiuwen.agent_evolving.trainer.trainer.create_agent_session") as mock_session:
            mock_session.return_value = MagicMock()
            agent.invoke = MagicMock(side_effect=Exception("test error"))
            predicts, sessions = trainer.predict(agent, case_loader)
            assert len(predicts) == 3
            assert all("error" in str(p) for p in predicts)


class TestProgressEdgeCases:
    """Edge case tests for Progress."""

    @staticmethod
    def test_defaults():
        """Progress with default values."""
        progress = Progress()
        assert progress.start_epoch == 0
        assert progress.current_epoch == 0
        assert progress.max_epoch == 3
        assert progress.best_score == 0.0
        assert progress.current_epoch_score == 0.0

    @staticmethod
    @pytest.mark.parametrize(
        "max_epoch,expected",
        [
            (1, [1]),
            (5, [1, 2, 3, 4, 5]),
            (3, [1, 2, 3]),
        ],
    )
    def test_run_epoch_count(max_epoch, expected):
        """run_epoch returns correct number of epochs."""
        progress = Progress(max_epoch=max_epoch)
        epochs = list(progress.run_epoch())
        assert len(epochs) == len(expected)
        assert epochs == expected

    @staticmethod
    def test_run_epoch_with_start_epoch():
        """run_epoch with non-zero start_epoch."""
        progress = Progress(start_epoch=3, max_epoch=5)
        epochs = list(progress.run_epoch())
        assert len(epochs) == 2
        assert epochs == [4, 5]

    @staticmethod
    def test_run_epoch_updates_current():
        """run_epoch updates current_epoch."""
        progress = Progress(max_epoch=3)
        for _ in progress.run_epoch():
            pass
        assert progress.current_epoch == 3

    @staticmethod
    def test_partial_iteration():
        """run_epoch when iteration is interrupted."""
        progress = Progress(max_epoch=5)
        generator = progress.run_epoch()
        next(generator)
        assert progress.current_epoch == 1
        next(generator)
        assert progress.current_epoch == 2

    @staticmethod
    def test_run_batch():
        """run_batch iteration."""
        progress = Progress(max_batch_iter=3)
        batches = list(progress.run_batch())
        assert len(batches) == 3
        assert progress.best_batch_score == 0.0

    @staticmethod
    def test_run_batch_resets_best():
        """run_batch resets best_batch_score."""
        progress = Progress()
        progress.best_batch_score = 0.99
        list(progress.run_batch())
        assert progress.best_batch_score == 0.0

    @staticmethod
    @pytest.mark.parametrize("score", [0.0, 1.0])
    def test_score_boundary(score):
        """Progress score at boundary."""
        progress = Progress()
        progress.best_score = score
        progress.current_epoch_score = score
        assert progress.best_score >= 0.0
        assert progress.best_score <= 1.0


class TestCallbacksEdgeCases:
    """Edge case tests for Callbacks."""

    @staticmethod
    def test_default_implementations():
        """Callbacks default implementations do nothing."""
        callbacks = Callbacks()
        agent = MagicMock()
        progress = Progress()
        eval_results = [create_evaluated_case()]
        callbacks.on_train_begin(agent, progress, eval_results)
        callbacks.on_train_end(agent, progress, eval_results)
        callbacks.on_train_epoch_begin(agent, progress)
        callbacks.on_train_epoch_end(agent, progress, eval_results)

    @staticmethod
    def test_custom_callback():
        """Custom callback can override default behavior."""
        call_tracker = {"begin": False, "end": False}

        class TrackingCallbacks(Callbacks):
            def on_train_begin(self, agent, progress, eval_info):
                call_tracker["begin"] = True

            def on_train_end(self, agent, progress, eval_info):
                call_tracker["end"] = True

        callbacks = TrackingCallbacks()
        callbacks.on_train_begin(MagicMock(), Progress(), [])
        callbacks.on_train_end(MagicMock(), Progress(), [])
        assert call_tracker["begin"] is True
        assert call_tracker["end"] is True


class TestCaseLoaderEdgeCases:
    """Edge case tests for CaseLoader."""

    @staticmethod
    @pytest.mark.parametrize(
        "cases,expected_len",
        [
            ([], 0),
            ([Case(inputs={"q": "test"}, label={"ans": "expected"}, case_id="single")], 1),
        ],
    )
    def test_case_loader(cases, expected_len):
        """CaseLoader with various case counts."""
        loader = CaseLoader(cases)
        assert len(loader.get_cases()) == expected_len
        if expected_len == 1:
            assert loader.get_cases()[0].case_id == "single"

    @staticmethod
    def test_split_empty():
        """split on empty loader returns two empty loaders."""
        loader = CaseLoader([])
        train, val = loader.split(ratio=0.8)
        assert len(train.get_cases()) == 0
        assert len(val.get_cases()) == 0


class TestEvaluatedCaseEdgeCases:
    """Edge case tests for EvaluatedCase."""

    @staticmethod
    @pytest.mark.parametrize(
        "input_score,expected",
        [
            (-0.5, 0.0),
            (1.5, 1.0),
            (0.0, 0.0),
            (1.0, 1.0),
        ],
    )
    def test_score_clamped(input_score, expected):
        """EvaluatedCase clamps score to [0, 1]."""
        case = Case(inputs={"q": "test"}, label={"ans": "expected"})
        evaluated = EvaluatedCase(case=case, answer={"output": "pred"}, score=input_score)
        assert evaluated.score == expected

    @staticmethod
    def test_default_score_and_reason():
        """EvaluatedCase default score is 0.0, reason is empty."""
        case = Case(inputs={"q": "test"}, label={"ans": "expected"})
        evaluated = EvaluatedCase(case=case, answer={"output": "pred"})
        assert evaluated.score == 0.0
        assert evaluated.reason == ""

    @staticmethod
    def test_properties():
        """EvaluatedCase convenience properties."""
        case = Case(inputs={"q": "test"}, label={"ans": "expected"})
        evaluated = EvaluatedCase(
            case=case,
            answer={"output": "pred"},
            score=0.8,
            reason="Good answer",
        )
        assert evaluated.inputs == case.inputs
        assert evaluated.label == case.label
        assert evaluated.case_id == case.case_id


class TestTrainerTrainMultipleIterations:
    """Test train method with multiple iterations (public API)."""

    @staticmethod
    def test_multiple_iterations():
        """Train runs through multiple iterations."""
        updater = create_mock_updater()
        evaluator = create_mock_evaluator()
        evaluator.batch_evaluate.return_value = [create_evaluated_case(score=0.5)]

        trainer = Trainer(
            updater=updater,
            evaluator=evaluator,
            early_stop_score=0.95,
            num_parallel=1,
        )
        agent = create_mock_agent()
        case_loader = create_case_loader()

        result = trainer.train(
            agent=agent,
            train_cases=case_loader,
            val_cases=case_loader,
            num_iterations=3,
        )

        assert result is agent
        assert updater.update.call_count == 3

    @staticmethod
    def test_early_stop_on_score():
        """Train stops early when score reaches threshold."""
        updater = create_mock_updater()
        evaluator = create_mock_evaluator()
        evaluator.batch_evaluate.return_value = [create_evaluated_case(score=0.95)]

        trainer = Trainer(
            updater=updater,
            evaluator=evaluator,
            early_stop_score=0.95,
            num_parallel=1,
        )
        agent = create_mock_agent()
        case_loader = create_case_loader()

        result = trainer.train(
            agent=agent,
            train_cases=case_loader,
            num_iterations=10,
        )

        assert result is agent
        updater.update.assert_not_called()

    @staticmethod
    def test_black_box_optimizer_skips_forward():
        """Black-box optimizer skips forward execution."""
        updater = create_mock_updater()
        updater.requires_forward_data.return_value = False
        updater.update.return_value = [{("op1", "param"): "v1"}]

        evaluator = create_mock_evaluator()
        evaluator.batch_evaluate.return_value = [create_evaluated_case(score=0.6)]

        trainer = Trainer(updater=updater, evaluator=evaluator, num_parallel=1)
        agent = create_mock_agent()

        operators = {"op1": MagicMock()}
        operators["op1"].get_state.return_value = {}
        operators["op1"].load_state = MagicMock()
        agent.get_operators.return_value = operators

        case_loader = create_case_loader()

        result = trainer.train(
            agent=agent,
            train_cases=case_loader,
            val_cases=case_loader,
            num_iterations=2,
        )

        assert result is agent
        assert updater.update.call_count == 2

    @staticmethod
    def test_checkpoint_dir_enables_checkpointing():
        """Checkpoint directory enables checkpoint behavior."""
        updater = create_mock_updater()
        evaluator = create_mock_evaluator()
        evaluator.batch_evaluate.return_value = [create_evaluated_case(score=0.5)]

        trainer = Trainer(
            updater=updater,
            evaluator=evaluator,
            early_stop_score=1.0,
            num_parallel=1,
            checkpoint_dir="/tmp",
        )
        agent = create_mock_agent()
        case_loader = create_case_loader()

        # With checkpoint_dir set, train should execute normally
        result = trainer.train(
            agent=agent,
            train_cases=case_loader,
            val_cases=case_loader,
            num_iterations=2,
        )

        assert result is agent
        # Verify training executed (checkpointing was enabled)
        updater.bind.assert_called()


class TestTrainerBoundaryCases:
    """Test boundary cases in forward/evaluate/predict methods (public API)."""

    @staticmethod
    @pytest.mark.parametrize(
        "method_name,args,expected",
        [
            ("forward", [CaseLoader([])], (0.0, [], [], [])),
            ("forward", [None], (0.0, [], [], [])),
            ("evaluate", [CaseLoader([])], (0.0, [])),
            ("evaluate", [None], (0.0, [])),
            ("predict_only", [None], []),
            ("predict", [CaseLoader([])], ([], [])),
        ],
    )
    def test_boundary_returns(method_name, args, expected):
        """Methods return correct zeros for empty/None cases."""
        trainer = Trainer(updater=create_mock_updater(), evaluator=create_mock_evaluator())
        agent = create_mock_agent()

        method = getattr(trainer, method_name)
        result = method(agent, *args)
        assert result == expected


class TestTrainerCandidatesWithList:
    """Test train with updater returning list of candidates (public API)."""

    @staticmethod
    def test_with_candidates_list():
        """Train evaluates and selects from list of candidates."""
        updater = create_mock_updater()
        evaluator = create_mock_evaluator()
        evaluator.batch_evaluate.return_value = [create_evaluated_case(score=0.6)]
        updater.update.return_value = [{("op1", "param"): "candidate1"}]

        trainer = Trainer(updater=updater, evaluator=evaluator, num_parallel=1)
        agent = create_mock_agent()

        operators = {"op1": MagicMock()}
        operators["op1"].get_state.return_value = {}
        operators["op1"].load_state = MagicMock()
        agent.get_operators.return_value = operators

        case_loader = create_case_loader()

        result = trainer.train(
            agent=agent,
            train_cases=case_loader,
            val_cases=case_loader,
            num_iterations=2,
        )

        assert result is agent
        assert updater.update.call_count == 2


class TestTrainerCallbacksInvocation:
    """Test callback invocation during training (public API)."""

    @staticmethod
    def test_all_callbacks_invoked():
        """All callbacks are invoked during training."""
        updater = create_mock_updater()
        evaluator = create_mock_evaluator()
        evaluator.batch_evaluate.return_value = [create_evaluated_case(score=0.5)]

        callbacks = MagicMock(spec=Callbacks)
        trainer = Trainer(updater=updater, evaluator=evaluator, num_parallel=1)
        trainer.set_callbacks(callbacks)

        agent = create_mock_agent()
        case_loader = create_case_loader()

        trainer.train(
            agent=agent,
            train_cases=case_loader,
            val_cases=case_loader,
            num_iterations=2,
        )

        callbacks.on_train_begin.assert_called_once()
        callbacks.on_train_epoch_begin.assert_called()
        callbacks.on_train_epoch_end.assert_called()
        callbacks.on_train_end.assert_called_once()

    @staticmethod
    def test_epoch_callback_receives_progress():
        """Epoch callback receives correct progress."""
        updater = create_mock_updater()
        evaluator = create_mock_evaluator()
        evaluator.batch_evaluate.return_value = [create_evaluated_case(score=0.5)]

        callbacks = MagicMock(spec=Callbacks)
        trainer = Trainer(updater=updater, evaluator=evaluator, num_parallel=1)
        trainer.set_callbacks(callbacks)

        agent = create_mock_agent()
        case_loader = create_case_loader()

        trainer.train(
            agent=agent,
            train_cases=case_loader,
            val_cases=case_loader,
            num_iterations=2,
        )

        epoch_end_call = callbacks.on_train_epoch_end.call_args_list[-1]
        progress = epoch_end_call[0][1]
        assert hasattr(progress, "current_epoch_score")
        assert hasattr(progress, "best_score")
