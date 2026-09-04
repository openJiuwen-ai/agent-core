"""Reporting Agent: turns a completed run's survey/plan/result/reflection
into a compiled paper (main.pdf + sections/*.tex + refs.bib + figures). See
docs/paper_writing_design.md.

Skills-driven DeepAgent session: the model itself decides when to draft,
check, and compile, using four skills (ts-plan/ts-write/ts-review/ts-latex)
that mirror code_implementation's own pattern (create_deep_agent + guarded
shell + fs tools, scoped to this run's paper workspace). Deterministic work
stays host Python either way — bibliography/figure building happen before
the agent starts, and the lint/citation/compile checks the skills' scripts
call are the same already-tested functions in lint.py/latex.py, just invoked
by the agent instead of a fixed host loop. The host still runs one
independent verification pass after the session ends and never trusts the
agent's own report of "done" — same rule code_implementation applies to its
smoke tests.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.logging import get_logger
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    paper_figures_dir,
    paper_output_path,
    paper_refs_bib_path,
    paper_sections_dir,
    paper_workspace_dir,
    project_root,
    resolve_project_reference,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.artifacts import (
    current_claim_text,
    latest_claim_text,
    parse_design_document,
    read_section_inner,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import (
    ExperimentPlan,
    ResearchBrief,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting import bibliography, figures, lint
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.bibliography import Bibliography
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.latex import (
    escape_latex,
    render_results_table,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.schemas import (
    ReportingInput,
    ReportingOutput,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.sections import DOCUMENT_ORDER, SECTIONS

_LOGGER = get_logger(__name__)

_SKILLS_DIR = Path(__file__).parent / "skills"
_ALL_SKILL_NAMES = ("ts-plan", "ts-write", "ts-figure", "ts-review", "ts-latex")
_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"
_REVIEWER_SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "reviewer_system_prompt.md"
_SECTIONS_BY_ID = {spec.id: spec for spec in SECTIONS}
_CURRENT_EXPERIMENT_HEADING = "## Current Experiment"
_RESEARCH_GROUNDING_HEADING = "## Research Grounding"
# Lives at the workspace root (not under sections/) — host-written on a
# failed attempt, read back (and overwritten) on the next retry. Named
# loudly/uppercase so it stands out among sections/*.tex in a directory
# listing, same reasoning as skills' own {PAPER_WORKSPACE} convention.
_PREVIOUS_ATTEMPT_NOTES_FILENAME = "PREVIOUS_ATTEMPT_NOTES.md"


def _format_metric(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "--"
    return f"{value:.4g}"


class ReportingAgent:
    """Builds refs.bib and figures deterministically, then runs one
    skills-driven DeepAgent session (ts-plan/ts-write/ts-review/ts-latex)
    to write and compile the paper. See docs/paper_writing_design.md.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._pw_config = dict(config.get("reporting") or {})

    def run(self, inputs: ReportingInput) -> ReportingOutput:
        import asyncio

        return asyncio.run(self._run_async(inputs))

    async def arun(self, inputs: ReportingInput) -> ReportingOutput:
        return await self._run_async(inputs)

    async def _run_async(self, inputs: ReportingInput) -> ReportingOutput:
        run_id = inputs.plan.run_id
        workspace = paper_workspace_dir(run_id)
        # First attempt for this run: wipe fresh, same "one-shot task" stance
        # topic_survey takes for the same reason (docs/paper_writing_design.md
        # §9). A retry (attempt > 1) keeps the workspace instead — the
        # evidence (survey/plan/result/reflection) is identical to the failed
        # attempt's, so there is no staleness risk within one run, and
        # rewriting every section from zero every time is exactly what turned
        # a single completion_timeout into a run that could never finish.
        # Read any previous-attempt notes before deciding whether to wipe,
        # since a wipe would otherwise take them with it.
        notes_path = workspace / _PREVIOUS_ATTEMPT_NOTES_FILENAME
        previous_attempt_notes = ""
        if inputs.attempt > 1 and notes_path.is_file():
            previous_attempt_notes = notes_path.read_text(encoding="utf-8")
        if inputs.attempt <= 1 and workspace.exists():
            shutil.rmtree(workspace)
        sections_dir = paper_sections_dir(run_id)
        figures_dir = paper_figures_dir(run_id)
        sections_dir.mkdir(parents=True, exist_ok=True)
        figures_dir.mkdir(parents=True, exist_ok=True)

        design_context = self._read_design_context(inputs.plan)
        background = self._read_survey_summary(inputs.survey)

        summary_path = self._resolve_summary_path(inputs.survey)
        bib = (
            bibliography.build_bibliography(
                summary_path, network_timeout=float(self._pw_config.get("bibliography_timeout", 5.0))
            )
            if summary_path is not None
            else Bibliography(bib_text="", title_to_key={}, known_keys=set())
        )
        refs_bib_path = paper_refs_bib_path(run_id)
        refs_bib_path.write_text(bib.bib_text, encoding="utf-8")

        figure_path = figures.build_results_figure(inputs.result, figures_dir / "results.pdf")
        figure_paths = [str(figure_path)] if figure_path else []

        # Data for the skill scripts (ts-review/ts-latex) — the agent's own
        # shell tool runs them, so their inputs have to live on disk, not in
        # a Python closure. Never evidence for the agent to write prose
        # from; the system prompt says so explicitly.
        (workspace / "results.json").write_text(inputs.result.model_dump_json(), encoding="utf-8")
        (workspace / "known_citation_keys.json").write_text(
            json.dumps(sorted(bib.known_keys)), encoding="utf-8"
        )

        evidence = self._build_evidence_blocks(inputs, design_context, background, bib, figure_path)
        # Host-authored (previous_attempt_notes, from the deterministic
        # verification pass) comes first — it's the ground truth. The
        # manager's own repair_instruction is layered after as optional
        # strategic commentary, not a replacement for it.
        repair_text = "\n\n".join(
            part for part in (previous_attempt_notes, inputs.repair_instruction) if part
        )
        query = self._build_task_query(evidence, repair_text)

        session_error = await self._run_paper_agent(run_id=run_id, query=query)

        output = self._verify_and_build_output(
            run_id=run_id,
            workspace=workspace,
            sections_dir=sections_dir,
            refs_bib_path=refs_bib_path,
            figure_paths=figure_paths,
            known_keys=bib.known_keys,
            result=inputs.result,
            session_error=session_error,
        )
        # Persist for the next retry to read back above — overwritten every
        # attempt (this attempt's outcome, not an accumulating history) so a
        # retry only ever sees what actually went wrong last time, never a
        # stale earlier failure that's since been fixed. Removed on success:
        # nothing left to warn a future attempt about, and a successful run
        # is terminal anyway (no next attempt will read it).
        if output.status == "failed":
            notes_path.write_text(output.notes or "(no diagnostic notes)", encoding="utf-8")
        elif notes_path.is_file():
            notes_path.unlink()
        return output

    # -- reading upstream context ---------------------------------------------

    @staticmethod
    def _read_design_context(plan: ExperimentPlan) -> dict[str, str | None]:
        """objective/hypothesis/grounding/experiment text from
        plan.design_path, or all-None if the path is empty, escapes the
        project root, or the file doesn't exist — this module must still
        produce a reasonable paper without it, same rule
        reflection/agent.py::_read_design_claims already follows."""
        empty: dict[str, str | None] = {
            "objective": None,
            "hypothesis": None,
            "grounding": None,
            "experiment": None,
        }
        if not plan.design_path:
            return empty
        try:
            abs_path = resolve_project_reference(plan.design_path)
        except ValueError:
            return empty
        if not abs_path.is_file():
            return empty
        doc = parse_design_document(abs_path.read_text(encoding="utf-8"))
        body = doc.body
        context = dict(empty)
        context["objective"] = current_claim_text(body, "objective") or latest_claim_text(
            body, "objective"
        )
        context["hypothesis"] = current_claim_text(body, "hypothesis") or latest_claim_text(
            body, "hypothesis"
        )
        for key, heading in (
            ("grounding", _RESEARCH_GROUNDING_HEADING),
            ("experiment", _CURRENT_EXPERIMENT_HEADING),
        ):
            try:
                context[key] = read_section_inner(body, heading) or None
            except ValueError:
                context[key] = None
        return context

    @staticmethod
    def _read_survey_summary(survey: ResearchBrief) -> str | None:
        """The first entry in survey.resource_paths is topic_survey's own
        curated research_summary.md (topic_survey/agent.py::asurvey) — read
        that one bounded file rather than granting a file-reading tool.
        resource_paths is min_length=1 on ResearchBrief, so it's never empty
        here."""
        try:
            abs_path = resolve_project_reference(survey.resource_paths[0])
        except ValueError:
            return None
        if not abs_path.is_file():
            return None
        return abs_path.read_text(encoding="utf-8")

    @staticmethod
    def _resolve_summary_path(survey: ResearchBrief) -> Path | None:
        try:
            path = resolve_project_reference(survey.resource_paths[0])
        except ValueError:
            return None
        return path if path.is_file() else None

    # -- evidence blocks: one per section, host-built ------------------------

    @staticmethod
    def _build_evidence_blocks(
        inputs: ReportingInput,
        design_context: dict[str, str | None],
        background: str | None,
        bib: Bibliography,
        figure_path: Path | None,
    ) -> dict[str, str]:
        result = inputs.result
        citation_list = (
            "\n".join(f"- {title} -> \\cite{{{key}}}" for title, key in bib.title_to_key.items())
            or "(no sources recovered)"
        )

        metric_name_set: set[str] = set()
        for variant in result.variants:
            for name, value in variant.metrics.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    metric_name_set.add(name)
        metric_names = sorted(metric_name_set)
        rows = [
            (
                escape_latex(variant.name),
                {escape_latex(name): _format_metric(variant.metrics.get(name)) for name in metric_names},
            )
            for variant in result.variants
        ]
        table_tex = render_results_table(rows, [escape_latex(name) for name in metric_names])

        figure_tex = ""
        if figure_path is not None:
            figure_tex = (
                f"\\begin{{figure}}[h]\\centering"
                f"\\includegraphics[width=0.9\\textwidth]{{figures/{figure_path.name}}}"
                f"\\caption{{Results by variant.}}\\end{{figure}}"
            )

        variant_lines = [
            f"- {escape_latex(variant.name)} (exit_code={variant.exit_code}): "
            + (
                ", ".join(f"{escape_latex(k)}={_format_metric(v)}" for k, v in variant.metrics.items())
                or "(no metrics)"
            )
            for variant in result.variants
        ] or ["(no variants)"]

        discussion_block = (
            inputs.reflection.content
            if inputs.reflection is not None
            else "(no reflection was recorded for this run; discuss the results directly "
            "against the hypothesis below instead)"
        )

        method_block = (
            f"Objective: {design_context.get('objective') or '(not available)'}\n"
            f"Hypothesis: {design_context.get('hypothesis') or '(not available)'}\n"
            f"Research grounding: {design_context.get('grounding') or '(not available)'}\n"
            f"Experiment design: {design_context.get('experiment') or '(not available)'}"
        )

        results_block = (
            f"Run status: {result.status}\n"
            + "\n".join(variant_lines)
            + "\n\nHost-rendered results table — include exactly as given, do not redraw it:\n\n"
            + (table_tex or "(no numeric metrics to tabulate)")
            + (
                "\n\nHost-rendered figure — include exactly as given:\n\n" + figure_tex
                if figure_tex
                else ""
            )
        )

        # Every section that's allowed to cite needs the exact valid keys —
        # a section grounded in "background" (introduction, related_work) is
        # exactly where citations are most needed, and having no key list at
        # all is what caused the model to invent plausible-looking keys from
        # filenames visible in the raw survey text instead (see run-ff1abb51's
        # \cite{GAIA_benchmark} vs the real generated key gaiaabenchmarkfo1c294c3f).
        citation_suffix = (
            f"\n\nCitable sources — use \\cite{{key}} only for these, never invent a key:\n{citation_list}"
        )

        return {
            "background": (background or "(not available)") + citation_suffix,
            "method": method_block + citation_suffix,
            "results": results_block + citation_suffix,
            "discussion": discussion_block + citation_suffix,
        }

    @staticmethod
    def _build_task_query(evidence: dict[str, str], repair_instruction: str = "") -> str:
        preamble = (
            "Write and compile the paper for this completed run using your skill set: ts-plan; "
            "ts-write; ts-review; ts-latex skills."
        )
        if repair_instruction:
            # The workspace itself is still wiped fresh (no section-file
            # resumability, see _run_async) — this is a heads-up, not
            # partial state to resume. Surfaced first, ahead of the
            # preamble, so it isn't buried under the evidence blocks.
            preamble = (
                f"A previous attempt at this report did not finish cleanly: "
                f"{repair_instruction}\nPrioritize actually finishing every "
                f"section, compiling, and staying within word/citation/"
                f"traceable-number requirements over polish. "
            ) + preamble
        return (
            preamble
            + "\n\n"
            + "\n\n".join(f"## Evidence: {key}\n\n{value}" for key, value in evidence.items())
        )

    def _enabled_skill_dirs(self) -> list[str]:
        """Explicit per-skill directory list rather than the whole
        {SKILLS_DIR} parent — the SDK's skill registration accepts either
        (confirmed against the installed harness: SkillManager.register
        treats each list entry as its own skill directory if it directly
        contains a SKILL.md, only falling back to scanning subdirectories
        when a single parent path is passed). This is what makes
        reporting.method_figure.enabled: false a real toggle — the model
        never sees ts-figure as an available skill at all, not just a
        skill it's told not to use."""
        method_figure_enabled = bool((self._pw_config.get("method_figure") or {}).get("enabled", True))
        return [
            str(_SKILLS_DIR / name)
            for name in _ALL_SKILL_NAMES
            if name != "ts-figure" or method_figure_enabled
        ]

    # -- agent construction: mirrors code_implementation's _build_coding_agent
    # (create_deep_agent + guarded shell + fs tools scoped to a workspace) --

    def _build_paper_agent(self, *, run_id: str):
        from openjiuwen.core.foundation.llm import init_model
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard
        from openjiuwen.harness import create_deep_agent
        from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
        from openjiuwen.harness.schema.config import SubAgentConfig

        from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.observability_rail import (
            with_observability,
        )

        # A real agentic session turn carries a lot of accumulated context
        # (evidence blocks, prior tool results, read-back section files) and
        # can legitimately take a while per completion — same reasoning
        # code_implementation's coding agent already settled on for its own
        # timeout. Computed once and reused for both knobs below: init_model's
        # timeout= only bounds the raw HTTP call to the model provider — it
        # does NOT touch the DeepAgent harness's own per-completion watchdog
        # (DeepAgentConfig.completion_timeout, default 600.0s, enforced by
        # task_loop_event_handler independently of the model client). Without
        # passing completion_timeout= explicitly to create_deep_agent below,
        # that harness-level 600s default silently overrides this setting on
        # exactly the large completions (full Method section draft, a
        # paper-reviewer subagent reading the whole draft) it was raised for
        # — confirmed against a live run that failed every attempt with
        # {"error": "completion_timeout"} well under 900s despite this
        # setting already resolving to 900.
        completion_timeout = float(self._setting("timeout", "MODEL_TIMEOUT", default="600"))
        model = init_model(
            provider=self._setting("provider", "MODEL_PROVIDER", default="OpenAI"),
            model_name=self._setting("model", "MODEL_NAME", default="default"),
            api_key=self._setting("api_key", "API_KEY", required=True, secret=True),
            api_base=self._setting("base_url", "API_BASE", required=True),
            timeout=completion_timeout,
        )

        # ts-latex/scripts/compile.py reads LATEX_BIN_DIR to put a TeX
        # distribution's bin/ on PATH for the subprocess it launches (SDK
        # shell subprocesses inherit the full parent env — see
        # OperationUtils.prepare_environment). Set here, not hardcoded in
        # the script: a live run once found latexmk/pdflatex missing from
        # PATH and, since the agent can write to that script (project_root
        # is a required sandbox root for skill discovery), edited a
        # machine-specific absolute path directly into tracked source.
        latex_bin_dir = os.environ.get("LATEX_BIN_DIR") or self._pw_config.get("latex_bin_dir")
        if latex_bin_dir:
            os.environ.setdefault("LATEX_BIN_DIR", latex_bin_dir)

        # Same bridging pattern as LATEX_BIN_DIR above, for ts-figure's
        # attempt_drawio.py: DRAWIO_BIN (the export binary) and
        # DRAWIO_SKILL_DIR (an external checkout of the full Draw.io +
        # paper-icons skill, not vendored here — see attempt_drawio.py's
        # own docstring for why). Both null by default; when either is
        # unset, ts-figure always uses its matplotlib renderer instead.
        method_figure_cfg = dict(self._pw_config.get("method_figure") or {})
        drawio_bin = os.environ.get("DRAWIO_BIN") or method_figure_cfg.get("drawio_bin")
        if drawio_bin:
            os.environ.setdefault("DRAWIO_BIN", drawio_bin)
        drawio_skill_dir = os.environ.get("DRAWIO_SKILL_DIR") or method_figure_cfg.get("drawio_skill_dir")
        if drawio_skill_dir:
            os.environ.setdefault("DRAWIO_SKILL_DIR", drawio_skill_dir)

        workspace = paper_workspace_dir(run_id)
        # {PAPER_WORKSPACE}, like {SKILLS_DIR}, is a literal placeholder in
        # every SKILL.md — resolved once here, not per-skill — so every
        # script invocation and file write in the session can use the one
        # true absolute path instead of a bare relative name. This is what
        # makes those writes immune to the shell's tracked cwd drifting
        # from a stray `cd` mid-session (see the "never cd" rule in
        # system_prompt.md, and compile.py/lint_check.py/check_citations.py's
        # own comments on why they take this as an explicit argument
        # instead of trusting Path.cwd()).
        system_prompt = (
            _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
            .replace("{SKILLS_DIR}", str(_SKILLS_DIR))
            .replace("{PAPER_WORKSPACE}", str(workspace))
        )

        # One isolated reviewer role, invoked up to 3x (Theory/Empirical/
        # Applied lenses) by ts-review's Step 2 via the task tool this
        # registers on the parent agent. Each call is a genuinely separate
        # context — the reviewer only ever sees what's inlined in its own
        # task prompt, never the parent session's reasoning or a prior
        # reviewer's output — matching spark-to-paper-skills'
        # ts-paper-review "Tier 2: subagents" isolation (verified feasible
        # against this project's actual installed openjiuwen harness before
        # adopting it: create_deep_agent's subagents= param registers a
        # task_tool backed by openjiuwen.harness.tools.create_task_tool). No
        # tools granted — the draft text travels in the task prompt, not
        # via file access, so there's nothing for it to read or write.
        reviewer_config = SubAgentConfig(
            agent_card=AgentCard(
                name="paper-reviewer",
                description=(
                    "Isolated peer reviewer for one lens (Theory, Empirical, or Applied) of "
                    "the paper draft. Pass it the full draft text and the lens name + mandate "
                    "in the task prompt — it has no access to anything else, by design."
                ),
            ),
            system_prompt=_REVIEWER_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8"),
            model=model,
            enable_task_loop=False,
        )

        return create_deep_agent(
            model,
            card=AgentCard(
                name="reporting",
                description="Writes and compiles the final paper for a completed research run.",
            ),
            system_prompt=system_prompt,
            skills=self._enabled_skill_dirs(),
            subagents=[reviewer_config],
            # Deliberately NOT passing sys_operation= here. This installed
            # SDK's resolve_deep_agent_parts() (harness/factory.py) only sets
            # restrict_to_sandbox=restrict_to_work_dir when it auto-builds the
            # SysOperation itself — if you pass your own SysOperation
            # instance (as an earlier version of this method did, to get a
            # tight shell_allowlist), the factory uses it completely as-is
            # and restrict_to_work_dir is silently discarded: the agent's
            # fs/shell tools end up with NO sandbox confinement at all.
            # Confirmed the hard way — a live run wrote sections/*.tex,
            # title.txt, main.tex, and even ad-hoc Python scripts the model
            # invented, straight into this source module's own directory and
            # into the repo root. Letting create_deep_agent build its own
            # SysOperation (this branch actually sets restrict_to_sandbox)
            # is what makes restrict_to_work_dir below real. This accepts
            # the SDK's broader default shell_allowlist instead of a tight
            # one — same trade-off code_implementation's coding agent
            # already makes, relying on workspace confinement, not a narrow
            # command list, as the actual boundary.
            rails=with_observability([SysOperationRail(with_code_tool=False)]),
            enable_task_loop=True,
            completion_timeout=completion_timeout,
            max_iterations=int(self._pw_config.get("max_iterations", 40)),
            workspace=str(workspace),
            restrict_to_work_dir=True,
            auto_create_workspace=False,
            # _SKILLS_DIR lives under the installed package tree, not inside
            # the narrow per-run paper workspace above — without an explicit
            # project_root, restrict_to_work_dir's sandbox only allows the
            # workspace itself, and skill registration's own fs.read_file
            # calls (SkillManager.register, called at invoke() time) fail
            # silently/raise before the agent ever gets a turn. Same
            # project_root/cwd pairing topic_survey's _create_agent already
            # uses for the same reason.
            project_root=str(project_root()),
            cwd=str(workspace),
        )

    async def _run_paper_agent(self, *, run_id: str, query: str) -> str | None:
        """Returns an error string on a session-level failure (exception, or
        an error surfaced in the agent's own result dict), None on a normal
        completion. Either way, _verify_and_build_output still independently
        checks the actual files on disk — this is diagnostic, not trust."""
        from openjiuwen.core.runner import Runner
        from openjiuwen.core.session.agent import Session

        agent = self._build_paper_agent(run_id=run_id)
        # Not "reporting:{run_id}" — this id ends up embedded in the
        # .agent_history log filename, and ':' is invalid in a Windows
        # filename (WinError 87 on every rename, silently swallowed as a
        # logged warning). Same convention common/workspace.py's
        # module_attempt_dirname already documents for the same reason.
        request_id = f"reporting-{run_id}"
        session = Session(session_id=request_id, card=getattr(agent, "card", None))
        try:
            await session.pre_run(inputs={"query": query, "conversation_id": request_id})
            result = await Runner.run_agent(
                agent, {"query": query, "conversation_id": request_id}, session=session
            )
        except Exception as exc:  # noqa: BLE001 — surfaced as a note, not swallowed
            return f"reporting agent session raised {type(exc).__name__}: {exc}"
        finally:
            try:
                await session.post_run()
            except Exception:  # noqa: BLE001 -- best-effort cleanup, must not mask the result above
                _LOGGER.exception("reporting agent session.post_run() cleanup failed")
        if isinstance(result, dict) and result.get("error"):
            return f"reporting agent session reported an error: {result['error']}"
        return None

    # -- figure verification: same "host re-checks, never trusts the agent's
    # own report" rule as everything else in this method, applied to
    # ts-figure's outputs. Soft notes, not a hard fail — by the time this
    # runs, ts-latex has already compiled with whatever was in place, so
    # there is nothing to silently repair-and-recompile here; this mirrors
    # how a lingering lint violation or missing section is already reported
    # rather than fixed post-hoc. -----------------------------------------

    @staticmethod
    def _verify_figures(*, workspace: Path, method_text: str, result: Any) -> tuple[list[str], list[str]]:
        notes: list[str] = []
        extra_paths: list[str] = []

        spec_path = workspace / "method_figure.spec.json"
        if spec_path.is_file():
            from pydantic import ValidationError

            from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.schemas import MethodFigureSpec

            try:
                spec = MethodFigureSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
            except ValidationError as exc:
                notes.append(f"method_figure.spec.json failed schema validation: {exc}")
                spec = None
            if spec is not None:
                headings = lint.extract_subsection_headings(method_text)
                bad_labels = lint.check_method_figure_headings(
                    [node.label for node in spec.nodes], headings
                )
                if bad_labels:
                    notes.append(
                        "method figure node label(s) not among method.tex's real "
                        f"subsection headings: {', '.join(bad_labels)}"
                    )
                if not lint.check_method_figure_included(method_text):
                    notes.append(
                        "method_figure.spec.json was authored but method.tex has no "
                        "matching \\includegraphics reference"
                    )
                for ext in ("pdf", "svg", "png"):
                    candidate = workspace / "figures" / f"method_figure.{ext}"
                    if candidate.is_file():
                        extra_paths.append(str(candidate))
                        break
                else:
                    notes.append("method_figure.spec.json was authored but no rendered figure file was found")

        results_script = workspace / "figures" / "make_results_figure.py"
        if results_script.is_file():
            import subprocess
            import sys

            source = results_script.read_text(encoding="utf-8")
            known = lint.known_numbers(result)
            fabricated = sorted(set(lint.scan_script_for_fabrication(source, known)))
            if fabricated:
                notes.append(
                    f"figures/make_results_figure.py contains number(s) not traceable "
                    f"to a real metric: {', '.join(str(v) for v in fabricated)}"
                )
            try:
                proc = subprocess.run(
                    [sys.executable, str(results_script)],
                    cwd=str(workspace),
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                if proc.returncode != 0:
                    notes.append(
                        "figures/make_results_figure.py failed on independent host re-run: "
                        + (proc.stderr or proc.stdout)[-500:]
                    )
            except subprocess.TimeoutExpired:
                notes.append("figures/make_results_figure.py timed out on independent host re-run")

        return notes, extra_paths

    # -- final verification: never trust the agent's own report of "done" --
    # (same rule code_implementation applies to its smoke tests)

    def _verify_and_build_output(
        self,
        *,
        run_id: str,
        workspace: Path,
        sections_dir: Path,
        refs_bib_path: Path,
        figure_paths: list[str],
        known_keys: set[str],
        result: Any,
        session_error: str | None = None,
    ) -> ReportingOutput:
        drafts: dict[str, str] = {}
        for section_id in DOCUMENT_ORDER:
            section_path = sections_dir / f"{section_id}.tex"
            if section_path.is_file():
                drafts[section_id] = section_path.read_text(encoding="utf-8")

        notes: list[str] = []
        if session_error:
            notes.append(session_error)
        missing = [section_id for section_id in DOCUMENT_ORDER if section_id not in drafts]
        if missing:
            notes.append(f"section(s) never written: {', '.join(missing)}")

        for section_id, text in drafts.items():
            spec = _SECTIONS_BY_ID.get(section_id)
            if spec is None:
                continue
            violations = lint.lint_section(text, spec, result)
            if violations:
                notes.append(f"section {section_id!r} unresolved issues: {'; '.join(violations)}")

        hallucinated = lint.check_citations("\n".join(drafts.values()), known_keys)
        if hallucinated:
            notes.append(f"hallucinated citation key(s) not in refs.bib: {', '.join(hallucinated)}")

        allow_citation_ids = [spec.id for spec in SECTIONS if spec.allow_citations]
        zero_cite = lint.check_zero_citation_sections(drafts, allow_citation_ids, known_keys)
        if zero_cite:
            notes.append(f"section(s) with real sources available but zero citations: {', '.join(zero_cite)}")

        figure_notes, extra_figure_paths = self._verify_figures(
            workspace=workspace, method_text=drafts.get("method", ""), result=result
        )
        notes.extend(figure_notes)
        figure_paths = [*figure_paths, *extra_figure_paths]

        final_pdf = paper_output_path(run_id)
        if not final_pdf.is_file():
            notes.append("no compiled PDF found at end of session — ts-latex did not report success")

        if hallucinated or not final_pdf.is_file():
            return ReportingOutput(
                status="failed",
                paper_pdf_path=None,
                sections_dir=str(sections_dir),
                refs_bib_path=str(refs_bib_path),
                figure_paths=figure_paths,
                notes="; ".join(notes),
            )

        return ReportingOutput(
            status="compiled",
            paper_pdf_path=str(final_pdf),
            sections_dir=str(sections_dir),
            refs_bib_path=str(refs_bib_path),
            figure_paths=figure_paths,
            notes="; ".join(notes) if notes else None,
        )

    # -- model settings: same per-module pattern every module uses -----------

    def _setting(
        self,
        config_key: str,
        env_key: str,
        *,
        default: str | None = None,
        required: bool = False,
        secret: bool = False,
    ) -> str | None:
        oj_cfg = {} if secret else (self.config.get("openjiuwen", {}) or {})
        # A reporting.<config_key> value (e.g. a heavier reporting.timeout
        # for this module's larger completions — full-draft review reads,
        # a 2000-3000 word Method section) overrides the shared
        # openjiuwen.<config_key> default without changing it for every
        # other module. Falls through to the global value when reporting
        # doesn't set one, same as before this existed.
        reporting_override = None if secret else self._pw_config.get(config_key)
        value = os.environ.get(env_key) or reporting_override or oj_cfg.get(config_key) or default
        if required and not value:
            raise RuntimeError(
                f"reporting needs a model {config_key} — set "
                f"the {env_key} environment variable"
                + ("" if secret else f" or configs['openjiuwen']['{config_key}']") + "."
            )
        return value
