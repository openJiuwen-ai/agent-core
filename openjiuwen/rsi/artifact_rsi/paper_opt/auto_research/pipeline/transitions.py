"""Host-side validation for manager decisions, contracts, and DONE claims."""

from __future__ import annotations

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import (
    ArtifactRecord,
    CodeHandoff,
    ExecutionHandoff,
    FactRecord,
    ManagerDecision,
    ModuleId,
    ModuleMode,
    PersistedManagerState,
    RecordStatus,
    RequirementRecord,
    StateChange,
    SubagentReport,
    SubtaskContract,
    TaskState,
)


class DecisionValidationError(ValueError):
    """Illegal manager decision or contract; retryable control-format error."""


def _requirement(state: TaskState, record_id: str) -> RequirementRecord | None:
    return next((item for item in state.requirements if item.id == record_id), None)


def _artifact(state: TaskState, record_id: str) -> ArtifactRecord | None:
    return next((item for item in state.artifacts if item.id == record_id), None)


def _fact(state: TaskState, record_id: str) -> FactRecord | None:
    return next((item for item in state.facts if item.id == record_id), None)


def _report_by_id(reports: list[SubagentReport], report_id: str) -> SubagentReport | None:
    return next((item for item in reports if item.report_id == report_id), None)


def _successful_report_ids(reports: list[SubagentReport]) -> set[str]:
    return {item.report_id for item in reports if item.outcome == "succeeded"}


def _latest_module_report(reports: list[SubagentReport], module: ModuleId) -> SubagentReport | None:
    return next((item for item in reversed(reports) if item.module == module), None)


def _latest_succeeded(
    reports: list[SubagentReport],
    module: ModuleId,
    *,
    modes: frozenset[ModuleMode] | None = None,
) -> SubagentReport | None:
    for item in reversed(reports):
        if item.module != module or item.outcome != "succeeded":
            continue
        if modes is not None and item.mode not in modes:
            continue
        return item
    return None


def _round_index(report: SubagentReport | None) -> int:
    return report.round_index if report is not None else -1


def _code_is_ready(state: TaskState, reports: list[SubagentReport]) -> bool:
    last = _latest_module_report(reports, "code_implementation")
    if last is not None and last.outcome == "failed":
        return False
    if last is not None and isinstance(last.handoff, CodeHandoff):
        if (
            last.handoff.status == "ready"
            or last.handoff.smoke_test_passed
            or last.handoff.readiness == "smoke_ready"
        ):
            return True
        if last.handoff.status == "failed":
            return False
    return state.latest_implementation_status == "ready"


def _unexecuted_ready_code(state: TaskState, reports: list[SubagentReport]) -> bool:
    if not _code_is_ready(state, reports):
        return False
    last_code = _latest_module_report(reports, "code_implementation")
    if last_code is None:
        return False
    last_exec = _latest_module_report(reports, "experiment_execution")
    return _round_index(last_code) > _round_index(last_exec)


def _design_newer_than_code(reports: list[SubagentReport]) -> bool:
    last_design = _latest_succeeded(reports, "experiment_design")
    if last_design is None:
        return False
    last_code = _latest_module_report(reports, "code_implementation")
    return _round_index(last_design) > _round_index(last_code)


def _redesign_after_execution(reports: list[SubagentReport]) -> bool:
    last_redesign = _latest_succeeded(
        reports,
        "experiment_design",
        modes=frozenset({"update", "revise_research"}),
    )
    last_exec = _latest_module_report(reports, "experiment_execution")
    return _round_index(last_redesign) > _round_index(last_exec)


def _process_failed(state: TaskState, reports: list[SubagentReport]) -> bool:
    last_exec = _latest_module_report(reports, "experiment_execution")
    if state.latest_execution_status == "failed":
        return True
    if last_exec is not None and last_exec.outcome == "failed":
        return True
    return isinstance(last_exec, SubagentReport) and isinstance(
        last_exec.handoff, ExecutionHandoff
    ) and last_exec.handoff.process_status == "failed"


def _process_completed(state: TaskState) -> bool:
    return state.latest_execution_status == "completed"


def _latest_scientific_status(reports: list[SubagentReport]) -> str:
    last_exec = _latest_module_report(reports, "experiment_execution")
    if isinstance(last_exec, SubagentReport) and isinstance(last_exec.handoff, ExecutionHandoff):
        return last_exec.handoff.scientific_status
    return "unknown" if last_exec is not None else ""


