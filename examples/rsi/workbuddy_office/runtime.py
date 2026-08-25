# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""WorkBuddy Bench Office runtime for single-harness evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time
from typing import Any
import xml.etree.ElementTree as ET

from openjiuwen.core.common.logging import logger
from openjiuwen.rsi.evaluator.errors import EvaluationInfrastructureError


_DOCKER_BUILD_MAX_ATTEMPTS = 3
_TRANSIENT_DOCKER_BUILD_MARKERS = (
    "bad gateway",
    "connection reset",
    "connection timed out",
    "curl: (18)",
    "curl: (28)",
    "curl: (35)",
    "curl: (56)",
    "curl: (92)",
    "http/2 protocol_error",
    "http/2 stream",
    "i/o timeout",
    "network is unreachable",
    "no route to host",
    "service unavailable",
    "temporary failure",
    "tls handshake timeout",
    "too many requests",
    "unexpected end of file",
    "unexpected end of input",
    "unexpected eof",
    "upstream connect error",
)


class WorkBuddyInfrastructureError(EvaluationInfrastructureError):
    """Raised when the WorkBuddy runtime cannot execute or score a task."""


def prepare_workbuddy_office_workspace(
    *,
    case: dict[str, Any],
    workspace_dir: Path,
    timeout_sec: int,
) -> str:
    """Extract a task workspace and return its cached environment image."""
    config, task_dir = resolve_workbuddy_office_case(case)
    archive = task_dir / "environment" / "workspace.tar.gz"
    dockerfile = task_dir / "environment" / "Dockerfile"
    if not archive.is_file():
        raise WorkBuddyInfrastructureError(f"WorkBuddy workspace archive not found: {archive}")
    if not dockerfile.is_file():
        raise WorkBuddyInfrastructureError(f"WorkBuddy Dockerfile not found: {dockerfile}")

    try:
        if workspace_dir.exists():
            shutil.rmtree(workspace_dir)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        _extract_workspace_archive(archive, workspace_dir)
    except (OSError, tarfile.TarError, ValueError) as exc:
        shutil.rmtree(workspace_dir, ignore_errors=True)
        raise WorkBuddyInfrastructureError(f"failed to extract WorkBuddy workspace archive {archive}: {exc}") from exc

    configured_image = str(config.get("docker_image", "") or "").strip()
    if configured_image:
        return configured_image
    image = workbuddy_office_image_name(task_dir)
    try:
        inspected = _run(
            ["docker", "image", "inspect", image],
            check=False,
            timeout=min(timeout_sec, 60),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkBuddyInfrastructureError(f"failed to inspect WorkBuddy Docker image {image}: {exc}") from exc
    if inspected.returncode == 0:
        return image
    _build_workbuddy_office_image(
        image=image,
        dockerfile=dockerfile,
        timeout_sec=timeout_sec,
    )
    return image


def run_workbuddy_office_verifier(
    *,
    case: dict[str, Any],
    container_name: str,
    output_dir: Path,
) -> dict[str, Any]:
    """Run the official WorkBuddy CompositeVerifier in the solver container."""
    config, task_dir = resolve_workbuddy_office_case(case)
    tests_dir = task_dir / "tests"
    if not (tests_dir / "verifier.toml").is_file():
        raise FileNotFoundError(f"WorkBuddy verifier contract not found: {tests_dir}")
    timeout_sec = int(float(config.get("verifier_timeout_sec") or config.get("timeout_sec") or 1800))

    verifier_dir = output_dir.expanduser().resolve() / "verifier"
    if verifier_dir.exists():
        shutil.rmtree(verifier_dir)
    verifier_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "docker",
            "exec",
            container_name,
            "bash",
            "-lc",
            "rm -rf /tests /logs/verifier && mkdir -p /tests /logs/verifier",
        ],
        timeout=min(timeout_sec, 120),
    )
    _run(
        ["docker", "cp", _docker_copy_contents_source(tests_dir), f"{container_name}:/tests"],
        timeout=min(timeout_sec, 300),
    )

    bridge_path = Path(__file__).resolve().parent / "_official_support" / "run_office_verifier.py"
    if not bridge_path.is_file():
        raise WorkBuddyInfrastructureError(f"WorkBuddy official verifier bridge not found: {bridge_path}")
    verifier_command = [
        *_workbuddy_python_command(config, task_dir),
        str(bridge_path),
        "--task-dir",
        str(task_dir),
        "--container-name",
        container_name,
        "--output-dir",
        str(verifier_dir),
    ]
    completed = _run(
        verifier_command,
        check=False,
        timeout=timeout_sec + 300,
    )
    if completed.returncode != 0:
        detail = _excerpt(
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
            8000,
        )
        raise WorkBuddyInfrastructureError(
            f"WorkBuddy CompositeVerifier failed for {task_dir.name} with exit code {completed.returncode}: {detail}"
        )

    reward_payload = _read_json_object(verifier_dir / "reward.json")
    score_payload = _read_json_object(verifier_dir / "score.json")
    score = _official_reward(reward_payload, score_payload)
    status = str(score_payload.get("test_status") or "").strip()
    if status in {"build_error", "judge_error"}:
        raise WorkBuddyInfrastructureError(f"WorkBuddy CompositeVerifier returned {status} for {task_dir.name}")

    results_path = verifier_dir / "results.xml"
    atomic_checks = _parse_junit_checks(results_path)
    scored_checks = [check for check in atomic_checks if check.get("status") != "skipped"]
    passed_count = _nonnegative_int(
        reward_payload.get("tests_passed"),
        default=sum(1 for check in scored_checks if check["passed"]),
    )
    total_count = _nonnegative_int(
        reward_payload.get("tests_total"),
        default=len(scored_checks),
    )
    if not status:
        status = "full_pass" if score >= 1.0 else "partial_pass" if score > 0.0 else "no_pass"
    failed_checks = [dict(check) for check in scored_checks if not check["passed"]]
    score_payload = {
        **score_payload,
        "test_status": status,
        "test_pass_rate": float(reward_payload.get("test_pass_rate", score) or score),
        "tests_passed": passed_count,
        "tests_total": total_count,
        "failed_checks": failed_checks,
        "atomic_checks": atomic_checks,
        "official_reward": reward_payload,
        "official_bridge_stdout": _excerpt(completed.stdout, 4000),
        "official_bridge_stderr": _excerpt(completed.stderr, 4000),
    }
    return {
        **score_payload,
        "score": score,
        "passed": score >= float(config.get("success_score") or 1.0),
        "task_id": str(config.get("task_id") or task_dir.name),
        "task_dir": str(task_dir),
        "verifier_dir": str(verifier_dir),
        "results_xml_path": str(results_path),
        "test_output_path": str(verifier_dir / "test_output.txt"),
    }


