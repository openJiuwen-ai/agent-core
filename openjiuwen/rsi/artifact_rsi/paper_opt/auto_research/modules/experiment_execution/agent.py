from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.env import load_project_dotenv
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.logging import active_artifact_dir
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.metrics import (
    harness_failed,
    sanitize_diagnostic_payload,
    validate_metrics_contract,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import logs_dir, results_dir, workspace_dir
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.schemas import ImplementedVariant
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import (
    ExperimentExecutionInput,
    ExperimentExecutionOutput,
    ExperimentResult,
    VariantResult,
)

# Deliberately plain Python, no OpenJiuwen — code_implementation already wrote
# and smoke-tested the code; running it for real is a deterministic subprocess
# invocation (variant.invocation) plus reading back metrics.json, not a task
# that benefits from an LLM/agent loop.

_OUTPUT_FLAG = "--output"
_ENV_DIAGNOSTIC_KEYS = ("API_KEY", "API_BASE", "MODEL_NAME", "OPENAI_API_KEY")
_SECRET_ENV_KEYS = ("API_KEY", "OPENAI_API_KEY")
# Retried: plausibly transient (environment/infra), not the code's fault.
# Not retried: nonzero_exit/missing_metrics/invalid_metrics are deterministic
# — the same code will fail the same way again, so a bare retry just wastes
# time. code_implementation's own repair loop (via the manager) is the right
# place to fix those, not this module re-running unchanged code.
_TRANSIENT_FAILURE_KINDS = frozenset({"launch_error", "timeout"})
_MAX_SIDECAR_BYTES = 64_000
_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9\-._~+/]+=*)")
_AUTH_RE = re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;]+)")


