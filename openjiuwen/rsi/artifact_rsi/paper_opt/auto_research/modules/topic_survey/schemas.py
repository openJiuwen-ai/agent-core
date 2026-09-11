"""Pydantic contracts for literature and webpage topic surveys."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _safe_relative_path(path: str) -> str:
    value = path.strip().replace("\\", "/")
    if not value:
        raise ValueError("path must be non-empty")
    if value.startswith("/") or (len(value) >= 2 and value[1] == ":"):
        raise ValueError(f"absolute paths are not allowed: {path!r}")
    if ".." in value.split("/"):
        raise ValueError(f"path traversal is not allowed: {path!r}")
    return value


class TopicSurveyInput(BaseModel):
    """A one-shot survey request. The topic itself is the user prompt."""

    topic: str = Field(min_length=1)
    max_papers: int = Field(default=20, ge=1, le=50)
    max_web_pages: int = Field(default=10, ge=0, le=50)
    initial_context: str = ""

    @field_validator("topic")
    @classmethod
    def _strip_topic(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("topic must be non-empty")
        return value

    @field_validator("initial_context")
    @classmethod
    def _strip_initial_context(cls, value: str) -> str:
        return value.strip()


class CitationMetadata(BaseModel):
    """Best-effort bibliographic metadata for one survey source.

    The host computes citation eligibility and the final citation key.  These
    fields are evidence supplied by the source or the survey agent, not an
    authority that can mark an incomplete source as citable.
    """

    authors: list[str] = Field(default_factory=list)
    year: str | int | None = None
    venue: str | None = None
    doi: str | None = None

    @field_validator("authors")
    @classmethod
    def _clean_authors(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("year", "venue", "doi")
    @classmethod
    def _strip_optional_text(cls, value: str | int | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


class SurveySource(BaseModel):
    """One saved source and the evidence distilled from it."""

    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    source_type: Literal["paper", "web_page"]
    local_path: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    abstract: str = ""
    # Not min_length=1: a source can be legitimate supporting/background
    # context (a methodology reference, a tool's docs) with no standalone
    # "finding" of its own -- requiring one just forces the model to
    # fabricate something to pass validation.
    key_findings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    retrieval_mode: Literal["downloaded_pdf", "downloaded_html", "metadata_only"] = (
        "downloaded_html"
    )
    citation: CitationMetadata = Field(default_factory=CitationMetadata)
    citation_key: str | None = None
    citation_eligible: bool = False
    citation_exclusion_reasons: list[str] = Field(default_factory=list)

    @field_validator("title", "url", "summary")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source text fields must be non-empty")
        return value

    @field_validator("local_path")
    @classmethod
    def _validate_local_path(cls, value: str) -> str:
        return _safe_relative_path(value)

    @field_validator("citation_key")
    @classmethod
    def _strip_citation_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class TopicSurveyDraft(BaseModel):
    """Structured survey returned after every listed source is saved locally."""

    short_summary: str = Field(min_length=1)
    # Not min_length=1: some surveys are pure background/context gathering
    # with no standalone finding or open problem to report -- forcing one
    # just invites a fabricated placeholder. `sources` stays min_length=1;
    # a survey with zero sources isn't a survey.
    key_findings: list[str] = Field(default_factory=list)
    open_problems: list[str] = Field(default_factory=list)
    sources: list[SurveySource] = Field(min_length=1)

    @field_validator("short_summary")
    @classmethod
    def _strip_summary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("short_summary must be non-empty")
        return value

    @model_validator(mode="after")
    def _unique_source_paths(self) -> "TopicSurveyDraft":
        paths = [source.local_path for source in self.sources]
        if len(paths) != len(set(paths)):
            raise ValueError("each source must have a unique local_path")
        return self


class TopicSurveyOutput(BaseModel):
    topic: str
    short_summary: str = ""
    key_findings: list[str]
    open_problems: list[str]
    references: list[str]
    research_summary_path: str = ""
    sources: list[SurveySource] = Field(default_factory=list)
