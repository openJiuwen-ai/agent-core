# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SWE-bench instance preparation and official evaluation helpers."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

import requests

from openjiuwen.rsi.harness_rsi.evaluator.errors import EvaluationInfrastructureError

DEFAULT_WSL_DISTRO = "Ubuntu-24.04"
DEFAULT_WSL_PYTHON = "python3"
DEFAULT_INFRASTRUCTURE_FAILURE_PATTERNS = (
    "AttributeError: `np.Inf` was removed in the NumPy 2.0 release",
    "numpy.dtype size changed, may indicate binary incompatibility",
    "ImportError: libGL.so.1: cannot open shared object file",
    "ImportError: libXrender.so.1: cannot open shared object file",
)
DEFAULT_TRANSIENT_NETWORK_FAILURE_PATTERNS = (
    "requests.exceptions.ConnectionError",
    "RemoteDisconnected",
    "Connection aborted",
    "Connection reset by peer",
    "Temporary failure in name resolution",
    "Name or service not known",
    "requests.exceptions.ConnectTimeout",
    "requests.exceptions.ReadTimeout",
)
_TEST_OUTPUT_EXCERPT_CHARS = 8000
_TEST_OUTPUT_EXCERPT_HEAD_CHARS = 1500
_DEPENDENCY_CACHE_ROOT = Path.home() / ".cache/openjiuwen/rsi/swebench_dependency_cache"
_OFFICIAL_SUPPORT_DIR = Path(__file__).with_name("_swebench_official_support")


class SWEbenchInfrastructureError(EvaluationInfrastructureError):
    """Raised when the official verifier cannot produce a valid model signal."""


def swebench_image_name(
    instance_id: str,
    *,
    namespace: str = "swebench",
    tag: str = "latest",
) -> str:
    arch = "arm64" if os.environ.get("SWEBENCH_ARCH", "").lower() == "arm64" else "x86_64"
    remote_instance_id = instance_id.lower().replace("__", "_1776_")
    return f"{namespace}/sweb.eval.{arch}.{remote_instance_id}:{tag}"


def prepare_swebench_workspace(
    *,
    case: dict[str, Any],
    workspace_dir: Path,
    timeout_sec: int,
) -> str:
    """Copy a pristine /testbed checkout from the official instance image."""
    config = _config(case)
    instance_id = str(config.get("instance_id") or case.get("case_id") or "").strip()
    if not instance_id:
        raise ValueError("swebench.instance_id is required")
    image = _configured_image(config, instance_id)
    _ensure_configured_image(
        config=config,
        instance_id=instance_id,
        image=image,
        timeout_sec=timeout_sec,
    )

    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    container = f"ach-sweb-prepare-{_safe_id(instance_id)}-{uuid.uuid4().hex[:8]}"
    try:
        _run(
            [
                "docker",
                "create",
                "--name",
                container,
                "--mount",
                f"type=bind,source={workspace_dir},target=/ach-workspace",
                image,
                "sleep",
                "infinity",
            ],
            timeout=120,
        )
        _run(["docker", "start", container], timeout=120)
        symlinks = _detach_container_symlinks(container)
        _run(["docker", "cp", f"{container}:/testbed/.", str(workspace_dir)], timeout=timeout_sec)
        _restore_workspace_symlinks(container, symlinks)
        _configure_workspace_git(workspace_dir)
        _cache_official_dependency_files(config, workspace_dir)
    finally:
        _run(["docker", "rm", "-f", container], check=False, timeout=60)
    return image


def _detach_container_symlinks(container: str) -> list[tuple[str, str]]:
    """Remove image symlinks before Windows ``docker cp`` and return a manifest."""
    script = """\
import json
import os

root = "/testbed"
links = []
for base, dirs, files in os.walk(root, followlinks=False):
    for name in [*dirs, *files]:
        path = os.path.join(base, name)
        if os.path.islink(path):
            links.append([os.path.relpath(path, root), os.readlink(path)])
for relative_path, _ in links:
    os.unlink(os.path.join(root, relative_path))
print(json.dumps(links))
"""
    completed = _run(
        [
            "docker",
            "exec",
            container,
            "/opt/miniconda3/envs/testbed/bin/python",
            "-c",
            script,
        ],
        timeout=120,
    )
    raw = json.loads(completed.stdout or "[]")
    if not isinstance(raw, list):
        raise RuntimeError("invalid SWE-bench workspace symlink manifest")
    links: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, list) or len(item) != 2:
            raise RuntimeError("invalid SWE-bench workspace symlink entry")
        relative_path, target = (str(item[0]), str(item[1]))
        normalized = PurePosixPath(relative_path)
        if normalized.is_absolute() or ".." in normalized.parts:
            raise RuntimeError(f"unsafe SWE-bench workspace symlink path: {relative_path}")
        links.append((normalized.as_posix(), target))
    return links


