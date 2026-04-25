# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Experience scoring and maintenance logic for skill evolution."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord, UsageStats
from openjiuwen.core.common.logging import logger

# E/U/F score weights
W_E = 0.5
W_U = 0.3
W_F = 0.2

FRESHNESS_HALF_LIFE_DAYS = 90
STALE_VERSION_PENALTY = 0.7


# Evaluate experience effectiveness from conversation snippet
EXPERIENCE_EVAL_PROMPT_CN = """\
你是一个经验评估专家。根据对话片段，评估之前展示给 Agent 的经验是否被有效使用。

## 展示给 Agent 的经验
{presented_experiences}

## 对话片段（展示经验之后的部分）
{conversation_snippet}

## 评估任务
对于每条展示的经验，判断：
1. 该经验是否被 Agent 理解和采纳（内容被用于指导后续行为）
2. 该经验是否产生了积极效果（帮助解决了问题或改进了输出）
3. 该经验是否产生了消极效果（导致错误或误导）

## 输出格式
输出 JSON 数组，每条经验一个对象：
```json
[
  {{
    "record_id": "经验ID",
    "used": true/false,
    "positive": true/false,
    "negative": true/false,
    "reason": "简短说明"
  }}
]
```

只输出 JSON，不要其他内容。"""

EXPERIENCE_EVAL_PROMPT_EN = """\
You are an experience evaluation expert. Based on the conversation snippet, evaluate whether the previously presented experiences were effectively used by the Agent.

## Experiences Presented to Agent
{presented_experiences}

## Conversation Snippet (after presenting experiences)
{conversation_snippet}

## Evaluation Task
For each presented experience, determine:
1. Whether the experience was understood and adopted by the Agent (content used to guide subsequent behavior)
2. Whether the experience produced positive effects (helped solve problems or improved output)
3. Whether the experience produced negative effects (caused errors or mislead)

## Output Format
Output a JSON array, one object per experience:
```json
[
  {{
    "record_id": "experience ID",
    "used": true/false,
    "positive": true/false,
    "negative": true/false,
    "reason": "brief explanation"
  }}
]
```

Output only JSON, no other content."""

EXPERIENCE_EVAL_PROMPT: Dict[str, str] = {
    "cn": EXPERIENCE_EVAL_PROMPT_CN,
    "en": EXPERIENCE_EVAL_PROMPT_EN,
}


# Simplify/maintain experience library
SIMPLIFY_PROMPT_CN = """\
你是一个经验库维护专家。根据当前经验的评分和使用情况，生成整理建议。

## Skill 名称
{skill_name}

## Skill 摘要
{skill_summary}

## 当前经验列表（按分数排序）
{scored_experiences}

## 整理操作类型
- DELETE: 删除低质量或过时的经验
- MERGE: 合并多条相似经验为一条
- REFINE: 优化单条经验的内容
- KEEP: 保留不变

## 规则
1. 删除分数低于 0.4 且使用率为 0 的经验
2. 合并内容高度相似的经验（保留分数最高的作为 primary）
3. 优化内容模糊或格式不规范的经验
4. 保留高质量、高使用率的经验

## 输出格式
输出 JSON 数组：
```json
[
  {{
    "action": "DELETE | MERGE | REFINE | KEEP",
    "record_id": "目标经验ID",
    "reason": "操作原因",
    "merge_remove_ids": ["要合并删除的经验ID列表（仅 MERGE 时）"],
    "new_content": "新内容（仅 REFINE 或 MERGE 时）"
  }}
]
```

只输出 JSON，不要其他内容。"""

SIMPLIFY_PROMPT_EN = """\
You are an experience library maintenance expert. Based on current experience scores and usage, generate organization suggestions.

## Skill Name
{skill_name}

## Skill Summary
{skill_summary}

## Current Experience List (sorted by score)
{scored_experiences}

## Maintenance Actions
- DELETE: Remove low-quality or outdated experiences
- MERGE: Combine multiple similar experiences into one
- REFINE: Optimize content of a single experience
- KEEP: Keep unchanged

## Rules
1. Delete experiences with score below 0.4 and zero utilization
2. Merge highly similar experiences (keep the highest-scored as primary)
3. Refine experiences with vague or poorly formatted content
4. Keep high-quality, high-utilization experiences

## Output Format
Output a JSON array:
```json
[
  {{
    "action": "DELETE | MERGE | REFINE | KEEP",
    "record_id": "target experience ID",
    "reason": "reason for action",
    "merge_remove_ids": ["list of experience IDs to merge and remove (MERGE only)"],
    "new_content": "new content (REFINE or MERGE only)"
  }}
]
```

Output only JSON, no other content."""

