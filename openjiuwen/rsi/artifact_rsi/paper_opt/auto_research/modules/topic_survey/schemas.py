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


class SurveySource(BaseModel):
    """One downloaded source and the evidence distilled from it."""

    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    source_type: Literal["paper", "web_page"]
    local_path: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    key_findings: list[str] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)

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


class TopicSurveyDraft(BaseModel):
    """Structured survey returned by the model after it saves every source."""

    short_summary: str = Field(min_length=1)
    key_findings: list[str] = Field(min_length=1)
    open_problems: list[str] = Field(min_length=1)
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
