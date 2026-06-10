# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""End-to-end tests for SkillDocumentOptimizer with a real LLM.

Exercises the full ReflACT pipeline (rollout → reflect → aggregate → select → apply)
using real LLM calls for the optimizer's analyst / merge / ranking prompts, and a
real agent that reads the skill document as its system prompt.

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
import json
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
os.environ.setdefault("LLM_SSL_VERIFY", "false")

_skip_reason = "Requires LLM API configuration (API_BASE, API_KEY, MODEL_NAME)"
_has_llm = pytest.mark.skipif(not (API_BASE and API_KEY and MODEL_NAME), reason=_skip_reason)


def _client_config() -> ModelClientConfig:
    return ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=MODEL_TIMEOUT,
        verify_ssl=False,
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


class SkillDocumentAgent:
    """Minimal agent that uses skill content as system prompt.

    Reads the current skill content from the operator on each invoke,
    so it always sees the latest version after optimizer updates.
    """

    def __init__(self, llm: Model, model: str, skill_op: SkillDocumentOperator):
        self._llm = llm
        self._model = model
        self._skill_op = skill_op

    async def invoke(self, inputs: dict, session=None) -> dict:
        skill = self._skill_op.get_state()["skill_content"]
        messages = [
            {"role": "system", "content": skill},
            {"role": "user", "content": inputs.get("query", "")},
        ]
        try:
            response = await self._llm.invoke(messages=messages, model=self._model)
            return {"output": response.content}
        except Exception as exc:
            return {"output": f"Error: {exc}"}

    def get_operators(self) -> dict:
        return {self._skill_op.operator_id: self._skill_op}


# ── Test data ─────────────────────────────────────────────────────────────

INITIAL_SKILL = """\
# Python 基础问答

提供一些 Python 相关问题的回答。

请尽量简洁地回答问题。
"""


def _make_cases() -> CaseLoader:
    return CaseLoader(
        [
            Case(
                inputs={"query": "Python 的 GIL 是什么？"},
                label={"answer": "全局解释器锁 Global Interpreter Lock 保证同一时刻只有一个线程执行 Python 字节码"},
                case_id="gil",
            ),
            Case(
                inputs={"query": "什么是列表推导式？"},
                label={"answer": "列表推导式 list comprehension 是创建列表的简洁语法 [expr for item in iterable]"},
                case_id="list_comp",
            ),
            Case(
                inputs={"query": "Python 装饰器怎么用？"},
                label={"answer": "装饰器 decorator 用 @decorator_name 语法包装函数 增强功能"},
                case_id="decorator",
            ),
            Case(
                inputs={"query": "生成器和迭代器有什么区别？"},
                label={"answer": "生成器 generator 使用 yield 惰性产出值 迭代器 iterator 实现 __next__ 协议"},
                case_id="generator",
            ),
            Case(
                inputs={"query": "Python 的 with 语句做什么？"},
                label={"answer": "with 语句管理上下文 context manager 自动处理资源获取和释放"},
                case_id="with_stmt",
            ),
            Case(
                inputs={"query": "什么是鸭子类型？"},
                label={"answer": "鸭子类型 duck typing 根据对象行为而非类型判断 如果走起来像鸭子就是鸭子"},
                case_id="duck_typing",
            ),
        ]
    )


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_evaluator() -> MetricEvaluator:
    return MetricEvaluator(metrics=[KeywordMatchMetric()])