SIMPLIFY_PROMPT: Dict[str, str] = {
    "cn": SIMPLIFY_PROMPT_CN,
    "en": SIMPLIFY_PROMPT_EN,
}


def calc_effectiveness(stats: UsageStats) -> float:
    """Calculate E (Effectiveness) score using Bayesian smoothing.

    Uses Beta(1,1) prior for smoothing, equivalent to adding 1 success and 1 failure.
    """
    total = stats.times_positive + stats.times_negative
    if total == 0:
        return 0.5  # No data, return neutral

    # Beta(1,1) prior: (positive + 1) / (total + 2)
    return (stats.times_positive + 1) / (total + 2)


def calc_utilization(stats: UsageStats) -> float:
    """Calculate U (Utilization) score: ratio of times_used to times_presented."""
    if stats.times_presented == 0:
        return 0.5  # No data, return neutral

    return stats.times_used / stats.times_presented


def calc_freshness(record: EvolutionRecord, current_skill_version: Optional[str] = None) -> float:
    """Calculate F (Freshness) score based on time decay and version staleness.

    Applies exponential decay with configurable half-life, plus version staleness penalty.
    """
    if not record.timestamp:
        return 0.5

    try:
        record_time = datetime.fromisoformat(record.timestamp.replace("Z", "+00:00"))
        if record_time.tzinfo is None:
            record_time = record_time.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 0.5

    now = datetime.now(timezone.utc)
    days_old = (now - record_time).days

    # Exponential decay: score = 0.5 * 2^(-days / half_life)
    decay_factor = 0.5 * (2 ** (-days_old / FRESHNESS_HALF_LIFE_DAYS))
    freshness = 0.5 + decay_factor  # Range: 0.5 to 1.0

    # Apply version staleness penalty if skill version is known
    if current_skill_version and record.skill_version:
        if record.skill_version != current_skill_version:
            freshness *= STALE_VERSION_PENALTY

    return max(0.0, min(1.0, freshness))


def calc_score(record: EvolutionRecord, current_skill_version: Optional[str] = None) -> float:
    """Calculate overall score as weighted sum of E/U/F components."""
    stats = record.usage_stats or UsageStats()

    e = calc_effectiveness(stats)
    u = calc_utilization(stats)
    f = calc_freshness(record, current_skill_version)

    return W_E * e + W_U * u + W_F * f


def update_score(
    record: EvolutionRecord,
    eval_result: Dict[str, Any],
    current_skill_version: Optional[str] = None,
) -> float:
    """Update record's usage stats and recalculate score based on evaluation result.

    Args:
        record: The evolution record to update
        eval_result: Dict with "used", "positive", "negative" boolean keys
        current_skill_version: Optional current skill version for freshness calc

    Returns:
        The new calculated score
    """
    if record.usage_stats is None:
        record.usage_stats = UsageStats()

    stats = record.usage_stats

    if eval_result.get("used"):
        stats.times_used += 1
    if eval_result.get("positive"):
        stats.times_positive += 1
    if eval_result.get("negative"):
        stats.times_negative += 1

    stats.last_evaluated_at = datetime.now(tz=timezone.utc).isoformat()

    record.score = calc_score(record, current_skill_version)
    return record.score