def _science_accepted(reports: list[SubagentReport]) -> bool:
    return _latest_scientific_status(reports) == "accepted"


def _has_succeeded_survey(reports: list[SubagentReport]) -> bool:
    return any(item.module == "topic_survey" and item.outcome == "succeeded" for item in reports)


def remaining_code_retries(state: TaskState) -> int:
    attempts = state.counters.code_attempts
    if attempts <= 0:
        return state.limits.max_code_retries
    return max(0, state.limits.max_code_retries - (attempts - 1))


def remaining_execution_retries(state: TaskState) -> int:
    attempts = state.counters.execution_attempts
    if attempts <= 0:
        return state.limits.max_execution_retries
    return max(0, state.limits.max_execution_retries - (attempts - 1))


def remaining_reporting_retries(state: TaskState) -> int:
    attempts = state.counters.reporting_attempts
    if attempts <= 0:
        return state.limits.max_reporting_retries
    return max(0, state.limits.max_reporting_retries - (attempts - 1))


def _can_produce_new_evidence(state: TaskState) -> bool:
    """Whether a new design could still be implemented and run.

    A follow-up survey or design update is a dead end once either retry
    budget is spent: there is nothing left to implement it with, or nothing
    left to run it on. Without this check those actions stay nominally
    "legal" forever and keep the science loop looking open, blocking
    reporting even when the run has genuinely exhausted its resources.
    """
    code_ok = remaining_code_retries(state) > 0 or state.counters.code_attempts == 0
    exec_ok = remaining_execution_retries(state) > 0 or state.counters.execution_attempts == 0
    return code_ok and exec_ok


def validate_state_changes(
    state: TaskState,
    reports: list[SubagentReport],
    changes: list[StateChange],
) -> None:
    success_ids = _successful_report_ids(reports)
    for change in changes:
        if change.record_kind == "requirement" and _requirement(state, change.record_id) is None:
            raise DecisionValidationError(f"unknown requirement: {change.record_id}")
        if change.record_kind == "artifact" and _artifact(state, change.record_id) is None:
            raise DecisionValidationError(f"unknown artifact: {change.record_id}")
        if change.record_kind == "fact" and _fact(state, change.record_id) is None:
            raise DecisionValidationError(f"unknown fact: {change.record_id}")
        if change.status == "completed":
            cited = [rid for rid in change.supporting_report_ids if rid in success_ids]
            if not cited:
                raise DecisionValidationError(
                    f"cannot mark {change.record_id} completed without a successful report id"
                )


def sanitize_execute_state_changes(
    state: TaskState,
    changes: list[StateChange],
) -> tuple[list[StateChange], list[str]]:
    """Drop unknown fact/artifact patches on EXECUTE. Requirements stay strict."""
    kept: list[StateChange] = []
    dropped: list[str] = []
    for change in changes:
        if change.record_kind == "requirement":
            kept.append(change)
            continue
        known = (
            _artifact(state, change.record_id)
            if change.record_kind == "artifact"
            else _fact(state, change.record_id)
        )
        if known is None:
            dropped.append(f"{change.record_kind}:{change.record_id}")
            continue
        kept.append(change)
    return kept, dropped


def apply_state_changes(state: TaskState, changes: list[StateChange]) -> TaskState:
    reqs = {item.id: item.model_copy() for item in state.requirements}
    arts = {item.id: item.model_copy() for item in state.artifacts}
    facts = {item.id: item.model_copy() for item in state.facts}
    for change in changes:
        target: RequirementRecord | ArtifactRecord | FactRecord | None = None
        if change.record_kind == "requirement":
            target = reqs.get(change.record_id)
        elif change.record_kind == "artifact":
            target = arts.get(change.record_id)
        else:
            target = facts.get(change.record_id)
        if target is None:
            continue
        if change.status is not None:
            target.status = change.status
        if change.notes is not None and hasattr(target, "notes"):
            target.notes = change.notes
        if change.supporting_report_ids:
            existing = list(getattr(target, "supporting_report_ids", []))
            for report_id in change.supporting_report_ids:
                if report_id not in existing:
                    existing.append(report_id)
            if hasattr(target, "supporting_report_ids"):
                target.supporting_report_ids = existing
        if change.text is not None and isinstance(target, FactRecord):
            target.text = change.text
    return state.model_copy(
        update={
            "requirements": [reqs[item.id] for item in state.requirements],
            "artifacts": [arts[item.id] for item in state.artifacts],
            "facts": [facts[item.id] for item in state.facts],
        }
    )


