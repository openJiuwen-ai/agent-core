#!/usr/bin/env python3

"""Validate SFT E2E Redis state and generated training dataset."""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import redis

RAW_PREFIX = "rl:sft_raw:"
SAMPLE_PREFIX = "rl:sft_sample:"


def _json_from_hash(row: dict[Any, Any]) -> dict[str, Any]:
    payload = row.get("sample_json") or row.get(b"sample_json")
    if isinstance(payload, bytes):
        payload = payload.decode()
    return json.loads(payload)


def _status_from_hash(row: dict[Any, Any]) -> str:
    status = row.get("status") or row.get(b"status") or ""
    return status.decode() if isinstance(status, bytes) else str(status)


def _load_items(r: redis.Redis, prefix: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in sorted(r.scan_iter(prefix + "*")):
        row = r.hgetall(key)
        if not row:
            continue
        item = _json_from_hash(row)
        item["_store_status"] = _status_from_hash(row)
        item["_redis_key"] = key.decode() if isinstance(key, bytes) else str(key)
        items.append(item)
    return items


def _items_for_user(items: list[dict[str, Any]], user_id: str) -> list[dict[str, Any]]:
    if not user_id:
        return items
    return [item for item in items if str(item.get("user_id") or "") == user_id]


def _pending_count(items: list[dict[str, Any]]) -> int:
    return sum(1 for item in items if item.get("_store_status") == "pending")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _compact_text(text: str) -> str:
    normalized = str(text or "").replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    return " ".join(normalized.split())


def _validate_raw(raw: dict[str, Any], *, require_dataset_case: bool) -> None:
    _assert(raw.get("protocol_version") == "sft-raw-v1", "raw protocol_version must be sft-raw-v1")
    _assert(str(raw.get("raw_id") or ""), "raw_id is required")
    _assert(str(raw.get("trajectory_id") or ""), "trajectory_id is required")
    _assert(str(raw.get("session_id") or ""), "session_id is required")
    _assert(str(raw.get("user_id") or ""), "user_id is required")
    _assert(str(raw.get("original_task") or ""), "original_task is required")
    datetime.fromisoformat(str(raw.get("created_at")))
    if require_dataset_case:
        case = raw.get("dataset_case")
        _assert(isinstance(case, dict), "dataset_case must be an object")
        _assert(str(case.get("docker_image") or case.get("image") or ""), "dataset_case.docker_image is required")
        _assert(str(case.get("instance_id") or ""), "dataset_case.instance_id is required")
        _assert(str(case.get("problem_statement") or ""), "dataset_case.problem_statement is required")
        _assert(isinstance(case.get("fail_to_pass"), list), "dataset_case.fail_to_pass must be a list")
        _assert(str(case.get("task_prompt") or case.get("prompt") or ""), "dataset_case.task_prompt is required")
        prompt = str(case.get("task_prompt") or case.get("prompt") or "")
        _assert("Problem Statement" in prompt, "dataset_case.task_prompt must include a Problem Statement section")
        _assert("Patch Output Format" in prompt, "dataset_case.task_prompt must include patch instructions")
        problem_statement = _compact_text(str(case.get("problem_statement") or ""))
        compact_prompt = _compact_text(prompt)
        _assert(problem_statement in compact_prompt, "dataset_case.task_prompt must embed the issue statement")
    steps = raw.get("steps")
    _assert(isinstance(steps, list) and steps, "raw.steps must be a non-empty list")
    llm_steps = [step for step in steps if isinstance(step, dict) and step.get("type") == "llm"]
    _assert(llm_steps, "raw.steps must contain at least one llm step")
    for step in llm_steps:
        _assert(isinstance(step.get("messages"), list) and step["messages"], "llm step messages are required")
        _assert(str(step.get("response_text") or (step.get("response") or {}).get("content") or ""), "llm response is required")
        _assert(str(step.get("model_id") or raw.get("model_id") or ""), "llm model_id is required")


def _validate_sample(sample: dict[str, Any]) -> None:
    _assert(sample.get("protocol_version") == "sft-sample-v1", "sample protocol_version must be sft-sample-v1")
    _assert(str(sample.get("sample_id") or ""), "sample_id is required")
    _assert(str(sample.get("source_raw_id") or ""), "source_raw_id is required")
    _assert(str(sample.get("user_id") or ""), "sample user_id is required")
    _assert(isinstance(sample.get("messages"), list) and sample["messages"], "sample messages are required")
    assistant = sample.get("assistant_message")
    _assert(isinstance(assistant, dict), "sample assistant_message is required")
    _assert(
        str(assistant.get("content") or sample.get("response_text") or "") or assistant.get("tool_calls"),
        "sample assistant text or tool_calls are required",
    )


def _latest_train_json(tmp_root: str) -> Path | None:
    candidates = [
        Path(path)
        for pattern in (
            os.path.join(tmp_root, "sft_run_*", "train.json"),
            os.path.join(tmp_root, "sft_run_*", "llama_factory", "train.json"),
        )
        for path in glob.glob(pattern)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _validate_dataset(tmp_root: str, *, min_samples: int) -> Path:
    path = _latest_train_json(tmp_root)
    _assert(path is not None, f"missing train.json under {tmp_root}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        samples = payload
        _assert(len(samples) >= min_samples, "LLaMA-Factory train.json records are missing")
        for sample in samples:
            messages = sample.get("messages")
            _assert(isinstance(messages, list) and messages, "LLaMA-Factory sample messages are required")
            _assert(messages[-1].get("role") in {"assistant", "function_call"}, "last train message must be assistant")
        return path

    samples = payload.get("samples") if isinstance(payload, dict) else None
    _assert(isinstance(samples, list) and len(samples) >= min_samples, "legacy train.json samples are missing")
    for sample in samples:
        messages = sample.get("messages")
        _assert(isinstance(messages, list) and messages, "dataset sample messages are required")
        _assert(any(msg.get("role") == "assistant" and msg.get("loss_mask") == 1 for msg in messages), "assistant loss_mask=1 is required")
        _assert(all("role" in msg for msg in messages), "dataset messages must keep ChatML roles")
    return path


def _gateway_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _task_status(gateway_url: str, task_id: str) -> str:
    task = _gateway_json(f"{gateway_url.rstrip('/')}/v1/training/tasks/{task_id}")
    return str(task.get("status") or "")


def wait_task(gateway_url: str, task_id: str, timeout: int) -> str:
    deadline = time.time() + timeout
    status = ""
    while time.time() < deadline:
        status = _task_status(gateway_url, task_id)
        if status in {"succeeded", "failed", "canceled"}:
            return status
        time.sleep(2)
    return status


def wait_rollout_state(r: redis.Redis, *, user_id: str, min_original_raw: int, timeout: int) -> None:
    deadline = time.time() + timeout
    last_state: dict[str, Any] = {}
    while time.time() < deadline:
        raw_items = _load_items(r, RAW_PREFIX)
        original_raw = [item for item in raw_items if item.get("user_id") == user_id]
        rollout_raw = [item for item in raw_items if ":sft-rollout:" in str(item.get("user_id") or "")]
        raw_statuses = sorted({item.get("_store_status") for item in raw_items})
        last_state = {
            "original_raw": len(original_raw),
            "rollout_raw": len(rollout_raw),
            "raw_statuses": raw_statuses,
        }
        if (
            len(original_raw) >= min_original_raw
            and rollout_raw
            and raw_items
            and all(item.get("_store_status") == "processed" for item in raw_items)
        ):
            return
        time.sleep(2)
    raise AssertionError(f"rollout state not ready after {timeout}s: {last_state}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--redis-url", default=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
    parser.add_argument("--gateway-url", default=os.getenv("GATEWAY_URL", "http://127.0.0.1:18180"))
    parser.add_argument("--user-id", default=os.getenv("WEB_USER_ID", "local-web-user"))
    parser.add_argument("--phase", choices=["raw", "rollout", "final", "samples", "direct-final"], required=True)
    parser.add_argument("--task-id", default="")
    parser.add_argument("--tmp-root", default="/tmp/agent_rl_online")
    parser.add_argument("--min-original-raw", type=int, default=1)
    parser.add_argument("--min-samples", type=int, default=1)
    parser.add_argument("--wait-timeout", type=int, default=600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    r = redis.Redis.from_url(args.redis_url, decode_responses=False)
    if args.phase == "rollout":
        wait_rollout_state(
            r,
            user_id=args.user_id,
            min_original_raw=args.min_original_raw,
            timeout=args.wait_timeout,
        )
    if args.phase in {"final", "direct-final"} and args.task_id:
        status = wait_task(args.gateway_url, args.task_id, args.wait_timeout)
        _assert(status == "succeeded", f"training task {args.task_id} expected succeeded, got {status!r}")

    raw_items = _load_items(r, RAW_PREFIX)
    sample_items = _load_items(r, SAMPLE_PREFIX)
    original_raw = [item for item in raw_items if item.get("user_id") == args.user_id]
    rollout_raw = [item for item in raw_items if ":sft-rollout:" in str(item.get("user_id") or "")]
    if args.phase in {"samples", "direct-final"}:
        user_samples = _items_for_user(sample_items, args.user_id)
        user_raw = _items_for_user(raw_items, args.user_id)
        _assert(
            len(user_samples) >= args.min_samples,
            f"expected samples >= {args.min_samples} for user {args.user_id}, got {len(user_samples)}",
        )
        for item in user_samples:
            _validate_sample(item)
        if args.phase == "samples":
            _assert(
                any(item.get("_store_status") == "pending" for item in user_samples),
                f"direct sample phase expects pending samples for user {args.user_id}",
            )
            print(json.dumps({"ok": True, "phase": "samples", "samples": len(user_samples)}, ensure_ascii=False))
            return 0
        _assert(
            all(item.get("_store_status") == "trained" for item in user_samples),
            f"direct final expects all samples trained for user {args.user_id}",
        )
        train_json = _validate_dataset(args.tmp_root, min_samples=args.min_samples)
        stats = _gateway_json(f"{args.gateway_url.rstrip('/')}/v1/gateway/stats")
        _assert(_pending_count(user_samples) == 0, f"pending samples should be 0 for user {args.user_id}")
        print(
            json.dumps(
                {
                    "ok": True,
                    "phase": "direct-final",
                    "samples": len(user_samples),
                    "user_pending_raw": _pending_count(user_raw),
                    "user_pending_samples": _pending_count(user_samples),
                    "train_json": str(train_json),
                    "stats": stats,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    _assert(len(original_raw) >= args.min_original_raw, f"expected original raw >= {args.min_original_raw}, got {len(original_raw)}")
    for item in original_raw:
        _validate_raw(item, require_dataset_case=True)

    if args.phase == "raw":
        _assert(any(item.get("_store_status") == "pending" for item in original_raw), "raw phase expects pending original raw")
        print(json.dumps({"ok": True, "phase": "raw", "original_raw": len(original_raw)}, ensure_ascii=False))
        return 0

    _assert(rollout_raw, "final phase expects supervisor rollout raw")
    for item in rollout_raw:
        _validate_raw(item, require_dataset_case=True)
    relevant_raw = original_raw + rollout_raw
    _assert(all(item.get("_store_status") == "processed" for item in relevant_raw), "final phase expects relevant raw processed")

    if args.phase == "rollout":
        print(
            json.dumps(
                {
                    "ok": True,
                    "phase": "rollout",
                    "original_raw": len(original_raw),
                    "rollout_raw": len(rollout_raw),
                    "raw_statuses": sorted({item.get("_store_status") for item in raw_items}),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    _assert(len(sample_items) >= args.min_samples, f"expected samples >= {args.min_samples}, got {len(sample_items)}")
    for item in sample_items:
        _validate_sample(item)
    _assert(all(item.get("_store_status") == "trained" for item in sample_items), "final phase expects all samples trained")
    train_json = _validate_dataset(args.tmp_root, min_samples=args.min_samples)
    stats = _gateway_json(f"{args.gateway_url.rstrip('/')}/v1/gateway/stats")
    _assert(_pending_count(relevant_raw) == 0, f"pending relevant raw should be 0 for user {args.user_id}")
    _assert(_pending_count(sample_items) == 0, "pending samples should be 0")
    print(
        json.dumps(
            {
                "ok": True,
                "phase": "final",
                "original_raw": len(original_raw),
                "rollout_raw": len(rollout_raw),
                "samples": len(sample_items),
                "train_json": str(train_json),
                "stats": stats,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[sft-e2e] validation failed: {exc}", file=sys.stderr)
        raise
