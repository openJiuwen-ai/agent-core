# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SearchQA rollout: prompt assembly, dialogue turns, and batch execution."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from openjiuwen.agent_evolving.skill_train.envs.io_helpers import (
    format_skill_section,
    write_prediction_artifacts,
)
from openjiuwen.agent_evolving.skill_train.envs.rollout_batch import run_parallel_rollout
from openjiuwen.agent_evolving.skill_train.envs.searchqa.evaluator import evaluate
from openjiuwen.agent_evolving.skill_train.llm_client import chat_target
from openjiuwen.agent_evolving.skill_train.prompts_loader import load_prompt

_CONTEXT_CHARS = 6000


@dataclass
class SearchQAItemConfig:
    """Runtime knobs for a single SearchQA rollout."""

    out_root: str
    skill_content: str
    exec_timeout: int = 120
    max_turns: int = 1
    max_completion_tokens: int = 16384
    diagnostic_mode: bool = False
    diagnostic_instruction: str = ""
    diagnostic_trace_context: str = ""


@dataclass
class SearchQABatchConfig:
    """Runtime knobs for a SearchQA batch rollout."""

    out_root: str
    skill_content: str
    exec_timeout: int = 120
    max_turns: int = 1
    workers: int = 64
    max_completion_tokens: int = 16384
    task_timeout: int = 600
    diagnostic_mode: bool = False
    diagnostic_instruction: str = ""
    diagnostic_trace_context_by_id: dict[str, str] = field(default_factory=dict)


@dataclass
class _PromptBundle:
    system: str
    user: str


@dataclass
class _DialogueState:
    response: str = ""
    turns: list[dict] = field(default_factory=list)


def _abort_if_all_pre_agent(rows: list[dict]) -> None:
    if not rows:
        return
    if any(row.get("agent_ok") is not False for row in rows):
        return
    counts = Counter(str(row.get("fail_reason") or "unknown error") for row in rows)
    top_reason, top_hits = counts.most_common(1)[0]
    raise RuntimeError(
        f"SearchQA rollout failed for all {len(rows)} items before an agent "
        f"response ({top_hits}x): {top_reason}"
    )


def _clip_context(context: str, budget: int = _CONTEXT_CHARS) -> str:
    """Keep whole ``[DOC]`` segments while staying under *budget* characters."""
    if len(context) <= budget:
        return context
    parts = context.split("[DOC]")
    kept = ""
    for idx, part in enumerate(parts):
        candidate = part if idx == 0 else f"{kept}[DOC]{part}"
        if len(candidate) > budget:
            break
        kept = candidate
    if kept:
        return kept
    return f"{context[:budget]}\n...[truncated]"


def _build_prompts(item: dict, cfg: SearchQAItemConfig) -> _PromptBundle:
    system = load_prompt("rollout_system", env="searchqa").format(
        skill_section=format_skill_section(cfg.skill_content)
    )
    blocks = [
        f"## Context\n{_clip_context(str(item.get('context') or ''))}",
        f"## Question\n{item['question']}",
    ]
    prior = (cfg.diagnostic_trace_context or "").strip()
    if prior:
        blocks.append(
            "## Previous Codex Trace Snapshot\n"
            "This is a partial transcript from an earlier attempt. "
            "Use it as your current reasoning context.\n\n"
            f"{prior}"
        )
    readout = (cfg.diagnostic_instruction or "").strip()
    if cfg.diagnostic_mode and readout:
        blocks.append(f"## Training Readout\n{readout}")
    return _PromptBundle(system=system, user="\n\n".join(blocks))


def _ask_model(system: str, user: str, cfg: SearchQAItemConfig) -> str:
    reply, _ = chat_target(
        system=system,
        user=user,
        max_completion_tokens=cfg.max_completion_tokens,
        retries=3,
        stage="rollout",
        timeout=cfg.exec_timeout,
    )
    return reply


def _retry_user(previous: str) -> str:
    return (
        f"Your previous answer was:\n{previous}\n\n"
        "Review it against the context and question. "
        "If correct, repeat it. If wrong, provide a corrected answer.\n"
        "Use <answer>...</answer> tags for your final answer."
    )


def _run_turns(prompts: _PromptBundle, cfg: SearchQAItemConfig) -> _DialogueState:
    state = _DialogueState()
    turn = 0
    while turn < cfg.max_turns:
        user_msg = prompts.user if turn == 0 else _retry_user(state.response)
        reply = _ask_model(prompts.system, user_msg, cfg)
        state.response = reply
        state.turns.append({"type": "message", "turn": turn + 1, "content": reply})
        if turn > 0 and "<answer>" in reply.lower():
            break
        turn += 1
    return state


