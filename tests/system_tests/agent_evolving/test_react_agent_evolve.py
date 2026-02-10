# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Agent Evolving System Tests

End-to-end tests require LLM API environment variables:
- API_BASE: API address
- API_KEY: API key
- MODEL_NAME: Model name
- MODEL_PROVIDER: Model provider (OpenAI, SiliconFlow)

Reference: agent_evo_examples/example_end_to_end_react_agent.py
"""

import asyncio
import os
import tempfile

import pytest

from openjiuwen.agent_evolving import (
    Case,
    CaseLoader,
    DefaultEvaluator,
    InstructionOptimizer,
    Trainer,
    SingleDimUpdater,
    TuneConstant,
)
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.single_agent import ReActAgentEvolve, ReActAgentConfig, AgentCard
from openjiuwen.agent_evolving.trainer.progress import Callbacks


# LLM API Config
API_BASE = os.getenv("API_BASE", "")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
MODEL_TEMPERATURE = float(os.getenv("MODEL_TEMPERATURE", "0.3"))
MODEL_TIMEOUT = int(os.getenv("MODEL_TIMEOUT", "120"))
os.environ.setdefault("LLM_SSL_VERIFY", "false")


def _has_llm_config() -> bool:
    """Check if LLM API is configured"""
    return bool(API_BASE and API_KEY and MODEL_NAME)


def _create_model_config() -> ModelRequestConfig:
    """Create model request config"""
    return ModelRequestConfig(
        model=MODEL_NAME,
        temperature=MODEL_TEMPERATURE,
        max_tokens=1000,
        top_p=0.9,
    )


def _create_model_client_config() -> ModelClientConfig:
    """Create model client config"""
    return ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=MODEL_TIMEOUT,
        verify_ssl=False,
    )


def _create_evaluator() -> DefaultEvaluator:
    """Create evaluator"""
    return DefaultEvaluator(
        model_config=_create_model_config(),
        model_client_config=_create_model_client_config(),
        metric="",
    )


def _create_optimizer() -> InstructionOptimizer:
    """Create optimizer"""
    return InstructionOptimizer(
        model_config=_create_model_config(),
        model_client_config=_create_model_client_config(),
    )


def _create_updater() -> SingleDimUpdater:
    """Create Updater"""
    return SingleDimUpdater(optimizer=_create_optimizer())


def _create_react_agent(agent_id: str = "demo_agent") -> ReActAgentEvolve:
    """Create ReActAgentEvolve"""
    agent_card = AgentCard(
        id=agent_id,
        name=f"{agent_id.title()}",
        description=f"{agent_id} for testing",
    )

    config = ReActAgentConfig()
    config.configure_model_client(
        provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        model_name=MODEL_NAME,
    )
    config.configure_prompt_template(
        [
            {"role": "system", "content": "You are a helpful AI assistant."},
            {"role": "user", "content": "{{query}}"},
        ]
    )
    config.configure_max_iterations(TuneConstant.default_iteration_num)

    agent = ReActAgentEvolve(card=agent_card)
    agent.configure(config)
    return agent


def _create_simple_qa_cases() -> CaseLoader:
    """Create QA test cases"""
    cases = [
        Case(inputs={"query": "什么是机器学习？"}, label={"answer": "机器学习是 AI 分支。"}),
        Case(inputs={"query": "Python 如何读取文件？"}, label={"answer": "使用 open() 函数。"}),
        Case(inputs={"query": "水的化学式是什么？"}, label={"answer": "H₂O。"}),
        Case(inputs={"query": "光速是多少？"}, label={"answer": "3×10⁸ 米/秒。"}),
        Case(inputs={"query": "地球直径是多少？"}, label={"answer": "12,742 公里。"}),
    ]
    return CaseLoader(cases)


def _create_simple_qa_cases_for_checkpoint() -> CaseLoader:
    """Create QA test cases for checkpoint"""
    cases = [
        Case(inputs={"query": "问题1"}, label={"answer": "答案1"}),
        Case(inputs={"query": "问题2"}, label={"answer": "答案2"}),
        Case(inputs={"query": "问题3"}, label={"answer": "答案3"}),
    ]
    return CaseLoader(cases)


class TrainingMonitor(Callbacks):
    """Training monitor callback"""

    def __init__(self):
        super().__init__()
        self.begin_called = False
        self.end_called = False
        self.best_score = 0.0
        self.score_history = []

    def on_train_begin(self, agent, progress, eval_info):
        self.begin_called = True

    def on_train_epoch_end(self, agent, progress, eval_info):
        self.score_history.append(progress.current_epoch_score)
        self.best_score = max(self.best_score, progress.best_score)

    def on_train_end(self, agent, progress, eval_info):
        self.end_called = True


@pytest.fixture
def runner():
    """Pytest fixture for Runner setup/teardown"""
    from openjiuwen.core.runner import Runner

    asyncio.run(Runner.start())
    yield
    asyncio.run(Runner.stop())


@pytest.mark.skipif(not _has_llm_config(), reason="Requires LLM API configuration")
def test_agent_creation(runner):
    """Test Agent creation and invoke"""
    agent = _create_react_agent("test_agent")
    assert agent.card.id == "test_agent"

    result = asyncio.run(agent.invoke({"query": "What is Python?"}))
    assert result is not None


@pytest.mark.skipif(not _has_llm_config(), reason="Requires LLM API configuration")
def test_end_to_end_training(runner):
    """End-to-end training test"""
    agent = _create_react_agent("train_demo")
    train_loader, val_loader = _create_simple_qa_cases().split(ratio=0.6)

    with tempfile.TemporaryDirectory() as tmpdir:
        trainer = Trainer(
            updater=_create_updater(),
            evaluator=_create_evaluator(),
            num_parallel=2,
            early_stop_score=0.95,
            checkpoint_dir=tmpdir,
            checkpoint_every_n_epochs=1,
        )

        evolved = trainer.train(
            agent=agent,
            train_cases=train_loader,
            val_cases=val_loader,
            num_iterations=3,
        )

        assert evolved is not None


@pytest.mark.skipif(not _has_llm_config(), reason="Requires LLM API configuration")
def test_training_with_callbacks(runner):
    """Test training with callbacks"""
    agent = _create_react_agent("callback_demo")
    train_loader, val_loader = _create_simple_qa_cases().split(ratio=0.6)

    trainer = Trainer(
        updater=_create_updater(),
        evaluator=_create_evaluator(),
        num_parallel=2,
        early_stop_score=0.95,
    )

    monitor = TrainingMonitor()
    trainer.set_callbacks(monitor)

    trainer.train(
        agent=agent,
        train_cases=train_loader,
        val_cases=val_loader,
        num_iterations=2,
    )

    assert monitor.begin_called
    assert monitor.end_called
    assert monitor.best_score >= 0.0


@pytest.mark.skipif(not _has_llm_config(), reason="Requires LLM API configuration")
def test_evolved_agent_inference(runner):
    """Test evolved agent inference"""
    agent = _create_react_agent("inference_demo")
    train_loader, val_loader = _create_simple_qa_cases().split(ratio=0.6)

    trainer = Trainer(
        updater=_create_updater(),
        evaluator=_create_evaluator(),
        num_parallel=2,
        early_stop_score=0.95,
    )

    evolved = trainer.train(
        agent=agent,
        train_cases=train_loader,
        val_cases=val_loader,
        num_iterations=2,
    )

    test_queries = [
        "Please introduce machine learning.",
        "Python how to write file?",
    ]

    for query in test_queries:
        result = asyncio.run(evolved.invoke({"query": query}))
        assert result is not None


@pytest.mark.skipif(not _has_llm_config(), reason="Requires LLM API configuration")
def test_checkpoint_save_and_resume(runner):
    """Test checkpoint save and resume"""
    agent = _create_react_agent("checkpoint_demo")
    cases = _create_simple_qa_cases_for_checkpoint()
    train_loader, val_loader = cases.split(ratio=0.6)

    with tempfile.TemporaryDirectory() as tmpdir:
        trainer = Trainer(
            updater=_create_updater(),
            evaluator=_create_evaluator(),
            num_parallel=2,
            early_stop_score=0.95,
            checkpoint_dir=tmpdir,
            checkpoint_every_n_epochs=1,
            checkpoint_on_improve=True,
        )

        trainer.train(
            agent=agent,
            train_cases=train_loader,
            val_cases=val_loader,
            num_iterations=2,
        )

        # Verify checkpoint files
        assert len(os.listdir(tmpdir)) > 0

        # Resume training from checkpoint
        agent2 = _create_react_agent("checkpoint_demo_2")
        trainer2 = Trainer(
            updater=_create_updater(),
            evaluator=_create_evaluator(),
            num_parallel=2,
            early_stop_score=0.95,
            checkpoint_dir=tmpdir,
            checkpoint_every_n_epochs=1,
            checkpoint_on_improve=True,
        )

        evolved = trainer2.train(
            agent=agent2,
            train_cases=train_loader,
            val_cases=val_loader,
            num_iterations=2,
        )

        assert evolved is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
