# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""L2 (pairwise collaboration) reflection: one directed pair, one LLM call.

Combines what a live team member does as two back-to-back tool calls
(``update_correlation`` + ``update_teammate_profile``) into one JSON output
— same evidence, same reflective act, replayed offline from trace evidence
instead of lived experience.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.foundation.llm import JsonOutputParser, Model, SystemMessage, UserMessage
from openjiuwen.extensions.context_evolver.offline_memory.prompts import L2_SYSTEM_PROMPT, build_l2_user_prompt


class L2ReflectionResult(BaseModel):
    correlation_notes: str = Field(default="")
    profile: dict[str, Any] = Field(default_factory=dict)


async def reflect_pair(
    model: Model,
    *,
    reflecting_role: str,
    partner_role: str,
    evidence_block: str,
    existing_notes: str,
    retries: int = 3,
) -> L2ReflectionResult:
    """Call ``model`` to produce curated correlation notes + new-only
    profile observations for one (reflecting_role, partner_role) pair.

    ``existing_notes`` is shown to the model (to curate, not restate) —
    the existing profile is deliberately NOT shown; see ``L2_SYSTEM_PROMPT``
    for why (avoids near-duplicate restatement bloating the profile list).
    Merging the returned ``profile`` into a stored profile is the caller's
    job (``bank_io.merge_profile``), not this function's.
    """
    messages = [
        SystemMessage(content=L2_SYSTEM_PROMPT),
        UserMessage(
            content=build_l2_user_prompt(
                reflecting_role=reflecting_role,
                partner_role=partner_role,
                evidence_block=evidence_block,
                existing_notes=existing_notes,
            )
        ),
    ]
    parser = JsonOutputParser()
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            response = await model.invoke(messages=messages)
            res = await parser.parse(response.content)
            return L2ReflectionResult.model_validate(res)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_err = exc
            memory_logger.warning("L2 reflection call failed on attempt %d/%d: %s", attempt + 1, retries, exc)
    raise_error(
        StatusCode.TOOLCHAIN_EVOLVING_MEMORY_LLM_GENERATION_EXECUTION_ERROR,
        reason=f"L2 reflection failed after {retries} attempts: {last_err}",
    )
