# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Step 2: Mechanism attribution -role-internal failure mechanism diagnosis.

Per feat_009 rule.md Section 3.3-3.4 and design.md Section 4.5-4.6.
MechanismAttributor only processes roles from Step 1 assigned_role_issues.
Uses fixed failure_signature and mechanism_type taxonomies.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.member_optimizer.agents.factory import (
    create_mechanism_attribution_agent,
)
from openjiuwen.rsi.member_optimizer.agents.output import (
    invoke_member_optimizer_agent_structured,
    parse_yaml_or_json_object_response,
)
from openjiuwen.rsi.member_optimizer.loader import (
    EvalRef,
    load_case_evidence,
)
from openjiuwen.rsi.member_optimizer.schema import (
    FailureSignature,
    MechanismAttributionReport,
    MechanismType,
    OptimizationSurface,
    RoleAttributionReport,
    RoleIssueAttribution,
    RoleMechanismAttribution,
)

MECHANISM_TYPE_VALUES: list[str] = [e.value for e in MechanismType if e is not MechanismType.RAIL]
FAILURE_SIGNATURE_VALUES: list[str] = [
    e.value for e in FailureSignature if e is not FailureSignature.CONFIG_OR_RAIL_MISMATCH
]
OPTIMIZATION_SURFACE_VALUES: list[str] = [e.value for e in OptimizationSurface if e is not OptimizationSurface.RAIL]


def _validate_mechanism_attribution_output(
    raw: dict[str, Any],
    assigned_roles: set[str],
) -> list[str]:
    """Validate mechanism attribution output. Returns list of error strings."""
    errors: list[str] = []
    if "role" not in raw:
        errors.append("missing field: role")
    elif raw["role"] not in assigned_roles:
        errors.append(f"role '{raw['role']}' not in assigned roles")
    attributions = raw.get("attributions")
    if not isinstance(attributions, list):
        errors.append("attributions must be a list")
        return errors
    for i, attr in enumerate(attributions):
        if "issue_id" not in attr:
            errors.append(f"attribution[{i}]: missing issue_id")
        mt = attr.get("mechanism_type", "")
        if mt not in MECHANISM_TYPE_VALUES:
            errors.append(f"attribution[{i}]: unknown mechanism_type '{mt}'")
        fs = attr.get("failure_signature", "")
        if fs not in FAILURE_SIGNATURE_VALUES:
            errors.append(f"attribution[{i}]: unknown failure_signature '{fs}'")
        surface = str(attr.get("optimization_surface", "") or "")
        if surface and surface not in OPTIMIZATION_SURFACE_VALUES:
            errors.append(f"attribution[{i}]: unknown optimization_surface '{surface}'")
        if surface == "none" and (
            mt != MechanismType.INSUFFICIENT_ROLE_EVIDENCE.value
            or fs != FailureSignature.INSUFFICIENT_ROLE_EVIDENCE.value
        ):
            errors.append(
                f"attribution[{i}]: optimization_surface 'none' requires "
                "insufficient_role_evidence mechanism and signature"
            )
        try:
            conf = float(attr.get("confidence", -1))
            if not (0.0 <= conf <= 1.0):
                errors.append(f"attribution[{i}]: confidence {conf} out of range")
        except (TypeError, ValueError):
            errors.append(f"attribution[{i}]: confidence is not a number")
    return errors


