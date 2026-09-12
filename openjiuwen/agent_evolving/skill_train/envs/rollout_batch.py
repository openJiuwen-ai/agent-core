# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Parallel rollout runner with JSONL checkpoint resume."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.core.common.logging import logger

ProcessOneFn = Callable[[dict], dict]
MakeResultFn = Callable[[dict], dict]
MakeErrorFn = Callable[[dict, Exception], dict]
AfterBatchFn = Callable[[list[dict]], None]


def _try_parse_jsonl(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _read_checkpoint(path: str) -> tuple[set[str], list[dict]]:
    seen: set[str] = set()
    rows: list[dict] = []
    if not os.path.exists(path):
        return seen, rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            row = _try_parse_jsonl(line)
            if row is None:
                continue
            item_id = row.get("id")
            if item_id is None:
                continue
            seen.add(str(item_id))
            rows.append(row)
    return seen, rows


def _blank_failure(item: dict, *, phase: str, reason: str) -> dict:
    return {
        "id": str(item["id"]),
        "hard": 0,
        "soft": 0.0,
        "predicted_answer": "",
        "response": "",
        "fail_reason": reason,
        "agent_ok": False,
        "n_turns": 0,
        "phase": phase,
    }


def _timeout_failure(item: dict, task_timeout: int) -> dict:
    return _blank_failure(item, phase="timeout", reason=f"task-timeout-{task_timeout}s")


def _exception_failure(item: dict, exc: Exception, task_timeout: int) -> dict:
    row = _timeout_failure(item, task_timeout)
    row["phase"] = "error"
    row["fail_reason"] = f"unexpected: {type(exc).__name__}: {exc}"
    return row


@dataclass
class _Progress:
    completed: int
    correct: int
    total: int
    rows: list[dict] = field(default_factory=list)

    def record(self, outf: Any, res: dict, *, timed_out: bool = False) -> None:
        self.rows.append(res)
        self.completed += 1
        if res.get("hard", 0):
            self.correct += 1
        acc = self.correct / self.completed if self.completed else 0.0
        if timed_out:
            logger.info(
                "[rollout] %s/%s (acc=%.3f) id=%s TIMEOUT",
                self.completed,
                self.total,
                acc,
                res.get("id", "?"),
            )
        else:
            logger.info(
                "[rollout] %s/%s (acc=%.3f) id=%s hard=%s",
                self.completed,
                self.total,
                acc,
                res.get("id", "?"),
                res.get("hard", "?"),
            )
        outf.write(json.dumps(res, ensure_ascii=False) + "\n")
        outf.flush()


@dataclass
class _DrainContext:
    """Shared knobs for draining a pending rollout pool."""

    pending: list[dict]
    process_one: ProcessOneFn
    workers: int
    progress: _Progress
    outf: Any
    error_factory: Callable[[dict, Exception], dict]


def _drain_as_completed(ctx: _DrainContext) -> None:
    with ThreadPoolExecutor(max_workers=ctx.workers) as pool:
        futures = {pool.submit(ctx.process_one, item): item for item in ctx.pending}
        for fut in as_completed(futures):
            item = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                res = ctx.error_factory(item, exc)
            ctx.progress.record(ctx.outf, res)


def _drain_with_timeout(
    ctx: _DrainContext,
    *,
    timeout_secs: int,
    poll: float,
    timeout_factory: Callable[[dict], dict],
) -> None:
    started_at: dict[str, float] = {}

    def _tracked(item: dict) -> dict:
        started_at[str(item["id"])] = time.time()
        return ctx.process_one(item)

    pool = ThreadPoolExecutor(max_workers=ctx.workers)
    try:
        futures = {pool.submit(_tracked, item): item for item in ctx.pending}
        outstanding = set(futures)
        while outstanding:
            finished, _ = wait(outstanding, timeout=poll, return_when=FIRST_COMPLETED)
            now = time.time()
            timed_out: list[Any] = []
            for fut in outstanding - finished:
                item_id = str(futures[fut]["id"])
                started = started_at.get(item_id)
                if started is not None and now - started >= timeout_secs:
                    timed_out.append(fut)

            for fut in finished:
                outstanding.remove(fut)
                item = futures[fut]
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001
                    res = ctx.error_factory(item, exc)
                ctx.progress.record(ctx.outf, res)

            for fut in timed_out:
                outstanding.remove(fut)
                fut.cancel()
                ctx.progress.record(ctx.outf, timeout_factory(futures[fut]), timed_out=True)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def run_parallel_rollout(
    items: list[dict],
    out_root: str,
    *,
    process_one: ProcessOneFn,
    workers: int = 16,
    task_timeout: int | None = None,
    make_timeout_result: MakeResultFn | None = None,
    make_error_result: MakeErrorFn | None = None,
    after_batch: AfterBatchFn | None = None,
    poll_interval: float = 5.0,
) -> list[dict]:
    """Run ``process_one`` over ``items``, resuming from ``results.jsonl``.

    With ``task_timeout`` set, overdue futures are cancelled via a poll loop.
    With ``task_timeout is None``, futures complete through ``as_completed``.
    """
    results_path = os.path.join(out_root, "results.jsonl")
    os.makedirs(out_root, exist_ok=True)

    done_ids, existing = _read_checkpoint(results_path)
    pending = [item for item in items if str(item["id"]) not in done_ids]
    if not pending:
        if after_batch is not None:
            after_batch(existing)
        return existing

    progress = _Progress(
        completed=len(existing),
        correct=sum(1 for row in existing if row.get("hard", 0)),
        total=len(existing) + len(pending),
        rows=list(existing),
    )
    if existing:
        logger.info("[rollout] resuming: %s/%s already done", progress.completed, progress.total)

    timeout_secs = int(task_timeout) if task_timeout is not None else 0
    poll = max(float(poll_interval), 0.05)

    def _timeout_row(item: dict) -> dict:
        if make_timeout_result is not None:
            return make_timeout_result(item)
        return _timeout_failure(item, timeout_secs)

    def _error_row(item: dict, exc: Exception) -> dict:
        if make_error_result is not None:
            return make_error_result(item, exc)
        return _exception_failure(item, exc, timeout_secs)

    with open(results_path, "a", encoding="utf-8") as outf:
        ctx = _DrainContext(
            pending=pending,
            process_one=process_one,
            workers=workers,
            progress=progress,
            outf=outf,
            error_factory=_error_row,
        )
        if task_timeout is None:
            _drain_as_completed(ctx)
        else:
            _drain_with_timeout(
                ctx,
                timeout_secs=timeout_secs,
                poll=poll,
                timeout_factory=_timeout_row,
            )

    if after_batch is not None:
        after_batch(progress.rows)
    return progress.rows
