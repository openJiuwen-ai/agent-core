# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DocVQA multimodal rollout: image + skill → ANLS-scored answer.

Public surface
--------------
- :func:`process_one` — one item (chat turns, evaluate, write artifacts)
- :func:`run_batch` — parallel batch via :func:`run_parallel_rollout`
- :class:`DocVQABatchConfig` / :class:`DocVQAItemConfig`
"""

from __future__ import annotations

import base64
import mimetypes
import os
from dataclasses import dataclass

from openjiuwen.agent_evolving.skill_train.envs.docvqa.evaluator import evaluate
from openjiuwen.agent_evolving.skill_train.envs.io_helpers import (
    format_skill_section,
    write_prediction_artifacts,
)
from openjiuwen.agent_evolving.skill_train.envs.rollout_batch import run_parallel_rollout
from openjiuwen.agent_evolving.skill_train.llm_client import chat_target_messages
from openjiuwen.agent_evolving.skill_train.prompts_loader import load_prompt

_ANLS_PASS = 0.999
_ANSWER_HINT = "Return the final answer inside <answer>...</answer>."
_REFINE_HINT = (
    "Review the same image carefully and answer again. "
    "Keep the final answer inside <answer>...</answer>."
)


@dataclass
class DocVQAItemConfig:
    """Per-item DocVQA rollout settings."""

    out_root: str
    skill_content: str
    exec_timeout: int = 120
    max_turns: int = 1
    max_completion_tokens: int = 16384
    image_detail: str = "auto"
    diagnostic_mode: bool = False
    diagnostic_instruction: str = ""


@dataclass
class DocVQABatchConfig:
    """Batch DocVQA rollout settings."""

    out_root: str
    skill_content: str
    exec_timeout: int = 120
    max_turns: int = 1
    workers: int = 16
    max_completion_tokens: int = 16384
    image_detail: str = "auto"
    task_timeout: int = 600
    diagnostic_mode: bool = False
    diagnostic_instruction: str = ""


def _system_prompt(skill_content: str) -> str:
    tmpl = load_prompt("rollout_system", env="docvqa")
    return tmpl.format(skill_section=format_skill_section(skill_content))


def _data_url(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _compose_user_text(
    question: str,
    *,
    diagnostic_mode: bool,
    diagnostic_instruction: str,
) -> str:
    chunks = [question, _ANSWER_HINT]
    note = diagnostic_instruction.strip()
    if diagnostic_mode and note:
        chunks.append(f"## Training Readout\n{note}")
    return "\n\n".join(chunks)


def _vision_part(path: str, detail: str) -> dict:
    image_url: dict = {"url": _data_url(path)}
    if detail and detail != "auto":
        image_url["detail"] = detail
    return {"type": "image_url", "image_url": image_url}


def _chat_messages(system: str, user_text: str, image_path: str, detail: str) -> list[dict]:
    user_content = [
        {"type": "text", "text": user_text},
        _vision_part(image_path, detail),
    ]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


def _blank_row(item: dict) -> dict:
    question = item.get("question", "")
    subtype = item.get("subtask") or item.get("task_type") or "docvqa"
    return {
        "id": str(item["id"]),
        "question": question,
        "task_type": subtype,
        "task_description": question,
        "hard": 0,
        "soft": 0.0,
        "predicted_answer": "",
        "response": "",
        "fail_reason": "",
        "agent_ok": False,
        "n_turns": 0,
        "image_paths": item.get("image_paths", []),
        "gold_answer": item.get("answers", []),
    }


def _eval_note(question: str, scored: dict, golds: list) -> str:
    return "\n".join(
        [
            "[EVALUATION RESULT]",
            f"Question: {question}",
            f"Predicted answer: {scored['predicted_answer']!r}",
            f"Gold answers: {golds!r}",
            f"ANLS: {scored['anls']:.4f}",
        ]
    )


def _has_answer_tag(text: str) -> bool:
    return "<answer>" in text.lower()


def _turn_payload(messages: list[dict], prior_reply: str, turn_idx: int) -> list[dict]:
    if turn_idx == 0:
        return messages
    return [
        messages[0],
        messages[1],
        {"role": "assistant", "content": prior_reply},
        {"role": "user", "content": _REFINE_HINT},
    ]


def _run_dialogue(
    messages: list[dict],
    *,
    max_turns: int,
    max_completion_tokens: int,
    exec_timeout: int,
) -> tuple[str, list[dict]]:
    """Iterate chat turns until ``max_turns`` or an ``<answer>`` tag appears."""
    transcript: list[dict] = []
    last = ""
    for turn_idx in range(max_turns):
        outgoing = _turn_payload(messages, last, turn_idx)
        reply, _ = chat_target_messages(
            messages=outgoing,
            max_completion_tokens=max_completion_tokens,
            retries=3,
            stage="rollout",
            timeout=exec_timeout,
        )
        last = reply
        transcript.append({"type": "message", "turn": turn_idx + 1, "content": reply})
        if _has_answer_tag(reply):
            break
    return last, transcript


def _item_from_batch(cfg: DocVQABatchConfig) -> DocVQAItemConfig:
    return DocVQAItemConfig(
        out_root=cfg.out_root,
        skill_content=cfg.skill_content,
        exec_timeout=cfg.exec_timeout,
        max_turns=cfg.max_turns,
        max_completion_tokens=cfg.max_completion_tokens,
        image_detail=cfg.image_detail,
        diagnostic_mode=cfg.diagnostic_mode,
        diagnostic_instruction=cfg.diagnostic_instruction,
    )


def _apply_scores(row: dict, reply: str, golds: list) -> dict:
    scored = evaluate(reply, golds)
    prediction = scored["predicted_answer"]
    anls = float(scored["anls"])
    row["predicted_answer"] = prediction
    row["hard"] = int(anls >= _ANLS_PASS)
    row["soft"] = anls
    if anls <= 0.0:
        row["fail_reason"] = f"predicted '{prediction}' but expected one of {golds}"
    return scored


def process_one(item: dict, cfg: DocVQAItemConfig) -> dict:
    """Run vision chat for one DocVQA item and score with ANLS."""
    row = _blank_row(item)
    question = item["question"]
    row["question"] = question
    row["task_description"] = question
    golds = item.get("answers", [])

    try:
        system = _system_prompt(cfg.skill_content)
        user_text = _compose_user_text(
            question,
            diagnostic_mode=cfg.diagnostic_mode,
            diagnostic_instruction=cfg.diagnostic_instruction,
        )
        messages = _chat_messages(system, user_text, item["image_path"], cfg.image_detail)
        image_label = f"[image] {os.path.basename(item['image_path'])}"
        seed = [{"role": "user", "content": f"{user_text}\n\n{image_label}"}]

        reply, turns = _run_dialogue(
            messages,
            max_turns=cfg.max_turns,
            max_completion_tokens=cfg.max_completion_tokens,
            exec_timeout=cfg.exec_timeout,
        )
        conversation = seed + turns
        row["response"] = reply
        row["agent_ok"] = True
        row["n_turns"] = len(turns)

        scored = _apply_scores(row, reply, golds)
        conversation.append(
            {"role": "system", "content": _eval_note(question, scored, golds)}
        )
        write_prediction_artifacts(
            cfg.out_root,
            str(item["id"]),
            system_prompt=system,
            user_prompt=user_text,
            conversation=conversation,
        )
    except Exception as exc:  # noqa: BLE001
        row["fail_reason"] = f"error: {exc}"
    return row


def _batch_deadline(cfg: DocVQABatchConfig) -> int:
    floor = int(cfg.exec_timeout) + 60
    return max(int(cfg.task_timeout), floor)


def _timeout_result_factory(deadline: int):
    def _timeout_row(item: dict) -> dict:
        row = _blank_row(item)
        row["fail_reason"] = f"task-timeout-{deadline}s"
        row["phase"] = "timeout"
        return row

    return _timeout_row


def _error_result_factory(deadline: int):
    timeout_row = _timeout_result_factory(deadline)

    def _error_row(item: dict, exc: Exception) -> dict:
        row = timeout_row(item)
        row["phase"] = "error"
        row["fail_reason"] = f"unexpected: {type(exc).__name__}: {exc}"
        return row

    return _error_row


def run_batch(items: list[dict], cfg: DocVQABatchConfig) -> list[dict]:
    """Parallel DocVQA rollout with resume via ``results.jsonl``."""
    deadline = _batch_deadline(cfg)
    item_cfg = _item_from_batch(cfg)

    def _invoke(item: dict) -> dict:
        return process_one(item, item_cfg)

    return run_parallel_rollout(
        items,
        cfg.out_root,
        process_one=_invoke,
        workers=cfg.workers,
        task_timeout=deadline,
        make_timeout_result=_timeout_result_factory(deadline),
        make_error_result=_error_result_factory(deadline),
    )
