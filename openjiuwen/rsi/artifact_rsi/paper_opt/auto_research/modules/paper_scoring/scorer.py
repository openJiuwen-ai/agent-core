"""Host-orchestrated single-paper scorer (no agent loop)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring import prompts as scoring_prompts
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.aggregation import (
    aggregate_experiment_rubric,
    aggregate_general_rubric,
    build_scoresheet,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.artifacts import (
    ensure_output_dir,
    write_named,
    write_scoresheet,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.ingestion import (
    ingest_latex,
    paper_manifest,
    render_paper_for_prompt,
    select_figures,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.llm import StructuredCompleter
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.schemas import (
    SCHEMA_VERSION,
    ExperimentRigorReviewOutput,
    FigureAsset,
    GeneralRubricReviewOutput,
    PaperDocument,
    PaperScoringInput,
    PaperScoringOutput,
    PaperScoringSettings,
)


def resolve_user_path(path: str, *, root: Path | None = None) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    base = root or Path.cwd()
    return (base / candidate).resolve()


class PaperScorer:
    """LaTeX-in / dual-rubric scoresheet-out. Control flow stays in Python."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        complete_fn: Callable[..., Any] | None = None,
        model: Any | None = None,
    ):
        self.config = config
        self.settings = PaperScoringSettings.from_config(config)
        self.llm = StructuredCompleter(
            config,
            settings=self.settings,
            complete_fn=complete_fn,
            model=model,
        )

    def run(
        self,
        *,
        tex_path: str,
        output_dir: str | Path,
    ) -> PaperScoringOutput:
        return asyncio.run(
            self.arun(
                tex_path=tex_path,
                output_dir=output_dir,
            )
        )

    async def arun(
        self,
        *,
        tex_path: str,
        output_dir: str | Path,
    ) -> PaperScoringOutput:
        directory = ensure_output_dir(output_dir)
        return await self.ascore(
            PaperScoringInput(
                tex_path=tex_path,
                output_dir=str(directory),
            )
        )

    async def ascore(self, inputs: PaperScoringInput) -> PaperScoringOutput:
        output_dir = ensure_output_dir(inputs.output_dir)
        paper = ingest_latex(
            resolve_user_path(inputs.tex_path),
            paper_id="paper",
            settings=self.settings,
        )
        write_named(
            output_dir,
            "ingestion.json",
            {
                "schema_version": SCHEMA_VERSION,
                "paper": paper_manifest(paper),
            },
        )

        general_reviews = await self._general_samples(paper)
        experiment_reviews = await self._experiment_samples(paper)

        range_flag = self.settings.dispersion_range
        mad_flag = self.settings.dispersion_mad
        general = aggregate_general_rubric(
            general_reviews,
            self.settings.general_weights,
            range_flag=range_flag,
            mad_flag=mad_flag,
        )
        experiment = aggregate_experiment_rubric(
            experiment_reviews,
            self.settings.experiment_weights,
            range_flag=range_flag,
            mad_flag=mad_flag,
        )
        write_named(
            output_dir,
            "rubric.general.json",
            {
                "aggregate": general.model_dump(mode="json"),
                "samples": [item.model_dump(mode="json") for item in general_reviews],
            },
        )
        write_named(
            output_dir,
            "rubric.experiments.json",
            {
                "aggregate": experiment.model_dump(mode="json"),
                "samples": [item.model_dump(mode="json") for item in experiment_reviews],
            },
        )

        scoresheet = build_scoresheet(
            general=general,
            experiment=experiment,
            composite_weights=self.settings.composite_weights,
            tex_path=paper.tex_path,
        )
        scoresheet_path = write_scoresheet(output_dir, scoresheet)
        write_named(
            output_dir,
            "audit.json",
            {
                "schema_version": SCHEMA_VERSION,
                "notes": "scored",
                "settings": self.settings.model_dump(mode="json"),
                "llm_calls": [call.model_dump(mode="json") for call in self.llm.calls],
                "scoresheet": scoresheet.model_dump(mode="json"),
            },
        )
        return PaperScoringOutput(
            status="scored",
            scoresheet_path=str(scoresheet_path),
            notes="scored",
            scoresheet=scoresheet,
        )

    async def _general_samples(self, paper: PaperDocument) -> list[GeneralRubricReviewOutput]:
        return await self._rubric_samples(
            paper,
            schema=GeneralRubricReviewOutput,
            system=scoring_prompts.GENERAL_RUBRIC_PROMPT,
            images=select_figures(paper, experiment_only=False),
        )

    async def _experiment_samples(
        self, paper: PaperDocument
    ) -> list[ExperimentRigorReviewOutput]:
        return await self._rubric_samples(
            paper,
            schema=ExperimentRigorReviewOutput,
            system=scoring_prompts.EXPERIMENT_RIGOR_PROMPT,
            images=select_figures(paper, experiment_only=True),
        )

    async def _rubric_samples(
        self,
        paper: PaperDocument,
        *,
        schema: type,
        system: str,
        images: Sequence[FigureAsset],
    ) -> list[Any]:
        reviews: list[Any] = []
        user = render_paper_for_prompt(paper, label="Paper under review")
        for _ in range(self.settings.rubric_samples):
            reviews.append(
                await self.llm.complete(
                    schema,
                    system=system,
                    user=user,
                    images=images,
                )
            )
        return reviews