def _blank_result(item: dict) -> dict:
    q = item.get("question", "")
    return {
        "id": str(item["id"]),
        "question": q,
        "task_description": q,
        "task_type": item.get("task_type") or "searchqa",
        "em": 0.0,
        "f1": 0.0,
        "sub_em": 0.0,
        "hard": 0,
        "soft": 0.0,
        "predicted_answer": "",
        "gold_answers": item.get("answers", []),
        "response": "",
        "fail_reason": "",
        "agent_ok": False,
        "n_turns": 0,
    }


def _stamp_metrics(row: dict, scores: dict, question: str, golds: list) -> str:
    row["em"] = scores["em"]
    row["f1"] = scores["f1"]
    row["sub_em"] = scores["sub_em"]
    row["predicted_answer"] = scores["predicted_answer"]
    row["hard"] = int(scores["em"])
    row["soft"] = scores["f1"]
    if scores["em"] < 1.0:
        row["fail_reason"] = (
            f"EM=0: predicted '{scores['predicted_answer']}' but expected {golds}"
        )
    return "\n".join(
        [
            "[EVALUATION RESULT]",
            f"Question: {question}",
            f"Predicted answer: {scores['predicted_answer']!r}",
            f"Gold answers: {golds!r}",
            f"Exact Match: {scores['em']}",
            f"F1: {scores['f1']:.4f}",
        ]
    )


def _item_cfg_for(batch: SearchQABatchConfig, item_id: str) -> SearchQAItemConfig:
    traces = batch.diagnostic_trace_context_by_id
    return SearchQAItemConfig(
        out_root=batch.out_root,
        skill_content=batch.skill_content,
        exec_timeout=batch.exec_timeout,
        max_turns=batch.max_turns,
        max_completion_tokens=batch.max_completion_tokens,
        diagnostic_mode=batch.diagnostic_mode,
        diagnostic_instruction=batch.diagnostic_instruction,
        diagnostic_trace_context=traces.get(item_id, ""),
    )


def process_one(item: dict, cfg: SearchQAItemConfig) -> dict:
    """Run the QA agent on one item and attach evaluation metrics."""
    row = _blank_result(item)
    question = item["question"]
    golds = item.get("answers", [])
    try:
        prompts = _build_prompts(item, cfg)
        dialogue = _run_turns(prompts, cfg)
        row["response"] = dialogue.response
        row["agent_ok"] = True
        row["n_turns"] = len(dialogue.turns)

        scores = evaluate(dialogue.response, golds)
        note = _stamp_metrics(row, scores, question, golds)
        conversation = list(dialogue.turns)
        conversation.append({"role": "system", "content": note})
        write_prediction_artifacts(
            cfg.out_root,
            str(item["id"]),
            system_prompt=prompts.system,
            user_prompt=prompts.user,
            conversation=conversation,
        )
    except Exception as exc:  # noqa: BLE001
        row["fail_reason"] = f"error: {exc}"
    return row


def run_batch(items: list[dict], cfg: SearchQABatchConfig) -> list[dict]:
    """Execute SearchQA rollouts in parallel with resume support."""
    soft_floor = int(cfg.exec_timeout) + 60
    deadline = max(int(cfg.task_timeout), soft_floor)

    def _timeout_payload(item: dict) -> dict:
        q = item.get("question", "")
        return {
            "id": str(item["id"]),
            "question": q,
            "task_description": q,
            "task_type": item.get("task_type") or "searchqa",
            "hard": 0,
            "soft": 0.0,
            "predicted_answer": "",
            "response": "",
            "fail_reason": f"task-timeout-{deadline}s",
            "agent_ok": False,
            "n_turns": 0,
            "gold_answer": item.get("answers", []),
            "phase": "timeout",
        }

    def _error_payload(item: dict, exc: Exception) -> dict:
        payload = _timeout_payload(item)
        payload["phase"] = "error"
        payload["fail_reason"] = f"unexpected: {type(exc).__name__}: {exc}"
        return payload

    def _invoke(item: dict) -> dict:
        return process_one(item, _item_cfg_for(cfg, str(item["id"])))

    return run_parallel_rollout(
        items,
        cfg.out_root,
        process_one=_invoke,
        workers=cfg.workers,
        task_timeout=deadline,
        make_timeout_result=_timeout_payload,
        make_error_result=_error_payload,
        after_batch=_abort_if_all_pre_agent,
    )
