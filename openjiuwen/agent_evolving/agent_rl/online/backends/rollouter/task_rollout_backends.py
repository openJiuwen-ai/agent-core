# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Concrete task rollout backends used by the SFT task rollouter."""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

from ...abstract.rollouter import TaskRolloutBackend, TaskRolloutCommandResult, TaskRolloutCommandSpec
from ...core.task_rollouter import SFTTaskCase, SFTTaskRolloutConfig
from .docker_runtime import (
    SFTJiuwenclawDockerRequest,
    build_jiuwenclaw_docker_command,
    build_jiuwenclaw_docker_env,
    default_jiuwenclaw_host_path,
    default_jiuwenclaw_task_command,
    normalize_dataset_case,
)

logger = logging.getLogger(__name__)


def _task_rollout_extra_env(tenant_id: str) -> dict[str, str]:
    return {
        "CUSTOM_HEADERS": json.dumps({"x-user-id": tenant_id}, separators=(",", ":")),
        "ENABLE_TRAJECTORY_COLLECTION": "false",
        "MEMORY_ENGINE": "none",
        "JIUWENSWARM_LIGHT_PROFILE": "1",
    }


def _host_pythonpath() -> str:
    agent_core_host = Path(
        os.getenv("SFT_DOCKER_AGENT_CORE_HOST_PATH", "") or Path(__file__).resolve().parents[6]
    ).resolve()
    jiuwenclaw_host = default_jiuwenclaw_host_path(agent_core_host)
    paths = [str(agent_core_host)]
    if jiuwenclaw_host.exists():
        paths.append(str(jiuwenclaw_host))
    current = os.getenv("PYTHONPATH", "").strip()
    if current:
        paths.append(current)
    return ":".join(paths)


def _local_repo_work_dir(case: SFTTaskCase, config: SFTTaskRolloutConfig) -> Path:
    configured = config.local_repo_work_root or os.getenv(
        "SFT_LOCAL_REPO_WORK_ROOT",
        "/tmp/jiuwenswarm-local-repos",
    )
    work_root = Path(configured)
    work_root = work_root.expanduser().resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{case.instance_id}-", dir=str(work_root)))


def _local_program_source_path(case: SFTTaskCase) -> Path:
    configured = (case.local_program_path or case.local_repo_path or "").strip()
    if not configured:
        raise FileNotFoundError(f"local program path is required for {case.instance_id}")
    candidate = Path(configured).expanduser().resolve()
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(f"local program directory not found for {case.instance_id}: {candidate}")


def _prepare_local_program_workspace(case: SFTTaskCase, config: SFTTaskRolloutConfig) -> tuple[Path, Path, Path]:
    source_dir = _local_program_source_path(case)
    work_dir = _local_repo_work_dir(case, config)
    repo_dir = work_dir / "repo"
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".git")
    shutil.copytree(source_dir, repo_dir, ignore=ignore)
    return source_dir, work_dir, repo_dir


def _base_task_env(
    case: SFTTaskCase,
    config: SFTTaskRolloutConfig,
    *,
    data_dir: Path,
) -> dict[str, str]:
    dataset_case_json = json.dumps(
        normalize_dataset_case(
            case.dataset_case(),
            image=case.docker_image,
            task_prompt=case.task_prompt,
            instance_id=case.instance_id,
        ),
        ensure_ascii=False,
    )
    return build_jiuwenclaw_docker_env(
        SFTJiuwenclawDockerRequest(
            image=case.docker_image,
            task_prompt=case.task_prompt,
            instance_id=case.instance_id,
            dataset_case=case.dataset_case(),
            gateway_url=config.gateway_url,
            supervisor_url=config.supervisor_url,
            supervisor_token=config.supervisor_token,
            supervisor_model=config.supervisor_model,
            tenant_id=config.tenant_id,
            rollout_command=config.rollout_command,
            data_dir=str(data_dir),
            sft_upload_mode=config.sft_upload_mode,
            extra_env=_task_rollout_extra_env(config.tenant_id),
        ),
        dataset_case_json=dataset_case_json,
        pythonpath=_host_pythonpath(),
        data_dir=str(data_dir),
    )


