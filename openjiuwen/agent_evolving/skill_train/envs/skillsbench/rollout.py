# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SkillsBench rollout: task workspace, then the task verifier for the score."""

from __future__ import annotations

import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from openjiuwen.core.common.logging import logger

from openjiuwen.agent_evolving.skill_train.envs.io_helpers import write_prediction_artifacts
from openjiuwen.agent_evolving.skill_train.envs.rollout_batch import run_parallel_rollout
from openjiuwen.agent_evolving.skill_train.envs.skillsbench.evaluator import (
    copy_environment_inputs,
    run_task_verifier,
)
from openjiuwen.agent_evolving.skill_train.jiuwenswarm_exec import (
    TARGET_SKILL_NAME,
    GatewayUnreachableError,
    default_exec_prompt,
    default_exec_prompt_multi,
    exec_work_dir_for,
    prepare_workspace,
    render_skill_bundle_mds,
    render_skill_md,
    run_jiuwenswarm_cli_exec,
    truncate_middle,
    verifier_output_chars,
)
from openjiuwen.agent_evolving.skill_train.model_compat import is_target_exec_backend
from openjiuwen.agent_evolving.skill_train.skill_bundle import format_skill_bundle

_NAME_RE = re.compile(r"(?m)^name:\s*[\"']?([A-Za-z0-9_-]+)")


@dataclass
class SkillsBenchItemConfig:
    """Runtime knobs for one SkillsBench task."""

    out_root: str
    skill_content: str
    exec_timeout: int = 900
    verifier_mode: str | None = None
    skills: dict[str, str] = field(default_factory=dict)
    verifier_log_root: str = ""


@dataclass
class SkillsBenchBatchConfig:
    """Runtime knobs for a SkillsBench batch."""

    out_root: str
    skill_content: str
    exec_timeout: int = 900
    workers: int = 1
    task_timeout: int = 1800
    verifier_mode: str | None = None
    skills: dict[str, str] = field(default_factory=dict)
    verifier_log_root: str = ""


def skill_name_from_markdown(text: str) -> str:
    """Read ``name:`` from SKILL.md frontmatter."""
    match = _NAME_RE.search(text or "")
    return match.group(1) if match else ""


def _blank_result(item: dict) -> dict:
    question = str(item.get("question") or "")
    return {
        "id": str(item["id"]),
        "question": question,
        "task_description": question,
        "task_type": item.get("task_type") or "skillsbench",
        "hard": 0,
        "soft": 0.0,
        "reward": "",
        "response": "",
        "fail_reason": "",
        "agent_ok": False,
        "n_turns": 0,
    }


def _install_task_skills(
    work_dir: Path,
    item: dict,
    *,
    evolving: dict[str, str] | None = None,
    skill_name: str = "",
    skill_md: str = "",
) -> None:
    """Copy ``environment/skills`` and overlay evolved SKILL.md bodies."""
    skills_root = work_dir / ".agents" / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    evolving = dict(evolving or {})
    for record in item.get("skills") or []:
        source = Path(str(record.get("path") or "")).parent
        name = str(record.get("name") or source.name)
        if not source.is_dir() or not name:
            continue
        dest = skills_root / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
        if name in evolving:
            rendered = render_skill_md(evolving[name], name=name)
            (dest / "SKILL.md").write_text(rendered, encoding="utf-8")
        elif skill_name and name == skill_name and skill_md:
            (dest / "SKILL.md").write_text(skill_md, encoding="utf-8")
    for name, body in evolving.items():
        dest = skills_root / name
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text(render_skill_md(body, name=name), encoding="utf-8")


def _task_prompt() -> str:
    return (
        "complete the task described in task.md. "
        "Read every SKILL.md under `.agents/skills/` (not only the reflact-target file). "
        "Treat `/root` as this workdir (the directory that contains `task.md`): "
        "`/root/foo` -> `./foo`, `/root/workspace/bar.py` -> `./workspace/bar.py`. "
        "Create or overwrite the required output files at those exact relative paths; "
        "do not only describe the solution in <answer>. "
        "If the task fills a Word template, replace placeholders in body paragraphs, "
        "headers/footers, and all tables including nested tables."
    )


