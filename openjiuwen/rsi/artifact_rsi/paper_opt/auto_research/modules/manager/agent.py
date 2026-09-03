"""Manager decision agent: one fresh LLM round, one typed control decision."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.env import load_project_dotenv
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import project_root, set_project_root
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.manager_capability_rail import ManagerCapabilityRail
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.observability_rail import with_observability
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.tools.submit_manager_decision import (
    SubmitManagerDecisionTool,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.artifacts import bounded_text
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import ManagerDecision, ManagerSnapshot

AGENT_CARD_ID = "manager-agent"
AGENT_CARD_NAME = "manager"
_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "system.md"


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


def _compact_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return None
    return {
        "revision": plan.get("revision"),
        "status": plan.get("status"),
        "design_path": plan.get("design_path"),
        "code_agent_instruction_path": plan.get("code_agent_instruction_path"),
        "metrics": plan.get("metrics") or [],
        "baselines": plan.get("baselines") or [],
        "expected_outcomes": bounded_text(str(plan.get("expected_outcomes") or ""), 400),
    }


def _compact_contract(contract: dict[str, Any] | None) -> dict[str, Any] | None:
    if not contract:
        return None
    return {
        "module": contract.get("module"),
        "mode": contract.get("mode"),
        "goal": contract.get("goal"),
    }


def _compact_task_state(snapshot: ManagerSnapshot) -> dict[str, Any]:
    data = snapshot.task_state.model_dump(mode="json")
    data["latest_plan"] = _compact_plan(data.get("latest_plan"))
    data["last_contract"] = _compact_contract(data.get("last_contract"))
    data["pending_contract"] = _compact_contract(data.get("pending_contract"))
    return data


def _compact_report(report: Any, max_chars: int) -> dict[str, Any]:
    data = report.model_dump(mode="json")
    handoff = data.get("handoff")
    if isinstance(handoff, dict):
        handoff.pop("failure_excerpts", None)
        for variant in handoff.get("variants") or []:
            if isinstance(variant, dict):
                variant.pop("excerpt", None)
        data["handoff"] = handoff
    encoded = json.dumps(data, ensure_ascii=False)
    if len(encoded) <= max_chars:
        return data
    data["summary"] = bounded_text(str(data.get("summary") or ""), min(400, max_chars))
    return data


def render_manager_query(snapshot: ManagerSnapshot) -> str:
    """Reconstruct a fresh manager prompt from compact persisted state."""
    limits = snapshot.task_state.limits
    reports = [_compact_report(report, limits.max_report_chars) for report in snapshot.reports]
    routing = snapshot.routing.model_dump(mode="json") if snapshot.routing is not None else {}
    body = {
        "routing": routing,
        "round_index": snapshot.round_index,
        "original_task": snapshot.original_task.model_dump(mode="json"),
        "task_state": _compact_task_state(snapshot),
        "reports": reports,
    }
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
    body["operator_followups"] = [
        item.model_dump(mode="json") for item in snapshot.operator_followups
    ]
    encoded = json.dumps(body, ensure_ascii=False, indent=2)
    encoded = bounded_text(encoded, limits.max_history_chars)
    return (
        "MODE: manage\n"
        f"RUN_ID: {snapshot.task_state.run_id}\n"
        f"ROUND: {snapshot.round_index}\n"
        "Call submit_manager_decision exactly once.\n"
        f"{feedback}{followup}\n"
        "STATE:\n"
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

    async def adecide(self, snapshot: ManagerSnapshot) -> ManagerDecision:
        if self._decide_fn is not None:
            return self._decide_fn(snapshot)

        run_id = snapshot.task_state.run_id
        session_id = f"manager-{run_id}-round-{snapshot.round_index}"
        request_id = f"decide-{run_id}-{snapshot.round_index}"
        submit_tool = SubmitManagerDecisionTool()
        submit_tool.reset(session_id=session_id, request_id=request_id)
        agent = self._create_agent(run_id=run_id, submit_tool=submit_tool)
        query = render_manager_query(snapshot)
        payload = {"query": query, "conversation_id": session_id}

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
