"""Build one tree node's ManagerRuntime.arun() inputs from its parent node.
See docs/paper_tree_orchestrator_design.md "Seeding".

Prior-paper carryover (2026-09-03): wraps the real
``auto_research.modules.paper_preprocess`` module (Option A — a prose
improvement prompt built from the parent's compiled paper), not the
Option B bib/prior_results.json merge originally planned. See
docs/paper_tree_orchestrator_design.md's dated note for why. The root's
uploaded-paper ingestion is still not wired in — out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.workspace import paper_workspace_dir
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_preprocess import (
    LatexValidationError,
    PaperPreprocessAgent,
    PaperPreprocessInput,
)


@dataclass(frozen=True)
class NodeSeed:
    run_id: str
    topic: str
    objective: str
    constraints: list[str] = field(default_factory=list)
    research_paths: list[str] = field(default_factory=list)


def build_node_run_id(task_id: str, round_index: int) -> str:
    """Filesystem-safe — no colons, matching
    `common/workspace.py::module_attempt_dirname`'s existing convention of
    never embedding characters Windows paths reject."""
    safe_task_id = task_id.replace(":", "-").replace("/", "-").replace("\\", "-")
    return f"{safe_task_id}-r{round_index}"


def build_prior_paper_prompt(parent_run_id: str | None) -> str | None:
    """Best-effort: turn the parent node's compiled paper into a prose
    improvement prompt via paper_preprocess. Returns `None` if there's no
    parent paper, or the parent's paper doesn't validate as a
    self-contained LaTeX paper — the next round then falls back to a plain
    from-scratch seed rather than crashing the tree loop."""
    if parent_run_id is None:
        return None
    paper_dir = paper_workspace_dir(parent_run_id)
    if not (paper_dir / "main.tex").is_file():
        return None
    try:
        return PaperPreprocessAgent().run(
            PaperPreprocessInput(paper_dir=str(paper_dir))
        ).initial_prompt
    except LatexValidationError:
        return None


def build_node_seed(
    *,
    task_id: str,
    round_index: int,
    optimization_instruction: str | None,
    retry_reason: str | None,
    parent_run_id: str | None,
) -> NodeSeed:
    run_id = build_node_run_id(task_id, round_index)
    prior_prompt = build_prior_paper_prompt(parent_run_id)

    constraints: list[str] = []
    if retry_reason:
        constraints.append(f"Previous attempt's issue to address: {retry_reason}")

    if prior_prompt:
        objective = prior_prompt
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
        research_paths=[],
    )
