# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end tests for multi-operator SkillDocumentOptimizer with a real LLM.

Exercises the full ReflACT pipeline with TWO operators (Python + Java skills),
verifying that per-operator reflect → aggregate → select → apply works correctly
with real LLM calls.

Uses MetricEvaluator (keyword match) to keep costs low — only the optimizer
and agent invoke the LLM, not the evaluator.

Requires LLM API environment variables:
- API_BASE: API address
- API_KEY: API key
- MODEL_NAME: Model name
- MODEL_PROVIDER: Model provider (OpenAI, SiliconFlow, etc.)

Without these, all tests are skipped.
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from openjiuwen.agent_evolving.dataset import Case, CaseLoader
from openjiuwen.agent_evolving.evaluator import MetricEvaluator
from openjiuwen.agent_evolving.evaluator.metrics.base import Metric
from openjiuwen.agent_evolving.optimizer.skill_document import SkillDocumentOptimizer
from openjiuwen.agent_evolving.trainer import Trainer
from openjiuwen.agent_evolving.updater.single_dim import SingleDimUpdater
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.operator.skill_call.document_operator import SkillDocumentOperator


# ── LLM configuration ────────────────────────────────────────────────────

API_BASE = os.getenv("API_BASE", "")
API_KEY = os.getenv("API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")
MODEL_TEMPERATURE = float(os.getenv("MODEL_TEMPERATURE", "0.3"))
MODEL_TIMEOUT = int(os.getenv("MODEL_TIMEOUT", "120"))
SSL_VERIFY = os.getenv("LLM_SSL_VERIFY", "true").lower() != "false"

_skip_reason = "Requires LLM API configuration (API_BASE, API_KEY, MODEL_NAME)"
_has_llm = pytest.mark.skipif(not (API_BASE and API_KEY and MODEL_NAME), reason=_skip_reason)


def _client_config() -> ModelClientConfig:
    return ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=MODEL_TIMEOUT,
        verify_ssl=SSL_VERIFY,
    )


def _request_config() -> ModelRequestConfig:
    return ModelRequestConfig(
        model=MODEL_NAME,
        temperature=MODEL_TEMPERATURE,
        max_tokens=1000,
        top_p=0.9,
    )


def _create_llm() -> Model:
    return Model(model_client_config=_client_config(), model_config=_request_config())


# ── Metric ────────────────────────────────────────────────────────────────


class KeywordMatchMetric(Metric):
    """Score = ratio of label keywords found in prediction text."""

    @property
    def name(self) -> str:
        return "keyword_match"

    def compute(self, prediction: Any, label: Any, **kwargs: Any) -> float:
        pred_text = prediction.get("output", "") if isinstance(prediction, dict) else str(prediction)
        label_text = label.get("answer", "") if isinstance(label, dict) else str(label)
        pred_lower = pred_text.lower()
        keywords = set(label_text.lower().split())
        if not keywords:
            return 1.0
        matched = sum(1 for kw in keywords if kw in pred_lower)
        return matched / len(keywords)


# ── Agent ─────────────────────────────────────────────────────────────────


class MultiSkillDocumentAgent:
    """Agent that combines multiple operators' skills as system prompt.

    Reads the current skill content from ALL bound operators on each invoke,
    concatenating them so the LLM has access to all skill documents.
    """

    def __init__(self, llm: Model, model: str, operators: dict[str, SkillDocumentOperator]):
        self._llm = llm
        self._model = model
        self._operators = operators

    async def invoke(self, inputs: dict, session=None) -> dict:
        # Combine all operators' skills into system prompt
        skill_parts = []
        for op_id, op in self._operators.items():
            skill = op.get_state()["skill_content"]
            skill_parts.append(f"## {op_id}\n{skill}")
        combined_skill = "\n\n".join(skill_parts)

        messages = [
            {"role": "system", "content": combined_skill},
            {"role": "user", "content": inputs.get("query", "")},
        ]
        try:
            response = await self._llm.invoke(messages=messages, model=self._model)
            return {"output": response.content}
        except Exception as exc:
            return {"output": f"Error: {exc}"}

    def get_operators(self) -> dict:
        return dict(self._operators)


