"""Contracts shared by LaTeX validation and paper evidence extraction."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class LatexValidationError(ValueError):
    """A paper folder is not a self-contained LaTeX paper."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("LaTeX validation failed:\n" + "\n".join(f"- {item}" for item in errors))


class PaperPreprocessError(ValueError):
    """Paper evidence could not be converted into a usable improvement prompt."""


class PaperSection(BaseModel):
    title: str
    content: str


class LatexPaperDocument(BaseModel):
    paper_dir: str
    main_tex_path: str
    expanded_tex: str
    title: str
    abstract: str
    sections: list[PaperSection] = Field(default_factory=list)
    bibliography_paths: list[str] = Field(default_factory=list)
    figure_paths: list[str] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list)


class PaperPreprocessInput(BaseModel):
    paper_dir: str = "experiments/mgr-agent-1/paper"

    @field_validator("paper_dir")
    @classmethod
    def _validate_paper_dir(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("paper_dir must be non-empty")
        return cleaned


class ResultClaim(BaseModel):
    claim: str
    evidence: str
    source_section: str


class PaperEvidence(BaseModel):
    research_question: str
    method_summary: str
    experiment_setup: list[str] = Field(default_factory=list)
    key_results: list[ResultClaim] = Field(default_factory=list)
    conclusions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    improvement_opportunities: list[str] = Field(default_factory=list)


class PaperPreprocessOutput(BaseModel):
    document: LatexPaperDocument
    evidence: PaperEvidence
    initial_prompt: str