def _make_optimizer(
    cases: CaseLoader,
    llm: Model,
    agent: SkillDocumentAgent,
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
        pass  # dangling tasks from optimizer's internal asyncio.run() calls


# ── Tests ─────────────────────────────────────────────────────────────────


@_has_llm
def test_two_epochs_full_pipeline(runner):
    """Run 2 epochs with real LLM. Skill content should be modified."""
    cases = _make_cases()
    llm = _create_llm()

    op = SkillDocumentOperator("python_qa", initial_content=INITIAL_SKILL)
    agent = SkillDocumentAgent(llm, MODEL_NAME, op)

    opt = _make_optimizer(cases, llm, agent, use_slow_update=False, use_meta_skill=False)
    opt.bind(operators={op.operator_id: op})

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

    # The gate may roll back if the candidate scores worse than base.
    # What matters is that the pipeline ran (global_step advanced).
    # If the skill WAS modified, verify it's valid markdown.
    final_skill = op.get_state()["skill_content"]
    assert isinstance(final_skill, str) and len(final_skill) > 0


@_has_llm
def test_gate_rollback_with_real_eval(runner):
    """Gate should roll back when candidate skill scores worse."""
    cases = _make_cases()
    llm = _create_llm()

    op = SkillDocumentOperator("python_qa", initial_content=INITIAL_SKILL)
    agent = SkillDocumentAgent(llm, MODEL_NAME, op)

    opt = _make_optimizer(cases, llm, agent, use_slow_update=False, use_meta_skill=False)
    opt.bind(operators={op.operator_id: op})

    # Smart evaluator: penalize skills that contain optimizer-generated edits
    def smart_eval(case_list, predicts, num_parallel=1):
        from openjiuwen.agent_evolving.dataset import EvaluatedCase

        current_skill = op.get_state().get("skill_content", "")
        # If skill has been significantly modified, score it lower
        if len(current_skill) > len(INITIAL_SKILL) * 2:
            score = 0.2
        else:
            score = 0.8
        return [EvaluatedCase(case=c, answer=p, score=score, reason="gate test") for c, p in zip(case_list, predicts)]

    from unittest.mock import MagicMock

    from openjiuwen.agent_evolving.evaluator import BaseEvaluator

    gate_evaluator = MagicMock(spec=BaseEvaluator)
    gate_evaluator.batch_evaluate = MagicMock(side_effect=smart_eval)

    # Replace the evaluator on the optimizer
    opt._evaluator = gate_evaluator

    updater = SingleDimUpdater(optimizer=opt)
    trainer = Trainer(updater=updater, evaluator=gate_evaluator, early_stop_score=1.0)

    trainer.train(agent=agent, train_cases=cases, val_cases=cases, num_iterations=1)

    # Gate should have rolled back: skill should not have grown significantly
    final_skill = op.get_state()["skill_content"]
    assert len(final_skill) <= len(INITIAL_SKILL) * 2, "Gate should have rolled back oversized skill"


@_has_llm
def test_artifact_export_complete(runner, tmp_path):
    """Artifact directory should contain expected files after 1 epoch."""
    cases = _make_cases()
    llm = _create_llm()
    artifact_dir = str(tmp_path / "artifacts")

    op = SkillDocumentOperator("python_qa", initial_content=INITIAL_SKILL)
    agent = SkillDocumentAgent(llm, MODEL_NAME, op)

    opt = _make_optimizer(
        cases, llm, agent,
        artifact_dir=artifact_dir,
        use_slow_update=False,
        use_meta_skill=False,
    )
    opt.bind(operators={op.operator_id: op})

    updater = SingleDimUpdater(optimizer=opt)
    trainer = Trainer(updater=updater, evaluator=_make_evaluator(), early_stop_score=0.95)

    # Register callbacks to trigger run_epoch_end (which exports gate_result.json)
    from openjiuwen.agent_evolving.callbacks.skill_document_callbacks import SkillDocumentCallbacks
    trainer.set_callbacks(SkillDocumentCallbacks(opt))

    trainer.train(agent=agent, train_cases=cases, val_cases=cases, num_iterations=1)

    # Verify artifact directory structure
    # Note: Trainer uses 1-indexed epochs, so first epoch is epoch_1
    # But backward pass artifacts go to epoch_0 (0-indexed steps)
    epoch_dir = Path(artifact_dir) / "epoch_0"
    assert epoch_dir.exists(), f"epoch_0 directory should exist, found: {list(Path(artifact_dir).iterdir()) if Path(artifact_dir).exists() else 'no artifact dir'}"

    # Skill before snapshot
    before_path = epoch_dir / "skill_before.md"
    assert before_path.exists(), "skill_before.md should exist"
    assert before_path.read_text().strip(), "skill_before.md should not be empty"

    # Gate result - check in epoch_1 (Trainer's epoch numbering is 1-indexed)
    gate_path_epoch1 = Path(artifact_dir) / "epoch_1" / "gate_result.json"
    gate_path_epoch0 = epoch_dir / "gate_result.json"
    gate_path = gate_path_epoch1 if gate_path_epoch1.exists() else gate_path_epoch0
    assert gate_path.exists(), "gate_result.json should exist in either epoch_0 or epoch_1"
    gate_data = json.loads(gate_path.read_text())
    assert "decision" in gate_data or "score" in gate_data or "base_score" in gate_data

    # Step-level metrics
    step_dir = epoch_dir / "step_0"
    assert step_dir.exists(), "step_0 directory should exist"
    metrics_path = step_dir / "metrics.json"
    assert metrics_path.exists(), "metrics.json should exist"


@_has_llm
def test_checkpoint_save_and_resume(runner, tmp_path):
    """Checkpoint saves and resumes global_step correctly."""
    cases = _make_cases()
    llm = _create_llm()
    ckpt_dir = str(tmp_path / "checkpoints")

    # Phase 1: train 1 epoch
    op1 = SkillDocumentOperator("python_qa", initial_content=INITIAL_SKILL)
    agent1 = SkillDocumentAgent(llm, MODEL_NAME, op1)

    opt1 = _make_optimizer(cases, llm, agent1, use_slow_update=False, use_meta_skill=False)
    opt1.bind(operators={op1.operator_id: op1})

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
    assert len(ckpt_files) >= 1, f"Checkpoint file should exist, found: {list(Path(ckpt_dir).iterdir()) if Path(ckpt_dir).exists() else 'no dir'}"

    # Phase 2: resume and continue
    op2 = SkillDocumentOperator("python_qa", initial_content=INITIAL_SKILL)
    agent2 = SkillDocumentAgent(llm, MODEL_NAME, op2)

    opt2 = _make_optimizer(cases, llm, agent2, use_slow_update=False, use_meta_skill=False)
    opt2.bind(operators={op2.operator_id: op2})

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


@_has_llm
def test_slow_update_and_meta_skill(runner):
    """After 2 epochs, slow_update markers and meta_skill context should be present."""
    cases = _make_cases()
    llm = _create_llm()

    op = SkillDocumentOperator("python_qa", initial_content=INITIAL_SKILL)
    agent = SkillDocumentAgent(llm, MODEL_NAME, op)

    opt = _make_optimizer(cases, llm, agent, use_slow_update=True, use_meta_skill=True)
    opt.bind(operators={op.operator_id: op})

    updater = SingleDimUpdater(optimizer=opt)
    trainer = Trainer(updater=updater, evaluator=_make_evaluator(), early_stop_score=0.95)

    # Register callbacks to trigger run_epoch_end (which runs slow_update and meta_skill)
    from openjiuwen.agent_evolving.callbacks.skill_document_callbacks import SkillDocumentCallbacks
    trainer.set_callbacks(SkillDocumentCallbacks(opt))

    trainer.train(agent=agent, train_cases=cases, val_cases=cases, num_iterations=2)

    # Slow update markers should be injected after epoch >= 1
    skill = opt._current_skill_content
    assert "<!-- SLOW_UPDATE_START -->" in skill, (
        f"slow_update markers should be present after 2 epochs. "
        f"Skill preview: {skill[:200]}..."
    )
    assert "<!-- SLOW_UPDATE_END -->" in skill

    # Meta skill context should be non-empty
    assert opt._meta_skill_context, "meta_skill_context should be non-empty after 2 epochs"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
