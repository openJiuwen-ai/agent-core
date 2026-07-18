# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Online Skill experience optimizer."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionContext,
    EvolutionPatch,
    EvolutionRecord,
    EvolutionRecordSpec,
)
from openjiuwen.agent_evolving.optimizer.base import BaseOptimizer
from openjiuwen.agent_evolving.optimizer.llm_resilience import (
    LLMInvokePolicy,
    invoke_text_with_retry_and_prompt,
    response_to_text,
)
from openjiuwen.agent_evolving.optimizer.skill_call.experience_draft_parser import (
    ParsedExperienceDraft,
    parse_experience_draft,
    parse_experience_drafts_with_error,
)
from openjiuwen.agent_evolving.optimizer.skill_call.templates import (
    JSON_FIX_PROMPT,
    JSON_FIX_PROMPT_STRICT,
    SKILL_EXPERIENCE_ANALYZER_PROMPT,
    SKILL_EXPERIENCE_FORMATTER_PROMPT,
    SKILL_EXPERIENCE_GENERATE_PROMPT,
)
from openjiuwen.agent_evolving.optimizer.skill_call.tool_call_chain import build_tool_call_chain
from openjiuwen.agent_evolving.signal.base import EvolutionSignal, EvolutionTarget
from openjiuwen.agent_evolving.trajectory.types import Updates
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger

EXPERIENCES_TARGET = "experiences"
# Keep a high default max_tokens for reasoning models; ModelArts may override higher.
_OPTIMIZER_LLM_MAX_TOKENS = 8192

# Initial score mapping by signal type
INITIAL_SCORE_BY_SIGNAL = {
    "execution_failure": 0.65,
    "user_intent": 0.70,
    "user_correction": 0.70,
    "script_artifact": 0.60,
    "conversation_review": 0.50,
}

GENERATE_RECORDS_LLM_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=150,
    total_budget_secs=300,
    max_attempts=2,
)
_ANALYZER_LOG_MAX_CHARS = 500

# When the model is deployed on Huawei Cloud ModelArts MaaS, bump max_tokens
# explicitly to avoid JSON truncation.
_HUAWEI_MODELARTS_KEYWORD = "modelarts"
_HUAWEI_MODELARTS_MAX_TOKENS = 20000


def _assistant_text_from_response(response: Any) -> str:
    """Normalize assistant output via shared ``response_to_text`` helper."""
    return response_to_text(response)


def _resolve_max_tokens(llm: Any) -> int | None:
    """Return the max_tokens override for the current LLM, or None to use defaults.

    The Huawei Cloud ModelArts MaaS gateway truncates long JSON outputs more
    aggressively than other providers. We bump max_tokens explicitly when the
    configured api_base targets ModelArts. For every other provider we return
    None and let the client/model default apply.
    """
    client_config = getattr(llm, "model_client_config", None)
    api_base = getattr(client_config, "api_base", "") or ""
    if _HUAWEI_MODELARTS_KEYWORD in api_base.lower():
        return _HUAWEI_MODELARTS_MAX_TOKENS
    return None


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


