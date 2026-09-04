"""Reflection Agent: judges an ExperimentResult against its plan's hypothesis.

See docs/reflection_design.md. Deliberately lightweight: plan + result are fully known
upfront, so it uses a single-shot completion rather than a multi-turn DeepAgent — no 
checkpointer or context-engine config.

Unlike experiment_design, reflection directly writes a small markdown artifact via a
scoped write_file tool. Since the output is prose and nothing downstream branches on
it, there is no schema or host-side templating; the host only specifies the output
path and reads the result back.

Must-have context (design story, implementation assumptions, final metrics) is preloaded
in the prompt. For anything else, the agent can use ReflectionToolsRail's read_file/list_files
over the run workspace to inspect logs, design docs, or generated code on demand.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    reflection_dir,
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
    ReflectionOutput,
)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"


class ReflectionAgent:
    """Turns an ExperimentResult (+ its plan) into a Reflection: the model
    writes a grounded markdown judgment straight to reflection_path(run_id,
    revision) via a scoped write_file tool; the host reads it back and stamps
    provenance. Runs right after experiment_execution — there is no
    evaluation module in this pipeline; reflection is the only thing that
    judges a result against its hypothesis.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

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
        # Path relative to the (now wider) workspace root — mirrors
        # reflection_path's own reflection_dir(run_id)/revision-N.md shape.
        target_filename = f"reflection/revision-{plan.revision}.md"
        task_prompt = inputs.extra_host_instructions + self._build_task_prompt(
            inputs, hypothesis_text, objective_text, design_context, target_filename
        )

        from openjiuwen.core.runner import Runner

        session_id = f"reflection-{plan.run_id}-{plan.revision}"
        await Runner.start()
        try:
            agent = self._build_reflection_agent(workspace)
            await Runner.run_agent(
                agent,
                {"query": task_prompt, "conversation_id": session_id},
            )
        finally:
            await Runner.stop()

        return ReflectionOutput(reflection=self._finalize_reflection(plan, target_path))

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

    def _build_reflection_agent(self, workspace: Path):
        from openjiuwen.core.foundation.llm import init_model
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard
        from openjiuwen.harness import create_deep_agent

        from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.observability_rail import (
            with_observability,
        )
        from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.reflection_tools_rail import (
            ReflectionToolsRail,
        )

        model = init_model(
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
                    "writes a grounded markdown reflection."
                ),
            ),
            system_prompt=self._render_system_prompt(),
            rails=with_observability([ReflectionToolsRail()]),
            # No task loop — but a couple of extra iterations beyond "read
            # then write" are still bounded, not unbounded exploration.
            enable_task_loop=False,
            max_iterations=int(module_cfg.get("max_iterations", 6)),
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
    def _build_task_prompt(
        inputs: ReflectionInput,
        hypothesis_text: str | None,
        objective_text: str | None,
        design_context: str | None,
        target_filename: str,
    ) -> str:
        result = inputs.result
        variant_lines = [
            f"- **{variant.name}** (exit_code={variant.exit_code}): "
            + (", ".join(f"{k}={v}" for k, v in variant.metrics.items()) or "(no metrics)")
            for variant in result.variants
        ] or ["(no variants)"]
        implementation_block = ReflectionAgent._build_implementation_block(inputs.implementation)
        extra_material_lines = [f"- `logs/{variant.name}.log`" for variant in result.variants]
        extra_material_lines.append("- `design/experiment_design.md` (the raw design document)")
        extra_material_lines.append("- `generated_code/` (the actual implementation)")

        return (
            "Reflect on this experiment result against the plan's hypothesis. You are "
            "given the whole story of this one experiment round below — the design, "
            "what was actually built, and the real results — not just the headline "
            "numbers; use all of it.\n\n"
            f"## Objective\n\n{objective_text or '(not available)'}\n\n"
            f"## Hypothesis\n\n{hypothesis_text or '(not available)'}\n\n"
            f"## Full experiment design\n\n{design_context or '(not available)'}\n\n"
            f"## What was actually implemented\n\n{implementation_block}\n\n"
            f"## Result status\n\n{result.status}\n\n"
            f"## Per-variant results\n\n" + "\n".join(variant_lines) + "\n\n"
            "## Extra material (optional)\n\n"
            "Everything above should usually be enough. If something specific is "
            "still unclear, you also have read_file/list_files, scoped to this run's "
            "full experiment folder, to look further — for example:\n"
            + "\n".join(extra_material_lines) + "\n\n"
            "Only read something if the context above leaves a real gap in what you "
            "need to judge the hypothesis — don't read speculatively.\n\n"
            "TASK: Judge whether this result supports, refutes, is mixed on, or is "
            "inconclusive about the hypothesis above. Ground your reasoning in the "
            "concrete numbers under 'Per-variant results' — do not invent data. Use "
            "'Full experiment design' and 'What was actually implemented' to notice "
            "things the numbers alone can't tell you: where the implementation had to "
            "diverge from the design (synthetic data, a substituted library, a "
            "narrower scope than the hypothesis actually claims), which of the "
            "design's stated risks/assumptions turned out to matter, and what about "
            "the protocol would need to change to test the hypothesis more directly. "
            "Follow-up ideas grounded in those specifics are far more useful than "
            "ones grounded only in whether a number went up or down.\n\n"
            f"Use the write_file tool to write your reflection to `{target_filename}` "
            "(a path relative to your workspace root — do not use an absolute path or "
            "any other filename) using this structure, then stop:\n\n"
            "```\n"
            "# Reflection\n\n"
            "**Hypothesis verdict:** <supported|refuted|mixed|inconclusive>\n\n"
            "## Rationale\n\n<grounded in the numbers above>\n\n"
            "## Insights\n\n- <what's surprising or generalizable, or omit this "
            "section if there's nothing beyond the headline verdict>\n\n"
            "## Follow-up ideas\n\n- <candidate directions this result's outcome "
            "suggests, specific to what actually happened in this round — not "
            "generic advice like \"run more experiments\" — or omit this section "
            "if none>\n"
            "```\n"
        )

    # -- finalizing the artifact ------------------------------------------------

    @staticmethod
    def _finalize_reflection(plan: ExperimentPlan, target_path: Path) -> Reflection:
        if not target_path.is_file():
            raise RuntimeError(
                f"reflection agent did not write {to_project_relative(target_path)}"
            )
        return Reflection(
            run_id=plan.run_id,
            revision=plan.revision,
            reflection_path=to_project_relative(target_path),
            content=target_path.read_text(encoding="utf-8"),
            created_at=datetime.now(UTC),
        )
