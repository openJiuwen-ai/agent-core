"""Build one tree node's ManagerRuntime.arun() inputs from its parent node.
See docs/paper_tree_orchestrator_design.md "Seeding".

Prior-paper carryover (2026-09-03): wraps the real
``auto_research.modules.paper_preprocess`` module (Option A — a prose
improvement prompt built from the parent's compiled paper), not the
Option B bib/prior_results.json merge originally planned. See
docs/paper_tree_orchestrator_design.md's dated note for why. Uploaded
papers are staged by the orchestrator and passed into each node as the
initial modification context.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import (
    paper_workspace_dir,
    to_project_relative,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_preprocess import (
    LatexValidationError,
    PaperPreprocessAgent,
    PaperPreprocessInput,
    PaperPreprocessOutput,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_preprocess.schemas import ResearchContext


@dataclass(frozen=True)
class NodeSeed:
    run_id: str
    topic: str
    objective: str
    constraints: list[str] = field(default_factory=list)
    research_paths: list[str] = field(default_factory=list)
    task_mode: str = "create_new_paper"
    initial_prompt: str = ""
    previous_context: ResearchContext | None = None


def build_node_run_id(task_id: str, round_index: int) -> str:
    """Filesystem-safe — no colons, matching
    `common/workspace.py::module_attempt_dirname`'s existing convention of
    never embedding characters Windows paths reject.
    """
    safe_task_id = task_id.replace(":", "-").replace("/", "-").replace("\\", "-")
    return f"{safe_task_id}-r{round_index}"


def preprocess_paper_dir(paper_dir: str) -> PaperPreprocessOutput | None:
    """Best-effort preprocess of a LaTeX paper folder. Returns `None` when
    the folder is not a self-contained paper — callers then keep their
    existing prompt/context instead of crashing the tree loop.
    """
    try:
        return PaperPreprocessAgent().run(PaperPreprocessInput(paper_dir=paper_dir))
    except LatexValidationError:
        return None


def build_prior_paper_output(parent_run_id: str | None) -> PaperPreprocessOutput | None:
    """Turn the parent node's compiled paper into prompt + ResearchContext."""
    if parent_run_id is None:
        return None
    paper_dir = paper_workspace_dir(parent_run_id)
    if not (paper_dir / "main.tex").is_file():
        return None
    return preprocess_paper_dir(str(paper_dir))


def build_prior_paper_prompt(parent_run_id: str | None) -> str | None:
    """Best-effort: turn the parent node's compiled paper into a prose
    improvement prompt via paper_preprocess. Returns `None` if there's no
    parent paper, or the parent's paper doesn't validate as a
    self-contained LaTeX paper — the next round then falls back to a plain
    from-scratch seed rather than crashing the tree loop.
    """
    output = build_prior_paper_output(parent_run_id)
    return None if output is None else output.initial_prompt


def build_node_seed(
    *,
    task_id: str,
    round_index: int,
    optimization_instruction: str | None,
    retry_reason: str | None,
    parent_run_id: str | None,
    initial_research_paths: list[str] | None = None,
    initial_prompt: str = "",
    previous_context: ResearchContext | None = None,
    task_mode: str = "create_new_paper",
) -> NodeSeed:
    run_id = build_node_run_id(task_id, round_index)
    prior = build_prior_paper_output(parent_run_id)
    prior_prompt = None if prior is None else prior.initial_prompt
    research_paths = list(initial_research_paths or [])

    constraints: list[str] = []
    if retry_reason:
        constraints.append(f"Previous attempt's issue to address: {retry_reason}")

    if prior is not None:
        # A compiled parent paper is a modify_paper baseline, even when the
        # task itself was instruction-only create_new_paper on round 1.
        task_mode = "modify_paper"
        previous_context = prior.research_context
        parent_paper = paper_workspace_dir(parent_run_id)
        try:
            parent_rel = to_project_relative(parent_paper)
        except ValueError:
            parent_rel = None
        if parent_rel and parent_rel not in research_paths:
            research_paths.insert(0, parent_rel)
        objective = prior_prompt or (optimization_instruction or "")
        if optimization_instruction:
            constraints.append(f"Additional instruction: {optimization_instruction}")
        topic = "Improve the existing paper from the current best node."
    else:
        objective = optimization_instruction or ""
        topic = optimization_instruction or "Improve the paper from the current best node."

    return NodeSeed(
        run_id=run_id,
        topic=topic,
        objective=objective,
        constraints=constraints,
        research_paths=research_paths,
        task_mode=task_mode,
        initial_prompt=prior_prompt or initial_prompt,
        previous_context=previous_context,
    )
