"""Run Evo-Bench Office RSI with the latest openJiuwen DeepAgent.

This launcher keeps benchmark-specific compatibility code outside the RSI
package while fixing three environment mismatches:

* APEX grading is multimodal. It uses a dedicated public Qwen3.7-Plus endpoint
  reachable from E2B instead of reusing the text-only DeepSeek task model.
* Evo-Bench's PolicyHarness protocol is adapted to the latest openJiuwen
  DeepAgent; task-loop, context compression, and anomaly rails execute inside
  the versioned APEX and general-policy sandboxes.
* Windows extended-length paths are normalized before Evo-Bench workspace
  containment checks, without weakening the task-workspace boundary.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import shlex
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
from typing import Any, Mapping
import urllib.error
import urllib.request
import uuid
import zlib

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = Path(os.environ.get("RSI_WORKSPACE_ROOT", REPO_ROOT)).expanduser().resolve()
RUNTIME_ROOT = (
    Path(os.environ.get("RSI_RUNTIME_ROOT", WORKSPACE_ROOT / ".local" / "rsi_runtime")).expanduser().resolve()
)
SHORT_SCRATCH_ROOT = (
    Path(os.environ.get("RSI_SHORT_SCRATCH_ROOT", Path(tempfile.gettempdir()) / "openjiuwen-rsi"))
    .expanduser()
    .resolve()
)
DEFAULT_POLICY_CONFIG = (
    Path(
        os.environ.get(
            "RSI_RUN_MODEL_CONFIG",
            REPO_ROOT / ".local" / "rsi" / "models" / "token_plan_deepseek_v4_flash_single_harness.yaml",
        )
    )
    .expanduser()
    .resolve()
)
DEFAULT_ENV_FILE = (
    Path(os.environ.get("RSI_EVOBENCH_ENV_FILE", REPO_ROOT / ".local" / "rsi" / "evobench.env")).expanduser().resolve()
)
DEFAULT_JUDGE_CONFIG = RUNTIME_ROOT / "model_configs/qwen37_plus_judge.yaml"
DEFAULT_JUDGE_MODEL = "qwen3.7-plus"
DEFAULT_JUDGE_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_JUDGE_API_BASE_ENV = "QWEN37_PLUS_API_BASE"
DEFAULT_JUDGE_API_KEY_ENV = "QWEN37_PLUS_API_KEY"
DEFAULT_SMOKE_TASK = "apex-2299b89dcaf64a4da4f3d03f8aac7215"
DEFAULT_DEEPAGENT_TEMPLATE = os.environ.get(
    "EVOBENCH_DEEPAGENT_TEMPLATE",
    "evobench-apex-openjiuwen",
)
_IN_PROCESS_RUN_LOCK = threading.Lock()
_INFRA_FAILURE_PREFIXES = (
    "judge_error:",
    "hle_judge_error:",
    "rubric_judge_error:",
    "pairwise_judge_error:",
    "claw_grader_error:",
    "apex_grader_error:",
    "eval_pipeline_error:",
)
_ANALYSIS_EVIDENCE_SUFFIXES = (
    ".csv",
    ".docx",
    ".html",
    ".htm",
    ".json",
    ".md",
    ".pdf",
    ".pptx",
    ".tsv",
    ".txt",
    ".xlsm",
    ".xlsx",
    ".xml",
)


def _task_infra_failure(task: Mapping[str, Any]) -> str:
    reason = str(task.get("score_reason") or "")
    exit_reason = str(task.get("exit_reason") or "")
    runtime_errors = task.get("runtime_errors")
    runtime_error = (
        next(
            (str(item) for item in runtime_errors if str(item)),
            "",
        )
        if isinstance(runtime_errors, list)
        else ""
    )
    if exit_reason == "eval_pipeline_error" and "outside allowed root" in reason and "\\\\?\\" in reason:
        return ""
    if exit_reason in {"policy_worker_error", "eval_pipeline_error", "deepagent_error"}:
        return runtime_error or reason or exit_reason
    if runtime_error:
        return runtime_error
    if reason.startswith(_INFRA_FAILURE_PREFIXES):
        return reason
    return ""


def _without_windows_extended_prefix(value: str | Path) -> Path:
    raw = str(value)
    if raw.startswith("\\\\?\\UNC\\"):
        raw = "\\\\" + raw[8:]
    elif raw.startswith("\\\\?\\"):
        raw = raw[4:]
    return Path(raw)


def _ensure_child_path_windows_safe(base: str | Path, candidate: str | Path) -> Path:
    """Validate containment after normalizing a Windows extended-path prefix."""
    base_resolved = _without_windows_extended_prefix(Path(base).resolve())
    candidate_resolved = _without_windows_extended_prefix(Path(candidate).resolve())
    try:
        candidate_resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError(f"Path {candidate_resolved} is outside allowed root {base_resolved}") from exc
    return candidate_resolved


def _remote_evidence_stage_command(sandbox_dir: str) -> str:
    """Build a bounded, task-workspace-only snapshot command for APEX."""
    script = f"""\
