"""Reflection Agent: judges an ExperimentResult against its plan's hypothesis.

The host preloads a structural metrics summary (not item-record lists or
generated code). The model may grep/read workspace files, then submits a
structured ReflectionJudgment via submit_reflection; the host validates the
primary-metric citation, renders markdown, and stamps provenance.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.metrics import (
    summarize_metrics_for_prompt,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    reflection_dir,
    reflection_metrics_summary_path,
    reflection_path,
    resolve_project_reference,
    to_project_relative,
    workspace_dir,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.schemas import (
    CodeImplementationManifest,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.artifacts import (
    current_claim_text,
    parse_design_document,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import ExperimentPlan
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reflection.schemas import (
    Reflection,
    ReflectionInput,
    ReflectionJudgment,
    ReflectionOutput,
    judgment_cites_primary,
    render_reflection_markdown,
)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"
_ENTRY_POINT = "run.py"
_ITEM_RECORD_KEYS = frozenset({"per_question", "task_records", "records", "item_records"})
_OBSERVATIONS_KEY = "observations"


class ReflectionAgent:
    """Turns an ExperimentResult (+ its plan) into a Reflection.

    The model submits a structured judgment; the host renders markdown to
    reflection_path(run_id, revision) and stamps provenance.
    """

    def __init__(self, config: dict[str, Any], *, model: Any | None = None):
        self.config = config
        self._injected_model = model

    def run(self, inputs: ReflectionInput) -> ReflectionOutput:
        return asyncio.run(self.arun(inputs))

    async def arun(self, inputs: ReflectionInput) -> ReflectionOutput:
        return await self._run_async(inputs)

    async def _run_async(self, inputs: ReflectionInput) -> ReflectionOutput:
        plan = inputs.plan
        hypothesis_text, objective_text, design_context = self._read_design_context(plan)

        # Workspace is the whole run folder, not just reflection/ — read_file
        # needs to reach design/, generated_code/, logs/ etc. for optional
        # extra context; the write target is still exactly reflection_path.
        workspace = workspace_dir(plan.run_id).resolve()
        reflection_dir(plan.run_id).mkdir(parents=True, exist_ok=True)
        target_path = reflection_path(plan.run_id, plan.revision)
        request_id = f"reflection-{plan.run_id}-{plan.revision}"
        summary_payload = ReflectionAgent._metrics_summary_payload(plan, result=inputs.result)
        ReflectionAgent._write_metrics_summary(plan, summary_payload)
        task_prompt = inputs.extra_host_instructions + self._build_task_prompt(
            inputs, hypothesis_text, objective_text, design_context, summary_payload
        )

        from openjiuwen.core.runner import Runner
        from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.tools.submit_reflection import (
            SubmitReflectionTool,
        )

        session_id = request_id
        submit_tool = SubmitReflectionTool()
        submit_tool.reset(request_id=request_id)
        await Runner.start()
        agent = None
        try:
            agent = self._build_reflection_agent(workspace, submit_tool=submit_tool)
            await Runner.run_agent(
                agent,
                {"query": task_prompt, "conversation_id": session_id},
            )
        finally:
            # Runner is process-global.  Stopping it here releases every
            # agent's resource-manager tool, including agents running in a
            # different paper task.  The server owns the Runner lifetime;
            # this module must only finish its own invocation.
            cleanup = getattr(agent, "cleanup_task_resources", None)
            if callable(cleanup):
                await cleanup()
            unregister = getattr(agent, "unregister_rail", None)
            configured_rails = getattr(agent, "configured_rails", None)
            if callable(unregister) and callable(configured_rails):
                for rail in reversed(list(configured_rails())):
                    try:
                        await unregister(rail)
                    except Exception:
                        # Cleanup must not mask the agent result.
                        pass
            ability_manager = getattr(agent, "ability_manager", None)
            teardown = getattr(ability_manager, "teardown_tools", None)
            if callable(teardown):
                teardown()
            sys_operation = getattr(getattr(agent, "deep_config", None), "sys_operation", None)
            sys_operation_id = getattr(sys_operation, "id", None)
            if sys_operation_id:
                from openjiuwen.core.runner import Runner

                try:
                    Runner.resource_mgr.remove_sys_operation(sys_operation_id)
                except Exception:
                    # Cleanup must not mask the agent result.
                    pass

        judgment = submit_tool.require_submission(request_id=request_id)
        judgment_cites_primary(judgment, plan.primary_metric)
        return ReflectionOutput(
            reflection=self._finalize_reflection(plan, target_path, judgment)
        )

    # -- reading the plan's design story --------------------------------------

    @staticmethod
    def _read_design_context(
        plan: ExperimentPlan,
    ) -> tuple[str | None, str | None, str | None]:
        """(hypothesis, objective, full design story) from plan.design_path,
        or (None, None, None) if the path is empty, escapes the project root,
        or the file doesn't exist. Reflection must still produce a meaningful
        result without this — just with less context in the prompt.

        `hypothesis`/`objective` are the two headline current claims (cheap
        callouts the prompt puts up front); the third value is the whole
        design body minus the revision log — baseline/intervention/protocol,
        risks & assumptions, research grounding — via
        ParsedDesignDocument.current_sections, reused as-is rather than
        picking it apart. Judging a result needs the actual experiment story
        (what was being tested and why), not just the hypothesis sentence in
        isolation — that's what makes follow-up ideas specific instead of
        generic.
        """
        if not plan.design_path:
            return None, None, None
        try:
            abs_path = resolve_project_reference(plan.design_path)
        except ValueError:
            return None, None, None
        if not abs_path.is_file():
            return None, None, None
        doc = parse_design_document(abs_path.read_text(encoding="utf-8"))
        return (
            current_claim_text(doc.body, "hypothesis"),
            current_claim_text(doc.body, "objective"),
            doc.current_sections,
        )

    # -- agent construction ---------------------------------------------------

    def _build_reflection_agent(self, workspace: Path, *, submit_tool):
        from openjiuwen.core.foundation.llm import init_model
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard
        from openjiuwen.harness import create_deep_agent

        from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.observability_rail import (
            with_observability,
        )
        from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.reflection_tools_rail import (
            ReflectionToolsRail,
        )

        model = self._injected_model or init_model(
            provider=self._setting("provider", "MODEL_PROVIDER", default="OpenAI"),
            model_name=self._setting("model", "MODEL_NAME", default="default"),
            api_key=self._setting("api_key", "API_KEY", required=True, secret=True),
            api_base=self._setting("base_url", "API_BASE", required=True),
            timeout=float(self._setting("timeout", "MODEL_TIMEOUT", default="120")),
        )
        module_cfg = self.config.get("reflection", {}) or {}
        return create_deep_agent(
            model,
            card=AgentCard(
                name="reflection_agent",
                description=(
                    "Judges an experiment result against its hypothesis and "
                    "submits a structured scientific judgment."
                ),
            ),
            system_prompt=self._render_system_prompt(),
            tools=[submit_tool],
            rails=with_observability([ReflectionToolsRail()]),
            enable_task_loop=False,
            max_iterations=int(module_cfg.get("max_iterations", 20)),
            tool_owner_id=f"rsi-reflection-{workspace.name}",
            workspace=str(workspace),
            auto_create_workspace=False,
        )

    def _setting(
        self,
        config_key: str,
        env_key: str,
        *,
        default: str | None = None,
        required: bool = False,
        secret: bool = False,
    ) -> str | None:
        # env wins over configs/*.yaml — see code_implementation/agent.py's
        # identical helper for why (checked-in placeholders must not shadow a
        # real local override; api_key never comes from yaml at all).
        oj_cfg = {} if secret else (self.config.get("openjiuwen", {}) or {})
        value = os.environ.get(env_key) or oj_cfg.get(config_key) or default
        if required and not value:
            raise RuntimeError(
                f"reflection needs a model {config_key} — set "
                f"the {env_key} environment variable"
                + ("" if secret else f" or configs['openjiuwen']['{config_key}']") + "."
            )
        return value

    @staticmethod
    def _render_system_prompt() -> str:
        return _PROMPT_PATH.read_text(encoding="utf-8")

    # -- task prompt -----------------------------------------------------------

    @staticmethod
    def _build_implementation_block(implementation: CodeImplementationManifest | None) -> str:
        if implementation is None:
            return "(not available)"
        lines = [
            f"Status: {implementation.status}; "
            f"smoke_test_passed={implementation.smoke_test_passed}"
        ]
        assumptions = "\n".join(f"- {item}" for item in implementation.assumptions)
        lines.append(
            "Judgment calls made turning the design into runnable code:\n"
            + (assumptions or "(none recorded)")
        )
        if implementation.notes:
            lines.append(f"Implementation notes:\n{implementation.notes}")
        return "\n\n".join(lines)

    @staticmethod
    def _dump_metrics(metrics: dict[str, Any]) -> str:
        try:
            return json.dumps(metrics, indent=2, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(metrics)

    @staticmethod
    def _logged_payload_keys(metrics: dict[str, Any]) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        def add(name: str) -> None:
            if name and name not in seen:
                seen.add(name)
                names.append(name)

        for key in metrics:
            if str(key) in _ITEM_RECORD_KEYS:
                continue
            add(str(key))
        nested = metrics.get("metrics")
        if isinstance(nested, dict):
            for key in nested:
                if str(key) == _OBSERVATIONS_KEY:
                    continue
                add(f"metrics.{key}")
        observations = metrics.get(_OBSERVATIONS_KEY)
        if not isinstance(observations, dict) and isinstance(nested, dict):
            observations = nested.get(_OBSERVATIONS_KEY)
        if isinstance(observations, dict):
            for key in observations:
                add(f"observations.{key}")
        return names

    @staticmethod
    def _observations_block(plan: ExperimentPlan, variants: list[Any]) -> str:
        requested = [item.strip() for item in plan.observations if str(item).strip()]
        logged: list[str] = []
        seen: set[str] = set()
        for variant in variants:
            for name in ReflectionAgent._logged_payload_keys(dict(variant.metrics or {})):
                if name not in seen:
                    seen.add(name)
                    logged.append(name)
        requested_text = "\n".join(f"- {item}" for item in requested) or "- (none requested)"
        logged_text = "\n".join(f"- `{item}`" for item in logged) or "- (none found in the payloads)"
        return (
            "Requested (advisory phrases from the design — not JSON keys):\n"
            f"{requested_text}\n\n"
            "Keys actually present in the metrics payloads:\n"
            f"{logged_text}"
        )

    @staticmethod
    def _workspace_rel(run_id: str, path: str, *, fallback: str) -> str:
        cleaned = str(path or "").strip().replace("\\", "/")
        if not cleaned:
            return fallback
        workspace = workspace_dir(run_id).resolve()
        candidate = Path(cleaned)
        try:
            if not candidate.is_absolute():
                candidate = resolve_project_reference(cleaned)
            return candidate.resolve().relative_to(workspace).as_posix()
        except (ValueError, OSError):
            prefix = f"experiments/{run_id}/"
            if cleaned.startswith(prefix):
                return cleaned[len(prefix):]
            if not Path(cleaned).is_absolute():
                return cleaned.lstrip("./")
            return fallback

    @staticmethod
    def _workspace_catalog(plan: ExperimentPlan, result: Any) -> str:
        lines: list[str] = []
        for variant in result.variants:
            metrics_rel = f"results/{variant.name}.metrics.json"
            log_rel = ReflectionAgent._workspace_rel(
                plan.run_id,
                str(getattr(variant, "log_path", "") or ""),
                fallback=f"logs/{variant.name}.log",
            )
            lines.append(
                f"- `{metrics_rel}` — full metrics JSON for `{variant.name}` "
                "(item-record lists omitted from the summary above)"
            )
            lines.append(f"- `{log_rel}` — run log for `{variant.name}`")
        lines.append("- `design/experiment_design.md` — living design summary")
        lines.append(
            f"- `generated_code/{_ENTRY_POINT}` — generated entry point "
            "(read if you suspect the code measured the wrong thing)"
        )
        lines.append("- `generated_code/` — rest of the implementation")
        lines.append(
            f"- `reflection/revision-{plan.revision}.metrics_summary.json` — "
            "host-built compact summary (same payload as the prompt)"
        )
        return "\n".join(lines)

    @staticmethod
    def _metrics_summary_payload(plan: ExperimentPlan, *, result: Any) -> dict[str, Any]:
        variants: list[dict[str, Any]] = []
        for variant in result.variants:
            metrics_path = f"results/{variant.name}.metrics.json"
            variants.append(
                {
                    "name": variant.name,
                    "exit_code": variant.exit_code,
                    "process_status": variant.process_status,
                    "metrics_state": variant.metrics_state,
                    "metrics_path": metrics_path,
                    "log_path": ReflectionAgent._workspace_rel(
                        plan.run_id,
                        str(getattr(variant, "log_path", "") or ""),
                        fallback=f"logs/{variant.name}.log",
                    ),
                    "summary": summarize_metrics_for_prompt(
                        dict(variant.metrics or {}), path=metrics_path
                    ),
                }
            )
        return {
            "run_id": plan.run_id,
            "revision": plan.revision,
            "primary_metric": plan.primary_metric,
            "primary_direction": plan.primary_direction,
            "result_status": result.status,
            "variants": variants,
        }

    @staticmethod
    def _write_metrics_summary(plan: ExperimentPlan, payload: dict[str, Any]) -> Path:
        path = reflection_metrics_summary_path(plan.run_id, plan.revision)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _build_task_prompt(
        inputs: ReflectionInput,
        hypothesis_text: str | None,
        objective_text: str | None,
        design_context: str | None,
        summary_payload: dict[str, Any] | None = None,
    ) -> str:
        plan = inputs.plan
        result = inputs.result
        payload = summary_payload or ReflectionAgent._metrics_summary_payload(plan, result=result)
        variant_blocks: list[str] = []
        for variant in payload.get("variants") or []:
            body = ReflectionAgent._dump_metrics(dict(variant.get("summary") or {}))
            variant_blocks.append(
                f"### {variant.get('name')}\n\n"
                f"exit_code={variant.get('exit_code')}; "
                f"process_status={variant.get('process_status')}; "
                f"metrics_state={variant.get('metrics_state')}\n\n"
                f"```json\n{body}\n```"
            )
        variants_text = "\n\n".join(variant_blocks) or "(no variants)"
        implementation_block = ReflectionAgent._build_implementation_block(inputs.implementation)
        primary = plan.primary_metric or "(unspecified)"
        direction = plan.primary_direction or "unspecified"
        observations_block = ReflectionAgent._observations_block(plan, list(result.variants))
        catalog = ReflectionAgent._workspace_catalog(plan, result)

        return (
            "Judge this experiment result against the pre-committed hypothesis and "
            "primary metric. The metrics below are a **summary**: object lists "
            "(item records) are omitted. Use grep/read_file for item-level or "
            "code detail, then submit exactly one structured judgment.\n\n"
            f"## Objective\n\n{objective_text or '(not available)'}\n\n"
            f"## Hypothesis\n\n{hypothesis_text or '(not available)'}\n\n"
            f"## Primary metric\n\n`{primary}` ({direction})\n\n"
            "At least one `evidence` item must cite this primary metric name. "
            "If you call the round a success on other grounds, set "
            "`reinterpreted: true` and explain.\n\n"
            f"## Requested observations (advisory)\n\n{observations_block}\n\n"
            f"## Full experiment design\n\n{design_context or '(not available)'}\n\n"
            f"## What was actually implemented\n\n{implementation_block}\n\n"
            f"## Result status\n\n{result.status}\n\n"
            f"## Per-variant metrics (summary)\n\n{variants_text}\n\n"
            "## Workspace files\n\n"
            "Use grep, read_file (offset/limit), glob, or list_files on this run "
            "folder. Prefer grep on large JSON/logs; do not slurp an entire file.\n"
            + catalog
            + "\n\n"
            "TASK: Call `submit_reflection` exactly once with a structured "
            "judgment after you have enough evidence. Judge validity first, then "
            "the hypothesis against the primary metric and direction, then "
            "objective progress. `recommendation` is a hint for the manager, "
            "not an instruction. Ground every evidence item in the summary or "
            "in files you read. Then stop.\n"
        )

    @staticmethod
    def _finalize_reflection(
        plan: ExperimentPlan,
        target_path: Path,
        judgment: ReflectionJudgment,
    ) -> Reflection:
        content = render_reflection_markdown(judgment, primary_metric=plan.primary_metric)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")
        return Reflection(
            run_id=plan.run_id,
            revision=plan.revision,
            reflection_path=to_project_relative(target_path),
            content=content,
            created_at=datetime.now(UTC),
            judgment=judgment,
        )
