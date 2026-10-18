# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Validated PA-11 handoff values; storage identity always comes from the host."""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

SEED_OBJECTIVE_MAX_CHARS = 2048
SEED_LIST_MAX_ITEMS = 16
SEED_ITEM_MAX_CHARS = 512
SEED_REFERENCE_MAX_ITEMS = 32
SEED_REFERENCE_MAX_CHARS = 256
SEED_MAX_JSON_BYTES = 16 * 1024


class WindowSeed(BaseModel):
    """Short task handoff supplied by the Agent, not proof that an action succeeded."""

    model_config = ConfigDict(extra="forbid", strict=True)

    objective: StrictStr = Field(min_length=1, max_length=SEED_OBJECTIVE_MAX_CHARS)
    completed: list[StrictStr] = Field(default_factory=list, max_length=SEED_LIST_MAX_ITEMS)
    next_steps: list[StrictStr] = Field(default_factory=list, max_length=SEED_LIST_MAX_ITEMS)
    constraints_and_decisions: list[StrictStr] = Field(default_factory=list, max_length=SEED_LIST_MAX_ITEMS)
    open_questions: list[StrictStr] = Field(default_factory=list, max_length=SEED_LIST_MAX_ITEMS)
    pending_effects: list[StrictStr] = Field(default_factory=list, max_length=SEED_REFERENCE_MAX_ITEMS)
    evidence_refs: list[StrictStr] = Field(default_factory=list, max_length=SEED_REFERENCE_MAX_ITEMS)

    @model_validator(mode="after")
    def validate_bounded_text(self):
        """Reject blank entries, oversized list items and an oversized combined handoff."""
        if not self.objective.strip():
            raise ValueError("objective must not be blank")
        text_fields = ("completed", "next_steps", "constraints_and_decisions", "open_questions")
        for name in text_fields:
            values = getattr(self, name)
            if any(not value.strip() or len(value) > SEED_ITEM_MAX_CHARS for value in values):
                raise ValueError(f"{name} contains blank or oversized text")
        reference_fields = ("pending_effects", "evidence_refs")
        for name in reference_fields:
            values = getattr(self, name)
            if any(not value.strip() or len(value) > SEED_REFERENCE_MAX_CHARS for value in values):
                raise ValueError(f"{name} contains blank or oversized references")
        if len(self.model_dump_json().encode("utf-8")) > SEED_MAX_JSON_BYTES:
            raise ValueError("window seed exceeds the combined size limit")
        return self


class WindowSwitchRequest(BaseModel):
    """One requested transition; the Agent cannot provide Session or archive identity."""

    model_config = ConfigDict(extra="forbid", strict=True)

    trigger_kind: Literal["agent", "capacity_fallback"]
    seed: WindowSeed


class WindowSwitchResult(BaseModel):
    """Committed transition returned only to runtime code, never accepted from the model."""

    model_config = ConfigDict(extra="forbid", strict=True)

    archive_ref: StrictStr
    current_window_id: StrictStr
    window_seq: int = Field(ge=1)
    token_usage: dict[str, int | float | str | bool | None]


@dataclass(frozen=True)
class WindowRollover:
    """Internal persistence input; Session and window identity are derived by WindowStore."""

    trigger_kind: Literal["agent", "capacity_fallback"]
    seed: WindowSeed
    old_messages: tuple[Any, ...]
    new_messages: tuple[Any, ...]
    retained_item_refs: tuple[str, ...]
    token_usage: dict[str, int | float | str | bool | None]
    iteration_seq: int
    last_group_message_id: str | None
