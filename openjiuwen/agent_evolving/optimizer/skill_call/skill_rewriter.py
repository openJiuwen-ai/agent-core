# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Skill rewriter: integrate evolution experiences into SKILL.md content."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.core.common.logging import logger

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.checkpointing import EvolutionStore


SKILL_REWRITE_PROMPT_CN = """\
你是一个 Skill 文档优化专家。根据当前 SKILL.md 内容和积累的经验记录，重写 SKILL.md 正文，将经验自然融入其中。

## 当前 SKILL.md

```markdown
{skill_content}
```

## 有效经验记录（按 section 分组）

{experiences_by_section}

## 用户主动描述的优化方向（可选）
{user_query}

## 重写任务

将上述经验记录自然融入 SKILL.md 正文，产出一份结构清晰、内容连贯的新版 SKILL.md。

### 融入原则

1. **知识融合而非简单追加**：经验中的知识点应合并进对应段落的自然描述，而非作为独立条目列出
2. **保留结构**：保留 YAML front-matter 和原有的 section 层级（## Instructions / ## Examples / ## Troubleshooting 等）
3. **可新增 section**：如果经验涉及新主题，可新增 section；但不要删除原有 section
4. **去重与更新**：如果经验与原有内容重复或矛盾，保留更新/更高分版本的信息
5. **语言一致**：输出语言与原 SKILL.md 保持一致
6. **移除索引块**：移除 evolution index block（<!-- evolution-index-start -->...<!-- evolution-index-end -->）

### 输出格式

只输出重写后的完整 SKILL.md 内容，用 markdown 代码块包裹：

```markdown
---
name: xxx
description: xxx
---

# xxx

...（完整内容）...
```

不要输出任何解释文字。"""

SKILL_REWRITE_PROMPT_EN = """\
You are a Skill documentation optimization expert. Rewrite the SKILL.md content by integrating the accumulated evolution experiences naturally into the document.

## Current SKILL.md

```markdown
{skill_content}
```

## Valid Experience Records (grouped by section)

{experiences_by_section}

## User-specified optimization direction (optional)
{user_query}

## Rewrite Task

Integrate the above experience records naturally into the SKILL.md body to produce a well-structured, coherent new version.

### Integration Principles

1. **Knowledge fusion, not simple appending**: Knowledge from experiences should merge into natural descriptions within corresponding paragraphs, not listed as standalone entries
2. **Preserve structure**: Keep YAML front-matter and original section hierarchy (## Instructions / ## Examples / ## Troubleshooting, etc.)
3. **New sections allowed**: You may add new sections for new topics, but do not delete existing sections
4. **Deduplication and updates**: If experiences duplicate or contradict existing content, keep the newer/higher-scored version
5. **Language consistency**: Output language must match the original SKILL.md
6. **Remove index block**: Remove the evolution index block (<!-- evolution-index-start -->...<!-- evolution-index-end -->)

### Output Format

Output only the complete rewritten SKILL.md content, wrapped in a markdown code block:

```markdown
---
name: xxx
description: xxx
---

# xxx

... (full content) ...
```

Do not output any explanatory text."""

SKILL_REWRITE_PROMPT: Dict[str, str] = {
    "cn": SKILL_REWRITE_PROMPT_CN,
    "en": SKILL_REWRITE_PROMPT_EN,
}

_RETRY_PROMPT_CN = """\
你上次的输出格式不正确。请重新输出重写后的 SKILL.md 内容。

要求：
1. 用 ```markdown 和 ``` 包裹完整内容
2. 保留 YAML front-matter（--- 开头部分）
3. 不要输出任何解释文字

上次输出预览：
{broken_preview}

请重新输出正确的格式。"""

_RETRY_PROMPT_EN = """\
Your previous output format was incorrect. Please re-output the rewritten SKILL.md content.

Requirements:
1. Wrap the full content with ```markdown and ```
2. Preserve YAML front-matter (starting with ---)
3. Do not output any explanatory text

Previous output preview:
{broken_preview}

Please output the correct format."""

_RETRY_PROMPT: Dict[str, str] = {
    "cn": _RETRY_PROMPT_CN,
    "en": _RETRY_PROMPT_EN,
}

