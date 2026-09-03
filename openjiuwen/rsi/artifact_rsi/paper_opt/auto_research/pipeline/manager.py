"""Resumable manager loop: decide → validate → route existing modules → persist."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.ids import new_run_id
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.logging import configure_run_logging, get_logger, log_context
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    find_harness_run_dirs,
    module_attempt_dir,
    resolve_project_reference,
    to_project_relative,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.agent import ManagerAgent, render_manager_query
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.artifacts import (
    append_event,
    append_round,
    bounded_text,
    save_state,
    try_load_state,
    write_crash_terminal,
    write_manager_round_snapshot,
    write_terminal_report,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import (
    ArtifactRecord,
    BudgetCounters,
    CodeHandoff,
    ExecutionHandoff,
    FactRecord,
    ManagedRound,
    ManagerDecision,
    ManagerSnapshot,
    ModuleId,
    OriginalTask,
    PersistedManagerState,
    ReflectionHandoff,
    ReportHandoff,
    RoutingHint,
    SubagentReport,
    SubtaskContract,
    SurveyHandoff,
    TaskState,
    TerminalReport,
    default_requirements,
    limits_from_config,
    report_requirement,
    utc_now,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reflection.agent import ReflectionAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.agent import ReportingAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.hitl import (
    apply_resume_steering,
    discard_in_flight_round,
    inject_operator_followup,
    persist_pause,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.subagents import SubagentRegistry, build_registry

logger = get_logger(__name__)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.transitions import (
    DecisionValidationError,
    apply_state_changes,
    can_complete,
    list_legal_actions,
    remaining_code_retries,
    remaining_execution_retries,
    remaining_reporting_retries,
    sanitize_execute_state_changes,
    select_report_context,
    sync_host_requirements,
    validate_decision,
)


def _enabled_modules(config: dict[str, Any], *, has_reflection: bool) -> list[ModuleId]:
    manager_cfg = dict(config.get("manager") or {})
    raw = dict(manager_cfg.get("modules") or {})
    defaults: dict[str, bool] = {
        "topic_survey": True,
        "experiment_design": True,
        "code_implementation": True,
        "experiment_execution": True,
        "reflection": has_reflection,
        "reporting": True,
    }
    enabled: list[ModuleId] = []
    for name, default in defaults.items():
        flag = raw.get(name, default)
        if flag and (name != "reflection" or has_reflection):
            enabled.append(name)  # type: ignore[arg-type]
    return enabled


def _initial_research_paths(task: OriginalTask) -> list[str]:
    paths: list[str] = []
    for raw in task.initial_research_paths:
        resolved = resolve_project_reference(raw)
        if resolved.is_file() or resolved.is_dir():
            paths.append(raw.replace("\\", "/"))
    return paths


def build_initial_state(
    task: OriginalTask,
    *,
    config: dict[str, Any],
    has_reflection: bool = False,
) -> PersistedManagerState:
    enabled = _enabled_modules(config, has_reflection=has_reflection)
    missing = [] if has_reflection else ["reflection"]
    requirements = default_requirements(task.topic)
    if "reporting" in enabled:
        # Conditional, not baked into default_requirements(): that function
        # has no notion of enabled_modules, and an unconditional req-report
        # would be permanently unsatisfiable whenever reporting is disabled.
        requirements.append(report_requirement())
    research_paths = _initial_research_paths(task)
    artifacts: list[ArtifactRecord] = []
    facts: list[FactRecord] = []
    if research_paths:
        artifacts.append(
            ArtifactRecord(
                id="art-initial-research",
                kind="research",
                path=research_paths[0],
                status="completed",
                notes="caller-supplied research paths",
            )
        )
        facts.append(
            FactRecord(
                id="fact-initial-research",
                text=f"{len(research_paths)} initial research path(s) provided",
                status="completed",
            )
        )
    return PersistedManagerState(
        original_task=task,
        task_state=TaskState(
            run_id=task.run_id,
            phase="start",
            requirements=requirements,
            artifacts=artifacts,
            facts=facts,
            research_paths=research_paths,
            counters=BudgetCounters(),
            limits=limits_from_config(config.get("manager")),
            enabled_modules=enabled,
            missing_capabilities=missing,
        ),
    )


def _upsert_fact(
    task: TaskState,
    fact_id: str,
    text: str,
    *,
    report_id: str | None = None,
) -> None:
    existing = next((item for item in task.facts if item.id == fact_id), None)
    if existing is None:
        existing = FactRecord(id=fact_id, text=text, status="completed")
        task.facts.append(existing)
    else:
        existing.text = text
        existing.status = "completed"
    if report_id and report_id not in existing.supporting_report_ids:
        existing.supporting_report_ids.append(report_id)


def apply_report_effects(state: PersistedManagerState, report: SubagentReport) -> None:
    """Host-owned fact/artifact updates from a completed module report."""
    try:
        _apply_report_effects(state, report)
    finally:
        sync_host_requirements(state)


def _apply_report_effects(state: PersistedManagerState, report: SubagentReport) -> None:
    task = state.task_state
    if report.module == "topic_survey":
        task.counters.survey_calls += 1
        if report.outcome == "succeeded" and isinstance(report.handoff, SurveyHandoff):
            for path in [report.handoff.research_summary_path, *report.handoff.source_paths]:
                if path and path not in task.research_paths:
                    task.research_paths.append(path)
            task.artifacts.append(
                ArtifactRecord(
                    id=f"art-survey-{report.round_index}",
                    kind="survey_summary",
                    path=report.handoff.research_summary_path,
                    status="completed",
                    produced_by_report_id=report.report_id,
                )
            )
            if task.latest_plan is not None:
                task.pending_research_revision = True
            task.phase = "survey"
            if report.handoff.evidence_gaps:
                issue = "survey evidence gaps: " + "; ".join(report.handoff.evidence_gaps[:3])
                if issue not in task.unresolved_issues:
                    task.unresolved_issues.append(issue)
            else:
                task.unresolved_issues = [
                    item for item in task.unresolved_issues if not item.startswith("survey evidence gaps")
                ]
        _upsert_fact(
            task,
            "fact-latest-survey",
            bounded_text(report.summary, 400),
            report_id=report.report_id,
        )
        return

    if report.module == "experiment_design":
        if report.mode in {"update", "revise_research"}:
            task.counters.design_revisions += 1
        if report.outcome == "succeeded":
            if report.mode == "revise_research":
                task.pending_research_revision = False
            task.phase = "design"
            if task.latest_plan is not None:
                task.artifacts.append(
                    ArtifactRecord(
                        id=f"art-design-{report.round_index}",
                        kind="experiment_design",
                        path=task.latest_plan.design_path,
                        status="completed",
                        produced_by_report_id=report.report_id,
                    )
                )
        _upsert_fact(
            task,
            "fact-latest-design",
            bounded_text(report.summary, 400),
            report_id=report.report_id,
        )
        return

    if report.module == "code_implementation":
        task.counters.code_attempts += 1
        if state.latest_implementation is not None:
            task.latest_implementation_status = state.latest_implementation.status
        task.phase = "code"
        if report.outcome != "succeeded":
            issue = f"code implementation failed: {report.summary}"
            task.unresolved_issues = [item for item in task.unresolved_issues if not item.startswith("code implementation")]
            task.unresolved_issues.append(issue)
        else:
            task.unresolved_issues = [
                item for item in task.unresolved_issues if not item.startswith("code implementation")
            ]
        readiness = "failed"
        if isinstance(report.handoff, CodeHandoff) and report.handoff.readiness:
            readiness = report.handoff.readiness
        elif report.outcome == "succeeded":
            readiness = "smoke_ready"
        _upsert_fact(
            task,
            "fact-latest-implementation",
            bounded_text(f"{readiness}: {report.summary}", 400),
            report_id=report.report_id,
        )
        return

    if report.module == "experiment_execution":
        task.counters.execution_attempts += 1
        if report.outcome == "succeeded":
            process_status = "completed"
        elif isinstance(report.handoff, ExecutionHandoff) and report.handoff.process_status:
            process_status = report.handoff.process_status
        elif state.latest_execution is not None:
            process_status = state.latest_execution.status
        else:
            process_status = "failed"
        task.latest_execution_status = process_status
        task.phase = "execute"
        if report.outcome != "succeeded":
            issue = f"execution failed: {report.summary}"
            task.unresolved_issues = [
                item for item in task.unresolved_issues if not item.startswith("execution failed")
            ]
            task.unresolved_issues.append(issue)
        else:
            task.unresolved_issues = [
                item for item in task.unresolved_issues if not item.startswith("execution failed")
            ]
        science = ""
        if isinstance(report.handoff, ExecutionHandoff):
            science = report.handoff.scientific_status
        _upsert_fact(
            task,
            "fact-latest-execution",
            bounded_text(
                f"process={process_status} science={science or 'unknown'}: {report.summary}",
                400,
            ),
            report_id=report.report_id,
        )
        return

    if report.module == "reflection" and report.outcome == "succeeded":
        task.phase = "reflect"
        if state.latest_reflection is not None:
            task.artifacts.append(
                ArtifactRecord(
                    id=f"art-reflection-{report.round_index}",
                    kind="reflection",
                    path=state.latest_reflection.reflection_path,
                    status="completed",
                    produced_by_report_id=report.report_id,
                )
            )
        verdict = ""
        if isinstance(report.handoff, ReflectionHandoff):
            verdict = report.handoff.verdict
        _upsert_fact(
            task,
            "fact-latest-reflection",
            bounded_text(f"verdict={verdict}: {report.summary}", 400),
            report_id=report.report_id,
        )
        return

    if report.module == "reporting":
        task.counters.reporting_attempts += 1
        if report.outcome == "succeeded":
            task.phase = "report"
            if isinstance(report.handoff, ReportHandoff):
                task.artifacts.append(
                    ArtifactRecord(
                        id=f"art-report-{report.round_index}",
                        kind="report",
                        path=report.handoff.report_path,
                        status="completed",
                        produced_by_report_id=report.report_id,
                    )
                )


def _terminal(
    state: PersistedManagerState,
    *,
    status: str,
    abort_reason: str,
    summary: str,
    failure_reason: str = "",
    completion_satisfied: bool = False,
) -> TerminalReport:
    report = TerminalReport(
        status=status,  # type: ignore[arg-type]
        run_id=state.task_state.run_id,
        rounds_run=len(state.rounds),
        abort_reason=abort_reason,
        failure_reason=failure_reason,
        summary=summary,
        completion_satisfied=completion_satisfied,
    )
    state.terminal = report
    state.task_state.phase = "terminal"
    write_terminal_report(state)
    return report


class ManagerRuntime:
    """Trusted host around the manager agent and existing module adapters."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        manager: ManagerAgent | None = None,
        registry: SubagentRegistry | None = None,
        reflection: ReflectionAgent | None = None,
        reporting: ReportingAgent | None = None,
        topic_survey=None,
        experiment_design=None,
        code_implementation=None,
        experiment_execution=None,
    ):
        self.config = config
        self.reflection = reflection
        enabled = _enabled_modules(config, has_reflection=reflection is not None)
        self.registry = registry or build_registry(
            config,
            topic_survey=topic_survey,
            experiment_design=experiment_design,
            code_implementation=code_implementation,
            experiment_execution=experiment_execution,
            reflection=reflection,
            reporting=reporting,
            enabled=enabled,
        )
        self.manager = manager or ManagerAgent(config)
        self._enabled = enabled

    def _routing_hint(self, state: PersistedManagerState) -> RoutingHint:
        task = state.task_state
        known = [item.id for item in (*task.requirements, *task.artifacts, *task.facts)]
        latest_metrics: dict[str, Any] = {}
        variant_metrics: dict[str, dict[str, Any]] = {}
        process_status = task.latest_execution_status or ""
        scientific_status = ""
        failure_kind = ""
        failure_stage = ""
        failure_substage = ""
        failure_class = ""
        fingerprint = ""
        diagnostic_paths: list[str] = []
        for report in reversed(state.reports):
            if report.module != "experiment_execution":
                continue
            handoff = report.handoff
            if isinstance(handoff, ExecutionHandoff):
                process_status = handoff.process_status or process_status
                scientific_status = handoff.scientific_status
                failure_kind = handoff.failure_kind
                failure_stage = handoff.failure_stage
                failure_substage = handoff.failure_substage
                failure_class = handoff.failure_class
                fingerprint = handoff.fingerprint
                diagnostic_paths = list(handoff.diagnostic_paths)
                if handoff.diagnostic:
                    latest_metrics = dict(handoff.diagnostic)
                    failure_stage = failure_stage or str(handoff.diagnostic.get("failure_stage") or "")
                    failure_substage = failure_substage or str(
                        handoff.diagnostic.get("failure_substage") or ""
                    )
                    fingerprint = fingerprint or str(handoff.diagnostic.get("fingerprint") or "")
                if handoff.variants:
                    variant_metrics = {
                        item.name: dict(item.metrics) for item in handoff.variants
                    }
                    proposed = next(
                        (item for item in handoff.variants if item.name == "proposed"),
                        None,
                    )
                    primary = proposed or handoff.variants[0]
                    latest_metrics = {**dict(primary.metrics), **latest_metrics}
                    for item in handoff.variants:
                        path = getattr(item, "diagnostics_path", "") or ""
                        if path and path not in diagnostic_paths:
                            diagnostic_paths.append(path)
                break
            break
        complete_ok, complete_reason = can_complete(state)
        return RoutingHint(
            remaining_rounds=max(0, task.limits.max_rounds - task.counters.rounds_used),
            remaining_code_retries=remaining_code_retries(task),
            remaining_execution_retries=remaining_execution_retries(task),
            remaining_survey_calls=max(0, task.limits.max_survey_calls - task.counters.survey_calls),
            remaining_design_revisions=max(
                0, task.limits.max_design_revisions - task.counters.design_revisions
            ),
            remaining_reporting_retries=remaining_reporting_retries(task),
            known_record_ids=known,
            legal_actions=list_legal_actions(task, state.reports),
            can_complete=complete_ok,
            can_complete_reason="" if complete_ok else complete_reason,
            latest_metrics=latest_metrics,
            latest_process_status=process_status,
            latest_scientific_status=scientific_status,
            latest_failure_kind=failure_kind,
            latest_failure_stage=failure_stage,
            latest_failure_substage=failure_substage,
            latest_failure_class=failure_class,
            latest_failure_fingerprint=fingerprint,
            diagnostic_paths=diagnostic_paths,
            variant_metrics=variant_metrics,
        )

    def _snapshot(self, state: PersistedManagerState, round_index: int) -> ManagerSnapshot:
        related = []
        if state.task_state.last_contract is not None:
            related = state.task_state.last_contract.related_report_ids
        reports = select_report_context(state.reports, related)
        return ManagerSnapshot(
            original_task=state.original_task,
            task_state=state.task_state,
            reports=reports,
            round_index=round_index,
            validation_feedback=state.pending_decision_feedback,
            routing=self._routing_hint(state),
            operator_followups=list(state.operator_followups),
        )

    async def _decide_with_repair(
        self, state: PersistedManagerState, round_index: int
    ) -> ManagerDecision:
        limits = state.task_state.limits
        last_error = ""
        run_id = state.task_state.run_id
        for attempt in range(limits.max_decision_retries + 1):
            snapshot = self._snapshot(state, round_index)
            query = render_manager_query(snapshot)
            write_manager_round_snapshot(run_id, round_index, snapshot, query)
            with log_context(
                run_id=run_id,
                round_index=round_index,
                module="manager",
                attempt=attempt + 1,
                report_id=f"manager:{round_index}:{attempt + 1}",
            ):
                try:
                    decision = await self.manager.adecide(snapshot)
                    if decision.signal == "EXECUTE":
                        kept, dropped = sanitize_execute_state_changes(
                            state.task_state, decision.state_changes
                        )
                        if dropped:
                            decision = decision.model_copy(update={"state_changes": kept})
                            append_event(
                                run_id,
                                "manager_state_change_dropped",
                                {
                                    "round": round_index,
                                    "dropped": dropped,
                                },
                            )
                    validate_decision(state, decision)
                    state.pending_decision_feedback = ""
                    state.task_state.counters.decision_retries = attempt
                    return decision
                except (DecisionValidationError, Exception) as exc:  # noqa: BLE001
                    last_error = str(exc)
                    state.pending_decision_feedback = last_error
                    state.task_state.counters.decision_retries = attempt + 1
                    append_event(
                        run_id,
                        "manager_decision_rejected",
                        {"round": round_index, "attempt": attempt + 1, "error": last_error},
                    )
        raise DecisionValidationError(
            f"manager decision failed after {limits.max_decision_retries + 1} attempts: {last_error}"
        )

    async def _execute_contract(
        self,
        state: PersistedManagerState,
        contract: SubtaskContract,
        round_index: int,
    ) -> SubagentReport:
        adapter = self.registry.get(contract.module)
        attempt = 1
        if contract.module == "code_implementation":
            attempt = state.task_state.counters.code_attempts + 1
        elif contract.module == "experiment_execution":
            attempt = state.task_state.counters.execution_attempts + 1
        elif contract.module == "topic_survey":
            attempt = state.task_state.counters.survey_calls + 1
        elif contract.module == "reporting":
            attempt = state.task_state.counters.reporting_attempts + 1
        run_id = state.task_state.run_id
        report_id = f"{contract.module}:{round_index}:{attempt}"
        attempt_dir = module_attempt_dir(run_id, contract.module, round_index, attempt)
        attempt_rel = to_project_relative(attempt_dir)
        append_event(
            run_id,
            "subagent_start",
            {
                "round": round_index,
                "round_index": round_index,
                "module": contract.module,
                "attempt": attempt,
                "report_id": report_id,
                "mode": contract.mode,
                "attempt_dir": attempt_rel,
            },
        )
        logger.info(
            "subagent_start module=%s round=%s attempt=%s",
            contract.module,
            round_index,
            attempt,
        )
        started = time.monotonic()
        with log_context(
            run_id=run_id,
            round_index=round_index,
            module=contract.module,
            attempt=attempt,
            report_id=report_id,
        ) as ctx:
            try:
                report = await adapter.ainvoke(
                    contract, state, round_index=round_index, attempt=attempt
                )
            except Exception as exc:  # noqa: BLE001
                duration_ms = int((time.monotonic() - started) * 1000)
                append_event(
                    run_id,
                    "subagent_exception",
                    {
                        "round": round_index,
                        "round_index": round_index,
                        "module": contract.module,
                        "attempt": attempt,
                        "report_id": report_id,
                        "duration_ms": duration_ms,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "attempt_dir": attempt_rel,
                        "trace_id": ctx.trace_id if ctx is not None else "",
                    },
                )
                logger.exception(
                    "subagent_exception module=%s round=%s attempt=%s",
                    contract.module,
                    round_index,
                    attempt,
                )
                raise
            duration_ms = int((time.monotonic() - started) * 1000)
            artifact_refs = _attempt_artifact_refs(
                run_id,
                contract.module,
                round_index,
                attempt,
                report.artifact_paths,
            )
            if artifact_refs and artifact_refs != list(report.artifact_paths):
                report = report.model_copy(update={"artifact_paths": artifact_refs})
            append_event(
                run_id,
                "subagent_finish",
                {
                    "round": round_index,
                    "round_index": round_index,
                    "module": contract.module,
                    "attempt": attempt,
                    "report_id": report.report_id or report_id,
                    "outcome": report.outcome,
                    "duration_ms": report.duration_ms or duration_ms,
                    "attempt_dir": attempt_rel,
                    "artifact_refs": artifact_refs,
                    "trace_id": ctx.trace_id if ctx is not None else "",
                },
            )
            logger.info(
                "subagent_finish module=%s round=%s attempt=%s outcome=%s duration_ms=%s",
                contract.module,
                round_index,
                attempt,
                report.outcome,
                report.duration_ms or duration_ms,
            )
            return report

    async def arun(
        self,
        *,
        topic: str,
        research_paths: list[str] | None = None,
        run_id: str | None = None,
        resume: bool = False,
        objective: str = "",
        constraints: list[str] | None = None,
        initial_prompt: str = "",
        task_mode: str = "create_new_paper",
        followup: str = "",
    ) -> TerminalReport:
        existing = try_load_state(run_id) if resume and run_id else None
        if resume and run_id and existing is None:
            raise FileNotFoundError(f"no manager state to resume for run_id={run_id!r}")
        is_new_run = False
        if existing is not None:
            state = existing
            should_continue = apply_resume_steering(state, followup=followup)
            if not should_continue:
                return state.terminal  # type: ignore[return-value]
        else:
            task = OriginalTask(
                topic=topic,
                objective=objective,
                constraints=list(constraints or []),
                initial_prompt=initial_prompt,
                task_mode=task_mode,
                initial_research_paths=list(research_paths or []),
                run_id=run_id or new_run_id(),
            )
            state = build_initial_state(
                task, config=self.config, has_reflection=self.reflection is not None
            )
            if followup.strip():
                inject_operator_followup(state, followup, source="cli")
            save_state(state)
            is_new_run = True

        configure_run_logging(state.task_state.run_id, self.config)
        with log_context(run_id=state.task_state.run_id, module="manager"):
            if is_new_run:
                append_event(state.task_state.run_id, "manager_start", {"topic": topic})
            try:
                return await self._run_loop(state)
            except asyncio.CancelledError:
                persist_pause(state)
                raise
            except Exception as exc:  # noqa: BLE001 - convert crashes to durable terminal records
                return write_crash_terminal(
                    run_id=state.task_state.run_id,
                    status="failed",
                    reason=f"management loop crashed: {exc}",
                    existing=state,
                    exception_type=type(exc).__name__,
                )

    async def _run_loop(self, state: PersistedManagerState) -> TerminalReport:
        if state.task_state.pending_contract is not None:
            payload = discard_in_flight_round(state)
            save_state(state)
            append_event(state.task_state.run_id, "manager_discard_in_flight", payload)
        limits = state.task_state.limits
        while state.task_state.counters.rounds_used < limits.max_rounds:
            sync_host_requirements(state)
            round_index = state.task_state.counters.rounds_used + 1
            started = utc_now()
            try:
                decision = await self._decide_with_repair(state, round_index)
            except DecisionValidationError as exc:
                return _terminal(
                    state,
                    status="blocked",
                    abort_reason="invalid_manager_decision",
                    summary=str(exc),
                    failure_reason=str(exc),
                )

            patched = apply_state_changes(state.task_state, decision.state_changes)
            state.task_state = patched

            if decision.signal == "DONE":
                ok, reason = can_complete(state)
                if not ok:
                    state.pending_decision_feedback = f"DONE rejected: {reason}"
                    save_state(state)
                    continue
                round_rec = ManagedRound(
                    round_index=round_index,
                    decision=decision,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                )
                state.rounds.append(round_rec)
                state.task_state.counters.rounds_used = round_index
                append_round(state)
                return _terminal(
                    state,
                    status="complete",
                    abort_reason="",
                    summary=decision.rationale,
                    completion_satisfied=True,
                )

            if decision.signal == "BLOCKED":
                round_rec = ManagedRound(
                    round_index=round_index,
                    decision=decision,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                )
                state.rounds.append(round_rec)
                state.task_state.counters.rounds_used = round_index
                append_round(state)
                return _terminal(
                    state,
                    status="blocked",
                    abort_reason="manager_blocked",
                    summary=decision.blocked_reason or decision.rationale,
                )

            contract = decision.contract
            assert contract is not None
            state.task_state.last_contract = contract
            state.task_state.pending_contract = contract
            state.task_state.counters.rounds_used = round_index
            save_state(state)
            append_event(
                state.task_state.run_id,
                "manager_execute",
                {"round": round_index, "module": contract.module, "mode": contract.mode},
            )
            report = await self._execute_contract(state, contract, round_index)
            await self._finish_round_from_report(state, round_index, decision, contract, report)
            if state.terminal is not None:
                return state.terminal

        return _terminal(
            state,
            status="incomplete",
            abort_reason="max_rounds_exhausted",
            summary=f"exhausted {limits.max_rounds} manager rounds",
        )

    async def _finish_round_from_report(
        self,
        state: PersistedManagerState,
        round_index: int,
        decision: ManagerDecision | None,
        contract: SubtaskContract,
        report: SubagentReport,
    ) -> TerminalReport | None:
        apply_report_effects(state, report)
        state.reports.append(report)
        state.task_state.pending_contract = None
        if decision is None:
            decision = ManagerDecision(
                signal="EXECUTE",
                rationale="resumed pending contract",
                contract=contract,
            )
        round_rec = ManagedRound(
            round_index=round_index,
            decision=decision,
            contract=contract,
            related_report_ids=list(contract.related_report_ids),
            report=report,
            started_at=utc_now(),
            finished_at=datetime.now(UTC),
        )
        # Avoid duplicating a resumed round that was already appended.
        if not state.rounds or state.rounds[-1].round_index != round_index:
            state.rounds.append(round_rec)
        else:
            state.rounds[-1] = round_rec
        append_round(state)
        save_state(state)
        append_event(
            state.task_state.run_id,
            "module_report",
            {
                "round": round_index,
                "round_index": round_index,
                "report_id": report.report_id,
                "outcome": report.outcome,
                "module": report.module,
                "attempt": report.attempt,
                "duration_ms": report.duration_ms,
                "artifact_paths": list(report.artifact_paths),
            },
        )
        return None

    def run(self, **kwargs: Any) -> TerminalReport:
        return asyncio.run(self.arun(**kwargs))


def _attempt_artifact_refs(
    run_id: str,
    module: str,
    round_index: int,
    attempt: int,
    extra: list[str] | None = None,
) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        if path and path not in seen:
            seen.add(path)
            refs.append(path)

    attempt_dir = module_attempt_dir(run_id, module, round_index, attempt)
    if attempt_dir.is_dir():
        _add(to_project_relative(attempt_dir))
        trace = attempt_dir / "agent_trace.jsonl"
        if trace.is_file():
            _add(to_project_relative(trace))
        for child in sorted(attempt_dir.iterdir()):
            if child.is_file():
                _add(to_project_relative(child))
    for path in extra or []:
        _add(path)
    for harness in find_harness_run_dirs(run_id):
        try:
            _add(to_project_relative(harness))
        except ValueError:
            continue
    return refs
