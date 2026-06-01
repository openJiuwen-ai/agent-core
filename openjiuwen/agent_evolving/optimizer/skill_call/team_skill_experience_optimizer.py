# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM-based patch generation for team skill evolution."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.experience.types import EvolutionContext
from openjiuwen.agent_evolving.optimizer.base import BaseOptimizer
from openjiuwen.agent_evolving.optimizer.llm_resilience import (
    LLMInvokePolicy,
    invoke_text_with_retry,
    invoke_text_with_retry_and_prompt,
)
from openjiuwen.agent_evolving.optimizer.skill_call.experience_draft_parser import (
    ParsedExperienceDraft,
    normalize_summary,
    parse_experience_drafts_with_error,
)
from openjiuwen.agent_evolving.signal import (
    build_team_trajectory_summary,
    get_team_signal_skill_content,
    get_team_trajectory_issues,
    parse_team_model_json,
)
from openjiuwen.agent_evolving.signal.base import EvolutionSignal
from openjiuwen.agent_evolving.protocols import EXPERIENCES_TARGET
from openjiuwen.agent_evolving.trajectory import Trajectory
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model

from openjiuwen.agent_evolving.optimizer.skill_call.templates import (
    TEAM_EXPERIENCE_GENERATE_PROMPT,
    TEAM_EXPERIENCE_GENERATE_PROMPT_EN,
    TEAM_JSON_FIX_PROMPT,
    TEAM_JSON_FIX_PROMPT_STRICT,
    TRAJECTORY_PATCH_PROMPT,
    TRAJECTORY_PATCH_PROMPT_EN,
    USER_PATCH_PROMPT,
    USER_PATCH_PROMPT_EN,
)

TEAM_SKILL_RECORD_LLM_POLICY = LLMInvokePolicy(
    attempt_timeout_secs=120,
    total_budget_secs=420,
    max_attempts=3,
)

PATCH_RETRY_SKILL_CONTENT_CHARS = 3000
PATCH_RETRY_TRAJECTORY_CHARS = 6000
TRAJECTORY_ISSUES_RETRY_CHARS = 2000
USER_INTENT_RETRY_CHARS = 500
SUMMARY_RETRY_CHARS = 200
TEAM_SKILL_CONTENT_MAX_CHARS = 6000
TEAM_EVOLUTION_PREVIEW_CHARS = 200
TEAM_EVOLUTION_MAX_RECORDS = 6
TEAM_RETRY_PARSE_TIMEOUT_SECS = 20
TEAM_INITIAL_SCORE_BY_SIGNAL = {
    "trajectory_issue": 0.65,
    "user_intent": 0.70,
    "team_skill_mixed": 0.68,
}


def _parse_json(raw: str) -> Optional[Dict]:
    parsed = parse_team_model_json(raw)
    return parsed if isinstance(parsed, dict) else None


def _parse_patch_response(raw: str) -> tuple[Dict | None, str]:
    parsed = _parse_json(raw)
    if not isinstance(parsed, dict):
        return None, "response is not a JSON object"
    return parsed, ""


def _extract_json_with_error(raw: str) -> tuple[Any, str] | tuple[None, str]:
    if not raw or not raw.strip():
        return None, "empty response"
    last_error = "unknown"
    try:
        parsed = json.loads(raw)
        return parsed, ""
    except json.JSONDecodeError as exc:
        last_error = str(exc)

    cleaned = raw.strip()
    for pattern in (r"\[[\s\S]*\]", r"\{[\s\S]*\}"):
        match = re.search(pattern, cleaned)
        if not match:
            continue
        candidate = match.group(0)
        try:
            parsed = json.loads(candidate)
            return parsed, ""
        except json.JSONDecodeError as exc:
            last_error = str(exc)
    return None, last_error


def _looks_truncated(text: str) -> bool:
    opens = text.count("{") + text.count("[")
    closes = text.count("}") + text.count("]")
    return opens > closes + 1