class ExperienceScorer:
    """LLM-based experience scorer and maintainer."""

    def __init__(self, llm: Any, model: str, language: str = "cn") -> None:
        self._llm = llm
        self._model = model
        self._language = language

    def update_llm(self, llm: Any, model: str) -> None:
        """Update runtime llm/model for hot reload."""
        self._llm = llm
        self._model = model

    async def evaluate(
        self,
        conversation_snippet: str,
        presented_records: List[EvolutionRecord],
    ) -> List[Dict[str, Any]]:
        """Evaluate whether presented experiences were effectively used.

        Args:
            conversation_snippet: Text of conversation after experiences were shown
            presented_records: List of records that were presented

        Returns:
            List of evaluation results with record_id, used, positive, negative, reason
        """
        if not presented_records:
            return []

        formatted = self._format_presented_experiences(presented_records)
        prompt = EXPERIENCE_EVAL_PROMPT[self._language].format(
            presented_experiences=formatted,
            conversation_snippet=conversation_snippet[:4000],
        )

        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("[ExperienceScorer] evaluate LLM call failed: %s", exc)
            return []

        results = self._parse_llm_json(raw)
        if results is None:
            logger.warning("[ExperienceScorer] evaluate: failed to parse LLM response")
            return []

        return results

    async def simplify(
        self,
        skill_name: str,
        skill_summary: str,
        records: List[EvolutionRecord],
        user_intent: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Generate maintenance actions for experience library.

        Args:
            skill_name: Name of the skill
            skill_summary: Summary of skill content
            records: Current experience records (should be sorted by score)
            user_intent: Optional natural-language instructions guiding simplification

        Returns:
            List of action dicts with action, record_id, reason, etc.
        """
        if not records:
            return []

        formatted = self._format_scored_experiences(records)
        prompt = SIMPLIFY_PROMPT[self._language].format(
            skill_name=skill_name,
            skill_summary=skill_summary[:1000],
            scored_experiences=formatted,
        )
        if user_intent:
            prompt += f"\n\n**用户意图**: {user_intent}"

        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("[ExperienceScorer] simplify LLM call failed: %s", exc)
            return []

        actions = self._parse_llm_json(raw)
        if actions is None:
            logger.warning("[ExperienceScorer] simplify: failed to parse LLM response")
            return []

        return actions

    async def execute_simplify_actions(
        self,
        store: Any,
        skill_name: str,
        actions: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """Execute maintenance actions on the experience store.

        Args:
            store: EvolutionStore instance
            skill_name: Name of the skill
            actions: List of action dicts from simplify()

        Returns:
            Dict with counts: {"deleted": N, "merged": N, "refined": N, "kept": N, "errors": N}
        """
        counts = {"deleted": 0, "merged": 0, "refined": 0, "kept": 0, "errors": 0}

        for action in actions:
            action_type = action.get("action", "KEEP")
            record_id = action.get("record_id", "")

            try:
                if action_type == "DELETE":
                    deleted = await store.delete_records(skill_name, [record_id])
                    if deleted > 0:
                        counts["deleted"] += 1
                    else:
                        counts["errors"] += 1

                elif action_type == "MERGE":
                    remove_ids = action.get("merge_remove_ids", [])
                    new_content = action.get("new_content", "")
                    result = await store.merge_records(
                        skill_name,
                        record_id,
                        remove_ids,
                        new_content,
                    )
                    if result:
                        counts["merged"] += 1
                    else:
                        counts["errors"] += 1

                elif action_type == "REFINE":
                    new_content = action.get("new_content", "")
                    result = await store.update_record_content(
                        skill_name,
                        record_id,
                        new_content,
                    )
                    if result:
                        counts["refined"] += 1
                    else:
                        counts["errors"] += 1

                elif action_type == "KEEP":
                    counts["kept"] += 1

                else:
                    logger.warning(
                        "[ExperienceScorer] unknown action type: %s",
                        action_type,
                    )
                    counts["errors"] += 1

            except Exception as exc:
                logger.error(
                    "[ExperienceScorer] execute action %s failed for %s: %s",
                    action_type,
                    record_id,
                    exc,
                )
                counts["errors"] += 1

        logger.info(
            "[ExperienceScorer] executed simplify actions for skill=%s: %s",
            skill_name,
            counts,
        )
        return counts

    @staticmethod
    def _format_presented_experiences(records: List[EvolutionRecord]) -> str:
        """Format presented experiences for prompt."""
        lines: List[str] = []
        for record in records:
            content = record.change.content[:200]
            lines.append(f"[{record.id}] {content}")
        return "\n".join(lines)

    @staticmethod
    def _format_scored_experiences(records: List[EvolutionRecord]) -> str:
        """Format scored experiences for prompt."""
        lines: List[str] = []
        for record in records:
            stats = record.usage_stats or UsageStats()
            content = record.change.content[:150]
            lines.append(
                f"[{record.id}] score={record.score:.2f} | "
                f"presented={stats.times_presented} used={stats.times_used} | "
                f"{content}"
            )
        return "\n".join(lines)

    @staticmethod
    def _parse_llm_json(raw: str) -> Optional[List[Dict[str, Any]]]:
        """Best-effort JSON parsing from LLM output."""
        raw = raw.strip()
        if not raw:
            return None

        # Remove markdown code blocks
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"//[^\n]*", "", raw)
        raw = re.sub(r",\s*([}\]])", r"\1", raw)
        raw = raw.strip()

        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
            return None
        except json.JSONDecodeError:
            # Try to extract JSON array
            match = re.search(r"\[[\s\S]*\]", raw)
            if match:
                try:
                    data = json.loads(match.group(0))
                    return data if isinstance(data, list) else None
                except json.JSONDecodeError:
                    pass
            return None


__all__ = [
    "W_E",
    "W_U",
    "W_F",
    "FRESHNESS_HALF_LIFE_DAYS",
    "STALE_VERSION_PENALTY",
    "calc_effectiveness",
    "calc_utilization",
    "calc_freshness",
    "calc_score",
    "update_score",
    "ExperienceScorer",
]