def _decode_captured(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _variant_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONFAULTHANDLER", "1")
    return env


def _redact_text(text: str) -> str:
    """Strip configured secret values and bearer-style credentials from logs."""
    redacted = text
    for name in _SECRET_ENV_KEYS:
        value = os.getenv(name, "").strip()
        if value and len(value) >= 4:
            redacted = redacted.replace(value, f"${name}")
    redacted = _BEARER_RE.sub(r"\1$REDACTED", redacted)
    redacted = _AUTH_RE.sub(r"\1$REDACTED", redacted)
    return redacted


def _remove_stale_metrics(metrics_path: Path) -> None:
    try:
        if metrics_path.exists():
            metrics_path.unlink()
    except OSError:
        pass


def _mirror_artifact(src: Path, dest: Path) -> None:
    """Copy ``src`` to the canonical results/logs tree; no-op if they are the same path."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if src.resolve() == dest.resolve():
            return
    except OSError:
        pass
    if not src.is_file():
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        return
    shutil.copy2(src, dest)


def _contained_in(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _resolve_diagnostics_path(raw: str, *, code_dir: Path) -> Path | None:
    """Return a sidecar file only when it lives inside the generated-code workspace."""
    candidate_text = str(raw or "").strip()
    if not candidate_text:
        return None
    candidate = Path(candidate_text)
    if not candidate.is_absolute():
        candidate = code_dir / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_file():
        return None
    if not _contained_in(resolved, code_dir):
        return None
    return resolved


def _load_sanitized_sidecar(path: Path) -> dict[str, Any] | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0 or size > _MAX_SIDECAR_BYTES:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    sanitized = sanitize_diagnostic_payload(payload)
    return sanitized if isinstance(sanitized, dict) else None


def _collect_diagnostics_sidecar(
    metrics: dict[str, Any], *, code_dir: Path, dest: Path
) -> str:
    raw_path = str(metrics.get("diagnostics_path") or "").strip()
    source = _resolve_diagnostics_path(raw_path, code_dir=code_dir)
    if source is None:
        return ""
    sanitized = _load_sanitized_sidecar(source)
    if not sanitized:
        return ""
    try:
        dest.write_text(
            json.dumps(sanitized, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return ""
    return str(dest)


@dataclass
class _VariantRun:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    launch_error: str
    duration_ms: int
    metrics: dict[str, Any]
    metrics_state: str
    failure_kind: str


def _classify_failure(
    *,
    launch_error: str,
    timed_out: bool,
    exit_code: int,
    metrics_state: str,
    metrics: dict[str, Any],
    expected_method: str = "",
) -> str:
    if launch_error:
        return "launch_error"
    if timed_out:
        return "timeout"
    if exit_code != 0:
        return "nonzero_exit"
    if metrics_state == "missing":
        return "missing_metrics"
    if metrics_state == "invalid_json":
        return "invalid_metrics"
    if not metrics:
        return "missing_metrics"
    contract = validate_metrics_contract(
        metrics, expected_method=expected_method, metrics_state=metrics_state
    )
    if not contract.ok:
        reasons = {item.reason for item in contract.issues}
        if "harness_failed" in reasons:
            return "harness_failed"
        if "missing_metrics" in reasons:
            return "missing_metrics"
        return "invalid_metrics"
    if harness_failed(metrics):
        return "harness_failed"
    return "ok"


def _process_status_for(run: _VariantRun, *, expected_method: str = "") -> str:
    if run.exit_code != 0:
        return "failed"
    if run.metrics_state != "present":
        return "failed"
    if harness_failed(run.metrics):
        return "failed"
    contract = validate_metrics_contract(
        run.metrics, expected_method=expected_method, metrics_state=run.metrics_state
    )
    if not contract.ok:
        return "failed"
    return "completed"


def _read_metrics(metrics_path: Path) -> tuple[dict[str, Any], str]:
    if not metrics_path.exists():
        return {}, "missing"
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, "invalid_json"
    if not isinstance(payload, dict):
        return {}, "invalid_json"
    return payload, "present"


def _diagnostics(
    *,
    code_dir: Path,
    metrics_path: Path,
    exit_code: int,
    timeout: int | None,
    duration_ms: int,
    metrics_state: str,
    failure_kind: str,
) -> str:
    env_flags = [
        f"{name}={'set' if os.getenv(name, '').strip() else 'missing'}"
        for name in _ENV_DIAGNOSTIC_KEYS
    ]
    try:
        files = sorted(path.name for path in code_dir.iterdir())[:40]
    except OSError as exc:
        files = [f"(unreadable: {exc})"]
    return (
        "--- diagnostics ---\n"
        f"cwd={code_dir}\n"
        f"exit_code={exit_code}\n"
        f"timeout_seconds={timeout}\n"
        f"duration_ms={duration_ms}\n"
        f"metrics_path={metrics_path} ({metrics_state})\n"
        f"failure_kind={failure_kind}\n"
        f"env: {', '.join(env_flags)}\n"
        f"code_dir files: {', '.join(files)}\n"
    )


_SUMMARY_METRIC_KEYS = (
    "status",
    "failure_stage",
    "failure_substage",
    "error_type",
    "error_code",
    "detail",
    "retryable",
    "fingerprint",
    "acquisition_status",
)


def _failure_summary(run: _VariantRun, *, diagnostics_path: str = "") -> str:
    """Host-generated cause line when the subprocess printed nothing."""
    lines = [
        f"exit_code={run.exit_code}",
        f"failure_kind={run.failure_kind}",
        f"metrics_state={run.metrics_state}",
        f"timed_out={run.timed_out}",
        f"duration_ms={run.duration_ms}",
    ]
    if run.launch_error:
        lines.append(f"launch_error={_redact_text(run.launch_error)}")
    for key in _SUMMARY_METRIC_KEYS:
        value = (run.metrics or {}).get(key)
        if value is None or value == "":
            continue
        lines.append(f"{key}={_redact_text(str(value))}")
    if diagnostics_path:
        lines.append(f"diagnostics_path={diagnostics_path}")
    return "--- failure_summary ---\n" + "\n".join(lines) + "\n"


def _variant_note(name: str, run: _VariantRun, attempts: int) -> str:
    extra = f", launch_error={run.launch_error}" if run.launch_error else ""
    attempt_note = f", attempts={attempts}" if attempts > 1 else ""
    return (
        f"{name}: {run.failure_kind} (exit_code={run.exit_code}, "
        f"metrics={run.metrics_state}, duration_ms={run.duration_ms}{extra}{attempt_note})"
    )


class ExperimentExecutionAgent:
    """Runs an already-implemented, smoke-tested experiment codebase for real."""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def run(self, inputs: ExperimentExecutionInput) -> ExperimentExecutionOutput:
        load_project_dotenv()
        plan, implementation = inputs.plan, inputs.implementation
        if implementation.status != "ready":
            raise RuntimeError(
                f"code_implementation did not produce a ready codebase for "
                f"{plan.run_id} (status={implementation.status}); "
                f"refusing to execute. notes: {implementation.notes}"
            )

        logs = active_artifact_dir(plan.run_id, logs_dir(plan.run_id))
        results = active_artifact_dir(plan.run_id, results_dir(plan.run_id))
        logs.mkdir(parents=True, exist_ok=True)
        results.mkdir(parents=True, exist_ok=True)
        # Resolve to absolute paths: the subprocess below runs with cwd=code_dir,
        # so a relative --output/log path would resolve against the wrong
        # directory. run_dir only ends up in the returned ExperimentResult, but
        # kept absolute too for consistency with CodeImplementationManifest.workspace_dir.
        run_dir = workspace_dir(plan.run_id).resolve()
        logs = logs.resolve()
        results = results.resolve()
        canonical_logs = logs_dir(plan.run_id).resolve()
        canonical_results = results_dir(plan.run_id).resolve()

        code_dir = Path(implementation.workspace_dir)
        exec_cfg = self.config.get("experiment_execution", {}) or {}
        timeout = exec_cfg.get("timeout_seconds")
        max_transient_retries = int(exec_cfg.get("max_transient_retries", 1))

        variant_results: list[VariantResult] = []
        notes_parts: list[str] = []
        for variant in implementation.variants:
            result, run = self._run_variant(
                variant,
                code_dir=code_dir,
                logs=logs,
                results=results,
                timeout=timeout,
                max_transient_retries=max_transient_retries,
            )
            variant_results.append(result)
            _mirror_artifact(
                results / f"{variant.name}.metrics.json",
                canonical_results / f"{variant.name}.metrics.json",
            )
            _mirror_artifact(logs / f"{variant.name}.log", canonical_logs / f"{variant.name}.log")
            _mirror_artifact(
                logs / f"{variant.name}.diagnostics.json",
                canonical_logs / f"{variant.name}.diagnostics.json",
            )
            if run.failure_kind != "ok":
                notes_parts.append(_variant_note(variant.name, run, result.attempts))

        process_complete = all(item.process_status == "completed" for item in variant_results)
        status = "completed" if process_complete else "failed"
        notes = "; ".join(notes_parts) if notes_parts else ""
        if status == "failed" and not notes:
            notes = "one or more variants crashed or wrote no metrics.json; see per-variant logs."

        result = ExperimentResult(
            run_id=plan.run_id,
            workspace_dir=str(run_dir),
            variants=variant_results,
            status=status,
            notes=notes,
        )
        return ExperimentExecutionOutput(result=result)

    @staticmethod
    def _execute_once(
        command: list[str],
        *,
        code_dir: Path,
        timeout: int,
        metrics_path: Path,
        expected_method: str = "",
    ) -> tuple[_VariantRun, str]:
        """One subprocess attempt. Returns (run, log_section) — the section
        is this attempt's own log text, not yet written to disk; the caller
        accumulates sections across retries into a single log file."""
        stdout = ""
        stderr = ""
        timed_out = False
        launch_error = ""
        started = time.monotonic()

        try:
            proc = subprocess.run(
                command,
                cwd=code_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=_variant_env(),
            )
            exit_code = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = -1
            stdout = _decode_captured(exc.stdout)
            stderr = _decode_captured(exc.stderr) or str(exc)
        except OSError as exc:
            exit_code = -1
            launch_error = f"{type(exc).__name__}: {exc}"
            stderr = launch_error

        duration_ms = int((time.monotonic() - started) * 1000)
        metrics, metrics_state = _read_metrics(metrics_path)
        failure_kind = _classify_failure(
            launch_error=launch_error,
            timed_out=timed_out,
            exit_code=exit_code,
            metrics_state=metrics_state,
            metrics=metrics,
            expected_method=expected_method,
        )
        run = _VariantRun(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            launch_error=launch_error,
            duration_ms=duration_ms,
            metrics=metrics,
            metrics_state=metrics_state,
            failure_kind=failure_kind,
        )

        log_section = (
            f"$ {' '.join(command)}\n"
            f"cwd={code_dir}\n\n"
            f"--- stdout ---\n{_redact_text(stdout)}\n"
            f"--- stderr ---\n{_redact_text(stderr)}\n"
        )
        if timed_out:
            log_section += f"\n--- timeout ---\nsubprocess exceeded timeout_seconds={timeout}\n"
        if launch_error:
            log_section += f"\n--- launch_error ---\n{_redact_text(launch_error)}\n"
        log_section += "\n" + _diagnostics(
            code_dir=code_dir,
            metrics_path=metrics_path,
            exit_code=exit_code,
            timeout=timeout,
            duration_ms=duration_ms,
            metrics_state=metrics_state,
            failure_kind=failure_kind,
        )
        return run, log_section

    @classmethod
    def _run_variant(
        cls,
        variant: ImplementedVariant,
        *,
        code_dir: Path,
        logs: Path,
        results: Path,
        timeout: int,
        max_transient_retries: int,
    ) -> tuple[VariantResult, _VariantRun]:
        """Runs variant.invocation, retrying only launch_error/timeout
        failures (see _TRANSIENT_FAILURE_KINDS) up to max_transient_retries
        extra times. A deterministic failure (nonzero_exit, missing/invalid
        metrics) is not retried — the same code will fail the same way
        again; that's code_implementation's repair loop's job, not this
        module re-running unchanged code and hoping."""
        metrics_path = results / f"{variant.name}.metrics.json"
        log_path = logs / f"{variant.name}.log"
        sidecar_path = logs / f"{variant.name}.diagnostics.json"
        command = [*variant.invocation, _OUTPUT_FLAG, str(metrics_path)]
        max_attempts = max(1, max_transient_retries + 1)

        attempt = 0
        log_sections: list[str] = []
        while True:
            attempt += 1
            _remove_stale_metrics(metrics_path)
            run, section = cls._execute_once(
                command,
                code_dir=code_dir,
                timeout=timeout,
                metrics_path=metrics_path,
                expected_method=variant.name,
            )
            log_sections.append(f"=== attempt {attempt}/{max_attempts} ===\n{section}")
            if run.failure_kind not in _TRANSIENT_FAILURE_KINDS or attempt >= max_attempts:
                break

        copied = _collect_diagnostics_sidecar(
            run.metrics, code_dir=code_dir, dest=sidecar_path
        )
        streams_empty = not run.stdout.strip() and not run.stderr.strip()
        if streams_empty and run.failure_kind != "ok":
            log_sections.append(_failure_summary(run, diagnostics_path=copied))

        log_path.write_text("\n\n".join(log_sections), encoding="utf-8")

        # run holds the last attempt's outcome (the loop only breaks past a
        # transient failure by exhausting retries, or on a non-transient/ok
        # result) — that's the correct basis for both process_status and the
        # metrics/exit_code/etc. fields below.
        process_status = _process_status_for(run, expected_method=variant.name)
        return (
            VariantResult(
                name=variant.name,
                metrics=run.metrics,
                exit_code=run.exit_code,
                log_path=str(log_path),
                failure_kind=run.failure_kind,
                metrics_state=run.metrics_state,
                duration_ms=run.duration_ms,
                process_status=process_status,
                attempts=attempt,
                diagnostics_path=copied,
            ),
            run,
        )