# Minimum content ratio to prevent truncation
_MIN_CONTENT_RATIO = 0.5
# Max retry attempts for malformed output
_MAX_RETRIES = 1


@dataclass
class SkillRewriteResult:
    """Result of a skill rewrite operation."""

    skill_name: str
    original_content: str
    rewritten_content: str
    consumed_record_ids: List[str]
    records_cleaned: int
    summary: str


class SkillRewriter:
    """Rewrite SKILL.md by integrating evolution experiences.

    This rewriter uses LLM to deeply integrate experiences into the
    SKILL.md body for a more natural, coherent document.
    """

    def __init__(self, llm: Any, model: str, language: str = "cn") -> None:
        self._llm = llm
        self._model = model
        self._language = language

    def update_llm(self, llm: Any, model: str) -> None:
        """Update runtime llm/model for hot reload."""
        self._llm = llm
        self._model = model

    async def rewrite(
        self,
        skill_name: str,
        store: "EvolutionStore",
        *,
        min_score: float = 0.0,
        dry_run: bool = False,
        user_query: str = "",
    ) -> Optional[SkillRewriteResult]:
        """Rewrite SKILL.md by integrating evolution experiences.

        Args:
            skill_name: Name of the skill to rewrite
            store: EvolutionStore instance for reading/writing
            min_score: Minimum score threshold for experiences to include
            dry_run: If True, only return result without writing to disk
            user_query: Optional user-specified optimization direction

        Returns:
            SkillRewriteResult on success, None if no valid experiences or rewrite not needed
        """
        # Load current skill content
        skill_content = await store.read_skill_content(skill_name)
        if not skill_content:
            logger.warning("[SkillRewriter] skill '%s' has no content to rewrite", skill_name)
            return None

        # Load all evolution records
        evo_log = await store.load_evolution_log(skill_name)
        if not evo_log.entries:
            logger.info("[SkillRewriter] skill '%s' has no evolution records", skill_name)
            return None

        # Filter valid records by score
        valid_records = [
            record for record in evo_log.entries if record.score >= min_score and not record.change.skip_reason
        ]
        if not valid_records:
            logger.info("[SkillRewriter] skill '%s' has no valid records above min_score=%.2f", skill_name, min_score)
            return None

        # Group records by target and section for structured prompt
        experiences_text = self._format_experiences_by_section(valid_records)

        # Build prompt
        prompt = SKILL_REWRITE_PROMPT[self._language].format(
            skill_content=skill_content,
            experiences_by_section=experiences_text,
            user_query=user_query or ("无" if self._language == "cn" else "None"),
        )

        logger.info("[SkillRewriter] rewriting skill='%s' with %d valid records", skill_name, len(valid_records))

        # Call LLM
        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("[SkillRewriter] LLM call failed: %s", exc)
            return None

        # Parse and validate output
        rewritten = self._extract_markdown(raw)
        if not rewritten:
            rewritten = await self._retry_parse(raw, prompt)

        if not rewritten:
            logger.warning("[SkillRewriter] failed to parse LLM output for skill='%s'", skill_name)
            return None

        # Validate output
        if not self._validate_output(skill_content, rewritten):
            logger.warning("[SkillRewriter] validation failed for skill='%s'", skill_name)
            return None

        # Prepare result
        consumed_ids = [record.id for record in valid_records]
        summary = self._generate_summary(valid_records, skill_content, rewritten)

        result = SkillRewriteResult(
            skill_name=skill_name,
            original_content=skill_content,
            rewritten_content=rewritten,
            consumed_record_ids=consumed_ids,
            records_cleaned=0,
            summary=summary,
        )

        if dry_run:
            logger.info("[SkillRewriter] dry_run completed for skill='%s'", skill_name)
            return result

        # Write new content
        write_success = await store.write_skill_content(skill_name, rewritten)
        if not write_success:
            logger.error("[SkillRewriter] failed to write skill content for '%s'", skill_name)
            return None

        # Clean up consumed records
        cleaned_count = await store.delete_records(skill_name, consumed_ids)
        result.records_cleaned = cleaned_count

        logger.info("[SkillRewriter] successfully rewrote skill='%s', cleaned %d records", skill_name, cleaned_count)
        return result

    def _format_experiences_by_section(self, records: List[EvolutionRecord]) -> str:
        """Format records grouped by target and section for prompt."""
        # Group by (target, section)
        groups: Dict[tuple, List[EvolutionRecord]] = {}
        for record in records:
            key = (record.change.target.value, record.change.section)
            groups.setdefault(key, []).append(record)

        lines: List[str] = []
        for (target, section), group_records in sorted(groups.items()):
            lines.append(f"### {target} / {section}")
            lines.append("")
            # Sort by score descending
            sorted_records = sorted(group_records, key=lambda r: r.score, reverse=True)
            for record in sorted_records:
                content_preview = record.change.content[:200]
                if len(record.change.content) > 200:
                    content_preview += "..."
                lines.append(f"- **[{record.id}]** (score={record.score:.2f}, source={record.source})")
                lines.append(f"  {content_preview}")
                lines.append("")

        return (
            "\n".join(lines)
            if lines
            else ("无有效经验记录" if self._language == "cn" else "No valid experience records")
        )

    @staticmethod
    def _extract_markdown(raw: str) -> Optional[str]:
        """Extract markdown content from LLM output."""
        raw = raw.strip()
        if not raw:
            return None

        # Try to extract from ```markdown ... ``` block
        pattern = r"```markdown\s*\n(.*?)\n```"
        match = re.search(pattern, raw, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Try generic code block
        pattern = r"```\s*\n(.*?)\n```"
        match = re.search(pattern, raw, re.DOTALL)
        if match:
            return match.group(1).strip()

        # If no code block but content looks like markdown with front-matter
        if raw.startswith("---"):
            return raw

        return None

    async def _retry_parse(self, broken_raw: str, original_prompt: str) -> Optional[str]:
        """Retry once if output parsing failed."""
        preview = broken_raw[:500]
        retry_prompt = _RETRY_PROMPT[self._language].format(broken_preview=preview)

        logger.warning("[SkillRewriter] retrying parse after failure")
        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": retry_prompt}],
            )
            retry_raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("[SkillRewriter] retry LLM call failed: %s", exc)
            return None

        return self._extract_markdown(retry_raw)

    @staticmethod
    def _validate_output(original: str, rewritten: str) -> bool:
        """Validate rewritten content."""
        # Check front-matter preserved
        if original.startswith("---"):
            if not rewritten.startswith("---"):
                logger.warning("[SkillRewriter] validation: front-matter missing")
                return False

        # Check not too short (possible truncation)
        orig_len = len(original)
        new_len = len(rewritten)
        if orig_len > 0 and new_len < orig_len * _MIN_CONTENT_RATIO:
            logger.warning(
                "[SkillRewriter] validation: content too short (%.1f%% of original)", (new_len / orig_len) * 100
            )
            return False

        # Check has some structure (headings)
        if "#" not in rewritten:
            logger.warning("[SkillRewriter] validation: no headings found")
            return False

        return True

    def _generate_summary(
        self,
        records: List[EvolutionRecord],
        original: str,
        rewritten: str,
    ) -> str:
        """Generate a summary of the rewrite."""
        target_counts: Dict[str, int] = {}
        section_counts: Dict[str, int] = {}
        for record in records:
            target = record.change.target.value
            target_counts[target] = target_counts.get(target, 0) + 1
            section = record.change.section
            section_counts[section] = section_counts.get(section, 0) + 1

        orig_lines = len(original.splitlines())
        new_lines = len(rewritten.splitlines())

        if self._language == "cn":
            parts = [
                f"整合了 {len(records)} 条经验记录",
                f"目标分布: {', '.join(f'{k}={v}' for k, v in sorted(target_counts.items()))}",
                f"章节分布: {', '.join(f'{k}={v}' for k, v in sorted(section_counts.items()))}",
                f"行数变化: {orig_lines} -> {new_lines}",
            ]
        else:
            parts = [
                f"Integrated {len(records)} experience records",
                f"Target distribution: {', '.join(f'{k}={v}' for k, v in sorted(target_counts.items()))}",
                f"Section distribution: {', '.join(f'{k}={v}' for k, v in sorted(section_counts.items()))}",
                f"Line count: {orig_lines} -> {new_lines}",
            ]

        return "; ".join(parts)


__all__ = [
    "SkillRewriter",
    "SkillRewriteResult",
]