from pathlib import Path
import shutil

source = Path('/filesystem')
destination = Path({json.dumps(sandbox_dir)}) / 'evidence_workspace'
allowed = {set(_ANALYSIS_EVIDENCE_SUFFIXES)!r}
max_files = 200
max_file_bytes = 50 * 1024 * 1024
max_total_bytes = 250 * 1024 * 1024
count = 0
total = 0
if source.is_dir():
    root = source.resolve()
    for candidate in sorted(source.rglob('*')):
        try:
            path = candidate.resolve()
            if not path.is_file() or not path.is_relative_to(root):
                continue
            if path.suffix.lower() not in allowed:
                continue
            size = path.stat().st_size
            if count >= max_files or size > max_file_bytes or total + size > max_total_bytes:
                continue
            relative = path.relative_to(root)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            count += 1
            total += size
        except OSError:
            continue
print(f'RSI_EVIDENCE_STAGED files={{count}} bytes={{total}}')
"""
    return f"python3 -c {shlex.quote(script)}"


def _argument_value(arguments: list[str], flag: str) -> str:
    try:
        return arguments[arguments.index(flag) + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"missing required Evo-Bench argument: {flag}") from exc


def _replace_argument(arguments: list[str], flag: str, value: str) -> list[str]:
    updated = list(arguments)
    try:
        updated[updated.index(flag) + 1] = value
    except (ValueError, IndexError) as exc:
        raise ValueError(f"missing required Evo-Bench argument: {flag}") from exc
    return updated


def _rebase_retry_paths(value: Any, *, source: Path, destination: Path) -> Any:
    if isinstance(value, str):
        return value.replace(str(source), str(destination))
    if isinstance(value, list):
        return [_rebase_retry_paths(item, source=source, destination=destination) for item in value]
    if isinstance(value, dict):
        return {key: _rebase_retry_paths(item, source=source, destination=destination) for key, item in value.items()}
    return value


def _merge_retry_result(
    *,
    output_dir: Path,
    retry_output_dir: Path,
    retry_result: Mapping[str, Any],
) -> set[str]:
    result_path = output_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    original_tasks = result.get("tasks")
    retry_tasks = retry_result.get("tasks")
    if not isinstance(original_tasks, list) or not isinstance(retry_tasks, list):
        raise ValueError("official Evo-Bench retry result has no task list")

    retry_by_id = {
        str(task.get("task_id") or ""): task
        for task in retry_tasks
        if isinstance(task, dict) and str(task.get("task_id") or "")
    }
    replaced: set[str] = set()
    merged_tasks: list[Any] = []
    for task in original_tasks:
        task_id = str(task.get("task_id") or "") if isinstance(task, dict) else ""
        retry_task = retry_by_id.get(task_id)
        if retry_task is None:
            merged_tasks.append(task)
            continue
        retry_rollout = retry_output_dir / "rollouts" / task_id
        output_rollout = output_dir / "rollouts" / task_id
        if retry_rollout.is_dir():
            shutil.copytree(retry_rollout, output_rollout, dirs_exist_ok=True)
        merged_tasks.append(_rebase_retry_paths(retry_task, source=retry_output_dir, destination=output_dir))
        replaced.add(task_id)

    result["tasks"] = merged_tasks
    retry_history = result.setdefault("rsi_infra_retry_history", [])
    if isinstance(retry_history, list):
        retry_history.append(
            {
                "retry_output_dir": str(retry_output_dir),
                "replaced_task_ids": sorted(replaced),
            }
        )
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return replaced


def _retry_infrastructure_failures(cli: Any, arguments: list[str]) -> None:
    max_retries = max(0, int(os.environ.get("RSI_EVOBENCH_INFRA_RETRIES", "2")))
    if max_retries == 0:
        return
    output_dir = Path(_argument_value(arguments, "--output-dir")).resolve()
    suite_path = Path(_argument_value(arguments, "--suite")).resolve()
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    validation_tasks = suite.get("validation")
    if not isinstance(validation_tasks, list):
        raise ValueError("official Evo-Bench suite has no validation task list")

    for attempt in range(1, max_retries + 1):
        result_path = output_dir / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        tasks = result.get("tasks")
        if not isinstance(tasks, list):
            raise ValueError("official Evo-Bench result has no task list")
        failed_ids = {
            str(task.get("task_id") or "") for task in tasks if isinstance(task, dict) and _task_infra_failure(task)
        }
        failed_ids.discard("")
        if not failed_ids:
            return

        retry_tasks = [
            task
            for task in validation_tasks
            if isinstance(task, dict) and str(task.get("id") or task.get("task_id") or "") in failed_ids
        ]
        if not retry_tasks:
            raise ValueError(f"failed Evo-Bench tasks are absent from suite: {sorted(failed_ids)}")

        retry_output_dir = output_dir.parent / f"retry_{attempt}_{uuid.uuid4().hex[:6]}"
        retry_suite_path = retry_output_dir.parent / f"retry_suite_{attempt}_{uuid.uuid4().hex[:6]}.json"
        retry_suite = dict(suite)
        retry_suite["validation"] = retry_tasks
        retry_suite_path.write_text(json.dumps(retry_suite, ensure_ascii=False, indent=2), encoding="utf-8")
        retry_arguments = _replace_argument(arguments, "--suite", str(retry_suite_path))
        retry_arguments = _replace_argument(retry_arguments, "--output-dir", str(retry_output_dir))
        print(f"RSI_INFRA_RETRY attempt={attempt}/{max_retries} tasks={','.join(sorted(failed_ids))}")
        sys.argv = ["evobench", *retry_arguments[3:]]
        retry_code = 0
        try:
            cli.main()
        except SystemExit as exc:
            retry_code = int(exc.code or 0)
        if retry_code:
            print(f"RSI_INFRA_RETRY_FAILED attempt={attempt} exit_code={retry_code}")
            continue
        retry_result_path = retry_output_dir / "result.json"
        if not retry_result_path.is_file():
            print(f"RSI_INFRA_RETRY_FAILED attempt={attempt} reason=result_missing")
            continue
        retry_result = json.loads(retry_result_path.read_text(encoding="utf-8"))
        replaced = _merge_retry_result(
            output_dir=output_dir,
            retry_output_dir=retry_output_dir,
            retry_result=retry_result,
        )
        print(f"RSI_INFRA_RETRY_MERGED attempt={attempt} tasks={','.join(sorted(replaced))}")


def _bootstrap_imports() -> tuple[Any, Any, Any, Any]:
    sys.path.insert(0, str(REPO_ROOT))
    from examples.rsi import run_evobench_single_harness as runner
    from examples.rsi.evobench import rsi_evaluator
    from examples.rsi.evobench.rsi_evaluator import (
        EvoBenchRSIEvaluator,
        EvoBenchRSIEvaluatorConfig,
    )

    return runner, rsi_evaluator, EvoBenchRSIEvaluator, EvoBenchRSIEvaluatorConfig


def _configure_environment() -> None:
    runtime_temp = SHORT_SCRATCH_ROOT / "tmp"
    runtime_temp.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = str(runtime_temp)
    os.environ["TMP"] = str(runtime_temp)
    os.environ.setdefault("EVOBENCH_E2B_REQUEST_TIMEOUT_SECONDS", "600")
    os.environ["EVOBENCH_APEX_SANDBOX_TTL_MINUTES"] = "60"
    os.environ["EVOBENCH_POLICY_SANDBOX_TTL_MINUTES"] = "60"
    os.environ.setdefault("EVOBENCH_APEX_ROLLOUT_TIMEOUT_SECONDS", "3600")
    os.environ.setdefault("EVOBENCH_APEX_GRADING_TIMEOUT_SECONDS", "1800")
    os.environ.setdefault("EVOBENCH_APEX_CREATE_CONCURRENCY", "2")
    os.environ.setdefault("EVOBENCH_E2B_TEMPLATE", DEFAULT_DEEPAGENT_TEMPLATE)
    os.environ.setdefault("EVOBENCH_E2B_APEX_TEMPLATE", DEFAULT_DEEPAGENT_TEMPLATE)
    os.environ["RSI_EVOBENCH_SCRATCH_ROOT"] = str(SHORT_SCRATCH_ROOT)
    _materialize_public_judge_config()


def _read_local_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _materialize_public_judge_config() -> Path:
    """Create the Jiuwen model config without committing judge credentials."""
    local_env = _read_local_env(DEFAULT_ENV_FILE)
    api_base = (
        os.environ.get(DEFAULT_JUDGE_API_BASE_ENV)
        or local_env.get(DEFAULT_JUDGE_API_BASE_ENV)
        or DEFAULT_JUDGE_API_BASE
    ).rstrip("/")
    api_key = os.environ.get(DEFAULT_JUDGE_API_KEY_ENV) or local_env.get(DEFAULT_JUDGE_API_KEY_ENV, "")
    if not api_key:
        raise ValueError(
            f"{DEFAULT_JUDGE_API_KEY_ENV} is missing from the environment or {DEFAULT_ENV_FILE}; "
            "APEX image grading cannot use the text-only policy model"
        )
    payload = {
        "model_client_config": {
            "client_provider": "OpenAI",
            "api_base": api_base,
            "api_key": api_key,
            "timeout": 1200,
            "max_retries": 2,
            "verify_ssl": True,
        },
        "model_request_config": {
            "model": DEFAULT_JUDGE_MODEL,
            "temperature": 0.0,
            "max_tokens": 65536,
        },
    }
    DEFAULT_JUDGE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_JUDGE_CONFIG.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return DEFAULT_JUDGE_CONFIG


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)


def _multimodal_probe_data_url() -> str:
    width = height = 16
    scanlines = b"".join(b"\x00" + (b"\xff\xff\xff" * width) for _ in range(height))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(scanlines))
        + _png_chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _validate_multimodal_judge() -> None:
    """Fail fast when the configured APEX judge cannot consume image_url."""
    payload = yaml.safe_load(DEFAULT_JUDGE_CONFIG.read_text(encoding="utf-8")) or {}
    client = payload.get("model_client_config", {})
    if not isinstance(client, dict):
        raise ValueError(f"invalid judge model config: {DEFAULT_JUDGE_CONFIG}")
    api_base = str(client.get("api_base") or "").rstrip("/")
    api_key = str(client.get("api_key") or "")
    if not api_base or not api_key:
        raise ValueError(f"judge model config has no api_base/api_key: {DEFAULT_JUDGE_CONFIG}")
    body = json.dumps(
        {
            "model": DEFAULT_JUDGE_MODEL,
            "temperature": 0,
            "max_tokens": 16,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Reply only OK."},
                        {"type": "image_url", "image_url": {"url": _multimodal_probe_data_url()}},
                    ],
                }
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{api_base}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - configured endpoint
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(
            f"APEX judge preflight rejected image_url: model={DEFAULT_JUDGE_MODEL}, status={exc.code}, detail={detail}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"APEX judge preflight failed: model={DEFAULT_JUDGE_MODEL}, endpoint={api_base}") from exc
    choices = result.get("choices") if isinstance(result, dict) else None
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"APEX judge preflight returned no completion: model={DEFAULT_JUDGE_MODEL}")
    print(f"RSI_JUDGE_PREFLIGHT model={DEFAULT_JUDGE_MODEL} multimodal=true")


def _runtime_seed() -> Path:
    source = WORKSPACE_ROOT / "scripts" / "rsi" / "evobench_deepagent_harness"
    destination = RUNTIME_ROOT / "policy_harness_openjiuwen_deepagent"
    if not source.is_dir():
        raise FileNotFoundError(f"openJiuwen Evo-Bench harness is missing: {source}")
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    manifest_path = destination / "harness.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["engine_revision"] = _source_revision()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def _source_revision() -> str:
    configured = os.environ.get("OPENJIUWEN_SOURCE_REVISION", "").strip()
    if configured:
        return configured
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=WORKSPACE_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _runtime_framework(evobench_root: Path) -> Path:
    source = evobench_root / "evobench"
    destination = RUNTIME_ROOT / "staged_framework" / "evobench"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"),
    )
    return destination


def _write_seed_refs(output_path: Path) -> Path:
    harness = _runtime_seed().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "harness_refs": {"policy_harness": str(harness)},
                "roles": [
                    {
                        "role": "policy_harness",
                        "member_name": "policy_harness",
                        "description": "Evo-Bench protocol adapter backed by openJiuwen DeepAgent.",
                        "harness_ref_path": str(harness),
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return output_path


def _install_runtime_patches(
    runner: Any,
    rsi_evaluator: Any,
    *,
    evobench_root: Path | None = None,
) -> None:
    evobench_root = (evobench_root or REPO_ROOT).expanduser().resolve()
    original_config = runner.EvoBenchRSIEvaluatorConfig
    original_validate = rsi_evaluator._validate_task_result

    def evaluator_config(**kwargs: Any) -> Any:
        # Task execution and grading are different roles. APEX grading includes
        # rendered images, so it must use the dedicated multimodal judge.
        kwargs["judge_model_config"] = str(DEFAULT_JUDGE_CONFIG)
        kwargs["judge_model"] = DEFAULT_JUDGE_MODEL
        return original_config(**kwargs)

    def write_seed_refs(_: Path, output_path: Path) -> Path:
        return _write_seed_refs(output_path)

    def validate_task_result(task: Mapping[str, Any], *, official_eval_dir: Path) -> None:
        reason = str(task.get("score_reason") or "")
        exit_reason = str(task.get("exit_reason") or "")
        infrastructure_failure = _task_infra_failure(task)
        if infrastructure_failure:
            if not isinstance(task, dict):
                raise TypeError("Evo-Bench infrastructure skip requires a mutable task result")
            task[rsi_evaluator.INFRASTRUCTURE_SKIP_KEY] = {
                "reason": infrastructure_failure,
                "category": "grader_or_runtime_infrastructure",
                "excluded_from_metrics": True,
            }
            print(f"RSI_INFRA_SKIP task={task.get('task_id', '')} reason={infrastructure_failure[:240]}")
            return
        if reason:
            task = dict(task)
            task["score_reason"] = reason
        if exit_reason:
            task = dict(task)
            task["exit_reason"] = exit_reason
        normalized = dict(task)
        normalized["runtime_errors"] = []
        original_validate(normalized, official_eval_dir=official_eval_dir)

    def run_host_command(
        command: list[str], cwd: Path, environment: Mapping[str, str]
    ) -> subprocess.CompletedProcess[Any]:
        # The managed Windows sandbox can deny a child process access to a
        # freshly-created TEMP subtree. Running the official CLI in the worker
        # thread keeps identical CLI semantics without that Windows ACL split.
        with _IN_PROCESS_RUN_LOCK:
            official_site_packages = evobench_root / ".venv/Lib/site-packages"
            if official_site_packages.is_dir():
                sys.path.insert(0, str(official_site_packages))
            sys.path.insert(0, str(evobench_root))
            from evobench import cli
            from evobench.common import fs as common_fs
            from evobench.evaluation import tasks as evaluation_tasks
            from evobench.execution import e2b as e2b_runtime
            from evobench.policy import apex_sandbox

            # pathlib may add ``\\?\`` only after the child path crosses the
            # Windows length threshold. Normalize both sides before the official
            # containment check so a real child is not rejected as an escape.
            common_fs.ensure_child_path = _ensure_child_path_windows_safe
            evaluation_tasks.ensure_child_path = _ensure_child_path_windows_safe
            apex_sandbox._STAGED_FRAMEWORK["framework"] = _runtime_framework(evobench_root)
            if not getattr(e2b_runtime, "_rsi_temp_patch_installed", False):

                def direct_download_tree(name: str, sandbox_dir: str, local_dir: str | Path) -> None:
                    destination = Path(local_dir)
                    destination.mkdir(parents=True, exist_ok=True)
                    sandbox_path = PurePosixPath(sandbox_dir)
                    try:
                        staged = e2b_runtime.exec_sync(
                            name,
                            _remote_evidence_stage_command(sandbox_dir),
                            timeout_seconds=300,
                            retries=1,
                        )
                        e2b_runtime._require_exec_success(staged, "stage bounded task evidence workspace")
                    except e2b_runtime.E2bError as exc:
                        # Scoring remains authoritative even when optional
                        # analysis evidence cannot be downloaded. The Analyzer
                        # will see explicit not_available rather than fabricated
                        # artifact evidence.
                        print(f"RSI_EVIDENCE_STAGE_UNAVAILABLE sandbox={name} reason={exc}")
                    remote_archive = f"/tmp/_download_{uuid.uuid4().hex}.tar.gz"
                    pack = e2b_runtime.exec_sync(
                        name,
                        f"tar -czf {shlex.quote(remote_archive)} "
                        f"-C {shlex.quote(str(sandbox_path.parent))} "
                        f"{shlex.quote(sandbox_path.name)}",
                        timeout_seconds=900,
                        retries=3,
                    )
                    e2b_runtime._require_exec_success(pack, f"pack sandbox directory {sandbox_dir}")
                    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as stream:
                        archive = Path(stream.name)
                    try:
                        e2b_runtime.download_file(name, remote_archive, archive)
                        with tarfile.open(archive, "r:gz") as tar:
                            members = list(
                                e2b_runtime._safe_members_without_root(
                                    tar,
                                    destination,
                                    expected_root=sandbox_path.name,
                                )
                            )
                            for member in members:
                                tar.extract(member, destination, set_attrs=False)
                    finally:
                        archive.unlink(missing_ok=True)
                        try:
                            e2b_runtime.exec_sync(
                                name,
                                f"rm -f {shlex.quote(remote_archive)}",
                                timeout_seconds=30,
                                retries=3,
                            )
                        except e2b_runtime.E2bError:
                            pass

                e2b_runtime.download_tree = direct_download_tree
                e2b_runtime._rsi_temp_patch_installed = True
            if not getattr(apex_sandbox, "_rsi_grader_patch_installed", False):
                original_launch_detached = apex_sandbox._launch_detached

                def launch_detached(tc: Any, name: str, command_text: str, log_path: str) -> None:
                    if "python -m runner.main" in command_text:
                        patch_result = apex_sandbox._exec(
                            tc,
                            name,
                            "sed -i 's/if response_format:/if False and response_format:/' "
                            "/app/grading/runner/utils/llm.py",
                            timeout=30,
                        )
                        apex_sandbox._require(
                            patch_result,
                            "grading_prepare",
                            "disable unsupported structured response_format",
                        )
                    original_launch_detached(tc, name, command_text, log_path)

                apex_sandbox._launch_detached = launch_detached
                apex_sandbox._rsi_grader_patch_installed = True

            old_argv = sys.argv[:]
            old_cwd = Path.cwd()
            old_environment = dict(os.environ)
            try:
                os.environ.clear()
                os.environ.update({str(key): str(value) for key, value in environment.items()})
                os.chdir(cwd)
                sys.argv = ["evobench", *command[3:]]
                return_code = 0
                try:
                    cli.main()
                except SystemExit as exc:
                    return_code = int(exc.code or 0)
                if return_code == 0:
                    _retry_infrastructure_failures(cli, command)
                return subprocess.CompletedProcess(command, return_code)
            finally:
                sys.argv = old_argv
                os.chdir(old_cwd)
                os.environ.clear()
                os.environ.update(old_environment)

    runner.EvoBenchRSIEvaluatorConfig = evaluator_config
    runner._write_seed_refs = write_seed_refs
    rsi_evaluator._validate_task_result = validate_task_result
    rsi_evaluator._run_host_command = run_host_command


def _with_default(arguments: list[str], flag: str, value: str) -> list[str]:
    if flag in arguments:
        return arguments
    return [*arguments, flag, value]


async def _smoke(task_id: str, evaluator_cls: Any, config_cls: Any, *, evobench_root: Path) -> int:
    smoke_root = RUNTIME_ROOT / "smoke" / task_id
    if smoke_root.exists():
        shutil.rmtree(smoke_root)
    os.environ["RSI_EVOBENCH_SCRATCH_ROOT"] = str(SHORT_SCRATCH_ROOT / "smoke" / uuid.uuid4().hex[:12])
    refs = _write_seed_refs(smoke_root / "harness_refs.yaml")
    evaluator = evaluator_cls(
        config_cls(
            evobench_root=str(evobench_root),
            policy_model_config=str(DEFAULT_POLICY_CONFIG),
            judge_model_config=str(DEFAULT_JUDGE_CONFIG),
            judge_model=DEFAULT_JUDGE_MODEL,
            rollout_concurrency=1,
            execution_mode="e2b",
            env_file=str(DEFAULT_ENV_FILE),
            e2b_template=DEFAULT_DEEPAGENT_TEMPLATE,
            apex_template=DEFAULT_DEEPAGENT_TEMPLATE,
        )
    )
    eval_ref_path = await evaluator.evaluate_batch(
        [{"case_id": task_id}],
        "",
        str(refs),
        str(smoke_root / "evaluation"),
        None,
    )
    eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
    case = eval_ref["cases"][0]
    result = json.loads(Path(case["result_path"]).read_text(encoding="utf-8"))
    print(f"SMOKE_EVAL_REF={eval_ref_path}")
    print(f"SMOKE_TASK_ID={task_id}")
    print(f"SMOKE_SCORE={case['score']}")
    print(f"SMOKE_NATIVE_SCORE={result['score']}")
    print("SMOKE_COMPLETED=true")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    mode = arguments.pop(0) if arguments else "optimize"
    if mode in {"-h", "--help"}:
        arguments.insert(0, mode)
        mode = "optimize"
    if any(value in {"-h", "--help"} for value in arguments):
        runner, _, _, _ = _bootstrap_imports()
        return int(runner.main(arguments))
    _configure_environment()
    _validate_multimodal_judge()
    runner, rsi_evaluator, evaluator_cls, config_cls = _bootstrap_imports()
    requested_root = (
        os.environ.get("EVOBENCH_ROOT", "") if mode == "smoke" else _argument_value(arguments, "--evobench-root")
    )
    evobench_root = runner.resolve_evobench_root(requested_root)
    _install_runtime_patches(runner, rsi_evaluator, evobench_root=evobench_root)
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)

    if mode == "smoke":
        task_id = arguments[0] if arguments else DEFAULT_SMOKE_TASK
        return asyncio.run(_smoke(task_id, evaluator_cls, config_cls, evobench_root=evobench_root))
    if mode != "optimize":
        raise ValueError("first argument must be 'smoke' or 'optimize'")

    arguments = _with_default(arguments, "--output-dir", str(RUNTIME_ROOT / "runs"))
    arguments = _with_default(arguments, "--env-file", str(DEFAULT_ENV_FILE))
    arguments = _with_default(arguments, "--evobench-root", str(evobench_root))
    arguments = _with_default(arguments, "--e2b-template", DEFAULT_DEEPAGENT_TEMPLATE)
    arguments = _with_default(arguments, "--apex-template", DEFAULT_DEEPAGENT_TEMPLATE)
    arguments = _with_default(arguments, "--sibling-candidate-count", "1")
    os.chdir(REPO_ROOT)
    return int(runner.main(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