class MechanismAttributorAgent:
    """LLM-as-Judge agent for role-internal mechanism attribution using DeepAgent."""

    def __init__(
        self,
        model_config_ref: str,
        attribution_retry_limit: int = 2,
        workspace: str | Path | None = None,
        agent_skills_dirs: list[str] | None = None,
    ) -> None:
        self._model_config_ref = model_config_ref
        self._retry_limit = attribution_retry_limit
        self._workspace = Path(workspace).expanduser().resolve() if workspace else None
        self._agent_skills_dirs = list(agent_skills_dirs or [])

    async def attribute_role_mechanisms(
        self,
        role: str,
        role_issues: list[RoleIssueAttribution],
        case_evidence: dict[str, dict[str, Any]],
        harness_summary: str,
    ) -> list[RoleMechanismAttribution]:
        """Attribute mechanism for all issues of one role.

        Raises:
            RuntimeError: if all retries fail.
        """
        agent = create_mechanism_attribution_agent(
            model_config_ref=self._model_config_ref,
            workspace=self._workspace or ".",
            agent_skills_dirs=self._agent_skills_dirs,
        )

        user_message = self._build_user_message(role, role_issues, case_evidence, harness_summary)
        candidate_roles = {ri.role for ri in role_issues}

        raw = await invoke_member_optimizer_agent_structured(
            agent=agent,
            agent_name=f"MechanismAttributorAgent[{role}]",
            user_message=user_message,
            session_id=f"mech_attr_{role}",
            retry_limit=self._retry_limit,
            parse_response=parse_yaml_or_json_object_response,
            validate_response=lambda data: _validate_mechanism_attribution_output(
                data,
                candidate_roles,
            ),
            build_retry_message=lambda _previous, error: self._build_retry_message(
                user_message,
                str(error),
            ),
        )

        attributions = []
        for attr_data in raw.get("attributions", []):
            attributions.append(
                RoleMechanismAttribution(
                    issue_id=attr_data.get("issue_id", ""),
                    role=raw.get("role", role),
                    mechanism_type=attr_data.get("mechanism_type", ""),
                    failure_signature=attr_data.get("failure_signature", ""),
                    confidence=float(attr_data.get("confidence", 0.0)),
                    optimization_surface=attr_data.get("optimization_surface", ""),
                    evidence=attr_data.get("evidence", []),
                    evidence_refs=attr_data.get("evidence_refs", []),
                    rationale=attr_data.get("rationale", ""),
                )
            )
        return attributions

    @staticmethod
    def _build_user_message(
        role: str,
        role_issues: list[RoleIssueAttribution],
        case_evidence: dict[str, dict[str, Any]],
        harness_summary: str,
    ) -> str:
        issues_text = "\n".join(
            f"  - issue_id: {ri.issue_id}"
            f"\n    confidence: {ri.confidence}"
            f"\n    rationale: {ri.rationale}"
            f"\n    evidence_summary: {ri.evidence[0].get('summary', '') if ri.evidence else ''}"
            f"\n    analyzer_attribution: {_summarize_role_issue_attribution(ri.evidence)}"
            for ri in role_issues
        )

        evidence_text = "\n".join(
            f"  Case {cid}:"
            f"\n    status: {ev.get('result', {}).get('status', '')}"
            f"\n    response: {str(ev.get('result', {}).get('response', ''))[:300]}"
            f"\n    evaluation: {ev.get('trace', {}).get('evaluation', '')}"
            f"\n    training_signal: {_summarize_training_signal(ev.get('result', {}).get('metadata', {}))}"
            f"\n    behavior_trace: {_summarize_behavior_trace(ev.get('trace', {}).get('behavior_trace', {}))}"
            for cid, ev in case_evidence.items()
        )

        return f"""## Role to Diagnose

role: {role}
Number of attributed issues: {len(role_issues)}

## Attributed Issues

{issues_text}

## Case Evidence (Bounded Excerpts)

{evidence_text}

## Role Harness Summary

{harness_summary}

## Output

Return ONLY a JSON object as specified in the system prompt.
"""

    @staticmethod
    def _build_retry_message(original: str, error: str) -> str:
        return (
            f"{original}\n\n"
            f"## Validation Error\n\n{error}\n\n"
            f"Please correct the JSON output and return a valid response."
        )