def _module_enabled(state: TaskState, module: ModuleId) -> bool:
    return module in state.enabled_modules


_LEGAL_PAIRS: tuple[tuple[ModuleId, ModuleMode], ...] = (
    ("topic_survey", "run"),
    ("experiment_design", "create"),
    ("experiment_design", "update"),
    ("experiment_design", "revise_research"),
    ("code_implementation", "run"),
    ("experiment_execution", "run"),
    ("reflection", "run"),
    ("reporting", "run"),
)

_PROBE_CONTRACT = SubtaskContract(
    module="topic_survey",
    mode="run",
    goal="probe legal action",
    acceptance_criteria=["host-validated"],
)


def _stuck_for_survey(state: TaskState, reports: list[SubagentReport]) -> bool:
    if _science_accepted(reports):
        return False
    if _process_completed(state) and not _redesign_after_execution(reports):
        return True
    return _process_failed(state, reports) and remaining_code_retries(state) <= 0


def _science_loop_open(state: TaskState, reports: list[SubagentReport]) -> bool:
    for module, mode in _LEGAL_PAIRS:
        if module == "reporting":
            continue
        probe = _PROBE_CONTRACT.model_copy(update={"module": module, "mode": mode})
        try:
            validate_contract(state, reports, probe)
        except DecisionValidationError:
            continue
        return True
    return False


def _code_needs_create(reports: list[SubagentReport]) -> bool:
    last_design = _latest_succeeded(reports, "experiment_design")
    last_code = _latest_module_report(reports, "code_implementation")
    if last_code is None:
        return _latest_module_report(reports, "experiment_execution") is None
    return _round_index(last_design) > _round_index(last_code)


def _code_needs_repair(state: TaskState, reports: list[SubagentReport]) -> bool:
    if not _code_is_ready(state, reports):
        last_code = _latest_module_report(reports, "code_implementation")
        return last_code is not None
    if not _process_failed(state, reports):
        return False
    last_code = _latest_module_report(reports, "code_implementation")
    last_exec = _latest_module_report(reports, "experiment_execution")
    return last_exec is not None and _round_index(last_code) < _round_index(last_exec)


