# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Online Skill experience optimizer."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from openjiuwen.agent_evolving.checkpointing.types import (
    VALID_SECTIONS,
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.experience.types import EvolutionContext
from openjiuwen.agent_evolving.optimizer.base import BaseOptimizer
from openjiuwen.agent_evolving.optimizer.llm_resilience import (
    LLMInvokePolicy,
    invoke_text_with_retry_and_prompt,
)
from openjiuwen.agent_evolving.optimizer.skill_call.templates import (
    SKILL_EXPERIENCE_GENERATE_PROMPT,
    JSON_FIX_PROMPT,
    JSON_FIX_PROMPT_STRICT,
)
from openjiuwen.agent_evolving.signal.base import EvolutionSignal
from openjiuwen.agent_evolving.protocols import EXPERIENCES_TARGET
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model

# Initial score mapping by signal type
INITIAL_SCORE_BY_SIGNAL = {
    "execution_failure": 0.65,
    "user_correction": 0.70,
    "script_artifact": 0.60,
    "conversation_review": 0.50,
}

GENERATE_RECORDS_LLM_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=60,
    total_budget_secs=180,
    max_attempts=3,
)
_RETRY_PARSE_TIMEOUT_SECS = 20


def _build_conversation_snippet(
    messages: List[dict],
    max_messages: int = 30,
    content_preview_chars: int = 300,
    language: str = "cn",
) -> str:
    """Build compact dialogue snippet for LLM prompt context."""
    if not messages:
        return ""

    def _extract_text(message: dict) -> str:
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts)
        return str(content)

    lines: List[str] = []
    recent = messages[-max_messages:]
    for i, message in enumerate(recent):
        role = message.get("role", "unknown")
        text = _extract_text(message).strip() or ("(无文本)" if language == "cn" else "(No text)")
        budget = content_preview_chars * 2 if i >= len(recent) - 5 else content_preview_chars
        if len(text) > budget:
            orig_len = len(text)
            text = text[:budget] + (
                f"\n... [已截断，原始长度 {orig_len} 字符]"
                if language == "cn"
                else f"\n... [truncated, original {orig_len} chars]"
            )
        tool_calls = message.get("tool_calls")
        if role == "assistant" and tool_calls:
            names = [tool_call.get("name", "") for tool_call in tool_calls if isinstance(tool_call, dict)]
            prefix = f"[assistant] (tool_calls: {', '.join(names)})\n  "
        else:
            prefix = f"[{role}] "
        lines.append(prefix + text)
    return "\n".join(lines)


_SKILL_CONTENT_MAX_CHARS = 6000
_HEADING_RE = re.compile(r"^#{1,4}\s+")
_SECTION_PREVIEW_CHARS = 200


def _summarize_skill_content(raw: str, max_chars: int = _SKILL_CONTENT_MAX_CHARS) -> str:
    """Condense a large SKILL.md for the evolution LLM."""
    if len(raw) <= max_chars:
        return raw

    sections = _split_into_sections(raw)
    if not sections:
        return raw[:max_chars] + f"\n... [已截断，原始共 {len(raw)} 字符]"

    parts: list[str] = [sections[0]]
    if len(sections) > 1:
        parts.append("\n[以下章节仅保留标题与开头摘要，完整内容已省略]\n")
        for section in sections[1:]:
            parts.append(_preview_section(section))

    summary = "\n".join(parts)
    if len(summary) > max_chars:
        summary = summary[:max_chars] + f"\n... [已截断，原始 SKILL.md 共 {len(raw)} 字符]"
    return summary


def _split_into_sections(text: str) -> list[str]:
    """Split markdown into sections by top-level headings (# or ##)."""
    lines = text.split("\n")
    sections: list[str] = []
    current: list[str] = []

    for line in lines:
        if _HEADING_RE.match(line) and current:
            sections.append("\n".join(current))
            current = []
        current.append(line)

    if current:
        sections.append("\n".join(current))
    return sections


