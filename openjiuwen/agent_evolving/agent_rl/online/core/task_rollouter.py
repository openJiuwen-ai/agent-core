# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Launch CPU-only SWE case containers for scenario 2-1 raw SFT collection."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..abstract.rollouter import TaskRolloutBackend, TaskRolloutCommandSpec
from ..backends.rollouter.docker_runtime import (
    sft_rollout_concurrency,
)

logger = logging.getLogger(__name__)


IMAGE_PATTERN = re.compile(r"`([^`]*sweb\.eval[^`]+)`")
DEFAULT_SWE_BENCH_DATASET = "princeton-nlp/SWE-bench_Verified"
DEFAULT_SWE_BENCH_SPLIT = "test"


# Case / config / result objects keep scenario 2-1 data and command execution
# boundaries explicit: load case metadata once, build one container command,
# and return bounded stdout/stderr for later SFT conversion.
@dataclass(frozen=True)
class SFTTaskCase:
    """One scenario 2-1 case that can be launched as an initial jiuwenswarm session."""

    instance_id: str
    docker_image: str
    task_prompt: str
    repo: str = ""
    base_commit: str = ""
    problem_statement: str = ""
    test_cmd: str = ""
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)
    gold_patch: str = ""
    repo_url: str = ""
    local_repo_path: str = ""
    local_program_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def dataset_case(self) -> dict[str, Any]:
        return {
            **self.metadata,
            "instance_id": self.instance_id,
            "image": self.docker_image,
            "docker_image": self.docker_image,
            "task_prompt": self.task_prompt,
            "prompt": self.task_prompt,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "problem_statement": self.problem_statement,
            "test_cmd": self.test_cmd,
            "fail_to_pass": list(self.fail_to_pass),
            "pass_to_pass": list(self.pass_to_pass),
            "gold_patch": self.gold_patch,
            "repo_url": self.repo_url,
            "local_repo_path": self.local_repo_path,
            "local_program_path": self.local_program_path,
        }


@dataclass(frozen=True)
class SFTTaskRolloutConfig:
    """Runtime config for launching task-rollout containers."""

    gateway_url: str
    supervisor_url: str
    supervisor_token: str = "EMPTY"
    supervisor_model: str = ""
    tenant_id: str = "local-web-user"
    rollout_command: str = ""
    timeout_seconds: int = 900
    sft_upload_mode: str = "raw"
    backend: str = "docker"
    local_repo_root: str = ""
    local_repo_work_root: str = "/tmp/jiuwenswarm-local-repos"
    local_repo_web_port_base: int = 19000
    local_repo_agent_port_base: int = 18092


@dataclass(frozen=True)
class SFTTaskRolloutResult:
    """Result from one task-rollout container process."""

    case: SFTTaskCase
    command: list[str]
    exit_code: int
    stdout_tail: str
    stderr_tail: str


def _normalize_rollout_backend(raw: str) -> str:
    backend = (raw or "").strip().lower()
    if backend in {"local_program", "local-program", "program"}:
        return "local_program"
    return "docker"


def _backend_registry() -> dict[str, TaskRolloutBackend]:
    from ..backends.rollouter.task_rollout_backends import (
        DockerTaskRolloutBackend,
        LocalProgramTaskRolloutBackend,
    )

    backends: tuple[TaskRolloutBackend, ...] = (
        DockerTaskRolloutBackend(),
        LocalProgramTaskRolloutBackend(),
    )
    registry: dict[str, TaskRolloutBackend] = {}
    for backend in backends:
        for name in (backend.name, *backend.aliases):
            registry[_normalize_rollout_backend(name)] = backend
            registry[name.strip().lower().replace("-", "_")] = backend
    return registry


def get_task_rollout_backend(name: str) -> TaskRolloutBackend:
    """Return the concrete rollout backend selected by ``name``."""

    registry = _backend_registry()
    normalized = _normalize_rollout_backend(name)
    return registry.get(normalized) or registry["docker"]