def validate_contract(state: TaskState, reports: list[SubagentReport], contract: SubtaskContract) -> None:
    if not _module_enabled(state, contract.module):
        raise DecisionValidationError(f"module {contract.module} is not enabled")
    counters = state.counters
    limits = state.limits
    if counters.rounds_used >= limits.max_rounds:
        raise DecisionValidationError("round budget exhausted")

    if contract.module == "topic_survey":
        if counters.survey_calls >= limits.max_survey_calls:
            raise DecisionValidationError("survey call budget exhausted")
        if state.pending_research_revision:
            raise DecisionValidationError(
                "new research must be incorporated via revise_research before another survey"
            )
        if _unexecuted_ready_code(state, reports):
            raise DecisionValidationError("unexecuted ready implementation must run first")
        if _has_succeeded_survey(reports) and not _stuck_for_survey(state, reports):
            raise DecisionValidationError(
                "follow-up survey is only allowed when the experiment is stuck"
            )
        if _has_succeeded_survey(reports) and not _can_produce_new_evidence(state):
            raise DecisionValidationError(
                "no code/execution capacity remains to act on a new survey"
            )
        return

    if contract.module == "experiment_design":
        if contract.mode == "create":
            if state.latest_plan is not None:
                raise DecisionValidationError("design already exists; use revise_research or update")
            if not state.research_paths:
                raise DecisionValidationError("create requires research_paths")
            if "topic_survey" in state.enabled_modules:
                if not _has_succeeded_survey(reports):
                    raise DecisionValidationError(
                        "create requires a completed topic survey when topic_survey is enabled"
                    )
        elif contract.mode == "revise_research":
            if state.latest_plan is None:
                raise DecisionValidationError("revise_research requires an existing design")
            if not state.pending_research_revision:
                raise DecisionValidationError("no pending supplemental research to incorporate")
            if not _can_produce_new_evidence(state):
                raise DecisionValidationError(
                    "no code/execution capacity remains to act on a revised design"
                )
        elif contract.mode == "update":
            if state.latest_plan is None:
                raise DecisionValidationError("update requires an existing design")
            if state.latest_plan.status == "stopped":
                raise DecisionValidationError("refusing to update a terminal design")
            if counters.design_revisions >= limits.max_design_revisions:
                raise DecisionValidationError("design revision budget exhausted")
            if state.pending_research_revision:
                raise DecisionValidationError(
                    "new research must be incorporated via revise_research before update"
                )
            if _science_accepted(reports):
                raise DecisionValidationError("science already accepted; reporting is next")
            if not _can_produce_new_evidence(state):
                raise DecisionValidationError(
                    "no code/execution capacity remains to act on a design update"
                )
            last_redesign = _latest_succeeded(
                reports,
                "experiment_design",
                modes=frozenset({"update", "revise_research"}),
            )
            last_reflection = _latest_succeeded(reports, "reflection")
            last_exec = _latest_module_report(reports, "experiment_execution")
            reflection_fresh = _round_index(last_reflection) > _round_index(last_redesign)
            simple_fresh = (
                _process_completed(state)
                and _round_index(last_exec) > _round_index(last_redesign)
            )
            if not reflection_fresh and not simple_fresh:
                raise DecisionValidationError(
                    "design update requires newer reflection or a process-completed execution"
                )
        return

    if contract.module == "code_implementation":
        if state.latest_plan is None:
            raise DecisionValidationError("code implementation requires a design")
        if state.pending_research_revision:
            raise DecisionValidationError(
                "new research must be incorporated via revise_research before implementation"
            )
        if state.latest_plan.status == "stopped":
            raise DecisionValidationError("terminal design has no further implementation work")
        if remaining_code_retries(state) <= 0 and state.counters.code_attempts > 0:
            raise DecisionValidationError("code retry budget exhausted")
        if _unexecuted_ready_code(state, reports):
            raise DecisionValidationError("unexecuted ready implementation must run first")
        needs_create = _code_needs_create(reports)
        needs_repair = _code_needs_repair(state, reports)
        if not needs_create and not needs_repair:
            raise DecisionValidationError(
                "code implementation is for a new design or a smoke/execution error"
            )
        return

    if contract.module == "experiment_execution":
        if state.latest_implementation_status != "ready":
            raise DecisionValidationError("execution requires a ready implementation")
        if remaining_execution_retries(state) <= 0 and state.counters.execution_attempts > 0:
            raise DecisionValidationError("execution retry budget exhausted")
        if state.pending_research_revision:
            raise DecisionValidationError(
                "new research must be incorporated via revise_research before execution"
            )
        if _design_newer_than_code(reports):
            raise DecisionValidationError(
                "new design must be implemented before execution"
            )
        if _science_accepted(reports) and not _unexecuted_ready_code(state, reports):
            raise DecisionValidationError(
                "science already accepted; another execution requires a newer implementation"
            )
        last_exec = _latest_module_report(reports, "experiment_execution")
        last_code = _latest_module_report(reports, "code_implementation")
        if _process_failed(state, reports):
            if last_exec is not None and (
                last_code is None or last_code.round_index < last_exec.round_index
            ):
                raise DecisionValidationError(
                    "failed execution must be repaired via code_implementation before another execution"
                )
            return
        if _unexecuted_ready_code(state, reports):
            return
        if _process_completed(state) and not _design_newer_than_code(reports):
            return
        if last_exec is None and _code_is_ready(state, reports):
            return
        raise DecisionValidationError("execution requires a newer ready implementation")

    if contract.module == "reflection":
        if "reflection" in state.missing_capabilities:
            raise DecisionValidationError("reflection capability is missing")
        if state.latest_execution_status is None:
            raise DecisionValidationError("reflection requires an execution result")
        if state.latest_execution_status != "completed":
            raise DecisionValidationError(
                "reflection requires a completed execution; repair code after a failed run"
            )
        if state.pending_research_revision:
            raise DecisionValidationError(
                "new research must be incorporated via revise_research before reflection"
            )
        if _unexecuted_ready_code(state, reports):
            raise DecisionValidationError("unexecuted ready implementation must run first")
        if _redesign_after_execution(reports):
            raise DecisionValidationError("latest execution was already consumed by a design change")
        last_reflection = _latest_succeeded(reports, "reflection")
        last_exec = _latest_module_report(reports, "experiment_execution")
        if _round_index(last_reflection) >= _round_index(last_exec):
            raise DecisionValidationError("this execution was already reflected")
        return

    if contract.module == "reporting":
        if state.latest_execution_status is None:
            raise DecisionValidationError("reporting requires an execution result")
        if state.latest_execution_status != "completed":
            raise DecisionValidationError(
                "reporting requires a completed execution; repair code after a failed run"
            )
        if remaining_reporting_retries(state) <= 0 and state.counters.reporting_attempts > 0:
            raise DecisionValidationError("reporting retry budget exhausted")
        if _unexecuted_ready_code(state, reports):
            raise DecisionValidationError("unexecuted ready implementation must run first")
        if state.pending_research_revision:
            raise DecisionValidationError(
                "new research must be incorporated via revise_research before reporting"
            )
        if _design_newer_than_code(reports):
            can_implement = remaining_code_retries(state) > 0 or state.counters.code_attempts == 0
            if can_implement:
                raise DecisionValidationError("new design must be implemented before reporting")
        if _execution_already_reported(reports):
            raise DecisionValidationError("this execution was already reported; emit DONE")
        # Reporting stays legal after a process-completed run even when science
        # is below_threshold or accepted and other modules are still legal.
        # The manager should treat it as the last step: enough original-task
        # evidence, or stuck.
        return