def _preview_section(section: str, preview_chars: int = _SECTION_PREVIEW_CHARS) -> str:
    """Return heading + first preview_chars of body text."""
    lines = section.split("\n")
    heading = lines[0]
    body = "\n".join(lines[1:]).strip()
    if not body:
        return heading
    if len(body) <= preview_chars:
        return section
    return f"{heading}\n{body[:preview_chars]}..."


def _fix_json_text(text: str) -> str:
    """Apply common fixes to malformed JSON produced by LLMs."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text.strip()


def _try_parse(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_json(raw: str) -> Optional[Any]:
    """Best-effort JSON extraction from LLM output."""
    raw = raw.strip()
    if not raw:
        return None

    result = _try_parse(raw)
    if result is not None:
        return result

    fixed = _fix_json_text(raw)
    result = _try_parse(fixed)
    if result is not None:
        return result

    # Step 3: regex extract outer [ ... ] or { ... } from already-fixed text
    for pattern in (r"\[[\s\S]*\]", r"\{[\s\S]*\}"):
        matched = re.search(pattern, fixed)
        if matched:
            result = _try_parse(matched.group(0))
            if result is not None:
                return result
            refixed = _fix_json_text(matched.group(0))
            result = _try_parse(refixed)
            if result is not None:
                return result

    return None


def _extract_json_with_error(raw: str) -> tuple[Any, str] | tuple[None, str]:
    """Like _extract_json but also returns the last parse error message."""
    raw = raw.strip()
    if not raw:
        return None, "empty response"

    last_error = "unknown"
    result = _try_parse(raw)
    if result is not None:
        return result, ""

    fixed = _fix_json_text(raw)
    result = _try_parse(fixed)
    if result is not None:
        return result, ""

    for pattern in (r"\[[\s\S]*\]", r"\{[\s\S]*\}"):
        matched = re.search(pattern, fixed)
        if matched:
            result = _try_parse(matched.group(0))
            if result is not None:
                return result, ""
            refixed = _fix_json_text(matched.group(0))
            try:
                parsed = json.loads(refixed)
                return parsed, ""
            except json.JSONDecodeError as e:
                last_error = str(e)

    return None, last_error


_CONTEXT_MAX_CHARS = 500


def _build_context(signals: list, max_chars: int = _CONTEXT_MAX_CHARS) -> str:
    """Build a concise context string from signals, capped to max_chars."""
    if not signals:
        return ""
    per_signal = max(80, max_chars // len(signals))
    parts: list[str] = []
    for sig in signals:
        excerpt = sig.excerpt.strip()
        if len(excerpt) > per_signal:
            excerpt = excerpt[:per_signal] + "..."
        parts.append(f"[{sig.signal_type}] {excerpt}")
    return " | ".join(parts)


def _looks_truncated(text: str) -> bool:
    """Heuristic check: does the LLM output look like it was cut off mid-way?"""
    opens = text.count("{") + text.count("[")
    closes = text.count("}") + text.count("]")
    return opens > closes + 1


def _build_existing_summary(records: List[EvolutionRecord], label: str = "") -> str:
    if not records:
        return ""
    lines: List[str] = []
    for record in records:
        prefix = f"[{label}] " if label else ""
        lines.append(f"- {prefix}[{record.id}] [{record.change.section}] {record.change.content}")
    return "\n".join(lines)


def _limit_summary_lines(summary: str, max_lines: int) -> str:
    if not summary or max_lines <= 0:
        return ""
    return "\n".join(summary.splitlines()[:max_lines])


def _parse_single_patch(data: dict) -> Optional[EvolutionPatch]:
    action = data.get("action", "append")
    if action == "skip":
        return EvolutionPatch(
            section="",
            action="skip",
            content="",
            skip_reason=data.get("skip_reason", "unknown"),
        )

    section = data.get("section", "Troubleshooting")
    if section not in VALID_SECTIONS:
        section = "Troubleshooting"

    raw_target = data.get("target", "body")
    try:
        target = EvolutionTarget(raw_target)
    except ValueError:
        target = EvolutionTarget.BODY

    merge_target = data.get("merge_target")
    if merge_target in ("null", None):
        merge_target = None

    return EvolutionPatch(
        section=section,
        action="append",
        content=data.get("content", ""),
        target=target,
        merge_target=merge_target,
        script_filename=data.get("script_filename"),
        script_language=data.get("script_language"),
        script_purpose=data.get("script_purpose"),
    )


def _parse_llm_response(raw: str) -> Optional[List[EvolutionPatch]]:
    """Parse response JSON into EvolutionPatch list. Returns None on parse failure."""
    data = _extract_json(raw)
    if data is None:
        return None

    items = data if isinstance(data, list) else [data]
    patches: List[EvolutionPatch] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        patch = _parse_single_patch(item)
        if patch is not None:
            patches.append(patch)
    return patches


class SkillExperienceOptimizer(BaseOptimizer):
    """Online Skill experience optimizer.

    Signals arrive from SkillEvolutionRail and are consumed through the
    optimizer's neutral signal-selection contract. _backward() groups
    selected signals by skill_name and generates EvolutionRecord(s).
    """

    domain = "skill_experience"

    def __init__(
        self,
        llm: Model,
        model: str,
        language: str = "cn",
        generate_records_llm_policy: LLMInvokePolicy = GENERATE_RECORDS_LLM_POLICY,
    ) -> None:
        super().__init__()
        self._llm = llm
        self._model = model
        self._language = language
        self._generate_records_llm_policy = generate_records_llm_policy
        self._online_contexts: Dict[str, EvolutionContext] = {}

    @property
    def generate_records_llm_policy(self) -> LLMInvokePolicy:
        """Get the configured record generation policy."""
        return self._generate_records_llm_policy

    @property
    def llm(self) -> Model:
        """Get the configured LLM client."""
        return self._llm

    @property
    def model(self) -> str:
        """Get the configured model name."""
        return self._model

    @staticmethod
    def default_targets() -> List[str]:
        return [EXPERIENCES_TARGET]

    def bind(
        self,
        operators: Optional[Dict[str, Any]] = None,
        targets: Optional[List[str]] = None,
        **config: Any,
    ) -> int:
        self._online_contexts = dict(config.get("online_contexts") or {})
        return super().bind(operators=operators, targets=targets, **config)

    async def _backward(self, signals: List[EvolutionSignal]) -> None:
        """Generate experience records for each bound SkillExperienceOperator."""
        for op_id, op in self._operators.items():
            skill_name = op_id.removeprefix("skill_experience_")
            skill_signals = [s for s in self._selected_signals if s.skill_name == skill_name or not s.skill_name]
            if not skill_signals:
                continue
            ctx = self._build_evolution_context(skill_name, op, skill_signals)
            records = await self.generate_records(ctx)
            if not records:
                logger.info("[SkillExperienceOptimizer] no records generated for skill=%s", skill_name)
                continue
            existing: List = self._parameters[op_id].get_gradient(EXPERIENCES_TARGET) or []
            self._parameters[op_id].set_gradient(EXPERIENCES_TARGET, existing + records)
            logger.info(
                "[SkillExperienceOptimizer] generated %d record(s) for skill=%s",
                len(records),
                skill_name,
            )

    def _build_evolution_context(
        self,
        skill_name: str,
        operator: Any,
        skill_signals: List[EvolutionSignal],
    ) -> EvolutionContext:
        online_ctx = self._online_contexts.get(skill_name)
        if online_ctx is not None:
            return online_ctx

        raise build_error(
            StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
            error_msg=(
                "online_contexts missing entry for skill "
                f"{skill_name}; SkillExperienceOptimizer requires EvolutionContext"
            ),
        )

    def _step(self) -> Dict[tuple[str, str], Any]:
        updates: Dict[tuple[str, str], Any] = {}
        for op_id, param in self._parameters.items():
            records: List = param.get_gradient(EXPERIENCES_TARGET) or []
            if records:
                updates[(op_id, EXPERIENCES_TARGET)] = records
        return updates

    async def generate_records(self, ctx: EvolutionContext) -> List[EvolutionRecord]:
        """Generate and parse evolution records from LLM output."""
        if not ctx.signals:
            return []

        conversation_snippet = _build_conversation_snippet(ctx.messages, language=self._language)
        signals_json = json.dumps(
            [signal.to_dict() for signal in ctx.signals],
            ensure_ascii=False,
            indent=2,
        )
        desc_summary = _build_existing_summary(ctx.existing_desc_records, label="description")
        body_summary = _build_existing_summary(ctx.existing_body_records, label="body")
        skill_content = _summarize_skill_content(ctx.skill_content)
        default_existing_summary = "无已有记录" if self._language == "cn" else "No existing records"
        prompt = SKILL_EXPERIENCE_GENERATE_PROMPT[self._language].format(
            skill_content=skill_content,
            signals_json=signals_json,
            conversation_snippet=(conversation_snippet or "").strip(),
            existing_desc_summary=desc_summary or default_existing_summary,
            existing_body_summary=body_summary or default_existing_summary,
            user_query=ctx.user_query or ("无" if self._language == "cn" else "None"),
        )
        retry_prompt = SKILL_EXPERIENCE_GENERATE_PROMPT[self._language].format(
            skill_content=_summarize_skill_content(ctx.skill_content, max_chars=2500),
            signals_json=json.dumps([signal.to_dict() for signal in ctx.signals], ensure_ascii=False),
            conversation_snippet=_build_conversation_snippet(
                ctx.messages,
                max_messages=10,
                content_preview_chars=100,
                language=self._language,
            ).strip(),
            existing_desc_summary=_limit_summary_lines(desc_summary, 2)
            or ("无已有记录" if self._language == "cn" else "No existing records"),
            existing_body_summary=_limit_summary_lines(body_summary, 2)
            or ("无已有记录" if self._language == "cn" else "No existing records"),
            user_query=(ctx.user_query[:500] if ctx.user_query else ("无" if self._language == "cn" else "None")),
        )

        logger.info("[SkillExperienceOptimizer] calling LLM (skill=%s)", ctx.skill_name)
        try:
            patches = await self._generate_patches_with_retries(
                prompt=prompt,
                retry_prompt=retry_prompt,
            )
        except BaseError as exc:
            logger.error("[SkillExperienceOptimizer] LLM call failed: %s", exc)
            return []
        except ValueError:
            logger.warning("[SkillExperienceOptimizer] all retries exhausted, returning no records")
            return []
        source = ctx.signals[0].signal_type
        merged_context = _build_context(ctx.signals)
        text_records: List[EvolutionRecord] = []
        script_records: List[EvolutionRecord] = []

        for patch in patches:
            if patch.action == "skip":
                logger.info(
                    "[SkillExperienceOptimizer] LLM decided to skip (reason=%s)",
                    patch.skip_reason or "unknown",
                )
                continue
            if not patch.content.strip():
                logger.info("[SkillExperienceOptimizer] LLM returned empty content, skipping")
                continue
            is_script = patch.target == EvolutionTarget.SCRIPT
            if is_script and len(script_records) >= 1:
                continue
            if not is_script and len(text_records) >= 2:
                continue
            initial_score = INITIAL_SCORE_BY_SIGNAL.get(source, 0.6)
            record = EvolutionRecord.make(
                source=source,
                context=merged_context,
                change=patch,
                score=initial_score,
            )
            if is_script:
                script_records.append(record)
            else:
                text_records.append(record)
            logger.info(
                "[SkillExperienceOptimizer] generated record %s -> [%s] target=%s merge_target=%s",
                record.id,
                patch.section,
                patch.target.value,
                patch.merge_target,
            )
        return text_records + script_records

    async def retry_parse(
        self,
        broken_raw: str,
        original_prompt: str,
        attempt_number: int = 1,
        parse_error: str = "",
    ) -> tuple[List[EvolutionPatch] | None, str]:
        """Retry parsing: fix JSON if malformed, or regenerate if truncated.

        Args:
            broken_raw: The raw LLM output that failed parsing.
            original_prompt: The original prompt used for generation.
            attempt_number: Which retry attempt this is (2 or 3). Affects strategy.
            parse_error: Specific error message from json.loads.

        Returns:
            (patches, retry_raw) where patches is None on failure,
            [] on successful empty parse, or a non-empty list.
            retry_raw is the raw LLM output for progressive retries.
        """
        truncated = _looks_truncated(broken_raw)

        if truncated:
            if attempt_number >= 3:
                logger.warning("[SkillExperienceOptimizer] output still truncated on attempt 3, giving up")
                return None, broken_raw
            logger.warning("[SkillExperienceOptimizer] output appears truncated, retrying full regeneration")
            retry_prompt = original_prompt
        elif attempt_number >= 3:
            logger.warning(
                "[SkillExperienceOptimizer] JSON malformed (attempt %d), using strict fix prompt",
                attempt_number,
            )
            error_detail = parse_error or "无法解析为合法 JSON"
            retry_prompt = JSON_FIX_PROMPT_STRICT.format(
                parse_error=error_detail,
                broken_preview=broken_raw[:500],
            )
        else:
            logger.warning(
                "[SkillExperienceOptimizer] JSON malformed, requesting fix (preview: %s)",
                broken_raw[:200],
            )
            error_detail = parse_error or "JSON 解析失败"
            retry_prompt = JSON_FIX_PROMPT.format(parse_error=error_detail, broken_output=broken_raw)

        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": retry_prompt}],
                temperature=0.1,
                timeout=_RETRY_PARSE_TIMEOUT_SECS,
            )
            retry_raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("[SkillExperienceOptimizer] retry LLM call failed: %s", exc)
            return None, ""

        patches = _parse_llm_response(retry_raw)
        if patches is None:
            strategy = "regeneration" if truncated else ("strict_fix" if attempt_number >= 3 else "fix")
            logger.warning("[SkillExperienceOptimizer] retry (%s) also failed, giving up", strategy)
            return None, retry_raw
        logger.info("[SkillExperienceOptimizer] retry succeeded, got %d patches", len(patches))
        return patches, retry_raw

    @staticmethod
    def _parse_patches_with_error(raw: str) -> tuple[List[EvolutionPatch] | None, str]:
        data, last_error = _extract_json_with_error(raw)
        if data is None:
            return None, last_error

        items = data if isinstance(data, list) else [data]
        patches: List[EvolutionPatch] = []
        for item in items:
            if isinstance(item, dict):
                patch = _parse_single_patch(item)
                if patch is not None:
                    patches.append(patch)
        return patches, ""

    async def _generate_patches_with_retries(
        self,
        *,
        prompt: str,
        retry_prompt: str,
    ) -> List[EvolutionPatch]:
        raw, prompt_used = await invoke_text_with_retry_and_prompt(
            llm=self._llm,
            model=self._model,
            prompt=prompt,
            retry_prompt=retry_prompt,
            policy=self._generate_records_llm_policy,
        )

        patches, last_error = self._parse_patches_with_error(raw)
        if patches is not None:
            return patches

        last_raw = raw
        for attempt in range(2, 4):
            logger.warning("[SkillExperienceOptimizer] parse failed, repair attempt %d/3", attempt)
            repaired, retry_raw = await self.retry_parse(
                broken_raw=last_raw,
                original_prompt=prompt_used,
                attempt_number=attempt,
                parse_error=last_error,
            )
            if repaired is not None:
                return repaired
            if retry_raw:
                last_raw = retry_raw
                _, last_error = self._parse_patches_with_error(retry_raw)

        raise ValueError("SkillExperienceOptimizer response could not be parsed")

    def update_llm(self, llm: Model, model: str) -> None:
        """Update runtime llm/model for hot reload."""
        self._llm = llm
        self._model = model
