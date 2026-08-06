# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Resolve a roster member to a stable ``role_type`` for memory-bank keying.

Predefined and dynamic mode are never mixed — an instance is always
constructed for one mode, and a dynamic-mode taxonomy is never consulted
for a predefined-mode run or vice versa.

- ``predefined`` mode: a fixed roster with already-stable member names
  (e.g. ``team_leader`` / ``researcher`` / ``skeptic`` / ``writer``) —
  identity mapping, no LLM call, no taxonomy file.
- ``dynamic`` mode: the leader invents member names per task (e.g.
  ``edtech-designer``, ``moe-expert``) — these must be classified into a
  small, persisted taxonomy of canonical role types, or L2/L3 memory keyed
  by raw member_name fragments into near-duplicates forever. Classification
  uses a cheap model tier: this is a low-stakes routing call, not a
  judgment call, and it is easy to re-derive if wrong.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.foundation.llm import JsonOutputParser, Model, SystemMessage, UserMessage
from openjiuwen.extensions.context_evolver.offline_memory import bank_io

SEED_TAXONOMY: dict[str, str] = {
    "leader": "Coordinates the team, assigns and tracks tasks, synthesizes the final call.",
    "researcher": "Gathers supporting evidence and background for a question.",
    "skeptic": "Independently reviews a design for gaps, contradictions, or weak claims.",
    "writer": "Synthesizes team input into the final deliverable document.",
    "domain_specialist": "Provides deep expertise in one specific technical sub-domain.",
    "architect": "Designs system/infrastructure structure (architecture, data flow, deployment).",
    "reviewer": "Verifies correctness or completeness of another member's work.",
}

_CLASSIFY_SYSTEM_PROMPT = """You classify a multi-agent teammate into a canonical role type for a \
memory system that accumulates collaboration notes across many tasks. The team roster changes \
every task (member names and personas are invented per-task), so notes must be keyed by a small, \
stable set of role types rather than by the raw invented name — otherwise every task creates a new \
role and nothing ever accumulates.

Given the member's display name and description, and the CURRENT taxonomy of canonical role types, \
decide:
- If an existing role type is a reasonable fit (same core function, even if the specific domain \
differs — e.g. "moe-expert" and "attention-expert" are both "domain_specialist"), return it.
- If no existing type fits without being misleading, propose ONE new canonical role type: a short \
lowercase_snake_case name plus a one-sentence, domain-agnostic description (describe the FUNCTION, \
not the specific subject matter, so it generalizes to future tasks).

Respond with a JSON object:
{"role_type": "<chosen or new type name>", "is_new": true|false, "new_description": "<only if is_new>"}
"""


class RosterMemberLike(Protocol):
    """Structural type for whatever roster-member record a caller has —
    decouples this module from any one trace-format's roster dataclass.
    """

    member_name: str
    display_name: str
    desc: str


class RoleClassificationResult(BaseModel):
    role_type: str = Field(default="")
    is_new: bool = Field(default=False)
    new_description: str = Field(default="")

    @field_validator("new_description", mode="before")
    @classmethod
    def _null_to_empty(cls, value: str | None) -> str:
        # The prompt documents new_description as "only if is_new" -- the
        # model correctly omits it (JSON null) whenever is_new is false.
        return value or ""


def _format_taxonomy(taxonomy: dict[str, str]) -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in sorted(taxonomy.items()))


class RoleNormalizer:
    def __init__(
        self,
        mode: str,
        taxonomy_path: Path | None = None,
        *,
        model: Model | None = None,
        retries: int = 3,
    ):
        if mode not in ("predefined", "dynamic"):
            raise ValueError(f"mode must be 'predefined' or 'dynamic', got {mode!r}")
        self.mode = mode
        self.taxonomy_path = taxonomy_path
        self._model = model
        self._retries = retries
        self._taxonomy: dict[str, str] = {}
        self._cache: dict[tuple[str, str], str] = {}  # (display_name, desc) -> role_type

        # predefined mode never reaches here — self._taxonomy stays {} and
        # unused, since resolve() short-circuits to identity below.
        if mode == "dynamic":
            if taxonomy_path is None:
                raise ValueError("dynamic mode requires taxonomy_path")
            if model is None:
                raise ValueError("dynamic mode requires a model for role classification")
            self._taxonomy = bank_io.load_yaml(taxonomy_path)
            if not self._taxonomy:
                self._taxonomy = dict(SEED_TAXONOMY)
                bank_io.save_yaml(self.taxonomy_path, self._taxonomy)

    async def resolve(self, member: RosterMemberLike) -> str:
        # predefined: roster names are already stable, identity is exact.
        if self.mode == "predefined":
            return member.member_name

        # dynamic: classify against the taxonomy, cached per (display_name, desc)
        # so the same invented persona seen across many episodes in one run
        # only costs one LLM call.
        cache_key = (member.display_name, member.desc)
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = await self._classify(member)

        role_type = result.role_type.strip().lower().replace(" ", "_")
        if not role_type:
            role_type = "domain_specialist"  # safe fallback, never leave a member unkeyed

        if result.is_new and role_type not in self._taxonomy:
            self._taxonomy[role_type] = result.new_description.strip() or member.desc
            bank_io.save_yaml(self.taxonomy_path, self._taxonomy)

        self._cache[cache_key] = role_type
        return role_type

    async def _classify(self, member: RosterMemberLike) -> RoleClassificationResult:
        messages = [
            SystemMessage(content=_CLASSIFY_SYSTEM_PROMPT),
            UserMessage(
                content=(
                    f"Current taxonomy:\n{_format_taxonomy(self._taxonomy)}\n\n"
                    f"Member to classify:\n"
                    f"display_name: {member.display_name}\n"
                    f"desc: {member.desc}"
                )
            ),
        ]
        parser = JsonOutputParser()
        last_err: Exception | None = None
        for attempt in range(self._retries):
            try:
                response = await self._model.invoke(messages=messages)
                res = await parser.parse(response.content)
                return RoleClassificationResult.model_validate(res)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_err = exc
                memory_logger.warning(
                    "role classification call failed on attempt %d/%d: %s", attempt + 1, self._retries, exc
                )
        raise_error(
            StatusCode.TOOLCHAIN_EVOLVING_MEMORY_LLM_GENERATION_EXECUTION_ERROR,
            reason=f"role classification failed after {self._retries} attempts: {last_err}",
        )

    async def resolve_roster(self, roster: list[RosterMemberLike]) -> dict[str, str]:
        """Return {member_name: role_type} for every member in the roster."""
        mapping: dict[str, str] = {}
        for member in roster:
            mapping[member.member_name] = await self.resolve(member)
        return mapping
