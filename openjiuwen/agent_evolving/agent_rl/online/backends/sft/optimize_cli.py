#!/usr/bin/env python3
# coding: utf-8

"""Run direct supervisor SWE rollouts and upload SFT samples for training."""

from __future__ import annotations

import argparse
import asyncio
import json as json_module
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CASES = "/data1/lll/workspace/sft_train_demo/swebench_verified_mini_docker_model.md"
logger = logging.getLogger("online_rl.sft_optimize")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-mapping",
        "--cases",
        dest="dataset_mapping",
        default=os.getenv("SFT_DATASET_MAPPING", DEFAULT_CASES),
        help="Dataset mapping file: JSON, JSONL, or SWE image-list Markdown.",
    )
    parser.add_argument("--limit", type=int, default=int(os.getenv("SFT_OPTIMIZE_LIMIT", "1")))
    parser.add_argument("--offset", type=int, default=int(os.getenv("SFT_OPTIMIZE_OFFSET", "0")))
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("RL_GATEWAY_URL") or os.getenv("TRAJECTORY_GATEWAY_URL") or "",
        help="Gateway URL reachable from host and SWE Docker containers.",
    )
    parser.add_argument(
        "--scheduler-url",
        default=os.getenv("RL_SCHEDULER_URL", ""),
        help="Reserved scheduler URL for future direct scheduler APIs; training tasks currently use gateway.",
    )
    parser.add_argument(
        "--supervisor-url",
        default=os.getenv("SUPERVISOR_URL", ""),
        help="OpenAI-compatible supervisor endpoint base URL reachable from SWE Docker containers.",
    )
    parser.add_argument("--supervisor-token", default=os.getenv("SUPERVISOR_TOKEN", "EMPTY"))
    parser.add_argument("--supervisor-model", default=os.getenv("SUPERVISOR_MODEL", ""))
    parser.add_argument(
        "--tenant-id",
        default=os.getenv("RL_ONLINE_TENANT_ID") or os.getenv("WEB_USER_ID") or "local-web-user",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.getenv("SFT_ROLLOUT_CONCURRENCY", "1")),
        help="Number of SWE Docker task containers to run concurrently.",
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
    parser.add_argument("--command", default=os.getenv("SFT_DOCKER_ROLLOUT_COMMAND", ""))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("SFT_TASK_ROLLOUT_TIMEOUT", "900")))
    parser.add_argument(
        "--trigger-training",
        action="store_true",
        help="Create a /v1/training/tasks task after rollout.",
    )
    parser.add_argument("--gateway-api-key", default=os.getenv("TRAJECTORY_GATEWAY_API_KEY", ""))
    parser.add_argument(
        "--upload-check-timeout",
        type=int,
        default=int(os.getenv("SFT_UPLOAD_CHECK_TIMEOUT", "60")),
        help="Seconds to wait until direct SFT samples are visible in Redis before creating a training task.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print Docker commands without launching containers.")
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def main() -> int:
    from openjiuwen.agent_evolving.agent_rl.online.core.task_rollouter import (
        SFTTaskRolloutConfig,
        build_task_rollout_docker_command,
        command_for_log,
        load_sft_task_cases,
        run_sft_task_cases,
    )

    configure_logging()
    args = parse_args()
    if not args.gateway_url:
        logger.error("missing --gateway-url or RL_GATEWAY_URL")
        return 2
    if not args.supervisor_url:
        logger.error("missing --supervisor-url or SUPERVISOR_URL")
        return 2

    cases = load_sft_task_cases(Path(args.dataset_mapping))
    selected = cases[args.offset:args.offset + max(0, args.limit)]
    if not selected:
        logger.error("no cases selected")
        return 2

    config = SFTTaskRolloutConfig(
        gateway_url=args.gateway_url,
        supervisor_url=args.supervisor_url,
        supervisor_token=args.supervisor_token,
        supervisor_model=args.supervisor_model,
        tenant_id=args.tenant_id,
        rollout_command=args.command,
        timeout_seconds=args.timeout,
        sft_upload_mode="sample",
        backend=args.backend,
        local_repo_root=args.local_repo_root,
        local_repo_work_root=args.local_repo_work_root,
        local_repo_web_port_base=int(os.getenv("SFT_LOCAL_REPO_WEB_PORT_BASE", "19000")),
        local_repo_agent_port_base=int(os.getenv("SFT_LOCAL_REPO_AGENT_PORT_BASE", "18092")),
    )
    backend = args.backend.strip().lower().replace("-", "_")
    logger.info(
        "[sft-optimize] direct supervisor rollout "
        "backend=%s cases=%d concurrency=%d user=%s gateway=%s scheduler=%s",
        args.backend,
        len(selected),
        args.concurrency,
        args.tenant_id,
        args.gateway_url,
        args.scheduler_url or "<gateway-task-api>",
    )
    for case in selected:
        logger.info(
            "[sft-optimize] instance=%s image=%s repo=%s base_commit=%s",
            case.instance_id,
            case.docker_image,
            case.repo,
            case.base_commit,
        )
        if args.dry_run:
            if backend in {"local_program", "local-program", "program"}:
                logger.info("local_program path=%s", case.local_program_path or "<unset>")
            elif backend in {"akernel", "local_repo", "local"}:
                logger.info(
                    "akernel repo=%s base_commit=%s",
                    case.repo,
                    case.base_commit or "<current-head>",
                )
            else:
                logger.info("%s", command_for_log(build_task_rollout_docker_command(case, config)))
    if args.dry_run:
        return 0

    results = asyncio.run(run_sft_task_cases(selected, config, concurrency=args.concurrency))
    failed = 0
    for result in results:
        logger.info("[sft-optimize] exit=%d instance=%s", result.exit_code, result.case.instance_id)
        if result.stdout_tail:
            logger.info("%s", result.stdout_tail)
        if result.stderr_tail:
            logger.error("%s", result.stderr_tail)
        if result.exit_code != 0:
            failed += 1
    if failed:
        return 1

    uploaded, source = wait_for_pending_sft_samples(
        user_id=args.tenant_id,
        expected=len(selected),
        timeout_seconds=args.upload_check_timeout,
        gateway_url=args.gateway_url,
        gateway_api_key=args.gateway_api_key,
    )
    logger.info(
        "[sft-optimize] uploaded_samples=%d expected=%d user=%s source=%s",
        uploaded,
        len(selected),
        args.tenant_id,
        source,
    )
    if uploaded >= 0 and uploaded < len(selected):
        logger.error(
            "[sft-optimize] ERROR: supervisor replay finished but uploaded SFT samples are insufficient; "
            "check that the Docker containers can reach the gateway and that the sample status is visible.",
        )
        return 1
    if uploaded < 0:
        logger.warning(
            "[sft-optimize] warning: uploaded sample visibility could not be verified; "
            "continuing to training trigger so the full chain still exercises the scheduler.",
        )

    if args.trigger_training:
        task = create_training_task(
            gateway_url=args.gateway_url,
            user_id=args.tenant_id,
            api_key=args.gateway_api_key,
            metadata=training_task_metadata(
                dataset_mapping=args.dataset_mapping,
                case_count=len(selected),
                concurrency=args.concurrency,
                scheduler_url=args.scheduler_url,
            ),
        )
        logger.info("[sft-optimize] training_task=%s", json_module.dumps(task, ensure_ascii=False))
    return 0