def _restore_workspace_symlinks(
    container: str,
    symlinks: list[tuple[str, str]],
) -> None:
    """Recreate image symlinks through the Linux bind mount.

    Docker Desktop can create these reparse points from a Linux container even
    when the Windows-side ``docker cp`` implementation lacks symlink privilege.
    """
    for relative_path, target in symlinks:
        _run(
            [
                "docker",
                "exec",
                container,
                "ln",
                "-s",
                "--",
                target,
                f"/ach-workspace/{relative_path}",
            ],
            timeout=60,
        )


def _configure_workspace_git(workspace_dir: Path) -> None:
    """Keep a Docker checkout stable when it is bind-mounted from Windows.

    Docker Desktop exposes host files with executable bits that do not reflect
    the image checkout.  Leaving ``core.filemode`` enabled makes every solver
    edit appear to introduce unrelated mode changes and can train the optimizer
    to compensate for infrastructure noise instead of the task failure.
    """
    for key, value in (
        ("core.autocrlf", "false"),
        ("core.eol", "lf"),
        ("core.safecrlf", "false"),
        ("core.filemode", "false"),
    ):
        _run(
            ["git", "-C", str(workspace_dir), "config", key, value],
            timeout=60,
        )


def collect_model_patch(workspace_dir: Path) -> str:
    result = _run(
        ["git", "-C", str(workspace_dir), "diff", "--binary", "HEAD"],
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        raise SWEbenchInfrastructureError(f"failed to collect SWE-bench patch: {result.stderr}")
    return result.stdout or ""


def run_official_swebench_evaluation(
    *,
    case: dict[str, Any],
    model_patch: str,
    output_dir: Path,
) -> dict[str, Any]:
    """Evaluate one prediction with the official SWE-bench harness."""
    config = _config(case)
    instance_id = str(config.get("instance_id") or case.get("case_id") or "").strip()
    dataset_path = Path(str(config.get("official_dataset_path") or "")).expanduser().resolve()
    if not dataset_path.is_file():
        raise FileNotFoundError(f"SWE-bench official dataset not found: {dataset_path}")

    verifier_dir = output_dir.resolve() / "verifier" / "swebench"
    verifier_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"ach_{_safe_id(instance_id)}_{uuid.uuid4().hex[:8]}"
    model_name = "openjiuwen_single_harness"
    predictions_path = verifier_dir / "predictions.jsonl"
    predictions_path.write_text(
        json.dumps(
            {
                "instance_id": instance_id,
                "model_patch": model_patch,
                "model_name_or_path": model_name,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    report_path = verifier_dir / f"{model_name}.{run_id}.json"
    stdout_path = verifier_dir / "stdout.log"
    stderr_path = verifier_dir / "stderr.log"
    instance_artifact_dir = verifier_dir / "logs" / "run_evaluation" / run_id / model_name / instance_id
    instance_report_path = instance_artifact_dir / "report.json"
    test_output_path = instance_artifact_dir / "test_output.txt"
    if not model_patch.strip():
        report = {
            "total_instances": 1,
            "submitted_instances": 1,
            "completed_instances": 0,
            "resolved_instances": 0,
            "unresolved_instances": 0,
            "error_instances": 0,
            "empty_patch_instances": 1,
            "submitted_ids": [instance_id],
            "completed_ids": [],
            "resolved_ids": [],
            "unresolved_ids": [],
            "error_ids": [],
            "empty_patch_ids": [instance_id],
            "incomplete_ids": [],
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        stdout_path.write_text(
            "Skipped official execution: model patch is empty.\n",
            encoding="utf-8",
        )
        stderr_path.write_text("", encoding="utf-8")
        return {
            "passed": False,
            "score": 0.0,
            "reason": "official SWE-bench evaluation received an empty model patch",
            "report_path": str(report_path),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "predictions_path": str(predictions_path),
            "run_id": run_id,
            "official_attempts": 0,
            "exit_code": 0,
            "report": report,
            "instance_report_path": str(instance_report_path),
            "test_output_path": str(test_output_path),
            "test_output_excerpt": "",
            "instance_report": {},
            "empty_patch": True,
        }

    timeout_sec = int(float(config.get("timeout_sec") or 1800))
    command = _official_command(
        config=config,
        dataset_path=dataset_path,
        predictions_path=predictions_path,
        instance_id=instance_id,
        run_id=run_id,
        timeout_sec=timeout_sec,
        dependency_cache_root=_available_dependency_cache(config),
    )
    completed, official_attempts = _run_official_command_with_retries(
        command=command,
        timeout_sec=timeout_sec,
        verifier_dir=verifier_dir,
        run_id=run_id,
        report_path=report_path,
        test_output_path=test_output_path,
        config=config,
    )
    stdout_path.write_text(completed.stdout or "", encoding="utf-8", errors="replace")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8", errors="replace")

    report = _read_json(report_path)
    instance_report = _read_json(instance_report_path)
    test_output = _read_text(test_output_path)
    infrastructure_reason = _infrastructure_failure_reason(
        config=config,
        instance_id=instance_id,
        completed=completed,
        report=report,
        instance_report=instance_report,
        test_output=test_output,
    )
    if infrastructure_reason:
        failure_detail = _bounded_infrastructure_failure_detail(
            completed=completed,
            test_output=test_output,
        )
        raise SWEbenchInfrastructureError(
            f"SWE-bench verifier infrastructure failed for {instance_id}: "
            f"{infrastructure_reason}; test_output={test_output_path}"
            + (f"; detail={failure_detail}" if failure_detail else "")
        )
    resolved = instance_id in set(report.get("resolved_ids") or [])
    empty_patch = instance_id in set(report.get("empty_patch_ids") or [])
    return {
        "passed": resolved,
        "score": 1.0 if resolved else 0.0,
        "reason": (
            ""
            if resolved
            else "official SWE-bench evaluation received an empty model patch"
            if empty_patch
            else "official SWE-bench evaluation did not resolve the instance"
        ),
        "report_path": str(report_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "predictions_path": str(predictions_path),
        "run_id": run_id,
        "official_attempts": official_attempts,
        "exit_code": completed.returncode,
        "report": report,
        "instance_report_path": str(instance_report_path),
        "test_output_path": str(test_output_path),
        "test_output_excerpt": _bounded_test_output_excerpt(test_output),
        "instance_report": instance_report,
        "empty_patch": empty_patch,
    }


def _run_official_command_with_retries(
    *,
    command: list[str],
    timeout_sec: int,
    verifier_dir: Path,
    run_id: str,
    report_path: Path,
    test_output_path: Path,
    config: dict[str, Any],
) -> tuple[subprocess.CompletedProcess[str], int]:
    """Retry only transient official-harness network setup failures."""
    raw_max_attempts = config.get("infrastructure_retry_attempts")
    max_attempts = int(3 if raw_max_attempts is None else raw_max_attempts)
    if max_attempts < 1 or max_attempts > 5:
        raise ValueError("swebench.infrastructure_retry_attempts must be between 1 and 5")
    raw_retry_delay = config.get("infrastructure_retry_delay_sec")
    retry_delay_sec = float(2 if raw_retry_delay is None else raw_retry_delay)
    if retry_delay_sec < 0 or retry_delay_sec > 60:
        raise ValueError("swebench.infrastructure_retry_delay_sec must be between 0 and 60")

    completed: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            report_path.unlink(missing_ok=True)
            shutil.rmtree(
                verifier_dir / "logs" / "run_evaluation" / run_id,
                ignore_errors=True,
            )
            if retry_delay_sec:
                time.sleep(retry_delay_sec * (attempt - 1))
        completed = _run(
            command,
            check=False,
            timeout=timeout_sec + 600,
            cwd=verifier_dir,
        )
        if completed.returncode == 0:
            return completed, attempt
        diagnostic = "\n".join(
            (
                completed.stdout or "",
                completed.stderr or "",
                _read_text(test_output_path),
            )
        )
        if not _is_transient_network_failure(diagnostic):
            return completed, attempt
    if completed is None:
        raise ValueError("official evaluation requires at least one attempt")
    return completed, max_attempts


def _is_transient_network_failure(value: str) -> bool:
    folded = str(value or "").casefold()
    return any(pattern.casefold() in folded for pattern in DEFAULT_TRANSIENT_NETWORK_FAILURE_PATTERNS)


def _bounded_test_output_excerpt(test_output: str) -> str:
    """Retain authoritative failure details without copying an unbounded log."""
    text = str(test_output or "").strip()
    if len(text) <= _TEST_OUTPUT_EXCERPT_CHARS:
        return text
    tail_chars = _TEST_OUTPUT_EXCERPT_CHARS - _TEST_OUTPUT_EXCERPT_HEAD_CHARS
    omitted = len(text) - _TEST_OUTPUT_EXCERPT_CHARS
    return text[:_TEST_OUTPUT_EXCERPT_HEAD_CHARS] + f"\n... {omitted} characters omitted ...\n" + text[-tail_chars:]


def _bounded_infrastructure_failure_detail(
    *,
    completed: subprocess.CompletedProcess[str],
    test_output: str,
) -> str:
    """Expose the verifier's concrete transport failure to retry classification."""
    combined = "\n".join(
        text.strip() for text in (completed.stdout or "", completed.stderr or "", test_output or "") if text.strip()
    )
    if not combined:
        return ""
    return _bounded_test_output_excerpt(combined)[-2000:]


def _dependency_cache_dir(config: dict[str, Any]) -> Path:
    repo = str(config.get("repo") or "unknown_repo").strip()
    commit = str(config.get("environment_setup_commit") or config.get("base_commit") or "unknown_commit").strip()
    root = Path(os.environ.get("SWEBENCH_DEPENDENCY_CACHE_ROOT", "").strip() or _DEPENDENCY_CACHE_ROOT).expanduser()
    if not root.is_absolute():
        raise ValueError("SWEBENCH_DEPENDENCY_CACHE_ROOT must be an absolute path")
    return root / _safe_id(repo) / _safe_id(commit)


def _download_dependency_manifest(url: str) -> bytes | None:
    """Retry only transient manifest downloads, without changing model routing."""
    proxy = os.environ.get("SWEBENCH_DOWNLOAD_PROXY", "").strip()
    options: dict[str, Any] = {"timeout": (10, 60)}
    if proxy:
        options["proxies"] = {"http": proxy, "https": proxy}
    for attempt in range(3):
        response = None
        try:
            response = requests.get(url, **options)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.content
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 2:
                raise
        except requests.HTTPError:
            if response is None or response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
        finally:
            if response is not None:
                response.close()
        time.sleep(2**attempt)
    return None  # All terminal failures above are re-raised.


def _cache_official_dependency_files(
    config: dict[str, Any],
    workspace_dir: Path,
) -> Path | None:
    """Cache pristine dependency manifests before the solver can edit them."""
    target = _dependency_cache_dir(config)
    marker = target / "cache.json"
    if _read_json(marker).get("version") == 2:
        return target
    repo = str(config.get("repo") or "").strip()
    commit = str(config.get("environment_setup_commit") or config.get("base_commit") or "").strip()
    copied: list[str] = []
    for root, dirs, files in os.walk(workspace_dir):
        dirs[:] = [name for name in dirs if name != ".git"]
        root_path = Path(root)
        for name in files:
            folded = name.casefold()
            if not (
                (
                    folded.endswith((".txt", ".in"))
                    and (
                        "requirement" in folded
                        or any("requirement" in part.casefold() for part in root_path.relative_to(workspace_dir).parts)
                    )
                )
                or folded
                in {
                    "environment.yml",
                    "environment.yaml",
                    "conda.yml",
                    "conda.yaml",
                }
            ):
                continue
            source = root_path / name
            relative = source.relative_to(workspace_dir)
            destination = target / relative
            content = None
            if commit != str(config.get("base_commit") or "").strip():
                # The image checkout can be newer than the official setup revision.
                content = _download_dependency_manifest(
                    f"https://raw.githubusercontent.com/{repo}/{commit}/{relative.as_posix()}",
                )
                if content is None:
                    continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                shutil.copy2(source, destination)
            else:
                destination.write_bytes(content)
            copied.append(relative.as_posix())
    if not copied:
        return None
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "version": 2,
                "repo": str(config.get("repo") or ""),
                "environment_setup_commit": str(
                    config.get("environment_setup_commit") or config.get("base_commit") or ""
                ),
                "files": sorted(copied),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def _available_dependency_cache(config: dict[str, Any]) -> Path | None:
    target = _dependency_cache_dir(config)
    return target if (target / "cache.json").is_file() else None


def _official_command(
    *,
    config: dict[str, Any],
    dataset_path: Path,
    predictions_path: Path,
    instance_id: str,
    run_id: str,
    timeout_sec: int,
    dependency_cache_root: Path | None = None,
) -> list[str]:
    base_args = [
        "-m",
        "swebench.harness.run_evaluation",
        "-d",
        str(dataset_path),
        "-s",
        str(config.get("split") or "dev"),
        "-i",
        instance_id,
        "-p",
        str(predictions_path),
        "--max_workers",
        "1",
        "-t",
        str(timeout_sec),
        "--cache_level",
        str(config.get("cache_level") or "instance"),
        "--clean",
        "false",
        "-id",
        run_id,
    ]
    namespace, instance_image_tag = _image_coordinates(config)
    base_args.extend(["--namespace", namespace, "--instance_image_tag", instance_image_tag])
    if os.name != "nt":
        prefix = _official_python_prefix(
            python_path=str(config.get("python_path") or "python"),
            support_dir=str(_OFFICIAL_SUPPORT_DIR),
            dependency_cache_root=(str(dependency_cache_root) if dependency_cache_root is not None else None),
        )
        return [*prefix, *base_args]

    distro = str(config.get("wsl_distro") or os.environ.get("SWEBENCH_WSL_DISTRO") or DEFAULT_WSL_DISTRO)
    python_path = str(config.get("python_path") or os.environ.get("SWEBENCH_WSL_PYTHON") or DEFAULT_WSL_PYTHON)
    translated = list(base_args)
    for host_path in (dataset_path, predictions_path):
        linux_path = _wsl_path(host_path, distro)
        translated[translated.index(str(host_path))] = linux_path
    prefix = _official_python_prefix(
        python_path=python_path,
        support_dir=_wsl_path(_OFFICIAL_SUPPORT_DIR, distro),
        dependency_cache_root=(_wsl_path(dependency_cache_root, distro) if dependency_cache_root is not None else None),
    )
    return ["wsl", "-d", distro, "--", *prefix, *translated]


def _official_python_prefix(
    *,
    python_path: str,
    support_dir: str,
    dependency_cache_root: str | None,
) -> list[str]:
    prefix = ["env", f"PYTHONPATH={support_dir}"]
    if dependency_cache_root is not None:
        prefix.append(f"ACH_SWEBENCH_DEPENDENCY_CACHE={dependency_cache_root}")
    return [*prefix, python_path]


def _wsl_path(path: Path, distro: str) -> str:
    drive = path.drive.rstrip(":").lower()
    if drive:
        relative = path.resolve().as_posix().split(":", 1)[1].lstrip("/")
        return f"/mnt/{drive}/{relative}"
    result = _run(
        ["wsl", "-d", distro, "--", "wslpath", "-a", str(path.resolve())],
        timeout=30,
    )
    return result.stdout.strip()


def _config(case: dict[str, Any]) -> dict[str, Any]:
    value = case.get("swebench")
    if not isinstance(value, dict):
        raise ValueError("case.swebench must be a mapping")
    return value


def _image_coordinates(config: dict[str, Any]) -> tuple[str, str]:
    namespace = str(config.get("namespace") or "swebench").strip()
    tag = str(config.get("instance_image_tag") or "").strip()
    setup_commands = _setup_commands(config)
    if not tag:
        if setup_commands:
            digest = hashlib.sha256(json.dumps(setup_commands, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
            tag = f"ach-{digest}"
        else:
            tag = "latest"
    if not namespace or not tag:
        raise ValueError("swebench namespace and instance_image_tag must be non-empty")
    return namespace, tag


def _configured_image(config: dict[str, Any], instance_id: str) -> str:
    namespace, tag = _image_coordinates(config)
    expected = swebench_image_name(instance_id, namespace=namespace, tag=tag)
    configured = str(config.get("docker_image") or "").strip()
    if configured and configured != expected:
        raise ValueError(
            "swebench.docker_image must match namespace/instance_image_tag so the solver "
            f"and official verifier use one image: expected {expected}, got {configured}"
        )
    return configured or expected


def _setup_commands(config: dict[str, Any]) -> list[str]:
    value = config.get("verifier_setup_commands")
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("swebench.verifier_setup_commands must be a list of shell commands")
    return [item.strip() for item in value if item.strip()]


def _ensure_configured_image(
    *,
    config: dict[str, Any],
    instance_id: str,
    image: str,
    timeout_sec: int,
) -> None:
    inspect = _run(["docker", "image", "inspect", image], check=False, timeout=60)
    if inspect.returncode == 0:
        return

    setup_commands = _setup_commands(config)
    if not setup_commands:
        _run(["docker", "pull", image], timeout=timeout_sec)
        return

    base_image = str(config.get("base_docker_image") or "").strip()
    if not base_image:
        base_image = swebench_image_name(instance_id)
    base_inspect = _run(
        ["docker", "image", "inspect", base_image],
        check=False,
        timeout=60,
    )
    if base_inspect.returncode != 0:
        _run(["docker", "pull", base_image], timeout=timeout_sec)
    dockerfile = "\n".join([f"FROM {base_image}", *(f"RUN {command}" for command in setup_commands), ""])
    build = _run(
        [
            "docker",
            "build",
            "--pull=false",
            "--provenance=false",
            "-t",
            image,
            "-",
        ],
        check=False,
        timeout=timeout_sec,
        input_text=dockerfile,
    )
    if build.returncode != 0:
        # Docker Desktop can finish naming/unpacking the image and then return
        # non-zero while BuildKit refreshes registry metadata.  Trust only a
        # concrete local image, never the partial build log.
        built_image = _run(
            ["docker", "image", "inspect", image],
            check=False,
            timeout=60,
        )
        if built_image.returncode != 0:
            raise RuntimeError(f"failed to build SWE-bench verifier image {image}:\n{build.stderr}")


def _infrastructure_failure_reason(
    *,
    config: dict[str, Any],
    instance_id: str,
    completed: subprocess.CompletedProcess[str],
    report: dict[str, Any],
    instance_report: dict[str, Any],
    test_output: str,
) -> str:
    if completed.returncode != 0:
        return f"official harness exited with code {completed.returncode}"
    if not report:
        return "official aggregate report is missing"
    if instance_id in set(report.get("error_ids") or []):
        return "official report classified the instance as an error"
    if instance_id in set(report.get("incomplete_ids") or []):
        return "official report classified the instance as incomplete"
    if instance_id in set(report.get("empty_patch_ids") or []):
        # The official harness intentionally skips execution for empty patches,
        # so there is no per-instance report. This is a valid model outcome,
        # not missing verifier infrastructure.
        return ""
    if not instance_report:
        return "official per-instance report is missing"

    raw_patterns = config.get("infrastructure_failure_patterns") or []
    if not isinstance(raw_patterns, list) or not all(isinstance(pattern, str) for pattern in raw_patterns):
        raise ValueError("swebench.infrastructure_failure_patterns must be a list of strings")
    folded_output = test_output.casefold()
    for pattern in [*DEFAULT_INFRASTRUCTURE_FAILURE_PATTERNS, *raw_patterns]:
        normalized = pattern.strip()
        if normalized and normalized.casefold() in folded_output:
            return f"configured infrastructure failure matched: {normalized}"
    return ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with open(_native_path(path), encoding="utf-8") as file:
            value = json.load(file)
    except FileNotFoundError:
        return {}
    return value if isinstance(value, dict) else {}


def _read_text(path: Path) -> str:
    try:
        with open(_native_path(path), encoding="utf-8", errors="replace") as file:
            return file.read()
    except FileNotFoundError:
        return ""


def _native_path(path: Path) -> str:
    """Return an extended Windows path so deep evaluation reports stay readable."""
    resolved = str(path.expanduser().resolve())
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return ntpath.join("\\\\?\\UNC", resolved.lstrip("\\"))
    drive, tail = ntpath.splitdrive(resolved)
    return ntpath.join(f"\\\\?\\{drive}\\", tail.lstrip("\\"))


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")[:48]


def _run(
    command: list[str],
    *,
    check: bool = True,
    timeout: int | float | None = None,
    cwd: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        input=input_text,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            "command failed: " + " ".join(command) + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


__all__ = [
    "SWEbenchInfrastructureError",
    "collect_model_patch",
    "prepare_swebench_workspace",
    "run_official_swebench_evaluation",
    "swebench_image_name",
]
