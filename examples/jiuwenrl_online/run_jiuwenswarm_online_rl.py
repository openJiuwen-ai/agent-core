#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Collect one JiuwenSwarm attempt and optionally start an Online RL Training Run."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import requests

_TERMINAL_RUN_STATUSES = {"succeeded", "failed", "canceled"}


class AIGWControlClient:
    """Call the signed AIGW Online RL control surface."""

    def __init__(self, endpoint: str, hmac_key: bytes | None, timeout: float) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._hmac_key = hmac_key
        self._timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        request_headers = dict(headers or {})
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        if self._hmac_key is not None:
            timestamp = str(int(time.time() * 1000))
            request_headers["X-Timestamp"] = timestamp
            request_headers["X-Signature"] = hmac.new(
                self._hmac_key,
                timestamp.encode() + body,
                hashlib.sha256,
            ).hexdigest()

        try:
            response = requests.request(
                method,
                f"{self._endpoint}{path}",
                headers=request_headers,
                data=body,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"AIGW request failed: {method} {path}: {exc}") from exc
        if not 200 <= response.status_code < 300:
            detail = response.text.strip()[:2000]
            raise RuntimeError(f"AIGW returned {response.status_code} for {method} {path}: {detail}")
        try:
            result = response.json()
        except requests.JSONDecodeError as exc:
            raise RuntimeError(f"AIGW returned invalid JSON for {method} {path}") from exc
        if not isinstance(result, dict):
            raise RuntimeError(f"AIGW returned a non-object response for {method} {path}")
        return result


def _run_jiuwenswarm(args: argparse.Namespace) -> dict[str, Any]:
    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    command = [
        args.jiuwenswarm_executable,
        "chat",
        args.prompt,
        "--mode",
        args.mode,
        "--session",
        args.chat_session or f"online-rl-{uuid.uuid4().hex[:12]}",
        "--cwd",
        str(workspace),
        "--project-dir",
        str(workspace),
        "--trusted-dir",
        str(workspace),
        "--json",
        "--timeout",
        str(args.agent_timeout),
    ]
    if args.gateway_url:
        command.extend(("--gateway-url", args.gateway_url))
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=args.agent_timeout + 60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"JiuwenSwarm executable was not found: {args.jiuwenswarm_executable}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"JiuwenSwarm timed out after {args.agent_timeout} seconds") from exc
    if completed.returncode != 0:
        raise RuntimeError(
            f"JiuwenSwarm failed\nstdout:\n{completed.stdout[-4000:]}\nstderr:\n{completed.stderr[-4000:]}"
        )
    for line in reversed(completed.stdout.splitlines()):
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict):
            if result.get("ok") is not True:
                raise RuntimeError(f"JiuwenSwarm returned an unsuccessful result: {result}")
            return result
    raise RuntimeError(f"JiuwenSwarm did not emit a JSON result:\n{completed.stdout[-4000:]}")


