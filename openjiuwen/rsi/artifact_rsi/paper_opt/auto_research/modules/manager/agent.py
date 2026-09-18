"""Manager decision agent: one fresh LLM round, one typed control decision."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.env import load_project_dotenv
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.metrics import compact_metrics_for_manager
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import project_root, set_project_root
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.manager_capability_rail import (
    ManagerCapabilityRail,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.observability_rail import with_observability
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.tools.submit_manager_decision import (
    SubmitManagerDecisionTool,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import ManagerDecision, ManagerSnapshot

AGENT_CARD_ID = "manager-agent"
AGENT_CARD_NAME = "manager"
_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "system.md"
_STATE_MARKER = "\nSTATE:\n"
_KEEP_PATHS = 3
_KEEP_LIST_ITEMS = 6
_KEEP_VARIANTS = 12
_SUMMARY_CHARS = 400
_FACT_TEXT_CHARS = 600
_PROMPT_CHARS = 2_500
_NOTE_CHARS = 200
_METRIC_LIMIT = 20


class ManagerQueryBudgetError(ValueError):
    """Mandatory manager input exceeds max_history_chars after structured compaction."""

    def __init__(self, message: str, *, section_sizes: dict[str, int], limit: int):
        super().__init__(message)
        self.section_sizes = section_sizes
        self.limit = limit


def _load_system_prompt() -> str:
    return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _build_model_from_config(config: dict[str, Any]):
    from openjiuwen.core.foundation.llm import Model
    from openjiuwen.core.foundation.llm.schema.config import (
        ModelClientConfig,
        ModelRequestConfig,
    )

    load_project_dotenv()
    oj = dict(config.get("openjiuwen") or {})
    api_key_env = oj.get("api_key_env", "API_KEY")
    api_key = os.getenv(api_key_env, "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            f"missing model API credentials; set environment variable {api_key_env}"
        )

    def _cfg_str(key: str) -> str | None:
        value = oj.get(key)
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() == "default":
            return None
        return text

    def _cfg_or(key: str, default):
        value = oj.get(key)
        return default if value is None else value

    return Model(
        model_client_config=ModelClientConfig(
            client_provider=_cfg_str("provider") or os.getenv("MODEL_PROVIDER") or "OpenAI",
            api_key=api_key,
            api_base=_cfg_str("base_url") or os.getenv("API_BASE") or "https://api.openai.com/v1",
            timeout=int(_cfg_or("timeout", os.getenv("MODEL_TIMEOUT", "360"))),
            verify_ssl=bool(_cfg_or("verify_ssl", False)),
        ),
        model_config=ModelRequestConfig(
            model_name=_cfg_str("model") or os.getenv("MODEL_NAME") or "gpt-4.1-mini",
            temperature=float(_cfg_or("temperature", 0.1)),
            top_p=float(_cfg_or("top_p", 0.9)),
        ),
    )


def _json_len(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _encode_state(body: dict[str, Any]) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2, default=str)


def parse_manager_query_state(query: str) -> dict[str, Any]:
    """Parse the STATE JSON object from a rendered manager query."""
    idx = query.rfind(_STATE_MARKER)
    if idx < 0:
        raise ValueError("manager query is missing STATE")
    state_start = idx + len(_STATE_MARKER)
    return json.loads(query[state_start:].strip())


def _clip_text(text: str, max_chars: int) -> tuple[str, int]:
    cleaned = (text or "").strip()
    if max_chars <= 0:
        return "", len(cleaned)
    if len(cleaned) <= max_chars:
        return cleaned, 0
    return cleaned[:max_chars].rstrip(), len(cleaned) - max_chars


def _count_key(list_key: str) -> str:
    if list_key.endswith("ies"):
        return f"{list_key[:-3]}y_count"
    if list_key.endswith("s"):
        return f"{list_key[:-1]}_count"
    return f"{list_key}_count"


def _shrink_str_list(data: dict[str, Any], key: str, *, keep: int = _KEEP_PATHS) -> None:
    items = data.get(key)
    if not isinstance(items, list) or not items:
        return
    total = len(items)
    data[_count_key(key)] = total
    if total <= keep:
        return
    data[key] = items[:keep]
    data[f"omitted_{_count_key(key)}"] = total - keep


def _shrink_text_list(
    data: dict[str, Any],
    key: str,
    *,
    keep: int = _KEEP_LIST_ITEMS,
    item_chars: int = 200,
) -> None:
    items = data.get(key)
    if not isinstance(items, list) or not items:
        return
    clipped: list[str] = []
    for item in items[:keep]:
        text, _omitted = _clip_text(str(item), item_chars)
        clipped.append(text)
    data[key] = clipped
    total = len(items)
    if total > keep:
        data[f"omitted_{_count_key(key)}"] = total - keep


def _plan_metric_names(snapshot: ManagerSnapshot) -> list[str]:
    plan = snapshot.task_state.latest_plan
    if plan is None:
        return []
    return [str(item).strip() for item in (plan.metrics or []) if str(item).strip()]


def _compact_metrics(metrics: Any, *, plan_metrics: list[str]) -> dict[str, float | int | str]:
    if not isinstance(metrics, dict) or not metrics:
        return {}
    return compact_metrics_for_manager(metrics, plan_metrics=plan_metrics, limit=_METRIC_LIMIT)


def _compact_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return None
    expected, omitted = _clip_text(str(plan.get("expected_outcomes") or ""), _SUMMARY_CHARS)
    payload = {
        "revision": plan.get("revision"),
        "status": plan.get("status"),
        "design_path": plan.get("design_path"),
        "code_agent_instruction_path": plan.get("code_agent_instruction_path"),
        "metrics": plan.get("metrics") or [],
        "baselines": plan.get("baselines") or [],
        "expected_outcomes": expected,
    }
    if omitted:
        payload["expected_outcomes_omitted_chars"] = omitted
    return payload


def _compact_contract(contract: dict[str, Any] | None) -> dict[str, Any] | None:
    if not contract:
        return None
    goal, omitted = _clip_text(str(contract.get("goal") or ""), _SUMMARY_CHARS)
    payload = {
        "module": contract.get("module"),
        "mode": contract.get("mode"),
        "goal": goal,
        "related_report_ids": list(contract.get("related_report_ids") or []),
        "target_variants": list(contract.get("target_variants") or []),
        "restore_code_commit": contract.get("restore_code_commit") or "",
        "repair_instruction": contract.get("repair_instruction") or "",
    }
    if omitted:
        payload["goal_omitted_chars"] = omitted
    return {key: value for key, value in payload.items() if value not in (None, "", [])}


def _compact_evaluation(
    evaluation: dict[str, Any] | None,
    *,
    plan_metrics: list[str],
) -> dict[str, Any] | None:
    if not evaluation:
        return None
    metrics = evaluation.get("observed_metrics") or {}
    if isinstance(metrics, dict):
        evaluation["observed_metrics"] = _compact_metrics(metrics, plan_metrics=plan_metrics)
    summary, omitted = _clip_text(str(evaluation.get("summary") or ""), _SUMMARY_CHARS)
    evaluation["summary"] = summary
    if omitted:
        evaluation["summary_omitted_chars"] = omitted
    _shrink_str_list(evaluation, "result_paths", keep=4)
    return evaluation


def _compact_records(records: list[Any], *, text_key: str, max_chars: int) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        text, omitted = _clip_text(str(item.get(text_key) or ""), max_chars)
        item[text_key] = text
        if omitted:
            item[f"{text_key}_omitted_chars"] = omitted
        compacted.append(item)
    return compacted


def _compact_task_state(snapshot: ManagerSnapshot, *, plan_metrics: list[str]) -> dict[str, Any]:
    data = snapshot.task_state.model_dump(mode="json")
    data["latest_plan"] = _compact_plan(data.get("latest_plan"))
    data["last_contract"] = _compact_contract(data.get("last_contract"))
    data["pending_contract"] = _compact_contract(data.get("pending_contract"))
    data["latest_evaluation"] = _compact_evaluation(
        data.get("latest_evaluation"),
        plan_metrics=plan_metrics,
    )
    data["facts"] = _compact_records(
        list(data.get("facts") or []),
        text_key="text",
        max_chars=_FACT_TEXT_CHARS,
    )
    for req in data.get("requirements") or []:
        if isinstance(req, dict) and req.get("notes"):
            notes, omitted = _clip_text(str(req["notes"]), _NOTE_CHARS)
            req["notes"] = notes
            if omitted:
                req["notes_omitted_chars"] = omitted
    _shrink_str_list(data, "research_paths", keep=6)
    return data


def _compact_original_task(snapshot: ManagerSnapshot) -> dict[str, Any]:
    data = snapshot.original_task.model_dump(mode="json")
    # Host-only payload for ReportingAgent. The manager LLM uses initial_prompt.
    data.pop("previous_context", None)
    prompt, omitted = _clip_text(str(data.get("initial_prompt") or ""), _PROMPT_CHARS)
    data["initial_prompt"] = prompt
    if omitted:
        data["initial_prompt_omitted_chars"] = omitted
    _shrink_str_list(data, "initial_research_paths", keep=6)
    return data


def _compact_history_rows(rows: Any, *, plan_metrics: list[str]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return compacted
    for item in rows:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["metrics"] = _compact_metrics(row.get("metrics") or {}, plan_metrics=plan_metrics)
        compacted.append(row)
    return compacted


def _compact_routing(snapshot: ManagerSnapshot, *, plan_metrics: list[str]) -> dict[str, Any]:
    if snapshot.routing is None:
        data: dict[str, Any] = {}
    else:
        data = snapshot.routing.model_dump(mode="json")
    metrics = data.get("latest_metrics") or {}
    if isinstance(metrics, dict) and metrics:
        data["latest_metrics"] = _compact_metrics(metrics, plan_metrics=plan_metrics)
    variants = data.get("variant_metrics") or {}
    if isinstance(variants, dict) and variants:
        data["variant_metrics"] = {
            name: _compact_metrics(values, plan_metrics=plan_metrics)
            if isinstance(values, dict)
            else values
            for name, values in variants.items()
        }
    data["execution_history"] = _compact_history_rows(
        data.get("execution_history"),
        plan_metrics=plan_metrics,
    )
    _shrink_str_list(data, "diagnostic_paths", keep=4)
    if not data.get("known_report_ids"):
        data["known_report_ids"] = [report.report_id for report in snapshot.reports]
    return data


def _compact_variant(variant: dict[str, Any], *, plan_metrics: list[str]) -> dict[str, Any]:
    payload = {
        "name": variant.get("name"),
        "passed": variant.get("passed"),
        "exit_code": variant.get("exit_code"),
        "process_status": variant.get("process_status") or "",
        "failure_kind": variant.get("failure_kind") or "",
        "metrics_state": variant.get("metrics_state") or "",
        "duration_ms": variant.get("duration_ms"),
        "metrics": _compact_metrics(variant.get("metrics") or {}, plan_metrics=plan_metrics),
        "log_path": variant.get("log_path") or "",
        "diagnostics_path": variant.get("diagnostics_path") or "",
        "code_commit": variant.get("code_commit") or "",
    }
    return {key: value for key, value in payload.items() if value not in (None, "", {}, [])}


def _compact_handoff(handoff: dict[str, Any], *, plan_metrics: list[str]) -> dict[str, Any]:
    handoff.pop("failure_excerpts", None)
    for key in ("log_paths", "source_paths", "result_paths", "diagnostic_paths"):
        _shrink_str_list(handoff, key, keep=_KEEP_PATHS)
    variants = handoff.get("variants")
    if isinstance(variants, list):
        compacted = [
            _compact_variant(item, plan_metrics=plan_metrics)
            for item in variants
            if isinstance(item, dict)
        ]
        names = [str(item.get("name") or "") for item in compacted if item.get("name")]
        total = len(compacted)
        handoff["variant_names"] = names
        handoff["variant_count"] = total
        if total > _KEEP_VARIANTS:
            handoff["omitted_variant_count"] = total - _KEEP_VARIANTS
            compacted = compacted[:_KEEP_VARIANTS]
        handoff["variants"] = compacted
    diagnostic = handoff.get("diagnostic")
    if isinstance(diagnostic, dict):
        compact = _compact_metrics(diagnostic, plan_metrics=plan_metrics)
        if compact:
            handoff["diagnostic"] = compact
        else:
            handoff.pop("diagnostic", None)
    smoke = handoff.get("smoke_failures")
    if isinstance(smoke, dict) and smoke:
        handoff["smoke_failures"] = {
            str(key): _clip_text(str(value), 160)[0] for key, value in list(smoke.items())[:6]
        }
    for text_key in (
        "notes",
        "short_summary",
        "coverage_assessment",
        "objective",
        "hypothesis",
        "evidence_sufficiency",
        "summary",
    ):
        if text_key in handoff and isinstance(handoff[text_key], str) and handoff[text_key]:
            text, omitted = _clip_text(handoff[text_key], _SUMMARY_CHARS)
            handoff[text_key] = text
            if omitted:
                handoff[f"{text_key}_omitted_chars"] = omitted
    for list_key in (
        "key_findings",
        "open_problems",
        "evidence_gaps",
        "suggested_followup_queries",
        "metrics",
        "baselines",
    ):
        _shrink_text_list(handoff, list_key, keep=_KEEP_LIST_ITEMS, item_chars=160)
    return handoff


def _structurally_compact_report(report: Any, *, plan_metrics: list[str]) -> dict[str, Any]:
    data = report.model_dump(mode="json")
    _shrink_str_list(data, "artifact_paths", keep=_KEEP_PATHS)
    handoff = data.get("handoff")
    if isinstance(handoff, dict):
        data["handoff"] = _compact_handoff(handoff, plan_metrics=plan_metrics)
    return data


def _variant_names_from_handoff(handoff: Any) -> list[str]:
    if not isinstance(handoff, dict):
        return []
    names = [str(item) for item in list(handoff.get("variant_names") or []) if item]
    if names:
        return names
    variants = handoff.get("variants")
    if not isinstance(variants, list):
        return []
    return [
        str(item.get("name") or "")
        for item in variants
        if isinstance(item, dict) and item.get("name")
    ]


def _minimal_handoff(handoff: Any) -> dict[str, Any] | None:
    if not isinstance(handoff, dict):
        return None
    names = _variant_names_from_handoff(handoff)
    payload = {
        "kind": handoff.get("kind"),
        "status": handoff.get("status"),
        "sanity": handoff.get("sanity"),
        "process_status": handoff.get("process_status"),
        "verdict": handoff.get("verdict"),
        "validity": handoff.get("validity"),
        "recommendation": handoff.get("recommendation"),
        "revision": handoff.get("revision"),
        "design_path": handoff.get("design_path"),
        "report_path": handoff.get("report_path"),
        "readiness": handoff.get("readiness"),
        "variant_names": names,
        "variant_count": handoff.get("variant_count", len(names)),
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _stub_report(data: dict[str, Any], *, summary_chars: int) -> dict[str, Any]:
    summary, omitted = _clip_text(str(data.get("summary") or ""), summary_chars)
    stub = {
        "report_id": data.get("report_id"),
        "module": data.get("module"),
        "mode": data.get("mode"),
        "round_index": data.get("round_index"),
        "attempt": data.get("attempt"),
        "outcome": data.get("outcome"),
        "summary": summary,
        "handoff": _minimal_handoff(data.get("handoff")),
    }
    if omitted:
        stub["summary_omitted_chars"] = omitted
    return {key: value for key, value in stub.items() if value is not None}


def _enforce_report_budget(
    report: Any,
    max_chars: int,
    *,
    plan_metrics: list[str],
) -> dict[str, Any]:
    data = _structurally_compact_report(report, plan_metrics=plan_metrics)
    if _json_len(data) <= max_chars:
        return data
    summary, omitted = _clip_text(str(data.get("summary") or ""), min(_SUMMARY_CHARS, max_chars))
    data["summary"] = summary
    if omitted:
        data["summary_omitted_chars"] = omitted
    handoff = data.get("handoff")
    if isinstance(handoff, dict):
        if "notes" in handoff:
            notes, notes_omitted = _clip_text(str(handoff.get("notes") or ""), _NOTE_CHARS)
            handoff["notes"] = notes
            if notes_omitted:
                handoff["notes_omitted_chars"] = notes_omitted
        variants = handoff.get("variants")
        names = _variant_names_from_handoff(handoff)
        if isinstance(variants, list) and variants:
            handoff["variants"] = [
                {
                    "name": item.get("name"),
                    "process_status": item.get("process_status"),
                    "metrics": item.get("metrics") or {},
                }
                for item in variants
                if isinstance(item, dict)
            ]
        if names:
            handoff["variant_names"] = names
        data["handoff"] = handoff
    if _json_len(data) <= max_chars:
        return data
    stub = _stub_report(data, summary_chars=min(_SUMMARY_CHARS, max(80, max_chars // 4)))
    while _json_len(stub) > max_chars and stub.get("summary"):
        stub["summary"] = str(stub["summary"])[: max(0, len(str(stub["summary"])) // 2)]
    if _json_len(stub) > max_chars:
        names = _variant_names_from_handoff(stub.get("handoff"))
        stub["handoff"] = {"variant_names": names} if names else None
        if not stub.get("handoff"):
            stub.pop("handoff", None)
    return stub


def _pinned_report_ids(snapshot: ManagerSnapshot) -> set[str]:
    latest: dict[str, str] = {}
    for report in snapshot.reports:
        latest[report.module] = report.report_id
    pinned = set(latest.values())
    for contract in (snapshot.task_state.last_contract, snapshot.task_state.pending_contract):
        if contract is not None:
            pinned.update(contract.related_report_ids)
    return pinned


def _assemble_query_body(
    *,
    routing: dict[str, Any],
    snapshot: ManagerSnapshot,
    original_task: dict[str, Any],
    task_state: dict[str, Any],
    reports: list[dict[str, Any]],
    omitted_report_ids: list[str],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "routing": routing,
        "round_index": snapshot.round_index,
        "original_task": original_task,
        "task_state": task_state,
        "reports": reports,
    }
    if omitted_report_ids:
        body["context"] = {
            "omitted_report_ids": omitted_report_ids,
            "omitted_report_count": len(omitted_report_ids),
        }
    body["operator_followups"] = [
        item.model_dump(mode="json") for item in snapshot.operator_followups
    ]
    return body


def _fit_manager_query_body(snapshot: ManagerSnapshot) -> dict[str, Any]:
    limits = snapshot.task_state.limits
    plan_metrics = _plan_metric_names(snapshot)
    routing = _compact_routing(snapshot, plan_metrics=plan_metrics)
    original_task = _compact_original_task(snapshot)
    task_state = _compact_task_state(snapshot, plan_metrics=plan_metrics)
    pinned = _pinned_report_ids(snapshot)
    kept = [
        _enforce_report_budget(report, limits.max_report_chars, plan_metrics=plan_metrics)
        for report in snapshot.reports
    ]
    omitted: list[str] = []
    body = _assemble_query_body(
        routing=routing,
        snapshot=snapshot,
        original_task=original_task,
        task_state=task_state,
        reports=kept,
        omitted_report_ids=omitted,
    )
    while _json_len(body) > limits.max_history_chars:
        unpinned = [
            index
            for index, report in enumerate(kept)
            if str(report.get("report_id") or "") not in pinned
        ]
        if not unpinned:
            break
        drop_at = unpinned[0]
        omitted.append(str(kept[drop_at].get("report_id") or ""))
        kept = [report for index, report in enumerate(kept) if index != drop_at]
        body = _assemble_query_body(
            routing=routing,
            snapshot=snapshot,
            original_task=original_task,
            task_state=task_state,
            reports=kept,
            omitted_report_ids=omitted,
        )
    encoded_len = _json_len(body)
    if encoded_len > limits.max_history_chars:
        sizes = {key: _json_len(value) for key, value in body.items()}
        raise ManagerQueryBudgetError(
            "manager query exceeds max_history_chars="
            f"{limits.max_history_chars} after structured compaction "
            f"(encoded={encoded_len}); section sizes: {sizes}",
            section_sizes=sizes,
            limit=limits.max_history_chars,
        )
    return body


def render_manager_query(snapshot: ManagerSnapshot) -> str:
    """Reconstruct a fresh manager prompt from compact persisted state."""
    body = _fit_manager_query_body(snapshot)
    encoded = _encode_state(body)
    feedback = ""
    if snapshot.validation_feedback.strip():
        feedback = (
            "\n\nVALIDATION FEEDBACK FROM HOST (repair the decision format/preconditions; "
            "this is not task blockage):\n"
            f"{snapshot.validation_feedback.strip()}\n"
        )
    followup = ""
    if snapshot.operator_followups:
        latest = snapshot.operator_followups[-1]
        followup = (
            "\n\nOPERATOR FOLLOW-UP (host-injected; take this as the next steering instruction):\n"
            f"{latest.text.strip()}\n"
        )
    return (
        "MODE: manage\n"
        f"RUN_ID: {snapshot.task_state.run_id}\n"
        f"ROUND: {snapshot.round_index}\n"
        "Call submit_manager_decision exactly once.\n"
        f"{feedback}{followup}\n"
        f"{_STATE_MARKER.lstrip()}"
        f"{encoded}\n"
    )


class ManagerAgent:
    """Low-privilege router: original task + state + reports → one decision."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        model: Any | None = None,
        agent: Any | None = None,
        runner: Any | None = None,
        agent_factory: Callable[..., Any] | None = None,
        decide_fn: Callable[[ManagerSnapshot], ManagerDecision] | None = None,
        project_root_path: str | Path | None = None,
    ):
        self.config = config
        self._injected_model = model
        self._injected_agent = agent
        self._runner = runner
        self._agent_factory = agent_factory
        self._decide_fn = decide_fn
        self._root = Path(project_root_path).resolve() if project_root_path else project_root()
        set_project_root(self._root)
        self._cfg = dict(config.get("manager") or {})

    def _create_agent(self, *, run_id: str, submit_tool: SubmitManagerDecisionTool):
        if self._injected_agent is not None:
            return self._injected_agent
        if self._agent_factory is not None:
            return self._agent_factory(run_id=run_id, submit_tool=submit_tool)

        from openjiuwen.core.single_agent.schema.agent_card import AgentCard
        from openjiuwen.harness import create_deep_agent

        model = self._injected_model or _build_model_from_config(self.config)
        max_iterations = int(self._cfg.get("max_iterations", 8))
        return create_deep_agent(
            model=model,
            card=AgentCard(
                id=AGENT_CARD_ID,
                name=AGENT_CARD_NAME,
                description="Routes research modules from persistent task state.",
            ),
            tool_owner_id=f"manager-tools:{run_id}",
            system_prompt=_load_system_prompt(),
            tools=[submit_tool],
            rails=with_observability([ManagerCapabilityRail()]),
            enable_task_loop=False,
            max_iterations=max_iterations,
            cwd=str(self._root),
            project_root=str(self._root),
            restrict_to_work_dir=True,
            auto_create_workspace=False,
            language="en",
        )

    async def adecide(
        self,
        snapshot: ManagerSnapshot,
        *,
        query: str | None = None,
    ) -> ManagerDecision:
        if self._decide_fn is not None:
            return self._decide_fn(snapshot)

        run_id = snapshot.task_state.run_id
        session_id = f"manager-{run_id}-round-{snapshot.round_index}"
        request_id = f"decide-{run_id}-{snapshot.round_index}"
        submit_tool = SubmitManagerDecisionTool()
        submit_tool.reset(session_id=session_id, request_id=request_id)
        agent = self._create_agent(run_id=run_id, submit_tool=submit_tool)
        payload_query = query if query is not None else render_manager_query(snapshot)
        payload = {"query": payload_query, "conversation_id": session_id}

        from openjiuwen.core.session.agent import Session

        session = Session(session_id=session_id, card=getattr(agent, "card", None))
        try:
            await session.pre_run(inputs=payload)
            if self._runner is not None:
                await self._runner(agent, payload, session=session)
            else:
                from openjiuwen.core.runner import Runner

                await Runner.run_agent(agent, payload, session=session)
        finally:
            try:
                await session.post_run()
            except Exception:  # noqa: BLE001, S110 - best-effort commit/close
                pass
        return submit_tool.require_submission(session_id=session_id, request_id=request_id)
