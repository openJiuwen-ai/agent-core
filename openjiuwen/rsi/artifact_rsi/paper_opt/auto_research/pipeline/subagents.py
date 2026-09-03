"""Allowlisted adapters that turn existing modules into manager subagents."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.metrics import (
    compact_metrics,
    failure_class_from_metrics,
    failure_fingerprint,
    metric_diagnostics,
    scientific_status_from_comparison,
    sanitize_diagnostic_payload,
    scientific_status_from_metrics,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    ensure_manager_dir,
    find_harness_run_dirs,
    generated_code_dir,
    module_attempt_dir,
    report_path,
    resolve_project_reference,
    results_dir,
    smoke_test_dir,
    to_project_relative,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.agent import CodeImplementationAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.schemas import (
    CodeImplementationInput,
    CodeImplementationOutput,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.agent import ExperimentDesignAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import (
    EvaluationFeedback,
    ExperimentDesignFeedbackInput,
    ExperimentDesignInput,
    ExperimentDesignOutput,
    ExperimentDesignResearchRevisionInput,
    ResearchBrief,
    utc_now,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.agent import ExperimentExecutionAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import (
    ExperimentExecutionInput,
    ExperimentExecutionOutput,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.artifacts import bounded_text
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import (
    CodeHandoff,
    DesignHandoff,
    ExecutionHandoff,
    ModuleId,
    ModuleMode,
    PersistedManagerState,
    ReflectionHandoff,
    ReportHandoff,
    SubagentReport,
    SubtaskContract,
    SurveyHandoff,
    VariantHandoff,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reflection.agent import ReflectionAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reflection.schemas import ReflectionInput
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.agent import ReportingAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.schemas import ReportingInput
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.agent import TopicSurveyAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.schemas import TopicSurveyInput


class SubagentAdapter(Protocol):
    module: ModuleId

    async def ainvoke(
        self,
        contract: SubtaskContract,
        state: PersistedManagerState,
        *,
        round_index: int,
        attempt: int,
    ) -> SubagentReport:
        ...


def _safe_rel(path: str) -> str:
    if not path:
        return ""
    try:
        return to_project_relative(path)
    except ValueError:
        return ""


def _normalize_failure(exc: BaseException) -> str:
    text = str(exc).lower()
    if "timeout" in text:
        return "timeout"
    if "cancel" in text:
        return "cancelled"
    if "credential" in text or "api" in text:
        return "provider"
    return "exception"


def _file_excerpt(path: str | Path, limit: int) -> str:
    """Read a log tail for repair prompts. Do not put this on manager reports."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return "[truncated head]\n" + cleaned[-(limit - 18) :]


def _contract_brief(contract: SubtaskContract) -> str:
    criteria = "\n".join(f"- {item}" for item in contract.acceptance_criteria)
    constraints = "\n".join(f"- {item}" for item in contract.constraints) or "- (none)"
    parts = [
        "# Manager subtask contract",
        "",
        f"Module: `{contract.module}` / `{contract.mode}`",
        "",
        "## Goal",
        "",
        contract.goal,
        "",
        "## Acceptance criteria",
        "",
        criteria,
        "",
        "## Constraints",
        "",
        constraints,
        "",
    ]
    if contract.repair_instruction.strip():
        parts.extend(["## Repair instruction", "", contract.repair_instruction.strip(), ""])
    if contract.followup_query.strip():
        parts.extend(["## Follow-up query", "", contract.followup_query.strip(), ""])
    return "\n".join(parts)


def _write_subtask_contract(state: PersistedManagerState, contract: SubtaskContract) -> str:
    dest = ensure_manager_dir(state.original_task.run_id) / "subtask_contract.md"
    dest.write_text(_contract_brief(contract), encoding="utf-8")
    return to_project_relative(dest)