def _build_host_process_env(
    case: SFTTaskCase,
    config: SFTTaskRolloutConfig,
    *,
    repo_dir: Path,
    work_dir: Path,
    data_dir: Path,
    web_port: int,
    agent_port: int,
    index: int,
    extra: dict[str, str],
) -> dict[str, str]:
    env = _base_task_env(case, config, data_dir=data_dir)
    env.update(
        {
            "SFT_TASK_CWD": str(repo_dir),
            "HOME": str(data_dir),
            "WEB_PORT": str(web_port),
            "GATEWAY_PORT": str(config.local_repo_web_port_base + 1000 + index),
            "AGENT_SERVER_PORT": str(agent_port),
            "AGENT_PORT": str(agent_port),
            "SFT_LOCAL_REPO_WORKDIR": str(work_dir),
            **extra,
        }
    )
    merged_env = dict(os.environ)
    merged_env.update(env)
    return merged_env


def _host_task_command(config: SFTTaskRolloutConfig) -> list[str]:
    rollout_command = config.rollout_command.strip() or default_jiuwenclaw_task_command()
    python_bin = Path(sys.executable).resolve().parent
    command_prefix = f"set -e; export PATH={shlex.quote(str(python_bin))}:$PATH; hash -r;"
    command_text = f"{command_prefix} {rollout_command}"
    return ["bash", "-lc", command_text]


class DockerTaskRolloutBackend(TaskRolloutBackend):
    """Run each SWE case inside its declared task Docker image."""

    name = "docker"
    aliases = ("container", "swe_docker")

    def build_command(self, case: SFTTaskCase, config: SFTTaskRolloutConfig) -> list[str]:
        return build_jiuwenclaw_docker_command(
            SFTJiuwenclawDockerRequest(
                image=case.docker_image,
                task_prompt=case.task_prompt,
                instance_id=case.instance_id,
                dataset_case=case.dataset_case(),
                gateway_url=config.gateway_url,
                supervisor_url=config.supervisor_url,
                supervisor_token=config.supervisor_token,
                supervisor_model=config.supervisor_model,
                tenant_id=config.tenant_id,
                rollout_command=config.rollout_command,
                data_dir=f"/tmp/jiuwenswarm-{case.instance_id}",
                sft_upload_mode=config.sft_upload_mode,
                extra_env=_task_rollout_extra_env(config.tenant_id),
            )
        )

    def build_spec(
        self,
        case: SFTTaskCase,
        config: SFTTaskRolloutConfig,
        *,
        index: int = 0,
    ) -> TaskRolloutCommandSpec:
        del index
        return TaskRolloutCommandSpec(
            name=case.instance_id,
            command=self.build_command(case, config),
            timeout_seconds=config.timeout_seconds,
        )


class LocalProgramTaskRolloutBackend(TaskRolloutBackend):
    """Run a self-contained local Python task directory without Docker."""

    name = "local_program"
    aliases = ("local-program", "program")

    def build_spec(
        self,
        case: SFTTaskCase,
        config: SFTTaskRolloutConfig,
        *,
        index: int = 0,
    ) -> TaskRolloutCommandSpec:
        source_dir, work_dir, repo_dir = _prepare_local_program_workspace(case, config)
        data_dir = work_dir / "jiuwenswarm"
        data_dir.mkdir(parents=True, exist_ok=True)
        env = _build_host_process_env(
            case,
            config,
            repo_dir=repo_dir,
            work_dir=work_dir,
            data_dir=data_dir,
            web_port=config.local_repo_web_port_base + index,
            agent_port=config.local_repo_agent_port_base + index,
            index=index,
            extra={
                "SFT_LOCAL_PROGRAM_SOURCE_DIR": str(source_dir),
                "SFT_LOCAL_PROGRAM_WORKDIR": str(work_dir),
                "SFT_TASK_LIGHT_CONFIG": os.getenv("SFT_TASK_LIGHT_CONFIG", "1"),
            },
        )
        logger.info(
            "Prepared local Python task case=%s source=%s workdir=%s",
            case.instance_id,
            source_dir,
            work_dir,
        )
        return TaskRolloutCommandSpec(
            name=case.instance_id,
            command=_host_task_command(config),
            timeout_seconds=config.timeout_seconds,
            env=env,
        )
