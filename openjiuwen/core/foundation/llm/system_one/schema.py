# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Typed request and response models for TypeSafe's System One API."""

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

_StructuredText: TypeAlias = str | dict[str, Any] | list[Any]
SystemOneState: TypeAlias = _StructuredText


class NoulCriteria(BaseModel):
    """Optional descriptions of what a yes and a no mean."""

    true: _StructuredText | None = Field(default=None, description="What counts as a yes")
    false: _StructuredText | None = Field(default=None, description="What counts as a no")


class NoulQuestion(BaseModel):
    """A yes/no question that returns the probability of yes."""

    type: Literal["noul"] = "noul"
    instructions: _StructuredText = Field(description="The yes/no question or statement to evaluate")
    criteria: NoulCriteria | None = Field(default=None, description="Optional yes/no guidance")


class ChoiceQuestion(BaseModel):
    """A question that selects one option from a caller-defined set."""

    type: Literal["choice"] = "choice"
    instructions: _StructuredText = Field(description="What the model should decide")
    criteria: dict[str, _StructuredText | None] = Field(
        min_length=1,
        max_length=255,
        description="Option name mapped to optional rubric guidance",
    )


class ScoreQuestion(BaseModel):
    """A question that rates state against an ordered rubric."""

    type: Literal["score"] = "score"
    instructions: _StructuredText = Field(description="What the model should rate")
    criteria: list[_StructuredText] = Field(
        min_length=2,
        max_length=10,
        description="Ordered score levels, from low to high",
    )


SystemOneQuestion = Annotated[
    NoulQuestion | ChoiceQuestion | ScoreQuestion,
    Field(discriminator="type"),
]


class NoulAnswer(BaseModel):
    """A calibrated probability that the answer is yes."""

    type: Literal["noul"] = "noul"
    noul: float = Field(description="Probability that the answer is yes")


class ChoiceAnswer(BaseModel):
    """The selected option and the full probability distribution."""

    type: Literal["choice"] = "choice"
    choice: str = Field(description="The option with the highest probability")
    confidence: float = Field(description="Confidence derived from the probability distribution")
    probabilities: dict[str, float] = Field(description="Probability of every option")


class ScoreAnswer(BaseModel):
    """A probability-weighted score and its distribution."""

    type: Literal["score"] = "score"
    score: float = Field(description="Probability-weighted mean of the score levels")
    confidence: float = Field(description="Confidence derived from the probability distribution")
    legend: dict[str, _StructuredText] = Field(description="Score levels mapped back to their criteria")
    probabilities: dict[str, float] = Field(description="Probability of every score level")


SystemOneAnswer = Annotated[
    NoulAnswer | ChoiceAnswer | ScoreAnswer,
    Field(discriminator="type"),
]


class SystemOneUsage(BaseModel):
    """Token usage for one System One evaluation."""

    input_tokens: int
    output_tokens: int
    cost: float | None = Field(default=None, description="Optional gateway-provided request cost")

    model_config = ConfigDict(extra="allow")


class _SystemOneRequest(BaseModel):
    """Wire request accepted by ``POST /v1/systemone``."""

    state: SystemOneState
    model: str
    questions: dict[str, SystemOneQuestion] = Field(min_length=1)

    model_config = ConfigDict(strict=True)


class SystemOneResponse(BaseModel):
    """Wire response returned by ``POST /v1/systemone``."""

    id: str | None = Field(default=None, description="Optional gateway-provided request ID")
    provider: str | None = Field(default=None, description="Optional gateway-provided serving provider")
    model: str
    answers: dict[str, SystemOneAnswer] = Field(min_length=1)
    usage: SystemOneUsage

    model_config = ConfigDict(extra="allow")