def _execution_already_reported(reports: list[SubagentReport]) -> bool:
    last_report = _latest_succeeded(reports, "reporting")
    if last_report is None:
        return False
    last_exec = _latest_module_report(reports, "experiment_execution")
    return _round_index(last_report) >= _round_index(last_exec)


def _complete_requirement(
    task: TaskState,
    req_id: str,
    report_id: str,
    *,
    notes: str = "",
) -> None:
    req = _requirement(task, req_id)
    if req is None or req.status == "completed":
        return
    req.status = "completed"
    if report_id and report_id not in req.supporting_report_ids:
        req.supporting_report_ids.append(report_id)
    if notes:
        req.notes = notes


def sync_host_requirements(state: PersistedManagerState) -> None:
    """Mark default requirements complete from already-succeeded reports.

    Safe to call on resume and every round. The manager is not required to
    emit ``state_changes`` for these host-owned requirement rows.
    """
    task = state.task_state
    reports = state.reports
    survey = _latest_succeeded(reports, "topic_survey")
    if survey is not None:
        _complete_requirement(
            task, "req-research", survey.report_id, notes="host: topic_survey succeeded"
        )
    design = _latest_succeeded(reports, "experiment_design")
    if design is not None:
        _complete_requirement(
            task, "req-design", design.report_id, notes="host: experiment_design succeeded"
        )
    if _code_is_ready(task, reports):
        code = _latest_succeeded(reports, "code_implementation")
        if code is not None:
            _complete_requirement(
                task, "req-implement", code.report_id, notes="host: implementation ready"
            )
    if task.latest_execution_status == "completed":
        execution = _latest_succeeded(reports, "experiment_execution")
        if execution is not None:
            _complete_requirement(
                task,
                "req-execute",
                execution.report_id,
                notes="host: execution process completed",
            )
    reporting = _latest_succeeded(reports, "reporting")
    if reporting is not None:
        _complete_requirement(
            task, "req-report", reporting.report_id, notes="host: reporting succeeded"
        )


def validate_decision(state: PersistedManagerState, decision: ManagerDecision) -> None:
    validate_state_changes(state.task_state, state.reports, decision.state_changes)
    if decision.signal == "EXECUTE":
        if decision.contract is None:
            raise DecisionValidationError("signal=EXECUTE requires a contract")
        unknown = [
            rid
            for rid in decision.contract.related_report_ids
            if _report_by_id(state.reports, rid) is None
        ]
        if unknown:
            raise DecisionValidationError(f"unknown related_report_ids: {unknown}")
        validate_contract(state.task_state, state.reports, decision.contract)
        return
    if decision.signal == "DONE":
        tentative = apply_state_changes(state.task_state, decision.state_changes)
        probe = state.model_copy(update={"task_state": tentative})
        ok, reason = can_complete(probe)
        if not ok:
            raise DecisionValidationError(f"DONE rejected: {reason}")
        return
    if decision.signal == "BLOCKED":
        ok, _ = can_complete(state)
        if ok:
            raise DecisionValidationError("task is complete; emit DONE")


