"""Pydantic contracts for the standalone paper-scoring module."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = 3

CanonicalSection = Literal[
    "abstract",
    "introduction",
    "related_work",
    "method",
    "experiments",
    "results",
    "discussion",
    "limitations",
    "appendix",
    "other",
]
ScoringRunStatus = Literal["scored", "failed"]
GeneralDimension = Literal["soundness", "clarity", "contribution", "overall"]
ExperimentDimension = Literal[
    "question_alignment",
    "design_and_controls",
    "measurement_and_statistics",
    "reporting_and_reproducibility",
    "claim_evidence_alignment",
    "overall_experimental_rigor",
]

GENERAL_DIMENSIONS: tuple[GeneralDimension, ...] = (
    "soundness",
    "clarity",
    "contribution",
    "overall",
)
EXPERIMENT_DIMENSIONS: tuple[ExperimentDimension, ...] = (
    "question_alignment",
    "design_and_controls",
    "measurement_and_statistics",
    "reporting_and_reproducibility",
    "claim_evidence_alignment",
    "overall_experimental_rigor",
)
EXPERIMENT_FIGURE_SECTIONS: frozenset[str] = frozenset(
    {"experiments", "results", "discussion", "limitations", "appendix"}
)


def _strip_nonempty(value: str, *, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    return cleaned


class PaperSection(BaseModel):
    section_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    canonical_name: CanonicalSection
    text: str
    source_path: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)

    @field_validator("section_id", "name", "source_path")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        return _strip_nonempty(value, field_name="section field")


class TableBlock(BaseModel):
    table_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    caption: str = ""
    markdown: str = Field(min_length=1)
    source_path: str = ""
    line_start: int = Field(default=1, ge=1)


class BibliographyEntry(BaseModel):
    key: str = Field(min_length=1)
    title: str = ""
    authors: str = ""
    year: str = ""


class FigureAsset(BaseModel):
    figure_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    canonical_section: CanonicalSection
    caption: str = ""
    label: str | None = None
    source_path: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    sha256: str = Field(min_length=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    png_bytes: bytes = Field(default=b"", exclude=True)


class PaperDocument(BaseModel):
    paper_id: str = "paper"
    tex_path: str
    paper_root: str
    sha256: str
    full_text: str
    sections: list[PaperSection] = Field(min_length=1)
    tables: list[TableBlock] = Field(default_factory=list)
    figures: list[FigureAsset] = Field(default_factory=list)
    bibliography: list[BibliographyEntry] = Field(default_factory=list)
    included_files: list[str] = Field(default_factory=list)
    token_estimate: int = Field(ge=0)


class RubricDimensionScore(BaseModel):
    score: int = Field(ge=1, le=10)
    justification: str = Field(min_length=1)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    cited_sections: list[str] = Field(default_factory=list)


class GeneralRubricReviewOutput(BaseModel):
    soundness: RubricDimensionScore
    clarity: RubricDimensionScore
    contribution: RubricDimensionScore
    overall: RubricDimensionScore


class ExperimentRigorReviewOutput(BaseModel):
    question_alignment: RubricDimensionScore
    design_and_controls: RubricDimensionScore
    measurement_and_statistics: RubricDimensionScore
    reporting_and_reproducibility: RubricDimensionScore
    claim_evidence_alignment: RubricDimensionScore
    overall_experimental_rigor: RubricDimensionScore


class DimensionAggregate(BaseModel):
    mean: float
    samples: list[int]
    score_range: int = Field(ge=0)
    mad: float = Field(ge=0.0)
    representative_justification: str
    cited_sections: list[str] = Field(default_factory=list)
    flagged_dispersion: bool = False


class PaperGeneralRubric(BaseModel):
    paper_id: str = "paper"
    soundness: DimensionAggregate
    clarity: DimensionAggregate
    contribution: DimensionAggregate
    overall: DimensionAggregate
    score: float


class PaperExperimentRubric(BaseModel):
    paper_id: str = "paper"
    question_alignment: DimensionAggregate
    design_and_controls: DimensionAggregate
    measurement_and_statistics: DimensionAggregate
    reporting_and_reproducibility: DimensionAggregate
    claim_evidence_alignment: DimensionAggregate
    overall_experimental_rigor: DimensionAggregate
    score: float


class Scoresheet(BaseModel):
    tex_path: str = ""
    soundness: float
    clarity: float
    contribution: float
    overall: float
    question_alignment: float
    design_and_controls: float
    measurement_and_statistics: float
    reporting_and_reproducibility: float
    claim_evidence_alignment: float
    overall_experimental_rigor: float
    general_score: float
    experiment_score: float
    composite_score: float


class LLMCallMeta(BaseModel):
    schema_name: str
    temperature: float
    prompt_chars: int
    response_chars: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    image_count: int = 0


class PaperScoringInput(BaseModel):
    tex_path: str = Field(min_length=1)
    output_dir: str = Field(min_length=1)

    @field_validator("tex_path", "output_dir")
    @classmethod
    def _strip_paths(cls, value: str) -> str:
        return _strip_nonempty(value, field_name="path")


class PaperScoringOutput(BaseModel):
    status: ScoringRunStatus
    scoresheet_path: str | None = None
    notes: str | None = None
    scoresheet: Scoresheet | None = None


class GeneralWeights(BaseModel):
    soundness: float = 0.30
    clarity: float = 0.20
    contribution: float = 0.25
    overall: float = 0.25

    @model_validator(mode="after")
    def _sum_to_one(self) -> GeneralWeights:
        total = self.soundness + self.clarity + self.contribution + self.overall
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"general weights must sum to 1.0, got {total}")
        for name in GENERAL_DIMENSIONS:
            if getattr(self, name) < 0:
                raise ValueError("scoring weights must be non-negative")
        return self


class ExperimentWeights(BaseModel):
    question_alignment: float = 0.20
    design_and_controls: float = 0.20
    measurement_and_statistics: float = 0.20
    reporting_and_reproducibility: float = 0.15
    claim_evidence_alignment: float = 0.15
    overall_experimental_rigor: float = 0.10

    @model_validator(mode="after")
    def _sum_to_one(self) -> ExperimentWeights:
        total = (
            self.question_alignment
            + self.design_and_controls
            + self.measurement_and_statistics
            + self.reporting_and_reproducibility
            + self.claim_evidence_alignment
            + self.overall_experimental_rigor
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"experiment weights must sum to 1.0, got {total}")
        for name in EXPERIMENT_DIMENSIONS:
            if getattr(self, name) < 0:
                raise ValueError("scoring weights must be non-negative")
        return self


class CompositeWeights(BaseModel):
    general: float = 0.50
    experiment: float = 0.50

    @model_validator(mode="after")
    def _sum_to_one(self) -> CompositeWeights:
        total = self.general + self.experiment
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"composite weights must sum to 1.0, got {total}")
        if self.general < 0 or self.experiment < 0:
            raise ValueError("scoring weights must be non-negative")
        return self


class PaperScoringSettings(BaseModel):
    rubric_samples: int = Field(default=5)
    max_validation_retries: int = Field(default=1, ge=0, le=3)
    timeout: int = Field(default=600, ge=1)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    context_window_tokens: int = Field(default=128_000, ge=1000)
    output_reserve_tokens: int = Field(default=4000, ge=0)
    prompt_overhead_tokens: int = Field(default=2500, ge=0)
    min_text_chars: int = Field(default=200, ge=1)
    max_figures: int = Field(default=12, ge=0, le=32)
    figure_max_side: int = Field(default=1280, ge=64, le=4096)
    dispersion_range: int = Field(default=3, ge=1)
    dispersion_mad: float = Field(default=1.0, ge=0.0)
    general_weights: GeneralWeights = Field(default_factory=GeneralWeights)
    experiment_weights: ExperimentWeights = Field(default_factory=ExperimentWeights)
    composite_weights: CompositeWeights = Field(default_factory=CompositeWeights)

    @field_validator("rubric_samples")
    @classmethod
    def _allowed_rubric_samples(cls, value: int) -> int:
        if value not in {3, 5}:
            raise ValueError("rubric_samples must be 3 or 5")
        return value

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> PaperScoringSettings:
        raw = dict((config or {}).get("paper_scoring") or {})
        general = raw.pop("general_weights", None)
        experiment = raw.pop("experiment_weights", None)
        composite = raw.pop("composite_weights", None)
        if general:
            raw["general_weights"] = GeneralWeights.model_validate(general)
        if experiment:
            raw["experiment_weights"] = ExperimentWeights.model_validate(experiment)
        if composite:
            raw["composite_weights"] = CompositeWeights.model_validate(composite)
        return cls.model_validate(raw)