def wait_for_pending_sft_samples(
    *,
    user_id: str,
    expected: int,
    timeout_seconds: int,
    gateway_url: str,
    gateway_api_key: str = "",
) -> tuple[int, str]:
    """Wait for Docker task containers to flush direct ``sft-sample-v1`` uploads.

    The user-visible gateway stats route is global and not user-scoped. For
    validation, read the same Redis-backed sample queue that the scheduler
    consumes so the check stays aligned with the real training trigger.
    """

    timeout_seconds = max(0, int(timeout_seconds))
    source = "redis"
    last_count = pending_sft_sample_count(user_id)
    if timeout_seconds <= 0:
        return last_count, source
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if last_count >= expected:
            return last_count, source
        count = pending_sft_sample_count(user_id)
        if count >= 0:
            last_count = count
        else:
            source = "unavailable"
        time.sleep(1)
    return last_count, source


def gateway_pending_sft_sample_count(*, gateway_url: str, gateway_api_key: str, user_id: str) -> int:
    """Compatibility alias for the Redis-backed pending SFT sample counter."""

    del gateway_url, gateway_api_key
    return pending_sft_sample_count(user_id)


def pending_sft_sample_count(user_id: str) -> int:
    """Return pending SFT sample count using Redis, falling back to zero on errors."""

    redis_url = detect_redis_url()
    try:
        import redis

        client = redis.Redis.from_url(redis_url, decode_responses=True)
        try:
            return int(client.zcard(f"rl:sft_sample_idx:{user_id}:pending"))
        finally:
            client.close()
    except Exception as exc:
        logger.warning("[sft-optimize] failed to check uploaded samples redis=%s err=%r", redis_url, exc)
        return -1


def detect_redis_url() -> str:
    configured = os.getenv("SFT_REDIS_URL") or os.getenv("REDIS_URL")
    if configured:
        return configured
    container = os.getenv("REDIS_CONTAINER_NAME", "pinchbench-redis")
    port = os.getenv("REDIS_PORT", "6379")
    docker_bin = shutil.which("docker") or "/usr/bin/docker"
    try:
        ip = subprocess.check_output(
            [docker_bin, "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception as exc:
        logger.debug("failed to inspect redis docker container=%s err=%r", container, exc)
        ip = ""
    return f"redis://{ip or '127.0.0.1'}:{port}/0"


def training_task_metadata(
    *,
    dataset_mapping: str,
    case_count: int,
    concurrency: int,
    scheduler_url: str,
) -> dict[str, object]:
    """Build the metadata attached to /v1/training/tasks creation requests."""

    return {
        "source": "sft-optimize",
        "dataset_mapping": str(Path(dataset_mapping)),
        "case_count": case_count,
        "concurrency": concurrency,
        "scheduler_url": scheduler_url,
    }


def create_training_task(
    *,
    gateway_url: str,
    user_id: str,
    api_key: str = "",
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    """Create an asynchronous training task through the gateway task API."""

    payload = json_module.dumps({"user_id": user_id, "metadata": metadata or {}}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        f"{gateway_url.rstrip('/')}/v1/training/tasks",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json_module.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"failed to create training task status={exc.code} body={body}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