class TeamSkillExperienceOptimizer(BaseOptimizer):
    """Formal team-skill experience optimizer implementation."""

    def __init__(
        self,
        llm: Model,
        model: str,
        language: str = "cn",
        debug_dir: Optional[str] = None,
        record_llm_policy: LLMInvokePolicy = TEAM_SKILL_RECORD_LLM_POLICY,
        evolution_store: Optional[EvolutionStore] = None,
    ) -> None:
        super().__init__()
        self._llm = llm
        self._model = model
        self._language = language
        self._debug_dir = debug_dir
        self._record_llm_policy = record_llm_policy
        self._evolution_store = evolution_store
        self._online_contexts: Dict[str, EvolutionContext] = {}

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
        trajectories = self.get_trajectories()
        default_trajectory = (
            trajectories[-1]
            if trajectories
            else Trajectory(
                execution_id="team-skill-evolution",
                session_id="team-skill-evolution",
                source="online",
                steps=[],
            )
        )

        for op_id, op in self._operators.items():
            skill_name = op_id.removeprefix("skill_experience_")
            skill_signals = [s for s in self._selected_signals if s.skill_name == skill_name or not s.skill_name]
            if not skill_signals:
                continue

            ctx = self._build_evolution_context(skill_name, op, skill_signals, default_trajectory)
            generated = await self.generate_records(ctx)

            if not generated:
                logger.info("[TeamSkillOptimizer] no records generated for skill=%s", skill_name)
                continue

            existing: List = self._parameters[op_id].get_gradient(EXPERIENCES_TARGET) or []
            self._parameters[op_id].set_gradient(EXPERIENCES_TARGET, existing + generated)
            logger.info(
                "[TeamSkillOptimizer] generated %d record(s) for skill=%s",
                len(generated),
                skill_name,
            )

    def _build_evolution_context(
        self,
        skill_name: str,
        operator: Any,
        skill_signals: List[EvolutionSignal],
        default_trajectory: Trajectory,
    ) -> EvolutionContext:
        online_ctx = self._online_contexts.get(skill_name)
        if online_ctx is not None:
            if online_ctx.trajectory is None:
                return EvolutionContext(
                    skill_name=online_ctx.skill_name,
                    signals=list(online_ctx.signals),
                    messages=list(online_ctx.messages),
                    user_query=online_ctx.user_query,
                    skill_content=online_ctx.skill_content,
                    existing_desc_records=list(online_ctx.existing_desc_records),
                    existing_body_records=list(online_ctx.existing_body_records),
                    existing_script_records=list(online_ctx.existing_script_records),
                    trajectory=default_trajectory,
                    metadata=dict(online_ctx.metadata),
                )
            return online_ctx

        raise build_error(
            StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
            error_msg=(
                "online_contexts missing entry for skill "
                f"{skill_name}; TeamSkillExperienceOptimizer requires EvolutionContext"
            ),
        )

    def _step(self) -> Dict[tuple[str, str], Any]:
        updates: Dict[tuple[str, str], Any] = {}
        for op_id, param in self._parameters.items():
            records: List = param.get_gradient(EXPERIENCES_TARGET) or []
            if records:
                updates[(op_id, EXPERIENCES_TARGET)] = records
        return updates

    @property
    def language(self) -> str:
        return self._language

    @property
    def llm(self) -> Model:
        return self._llm

    @property
    def model(self) -> str:
        return self._model

    @property
    def record_llm_policy(self) -> LLMInvokePolicy:
        return self._record_llm_policy

    def update_llm(self, llm: Model, model: str) -> None:
        self._llm = llm
        self._model = model

    async def generate_records(self, ctx: EvolutionContext) -> List[EvolutionRecord]:
        """Generate zero or more team evolution records from aggregated context."""
        if not ctx.signals:
            return []

        trajectory = ctx.trajectory or Trajectory(
            execution_id="team-skill-evolution",
            session_id="team-skill-evolution",
            source="online",
            steps=[],
        )
        if any(not hasattr(step, "kind") for step in getattr(trajectory, "steps", [])):
            generated: List[EvolutionRecord] = []
            for signal in ctx.signals:
                if signal.signal_type == "user_intent":
                    record = await self.generate_user_patch(
                        trajectory,
                        ctx.skill_name,
                        signal.excerpt or ctx.user_query,
                    )
                else:
                    record = await self.generate_trajectory_patch(
                        trajectory,
                        ctx.skill_name,
                        get_team_signal_skill_content(signal) or ctx.skill_content,
                        get_team_trajectory_issues(signal),
                    )
                if record is not None:
                    generated.append(record)
            return generated

        trajectory_summary = (
            build_team_trajectory_summary(trajectory) if getattr(trajectory, "steps", None) is not None else ""
        )
        prompt_template = TEAM_EXPERIENCE_GENERATE_PROMPT.get(self._language, TEAM_EXPERIENCE_GENERATE_PROMPT_EN)
        signals_json = json.dumps([signal.to_dict() for signal in ctx.signals], ensure_ascii=False, indent=2)
        current_skill_content = self._summarize_skill_content(ctx.skill_content)
        desc_summary = self._summarize_existing_evolutions(ctx.existing_desc_records, language=self._language)
        body_summary = self._summarize_existing_evolutions(ctx.existing_body_records, language=self._language)
        script_summary = self._summarize_existing_evolutions(ctx.existing_script_records, language=self._language)
        prompt = prompt_template.format(
            skill_content=current_skill_content or ("无" if self._language == "cn" else "None"),
            trajectory_summary=trajectory_summary
            or ("无轨迹摘要" if self._language == "cn" else "No trajectory summary"),
            signals_json=signals_json,
            existing_desc_summary=desc_summary,
            existing_body_summary=body_summary,
            existing_script_summary=script_summary,
            user_query=ctx.user_query or ("无" if self._language == "cn" else "None"),
        )
        retry_prompt = prompt_template.format(
            skill_content=self._summarize_skill_content(ctx.skill_content, max_chars=2500)
            or ("无" if self._language == "cn" else "None"),
            trajectory_summary=(
                trajectory_summary[:PATCH_RETRY_TRAJECTORY_CHARS]
                if trajectory_summary
                else ("无轨迹摘要" if self._language == "cn" else "No trajectory summary")
            ),
            signals_json=json.dumps([signal.to_dict() for signal in ctx.signals], ensure_ascii=False),
            existing_desc_summary=self._shorten_existing_evolutions_summary(desc_summary, max_records=2),
            existing_body_summary=self._shorten_existing_evolutions_summary(body_summary, max_records=2),
            existing_script_summary=self._shorten_existing_evolutions_summary(script_summary, max_records=1),
            user_query=(
                ctx.user_query[:USER_INTENT_RETRY_CHARS]
                if ctx.user_query
                else ("无" if self._language == "cn" else "None")
            ),
        )

        logger.info("[TeamSkillOptimizer] calling aggregated LLM flow (skill=%s)", ctx.skill_name)
        try:
            drafts = await self._generate_drafts_with_retries(
                prompt=prompt,
                retry_prompt=retry_prompt,
            )
        except BaseError as exc:
            logger.error("[TeamSkillOptimizer] aggregated LLM call failed: %s", exc)
            raise
        except ValueError:
            logger.warning("[TeamSkillOptimizer] all aggregated retries exhausted, returning no records")
            return []

        sources = {signal.signal_type for signal in ctx.signals}
        source = next(iter(sources)) if len(sources) == 1 else "team_skill_mixed"
        initial_score = TEAM_INITIAL_SCORE_BY_SIGNAL.get(source, 0.6)
        merged_context = self._build_context(ctx.signals)
        text_records: List[EvolutionRecord] = []
        script_records: List[EvolutionRecord] = []
        for draft in drafts:
            patch = draft.patch
            if patch.action == "skip":
                logger.info(
                    "[TeamSkillOptimizer] aggregated flow skipped record (reason=%s)",
                    patch.skip_reason or "unknown",
                )
                continue
            if not patch.content.strip():
                logger.info("[TeamSkillOptimizer] aggregated flow returned empty content, skipping")
                continue
            is_script = patch.target == EvolutionTarget.SCRIPT
            if is_script and len(script_records) >= 1:
                continue
            if not is_script and len(text_records) >= 2:
                continue
            record = EvolutionRecord.make(
                source=source,
                context=merged_context,
                change=patch,
                score=initial_score,
                summary=draft.summary,
            )
            if is_script:
                script_records.append(record)
            else:
                text_records.append(record)
            logger.info(
                "[TeamSkillOptimizer] aggregated record %s -> [%s] target=%s merge_target=%s",
                record.id,
                patch.section,
                patch.target.value,
                patch.merge_target,
            )
        return text_records + script_records

    async def generate_user_patch(
        self,
        trajectory: Trajectory,
        skill_name: str,
        user_intent: str,
    ) -> Optional[EvolutionRecord]:
        description = "team-skill"
        roles_summary = "N/A"
        workflow_summary = "N/A"

        summary = build_team_trajectory_summary(trajectory)
        if "spawn_member" in summary:
            role_mentions = re.findall(r"role[_-]?([a-z]+)", summary, re.IGNORECASE)
            if role_mentions:
                roles_summary = ", ".join(set(role_mentions[:5]))
        if "workflow" in summary.lower() or "mermaid" in summary.lower():
            workflow_summary = "Present in trajectory"
        skill_content = await self._load_skill_content(skill_name)
        existing_evolutions = await self._load_existing_evolutions_summary(skill_name)

        prompt_template = USER_PATCH_PROMPT.get(self._language, USER_PATCH_PROMPT_EN)
        prompt = prompt_template.format(
            skill_name=skill_name,
            description=description,
            roles_summary=roles_summary,
            workflow_summary=workflow_summary,
            skill_content=skill_content,
            existing_evolutions=existing_evolutions,
            user_intent=user_intent,
        )
        retry_prompt = prompt_template.format(
            skill_name=skill_name,
            description=description,
            roles_summary=roles_summary[:SUMMARY_RETRY_CHARS],
            workflow_summary=workflow_summary[:SUMMARY_RETRY_CHARS],
            skill_content=self._summarize_skill_content(skill_content, max_chars=2500),
            existing_evolutions=self._shorten_existing_evolutions_summary(existing_evolutions, max_records=2),
            user_intent=user_intent[:USER_INTENT_RETRY_CHARS],
        )

        t0 = time.time()
        try:
            raw = await self._call_llm(
                prompt,
                retry_prompt=retry_prompt,
                policy=self._record_llm_policy,
                is_result_usable=lambda text: isinstance(_parse_json(text), dict),
            )
        except Exception as exc:
            elapsed = time.time() - t0
            logger.warning("[TeamSkillOptimizer] user_patch: LLM generation failed (%.1fs): %s", elapsed, exc)
            raise
        parsed, _last_error = _parse_patch_response(raw)
        if parsed is None:
            raise ValueError("TeamSkillExperienceOptimizer response could not be parsed")
        elapsed = time.time() - t0
        if (not parsed.get("need_patch", True)) or parsed.get("action") == "skip":
            reason = parsed.get("reason", "N/A")
            logger.info("[TeamSkillOptimizer] user_patch: no patch needed, reason: %s", reason)
            return None

        section = parsed.get("section", "Instructions")
        content = parsed.get("content", "")
        if not content.strip():
            raise ValueError("TeamSkill user patch response contained empty content")

        logger.info(
            "[TeamSkillOptimizer] user_patch: section='%s', content_len=%d (%.1fs)",
            section,
            len(content),
            elapsed,
        )
        return EvolutionRecord.make(
            source="team_skill_user_patch",
            context=f"User intent: {user_intent[:200]}",
            change=EvolutionPatch(
                section=section,
                action="append",
                content=content,
                target=EvolutionTarget.BODY,
            ),
            summary=normalize_summary(parsed.get("summary")),
        )

    async def generate_trajectory_patch(
        self,
        trajectory: Trajectory,
        skill_name: str,
        current_skill_content: str,
        trajectory_issues: list[dict],
    ) -> Optional[EvolutionRecord]:
        try:
            summary = build_team_trajectory_summary(trajectory)
        except Exception as exc:
            logger.warning("[TeamSkillOptimizer] trajectory_patch: failed to build summary: %s", exc)
            return None

        issues_text = json.dumps(trajectory_issues, ensure_ascii=False, indent=2) if trajectory_issues else "N/A"
        existing_evolutions = await self._load_existing_evolutions_summary(skill_name)
        logger.info(
            "[TeamSkillOptimizer] trajectory_patch: skill='%s', summary_len=%d, content_len=%d, issues_len=%d",
            skill_name,
            len(summary),
            len(current_skill_content),
            len(issues_text),
        )

        prompt_template = TRAJECTORY_PATCH_PROMPT.get(self._language, TRAJECTORY_PATCH_PROMPT_EN)
        prompt = prompt_template.format(
            skill_content=current_skill_content[:15000],
            existing_evolutions=existing_evolutions,
            trajectory_summary=summary,
            trajectory_issues=issues_text[:5000],
        )
        retry_prompt = prompt_template.format(
            skill_content=current_skill_content[:PATCH_RETRY_SKILL_CONTENT_CHARS],
            existing_evolutions=self._shorten_existing_evolutions_summary(existing_evolutions, max_records=2),
            trajectory_summary=summary[:PATCH_RETRY_TRAJECTORY_CHARS],
            trajectory_issues=issues_text[:TRAJECTORY_ISSUES_RETRY_CHARS],
        )

        t0 = time.time()
        try:
            raw = await self._call_llm(
                prompt,
                retry_prompt=retry_prompt,
                policy=self._record_llm_policy,
                is_result_usable=lambda text: isinstance(_parse_json(text), dict),
            )
        except Exception as exc:
            elapsed = time.time() - t0
            logger.warning(
                "[TeamSkillOptimizer] trajectory_patch: LLM generation failed (%.1fs): %s",
                elapsed,
                exc,
            )
            raise
        parsed, _last_error = _parse_patch_response(raw)
        if parsed is None:
            raise ValueError("TeamSkillExperienceOptimizer response could not be parsed")
        elapsed = time.time() - t0
        if not parsed.get("need_patch"):
            reason = parsed.get("reason", "N/A")
            logger.info("[TeamSkillOptimizer] trajectory_patch: no patch needed, reason: %s", reason)
            return None

        section = parsed.get("section", "Workflow")
        content = parsed.get("content", "")
        if not content.strip():
            raise ValueError("TeamSkill trajectory patch response contained empty content")

        logger.info(
            "[TeamSkillOptimizer] trajectory_patch: section='%s', content_len=%d (%.1fs)",
            section,
            len(content),
            elapsed,
        )
        return EvolutionRecord.make(
            source="team_skill_trajectory_patch",
            context=f"Trajectory issues: {issues_text[:200]}",
            change=EvolutionPatch(
                section=section,
                action="append",
                content=content,
                target=EvolutionTarget.BODY,
            ),
            summary=normalize_summary(parsed.get("summary")),
        )

    @staticmethod
    def _build_frontmatter(name: str, description: str, roles: List[Dict]) -> str:
        lines = [
            "---",
            f"name: {name}",
            "description: |",
            f"  {description}",
            "kind: team-skill",
            "teammate_mode: build_mode",
        ]
        if roles:
            lines.append("roles:")
            for role in roles:
                role_id = role.get("id", "unknown")
                skills = json.dumps(role.get("skills", []), ensure_ascii=False)
                tools = json.dumps(role.get("tools", []), ensure_ascii=False)
                lines.append(f"  - id: {role_id}")
                lines.append(f"    skills: {skills}")
                lines.append(f"    tools: {tools}")
        lines.append("provenance:")
        lines.append("  origin: auto-generated")
        lines.append("---")
        return "\n".join(lines)

    async def _call_llm(
        self,
        prompt: str,
        *,
        retry_prompt: Optional[str] = None,
        policy: Optional[LLMInvokePolicy] = None,
        is_result_usable: Optional[Any] = None,
    ) -> str:
        logger.info("[TeamSkillOptimizer] LLM call start: model=%s, prompt_len=%d", self._model, len(prompt))
        t0 = time.time()
        try:
            if policy is None:
                response = await self._llm.invoke(
                    messages=[{"role": "user", "content": prompt}],
                    model=self._model,
                )
                if hasattr(response, "content"):
                    result = str(response.content)
                elif isinstance(response, dict):
                    result = response.get("content", "") or response.get("text", "")
                else:
                    result = str(response)
            else:
                result = await invoke_text_with_retry(
                    llm=self._llm,
                    model=self._model,
                    prompt=prompt,
                    retry_prompt=retry_prompt,
                    policy=policy,
                    is_result_usable=is_result_usable,
                )
        except Exception as exc:
            logger.error("[TeamSkillOptimizer] LLM call failed: %s", exc, exc_info=True)
            raise

        elapsed = time.time() - t0
        logger.info("[TeamSkillOptimizer] LLM call done: %.1fs, response_len=%d", elapsed, len(result))
        return result

    async def regenerate_body(
        self,
        skill_name: str,
        current_body: str,
        evolution_records: List[Any],
        user_intent: Optional[str] = None,
    ) -> Optional[str]:
        evo_summary = (
            "\n".join(
                f"- [{getattr(r, 'id', '?')}] {getattr(getattr(r, 'change', None), 'section', '?')}: "
                f"{getattr(getattr(r, 'change', None), 'content', '')[:200]}"
                for r in evolution_records[:20]
            )
            or "(no evolutions)"
        )
        intent_section = f"\n\n## 用户意图\n{user_intent}" if user_intent else ""
        prompt = (
            f"你是多角色协作 Skill 文档重写专家。请根据当前 Team Skill body 和积累的演进经验，"
            f"重新编写一份更优的 body。\n\n"
            f"## 当前 Team Skill: {skill_name}\n\n"
            f"```markdown\n{current_body[:8000]}\n```\n\n"
            f"## 积累的演进经验\n{evo_summary}"
            f"{intent_section}\n\n"
            f"## 要求\n"
            f"1. 保留 YAML frontmatter 不动（不要输出 frontmatter）\n"
            f"2. 将有价值的演进经验融入 body 正文\n"
            f"3. 保持 roles 子文件的引用结构\n"
            f"4. 精简冗余内容，保持结构清晰\n"
            f"5. 直接输出 Markdown body，不要加任何解释\n"
        )
        raw = await self._call_llm(prompt)
        body = raw.strip()
        if not body or len(body) < 50:
            return None
        return body

    def _dump_raw(self, tag: str, raw: str) -> None:
        if not self._debug_dir or not raw:
            return
        try:
            import uuid
            from datetime import datetime, timezone
            from pathlib import Path

            d = Path(self._debug_dir)
            d.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
            path = d / f"llm_raw_{tag}_{ts}_{uuid.uuid4().hex[:8]}.txt"
            path.write_text(raw, encoding="utf-8")
            logger.info("[TeamSkillOptimizer] raw response dumped to %s", path)
        except Exception as exc:
            logger.warning("[TeamSkillOptimizer] dump raw failed: %s", exc)

    async def _load_skill_content(self, skill_name: str) -> str:
        if self._evolution_store is None:
            return "N/A" if self._language == "en" else "无"
        content = await self._evolution_store.read_skill_content(skill_name)
        if not content.strip():
            return "N/A" if self._language == "en" else "无"
        return self._summarize_skill_content(content)

    async def _load_existing_evolutions_summary(self, skill_name: str) -> str:
        if self._evolution_store is None:
            return "No existing evolution records" if self._language == "en" else "无已有演进经验"
        evo_log = await self._evolution_store.load_full_evolution_log(skill_name)
        return self._summarize_existing_evolutions(evo_log.entries, language=self._language)

    @staticmethod
    def _summarize_skill_content(raw: str, max_chars: int = TEAM_SKILL_CONTENT_MAX_CHARS) -> str:
        if not raw:
            return ""
        if len(raw) <= max_chars:
            return raw
        return raw[:max_chars] + f"\n... [truncated, original {len(raw)} chars]"

    @staticmethod
    def _shorten_existing_evolutions_summary(summary: str, max_records: int = 2) -> str:
        if not summary:
            return summary
        lines = summary.splitlines()
        kept: list[str] = []
        record_count = 0
        for line in lines:
            if line.startswith("- ["):
                record_count += 1
                if record_count > max_records:
                    break
            kept.append(line)
        return "\n".join(kept).strip() or summary

    @staticmethod
    def _summarize_existing_evolutions(
        records: List[EvolutionRecord],
        *,
        language: str = "cn",
        max_records: int = TEAM_EVOLUTION_MAX_RECORDS,
        preview_chars: int = TEAM_EVOLUTION_PREVIEW_CHARS,
    ) -> str:
        active_records = [
            record for record in records if not getattr(getattr(record, "change", None), "skip_reason", None)
        ]
        if not active_records:
            return "No existing evolution records" if language == "en" else "无已有演进经验"

        header = "Existing evolution records:" if language == "en" else "已有演进经验："
        lines = [header]
        for record in active_records[-max_records:]:
            change = getattr(record, "change", None)
            section = getattr(change, "section", "?") if change else "?"
            content = getattr(change, "content", "") if change else ""
            content = re.sub(r"\s+", " ", content).strip()
            if len(content) > preview_chars:
                content = content[:preview_chars] + "..."
            lines.append(f"- [{record.id}] [{section}] {content}")
        return "\n".join(lines)

    @staticmethod
    def _build_context(signals: List[EvolutionSignal], max_chars: int = 500) -> str:
        if not signals:
            return ""
        per_signal = max(80, max_chars // len(signals))
        parts: list[str] = []
        for signal in signals:
            excerpt = signal.excerpt.strip()
            if len(excerpt) > per_signal:
                excerpt = excerpt[:per_signal] + "..."
            parts.append(f"[{signal.signal_type}] {excerpt}")
        return " | ".join(parts)

    async def retry_parse(
        self,
        broken_raw: str,
        original_prompt: str,
        attempt_number: int = 1,
        parse_error: str = "",
    ) -> tuple[List[EvolutionPatch] | None, str]:
        drafts, retry_raw = await self.retry_parse_drafts(
            broken_raw=broken_raw,
            original_prompt=original_prompt,
            attempt_number=attempt_number,
            parse_error=parse_error,
        )
        if drafts is None:
            return None, retry_raw
        return [draft.patch for draft in drafts], retry_raw

    async def retry_parse_drafts(
        self,
        broken_raw: str,
        original_prompt: str,
        attempt_number: int = 1,
        parse_error: str = "",
    ) -> tuple[List[ParsedExperienceDraft] | None, str]:
        truncated = _looks_truncated(broken_raw)
        if truncated:
            if attempt_number >= 3:
                logger.warning("[TeamSkillOptimizer] output still truncated on attempt 3, giving up")
                return None, broken_raw
            logger.warning("[TeamSkillOptimizer] output appears truncated, retrying full regeneration")
            retry_prompt = original_prompt
        elif attempt_number >= 3:
            logger.warning("[TeamSkillOptimizer] JSON malformed on attempt %d, using strict fix prompt", attempt_number)
            retry_prompt = TEAM_JSON_FIX_PROMPT_STRICT.format(
                parse_error=parse_error or "无法解析为合法 JSON",
                broken_preview=broken_raw[:500],
            )
        else:
            logger.warning("[TeamSkillOptimizer] JSON malformed, requesting fix (preview: %s)", broken_raw[:200])
            retry_prompt = TEAM_JSON_FIX_PROMPT.format(
                parse_error=parse_error or "JSON 解析失败",
                broken_output=broken_raw,
            )

        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": retry_prompt}],
                temperature=0.1,
                timeout=TEAM_RETRY_PARSE_TIMEOUT_SECS,
            )
            retry_raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("[TeamSkillOptimizer] retry LLM call failed: %s", exc)
            return None, ""

        drafts, _last_error = parse_experience_drafts_with_error(retry_raw, _extract_json_with_error)
        if drafts is None:
            strategy = "regeneration" if truncated else ("strict_fix" if attempt_number >= 3 else "fix")
            logger.warning("[TeamSkillOptimizer] retry (%s) also failed, giving up", strategy)
            return None, retry_raw
        logger.info("[TeamSkillOptimizer] retry succeeded, got %d patches", len(drafts))
        return drafts, retry_raw

    async def _generate_drafts_with_retries(
        self,
        *,
        prompt: str,
        retry_prompt: str,
    ) -> List[ParsedExperienceDraft]:
        raw, prompt_used = await invoke_text_with_retry_and_prompt(
            llm=self._llm,
            model=self._model,
            prompt=prompt,
            retry_prompt=retry_prompt,
            policy=self._record_llm_policy,
        )

        drafts, last_error = parse_experience_drafts_with_error(raw, _extract_json_with_error)
        if drafts is not None:
            return drafts

        last_raw = raw
        for attempt in range(2, 4):
            logger.warning("[TeamSkillOptimizer] aggregated parse failed, repair attempt %d/3", attempt)
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

        raise ValueError("TeamSkillExperienceOptimizer aggregated response could not be parsed")


TeamSkillOptimizer = TeamSkillExperienceOptimizer

__all__ = [
    "TeamSkillExperienceOptimizer",
    "TeamSkillOptimizer",
]
