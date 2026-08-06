# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Step 1: Role attribution -LLM-as-Judge attribution of TeamIssues to roles.

Per feat_009 rule.md Section 3.1-3.2 and design.md Section 4.1-4.4.
Core intelligence is provided by RoleAttributorAgent (DeepAgent).
Outer Python components handle input loading, evidence bundling, validation,
retry, and artifact writing.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.member_optimizer.agents.factory import (
    create_role_attribution_agent,
)
from openjiuwen.rsi.member_optimizer.agents.output import (
    invoke_member_optimizer_agent_structured,
    parse_json_object_response,
)
from openjiuwen.rsi.member_optimizer.loader import (
    BoundedEvidenceBundle,
    EvalRef,
    load_case_evidence,
)
from openjiuwen.rsi.member_optimizer.schema import (
    MemberRoleCandidate,
    RoleAttributionReport,
    RoleIssueAttribution,
    TeamIssue,
    UnassignedMemberIssue,
)


def _validate_role_attribution_output(
    raw: dict[str, Any],
    candidate_roles: list[str],
) -> list[str]:
    """Validate a parsed role attribution output. Returns list of error strings."""
    errors: list[str] = []
    if "issue_id" not in raw:
        errors.append("missing field: issue_id")
    if "decision" not in raw:
        errors.append("missing field: decision")
    if raw.get("decision") == "assigned":
        if "role" not in raw:
            errors.append("missing field: role for assigned decision")
        elif raw["role"] not in candidate_roles:
            errors.append(f"role '{raw['role']}' not in candidate_roles")
    if "confidence" not in raw:
        errors.append("missing field: confidence")
    else:
        try:
            conf = float(raw["confidence"])
            if not (0.0 <= conf <= 1.0):
                errors.append(f"confidence {conf} out of range [0, 1]")
        except (TypeError, ValueError):
            errors.append(f"confidence is not a number: {raw['confidence']}")
    return errors


def _normalize_member_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _is_single_role_alias_key(value: str) -> bool:
    return value in {
        "solver",
        "agent",
        "assistant",
        "default",
        "single_agent",
        "single_harness",
        "worker",
    }


def _target_members_for_issue(issue: TeamIssue) -> list[str]:
    targets: list[str] = []
    for item in issue.target_members:
        if isinstance(item, str) and item.strip():
            targets.append(item.strip())
    if not targets:
        affected_component = issue.metadata.get("affected_component")
        if isinstance(affected_component, str) and affected_component.strip():
            targets.append(affected_component.strip())
    return list(dict.fromkeys(targets))


def _match_target_member(
    issue: TeamIssue,
    candidate_roles: list[MemberRoleCandidate],
) -> tuple[MemberRoleCandidate | None, str]:
    """Resolve analyzer target_members to exactly one candidate role."""
    if _normalize_member_key(issue.optimization_target) != "member_harness":
        return None, "not_member_harness"
    target_members = _target_members_for_issue(issue)
    if not target_members:
        return None, "missing_target_members"

    target_keys = {_normalize_member_key(item) for item in target_members if item}
    matches: list[MemberRoleCandidate] = []
    for candidate in candidate_roles:
        candidate_keys = {
            _normalize_member_key(candidate.role),
            _normalize_member_key(candidate.member_name),
        }
        aliases = candidate.metadata.get("aliases", [])
        if isinstance(aliases, list):
            candidate_keys.update(_normalize_member_key(alias) for alias in aliases if alias)
        stable_member_id = candidate.metadata.get("member_id")
        if stable_member_id:
            candidate_keys.add(_normalize_member_key(stable_member_id))
        if target_keys & candidate_keys:
            matches.append(candidate)

    unique = {(candidate.role, candidate.member_name, candidate.harness_ref_path): candidate for candidate in matches}
    if len(unique) == 1:
        return next(iter(unique.values())), "target_members_exact_match"
    if unique:
        return None, "target_members_ambiguous"
    if len(candidate_roles) == 1 and target_keys and all(_is_single_role_alias_key(key) for key in target_keys):
        return candidate_roles[0], "target_members_single_role_alias"
    return None, "target_members_mismatch"