def _strip_outer_markdown_fence(text: str) -> str:
    """Remove only the outermost markdown code fence, preserving inner fences in JSON strings."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    first_newline = stripped.find("\n")
    if first_newline == -1:
        return stripped

    first_line = stripped[:first_newline].strip()
    if not re.fullmatch(r"```(?:json|JSON)?", first_line):
        return stripped

    content_start = first_newline + 1
    inner = stripped[content_start:].rstrip()
    if inner.endswith("```"):
        last_newline = inner.rfind("\n")
        if last_newline != -1:
            last_line = inner[last_newline + 1:].strip()
        else:
            last_line = inner.strip()
        if last_line == "```":
            inner = inner[:last_newline] if last_newline != -1 else ""
    return inner.strip()


def _fix_json_text(text: str) -> str:
    """Apply common fixes to malformed JSON produced by LLMs."""
    text = _strip_outer_markdown_fence(text.strip())
    # Strip // comments; keep https:// and other scheme:// sequences intact.
    text = re.sub(r"(?<!:)//[^\n]*", "", text)
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
        if not isinstance(record, EvolutionRecord):
            lines.append(f"- {record}")
            continue
        prefix = f"[{label}] " if label else ""
        lines.append(f"- {prefix}[{record.id}] [{record.change.section}] {record.change.content}")
    return "\n".join(lines)


def _limit_summary_lines(summary: str, max_lines: int) -> str:
    if not summary or max_lines <= 0:
        return ""
    return "\n".join(summary.splitlines()[:max_lines])


def _parse_analyzer_response(raw: str) -> Optional[dict]:
    """Parse analyzer-stage JSON object. Returns None on failure."""
    data = _extract_json(raw)
    if not isinstance(data, dict):
        return None
    if "candidates" not in data:
        data["candidates"] = []
    if "root_causes" not in data:
        data["root_causes"] = []
    return data


def _filter_analyzer_candidates(candidates: list) -> list:
    """Keep only append candidates with non-empty content."""
    result: list = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        if item.get("action", "append") != "append":
            continue
        if not str(item.get("content", "")).strip():
            continue
        result.append(item)
    return result


def _parse_single_patch(data: dict) -> Optional[EvolutionPatch]:
    """Compatibility wrapper over the draft parser."""
    draft = parse_experience_draft(data)
    if draft is None:
        return None
    return draft.patch


def _parse_llm_response(raw: str) -> Optional[List[EvolutionPatch]]:
    """Compatibility wrapper: parse LLM JSON into EvolutionPatch list."""
    drafts, _ = parse_experience_drafts_with_error(raw, _extract_json_with_error)
    if drafts is None:
        return None
    return [draft.patch for draft in drafts]


class SkillExperienceOptimizer(BaseOptimizer):
    """Online Skill experience optimizer.

    Signals arrive from SkillEvolutionRail and are consumed through the
    optimizer's neutral signal-selection contract. _backward() groups
    selected signals by skill_name and generates EvolutionRecord(s).
    """

    domain = "skill_experience"

    def __init__(
        self,
        llm: Any,
        model: str,
        language: str = "cn",
        generate_records_llm_policy: LLMInvokePolicy = GENERATE_RECORDS_LLM_POLICY,
        *,
        two_stage: bool = True,
    ) -> None:
        super().__init__()
        self._llm = llm
        self._model = model
        self._language = language
        self._generate_records_llm_policy = generate_records_llm_policy
        self._two_stage = two_stage
        self._online_contexts: Dict[str, EvolutionContext] = {}

    @property
    def generate_records_llm_policy(self) -> LLMInvokePolicy:
        """Get the configured record generation policy."""
        return self._generate_records_llm_policy

    @property
    def record_llm_policy(self) -> LLMInvokePolicy:
        """Compatibility property for callers that label record generation as record_llm_policy."""
        return self._generate_records_llm_policy

    @property
    def llm(self) -> Any:
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
            skill_name = op_id.removeprefix("skill_experience_").removeprefix("skill_call_")
            selected = getattr(self, "_selected_signals", None) or self._bad_signals
            skill_signals = [
                s for s in selected if s.skill_name == skill_name or not s.skill_name
            ]
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

        # Backward-compatible path used by SkillEvolutionRail + SkillCallOperator.
        get_state = getattr(operator, "get_state", None)
        if callable(get_state):
            state = get_state() or {}
            return EvolutionContext(
                skill_name=skill_name,
                signals=skill_signals,
                skill_content=state.get("skill_content", ""),
                messages=state.get("messages", []),
                existing_desc_records=state.get("desc_records", []),
                existing_body_records=state.get("body_records", []),
                tool_call_chain=state.get("tool_call_chain", ""),
                user_query=state.get("user_query", ""),
            )

        raise build_error(
            StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
            error_msg=(
                "online_contexts missing entry for skill "
                f"{skill_name}; SkillExperienceOptimizer requires EvolutionContext"
            ),
        )

    def _step(self) -> Updates:
        updates: Updates = {}
        for op_id, param in self._parameters.items():
            records: List = param.get_gradient(EXPERIENCES_TARGET) or []
            if records:
                updates[(op_id, EXPERIENCES_TARGET)] = records
        return updates

    async def generate_records(self, ctx: EvolutionContext) -> List[EvolutionRecord]:
        """Generate and parse evolution records from LLM output."""
        if not ctx.signals:
            return []
        return await self._generate_regular_records(ctx)

    def _build_generation_inputs(self, ctx: EvolutionContext) -> dict:
        """Shared prompt inputs for analyzer / single-stage paths."""
        tool_call_chain = ctx.tool_call_chain or build_tool_call_chain(
            ctx.messages,
            language=self._language,
        )
        conversation_snippet = _build_conversation_snippet(ctx.messages, language=self._language)
        signals_json = json.dumps(
            [signal.to_dict() for signal in ctx.signals],
            ensure_ascii=False,
            indent=2,
        )
        desc_summary = _build_existing_summary(ctx.existing_desc_records, label="description")
        body_summary = _build_existing_summary(ctx.existing_body_records, label="body")
        return {
            "skill_content": _summarize_skill_content(ctx.skill_content),
            "signals_json": signals_json,
            "tool_call_chain": tool_call_chain.strip() or (
                "(无)" if self._language == "cn" else "(none)"
            ),
            "conversation_snippet": (conversation_snippet or "").strip(),
            "existing_desc_summary": desc_summary or self._default_existing_summary(),
            "existing_body_summary": body_summary or self._default_existing_summary(),
            "user_query": self._default_user_query(ctx.user_query),
        }

    def _build_analyzer_retry_inputs(self, ctx: EvolutionContext, inputs: dict) -> dict:
        """Shorter analyzer prompt inputs for timeout retry (mirrors single-stage retry)."""
        return {
            "skill_content": _summarize_skill_content(ctx.skill_content, max_chars=2500),
            "signals_json": json.dumps(
                [signal.to_dict() for signal in ctx.signals],
                ensure_ascii=False,
            ),
            "tool_call_chain": inputs["tool_call_chain"],
            "conversation_snippet": _build_conversation_snippet(
                ctx.messages,
                max_messages=10,
                content_preview_chars=100,
                language=self._language,
            ).strip(),
            "existing_desc_summary": _limit_summary_lines(inputs["existing_desc_summary"], 2)
            or self._default_existing_summary(),
            "existing_body_summary": _limit_summary_lines(inputs["existing_body_summary"], 2)
            or self._default_existing_summary(),
            "user_query": self._default_user_query(ctx.user_query, max_chars=500),
        }

    def _build_formatter_retry_prompt(self, analyzer_data: dict) -> str:
        """Shorter formatter prompt for timeout retry."""
        analyzer_output = json.dumps(analyzer_data, ensure_ascii=False, indent=2)
        analyzer_output = self._limit_text(analyzer_output, 4000)
        return SKILL_EXPERIENCE_FORMATTER_PROMPT[self._language].format(
            analyzer_output=analyzer_output,
        )

    async def _invoke_llm(
        self,
        prompt: str,
        *,
        retry_prompt: str | None = None,
    ) -> Optional[str]:
        try:
            raw, _ = await invoke_text_with_retry_and_prompt(
                llm=self._llm,
                model=self._model,
                prompt=prompt,
                retry_prompt=retry_prompt,
                policy=self._generate_records_llm_policy,
                max_tokens=_resolve_max_tokens(self._llm) or _OPTIMIZER_LLM_MAX_TOKENS,
            )
            return raw
        except BaseError as exc:
            logger.error("[SkillExperienceOptimizer] LLM call failed: %s", exc)
            return None
        except Exception as exc:
            logger.error("[SkillExperienceOptimizer] LLM call failed: %s", exc)
            return None

    async def _run_analyzer(
        self,
        ctx: EvolutionContext,
        inputs: dict,
    ) -> Optional[dict]:
        prompt = SKILL_EXPERIENCE_ANALYZER_PROMPT[self._language].format(**inputs)
        retry_prompt = SKILL_EXPERIENCE_ANALYZER_PROMPT[self._language].format(
            **self._build_analyzer_retry_inputs(ctx, inputs),
        )
        logger.info(
            "[SkillExperienceOptimizer] analyzer stage (skill=%s)",
            ctx.skill_name,
        )
        raw = await self._invoke_llm(prompt, retry_prompt=retry_prompt)
        if raw is None:
            return None
        data = _parse_analyzer_response(raw)
        if data is None:
            logger.warning(
                "[SkillExperienceOptimizer] analyzer parse failed (preview: %s)",
                raw[:200],
            )
            retry_raw = await self._invoke_llm(prompt, retry_prompt=retry_prompt)
            if retry_raw:
                data = _parse_analyzer_response(retry_raw)
        if data is None:
            return None
        data["candidates"] = _filter_analyzer_candidates(data.get("candidates", []))
        analyzer_preview = json.dumps(data, ensure_ascii=False)
        if len(analyzer_preview) > _ANALYZER_LOG_MAX_CHARS:
            analyzer_preview = analyzer_preview[:_ANALYZER_LOG_MAX_CHARS] + "..."
        logger.info(
            "[SkillExperienceOptimizer] analyzer data (skill=%s): %s",
            ctx.skill_name,
            analyzer_preview,
        )
        if not data["candidates"]:
            causes = data.get("root_causes", [])
            logger.info(
                "[SkillExperienceOptimizer] analyzer produced 0 candidates "
                "(root_causes=%d, skill=%s)",
                len(causes),
                ctx.skill_name,
            )
        return data

    async def _run_formatter(
        self,
        analyzer_data: dict,
        skill_name: str,
    ) -> List[ParsedExperienceDraft]:
        analyzer_output = json.dumps(analyzer_data, ensure_ascii=False, indent=2)
        prompt = SKILL_EXPERIENCE_FORMATTER_PROMPT[self._language].format(
            analyzer_output=analyzer_output,
        )
        retry_prompt = self._build_formatter_retry_prompt(analyzer_data)
        logger.info(
            "[SkillExperienceOptimizer] formatter stage (skill=%s)",
            skill_name,
        )
        raw = await self._invoke_llm(prompt, retry_prompt=retry_prompt)
        if raw is None:
            return []
        drafts, last_error = parse_experience_drafts_with_error(raw, _extract_json_with_error)
        if drafts is not None:
            return drafts

        last_raw = raw
        for attempt in range(2, 4):
            logger.warning(
                "[SkillExperienceOptimizer] formatter parse failed, repair attempt %d/3",
                attempt,
            )
            repaired, retry_raw = await self.retry_parse_drafts(
                broken_raw=last_raw,
                original_prompt=prompt,
                attempt_number=attempt,
                parse_error=last_error,
            )
            if repaired is not None:
                return repaired
            if retry_raw:
                last_raw = retry_raw
                _, last_error = parse_experience_drafts_with_error(retry_raw, _extract_json_with_error)
        return []

    async def _generate_regular_records(self, ctx: EvolutionContext) -> List[EvolutionRecord]:
        """Generate regular-profile records via two-stage or single-stage pipeline."""
        inputs = self._build_generation_inputs(ctx)

        if self._two_stage:
            logger.info(
                "[SkillExperienceOptimizer] two-stage pipeline (skill=%s, signals=%d)",
                ctx.skill_name,
                len(ctx.signals),
            )
            try:
                analyzer_data = await self._run_analyzer(ctx, inputs)
                if not analyzer_data or not analyzer_data.get("candidates"):
                    root_cause_count = len((analyzer_data or {}).get("root_causes", []))
                    logger.info(
                        "[SkillExperienceOptimizer] two-stage early exit (skill=%s): "
                        "no candidates (root_causes=%d)",
                        ctx.skill_name,
                        root_cause_count,
                    )
                    return []
                candidates = analyzer_data.get("candidates", [])
                logger.info(
                    "[SkillExperienceOptimizer] analyzer done (skill=%s): "
                    "candidates=%d, entering formatter",
                    ctx.skill_name,
                    len(candidates),
                )
                drafts = await self._run_formatter(analyzer_data, ctx.skill_name)
            except BaseError as exc:
                logger.error("[SkillExperienceOptimizer] LLM call failed: %s", exc)
                raise
        else:
            prompt = SKILL_EXPERIENCE_GENERATE_PROMPT[self._language].format(**inputs)
            retry_prompt = SKILL_EXPERIENCE_GENERATE_PROMPT[self._language].format(
                skill_content=_summarize_skill_content(ctx.skill_content, max_chars=2500),
                signals_json=json.dumps([signal.to_dict() for signal in ctx.signals], ensure_ascii=False),
                tool_call_chain=inputs["tool_call_chain"],
                conversation_snippet=_build_conversation_snippet(
                    ctx.messages,
                    max_messages=10,
                    content_preview_chars=100,
                    language=self._language,
                ).strip(),
                existing_desc_summary=_limit_summary_lines(inputs["existing_desc_summary"], 2)
                or self._default_existing_summary(),
                existing_body_summary=_limit_summary_lines(inputs["existing_body_summary"], 2)
                or self._default_existing_summary(),
                user_query=self._default_user_query(ctx.user_query, max_chars=500),
            )
            logger.info(
                "[SkillExperienceOptimizer] single-stage LLM (skill=%s)",
                ctx.skill_name,
            )
            try:
                drafts = await self._generate_drafts_with_retries(
                    prompt=prompt,
                    retry_prompt=retry_prompt,
                )
            except BaseError as exc:
                logger.error("[SkillExperienceOptimizer] LLM call failed: %s", exc)
                raise
            except ValueError:
                logger.warning("[SkillExperienceOptimizer] all retries exhausted, returning no records")
                return []

        if not drafts:
            return []
        return self._build_records_from_drafts(
            drafts,
            signals=ctx.signals,
            skip_log_message="LLM decided to skip",
            empty_log_message="LLM returned empty content, skipping",
            generated_log_prefix="",
        )

    def _default_existing_summary(self) -> str:
        return "无已有记录" if self._language == "cn" else "No existing records"

    def _default_user_query(self, user_query: str | None, max_chars: int | None = None) -> str:
        if not user_query:
            return "无" if self._language == "cn" else "None"
        if max_chars is None or len(user_query) <= max_chars:
            return user_query
        return user_query[:max_chars]

    def _build_records_from_drafts(
        self,
        drafts: List[ParsedExperienceDraft],
        *,
        signals: List[EvolutionSignal],
        skip_log_message: str,
        empty_log_message: str,
        generated_log_prefix: str,
    ) -> List[EvolutionRecord]:
        source = signals[0].signal_type
        merged_context = _build_context(signals)
        text_records: List[EvolutionRecord] = []
        script_records: List[EvolutionRecord] = []
        for draft in drafts:
            patch = draft.patch
            if patch.action == "skip":
                logger.info(
                    "[SkillExperienceOptimizer] %s (reason=%s)",
                    skip_log_message,
                    patch.skip_reason or "unknown",
                )
                continue
            if not patch.content.strip():
                logger.info("[SkillExperienceOptimizer] %s", empty_log_message)
                continue
            is_script = patch.target == EvolutionTarget.SCRIPT
            if is_script and len(script_records) >= 1:
                logger.info(
                    "[SkillExperienceOptimizer] %sskipped draft due to script limit "
                    "(kept=%d, limit=1, section=%s, summary=%r)",
                    generated_log_prefix,
                    len(script_records),
                    patch.section,
                    (draft.summary or "")[:80],
                )
                continue
            if not is_script and len(text_records) >= 2:
                logger.info(
                    "[SkillExperienceOptimizer] %sskipped draft due to text limit "
                    "(kept=%d, limit=2, section=%s, target=%s, summary=%r)",
                    generated_log_prefix,
                    len(text_records),
                    patch.section,
                    patch.target.value,
                    (draft.summary or "")[:80],
                )
                continue
            record = EvolutionRecord.make(
                EvolutionRecordSpec(
                    source=source,
                    context=merged_context,
                    change=patch,
                    score=INITIAL_SCORE_BY_SIGNAL.get(source, 0.6),
                    summary=draft.summary,
                )
            )
            if is_script:
                script_records.append(record)
            else:
                text_records.append(record)
            logger.info(
                "[SkillExperienceOptimizer] %sgenerated record %s -> [%s] target=%s merge_target=%s",
                generated_log_prefix,
                record.id,
                patch.section,
                patch.target.value,
                patch.merge_target,
            )
        return text_records + script_records

    @staticmethod
    def _limit_text(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n... [truncated, original {len(text)} chars]"

    async def retry_parse(
        self,
        broken_raw: str,
        original_prompt: str,
        attempt_number: int = 1,
        parse_error: str = "",
    ) -> List[EvolutionPatch]:
        """Retry parsing: fix JSON if malformed, or regenerate if truncated.

        Compatibility wrapper used by existing callers/tests. Returns a list of
        patches (empty on failure). Progressive draft-preserving retries are
        available via :meth:`retry_parse_drafts`.
        """
        drafts, _ = await self.retry_parse_drafts(
            broken_raw=broken_raw,
            original_prompt=original_prompt,
            attempt_number=attempt_number,
            parse_error=parse_error,
        )
        if drafts is None:
            return []
        return [draft.patch for draft in drafts]

    async def retry_parse_drafts(
        self,
        broken_raw: str,
        original_prompt: str,
        attempt_number: int = 1,
        parse_error: str = "",
    ) -> tuple[List[ParsedExperienceDraft] | None, str]:
        """Retry parsing while preserving per-record summaries."""
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
            retry_raw, _ = await invoke_text_with_retry_and_prompt(
                llm=self._llm,
                model=self._model,
                prompt=retry_prompt,
                policy=self._generate_records_llm_policy,
                temperature=0.1,
                max_tokens=_resolve_max_tokens(self._llm) or _OPTIMIZER_LLM_MAX_TOKENS,
            )
        except BaseError as exc:
            logger.error("[SkillExperienceOptimizer] retry LLM call failed: %s", exc)
            return None, ""
        except Exception as exc:
            logger.error("[SkillExperienceOptimizer] retry LLM call failed: %s", exc)
            return None, ""

        drafts, _ = parse_experience_drafts_with_error(retry_raw, _extract_json_with_error)
        if drafts is None:
            strategy = "regeneration" if truncated else ("strict_fix" if attempt_number >= 3 else "fix")
            logger.warning("[SkillExperienceOptimizer] retry (%s) also failed, giving up", strategy)
            return None, retry_raw
        logger.info("[SkillExperienceOptimizer] retry succeeded, got %d patches", len(drafts))
        return drafts, retry_raw

    async def _generate_drafts_with_retries(
        self,
        *,
        prompt: str,
        retry_prompt: str,
    ) -> List[ParsedExperienceDraft]:
        extra_kwargs: dict[str, Any] = {
            "max_tokens": _resolve_max_tokens(self._llm) or _OPTIMIZER_LLM_MAX_TOKENS,
        }
        raw, prompt_used = await invoke_text_with_retry_and_prompt(
            llm=self._llm,
            model=self._model,
            prompt=prompt,
            retry_prompt=retry_prompt,
            policy=self._generate_records_llm_policy,
            **extra_kwargs,
        )

        drafts, last_error = parse_experience_drafts_with_error(raw, _extract_json_with_error)
        if drafts is not None:
            return drafts

        last_raw = raw
        for attempt in range(2, 4):
            logger.warning("[SkillExperienceOptimizer] parse failed, repair attempt %d/3", attempt)
            repaired, retry_raw = await self.retry_parse_drafts(
                broken_raw=last_raw,
                original_prompt=prompt_used,
                attempt_number=attempt,
                parse_error=last_error,
            )
            if repaired is not None:
                return repaired
            if retry_raw:
                last_raw = retry_raw
                _, last_error = parse_experience_drafts_with_error(retry_raw, _extract_json_with_error)

        raise ValueError("SkillExperienceOptimizer response could not be parsed")

    def update_llm(self, llm: Any, model: str) -> None:
        """Update runtime llm/model for hot reload."""
        self._llm = llm
        self._model = model