def _prepare(item: dict, cfg: SkillsBenchItemConfig) -> tuple[Path, str]:
    work_dir = Path(exec_work_dir_for(cfg.out_root, str(item["id"])))
    task_text = str(item.get("question") or "")
    evolving = dict(cfg.skills or {})
    if len(evolving) > 1:
        skill_mds = render_skill_bundle_mds(evolving)
        prepare_workspace(
            work_dir=str(work_dir),
            skill_mds=skill_mds,
            task_text=task_text,
        )
        _install_task_skills(work_dir, item, evolving=evolving)
        skill_md = format_skill_bundle(evolving)
    else:
        body = next(iter(evolving.values())) if evolving else cfg.skill_content
        name = next(iter(evolving)) if evolving else ""
        skill_name = name or skill_name_from_markdown(body) or TARGET_SKILL_NAME
        skill_md = render_skill_md(body, name=skill_name)
        prepare_workspace(
            work_dir=str(work_dir),
            skill_md=skill_md,
            task_text=task_text,
            skill_name=skill_name,
        )
        _install_task_skills(
            work_dir,
            item,
            evolving=evolving or {skill_name: body},
            skill_name=skill_name,
            skill_md=skill_md,
        )
    copy_environment_inputs(str(item.get("task_dir") or ""), work_dir)
    return work_dir, skill_md


def _stamp(row: dict, scores: dict) -> None:
    row["hard"] = int(scores.get("hard") or 0)
    row["soft"] = float(scores.get("soft") or 0.0)
    row["reward"] = str(scores.get("reward") or "")
    for key in ("tests_passed", "tests_total"):
        if key in scores:
            row[key] = int(scores[key])
    reason = str(scores.get("fail_reason") or "")
    if reason:
        row["fail_reason"] = reason


def _soft_note(row: dict) -> str:
    """Render soft with the underlying pytest tally when the verifier reported it."""
    passed = row.get("tests_passed")
    total = row.get("tests_total")
    if passed is None or total is None:
        return f"soft: {row['soft']}"
    return f"soft: {row['soft']} ({int(passed)}/{int(total)})"


def verifier_logs_parent(cfg: SkillsBenchItemConfig, item_id: str) -> Path:
    """Directory holding one item's verifier artifacts.

    Defaults to ``<out_root>/verifier_logs/<id>`` — deliberately *not* under
    ``predictions/``: that tree is where the agent workdir lives, so the next
    iteration's agent could read the pytest ``parametrize`` expected values from
    ``../<id>/verifier_logs/verifier/ctrf.json`` and hardcode them.
    """
    root = Path(cfg.verifier_log_root) if cfg.verifier_log_root else Path(cfg.out_root) / "verifier_logs"
    return root / str(item_id)


def _score(item: dict, work_dir: Path, cfg: SkillsBenchItemConfig) -> dict:
    logs_dir = verifier_logs_parent(cfg, str(item["id"]))
    return run_task_verifier(
        task_dir=str(item.get("task_dir") or ""),
        agent_workdir=work_dir,
        logs_dir=logs_dir,
        timeout_sec=int(item.get("verifier_timeout_sec") or 900),
        verifier_mode=cfg.verifier_mode,
    )