def _write_original_task_brief(state: PersistedManagerState) -> str:
    """Persist the original task so design can read constraints without a fixture folder."""
    task = state.original_task
    dest = ensure_manager_dir(task.run_id) / "original_task.md"
    constraints = "\n".join(f"- {item}" for item in task.constraints) or "- (none)"
    dest.write_text(
        "\n".join(
            [
                "# Original task",
                "",
                "## Topic",
                "",
                task.topic,
                "",
                "## Objective",
                "",
                task.objective or "(none)",
                "",
                "## Task mode",
                "",
                task.task_mode,
                "",
                "## Initial paper context",
                "",
                task.initial_prompt or "(none)",
                "",
                "## Constraints",
                "",
                constraints,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return to_project_relative(dest)


def _unique_paths(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for path in group:
            if path and path not in seen:
                seen.add(path)
                out.append(path)
    return out


def _design_resource_paths(
    state: PersistedManagerState, contract: SubtaskContract | None = None
) -> list[str]:
    """Prefer topic-survey artifacts over leftover fixture paths; always include the task brief."""
    brief = _write_original_task_brief(state)
    extra: list[str] = []
    if contract is not None:
        extra.append(_write_subtask_contract(state, contract))
    survey = _latest_survey_paths(state)
    if survey:
        return _unique_paths(survey, [brief], extra)
    return _unique_paths(list(state.task_state.research_paths), [brief], extra)


_REPAIR_CONTEXT_CHARS = 4000
_REPAIR_LINE_CHARS = 400
_REPAIR_SUMMARY_CHARS = 1200
_REPAIR_METRICS_CHARS = 800
_REPAIR_SIDECAR_CHARS = 1200
_REPAIR_LOG_CHARS = 800
_SDK_LOG_TYPE_RE = re.compile(
    r"\| (?:llm|common|tool|interface|performance|prompt_builder) \|"
)


_LOCAL_DATASET_RE = re.compile(
    r"(?i)(\bdata[/\\][\w./\\-]+\.(jsonl?|csv)\b|\b(local|fixed)\b.{0,40}\b(dataset|file|path)\b"
    r"|\b(do not|don't|never)\s+download\b)"
)


def _uses_fixed_local_dataset(state: PersistedManagerState) -> bool:
    texts = [*(state.original_task.constraints or []), getattr(state.original_task, "topic", "") or ""]
    return bool(_LOCAL_DATASET_RE.search("\n".join(texts)))


def _code_host_instructions(contract: SubtaskContract, state: PersistedManagerState) -> str:
    constraints = state.original_task.constraints
    constraint_block = "\n".join(f"- {item}" for item in constraints) or "- (none listed)"
    if _uses_fixed_local_dataset(state):
        dataset_rule = (
            "1. Use the dataset source specified by the original task / design. "
            "A fixed local dataset is supplied — use that file. Do not download a "
            "replacement and do not invent a hardcoded toy corpus.\n"
        )
    else:
        dataset_rule = (
            "1. Use the dataset source specified by the original task / design. "
            "Do not invent a hardcoded toy corpus.\n"
        )
    live = (
        "## Original-task constraints (host-injected)\n\n"
        f"{constraint_block}\n\n"
        "## Live experiment requirements\n\n"
        "Smoke tests (`--smoke-test`) must use the same invoke/run_method path as a "
        "full run, on exactly one item (synthetic context or the first dataset row), "
        "and must call the live model via `API_KEY`, `API_BASE`, and `MODEL_NAME`. "
        "Parser-only stubs, dummy replies, and skipping variant construction are "
        "invalid. Write one item record and a positive `model_call_count`. "
        "The real entry point invoked WITHOUT `--smoke-test` must:\n"
        f"{dataset_rule}"
        "2. If the original task specifies a subset size (for example 20 tasks), apply "
        "that as a sample-size cap on the real dataset — not as a replacement for it.\n"
            "3. Call the live model using `API_KEY`, `API_BASE`, and `MODEL_NAME` from the "
            "process environment (loaded from the repo `.env`). Do not hardcode keys or "
            "substitute a non-LLM heuristic.\n"
            "4. Follow the design for each variant. A no-tool baseline may be a single "
            "live-model completion. When the design asks for an agent, prefer OpenJiuwen "
            "(`create_deep_agent` / `ReActAgent` / equivalent) with the required tools; "
            "plain Python is allowed if the SDK is a poor fit. Do not treat a missing "
            "OpenJiuwen import as an automatic implementation failure.\n"
            "5. The host invokes each variant separately (`--method <name> --output "
            "<path>`). Do not require `--method all`, and do not refuse a full "
            "`--method proposed` or `--method <baseline>` run.\n\n"
    )
    return _execution_repair_block(contract, state) + _code_retry_block(contract, state) + live + (
        "## Manager subtask contract\n\n"
        f"{_contract_brief(contract)}\n"
    )


def _read_log_for_repair(path: str, limit: int) -> str:
    raw = Path(path)
    if not raw.is_absolute():
        try:
            raw = resolve_project_reference(path)
        except ValueError:
            raw = Path(path)
    try:
        text = raw.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if len(text) <= limit:
        return text
    return "[truncated head]\n" + text[-(limit - 18) :]


def _is_sdk_noise_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if "reasoning.encrypted" in stripped or "gAAAA" in stripped:
        return True
    if "ChatCompletion(" in stripped:
        return True
    if _SDK_LOG_TYPE_RE.search(stripped):
        return True
    if '"event_type"' in stripped and '"module_type"' in stripped:
        return True
    return len(stripped) > 800 and stripped[:1] in "{["


def _diagnostic_log_excerpt(path: str, limit: int) -> str:
    raw = _read_log_for_repair(path, max(limit * 8, 8_000))
    if not raw:
        return ""
    kept: list[str] = []
    for line in raw.splitlines():
        if _is_sdk_noise_line(line):
            continue
        clipped = line.rstrip()
        if len(clipped) > _REPAIR_LINE_CHARS:
            clipped = clipped[: _REPAIR_LINE_CHARS - 1] + "…"
        kept.append(clipped)
    if not kept:
        return ""
    text = "\n".join(kept)
    if len(text) <= limit:
        return text
    return "[truncated head]\n" + text[-(limit - 18) :]


def _variant_repair_lines(handoff: Any) -> list[str]:
    lines: list[str] = []
    for item in list(getattr(handoff, "variants", []) or []):
        name = getattr(item, "name", "") or "variant"
        bits = [name]
        process_status = getattr(item, "process_status", "") or ""
        if process_status:
            bits.append(f"process={process_status}")
        exit_code = getattr(item, "exit_code", None)
        if exit_code is not None:
            bits.append(f"exit_code={exit_code}")
        failure_kind = getattr(item, "failure_kind", "") or ""
        if failure_kind:
            bits.append(f"failure_kind={failure_kind}")
        metrics_state = getattr(item, "metrics_state", "") or ""
        if metrics_state:
            bits.append(f"metrics_state={metrics_state}")
        compact = compact_metrics(dict(getattr(item, "metrics", {}) or {}))
        if compact:
            bits.append("metrics=" + str(compact))
        lines.append("- " + "; ".join(bits))
    return lines


def _clip_repair_text(text: str, limit: int) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)] + "…"


def _structured_json_excerpt(path: str, limit: int) -> str:
    raw = _read_log_for_repair(path, max(limit * 4, 8_000))
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _clip_repair_text(raw, limit)
    sanitized = sanitize_diagnostic_payload(payload)
    try:
        dumped = json.dumps(sanitized, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return _clip_repair_text(raw, limit)
    return _clip_repair_text(dumped, limit)


def _path_kind(path: str) -> str:
    lowered = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if lowered.endswith(".diagnostics.json"):
        return "sidecar"
    if lowered.endswith(".metrics.json"):
        return "metrics"
    if lowered.endswith(".json"):
        return "sidecar"
    if lowered.endswith((".log", ".txt")):
        return "log"
    return "other"


def _structured_failure_summary(handoff: Any) -> str:
    bits: list[str] = []
    process_status = getattr(handoff, "process_status", "") or ""
    scientific_status = getattr(handoff, "scientific_status", "") or ""
    failure_kind = getattr(handoff, "failure_kind", "") or ""
    failure_class = getattr(handoff, "failure_class", "") or ""
    stage = getattr(handoff, "failure_stage", "") or ""
    substage = getattr(handoff, "failure_substage", "") or ""
    fingerprint = getattr(handoff, "fingerprint", "") or ""
    status_line: list[str] = []
    if process_status:
        status_line.append(f"process={process_status}")
    if scientific_status:
        status_line.append(f"science={scientific_status}")
    if failure_kind:
        status_line.append(f"failure_kind={failure_kind}")
    if failure_class:
        status_line.append(f"failure_class={failure_class}")
    if status_line:
        bits.append("Status: " + "; ".join(status_line))
    diagnostic = getattr(handoff, "diagnostic", {}) or {}
    if isinstance(diagnostic, dict):
        stage = stage or str(diagnostic.get("failure_stage") or "")
        substage = substage or str(diagnostic.get("failure_substage") or "")
        fingerprint = fingerprint or str(diagnostic.get("fingerprint") or "")
        detail = str(diagnostic.get("detail") or "")
        error_code = str(diagnostic.get("error_code") or "")
        error_type = str(diagnostic.get("error_type") or "")
        cause_bits = []
        if stage or substage:
            cause_bits.append(f"Harness failed at {stage}/{substage}")
        if error_code:
            cause_bits.append(f"error_code={error_code}")
        if error_type:
            cause_bits.append(f"error_type={error_type}")
        if detail:
            cause_bits.append(f"detail={detail}")
        if fingerprint:
            cause_bits.append(f"fingerprint={fingerprint}")
        if cause_bits:
            bits.append("Cause: " + "; ".join(cause_bits))
    variant_lines = _variant_repair_lines(handoff)
    if variant_lines:
        bits.append("Variants:\n" + "\n".join(variant_lines))
    return "\n".join(bits)


def _compact_metrics_block(handoff: Any, limit: int) -> str:
    chunks: list[str] = []
    for item in list(getattr(handoff, "variants", []) or []):
        name = getattr(item, "name", "") or "variant"
        compact = compact_metrics(dict(getattr(item, "metrics", {}) or {}))
        if not compact:
            continue
        chunks.append(f"{name}: {compact}")
    return _clip_repair_text("\n".join(chunks), limit)


def _execution_repair_block(contract: SubtaskContract, state: PersistedManagerState) -> str:
    prior = next(
        (item for item in reversed(state.reports) if item.module == "experiment_execution"),
        None,
    )
    if prior is None:
        return ""
    handoff = getattr(prior, "handoff", None)
    process_failed = (
        prior.outcome == "failed"
        or state.task_state.latest_execution_status == "failed"
        or getattr(handoff, "process_status", "") == "failed"
        or getattr(handoff, "failure_class", "") == "infrastructure"
    )
    if not process_failed and not contract.repair_instruction.strip():
        return ""

    header_bits = [f"Execution summary:\n{prior.summary or '(none)'}"]
    structured = _clip_repair_text(_structured_failure_summary(handoff), _REPAIR_SUMMARY_CHARS)
    if structured:
        header_bits.append(structured)

    metrics_block = _compact_metrics_block(handoff, _REPAIR_METRICS_CHARS)

    log_paths = [
        _safe_rel(path) for path in list(getattr(handoff, "result_paths", []) or []) if path
    ]
    log_paths = [path for path in log_paths if path]
    if not log_paths:
        log_paths = [
            rel
            for rel in (
                _safe_rel(item.log_path)
                for item in list(getattr(handoff, "variants", []) or [])
                if getattr(item, "log_path", "")
            )
            if rel
        ]
    sidecar_paths = [
        _safe_rel(path)
        for path in list(getattr(handoff, "diagnostic_paths", []) or [])
        if path
    ]
    sidecar_paths = [path for path in sidecar_paths if path]
    for item in list(getattr(handoff, "variants", []) or []):
        rel = _safe_rel(str(getattr(item, "diagnostics_path", "") or ""))
        if rel and rel not in sidecar_paths:
            sidecar_paths.append(rel)
    for path in log_paths:
        if _path_kind(path) == "sidecar" and path not in sidecar_paths:
            sidecar_paths.append(path)

    sidecar_sections: list[str] = []
    remaining_sidecar = _REPAIR_SIDECAR_CHARS
    for path in sidecar_paths:
        if remaining_sidecar <= 0:
            break
        body = _structured_json_excerpt(path, remaining_sidecar)
        if not body:
            continue
        block = f"### {path}\n{body}"
        sidecar_sections.append(block)
        remaining_sidecar -= len(block)

    log_sections: list[str] = []
    remaining_log = _REPAIR_LOG_CHARS
    log_file_paths = [path for path in log_paths if _path_kind(path) == "log"]
    per_file = max(200, remaining_log // max(len(log_file_paths), 1)) if log_file_paths else remaining_log
    for path in log_file_paths:
        if remaining_log <= 0:
            break
        body = _diagnostic_log_excerpt(path, min(per_file, remaining_log))
        if not body:
            continue
        block = f"### {path}\n{body}"
        log_sections.append(block)
        remaining_log -= len(block)
    if remaining_log > 0:
        for excerpt in list(getattr(handoff, "failure_excerpts", []) or []):
            if remaining_log <= 0:
                break
            if not excerpt or _is_sdk_noise_line(excerpt):
                continue
            clipped = _clip_repair_text(excerpt, remaining_log)
            if not clipped:
                continue
            log_sections.append(clipped)
            remaining_log -= len(clipped)

    artifact_paths = _unique_paths(
        log_paths,
        sidecar_paths,
        [_safe_rel(path) for path in list(getattr(handoff, "diagnostic_paths", []) or [])],
    )
    sections: list[str] = []
    if metrics_block:
        sections.append("Metrics:\n" + metrics_block)
    if sidecar_sections:
        sections.append("Diagnostic sidecar:\n" + "\n\n".join(sidecar_sections))
    logs = "\n\n".join(log_sections) or "(no diagnostic log lines were readable)"
    sections.append("Terminal output:\n" + logs)
    if artifact_paths:
        sections.append("Artifacts:\n" + "\n".join(f"- {path}" for path in artifact_paths))
    return (
        "## Repair the failed full execution\n\n"
        "The previous non-smoke run failed. Diagnose from the structured failure first, "
        "then metrics, then the terminal tail. A passing `--smoke-test` is not proof that "
        "dataset download, live API calls, or metrics output work.\n\n"
        f"Repair instruction from the manager:\n{contract.repair_instruction or '(none)'}\n\n"
        + "\n\n".join(header_bits)
        + "\n\n"
        + "\n\n".join(sections)
        + "\n\n"
    )


def _code_log_artifacts(
    run_id: str,
    excerpt_limit: int,
    *,
    round_index: int | None = None,
    attempt: int | None = None,
) -> tuple[list[str], list[str]]:
    """Collect promotion + smoke logs for a code attempt without schema growth."""
    dirs: list[Path] = []
    if round_index is not None and attempt is not None:
        dirs.append(module_attempt_dir(run_id, "code_implementation", round_index, attempt))
    dirs.append(smoke_test_dir(run_id))
    log_paths: list[str] = []
    excerpts: list[str] = []
    ordered: list[Path] = []
    seen_names: set[str] = set()
    for log_dir in dirs:
        if not log_dir.is_dir():
            continue
        promotion = log_dir / "promotion.log"
        candidates = []
        if promotion.is_file():
            candidates.append(promotion)
        candidates.extend(sorted(path for path in log_dir.glob("*.log") if path != promotion))
        candidates.extend(sorted(log_dir.glob("cycle_*/*.log")))
        for path in candidates:
            key = path.relative_to(log_dir).as_posix() if path.is_relative_to(log_dir) else path.name
            if key in seen_names:
                continue
            seen_names.add(key)
            ordered.append(path)
    for path in ordered:
        rel = _safe_rel(str(path))
        if not rel or rel in log_paths:
            continue
        log_paths.append(rel)
        excerpt = _file_excerpt(path, excerpt_limit)
        if excerpt:
            excerpts.append(excerpt)
    return log_paths, excerpts


def _code_retry_block(contract: SubtaskContract, state: PersistedManagerState) -> str:
    prior = next(
        (item for item in reversed(state.reports) if item.module == "code_implementation"),
        None,
    )
    if prior is None or prior.outcome == "succeeded":
        return ""
    handoff = getattr(prior, "handoff", None)
    log_paths = list(getattr(handoff, "log_paths", []) or [])
    remaining = _REPAIR_CONTEXT_CHARS
    sections: list[str] = []
    per_file = max(400, remaining // max(len(log_paths), 1)) if log_paths else remaining
    for path in log_paths:
        if remaining <= 0:
            break
        budget = min(per_file, remaining)
        body = _diagnostic_log_excerpt(path, budget)
        if not body:
            continue
        block = f"### {path}\n{body}"
        sections.append(block)
        remaining -= len(block)
    if remaining > 0:
        for excerpt in list(getattr(handoff, "failure_excerpts", []) or []):
            if remaining <= 0:
                break
            if not excerpt or _is_sdk_noise_line(excerpt):
                continue
            clipped = excerpt.strip()
            if len(clipped) > remaining:
                clipped = clipped[: remaining - 1] + "…"
            sections.append(clipped)
            remaining -= len(clipped)
    logs = "\n\n".join(sections) or (
        "\n".join(f"- {path}" for path in log_paths) or "- (none)"
    )
    return (
        "## Repair this failed attempt\n\n"
        f"This is attempt {state.task_state.counters.code_attempts + 1}. Diagnose the previous "
        "failure instead of repeating the same implementation.\n\n"
        f"Prior failure summary:\n{prior.summary or '(none)'}\n\n"
        f"Repair instruction:\n{contract.repair_instruction or '(none)'}\n\n"
        f"Logs from the previous attempt:\n{logs}\n\n"
    )


def _variant_handoffs_from_logs(
    *,
    names: list[str],
    log_dir: Path,
    passed: bool,
    excerpt_limit: int,
) -> tuple[list[VariantHandoff], list[str]]:
    variants: list[VariantHandoff] = []
    log_paths: list[str] = []
    for name in names:
        log_path = log_dir / f"{name}.log"
        rel = _safe_rel(str(log_path)) if log_path.is_file() else ""
        if rel:
            log_paths.append(rel)
        variants.append(
            VariantHandoff(
                name=name,
                passed=passed,
                log_path=rel,
            )
        )
    return variants, log_paths


def _report(
    *,
    module: ModuleId,
    mode: ModuleMode,
    round_index: int,
    attempt: int,
    outcome: str,
    summary: str,
    retryable: bool = False,
    artifact_paths: list[str] | None = None,
    duration_ms: int | None = None,
    runtime_failure: str = "none",
    related_report_ids: list[str] | None = None,
    handoff: Any = None,
) -> SubagentReport:
    return SubagentReport(
        report_id=f"{module}:{round_index}:{attempt}",
        module=module,
        mode=mode,
        round_index=round_index,
        attempt=attempt,
        outcome=outcome,  # type: ignore[arg-type]
        retryable=retryable,
        summary=summary,
        artifact_paths=[path for path in (artifact_paths or []) if path],
        duration_ms=duration_ms,
        runtime_failure=runtime_failure,  # type: ignore[arg-type]
        related_report_ids=list(related_report_ids or []),
        handoff=handoff,
    )


class TopicSurveyAdapter:
    module: ModuleId = "topic_survey"

    def __init__(self, agent: TopicSurveyAgent):
        self.agent = agent

    async def ainvoke(
        self,
        contract: SubtaskContract,
        state: PersistedManagerState,
        *,
        round_index: int,
        attempt: int,
    ) -> SubagentReport:
        survey_cfg = dict(self.agent.config.get("topic_survey") or {})
        manager_cfg = dict(self.agent.config.get("manager") or {})
        topic = state.original_task.topic
        if state.original_task.constraints:
            topic = f"{topic}. Constraints: {'; '.join(state.original_task.constraints)}"
        if contract.goal.strip():
            topic = f"{topic}. Goal: {contract.goal.strip()}"
        if contract.followup_query.strip():
            topic = f"{topic}. Focus: {contract.followup_query.strip()}"
        inputs = TopicSurveyInput(
            topic=topic,
            max_papers=int(survey_cfg.get("max_papers", 20)),
            max_web_pages=int(survey_cfg.get("max_web_pages", 10)),
            initial_context=bounded_text(
                state.original_task.initial_prompt,
                int(manager_cfg.get("max_history_chars", 16_000)),
            ),
        )
        started = time.monotonic()
        try:
            output: ResearchBrief = await self.agent.asurvey(inputs)
        except Exception as exc:  # noqa: BLE001
            return _report(
                module=self.module,
                mode="run",
                round_index=round_index,
                attempt=attempt,
                outcome="failed",
                summary=str(exc),
                retryable=True,
                duration_ms=int((time.monotonic() - started) * 1000),
                runtime_failure=_normalize_failure(exc),
                related_report_ids=contract.related_report_ids,
            )
        artifacts = [_safe_rel(path) for path in output.resource_paths if path]
        research_summary_path = artifacts[0] if artifacts else ""
        source_paths = artifacts[1:]
        short_summary, key_findings, open_problems = _survey_fields_from_summary(
            research_summary_path
        )
        return _report(
            module=self.module,
            mode="run",
            round_index=round_index,
            attempt=attempt,
            outcome="succeeded",
            summary=bounded_text(short_summary or f"survey complete with {len(source_paths)} sources", 400),
            artifact_paths=artifacts,
            duration_ms=int((time.monotonic() - started) * 1000),
            related_report_ids=contract.related_report_ids,
            handoff=SurveyHandoff(
                short_summary=bounded_text(short_summary or "survey complete", 400),
                key_findings=key_findings,
                open_problems=open_problems,
                coverage_assessment=f"{len(source_paths)} sources",
                source_count=len(source_paths),
                research_summary_path=research_summary_path,
                source_paths=source_paths,
            ),
        )


def _evaluation_from_execution(state: PersistedManagerState) -> EvaluationFeedback:
    """Host-synthesized feedback so design/update can follow a simple completed run."""
    plan = state.task_state.latest_plan
    run_id = state.task_state.run_id
    observed: dict[str, float | int | str] = {}
    paths: list[str] = []
    result = state.latest_execution
    if result is not None:
        for variant in result.variants:
            observed.update(compact_metrics(dict(variant.metrics), prefix=variant.name))
            if variant.log_path:
                rel = _safe_rel(variant.log_path)
                if rel:
                    paths.append(rel)
            sidecar = getattr(variant, "diagnostics_path", "") or ""
            if sidecar:
                rel = _safe_rel(sidecar)
                if rel and rel not in paths:
                    paths.append(rel)
    last_exec = next(
        (item for item in reversed(state.reports) if item.module == "experiment_execution"),
        None,
    )
    if isinstance(last_exec, SubagentReport) and isinstance(last_exec.handoff, ExecutionHandoff):
        for item in last_exec.handoff.result_paths:
            rel = _safe_rel(item) if item else ""
            if rel and rel not in paths:
                paths.append(rel)
        for item in last_exec.handoff.diagnostic_paths:
            rel = _safe_rel(item) if item else ""
            if rel and rel not in paths:
                paths.append(rel)
        for variant in last_exec.handoff.variants:
            observed.update(compact_metrics(dict(variant.metrics), prefix=variant.name))
    if not paths:
        paths = [f"experiments/{run_id}/results/proposed.metrics.json"]
    if observed:
        summary = ", ".join(f"{key}={value}" for key, value in list(observed.items())[:8])
    else:
        summary = "process-completed execution; no compact metrics"
    return EvaluationFeedback(
        verdict="continue",
        summary=summary,
        observed_metrics=observed,
        result_paths=paths,
        branch=(plan.branch if plan is not None and plan.branch else "main"),
        code_session_id=f"code_implementation-{run_id}",
        evaluator_session_id=f"execution-{run_id}",
        evaluated_at=utc_now(),
    )


class ExperimentDesignAdapter:
    module: ModuleId = "experiment_design"

    def __init__(self, agent: ExperimentDesignAgent):
        self.agent = agent

    async def ainvoke(
        self,
        contract: SubtaskContract,
        state: PersistedManagerState,
        *,
        round_index: int,
        attempt: int,
    ) -> SubagentReport:
        started = time.monotonic()
        try:
            if contract.mode == "create":
                output = await self.agent.acreate(
                    ExperimentDesignInput(
                        research=ResearchBrief(
                            resource_paths=_design_resource_paths(state, contract)
                        ),
                        run_id=state.task_state.run_id,
                        session_epoch=state.task_state.design_session_epoch,
                    )
                )
            elif contract.mode == "update":
                feedback = state.task_state.latest_evaluation
                if feedback is None:
                    feedback = _evaluation_from_execution(state)
                    state.task_state.latest_evaluation = feedback
                _write_subtask_contract(state, contract)
                output = await self.agent.aupdate(
                    ExperimentDesignFeedbackInput(
                        run_id=state.task_state.run_id,
                        feedback=feedback,
                        session_epoch=state.task_state.design_session_epoch,
                    )
                )
            elif contract.mode == "revise_research":
                extra = _design_resource_paths(state, contract)
                output = await self.agent.arevise_research(
                    ExperimentDesignResearchRevisionInput(
                        run_id=state.task_state.run_id,
                        additional_research_paths=extra,
                        reason=contract.repair_instruction or contract.goal,
                        session_epoch=state.task_state.design_session_epoch,
                    )
                )
            else:
                raise RuntimeError(f"unsupported design mode: {contract.mode}")
        except Exception as exc:  # noqa: BLE001
            return _report(
                module=self.module,
                mode=contract.mode,
                round_index=round_index,
                attempt=attempt,
                outcome="failed",
                summary=str(exc),
                retryable=False,
                duration_ms=int((time.monotonic() - started) * 1000),
                runtime_failure=_normalize_failure(exc),
                related_report_ids=contract.related_report_ids,
            )
        state.task_state.latest_plan = _plan_with_inferred_baselines(output.plan, state)
        return _design_report(output, contract, round_index, attempt, started)


def _task_baseline_texts(state: PersistedManagerState) -> list[str]:
    task = state.original_task
    return [task.topic, task.objective, *task.constraints]


def _plan_with_inferred_baselines(plan, state: PersistedManagerState):
    if plan is None:
        return None
    from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.artifacts import merge_plan_baselines

    return merge_plan_baselines(
        plan,
        *_task_baseline_texts(state),
        plan.setup,
        plan.expected_outcomes,
    )


def _design_claims(plan) -> tuple[str, str]:
    objective = ""
    hypothesis = (getattr(plan, "expected_outcomes", "") or "").strip()
    setup = (getattr(plan, "setup", "") or "").strip()
    if setup.startswith("#"):
        setup = ""
    try:
        path = resolve_project_reference(plan.design_path)
    except ValueError:
        return setup, hypothesis
    if not path.is_file():
        return setup, hypothesis
    from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.artifacts import extract_claim_entries

    body = path.read_text(encoding="utf-8")
    objective_entries = extract_claim_entries(body, "objective")
    hypothesis_entries = extract_claim_entries(body, "hypothesis")
    if objective_entries:
        objective = objective_entries[-1][2]
    if hypothesis_entries:
        hypothesis = hypothesis_entries[-1][2]
    return objective or setup, hypothesis


def _design_report(
    output: ExperimentDesignOutput,
    contract: SubtaskContract,
    round_index: int,
    attempt: int,
    started: float,
) -> SubagentReport:
    plan = output.plan
    artifacts = [path for path in [plan.design_path, plan.code_agent_instruction_path] if path]
    objective, hypothesis = _design_claims(plan)
    summary = objective or hypothesis or f"design revision {plan.revision}"
    return _report(
        module="experiment_design",
        mode=contract.mode,
        round_index=round_index,
        attempt=attempt,
        outcome="succeeded",
        summary=bounded_text(summary, 400),
        artifact_paths=artifacts,
        duration_ms=int((time.monotonic() - started) * 1000),
        related_report_ids=contract.related_report_ids,
        handoff=DesignHandoff(
            objective=bounded_text(objective, 400),
            hypothesis=bounded_text(hypothesis, 400),
            metrics=list(plan.metrics),
            revision=plan.revision,
            status=plan.status,
            design_path=plan.design_path,
            code_agent_instruction_path=plan.code_agent_instruction_path,
        ),
    )


def _survey_fields_from_summary(path: str) -> tuple[str, list[str], list[str]]:
    if not path:
        return "", [], []
    try:
        raw = resolve_project_reference(path)
        text = raw.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return "", [], []

    def _section(heading: str) -> list[str]:
        lines = text.splitlines()
        capture = False
        items: list[str] = []
        body: list[str] = []
        marker = f"## {heading}".lower()
        for line in lines:
            stripped = line.strip()
            if stripped.lower().startswith("## ") and capture:
                break
            if stripped.lower() == marker:
                capture = True
                continue
            if not capture or not stripped:
                continue
            if stripped.startswith("- "):
                items.append(stripped[2:].strip())
            else:
                body.append(stripped)
        return items or body

    summary_parts = _section("Short Summary")
    findings = _section("Key Findings")[:8]
    problems = _section("Open Problems")[:8]
    return " ".join(summary_parts), findings, problems


def _latest_survey_paths(state: PersistedManagerState) -> list[str]:
    for report in reversed(state.reports):
        if report.module != "topic_survey" or report.outcome != "succeeded":
            continue
        handoff = report.handoff
        if isinstance(handoff, SurveyHandoff):
            paths = [handoff.research_summary_path, *handoff.source_paths]
            return [path for path in paths if path]
        return list(report.artifact_paths)
    return []


class CodeImplementationAdapter:
    module: ModuleId = "code_implementation"

    def __init__(self, agent: CodeImplementationAgent):
        self.agent = agent

    async def ainvoke(
        self,
        contract: SubtaskContract,
        state: PersistedManagerState,
        *,
        round_index: int,
        attempt: int,
    ) -> SubagentReport:
        if state.task_state.latest_plan is None:
            raise RuntimeError("code implementation requires a design plan")
        plan = _plan_with_inferred_baselines(state.task_state.latest_plan, state)
        if plan is not None:
            state.task_state.latest_plan = plan
        extra = _code_host_instructions(contract, state)
        original_prompt = self.agent._build_task_prompt

        def _prompt_with_host(plan, design_context):
            return extra + original_prompt(plan, design_context)

        started = time.monotonic()
        run_id = state.task_state.run_id
        excerpt_limit = state.task_state.limits.excerpt_chars
        try:
            self.agent._build_task_prompt = _prompt_with_host  # type: ignore[method-assign]
            output: CodeImplementationOutput = await self.agent.arun(
                CodeImplementationInput(plan=state.task_state.latest_plan)
            )
        except Exception as exc:  # noqa: BLE001
            log_paths, _excerpts = _code_log_artifacts(
                run_id, excerpt_limit, round_index=round_index, attempt=attempt
            )
            summary = str(exc)
            return _report(
                module=self.module,
                mode="run",
                round_index=round_index,
                attempt=attempt,
                outcome="failed",
                summary=summary,
                retryable=True,
                artifact_paths=log_paths,
                duration_ms=int((time.monotonic() - started) * 1000),
                runtime_failure=_normalize_failure(exc),
                related_report_ids=contract.related_report_ids,
                handoff=CodeHandoff(
                    status="failed",
                    readiness="failed",
                    notes=summary,
                    workspace_dir=_safe_rel(str(generated_code_dir(run_id))),
                    log_paths=log_paths,
                ),
            )
        finally:
            self.agent._build_task_prompt = original_prompt  # type: ignore[method-assign]
        impl = output.implementation
        state.latest_implementation = impl
        smoke_dir = module_attempt_dir(impl.run_id, "code_implementation", round_index, attempt)
        if not smoke_dir.is_dir():
            smoke_dir = smoke_test_dir(impl.run_id)
        variants, smoke_logs = _variant_handoffs_from_logs(
            names=[item.name for item in impl.variants],
            log_dir=smoke_dir,
            passed=impl.smoke_test_passed,
            excerpt_limit=excerpt_limit,
        )
        extra_logs, extra_excerpts = _code_log_artifacts(
            impl.run_id, excerpt_limit, round_index=round_index, attempt=attempt
        )
        log_paths = _unique_paths(extra_logs, smoke_logs)
        artifacts = [_safe_rel(impl.workspace_dir), *log_paths]
        for harness in find_harness_run_dirs(impl.run_id):
            artifacts.append(_safe_rel(str(harness)))
        succeeded = impl.status == "ready"
        readiness = getattr(impl, "readiness", None) or (
            "smoke_ready" if succeeded else "failed"
        )
        smoke_failures = dict(getattr(impl, "smoke_failures", {}) or {})
        notes = bounded_text(impl.notes or f"implementation {impl.status}", 400)
        failure_excerpts: list[str] = []
        for name, detail in smoke_failures.items():
            chunk = f"{name}: {detail}"
            if chunk.strip():
                failure_excerpts.append(bounded_text(chunk, 800))
        failure_excerpts.extend(extra_excerpts)
        if impl.notes and not succeeded:
            failure_excerpts.append(bounded_text(impl.notes, 800))
        failure_excerpts = [item for item in _unique_paths(failure_excerpts) if item][:8]
        return _report(
            module=self.module,
            mode="run",
            round_index=round_index,
            attempt=attempt,
            outcome="succeeded" if succeeded else "failed",
            retryable=not succeeded,
            summary=notes,
            artifact_paths=[path for path in artifacts if path],
            duration_ms=int((time.monotonic() - started) * 1000),
            related_report_ids=contract.related_report_ids,
            handoff=CodeHandoff(
                status=impl.status,
                readiness=readiness,
                smoke_test_passed=impl.smoke_test_passed,
                smoke_failures=smoke_failures,
                variants=variants,
                notes=notes,
                workspace_dir=_safe_rel(impl.workspace_dir),
                log_paths=log_paths,
                failure_excerpts=failure_excerpts,
            ),
        )


class ExperimentExecutionAdapter:
    module: ModuleId = "experiment_execution"

    def __init__(self, agent: ExperimentExecutionAgent):
        self.agent = agent
        self._last_result: ExperimentExecutionOutput | None = None

    async def ainvoke(
        self,
        contract: SubtaskContract,
        state: PersistedManagerState,
        *,
        round_index: int,
        attempt: int,
    ) -> SubagentReport:
        if state.task_state.latest_plan is None:
            raise RuntimeError("execution requires a design plan")
        implementation = state.latest_implementation
        if implementation is None or implementation.status != "ready":
            raise RuntimeError("execution requires a ready implementation")
        started = time.monotonic()
        try:
            output = await asyncio.to_thread(
                self.agent.run,
                ExperimentExecutionInput(
                    plan=state.task_state.latest_plan,
                    implementation=implementation,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return _report(
                module=self.module,
                mode="run",
                round_index=round_index,
                attempt=attempt,
                outcome="failed",
                summary=str(exc),
                retryable=True,
                duration_ms=int((time.monotonic() - started) * 1000),
                runtime_failure=_normalize_failure(exc),
                related_report_ids=contract.related_report_ids,
            )
        self._last_result = output
        state.latest_execution = output.result
        result = output.result
        variants: list[VariantHandoff] = []
        sciences: list[str] = []
        diagnostic_paths: list[str] = []
        failure_excerpts: list[str] = []
        for item in result.variants:
            compact = compact_metrics(dict(item.metrics))
            science = scientific_status_from_metrics(dict(item.metrics))
            sciences.append(science)
            diagnostic = metric_diagnostics(
                dict(item.metrics),
                failure_kind=item.failure_kind,
                metrics_state=item.metrics_state,
                duration_ms=item.duration_ms,
                exit_code=item.exit_code,
            )
            process_status = item.process_status or (
                "completed" if item.exit_code == 0 and item.metrics_state == "present" else "failed"
            )
            sidecar_rel = _safe_rel(item.diagnostics_path) if item.diagnostics_path else ""
            variants.append(
                VariantHandoff(
                    name=item.name,
                    passed=process_status == "completed" and science == "accepted",
                    exit_code=item.exit_code,
                    metrics=compact,
                    log_path=_safe_rel(item.log_path),
                    failure_kind=item.failure_kind,
                    metrics_state=item.metrics_state,
                    duration_ms=item.duration_ms,
                    process_status=process_status,
                    diagnostic=diagnostic,
                    diagnostics_path=sidecar_rel,
                )
            )
            if sidecar_rel:
                diagnostic_paths.append(sidecar_rel)
            stage = str(diagnostic.get("failure_stage") or compact.get("failure_stage") or "")
            substage = str(
                diagnostic.get("failure_substage") or compact.get("failure_substage") or ""
            )
            detail = str(diagnostic.get("detail") or compact.get("detail") or "")
            error_code = str(diagnostic.get("error_code") or compact.get("error_code") or "")
            if stage or substage or detail or error_code:
                banner = f"Harness failed at {stage}/{substage}"
                if error_code:
                    banner += f": {error_code}"
                if detail:
                    banner += f": {detail}"
                failure_excerpts.append(banner)
        result_paths = [
            rel for rel in (_safe_rel(item.log_path) for item in result.variants if item.log_path) if rel
        ]
        metrics_dir = results_dir(result.run_id)
        attempt_dir = module_attempt_dir(result.run_id, "experiment_execution", round_index, attempt)
        for item in result.variants:
            for candidate in (
                Path(item.log_path).with_name(f"{item.name}.metrics.json") if item.log_path else None,
                Path(item.log_path).with_name(f"{item.name}.diagnostics.json")
                if item.log_path
                else None,
                attempt_dir / f"{item.name}.metrics.json",
                attempt_dir / f"{item.name}.diagnostics.json",
                metrics_dir / f"{item.name}.metrics.json",
            ):
                if candidate is not None and candidate.is_file():
                    rel = _safe_rel(str(candidate))
                    if rel:
                        result_paths.append(rel)
                        if rel.endswith(".diagnostics.json"):
                            diagnostic_paths.append(rel)
        for harness in find_harness_run_dirs(result.run_id):
            rel = _safe_rel(str(harness))
            if rel:
                result_paths.append(rel)
        result_paths = _unique_paths(result_paths)
        diagnostic_paths = _unique_paths(diagnostic_paths)
        process_ok = result.status == "completed" and all(
            item.process_status == "completed" for item in variants
        )
        status_by_name = {
            item.name: science for item, science in zip(result.variants, sciences)
        }
        if "proposed" in status_by_name:
            # Compare the real numbers; fall back to
            # the old self-report-based value when there's nothing to compare
            # (no declared metrics, or no baseline variant present).
            # state.task_state.latest_plan is guaranteed non-None here (see
            # the guard at the top of this method).
            scientific = scientific_status_from_comparison(
                state.task_state.latest_plan.metrics, result.variants
            )
            if scientific == "unknown":
                scientific = status_by_name["proposed"]
        elif "below_threshold" in sciences:
            scientific = "below_threshold"
        elif sciences and all(item == "accepted" for item in sciences):
            scientific = "accepted"
        else:
            scientific = "unknown"
        if not process_ok:
            scientific = "unknown"
        failure_kind = next((item.failure_kind for item in result.variants if item.failure_kind), "")
        proposed_handoff = next((item for item in variants if item.name == "proposed"), None)
        diagnostic = (
            proposed_handoff.diagnostic
            if proposed_handoff is not None
            else (variants[0].diagnostic if variants else {})
        )
        if not isinstance(diagnostic, dict):
            diagnostic = {}
        failure_class = failure_class_from_metrics(
            dict(proposed_handoff.metrics) if proposed_handoff is not None else {},
            process_status="completed" if process_ok else "failed",
            scientific_status=scientific,
        )
        if not process_ok:
            failure_class = "infrastructure"
        failure_stage = str(diagnostic.get("failure_stage") or "")
        failure_substage = str(diagnostic.get("failure_substage") or "")
        fingerprint = str(
            diagnostic.get("fingerprint")
            or failure_fingerprint(
                dict(proposed_handoff.metrics if proposed_handoff is not None else {})
            )
        )
        if diagnostic.get("detail"):
            failure_excerpts.append(str(diagnostic["detail"]))
        failure_excerpts = [item for item in _unique_paths(failure_excerpts) if item][:8]
        goal_note = bounded_text(contract.goal, 200)
        summary_bits = [
            f"process={'completed' if process_ok else 'failed'}",
            f"science={scientific}",
        ]
        if failure_kind:
            summary_bits.append(f"failure_kind={failure_kind}")
        if failure_stage:
            summary_bits.append(f"stage={failure_stage}/{failure_substage}" if failure_substage else f"stage={failure_stage}")
        events = diagnostic.get("event_counts") if isinstance(diagnostic, dict) else None
        if isinstance(events, dict) and events:
            top = next(iter(events.items()))
            summary_bits.append(f"{top[0]} x{top[1]}")
        coverage = diagnostic.get("browsed_both_tools") if isinstance(diagnostic, dict) else None
        n_tasks = diagnostic.get("n_tasks") if isinstance(diagnostic, dict) else None
        if coverage is not None and n_tasks:
            summary_bits.append(f"browsing {coverage}/{n_tasks}")
        summary = "; ".join(summary_bits)
        if goal_note:
            summary = f"{summary}. requested: {goal_note}"
        artifact_paths = _unique_paths(
            [_safe_rel(result.workspace_dir)], result_paths, diagnostic_paths
        )
        return _report(
            module=self.module,
            mode="run",
            round_index=round_index,
            attempt=attempt,
            outcome="succeeded" if process_ok else "failed",
            retryable=not process_ok,
            summary=bounded_text(summary, 400),
            artifact_paths=artifact_paths,
            duration_ms=int((time.monotonic() - started) * 1000),
            related_report_ids=contract.related_report_ids,
            handoff=ExecutionHandoff(
                status="completed" if process_ok else "failed",
                process_status="completed" if process_ok else "failed",
                scientific_status=scientific,  # type: ignore[arg-type]
                failure_kind=failure_kind,
                variants=variants,
                notes=result.notes,
                result_paths=result_paths,
                failure_excerpts=failure_excerpts,
                diagnostic=diagnostic,
                failure_stage=failure_stage,
                failure_substage=failure_substage,
                failure_class=failure_class,
                fingerprint=fingerprint,
                diagnostic_paths=diagnostic_paths,
            ),
        )


_REFLECTION_VERDICT_RE = re.compile(
    r"\*\*Hypothesis verdict:\*\*\s*(supported|refuted|mixed|inconclusive)\b",
    re.IGNORECASE,
)


def _reflection_verdict(content: str) -> str:
    match = _REFLECTION_VERDICT_RE.search(content)
    return match.group(1).lower() if match else "inconclusive"


def _reflection_feedback(
    *,
    plan,
    result,
    reflection,
    verdict: str,
    summary: str,
) -> EvaluationFeedback:
    observed: dict[str, float | int | str] = {}
    for variant in result.variants:
        observed.update(compact_metrics(dict(variant.metrics), prefix=variant.name))
    return EvaluationFeedback(
        verdict="accept" if verdict == "supported" else "continue",
        summary=summary,
        observed_metrics=observed,
        result_paths=[reflection.reflection_path],
        branch=plan.branch or "main",
        code_session_id=f"code_implementation-{plan.run_id}",
        evaluator_session_id=f"reflection-{plan.run_id}-{plan.revision}",
        evaluated_at=utc_now(),
    )


class ReflectionAdapter:
    module: ModuleId = "reflection"

    def __init__(self, agent: ReflectionAgent):
        self.agent = agent

    async def ainvoke(
        self,
        contract: SubtaskContract,
        state: PersistedManagerState,
        *,
        round_index: int,
        attempt: int,
    ) -> SubagentReport:
        plan = state.task_state.latest_plan
        result = state.latest_execution
        if plan is None or result is None:
            raise RuntimeError("reflection requires plan and execution result")
        started = time.monotonic()
        extra = (
            "## Manager subtask contract\n\n"
            f"{_contract_brief(contract)}\n\n"
        )
        original_prompt = getattr(self.agent, "_build_task_prompt", None)

        def _prompt_with_contract(
            inputs, hypothesis_text, objective_text, design_context, target_filename
        ):
            return extra + original_prompt(
                inputs, hypothesis_text, objective_text, design_context, target_filename
            )

        try:
            if original_prompt is not None:
                self.agent._build_task_prompt = _prompt_with_contract  # type: ignore[method-assign]
            output = await self.agent.arun(
                ReflectionInput(
                    plan=plan, result=result, implementation=state.latest_implementation
                )
            )
        except Exception as exc:  # noqa: BLE001
            return _report(
                module=self.module,
                mode="run",
                round_index=round_index,
                attempt=attempt,
                outcome="failed",
                summary=str(exc),
                retryable=True,
                duration_ms=int((time.monotonic() - started) * 1000),
                runtime_failure=_normalize_failure(exc),
                related_report_ids=contract.related_report_ids,
            )
        finally:
            if original_prompt is not None:
                self.agent._build_task_prompt = original_prompt  # type: ignore[method-assign]
        reflection = output.reflection
        verdict = _reflection_verdict(reflection.content)
        summary = bounded_text(reflection.content, state.task_state.limits.excerpt_chars)
        state.latest_reflection = reflection
        state.task_state.latest_evaluation = _reflection_feedback(
            plan=plan,
            result=result,
            reflection=reflection,
            verdict=verdict,
            summary=summary,
        )
        return _report(
            module=self.module,
            mode="run",
            round_index=round_index,
            attempt=attempt,
            outcome="succeeded",
            summary=summary,
            artifact_paths=[reflection.reflection_path],
            duration_ms=int((time.monotonic() - started) * 1000),
            related_report_ids=contract.related_report_ids,
            handoff=ReflectionHandoff(
                verdict=verdict,  # type: ignore[arg-type]
                summary=summary,
                reflection_path=reflection.reflection_path,
            ),
        )


class ReportingAdapter:
    module: ModuleId = "reporting"

    def __init__(self, agent: ReportingAgent):
        self.agent = agent

    async def ainvoke(
        self,
        contract: SubtaskContract,
        state: PersistedManagerState,
        *,
        round_index: int,
        attempt: int,
    ) -> SubagentReport:
        plan = state.task_state.latest_plan
        result = state.latest_execution
        research_paths = state.task_state.research_paths
        if not research_paths or plan is None or result is None:
            raise RuntimeError("reporting requires research_paths, plan, and execution result")
        started = time.monotonic()
        try:
            output = await self.agent.arun(
                ReportingInput(
                    survey=ResearchBrief(resource_paths=research_paths),
                    plan=plan,
                    result=result,
                    reflection=state.latest_reflection,
                    repair_instruction=contract.repair_instruction,
                    attempt=attempt,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return _report(
                module=self.module,
                mode="run",
                round_index=round_index,
                attempt=attempt,
                outcome="failed",
                summary=str(exc),
                retryable=True,
                duration_ms=int((time.monotonic() - started) * 1000),
                runtime_failure=_normalize_failure(exc),
                related_report_ids=contract.related_report_ids,
            )
        # check paper compile status
        succeeded = output.status == "compiled"
        rel_path = _safe_rel(output.paper_pdf_path) if succeeded and output.paper_pdf_path else None
        summary = bounded_text(
            output.notes or (f"paper {output.status}" if succeeded else "reporting did not produce a compiled paper"),
            state.task_state.limits.excerpt_chars,
        )
        return _report(
            module=self.module,
            mode="run",
            round_index=round_index,
            attempt=attempt,
            outcome="succeeded" if succeeded else "failed",
            summary=summary,
            retryable=not succeeded,
            artifact_paths=[rel_path] if rel_path else [],
            duration_ms=int((time.monotonic() - started) * 1000),
            related_report_ids=contract.related_report_ids,
            handoff=ReportHandoff(report_path=rel_path or ""),
        )


@dataclass
class SubagentRegistry:
    adapters: dict[ModuleId, SubagentAdapter]
    implementation: Any | None = None
    execution_result: Any | None = None
    reflection: Any | None = None

    def get(self, module: ModuleId) -> SubagentAdapter:
        adapter = self.adapters.get(module)
        if adapter is None:
            raise RuntimeError(f"module {module} is not registered")
        return adapter


def build_registry(
    config: dict[str, Any],
    *,
    topic_survey: TopicSurveyAgent | None = None,
    experiment_design: ExperimentDesignAgent | None = None,
    code_implementation: CodeImplementationAgent | None = None,
    experiment_execution: ExperimentExecutionAgent | None = None,
    reflection: ReflectionAgent | None = None,
    reporting: ReportingAgent | None = None,
    enabled: list[ModuleId] | None = None,
) -> SubagentRegistry:
    allowed = set(enabled or [])
    adapters: dict[ModuleId, SubagentAdapter] = {}
    if "topic_survey" in allowed:
        adapters["topic_survey"] = TopicSurveyAdapter(
            topic_survey or TopicSurveyAgent(config)
        )
    if "experiment_design" in allowed:
        adapters["experiment_design"] = ExperimentDesignAdapter(
            experiment_design or ExperimentDesignAgent(config)
        )
    if "code_implementation" in allowed:
        adapters["code_implementation"] = CodeImplementationAdapter(
            code_implementation or CodeImplementationAgent(config)
        )
    if "experiment_execution" in allowed:
        adapters["experiment_execution"] = ExperimentExecutionAdapter(
            experiment_execution or ExperimentExecutionAgent(config)
        )
    if "reflection" in allowed:
        if reflection is None:
            raise RuntimeError("reflection is enabled but no reflection agent was provided")
        adapters["reflection"] = ReflectionAdapter(reflection)
    if "reporting" in allowed:
        adapters["reporting"] = ReportingAdapter(reporting or ReportingAgent(config))
    if not adapters:
        raise RuntimeError("manager registry has no enabled modules")
    return SubagentRegistry(adapters=adapters)