# ── Test data ─────────────────────────────────────────────────────────────

PYTHON_SKILL = """\
# Python 基础问答

回答 Python 相关问题。请简洁回答。
"""

JAVA_SKILL = """\
# Java 基础问答

回答 Java 相关问题。请简洁回答。
"""


def _make_cases() -> CaseLoader:
    return CaseLoader(
        [
            Case(
                inputs={"query": "Python 的 GIL 是什么？"},
                label={"answer": "全局解释器锁 Global Interpreter Lock 保证同一时刻只有一个线程执行 Python 字节码"},
                case_id="py_gil",
            ),
            Case(
                inputs={"query": "什么是 Python 列表推导式？"},
                label={"answer": "列表推导式 list comprehension 是创建列表的简洁语法 [expr for item in iterable]"},
                case_id="py_list_comp",
            ),
            Case(
                inputs={"query": "Java 的 JVM 是什么？"},
                label={"answer": "Java Virtual Machine Java 虚拟机 负责加载字节码并执行 实现跨平台运行"},
                case_id="java_jvm",
            ),
            Case(
                inputs={"query": "Java 的垃圾回收机制是怎样的？"},
                label={"answer": "垃圾回收 GC Garbage Collection 自动回收不再使用的对象 释放内存 常用分代收集"},
                case_id="java_gc",
            ),
            Case(
                inputs={"query": "Java 接口和抽象类有什么区别？"},
                label={"answer": "接口 interface 支持多实现 抽象类 abstract class 支持单继承 接口方法默认公开"},
                case_id="java_interface",
            ),
            Case(
                inputs={"query": "Python 的 with 语句做什么？"},
                label={"answer": "with 语句管理上下文 context manager 自动处理资源获取和释放"},
                case_id="py_with",
            ),
        ]
    )


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_evaluator() -> MetricEvaluator:
    return MetricEvaluator(metrics=[KeywordMatchMetric()])