def process_one(item: dict, cfg: SkillsBenchItemConfig) -> dict:
    """Run one task, then score it with the task verifier."""
    row = _blank_result(item)
    task_id = str(item["id"])
    try:
        work_dir, skill_md = _prepare(item, cfg)
        names = list(cfg.skills.keys()) if cfg.skills else []
        logger.info(
            "[skillsbench] workspace id=%s skills=%s dir=%s",
            task_id,
            ",".join(names) or "-",
            work_dir,
        )
        response = ""
        if is_target_exec_backend():
            prompt = (
                default_exec_prompt_multi(_task_prompt(), names)
                if len(names) > 1
                else default_exec_prompt(_task_prompt())
            )
            agent_timeout = max(int(item.get("timeout_sec") or 0), cfg.exec_timeout)
            logger.info(
                "[skillsbench] agent start id=%s timeout=%ss prompt_chars=%s skills=%s",
                task_id,
                agent_timeout,
                len(prompt or ""),
                ",".join(names) or "-",
            )
            agent_started = time.perf_counter()
            response, _raw = run_jiuwenswarm_cli_exec(
                work_dir=str(work_dir),
                prompt=prompt,
                timeout=agent_timeout,
                allow_file_edits=True,
            )
            row["n_turns"] = 1
            response_text = (response or "").strip()
            logger.info(
                "[skillsbench] agent done id=%s elapsed=%.1fs response_chars=%s raw_chars=%s preview=%s",
                task_id,
                time.perf_counter() - agent_started,
                len(response or ""),
                len(_raw or ""),
                response_text.replace("\n", " ")[:160] or "-",
            )
            if not response_text:
                logger.warning(
                    "[skillsbench] agent empty response id=%s raw_tail=%s",
                    task_id,
                    (_raw or "").strip()[-600:].replace("\n", " | ") or "-",
                )
        row["response"] = response or ""
        row["agent_ok"] = True
        verifier_timeout = int(item.get("verifier_timeout_sec") or 900)
        logger.info(
            "[skillsbench] verifier start id=%s mode=%s timeout=%ss",
            task_id,
            cfg.verifier_mode or "default",
            verifier_timeout,
        )
        verify_started = time.perf_counter()
        scores = _score(item, work_dir, cfg)
        _stamp(row, scores)
        logger.info(
            "[skillsbench] verifier done id=%s hard=%s soft=%s fail=%s elapsed=%.1fs",
            task_id,
            row["hard"],
            row["soft"],
            row["fail_reason"] or "-",
            time.perf_counter() - verify_started,
        )
        note = (
            "[EVALUATION RESULT]\n"
            f"reward: {row['reward']}\n"
            f"hard: {row['hard']}\n"
            f"{_soft_note(row)}\n"
            f"fail_reason: {row['fail_reason']}"
        )
        detail = str(scores.get("verifier_output") or "").strip()
        if detail:
            note += "\nverifier_output:\n" + truncate_middle(detail, verifier_output_chars())
        write_prediction_artifacts(
            cfg.out_root,
            str(item["id"]),
            system_prompt=skill_md,
            user_prompt=str(item.get("question") or ""),
            conversation=[{"role": "system", "content": note}],
        )
    except (AttributeError, KeyError, NameError, TypeError, AssertionError, GatewayUnreachableError):
        raise
    except Exception as exc:  # noqa: BLE001 — expected rollout/runtime failures
        row["fail_reason"] = f"error: {exc}"
        logger.warning("[skillsbench] rollout error id=%s %s", task_id, exc)
        if "skill_md" in locals():
            note = (
                "[EVALUATION RESULT]\n"
                f"reward: {row['reward']}\n"
                f"hard: {row['hard']}\n"
                f"{_soft_note(row)}\n"
                f"fail_reason: {row['fail_reason']}"
            )
            response = str(row.get("response") or "").strip()
            if response:
                note += "\nresponse:\n" + truncate_middle(response, verifier_output_chars())
            write_prediction_artifacts(
                cfg.out_root,
                str(item["id"]),
                system_prompt=skill_md,
                user_prompt=str(item.get("question") or ""),
                conversation=[{"role": "system", "content": note}],
            )
    return row


def _abort_if_all_pre_agent(rows: list[dict]) -> None:
    if not rows or any(row.get("agent_ok") is not False for row in rows):
        return
    counts = Counter(str(row.get("fail_reason") or "unknown error") for row in rows)
    top_reason, top_hits = counts.most_common(1)[0]
    raise RuntimeError(
        f"SkillsBench rollout failed for all {len(rows)} items before an agent "
        f"response ({top_hits}x): {top_reason}"
    )


def run_batch(items: list[dict], cfg: SkillsBenchBatchConfig) -> list[dict]:
    """Execute SkillsBench tasks with resume support."""
    deadline = max(int(cfg.task_timeout), int(cfg.exec_timeout) + 60)
    item_cfg = SkillsBenchItemConfig(
        out_root=cfg.out_root,
        skill_content=cfg.skill_content,
        exec_timeout=cfg.exec_timeout,
        verifier_mode=cfg.verifier_mode,
        skills=dict(cfg.skills or {}),
        verifier_log_root=cfg.verifier_log_root,
    )

    def _timeout_payload(item: dict) -> dict:
        row = _blank_result(item)
        row["fail_reason"] = f"task-timeout-{deadline}s"
        row["phase"] = "timeout"
        return row

    def _error_payload(item: dict, exc: Exception) -> dict:
        row = _timeout_payload(item)
        row["phase"] = "error"
        row["fail_reason"] = f"unexpected: {type(exc).__name__}: {exc}"
        return row

    return run_parallel_rollout(
        items,
        cfg.out_root,
        process_one=lambda item: process_one(item, item_cfg),
        workers=cfg.workers,
        task_timeout=deadline,
        make_timeout_result=_timeout_payload,
        make_error_result=_error_payload,
        after_batch=_abort_if_all_pre_agent,
    )
