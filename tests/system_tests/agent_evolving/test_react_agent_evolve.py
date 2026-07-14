# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""System tests for ReAct agent evolution using mock LLM responses."""

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator

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
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    AssistantMessageChunk,
    BaseMessage,
    BaseOutputParser,
    ModelRequestConfig,
    ModelClientConfig,
)
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.single_agent import ReActAgentEvolve, ReActAgentConfig, AgentCard
from openjiuwen.agent_evolving.trainer.progress import Callbacks
from tests.unit_tests.fixtures.mock_llm import (
    MockLLMModel,
    create_json_response,
    create_text_response,
)

SYSTEM_TEST_MOCK_PROVIDER = "SystemReActEvolveMockLLM"
MessagePayload = str | list[BaseMessage] | list[dict]
ToolPayload = list[ToolInfo] | list[dict] | None

MOCK_AGENT_RESPONSE = create_text_response("This is a mock agent answer.")
MOCK_EVAL_RESPONSE = create_json_response(
    {"result": False, "reason": "mock low score"},
)
MOCK_GRADIENT_RESPONSE = create_text_response(
    "Improve prompt clarity and answer completeness.",
)
MOCK_PROMPT_RESPONSE = create_text_response(
    "<PROMPT_OPTIMIZED>Refined prompt to improve instruction compliance.</PROMPT_OPTIMIZED>",
)
os.environ.setdefault("LLM_SSL_VERIFY", "false")


def _messages_to_text(messages) -> str:
    """Convert message payload into plain text for simple pattern matching."""
    if isinstance(messages, str):
        return messages
    if not isinstance(messages, list):
        return ""

    parts = []
    for message in messages:
        if isinstance(message, dict):
            value = message.get("content", "")
        else:
            value = getattr(message, "content", "")
        if value:
            parts.append(str(value))

    return " ".join(parts)


def _infer_mock_call_type(messages) -> str:
    """Infer which component is invoking the LLM in this call."""
    content = _messages_to_text(messages).lower()

    if "expected answer" in content and "model answer" in content:
        return "evaluator"
    if "<ins>" in content or "detailed feedback" in content:
        return "optimizer_gradient"
    if "prompt optimization expert" in content:
        return "optimizer_prompt"

    return "agent"


def _mock_response_for(messages):
    kind = _infer_mock_call_type(messages)
    if kind == "evaluator":
        return MOCK_EVAL_RESPONSE
    if kind == "optimizer_gradient":
        return MOCK_GRADIENT_RESPONSE
    if kind == "optimizer_prompt":
        return MOCK_PROMPT_RESPONSE
    return MOCK_AGENT_RESPONSE


class _SystemTestMockLLM(MockLLMModel):
    """Local mock LLM client with deterministic content-routed responses."""

    __client_name__ = SYSTEM_TEST_MOCK_PROVIDER

    async def invoke(
        self,
        messages: MessagePayload,
        *,
        tools: ToolPayload = None,
        temperature: float | None = None,
        top_p: float | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        stop: str | None = None,
        output_parser: BaseOutputParser | None = None,
        timeout: float | None = None,
        **kwargs,
    ) -> AssistantMessage:
        return _mock_response_for(messages)

    async def stream(
        self,
        messages: MessagePayload,
        *,
        tools: ToolPayload = None,
        temperature: float | None = None,
        top_p: float | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        stop: str | None = None,
        output_parser: BaseOutputParser | None = None,
        timeout: float | None = None,
        **kwargs,
    ) -> AsyncIterator[AssistantMessageChunk]:
        response = await self.invoke(messages, **kwargs)
        yield AssistantMessageChunk(
            content=response.content,
            tool_calls=response.tool_calls,
            usage_metadata=response.usage_metadata,
        )


def _create_model_config() -> ModelRequestConfig:
    """Create model request config."""
    return ModelRequestConfig(
        model="mock-model",
        temperature=0.3,
        max_tokens=1000,
        top_p=0.9,
    )


def _create_model_client_config() -> ModelClientConfig:
    """Create model client config."""
    return ModelClientConfig(
        client_provider=SYSTEM_TEST_MOCK_PROVIDER,
        api_key="mock-api-key",
        api_base="http://mock-api-base",
        timeout=30,
        verify_ssl=False,
    )


def _create_evaluator() -> DefaultEvaluator:
    """Create DefaultEvaluator."""
    return DefaultEvaluator(
        model_config=_create_model_config(),
        model_client_config=_create_model_client_config(),
        metric="",
    )


