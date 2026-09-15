"""Deterministic rubric aggregation and score composition."""

from __future__ import annotations

from statistics import mean, median

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.paper_scoring.schemas import (
    EXPERIMENT_DIMENSIONS,
    GENERAL_DIMENSIONS,
    CompositeWeights,
    DimensionAggregate,
    ExperimentRigorReviewOutput,
    ExperimentWeights,
    GeneralRubricReviewOutput,
    GeneralWeights,
    PaperExperimentRubric,
    PaperGeneralRubric,
    Scoresheet,
)


def _mad(values: list[float], center: float) -> float:
    if not values:
        return 0.0
    return float(median([abs(value - center) for value in values]))


def aggregate_dimension(
    reviews: list[object],
    name: str,
    *,
    range_flag: int = 3,
    mad_flag: float = 1.0,
) -> DimensionAggregate:
    scored = [getattr(review, name) for review in reviews]
    samples = [int(item.score) for item in scored]
    center = float(mean(samples))
    matching = min(scored, key=lambda item: abs(float(item.score) - center))
    score_range = max(samples) - min(samples)
    mad = _mad([float(value) for value in samples], center)
    return DimensionAggregate(
        mean=center,
        samples=samples,
        score_range=score_range,
        mad=mad,
        representative_justification=matching.justification,
        cited_sections=list(matching.cited_sections),
        flagged_dispersion=score_range >= range_flag or mad >= mad_flag,
    )


def general_score(rubric: PaperGeneralRubric, weights: GeneralWeights) -> float:
    return (
        weights.soundness * rubric.soundness.mean
        + weights.clarity * rubric.clarity.mean
        + weights.contribution * rubric.contribution.mean
        + weights.overall * rubric.overall.mean
    )


def experiment_score(rubric: PaperExperimentRubric, weights: ExperimentWeights) -> float:
    return (
        weights.question_alignment * rubric.question_alignment.mean
        + weights.design_and_controls * rubric.design_and_controls.mean
        + weights.measurement_and_statistics * rubric.measurement_and_statistics.mean
        + weights.reporting_and_reproducibility * rubric.reporting_and_reproducibility.mean
        + weights.claim_evidence_alignment * rubric.claim_evidence_alignment.mean
        + weights.overall_experimental_rigor * rubric.overall_experimental_rigor.mean
    )


def composite_score(
    *,
    general: float,
    experiment: float,
    weights: CompositeWeights,
) -> float:
    return weights.general * general + weights.experiment * experiment


def aggregate_general_rubric(
    reviews: list[GeneralRubricReviewOutput],
    weights: GeneralWeights,
    *,
    paper_id: str = "paper",
    range_flag: int = 3,
    mad_flag: float = 1.0,
) -> PaperGeneralRubric:
    if not reviews:
        raise ValueError("general rubric reviews must be non-empty")
    aggregated = PaperGeneralRubric(
        paper_id=paper_id,
        soundness=aggregate_dimension(reviews, "soundness", range_flag=range_flag, mad_flag=mad_flag),
        clarity=aggregate_dimension(reviews, "clarity", range_flag=range_flag, mad_flag=mad_flag),
        contribution=aggregate_dimension(
            reviews, "contribution", range_flag=range_flag, mad_flag=mad_flag
        ),
        overall=aggregate_dimension(reviews, "overall", range_flag=range_flag, mad_flag=mad_flag),
        score=0.0,
    )
    return aggregated.model_copy(update={"score": general_score(aggregated, weights)})


def aggregate_experiment_rubric(
    reviews: list[ExperimentRigorReviewOutput],
    weights: ExperimentWeights,
    *,
    paper_id: str = "paper",
    range_flag: int = 3,
    mad_flag: float = 1.0,
) -> PaperExperimentRubric:
    if not reviews:
        raise ValueError("experiment rubric reviews must be non-empty")
    kwargs = {
        name: aggregate_dimension(reviews, name, range_flag=range_flag, mad_flag=mad_flag)
        for name in EXPERIMENT_DIMENSIONS
    }
    aggregated = PaperExperimentRubric(paper_id=paper_id, score=0.0, **kwargs)
    return aggregated.model_copy(update={"score": experiment_score(aggregated, weights)})


def build_scoresheet(
    *,
    general: PaperGeneralRubric,
    experiment: PaperExperimentRubric,
    composite_weights: CompositeWeights,
    tex_path: str = "",
) -> Scoresheet:
    composite = composite_score(
        general=general.score,
        experiment=experiment.score,
        weights=composite_weights,
    )
    return Scoresheet(
        tex_path=tex_path,
        soundness=general.soundness.mean,
        clarity=general.clarity.mean,
        contribution=general.contribution.mean,
        overall=general.overall.mean,
        question_alignment=experiment.question_alignment.mean,
        design_and_controls=experiment.design_and_controls.mean,
        measurement_and_statistics=experiment.measurement_and_statistics.mean,
        reporting_and_reproducibility=experiment.reporting_and_reproducibility.mean,
        claim_evidence_alignment=experiment.claim_evidence_alignment.mean,
        overall_experimental_rigor=experiment.overall_experimental_rigor.mean,
        general_score=general.score,
        experiment_score=experiment.score,
        composite_score=composite,
    )


def compact_scoresheet(scoresheet: Scoresheet) -> dict[str, object]:
    return {
        "tex_path": scoresheet.tex_path,
        "soundness": round(scoresheet.soundness, 4),
        "clarity": round(scoresheet.clarity, 4),
        "contribution": round(scoresheet.contribution, 4),
        "overall": round(scoresheet.overall, 4),
        "question_alignment": round(scoresheet.question_alignment, 4),
        "design_and_controls": round(scoresheet.design_and_controls, 4),
        "measurement_and_statistics": round(scoresheet.measurement_and_statistics, 4),
        "reporting_and_reproducibility": round(scoresheet.reporting_and_reproducibility, 4),
        "claim_evidence_alignment": round(scoresheet.claim_evidence_alignment, 4),
        "overall_experimental_rigor": round(scoresheet.overall_experimental_rigor, 4),
        "general_score": round(scoresheet.general_score, 4),
        "experiment_score": round(scoresheet.experiment_score, 4),
        "composite_score": round(scoresheet.composite_score, 4),
    }


def dispersion_flags(rubric, *, family: str, names: tuple[str, ...]) -> list[str]:
    flags: list[str] = []
    for name in names:
        dimension = getattr(rubric, name)
        if dimension.flagged_dispersion:
            flags.append(f"{family}_dispersion:{name}")
    return flags


def general_dispersion_flags(rubric: PaperGeneralRubric) -> list[str]:
    return dispersion_flags(rubric, family="general", names=GENERAL_DIMENSIONS)


def experiment_dispersion_flags(rubric: PaperExperimentRubric) -> list[str]:
    return dispersion_flags(rubric, family="experiment", names=EXPERIMENT_DIMENSIONS)