def _workbuddy_python_command(config: dict[str, Any], task_dir: Path) -> list[str]:
    configured = str(config.get("python_executable", "") or "").strip()
    if configured:
        executable = Path(configured).expanduser().resolve()
        if not executable.is_file():
            raise WorkBuddyInfrastructureError(f"configured WorkBuddy Python not found: {executable}")
        return [str(executable)]

    repo_root = _find_workbuddy_repo_root(task_dir)
    candidates = [
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "bin" / "python",
    ]
    for executable in candidates:
        if executable.is_file():
            return [str(executable)]
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "--project", str(repo_root), "python"]
    raise WorkBuddyInfrastructureError(
        "WorkBuddy Bench Python environment not found; create its .venv or set workbuddy_office.python_executable"
    )


def _find_workbuddy_repo_root(task_dir: Path) -> Path:
    for candidate in (task_dir, *task_dir.parents):
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file() and (candidate / "src" / "workbuddy_bench").is_dir():
            return candidate
    raise WorkBuddyInfrastructureError(f"could not find the WorkBuddy Bench repository for task: {task_dir}")


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkBuddyInfrastructureError(f"WorkBuddy verifier did not produce valid {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkBuddyInfrastructureError(f"WorkBuddy verifier output must be an object: {path}")
    return value


def _official_reward(
    reward_payload: dict[str, Any],
    score_payload: dict[str, Any],
) -> float:
    for payload, keys in (
        (reward_payload, ("reward", "overall", "test_pass_rate")),
        (score_payload, ("reward", "overall", "test_pass_rate")),
    ):
        for key in keys:
            value = payload.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                return max(0.0, min(float(value), 1.0))
            except (TypeError, ValueError):
                continue
    raise WorkBuddyInfrastructureError("WorkBuddy verifier output contains no numeric reward")


def _nonnegative_int(value: Any, *, default: int) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def resolve_workbuddy_office_case(
    case: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    config = case.get("workbuddy_office")
    if not isinstance(config, dict):
        raise WorkBuddyInfrastructureError("case.workbuddy_office must be a mapping")
    task_dir_value = str(config.get("task_dir", "") or "").strip()
    if not task_dir_value:
        raise WorkBuddyInfrastructureError("workbuddy_office.task_dir is required")
    task_dir = Path(task_dir_value).expanduser().resolve()
    if not task_dir.is_dir():
        raise WorkBuddyInfrastructureError(f"WorkBuddy task dir not found: {task_dir}")
    return config, task_dir


def workbuddy_office_image_name(task_dir: Path) -> str:
    environment_dir = task_dir / "environment"
    digest = hashlib.sha256()
    for path in sorted(item for item in environment_dir.rglob("*") if item.is_file()):
        digest.update(path.relative_to(environment_dir).as_posix().encode("utf-8"))
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    safe_task_id = re.sub(r"[^a-z0-9]+", "-", task_dir.name.lower()).strip("-")
    safe_task_id = safe_task_id[:36].rstrip("-") or "task"
    return f"ach-workbuddy-office-{safe_task_id}:{digest.hexdigest()[:12]}"


def _build_workbuddy_office_image(
    *,
    image: str,
    dockerfile: Path,
    timeout_sec: int,
) -> None:
    command = [
        "docker",
        "build",
        "--tag",
        image,
        "--file",
        str(dockerfile),
        str(dockerfile.parent),
    ]
    for attempt in range(1, _DOCKER_BUILD_MAX_ATTEMPTS + 1):
        try:
            completed = _run(command, check=False, timeout=timeout_sec)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkBuddyInfrastructureError(f"failed to run Docker build for {image}: {exc}") from exc
        if completed.returncode == 0:
            return

        detail = _excerpt(
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
            8000,
        )
        can_retry = attempt < _DOCKER_BUILD_MAX_ATTEMPTS and _is_transient_docker_build_failure(completed)
        if not can_retry:
            raise WorkBuddyInfrastructureError(
                f"WorkBuddy Docker image build failed for {image} after {attempt} attempt(s): {detail}"
            )
        logger.warning(
            "WorkBuddy Docker build for {} hit a transient network error; retrying ({}/{})",
            image,
            attempt + 1,
            _DOCKER_BUILD_MAX_ATTEMPTS,
        )
        time.sleep(2 ** (attempt - 1))


def _is_transient_docker_build_failure(
    completed: subprocess.CompletedProcess[str],
) -> bool:
    output = f"{completed.stdout}\n{completed.stderr}".lower()
    return any(marker in output for marker in _TRANSIENT_DOCKER_BUILD_MARKERS)


def _extract_workspace_archive(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise ValueError(f"unsafe path in WorkBuddy workspace archive: {member.name}") from exc
            if member.issym() or member.islnk():
                raise ValueError(f"links are not allowed in WorkBuddy workspace archive: {member.name}")
        tar.extractall(destination)
        for member in members:
            if not member.isfile():
                continue
            extracted = destination / member.name
            if not extracted.is_file() or extracted.stat().st_size != member.size:
                raise OSError(f"WorkBuddy workspace archive was only partially extracted: {member.name}")


def _parse_junit_checks(path: Path) -> list[dict[str, Any]]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, FileNotFoundError, OSError):
        return []
    checks: list[dict[str, Any]] = []
    for test_case in root.iter("testcase"):
        failure = test_case.find("failure")
        error = test_case.find("error")
        skipped = test_case.find("skipped")
        detail_node = failure if failure is not None else error if error is not None else skipped
        detail = ""
        if detail_node is not None:
            detail = str(detail_node.get("message") or detail_node.text or "").strip()
        checks.append(
            {
                "name": str(test_case.get("name", "") or "unnamed_check"),
                "classname": str(test_case.get("classname", "") or ""),
                "passed": failure is None and error is None and skipped is None,
                "status": (
                    "failed"
                    if failure is not None
                    else "error"
                    if error is not None
                    else "skipped"
                    if skipped is not None
                    else "passed"
                ),
                "detail": _excerpt(detail, 2000),
            }
        )
    return checks


def _docker_copy_contents_source(path: Path) -> str:
    return str(path.resolve()) + "/."


def _excerpt(value: str, limit: int) -> str:
    value = str(value or "")
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _run(
    command: list[str],
    *,
    check: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            "command failed: " + " ".join(command) + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


__all__ = [
    "WorkBuddyInfrastructureError",
    "prepare_workbuddy_office_workspace",
    "resolve_workbuddy_office_case",
    "run_workbuddy_office_verifier",
    "workbuddy_office_image_name",
]