def _create_optimizer() -> InstructionOptimizer:
    """Create InstructionOptimizer."""
    return InstructionOptimizer(
        model_config=_create_model_config(),
        model_client_config=_create_model_client_config(),
    )


def _create_updater() -> SingleDimUpdater:
    """Create Updater."""
    return SingleDimUpdater(optimizer=_create_optimizer())


def _create_react_agent(agent_id: str = "demo_agent") -> ReActAgentEvolve:
    """Create ReActAgentEvolve."""
    agent_card = AgentCard(
        id=agent_id,
        name=f"{agent_id.title()}",
        description=f"{agent_id} for testing",
    )

    config = ReActAgentConfig()
    config.configure_model_client(
        provider=SYSTEM_TEST_MOCK_PROVIDER,
        api_key="mock-api-key",
        api_base="http://mock-api-base",
        model_name="mock-model",
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
    # Re-initialize operators so they pick up the configured prompt_template
    # (operators were first created in __init__ with default empty config).
    agent._init_operators()
    return agent


def _create_simple_qa_cases() -> CaseLoader:
    """Create QA test cases."""
    cases = [
        Case(inputs={"query": "什么是机器学习？"}, label={"answer": "机器学习是 AI 分支。"}),
        Case(inputs={"query": "Python 如何读取文件？"}, label={"answer": "使用 open() 函数。"}),
        Case(inputs={"query": "水的化学式是什么？"}, label={"answer": "H₂O。"}),
        Case(inputs={"query": "光速是多少？"}, label={"answer": "3×10⁸ 米/秒。"}),
        Case(inputs={"query": "地球直径是多少？"}, label={"answer": "12,742 公里。"}),
    ]
    return CaseLoader(cases)


def _create_simple_qa_cases_for_checkpoint() -> CaseLoader:
    """Create QA test cases for checkpoint."""
    cases = [
        Case(inputs={"query": "问题1"}, label={"answer": "答案1"}),
        Case(inputs={"query": "问题2"}, label={"answer": "答案2"}),
        Case(inputs={"query": "问题3"}, label={"answer": "答案3"}),
    ]
    return CaseLoader(cases)


class TrainingMonitor(Callbacks):
    """Training monitor callback."""

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


def test_agent_creation():
    """Test Agent creation and invoke."""
    agent = _create_react_agent("test_agent")
    assert agent.card.id == "test_agent"

    result = asyncio.run(agent.invoke({"query": "What is Python?"}))
    assert result is not None


def test_end_to_end_training():
    """End-to-end training test."""
    agent = _create_react_agent("train_demo")
    train_loader, val_loader = _create_simple_qa_cases().split(ratio=0.6)

    with tempfile.TemporaryDirectory() as tmpdir:
        trainer = Trainer(
            updater=_create_updater(),
            evaluator=_create_evaluator(),
            num_parallel=1,
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


def test_training_with_callbacks():
    """Test training with callbacks."""
    agent = _create_react_agent("callback_demo")
    train_loader, val_loader = _create_simple_qa_cases().split(ratio=0.6)

    trainer = Trainer(
        updater=_create_updater(),
        evaluator=_create_evaluator(),
        num_parallel=1,
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


def test_evolved_agent_inference():
    """Test evolved agent inference."""
    agent = _create_react_agent("inference_demo")
    train_loader, val_loader = _create_simple_qa_cases().split(ratio=0.6)

    trainer = Trainer(
        updater=_create_updater(),
        evaluator=_create_evaluator(),
        num_parallel=1,
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


def test_checkpoint_save_and_resume():
    """Test checkpoint save and resume."""
    agent = _create_react_agent("checkpoint_demo")
    cases = _create_simple_qa_cases_for_checkpoint()
    train_loader, val_loader = cases.split(ratio=0.6)

    with tempfile.TemporaryDirectory() as tmpdir:
        trainer = Trainer(
            updater=_create_updater(),
            evaluator=_create_evaluator(),
            num_parallel=1,
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

        assert len(os.listdir(tmpdir)) > 0

        agent2 = _create_react_agent("checkpoint_demo_2")
        trainer2 = Trainer(
            updater=_create_updater(),
            evaluator=_create_evaluator(),
            num_parallel=1,
            early_stop_score=0.95,
            checkpoint_dir=tmpdir,
            resume_from=os.path.join(tmpdir, "latest.json"),
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