def _wait_for_training(
    client: AIGWControlClient,
    run: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    run_id = str(run["training_run_id"])
    deadline = time.monotonic() + timeout
    last_state: tuple[str, str] | None = None
    while time.monotonic() < deadline:
        current = client.request("GET", f"/v1/rl/training/runs/{run_id}")
        state = (str(current.get("status")), str(current.get("stage")))
        if state != last_state:
            print(f"Training Run {run_id}: status={state[0]} stage={state[1]}")
            last_state = state
        if state[0] in _TERMINAL_RUN_STATUSES:
            return current
        time.sleep(2)
    raise RuntimeError(f"Training Run {run_id} timed out after {timeout} seconds")


def _parser() -> argparse.ArgumentParser:
    session_id = os.getenv("ONLINE_RL_SESSION_ID")
    parser = argparse.ArgumentParser(
        description="Collect one terminal-reward JiuwenSwarm attempt through AIGW and optionally train a LoRA.",
    )
    parser.add_argument("prompt", help="Prompt sent to JiuwenSwarm")
    parser.add_argument("--reward", type=float, required=True, help="Terminal reward in the range [0, 1]")
    parser.add_argument(
        "--session-id",
        default=session_id,
        required=session_id is None,
        help="Value configured as JiuwenSwarm's X-Agent-Session-Id (or ONLINE_RL_SESSION_ID)",
    )
    parser.add_argument("--aigw-url", default=os.getenv("AIGW_URL", "http://127.0.0.1:18080"))
    parser.add_argument("--gateway-url", default=os.getenv("JIUWENSWARM_GATEWAY_URL"))
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--mode", default="agent")
    parser.add_argument("--chat-session")
    parser.add_argument("--jiuwenswarm-executable", default="jiuwenswarm")
    parser.add_argument("--http-timeout", type=float, default=300)
    parser.add_argument("--agent-timeout", type=float, default=900)
    parser.add_argument("--training-timeout", type=float, default=2400)
    parser.add_argument("--start-service", action="store_true", help="Start the AIGW-owned RL Service if needed")
    parser.add_argument("--train", action="store_true", help="Create and wait for a Training Run after capture")
    parser.add_argument(
        "--unsigned",
        action="store_true",
        help="Use only with a development AIGW that has HMAC explicitly disabled",
    )
    return parser


def _client(args: argparse.Namespace) -> AIGWControlClient:
    key: bytes | None = None
    if not args.unsigned:
        raw_key = os.getenv("AIGW_API_HMAC_KEY")
        if not raw_key:
            raise RuntimeError("AIGW_API_HMAC_KEY is required unless --unsigned is used")
        key = raw_key.encode()
    return AIGWControlClient(args.aigw_url, key, args.http_timeout)


def _run(args: argparse.Namespace) -> None:
    if not 0 <= args.reward <= 1:
        raise RuntimeError("--reward must be in the range [0, 1]")
    client = _client(args)
    service = client.request("GET", "/v1/rl/service")
    if service.get("status") != "running":
        if not args.start_service:
            raise RuntimeError("RL Service is not running; start it first or pass --start-service")
        service = client.request("POST", "/v1/rl/service/start")
        if service.get("status") != "running":
            raise RuntimeError(f"RL Service did not become ready: {service}")

    task = client.request(
        "POST",
        "/v1/rl/tasks/start",
        payload={"reward_mode": "terminal"},
        headers={"X-Agent-Session-Id": args.session_id},
    )
    task_id = str(task["rl_task_id"])
    print(f"RL Task {task_id}: policy={task['policy_lora_name']}")
    try:
        result = _run_jiuwenswarm(args)
    except BaseException:
        try:
            client.request("POST", f"/v1/rl/tasks/{task_id}/stop")
        except RuntimeError as cleanup_error:
            print(f"Failed to stop RL Task {task_id}: {cleanup_error}", file=sys.stderr)
        raise

    client.request("POST", f"/v1/rl/tasks/{task_id}/stop")
    reward_result = client.request(
        "POST",
        f"/v1/rl/tasks/{task_id}/reward",
        payload={"reward": args.reward},
    )
    print(f"JiuwenSwarm response: {result.get('content', '')}")
    print(f"Captured samples: {reward_result.get('sample_count', 0)}")

    if not args.train:
        return
    run = client.request("POST", "/v1/rl/training/runs", payload={})
    print(f"Training Run {run['training_run_id']}: samples={run['sample_count']}")
    finished = _wait_for_training(client, run, args.training_timeout)
    if finished.get("status") != "succeeded":
        raise RuntimeError(
            f"Training Run {finished['training_run_id']} ended as {finished.get('status')}: "
            f"{finished.get('failure_reason')}"
        )
    print(f"Activated policy: {finished['lora_name']} ({finished['lora_path']})")


def main() -> int:
    args = _parser().parse_args()
    try:
        _run(args)
    except KeyboardInterrupt:
        return 130
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
