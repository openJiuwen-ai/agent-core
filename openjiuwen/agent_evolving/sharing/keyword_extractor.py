# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""KeywordExtractor: bridges the optimizer output and the sharer query path.

Two responsibilities:
1. **Upload path** – parse keywords/summary that the modified
   ``SkillExperienceOptimizer`` already produced as part of the existing
   LLM call. No extra network round-trip.
2. **Download path** – run a small, focused LLM call against a conversation
   excerpt (user queries, tool execution results, etc.) to obtain
   ``QueryKeywords`` for retrieval.
   On LLM failure returns empty keywords so the calling rail never blocks.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional, Tuple

from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch
from openjiuwen.agent_evolving.optimizer.llm_resilience import (
    LLMInvokePolicy,
    invoke_text_with_retry,
)
from openjiuwen.agent_evolving.sharing.types import QueryKeywords
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model

QUERY_KEYWORDS_LLM_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=1500,
    total_budget_secs=4000,
    max_attempts=5,
)


_QUERY_KEYWORDS_PROMPT_CN = """\
你是一个检索关键词抽取器。下面是从对话中提取的关键信息片段，包含用户查询、工具执行结果（特别是出错的）等，请提取用于"跨用户经验检索"的关键词。

## 输入
{excerpt}

## 当前 Skill 提示（可能为空）
{skill_hint}

## 输出要求
- 关键词 10-20 个，覆盖问题的核心概念，避免主语/口头语
- 优先输出英文标识符 / 报错关键字；同时给出对应中文术语，提升召回
- 同时给出 <=40 字的查询意图描述
- 严格输出以下 JSON，不要任何其它内容（包括 Markdown 代码块）：

{{
  "keywords": ["..."],
  "intent": "..."
}}"""


_QUERY_KEYWORDS_PROMPT_EN = """\
You are a retrieval keyword extractor. The text below is an excerpt from a conversation,
containing user queries, tool execution results (especially failed ones), etc.
Extract keywords useful for *cross-user experience retrieval*.

## Input
{excerpt}

## Current skill hint (may be empty)
{skill_hint}

## Output requirements
- 10-15 keywords covering the core concepts; avoid pronouns / fillers
- Prefer English code identifiers / error keywords; you may add the matching Chinese term to widen recall
- Plus an intent string of <= 40 characters
- Output ONLY this JSON (no Markdown, no explanation):

{{
  "keywords": ["..."],
  "intent": "..."
}}"""


_PROMPTS = {
    "cn": _QUERY_KEYWORDS_PROMPT_CN,
    "en": _QUERY_KEYWORDS_PROMPT_EN,
}


def _extract_query_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    matched = re.search(r"\{[\s\S]*\}", raw)
    if not matched:
        return None
    try:
        return json.loads(matched.group(0))
    except json.JSONDecodeError:
        return None


class KeywordExtractor:
    """Produces ``(keywords, summary)`` for upload and ``QueryKeywords`` for download."""

    def __init__(
        self,
        llm: Optional[Model] = None,
        model: Optional[str] = None,
        language: str = "cn",
        query_llm_policy: LLMInvokePolicy = QUERY_KEYWORDS_LLM_POLICY,
    ) -> None:
        self._llm = llm
        self._model = model
        self._language = language if language in _PROMPTS else "cn"
        self._query_llm_policy = query_llm_policy

    def update_llm(self, llm: Optional[Model], model: Optional[str]) -> None:
        self._llm = llm
        self._model = model

    @staticmethod
    def parse_from_optimizer_output(raw_patch: Any) -> Tuple[List[str], str]:
        """Read ``keywords`` / ``summary`` from either a dict or an EvolutionPatch.

        The optimizer prompt is augmented to emit these two fields alongside
        the existing ``EvolutionPatch`` fields. Either an in-memory
        ``EvolutionPatch`` (already parsed) or the raw JSON dict that came
        out of the LLM is acceptable here.
        """
        keywords: List[str] = []
        summary = ""

        if isinstance(raw_patch, EvolutionPatch):
            kws = raw_patch.keywords or []
            keywords = [str(k).strip() for k in kws if str(k).strip()]
            summary = (raw_patch.summary or "").strip()
        elif isinstance(raw_patch, dict):
            kws = raw_patch.get("keywords") or []
            if isinstance(kws, list):
                keywords = [str(k).strip() for k in kws if str(k).strip()]
            raw_summary = raw_patch.get("summary")
            if isinstance(raw_summary, str):
                summary = raw_summary.strip()
        return keywords, summary

    async def extract_query_keywords(
        self,
        feedback_excerpt: str,
        skill_hint: Optional[str] = None,
    ) -> QueryKeywords:
        """Return ``QueryKeywords`` extracted from a conversation excerpt.

        The excerpt typically contains user queries, tool execution results
        (especially failed ones), and other key information for retrieval.

        Strategy: prefer a small focused LLM call; on failure return empty
        keywords so the calling rail never blocks.
        """
        excerpt = (feedback_excerpt or "").strip()
        if not excerpt:
            return QueryKeywords(keywords=[], intent="", raw_excerpt="")

        if self._llm is None or not self._model:
            logger.debug("[KeywordExtractor] no LLM bound, skipping query keyword extraction")
            return QueryKeywords(keywords=[], intent=excerpt[:40], raw_excerpt=excerpt)

        logger.info("[KeywordExtractor] query before keyword extraction:\n%s", excerpt)

        prompt = _PROMPTS[self._language].format(
            excerpt=excerpt,
            skill_hint=(skill_hint or "").strip() or ("无" if self._language == "cn" else "None"),
        )
        try:
            raw = await invoke_text_with_retry(
                llm=self._llm,
                model=self._model,
                prompt=prompt,
                policy=self._query_llm_policy,
                temperature=0.2,
            )
        except BaseError as exc:
            logger.warning("[KeywordExtractor] LLM call failed (%s)", exc)
            return QueryKeywords(keywords=[], intent=excerpt[:40], raw_excerpt=excerpt)
        except Exception as exc:  # noqa: BLE001 - resilience boundary
            logger.warning("[KeywordExtractor] unexpected LLM error (%s)", exc)
            return QueryKeywords(keywords=[], intent=excerpt[:40], raw_excerpt=excerpt)

        data = _extract_query_json(raw)
        if not isinstance(data, dict):
            logger.warning("[KeywordExtractor] LLM JSON parse failed")
            return QueryKeywords(keywords=[], intent=excerpt[:40], raw_excerpt=excerpt)

        raw_keywords = data.get("keywords") or []
        if not isinstance(raw_keywords, list):
            raw_keywords = []
        keywords = [str(item).strip() for item in raw_keywords if str(item).strip()]
        intent = str(data.get("intent", "") or "").strip()[:80]
        return QueryKeywords(keywords=keywords[:20], intent=intent, raw_excerpt=excerpt)


__all__ = [
    "KeywordExtractor",
    "QUERY_KEYWORDS_LLM_POLICY",
]