def _make_optimizer(
    cases: CaseLoader,
    llm: Model,
    agent: MultiSkillDocumentAgent,
    *,
    artifact_dir: str | None = None,
    use_slow_update: bool = True,
    use_meta_skill: bool = True,
) -> SkillDocumentOptimizer:
    return SkillDocumentOptimizer(
        agent=agent,
        evaluator=_make_evaluator(),
        llm=llm,
        model=MODEL_NAME,
        train_cases=cases,
        batch_size=3,
        accumulation=1,
        steps_per_epoch=1,
        minibatch_size=3,
        edit_budget=5,
        score_threshold=0.5,
        parallelism=2,
        num_parallel=2,
        use_slow_update=use_slow_update,
        use_meta_skill=use_meta_skill,
        artifact_dir=artifact_dir,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def runner():
    from openjiuwen.core.runner import Runner

    asyncio.run(Runner.start())
    yield
    try:
        asyncio.run(Runner.stop())
    except (asyncio.CancelledError, RuntimeError):
        pass


# ── Tests ─────────────────────────────────────────────────────────────────


@_has_llm
def test_multi_operator_two_epochs(runner):
    """Run 2 epochs with 2 operators + real LLM. Both skills should evolve."""
    cases = _make_cases()
    llm = _create_llm()

    op_py = SkillDocumentOperator("python_skill", initial_content=PYTHON_SKILL)
    op_java = SkillDocumentOperator("java_skill", initial_content=JAVA_SKILL)

    operators = {op_py.operator_id: op_py, op_java.operator_id: op_java}
    agent = MultiSkillDocumentAgent(llm, MODEL_NAME, operators)

    opt = _make_optimizer(cases, llm, agent, use_slow_update=False, use_meta_skill=False)
    opt.bind(operators=operators)

    updater = SingleDimUpdater(optimizer=opt)
    trainer = Trainer(updater=updater, evaluator=_make_evaluator(), early_stop_score=0.95)

    result = trainer.train(
        agent=agent,
        train_cases=cases,
        val_cases=cases,
        num_iterations=2,
    )

    assert result is agent
    state = opt.get_state()
    assert state["global_step"] >= 2

    # Both operators should have valid skill content
    skill_py = op_py.get_state()["skill_content"]
    skill_java = op_java.get_state()["skill_content"]
    assert isinstance(skill_py, str) and len(skill_py) > 0
    assert isinstance(skill_java, str) and len(skill_java) > 0

    # Verify per-operator state is tracked
    assert len(opt._current_skill_by_operator) == 2


@_has_llm
def test_multi_operator_no_cross_contamination(runner):
    """Edits for Python skill should not appear in Java skill and vice versa."""
    cases = _make_cases()
    llm = _create_llm()

    op_py = SkillDocumentOperator("python_skill", initial_content=PYTHON_SKILL)
    op_java = SkillDocumentOperator("java_skill", initial_content=JAVA_SKILL)

    operators = {op_py.operator_id: op_py, op_java.operator_id: op_java}
    agent = MultiSkillDocumentAgent(llm, MODEL_NAME, operators)

    opt = _make_optimizer(cases, llm, agent, use_slow_update=False, use_meta_skill=False)
    opt.bind(operators=operators)

    updater = SingleDimUpdater(optimizer=opt)
    trainer = Trainer(updater=updater, evaluator=_make_evaluator(), early_stop_score=0.95)

    trainer.train(agent=agent, train_cases=cases, val_cases=cases, num_iterations=1)

    skill_py = op_py.get_state()["skill_content"]
    skill_java = op_java.get_state()["skill_content"]

    # Each skill should still contain its own domain markers
    assert "Python" in skill_py, "Python skill should still contain Python domain marker"
    assert "Java" in skill_java, "Java skill should still contain Java domain marker"


@_has_llm
def test_multi_operator_artifact_export(runner, tmp_path):
    """Artifact directory should have per-operator files after multi-operator training."""
    cases = _make_cases()
    llm = _create_llm()
    artifact_dir = str(tmp_path / "artifacts")

    op_py = SkillDocumentOperator("python_skill", initial_content=PYTHON_SKILL)
    op_java = SkillDocumentOperator("java_skill", initial_content=JAVA_SKILL)

    operators = {op_py.operator_id: op_py, op_java.operator_id: op_java}
    agent = MultiSkillDocumentAgent(llm, MODEL_NAME, operators)

    opt = _make_optimizer(
        cases,
        llm,
        agent,
        artifact_dir=artifact_dir,
        use_slow_update=False,
        use_meta_skill=False,
    )
    opt.bind(operators=operators)

    updater = SingleDimUpdater(optimizer=opt)
    trainer = Trainer(updater=updater, evaluator=_make_evaluator(), early_stop_score=0.95)

    # Register callbacks to trigger run_epoch_end
    from openjiuwen.agent_evolving.callbacks.skill_document_callbacks import SkillDocumentCallbacks

    trainer.set_callbacks(SkillDocumentCallbacks(opt))

    trainer.train(agent=agent, train_cases=cases, val_cases=cases, num_iterations=1)

    # Verify artifact directory exists
    artifact_root = Path(artifact_dir)
    assert artifact_root.exists(), "Artifact directory should be created"

    # Find all epoch directories
    epoch_dirs = sorted(artifact_root.glob("epoch_*"))
    assert len(epoch_dirs) >= 1, f"Should have at least 1 epoch dir, found: {epoch_dirs}"

    # Gate result should exist
    gate_files = list(artifact_root.rglob("gate_result.json"))
    assert len(gate_files) >= 1, "gate_result.json should exist"

    # Check for per-operator merged patch files
    merged_files = list(artifact_root.rglob("merged_patch_*.json"))
    if merged_files:
        file_names = [f.name for f in merged_files]
        has_operator_id = any("python" in name or "java" in name for name in file_names)
        assert has_operator_id, f"Should have per-operator merged patch files, found: {file_names}"


@_has_llm
def test_multi_operator_slow_update(runner):
    """After 2 epochs with slow_update, each operator should have markers."""
    cases = _make_cases()
    llm = _create_llm()

    op_py = SkillDocumentOperator("python_skill", initial_content=PYTHON_SKILL)
    op_java = SkillDocumentOperator("java_skill", initial_content=JAVA_SKILL)

    operators = {op_py.operator_id: op_py, op_java.operator_id: op_java}
    agent = MultiSkillDocumentAgent(llm, MODEL_NAME, operators)

    opt = _make_optimizer(cases, llm, agent, use_slow_update=True, use_meta_skill=True)
    opt.bind(operators=operators)

    updater = SingleDimUpdater(optimizer=opt)
    trainer = Trainer(updater=updater, evaluator=_make_evaluator(), early_stop_score=0.95)

    # Register callbacks to trigger run_epoch_end
    from openjiuwen.agent_evolving.callbacks.skill_document_callbacks import SkillDocumentCallbacks

    trainer.set_callbacks(SkillDocumentCallbacks(opt))

    trainer.train(agent=agent, train_cases=cases, val_cases=cases, num_iterations=2)

    # Both operators should have slow update markers after 2 epochs
    skill_py = op_py.get_state()["skill_content"]
    skill_java = op_java.get_state()["skill_content"]

    has_markers_py = "<!-- SLOW_UPDATE_START -->" in skill_py
    has_markers_java = "<!-- SLOW_UPDATE_START -->" in skill_java

    assert has_markers_py, (
        f"Python operator should have slow update markers after 2 epochs. "
        f"Preview: {skill_py[:200]}..."
    )
    assert has_markers_java, (
        f"Java operator should have slow update markers after 2 epochs. "
        f"Preview: {skill_java[:200]}..."
    )


@_has_llm
def test_multi_operator_checkpoint_resume(runner, tmp_path):
    """Checkpoint save/resume works with multiple operators."""
    cases = _make_cases()
    llm = _create_llm()
    ckpt_dir = str(tmp_path / "checkpoints")

    # Phase 1: train 1 epoch
    op_py1 = SkillDocumentOperator("python_skill", initial_content=PYTHON_SKILL)
    op_java1 = SkillDocumentOperator("java_skill", initial_content=JAVA_SKILL)
    operators1 = {op_py1.operator_id: op_py1, op_java1.operator_id: op_java1}
    agent1 = MultiSkillDocumentAgent(llm, MODEL_NAME, operators1)

    opt1 = _make_optimizer(cases, llm, agent1, use_slow_update=False, use_meta_skill=False)
    opt1.bind(operators=operators1)

    updater1 = SingleDimUpdater(optimizer=opt1)
    trainer1 = Trainer(
        updater=updater1,
        evaluator=_make_evaluator(),
        checkpoint_dir=ckpt_dir,
        checkpoint_every_n_epochs=1,
        early_stop_score=0.95,
    )

    trainer1.train(agent=agent1, train_cases=cases, val_cases=cases, num_iterations=1)
    step_after_phase1 = opt1.get_state()["global_step"]
    assert step_after_phase1 >= 1

    # Find checkpoint
    ckpt_files = list(Path(ckpt_dir).glob("*.json"))
    assert len(ckpt_files) >= 1, "Checkpoint file should exist"

    # Phase 2: resume and train 2 more epochs
    op_py2 = SkillDocumentOperator("python_skill", initial_content=PYTHON_SKILL)
    op_java2 = SkillDocumentOperator("java_skill", initial_content=JAVA_SKILL)
    operators2 = {op_py2.operator_id: op_py2, op_java2.operator_id: op_java2}
    agent2 = MultiSkillDocumentAgent(llm, MODEL_NAME, operators2)

    opt2 = _make_optimizer(cases, llm, agent2, use_slow_update=False, use_meta_skill=False)
    opt2.bind(operators=operators2)

    updater2 = SingleDimUpdater(optimizer=opt2)
    trainer2 = Trainer(
        updater=updater2,
        evaluator=_make_evaluator(),
        checkpoint_dir=ckpt_dir,
        resume_from=str(ckpt_files[0]),
        checkpoint_every_n_epochs=1,
        early_stop_score=0.95,
    )

    trainer2.train(agent=agent2, train_cases=cases, val_cases=cases, num_iterations=2)

    final_step = opt2.get_state()["global_step"]
    assert final_step > step_after_phase1, "global_step should advance after resume"

    # Both operators should still have valid skills
    assert len(op_py2.get_state()["skill_content"]) > 0
    assert len(op_java2.get_state()["skill_content"]) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