def _summarize_behavior_trace(behavior_trace: Any) -> str:
    if not isinstance(behavior_trace, dict):
        return "none"
    workspace_changes = behavior_trace.get("workspace_changes") or {}
    command_log = behavior_trace.get("command_log") or []
    parts: list[str] = []
    if isinstance(workspace_changes, dict):
        added = workspace_changes.get("added") or []
        modified = workspace_changes.get("modified") or []
        removed = workspace_changes.get("removed") or []
        if added or modified or removed:
            parts.append(
                "workspace_changes="
                f"added:{list(added)[:12]}, "
                f"modified:{list(modified)[:12]}, "
                f"removed:{list(removed)[:12]}"
            )
    if isinstance(command_log, list) and command_log:
        commands = []
        for item in command_log[:12]:
            if not isinstance(item, dict):
                continue
            command = str(item.get("command", ""))
            exit_code = item.get("exit_code")
            stderr = str(item.get("stderr_excerpt", "") or "")
            commands.append(
                {
                    "command": command[:240],
                    "exit_code": exit_code,
                    "stderr": stderr[:240],
                }
            )
        if commands:
            parts.append(f"commands={commands}")
    window = behavior_trace.get("trajectory_window_summary")
    if isinstance(window, dict):
        signatures = window.get("failure_signatures") or []
        recent_events = []
        for item in (window.get("recent_events") or [])[:12]:
            if not isinstance(item, dict):
                continue
            recent_events.append(
                {
                    "event_type": str(item.get("event_type", ""))[:80],
                    "summary": str(item.get("summary", ""))[:300],
                }
            )
        if signatures or recent_events:
            parts.append(f"trajectory_window=failure_signatures:{list(signatures)[:12]}, recent_events:{recent_events}")
    normalized = behavior_trace.get("normalized_trace_summary")
    if isinstance(normalized, dict):
        trace_refs = []
        for trace in (normalized.get("traces") or [])[:4]:
            if not isinstance(trace, dict):
                continue
            for message in (trace.get("messages") or [])[:12]:
                if not isinstance(message, dict):
                    continue
                for call in (message.get("tool_calls") or [])[:4]:
                    if not isinstance(call, dict) or not call.get("error"):
                        continue
                    trace_refs.append(
                        {
                            "trace_id": trace.get("trace_id", ""),
                            "role": trace.get("member_role", ""),
                            "message_index": message.get("message_index"),
                            "step_pointer": call.get("step_pointer", ""),
                            "tool": call.get("name", ""),
                            "error": str(call.get("error", ""))[:240],
                        }
                    )
        if trace_refs:
            parts.append(f"normalized_trace_failures={trace_refs}")
    return " | ".join(parts) if parts else "none"


def _summarize_training_signal(metadata: Any) -> str:
    if not isinstance(metadata, dict):
        return "none"
    signal = metadata.get("training_signal")
    if not isinstance(signal, dict):
        return "none"
    compact = {
        "expected_failure_modes": signal.get("expected_failure_modes", []),
        "capability_gap": str(signal.get("capability_gap", ""))[:500],
        "target_surfaces": signal.get("target_surfaces", []),
        "difficulty_rationale": str(signal.get("difficulty_rationale", ""))[:500],
    }
    return str(compact)


def _summarize_role_issue_attribution(evidence: list[dict[str, Any]]) -> str:
    compact = []
    for item in evidence[:4]:
        if not isinstance(item, dict):
            continue
        attribution = item.get("attribution")
        if not isinstance(attribution, dict):
            continue
        compact.append(
            {
                "target_ref": attribution.get("target_ref", ""),
                "root_cause": str(attribution.get("root_cause", ""))[:500],
                "critical_mistake": str(attribution.get("critical_mistake", ""))[:500],
                "general_mechanism": str(attribution.get("general_mechanism", ""))[:500],
                "evidence_refs": attribution.get("evidence_refs", []),
            }
        )
    return str(compact) if compact else "none"