def can_complete(state: PersistedManagerState) -> tuple[bool, str]:
    task = state.task_state
    pending = [item.id for item in task.requirements if item.status != "completed"]
    if pending:
        return False, f"pending requirements: {', '.join(pending)}"
    if task.unresolved_issues:
        return False, f"unresolved issues: {'; '.join(task.unresolved_issues)}"
    execution_reports = [
        item
        for item in state.reports
        if item.module == "experiment_execution" and item.outcome == "succeeded"
    ]
    if not execution_reports or task.latest_execution_status != "completed":
        return False, "no successful completed execution report"
    if "reporting" in task.enabled_modules:
        report_reports = [
            item for item in state.reports if item.module == "reporting" and item.outcome == "succeeded"
        ]
        if not report_reports:
            return False, "no successful reporting report"
    return True, ""


def select_report_context(
    reports: list[SubagentReport],
    related_ids: list[str] | None = None,
    *,
    limit: int = 6,
) -> list[SubagentReport]:
    """Keep latest-per-module and recent reports; union related ids, never replace them."""
    wanted: set[str] = set()
    latest_by_module: dict[ModuleId, SubagentReport] = {}
    for item in reports:
        latest_by_module[item.module] = item
    for item in latest_by_module.values():
        wanted.add(item.report_id)
    for item in reversed(reports):
        if item.outcome == "succeeded":
            wanted.add(item.report_id)
            break
    if related_ids:
        wanted.update(related_ids)
    for item in reports[-limit:]:
        wanted.add(item.report_id)
    selected = [item for item in reports if item.report_id in wanted]
    if len(selected) <= limit + len(latest_by_module):
        return selected
    latest_ids = {item.report_id for item in latest_by_module.values()}
    related = set(related_ids or [])
    pinned = [item for item in selected if item.report_id in latest_ids or item.report_id in related]
    extras = [item for item in selected if item.report_id not in latest_ids and item.report_id not in related]
    keep_extra = extras[-limit:]
    keep_ids = {item.report_id for item in pinned + keep_extra}
    return [item for item in reports if item.report_id in keep_ids]


def _action_reason(
    module: ModuleId,
    mode: ModuleMode,
    state: TaskState,
    reports: list[SubagentReport],
) -> str:
    if module == "topic_survey":
        if not _has_succeeded_survey(reports):
            return "first survey"
        return "stuck: survey allowed"
    if module == "experiment_design":
        if mode == "create":
            return "create after survey"
        if mode == "update":
            last_redesign = _latest_succeeded(
                reports,
                "experiment_design",
                modes=frozenset({"update", "revise_research"}),
            )
            last_reflection = _latest_succeeded(reports, "reflection")
            if _round_index(last_reflection) > _round_index(last_redesign):
                return "update after reflection"
            return "update after execution"
        return "revise after new survey"
    if module == "code_implementation":
        if _code_needs_repair(state, reports) and not _code_needs_create(reports):
            if not _code_is_ready(state, reports):
                return "repair smoke test error"
            return "repair process failure"
        return "create after design"
    if module == "experiment_execution":
        if _unexecuted_ready_code(state, reports):
            return "unexecuted implementation"
        return "re-run without code change"
    if module == "reflection":
        return "after process-completed execution"
    if module == "reporting":
        if not _science_loop_open(state, reports):
            return "science loop exhausted"
        if _science_accepted(reports):
            return "last step if original task is complete"
        return "last step if enough or stuck"
    return "permitted"


def list_legal_actions(state: TaskState, reports: list[SubagentReport]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for module, mode in _LEGAL_PAIRS:
        probe = _PROBE_CONTRACT.model_copy(update={"module": module, "mode": mode})
        try:
            validate_contract(state, reports, probe)
        except DecisionValidationError:
            continue
        actions.append(
            {
                "module": module,
                "mode": mode,
                "reason": _action_reason(module, mode, state, reports),
            }
        )
    return actions


def record_status(state: TaskState, record_id: str) -> RecordStatus | None:
    for collection in (state.requirements, state.artifacts, state.facts):
        for item in collection:
            if item.id == record_id:
                return item.status
    return None
