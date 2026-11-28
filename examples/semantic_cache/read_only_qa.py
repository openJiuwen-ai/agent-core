# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Explicit integration with Model.invoke; no global hooks or tool execution.

The host authorizes each request before calling answer_question and supplies
trusted scope plus its own answer-equivalence and storage policies.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from openjiuwen.extensions.semantic_cache import (
    CacheCandidate,
    CacheResult,
    CacheScope,
    GeneratedAnswer,
    SemanticResponseCache,
)

if TYPE_CHECKING:
    from openjiuwen.core.foundation.llm import Model
    from openjiuwen.core.foundation.llm.schema.message import AssistantMessage


async def answer_question(
    model: "Model",
    cache: SemanticResponseCache,
    question: str,
    *,
    system_prompt: str,
    scope: CacheScope,
    can_reuse: Callable[[str, CacheCandidate], Awaitable[bool]],
    may_store: Callable[["AssistantMessage"], bool],
) -> CacheResult:
    """Host fixes the model/config and versions system_prompt in scope.template.

    may_store must reject refusals, sensitive data, stale or otherwise ineligible
    answers. can_reuse checks semantics beyond vector similarity; it is required.
    """

    async def generate(prompt: str) -> GeneratedAnswer:
        response = await model.invoke(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
        )
        if not isinstance(response.content, str):
            raise TypeError("Only plain text QA is supported")
        complete = response.finish_reason == "stop" and not response.tool_calls
        return GeneratedAnswer(response.content, reusable=complete and may_store(response))

    return await cache.invoke(
        question,
        scope=scope,
        generate=generate,
        can_reuse=can_reuse,
        eligible=True,
    )