class MechanismAttributor:
    """Orchestrates mechanism attribution for all assigned roles using MechanismAttributorAgent."""

    def __init__(
        self,
        mechanism_attributor_agent: MechanismAttributorAgent | None = None,
        max_concurrent_roles: int = 2,
    ) -> None:
        self._agent = mechanism_attributor_agent
        self._semaphore = asyncio.Semaphore(max_concurrent_roles)

    def _get_agent(
        self,
        model_config_ref: str,
        attribution_retry_limit: int,
        agent_workspace: str | Path | None = None,
        agent_skills_dirs: list[str] | None = None,
    ) -> MechanismAttributorAgent:
        if self._agent is not None:
            return self._agent
        return MechanismAttributorAgent(
            model_config_ref=model_config_ref,
            attribution_retry_limit=attribution_retry_limit,
            workspace=agent_workspace,
            agent_skills_dirs=agent_skills_dirs,
        )

    async def attribute(  # pylint: disable=huawei-too-many-arguments
        self,
        role_attribution_report: RoleAttributionReport,
        eval_ref: EvalRef,
        candidate_roles: list[Any],
        model_config_ref: str,
        attribution_retry_limit: int = 2,
        max_cases_per_issue: int = 3,
        max_trace_chars: int = 4000,
        max_result_chars: int = 2000,
        agent_workspace: str | Path | None = None,
        agent_skills_dirs: list[str] | None = None,
    ) -> MechanismAttributionReport:
        """Perform mechanism attribution for all assigned roles.

        Only processes roles from role_attribution_report.assigned_role_issues.
        """
        import uuid

        agent = self._get_agent(
            model_config_ref,
            attribution_retry_limit,
            agent_workspace,
            agent_skills_dirs,
        )

        assigned_roles = {ri.role for ri in role_attribution_report.assigned_role_issues}
        if not assigned_roles:
            return MechanismAttributionReport(
                mechanism_attribution_id=f"mechanism_attribution_{uuid.uuid4().hex[:8]}",
                source_role_attribution_path="",
                role_mechanisms={},
                skipped_roles=[],
                warnings=["No assigned roles to process"],
                metadata={"model_config_ref": model_config_ref},
            )

        grouped: dict[str, list[RoleIssueAttribution]] = {}
        for ri in role_attribution_report.assigned_role_issues:
            grouped.setdefault(ri.role, []).append(ri)

        harness_refs = {r.role: r for r in candidate_roles}

        async def process_role(
            role: str, role_issues: list[RoleIssueAttribution]
        ) -> tuple[str, list[RoleMechanismAttribution] | None, str | None]:
            async with self._semaphore:
                all_case_ids = set()
                for ri in role_issues:
                    for ref in ri.trace_refs:
                        if "case_id" in ref:
                            all_case_ids.add(ref["case_id"])

                case_evidence = load_case_evidence(
                    eval_ref=eval_ref,
                    case_ids=list(all_case_ids),
                    max_cases=max_cases_per_issue,
                    max_trace_chars=max_trace_chars,
                    max_result_chars=max_result_chars,
                )

                harness_summary = self._build_harness_summary(role, role_issues, harness_refs)

                attributions = await agent.attribute_role_mechanisms(
                    role=role,
                    role_issues=role_issues,
                    case_evidence=case_evidence,
                    harness_summary=harness_summary,
                )
                return role, attributions, None

        tasks = [process_role(r, ris) for r, ris in grouped.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        role_mechanisms: dict[str, list[RoleMechanismAttribution]] = {}
        skipped_roles: list[str] = []
        warnings: list[str] = []

        for result in results:
            if isinstance(result, Exception):
                raise RuntimeError(f"Mechanism attribution task failed: {result}") from result
            role, attributions, error = result
            if attributions is not None:
                role_mechanisms[role] = attributions
            else:
                skipped_roles.append(role)
                if error:
                    warnings.append(f"role {role}: {error}")

        return MechanismAttributionReport(
            mechanism_attribution_id=f"mechanism_attribution_{uuid.uuid4().hex[:8]}",
            source_role_attribution_path="",
            role_mechanisms=role_mechanisms,
            skipped_roles=skipped_roles,
            warnings=warnings,
            metadata={
                "model_config_ref": model_config_ref,
                "roles_processed": list(role_mechanisms.keys()),
                "roles_skipped": skipped_roles,
            },
        )

    @staticmethod
    def _build_harness_summary(
        role: str,
        role_issues: list[RoleIssueAttribution],
        harness_refs: dict[str, Any],
    ) -> str:
        harness_ref = harness_refs.get(role)
        if harness_ref:
            return f"Role: {role}, Harness: {harness_ref.harness_ref_path}, Description: {harness_ref.description}"
        return f"Role: {role} (no harness ref available)"

    @staticmethod
    def write_report(
        report: MechanismAttributionReport,
        output_dir: Path,
    ) -> Path:
        """Write mechanism attribution report to mechanism_attribution.yaml."""
        path = output_dir / "mechanism_attribution.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)

        mechanisms_by_role = {}
        for role, attributions in report.role_mechanisms.items():
            mechanisms_by_role[role] = [
                {
                    "issue_id": a.issue_id,
                    "role": a.role,
                    "mechanism_type": a.mechanism_type,
                    "failure_signature": a.failure_signature,
                    "confidence": a.confidence,
                    "optimization_surface": a.optimization_surface,
                    "evidence": a.evidence,
                    "evidence_refs": a.evidence_refs,
                    "rationale": a.rationale,
                }
                for a in attributions
            ]

        payload = {
            "mechanism_attribution_id": report.mechanism_attribution_id,
            "source_role_attribution_path": report.source_role_attribution_path,
            "role_mechanisms": mechanisms_by_role,
            "skipped_roles": report.skipped_roles,
            "warnings": report.warnings,
            "metadata": report.metadata,
        }

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)

        return path


__all__ = [
    "MechanismAttributionReport",
    "MechanismAttributor",
    "MechanismAttributorAgent",
]
