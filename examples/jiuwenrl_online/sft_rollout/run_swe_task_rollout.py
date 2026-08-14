#!/usr/bin/env python3
# coding: utf-8

"""Launch SWE case containers to collect scenario 2-1 SFT raw trajectories."""

from __future__ import annotations

import argparse
import asyncio
import os
import logging
from pathlib import Path
import sys

from openjiuwen.agent_evolving.agent_rl.online.core.task_rollouter import (
    SFTTaskRolloutConfig,
    build_task_rollout_docker_command,
    command_for_log,
    load_sft_task_cases,
    run_sft_task_cases,
)


DEFAULT_CASES = "/data1/lll/workspace/sft_train_demo/swebench_verified_mini_docker_model.md"
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=DEFAULT_CASES, help="SFT task case table: Markdown, JSON, or JSONL.")
    parser.add_argument("--limit", type=int, default=2, help="Number of cases to launch.")
    parser.add_argument("--offset", type=int, default=0, help="Start offset in the case table.")
    parser.add_argument("--gateway-url", required=True, help="Gateway URL reachable from the SWE containers.")
    parser.add_argument("--supervisor-url", required=True, help="OpenAI-compatible model endpoint base URL.")
    parser.add_argument("--supervisor-token", default="EMPTY", help="Supervisor model API token.")
    parser.add_argument("--supervisor-model", default="", help="Supervisor model name.")
    parser.add_argument("--tenant-id", default="local-web-user", help="Tenant/user id for SFTOnlineRail.")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.getenv("SFT_ROLLOUT_CONCURRENCY", "1")),
        help="Number of SWE containers to run concurrently.",
    )
    parser.add_argument(
        "--backend",
        default=os.getenv("SFT_ROLLOUT_BACKEND", "docker"),
        help="Rollout backend: docker, local_program, or akernel. local_repo is kept as an akernel alias.",
    )
    parser.add_argument(
        "--local-repo-root",
        default=os.getenv("SFT_LOCAL_REPO_ROOT", ""),
        help="Root containing local SWE repo mirrors for --backend akernel/local_repo.",
    )
    parser.add_argument(
        "--local-repo-work-root",
        default=os.getenv("SFT_LOCAL_REPO_WORK_ROOT", "/tmp/jiuwenswarm-local-repos"),
        help="Temporary checkout root for --backend akernel/local_repo.",
    )
    parser.add_argument(
        "--command",
        default="",
        help="Command executed inside each SWE container after activating host conda; default sends SFT_TASK_PROMPT once.",
    )
    parser.add_argument("--timeout", type=int, default=900, help="Per-case timeout in seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Print Docker commands without launching containers.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = load_sft_task_cases(Path(args.cases))
    selected = cases[args.offset : args.offset + max(0, args.limit)]
    config = SFTTaskRolloutConfig(
        gateway_url=args.gateway_url,
        supervisor_url=args.supervisor_url,
        supervisor_token=args.supervisor_token,
        supervisor_model=args.supervisor_model,
        tenant_id=args.tenant_id,
        rollout_command=args.command,
        timeout_seconds=args.timeout,
        backend=args.backend,
        local_repo_root=args.local_repo_root,
        local_repo_work_root=args.local_repo_work_root,
        local_repo_web_port_base=int(os.getenv("SFT_LOCAL_REPO_WEB_PORT_BASE", "19000")),
        local_repo_agent_port_base=int(os.getenv("SFT_LOCAL_REPO_AGENT_PORT_BASE", "18092")),
    )
    if not selected:
        print("no cases selected", file=sys.stderr)
        return 2

    logger.info("Selected %d SFT task case(s) with concurrency=%d", len(selected), args.concurrency)
    backend = args.backend.strip().lower().replace("-", "_")
    for case in selected:
        print(f"[task-rollout] instance={case.instance_id} image={case.docker_image} repo={case.repo} base_commit={case.base_commit}")
        if args.dry_run:
            if backend in {"akernel", "local_repo", "local"}:
                print(f"akernel repo={case.repo} base_commit={case.base_commit or '<current-head>'}")
            else:
                cmd = build_task_rollout_docker_command(case, config)
                print(command_for_log(cmd))
    if args.dry_run:
        return 0

    results = asyncio.run(run_sft_task_cases(selected, config, concurrency=args.concurrency))
    failed = 0
    for result in results:
        print(f"[task-rollout] exit={result.exit_code} instance={result.case.instance_id}")
        if result.stdout_tail:
            print(result.stdout_tail)
        if result.stderr_tail:
            print(result.stderr_tail, file=sys.stderr)
        if result.exit_code != 0:
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
