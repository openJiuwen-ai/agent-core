"""Paper scorer adapter — see docs/paper_tree_orchestrator_design.md "Judge
module".

Thin wrapper around the real `auto_research.modules.paper_scoring.PaperScorer`
(landed 2026-09-02): scores one paper in isolation via a dual NeurIPS-style
rubric (general + experiment-rigor, each sampled and aggregated with
dispersion flagging) and returns one composite score plus a breakdown.
Deciding adopt/reject from two independently-scored `PaperScore`s (candidate
vs. the current frontier) is still the orchestrator's job — see
`orchestrator.py`'s `_build_node`/`_node_score`.

`model`, when given, is the AgentServer-resolved
`openjiuwen.core.foundation.llm.Model` instance
(`ArtifactEngineRequest.model`) — passed straight through to `PaperScorer`,
which uses it directly (see `paper_scoring/llm.py::StructuredCompleter`).
When `None` (the default, e.g. in tests or manual runs), `PaperScorer`
self-resolves its own model from `config`'s `openjiuwen`/`paper_scoring`
blocks instead (`build_model_from_config`). This does NOT extend to the
six-module `ManagerRuntime` pipeline `orchestrator.py::_run_manager`
calls — only 3 of its 6 module agents have a `model=` injection seam today
(manager/experiment_design/topic_survey), and `ManagerRuntime` itself has
no direct pass-through parameter; that remains a separate, larger, not-yet
addressed piece of the same architecture question.

Errors (missing API key, LLM/schema validation failure, LaTeX ingestion
failure, ...) are NOT caught here — they propagate to the caller, same
"the callee doesn't decide what a failure means, the orchestrator does"
split `_run_manager` already uses for the pipeline run itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.scorer import PaperScorer


@dataclass(frozen=True)
class PaperScore:
    overall: float
    breakdown: dict[str, float] = field(default_factory=dict)
    reason: str = ""


async def score_paper(
    *, tex_path: str, output_dir: str, config: dict[str, Any], model: Any = None
) -> PaperScore:
    """Score one compiled paper. Raises on any scoring failure — the caller
    decides what that means for the node (see orchestrator.py's
    `scoring_error` handling)."""
    scorer = PaperScorer(config, model=model)
    output = await scorer.arun(tex_path=tex_path, output_dir=output_dir)
    sheet = output.scoresheet
    if sheet is None:
        raise RuntimeError(
            f"paper_scoring returned status={output.status!r} with no scoresheet: "
            f"{output.notes}"
        )

    # NOTE: Scoresheet.overall is the *general rubric's own* "overall"
    # sub-dimension, not the final composite -- our PaperScore.overall must
    # be composite_score. Renamed to general_overall in the breakdown to
    # avoid the two colliding under the same key.
    breakdown = {
        "soundness": sheet.soundness,
        "clarity": sheet.clarity,
        "contribution": sheet.contribution,
        "general_overall": sheet.overall,
        "question_alignment": sheet.question_alignment,
        "design_and_controls": sheet.design_and_controls,
        "measurement_and_statistics": sheet.measurement_and_statistics,
        "reporting_and_reproducibility": sheet.reporting_and_reproducibility,
        "claim_evidence_alignment": sheet.claim_evidence_alignment,
        "overall_experimental_rigor": sheet.overall_experimental_rigor,
        "general_score": sheet.general_score,
        "experiment_score": sheet.experiment_score,
    }
    reason = (
        f"composite {sheet.composite_score:.2f} "
        f"(general {sheet.general_score:.2f}: soundness {sheet.soundness:.1f}/"
        f"clarity {sheet.clarity:.1f}/contribution {sheet.contribution:.1f}; "
        f"experiment {sheet.experiment_score:.2f}: rigor {sheet.overall_experimental_rigor:.1f})"
    )
    return PaperScore(overall=sheet.composite_score, breakdown=breakdown, reason=reason)