def _trace_refs_for_issue(issue: TeamIssue, raw: dict[str, Any]) -> list[dict[str, str]]:
    refs = raw.get("evidence_refs", [])
    if isinstance(refs, list) and refs:
        return [{str(key): str(value) for key, value in ref.items()} for ref in refs if isinstance(ref, dict)]
    return [{"case_id": str(case_id)} for case_id in issue.affected_cases if case_id]


class RoleAttributorAgent:
    """LLM-as-Judge agent for role attribution using DeepAgent."""

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

    def _create_deep_agent(self) -> Any:
        """Create a DeepAgent instance for role attribution."""
        return create_role_attribution_agent(
            model_config_ref=self._model_config_ref,
            workspace=self._workspace or ".",
            agent_skills_dirs=self._agent_skills_dirs,
        )

    async def attribute_issue(
        self,
        evidence_bundle: BoundedEvidenceBundle,
    ) -> dict[str, Any]:
        """Attribute a single issue to a role or mark as unassigned.

        Raises:
            RuntimeError: if all retries fail or output remains invalid.
        """
        agent = self._create_deep_agent()
        candidate_roles = [r.role for r in evidence_bundle.candidate_roles]
        issue_data = evidence_bundle.issue
        user_message = self._build_user_message(evidence_bundle)

        return await invoke_member_optimizer_agent_structured(
            agent=agent,
            agent_name="RoleAttributorAgent",
            user_message=user_message,
            session_id=f"role_attr_{issue_data.get('issue_id', 'unknown')}",
            retry_limit=self._retry_limit,
            parse_response=parse_json_object_response,
            validate_response=lambda raw: _validate_role_attribution_output(
                raw,
                candidate_roles,
            ),
            build_retry_message=self._build_retry_message,
        )

    @staticmethod
    def _build_retry_message(previous: Any, error: Any) -> str:
        if isinstance(error, list):
            error_text = "; ".join(str(item) for item in error)
        else:
            error_text = str(error)
        previous_text = json.dumps(previous, ensure_ascii=False, indent=2) if previous else "{}"
        return f"""## Previous Invalid Output

```json
{previous_text}
```

## Validation Error

{error_text}

Return ONLY a corrected JSON object matching the role attribution schema.
"""

    @staticmethod
    def _build_user_message(
        evidence_bundle: BoundedEvidenceBundle,
    ) -> str:
        """Build the user message for role attribution."""
        issue = evidence_bundle.issue
        candidate_roles = evidence_bundle.candidate_roles
        case_results = evidence_bundle.case_results
        case_traces = evidence_bundle.case_traces

        roles_text = "\n".join(
            f"  - role: {r.role}"
            f"\n    member_name: {r.member_name}"
            f"\n    harness_ref: {r.harness_ref_path}"
            f"\n    description: {r.description}"
            for r in candidate_roles
        )

        cases_text = ""
        for case in case_results:
            cid = case.get("case_id", "")
            trace_case = next((t for t in case_traces if t.get("case_id") == cid), {})
            cases_text += f"\n  Case {cid}:"
            cases_text += f"\n    status: {case.get('status', '')}"
            cases_text += f"\n    score: {case.get('score', '')}"
            cases_text += f"\n    response_summary: {case.get('response', '')[:200]}"
            cases_text += f"\n    trace_evaluation: {trace_case.get('evaluation', '')}"
            cases_text += f"\n    behavior_trace: {_summarize_behavior_trace(trace_case.get('behavior_trace', {}))}"

        return f"""## Issue to Attribute

- issue_id: {issue.get("issue_id", "")}
- category: {issue.get("category", "")}
- severity: {issue.get("severity", "")}
- summary: {issue.get("summary", "")}
- optimization_target: {issue.get("optimization_target", "")}
- target_members: {issue.get("target_members", [])}
- affected_component: {issue.get("affected_component", "")}
- suspected_team_scope: {issue.get("suspected_team_scope", "")}
- recommendation: {issue.get("recommendation", "")}
- analyzer_attribution: {_summarize_issue_attribution(issue)}
- issue_evidence: {_summarize_issue_evidence(issue.get("evidence", []))}

## Affected Cases

{cases_text}

## Candidate Roles

{roles_text}

## Output Instructions

Return ONLY a JSON object as specified in the system prompt.
Do not include any explanation outside the JSON.
"""


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
            commands.append(
                {
                    "command": str(item.get("command", ""))[:240],
                    "exit_code": item.get("exit_code"),
                    "stderr": str(item.get("stderr_excerpt", "") or "")[:240],
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


def _summarize_issue_attribution(issue: dict[str, Any]) -> str:
    metadata = issue.get("metadata") if isinstance(issue.get("metadata"), dict) else {}
    attribution = metadata.get("attribution") if isinstance(metadata, dict) else None
    if not isinstance(attribution, dict):
        return "none"
    return json.dumps(
        {
            "root_cause": str(attribution.get("root_cause", ""))[:500],
            "critical_mistake": str(attribution.get("critical_mistake", ""))[:500],
            "general_mechanism": str(attribution.get("general_mechanism", ""))[:500],
            "target_ref": str(attribution.get("target_ref", "")),
            "evidence_refs": attribution.get("evidence_refs", []),
        },
        ensure_ascii=False,
    )


def _summarize_issue_evidence(evidence: Any) -> str:
    if not isinstance(evidence, list):
        return "none"
    compact = []
    for item in evidence[:4]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "case_id": item.get("case_id", ""),
                "signals": item.get("signals", []),
                "attribution": item.get("attribution", {}),
                "trajectory_failure_signatures": item.get("trajectory_failure_signatures", []),
            }
        )
    return json.dumps(compact, ensure_ascii=False)