def load_sft_task_cases(path: str | Path) -> list[SFTTaskCase]:
    """Load SFT task cases from JSON, JSONL, or the current SWE image-list Markdown."""

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".jsonl":
        return [
            _case_from_mapping(json.loads(line), base_dir=source.parent)
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    if suffix == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        items = payload.get("items", payload.get("cases", payload)) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise ValueError(f"SFT task JSON must contain a list: {source}")
        return [_case_from_mapping(item, base_dir=source.parent) for item in items if isinstance(item, dict)]
    return _cases_from_markdown(source)


def build_task_rollout_docker_command(case: SFTTaskCase, config: SFTTaskRolloutConfig) -> list[str]:
    """Build the CPU-only Docker command for an initial scenario 2-1 jiuwenswarm run."""

    from ..backends.rollouter.task_rollout_backends import DockerTaskRolloutBackend

    return DockerTaskRolloutBackend().build_command(case, config)


def build_task_rollout_local_program_spec(
    case: SFTTaskCase,
    config: SFTTaskRolloutConfig,
    *,
    index: int = 0,
) -> TaskRolloutCommandSpec:
    """Build a host-process rollout command for a self-contained local Python task."""

    from ..backends.rollouter.task_rollout_backends import LocalProgramTaskRolloutBackend

    return LocalProgramTaskRolloutBackend().build_spec(case, config, index=index)


def run_sft_task_case(case: SFTTaskCase, config: SFTTaskRolloutConfig) -> SFTTaskRolloutResult:
    backend = get_task_rollout_backend(config.backend)
    logger.info(
        "Launching single SFT task case instance=%s backend=%s image=%s timeout=%ss",
        case.instance_id,
        backend.name,
        case.docker_image,
        config.timeout_seconds,
    )
    result = asyncio.run(
        backend.run_case(case, config)
    )
    logger.info(
        "Completed single SFT task case instance=%s exit=%s",
        case.instance_id,
        result.exit_code,
    )
    return SFTTaskRolloutResult(
        case=case,
        command=result.command,
        exit_code=result.exit_code,
        stdout_tail=result.stdout_tail,
        stderr_tail=result.stderr_tail,
    )


async def run_sft_task_cases(
    cases: list[SFTTaskCase],
    config: SFTTaskRolloutConfig,
    *,
    concurrency: int | None = None,
) -> list[SFTTaskRolloutResult]:
    """Launch initial scenario 2-1 task containers concurrently.

    The caller still owns case selection and validation. This function is the
    single execution boundary for original raw collection, so multiple agent
    containers can run without interleaving their case metadata in Python code.
    """

    selected_concurrency = concurrency if concurrency is not None else sft_rollout_concurrency()
    logger.info(
        "Launching %d SFT task case(s) with concurrency=%d",
        len(cases),
        selected_concurrency,
    )
    backend = get_task_rollout_backend(config.backend)
    semaphore = asyncio.Semaphore(max(1, int(selected_concurrency)))

    async def _run_one(case: SFTTaskCase, index: int):
        async with semaphore:
            return await backend.run_case(case, config, index=index)

    results = await asyncio.gather(
        *(_run_one(case, index) for index, case in enumerate(cases))
    )
    return [
        SFTTaskRolloutResult(
            case=case,
            command=result.command,
            exit_code=result.exit_code,
            stdout_tail=result.stdout_tail,
            stderr_tail=result.stderr_tail,
        )
        for case, result in zip(cases, results)
    ]


def _cases_from_markdown(path: Path) -> list[SFTTaskCase]:
    cases: list[SFTTaskCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith(("-", "*")):
            continue
        for match in IMAGE_PATTERN.finditer(stripped):
            image = match.group(1).strip()
            if "<" in image or ">" in image:
                continue
            instance_id = _instance_id_from_image(image)
            cases.append(
                _case_from_mapping(
                    {
                        "instance_id": instance_id,
                        "docker_image": image,
                    }
                )
            )
    return cases


def _case_from_mapping(item: dict[str, Any], *, base_dir: Path | None = None) -> SFTTaskCase:
    image = str(item.get("docker_image") or item.get("image") or "").strip()
    local_program_path = _resolve_case_path(
        item.get("local_program_path") or item.get("program_path"),
        base_dir=base_dir,
    )
    if not image and local_program_path:
        image = f"local/python-{str(item.get('instance_id') or 'program').strip() or 'program'}:latest"
    if not image:
        raise ValueError(f"SFT task case missing docker image: {item}")
    instance_id = str(item.get("instance_id") or _instance_id_from_image(image)).strip()
    swe_case = _resolve_swe_bench_case(instance_id) if _needs_swe_bench_lookup(item) else {}
    repo = str(item.get("repo") or swe_case.get("repo") or "").strip()
    base_commit = str(item.get("base_commit") or swe_case.get("base_commit") or "").strip()
    repo_url = str(item.get("repo_url") or swe_case.get("repo_url") or "").strip()
    local_repo_path = _resolve_case_path(item.get("local_repo_path") or item.get("repo_path"), base_dir=base_dir)
    problem_statement = str(item.get("problem_statement") or swe_case.get("problem_statement") or "").strip()
    test_cmd = str(item.get("test_cmd") or swe_case.get("test_cmd") or "").strip()
    fail_to_pass = _coerce_str_list(
        item.get("fail_to_pass") or swe_case.get("FAIL_TO_PASS") or swe_case.get("fail_to_pass")
    )
    pass_to_pass = _coerce_str_list(
        item.get("pass_to_pass") or swe_case.get("PASS_TO_PASS") or swe_case.get("pass_to_pass")
    )
    gold_patch = str(item.get("gold_patch") or swe_case.get("patch") or "").strip()
    task_prompt = (
        str(item.get("task_prompt") or item.get("prompt") or "").strip()
        or build_swe_bench_task_prompt(
            instance_id=instance_id,
            repo=repo,
            base_commit=base_commit,
            problem_statement=problem_statement,
            test_cmd=test_cmd,
            fail_to_pass=fail_to_pass,
            pass_to_pass=pass_to_pass,
        )
    )
    case_keys = {
        "image",
        "docker_image",
        "task_prompt",
        "prompt",
        "repo",
        "base_commit",
        "problem_statement",
        "test_cmd",
        "fail_to_pass",
        "pass_to_pass",
        "gold_patch",
        "repo_url",
        "local_repo_path",
        "local_program_path",
        "program_path",
    }
    metadata: dict[str, Any] = {}
    for key, value in item.items():
        if key not in case_keys:
            metadata[key] = value
    return SFTTaskCase(
        instance_id=instance_id,
        docker_image=image,
        task_prompt=task_prompt,
        repo=repo,
        base_commit=base_commit,
        problem_statement=problem_statement,
        test_cmd=test_cmd,
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
        gold_patch=gold_patch,
        repo_url=repo_url,
        local_repo_path=local_repo_path,
        local_program_path=local_program_path,
        metadata=metadata,
    )


def _resolve_case_path(value: Any, *, base_dir: Path | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path.resolve())
    if base_dir is not None:
        return str((base_dir / path).resolve())
    return str((Path.cwd() / path).resolve())


def _needs_swe_bench_lookup(item: dict[str, Any]) -> bool:
    if str(item.get("task_prompt") or item.get("prompt") or "").strip():
        return False
    return any(
        not str(item.get(key) or "").strip()
        for key in ("repo", "base_commit", "problem_statement")
    )


# Prompt synthesis is kept together so dataset mappings can stay minimal while
# still producing the SWE-bench style instructions used by scenario 2-1.
def build_swe_bench_task_prompt(
    *,
    instance_id: str,
    repo: str = "",
    base_commit: str = "",
    problem_statement: str = "",
    test_cmd: str = "",
    fail_to_pass: list[str] | None = None,
    pass_to_pass: list[str] | None = None,
) -> str:
    """Build the SWE-bench prompt used for raw task collection."""

    fail_to_pass = [item for item in (fail_to_pass or []) if str(item).strip()]
    pass_to_pass = [item for item in (pass_to_pass or []) if str(item).strip()]
    lines: list[str] = [
        f"You are fixing a SWE-bench issue in the {repo or 'target'} repository.",
        f"Instance ID: {instance_id}",
    ]
    if base_commit:
        lines.append(f"Base commit: {base_commit}")
    lines.extend(
        [
            "",
            "Problem Statement",
            problem_statement.strip() or "Investigate the repository and fix the described failure.",
        ]
    )
    if fail_to_pass:
        lines.extend(["", "Failing tests to make pass"])
        lines.extend(f"- {item}" for item in fail_to_pass)
    if pass_to_pass:
        lines.extend(["", "Regression tests that should keep passing"])
        lines.extend(f"- {item}" for item in pass_to_pass)
    if test_cmd:
        lines.extend(["", "Verification Command", "```bash", test_cmd.strip(), "```"])
    lines.extend(
        [
            "",
            "Instructions",
            "1. Inspect the repository and reproduce the bug with the available tools.",
            "2. Make the smallest reasonable patch that fixes the issue.",
            "3. Return only a unified git patch, or NO_PATCH_NEEDED if no code change is required.",
            "",
            "Patch Output Format",
            "```patch",
            "--- a/specific/file/path.py",
            "+++ b/specific/file/path.py",
            "@@ -line_number,num_lines +line_number,num_lines @@",
            " actual code changes here",
            "```",
        ]
    )
    return "\n".join(lines).strip()


@lru_cache(maxsize=1)
def _resolve_swe_bench_verified_lookup() -> dict[str, dict[str, Any]]:
    dataset_name = os.getenv("SFT_SWE_BENCH_DATASET", DEFAULT_SWE_BENCH_DATASET).strip() or DEFAULT_SWE_BENCH_DATASET
    split_name = os.getenv("SFT_SWE_BENCH_SPLIT", DEFAULT_SWE_BENCH_SPLIT).strip() or DEFAULT_SWE_BENCH_SPLIT
    try:
        from datasets import load_dataset
    except Exception as exc:  # pragma: no cover - optional dependency
        logger.warning("SWE-bench dataset loader unavailable: %s", exc)
        return {}
    try:
        dataset = load_dataset(dataset_name, split=split_name)
    except Exception as exc:  # pragma: no cover - network/data dependent
        logger.warning("Failed to load SWE-bench dataset %s[%s]: %s", dataset_name, split_name, exc)
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    for row in dataset:
        if not isinstance(row, dict):
            row = dict(row)
        instance_id = str(row.get("instance_id") or "").strip()
        if instance_id:
            lookup[instance_id] = dict(row)
    logger.info("Loaded SWE-bench dataset lookup dataset=%s split=%s size=%d", dataset_name, split_name, len(lookup))
    return lookup


def _resolve_swe_bench_case(instance_id: str) -> dict[str, Any]:
    if not instance_id:
        return {}
    return _resolve_swe_bench_verified_lookup().get(instance_id, {})


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return []


def _instance_id_from_image(image: str) -> str:
    name = image.rsplit("/", 1)[-1].split(":", 1)[0]
    prefix = "sweb.eval.x86_64."
    if name.startswith(prefix):
        name = name[len(prefix):]
    if "_1776_" in name:
        return name.replace("_1776_", "__", 1)
    return name


def command_for_log(cmd: list[str]) -> str:
    """Return a shell-escaped command string for logs and dry-run output."""

    return shlex.join(cmd)
