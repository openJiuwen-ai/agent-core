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
        logger.info("[rollout] checkpoint missing path=%s", path)
        return seen, rows
    skipped = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            row = _try_parse_jsonl(line)
            if row is None or row.get("id") is None:
                if line.strip():
                    skipped += 1
                continue
            seen.add(str(row["id"]))
            rows.append(row)
    logger.info(
        "[rollout] checkpoint loaded=%s skipped=%s path=%s",
        len(rows),
        skipped,
        path,
    )
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


_LOG_TEXT_LIMIT = 300


def _clip_for_log(value: Any) -> Any:
    """Shorten long strings so a rollout row can be printed without the whole task."""
    if isinstance(value, str) and len(value) > _LOG_TEXT_LIMIT:
        return f"{value[:_LOG_TEXT_LIMIT]}...[{len(value)} chars]"
    if isinstance(value, dict):
        return {str(key): _clip_for_log(item) for key, item in value.items()}
    if isinstance(value, list):
        clipped = [_clip_for_log(item) for item in value[:8]]
        if len(value) > 8:
            clipped.append(f"...[{len(value)} items]")
        return clipped
    return value


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
        logger.info(
            "[rollout] result %s",
            json.dumps(_clip_for_log(res), ensure_ascii=False),
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


def _log_submit(item: dict) -> None:
    logger.info("[rollout] submit id=%s", item.get("id", "?"))


def _log_error(item: dict, exc: Exception) -> None:
    logger.warning(
        "[rollout] error id=%s %s: %s",
        item.get("id", "?"),
        type(exc).__name__,
        exc,
    )


def _drain_as_completed(ctx: _DrainContext) -> None:
    started_at: dict[str, float] = {}

    def _tracked(item: dict) -> dict:
        item_id = str(item.get("id", "?"))
        started_at[item_id] = time.perf_counter()
        logger.info("[rollout] running id=%s", item_id)
        return ctx.process_one(item)

    with ThreadPoolExecutor(max_workers=ctx.workers) as pool:
        futures = {}
        for item in ctx.pending:
            _log_submit(item)
            futures[pool.submit(_tracked, item)] = item
        for fut in as_completed(futures):
            item = futures.get(fut)
            if item is None:
                continue
            item_id = str(item.get("id", "?"))
            started = started_at.get(item_id)
            elapsed = time.perf_counter() - started if started is not None else None
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                _log_error(item, exc)
                res = ctx.error_factory(item, exc)
            if elapsed is not None:
                logger.info("[rollout] returned id=%s elapsed=%.1fs", item_id, elapsed)
            ctx.progress.record(ctx.outf, res)


def _drain_with_timeout(
    ctx: _DrainContext,
    *,
    timeout_secs: int,
    poll: float,
    timeout_factory: Callable[[dict], dict],
) -> None:
    """Drain futures with a wall-clock deadline per item.

    ``Future.cancel()`` only stops work that has not started. Already-running
    workers keep going; callers must bound inner I/O (e.g. LLM ``timeout``) so
    orphaned threads exit after the call-level deadline instead of hanging
    forever and exhausting the pool.
    """
    started_at: dict[str, float] = {}
    last_wait_log = time.perf_counter()

    def _tracked(item: dict) -> dict:
        item_id = str(item["id"])
        started_at[item_id] = time.perf_counter()
        logger.info("[rollout] running id=%s timeout=%ss", item_id, timeout_secs)
        return ctx.process_one(item)

    pool = ThreadPoolExecutor(max_workers=ctx.workers)
    try:
        futures = {}
        for item in ctx.pending:
            _log_submit(item)
            futures[pool.submit(_tracked, item)] = item
        outstanding = set(futures)
        while outstanding:
            finished, _ = wait(outstanding, timeout=poll, return_when=FIRST_COMPLETED)
            now = time.perf_counter()
            timed_out: list[Any] = []
            for fut in outstanding - finished:
                item_id = str(futures.get(fut, {}).get("id", "?"))
                started = started_at.get(item_id)
                if started is not None and now - started >= timeout_secs:
                    timed_out.append(fut)

            if not finished and not timed_out and now - last_wait_log >= 30:
                running: list[str] = []
                queued: list[str] = []
                for fut in outstanding:
                    item_id = str(futures.get(fut, {}).get("id", "?"))
                    started = started_at.get(item_id)
                    if started is None:
                        queued.append(item_id)
                    else:
                        running.append(f"{item_id}:{now - started:.0f}s")
                logger.info(
                    "[rollout] waiting running=%s queued=%s outstanding=%s",
                    ",".join(running) or "-",
                    ",".join(queued) or "-",
                    len(outstanding),
                )
                last_wait_log = now

            for fut in finished:
                outstanding.remove(fut)
                item = futures.get(fut)
                if item is None:
                    continue
                item_id = str(item.get("id", "?"))
                started = started_at.get(item_id)
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001
                    _log_error(item, exc)
                    res = ctx.error_factory(item, exc)
                if started is not None:
                    logger.info(
                        "[rollout] returned id=%s elapsed=%.1fs",
                        item_id,
                        time.perf_counter() - started,
                    )
                ctx.progress.record(ctx.outf, res)

            for fut in timed_out:
                outstanding.remove(fut)
                item = futures.get(fut)
                if item is None:
                    continue
                # Best-effort: cancels only if the worker has not started yet.
                if not fut.cancel():
                    logger.warning(
                        "[rollout] id=%s exceeded task_timeout=%ss while still "
                        "running; relying on call-level timeouts to release the worker",
                        item.get("id", "?"),
                        timeout_secs,
                    )
                ctx.progress.record(ctx.outf, timeout_factory(item), timed_out=True)
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
        logger.info("[rollout] nothing pending loaded=%s path=%s", len(existing), results_path)
        if after_batch is not None:
            after_batch(existing)
        return existing

    progress = _Progress(
        completed=len(existing),
        correct=sum(1 for row in existing if row.get("hard", 0)),
        total=len(existing) + len(pending),
        rows=list(existing),
    )
    timeout_secs = int(task_timeout) if task_timeout is not None else 0
    poll = max(float(poll_interval), 0.05)
    batch_started = time.perf_counter()
    logger.info(
        "[rollout] start pending=%s done=%s workers=%s timeout=%s poll=%ss out=%s",
        len(pending),
        len(existing),
        workers,
        timeout_secs or "none",
        poll,
        out_root,
    )
    if existing:
        logger.info("[rollout] resuming: %s/%s already done", progress.completed, progress.total)

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

    logger.info(
        "[rollout] batch done completed=%s/%s correct=%s elapsed=%.1fs",
        progress.completed,
        progress.total,
        progress.correct,
        time.perf_counter() - batch_started,
    )
    if after_batch is not None:
        after_batch(progress.rows)
    return progress.rows