class RoleAttributor:
    """Orchestrates role attribution for all TeamIssues using RoleAttributorAgent."""

    def __init__(
        self,
        role_attributor_agent: RoleAttributorAgent | None = None,
        max_concurrent_issues: int = 4,
    ) -> None:
        self._agent = role_attributor_agent
        self._semaphore = asyncio.Semaphore(max_concurrent_issues)

    def _get_agent(
        self,
        model_config_ref: str,
        attribution_retry_limit: int,
        agent_workspace: str | Path | None = None,
        agent_skills_dirs: list[str] | None = None,
    ) -> RoleAttributorAgent:
        if self._agent is not None:
            return self._agent
        return RoleAttributorAgent(
            model_config_ref=model_config_ref,
            attribution_retry_limit=attribution_retry_limit,
            workspace=agent_workspace,
            agent_skills_dirs=agent_skills_dirs,
        )

    async def attribute(  # pylint: disable=huawei-too-many-arguments
        self,
        eval_ref: EvalRef,
        team_issues: list[TeamIssue],
        candidate_roles: list[MemberRoleCandidate],
        model_config_ref: str,
        attribution_retry_limit: int = 2,
        max_cases_per_issue: int = 3,
        max_trace_chars: int = 4000,
        max_result_chars: int = 2000,
        agent_workspace: str | Path | None = None,
        agent_skills_dirs: list[str] | None = None,
    ) -> RoleAttributionReport:
        """Attribute all TeamIssues to roles.

        Processes issues concurrently with bounded concurrency.
        Produces a RoleAttributionReport with assigned and unassigned issues.
        """
        import uuid

        agent = self._get_agent(
            model_config_ref,
            attribution_retry_limit,
            agent_workspace,
            agent_skills_dirs,
        )
        assigned: list[RoleIssueAttribution] = []
        unassigned: list[UnassignedMemberIssue] = []
        warnings: list[str] = []

        async def process_one(
            issue: TeamIssue,
        ) -> tuple[RoleIssueAttribution | None, UnassignedMemberIssue | None, str | None]:
            async with self._semaphore:
                case_ids = issue.affected_cases if issue.affected_cases else []
                case_evidence = load_case_evidence(
                    eval_ref=eval_ref,
                    case_ids=case_ids,
                    max_cases=max_cases_per_issue,
                    max_trace_chars=max_trace_chars,
                    max_result_chars=max_result_chars,
                )

                bundle = BoundedEvidenceBundle(
                    issue={
                        "issue_id": issue.issue_id,
                        "category": issue.category,
                        "severity": issue.severity,
                        "summary": issue.summary,
                        "affected_cases": issue.affected_cases,
                        "evidence": issue.evidence[:5],
                        "suspected_team_scope": issue.suspected_team_scope,
                        "optimization_target": issue.optimization_target,
                        "target_members": issue.target_members,
                        "affected_component": issue.metadata.get("affected_component", ""),
                        "recommendation": issue.recommendation,
                    },
                    case_results=[{"case_id": cid, **ev["result"]} for cid, ev in case_evidence.items()],
                    case_traces=[{"case_id": cid, **ev["trace"]} for cid, ev in case_evidence.items()],
                    candidate_roles=candidate_roles,
                )

                raw = await agent.attribute_issue(bundle)
                target_candidate, target_match_status = _match_target_member(issue, candidate_roles)

                decision = raw.get("decision", "unassigned")
                if decision == "assigned" or target_candidate is not None:
                    raw_role = raw.get("role", "")
                    role = raw_role if decision == "assigned" else target_candidate.role
                    if target_candidate is not None and decision != "assigned":
                        role = target_candidate.role
                    elif target_candidate is not None and raw_role != target_candidate.role:
                        role = target_candidate.role
                        target_match_status = "target_members_overrode_unassigned_or_mismatch"
                    harness_ref = next(
                        (r.harness_ref_path for r in candidate_roles if r.role == role),
                        "",
                    )
                    member_name = next(
                        (r.member_name for r in candidate_roles if r.role == role),
                        "",
                    )
                    assignment_source = (
                        "llm_assigned" if decision == "assigned" and target_candidate is None else target_match_status
                    )
                    evidence_quality = (
                        "thin" if assignment_source != "llm_assigned" and decision != "assigned" else "case_evidence"
                    )
                    raw_confidence = float(raw.get("confidence", 0.0))
                    confidence = raw_confidence if decision == "assigned" else min(0.5, max(raw_confidence, 0.5))
                    role_attr = RoleIssueAttribution(
                        issue_id=raw.get("issue_id", issue.issue_id),
                        role=role,
                        harness_ref_path=harness_ref,
                        confidence=confidence,
                        evidence=[
                            {
                                "summary": issue.summary,
                                "severity": issue.severity,
                                "category": issue.category,
                                "recommendation": issue.recommendation,
                                "metadata": issue.metadata,
                                "improvement_brief": issue.metadata.get("improvement_brief", {}),
                                "candidate_query": issue.metadata.get("candidate_query", ""),
                                "optimization_target": issue.optimization_target,
                                "target_members": issue.target_members,
                                "affected_component": issue.metadata.get("affected_component", ""),
                                "target_match_status": target_match_status,
                                "assignment_source": assignment_source,
                                "evidence_quality": evidence_quality,
                            }
                        ],
                        trace_refs=_trace_refs_for_issue(issue, raw),
                        rationale=raw.get("rationale", ""),
                        member_name=member_name,
                    )
                    return role_attr, None, None
                else:
                    target_match_status = target_match_status if target_match_status else "not_matched"
                    reason = (
                        target_match_status
                        if target_match_status
                        in {
                            "target_members_ambiguous",
                            "target_members_mismatch",
                        }
                        else raw.get("reason", target_match_status or "insufficient_evidence")
                    )
                    unass = UnassignedMemberIssue(
                        issue_id=issue.issue_id,
                        reason=reason,
                        evidence=[
                            {
                                "summary": issue.summary,
                                "severity": issue.severity,
                                "category": issue.category,
                                "recommendation": issue.recommendation,
                                "metadata": issue.metadata,
                                "improvement_brief": issue.metadata.get("improvement_brief", {}),
                                "candidate_query": issue.metadata.get("candidate_query", ""),
                                "optimization_target": issue.optimization_target,
                                "target_members": issue.target_members,
                                "affected_component": issue.metadata.get("affected_component", ""),
                                "target_match_status": target_match_status,
                            }
                        ],
                        trace_refs=_trace_refs_for_issue(issue, raw),
                        rationale=raw.get("rationale", "Insufficient evidence to attribute."),
                    )
                    warning = None
                    if target_match_status in {"target_members_ambiguous", "target_members_mismatch"}:
                        warning = f"{issue.issue_id}: {target_match_status}"
                    return None, unass, warning

        tasks = [process_one(issue) for issue in team_issues]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                raise RuntimeError(f"Role attribution task failed: {result}") from result
            role_attr, unass, warning = result
            if role_attr is not None:
                assigned.append(role_attr)
            elif unass is not None:
                unassigned.append(unass)
            if warning is not None:
                warnings.append(warning)

        assigned.sort(key=lambda a: (a.issue_id, a.role))
        unassigned.sort(key=lambda u: u.issue_id)

        return RoleAttributionReport(
            attribution_id=f"role_attribution_{uuid.uuid4().hex[:8]}",
            source_eval_ref_path=str(eval_ref),
            source_analysis_result_path="",
            harness_refs_path="",
            candidate_roles=candidate_roles,
            assigned_role_issues=assigned,
            unassigned_issues=unassigned,
            warnings=warnings,
            metadata={
                "model_config_ref": model_config_ref,
                "issue_count": len(team_issues),
                "assigned_count": len(assigned),
                "unassigned_count": len(unassigned),
                "target_member_routing": "exact_match_or_single_role_alias",
            },
        )

    @staticmethod
    def write_report(
        report: RoleAttributionReport,
        output_dir: Path,
    ) -> Path:
        """Write the role attribution report to role_attribution.yaml."""
        path = output_dir / "role_attribution.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)

        role_issues = []
        for ri in report.assigned_role_issues:
            role_issues.append(
                {
                    "issue_id": ri.issue_id,
                    "role": ri.role,
                    "member_name": ri.member_name,
                    "harness_ref_path": ri.harness_ref_path,
                    "confidence": ri.confidence,
                    "assignment_source": (ri.evidence[0].get("assignment_source", "") if ri.evidence else ""),
                    "evidence_quality": (ri.evidence[0].get("evidence_quality", "") if ri.evidence else ""),
                    "evidence": ri.evidence,
                    "trace_refs": ri.trace_refs,
                    "rationale": ri.rationale,
                    "role_output_refs": ri.role_output_refs,
                }
            )

        unass_issues = []
        for ui in report.unassigned_issues:
            unass_issues.append(
                {
                    "issue_id": ui.issue_id,
                    "reason": ui.reason,
                    "evidence": ui.evidence,
                    "trace_refs": ui.trace_refs,
                    "rationale": ui.rationale,
                }
            )

        candidate_list = []
        for cr in report.candidate_roles:
            candidate_list.append(
                {
                    "role": cr.role,
                    "member_name": cr.member_name,
                    "harness_ref_path": cr.harness_ref_path,
                    "description": cr.description,
                    "metadata": cr.metadata,
                }
            )

        payload = {
            "attribution_id": report.attribution_id,
            "source_eval_ref_path": report.source_eval_ref_path,
            "source_analysis_result_path": report.source_analysis_result_path,
            "harness_refs_path": report.harness_refs_path,
            "candidate_roles": candidate_list,
            "assigned_role_issues": role_issues,
            "unassigned_issues": unass_issues,
            "warnings": report.warnings,
            "metadata": report.metadata,
        }

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)

        return path


__all__ = [
    "RoleAttributionReport",
    "RoleAttributor",
    "RoleAttributorAgent",
]
