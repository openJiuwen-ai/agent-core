# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cross-user experience sharing integration for SkillEvolutionRail."""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord, EvolutionTarget
from openjiuwen.agent_evolving.experience.types import ExperienceApprovalRequest
from openjiuwen.agent_evolving.optimizer.skill_call.experience_optimizer import (
    GENERATE_RECORDS_LLM_POLICY,
)
from openjiuwen.agent_evolving.sharing import (
    ExperienceSharer,
    KeywordExtractor,
    LocalFileBackend,
    ShareStager,
    StagingResult,
)
from openjiuwen.agent_evolving.signal import EvolutionSignal
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

if TYPE_CHECKING:
    from openjiuwen.harness.rails.evolution.skill_evolution_rail import SkillEvolutionRail

_SHARED_RECORD_CONTEXT_MARKER = "[shared origin="
_DEFAULT_SHARING_MAX_UPLOAD_RETRIES = 3
_DEFAULT_SHARING_DOWNLOAD_TOP_K = 3


class SkillEvolutionSharingMixin:
    """Mixin providing opt-in cross-user experience sharing for SkillEvolutionRail."""

    _experience_sharer: Optional[ExperienceSharer]
    _keyword_extractor: Optional[KeywordExtractor]
    _share_stager: Optional[ShareStager]
    _sharing_download_top_k: int
    _excerpt_offsets: Dict[str, int]
    _language: str
    _auto_save: bool

    def _init_sharing(
        self,
        sharing_config: Optional[Dict[str, Any]],
        *,
        llm: Model,
        model: str,
        language: str,
        evolution_store: Any,
    ) -> None:
        self._experience_sharer = self._build_experience_sharer(sharing_config)
        self._sharing_download_top_k = self._resolve_download_top_k(sharing_config)
        self._excerpt_offsets = {}
        if self._experience_sharer is None:
            self._keyword_extractor = None
            self._share_stager = None
        else:
            self._keyword_extractor = KeywordExtractor(llm=llm, model=model, language=language)
            self._share_stager = ShareStager(
                keyword_extractor=self._keyword_extractor,
                sharer=self._experience_sharer,
            )
            self._experience_sharer.set_skill_sharing_context_provider(
                self._make_sharing_context_provider(evolution_store)
            )

    @staticmethod
    def _make_sharing_context_provider(evolution_store: Any):
        async def _provider(skill_name: str):
            skill_id = await evolution_store.ensure_skill_id(skill_name)
            package_bytes = await evolution_store.pack_skill_for_sharing(skill_name)
            content = await evolution_store.read_pristine_skill_content(skill_name)
            description = evolution_store.extract_description_from_skill_md(content)
            return skill_id, package_bytes, skill_name, description

        return _provider

    @staticmethod
    def _resolve_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return False

    @staticmethod
    def _resolve_download_top_k(sharing_config: Optional[Dict[str, Any]]) -> int:
        config = sharing_config or {}
        raw_top_k = config.get("download_top_k", _DEFAULT_SHARING_DOWNLOAD_TOP_K)
        try:
            top_k = int(raw_top_k)
        except (TypeError, ValueError):
            top_k = _DEFAULT_SHARING_DOWNLOAD_TOP_K
        return max(top_k, 1)

    @classmethod
    def _build_experience_sharer(
        cls,
        sharing_config: Optional[Dict[str, Any]],
    ) -> Optional[ExperienceSharer]:
        config = sharing_config or {}
        env_enabled = os.getenv("EVOLUTION_SHARING_ENABLED")
        enabled = (
            cls._resolve_bool(env_enabled)
            if env_enabled is not None
            else cls._resolve_bool(config.get("enabled", False))
        )
        if not enabled:
            return None

        backend_name = str(config.get("backend", "local_file") or "local_file").lower()
        if backend_name != "local_file":
            logger.warning(
                "[SkillEvolutionRail] unsupported sharing backend=%s, falling back to local_file",
                backend_name,
            )

        hub_path = os.getenv("EVOLUTION_SHARING_HUB_PATH") or str(config.get("hub_path") or "").strip() or None
        local_cache_dir = str(config.get("local_cache_dir") or "").strip() or None
        raw_retries = config.get("max_upload_retries", _DEFAULT_SHARING_MAX_UPLOAD_RETRIES)
        try:
            max_upload_retries = max(int(raw_retries), 1)
        except (TypeError, ValueError):
            max_upload_retries = _DEFAULT_SHARING_MAX_UPLOAD_RETRIES

        try:
            backend = LocalFileBackend(hub_path=hub_path)
            return ExperienceSharer(
                backend=backend,
                local_cache_dir=local_cache_dir,
                max_upload_retries=max_upload_retries,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[SkillEvolutionRail] failed to build ExperienceSharer (%s); sharing disabled",
                exc,
            )
            return None

    @property
    def experience_sharer(self) -> Optional[ExperienceSharer]:
        return getattr(self, "_experience_sharer", None)

    @property
    def share_stager(self) -> Optional[ShareStager]:
        return getattr(self, "_share_stager", None)

    @property
    def keyword_extractor(self) -> Optional[KeywordExtractor]:
        return getattr(self, "_keyword_extractor", None)

    @property
    def is_sharing_enabled(self) -> bool:
        return self._experience_sharer is not None and self._share_stager is not None

    def _get_excerpt_offset_by_key(self, key: str) -> int:
        if not key:
            return 0
        return self._excerpt_offsets.get(key, 0)

    def _set_excerpt_offset_by_key(self, key: str, offset: int) -> None:
        if key:
            self._excerpt_offsets[key] = offset

    @staticmethod
    def _get_excerpt_key_from_ctx(ctx: Optional[AgentCallbackContext]) -> str:
        if ctx is None:
            return ""
        conversation_id = ctx.inputs.conversation_id if ctx.inputs else ""
        if conversation_id:
            return conversation_id
        session = ctx.session if hasattr(ctx, "session") else None
        if session is not None:
            session_id = getattr(session, "session_id", "")
            if session_id:
                return session_id
        return ""

    def _resolve_incremental_messages(
        self,
        messages: List[dict],
        ctx: Optional[AgentCallbackContext],
        snapshot: Optional[dict],
    ) -> Optional[List[dict]]:
        if snapshot is not None:
            incremental = snapshot.get("incremental_messages")
            return incremental if incremental is not None else messages
        if ctx is not None:
            excerpt_key = self._get_excerpt_key_from_ctx(ctx)
            prev_offset = self._get_excerpt_offset_by_key(excerpt_key)
            incremental_messages = messages[prev_offset:]
            self._set_excerpt_offset_by_key(excerpt_key, len(messages))
            return incremental_messages
        return messages

    async def _evolve_skill_with_sharing(
        self: "SkillEvolutionRail",
        *,
        skill_name: str,
        skill_signals: List[EvolutionSignal],
        messages: List[dict],
        ctx: Optional[AgentCallbackContext],
        shared_records: List[EvolutionRecord],
    ) -> bool:
        if shared_records:
            shared_records = await self._filter_duplicate_shared_records(skill_name, shared_records)
        if not shared_records:
            request = await self._handle_evolution_from_signals(
                skill_name=skill_name,
                signals=skill_signals,
                messages=messages,
                ctx=ctx,
                requires_approval=not self._auto_save,
            )
            return request is not None

        if self._auto_save:
            for record in shared_records:
                await self._evolution_store.append_record(skill_name, record)
            logger.info(
                "[SkillEvolutionRail] persisted %d shared record(s) for skill=%s",
                len(shared_records),
                skill_name,
            )
            await self._handle_evolution_from_signals(
                skill_name=skill_name,
                signals=skill_signals,
                messages=messages,
                ctx=ctx,
                requires_approval=False,
            )
            return True

        await self._emit_shared_records_approval(
            ctx=ctx,
            skill_name=skill_name,
            records=shared_records,
            messages=messages,
        )
        return True

    async def _emit_shared_records_approval(
        self: "SkillEvolutionRail",
        *,
        ctx: Optional[AgentCallbackContext],
        skill_name: str,
        records: List[EvolutionRecord],
        messages: Optional[List[dict]],
    ) -> None:
        if not records:
            return
        request = self._manager.stage_records(
            skill_name,
            records,
            source="experience_sharing",
            messages=messages,
            is_shared_records=True,
        )
        await self._emit_generated_records(ctx, skill_name, request)

    async def _stage_records_for_share(
        self,
        *,
        skill_name: str,
        messages: Optional[List[dict]],
        records: List[EvolutionRecord],
    ) -> Optional[StagingResult]:
        if not self.is_sharing_enabled or not records or self._share_stager is None:
            return None
        try:
            return await self._share_stager.screen_and_stage(
                skill_name=skill_name,
                records=records,
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[SkillEvolutionRail] share staging failed for skill=%s: %s",
                skill_name,
                exc,
            )
            return None

    async def _flush_share_uploads(self, skill_name: str) -> None:
        if self._experience_sharer is None or not self._experience_sharer.has_pending(skill_name):
            return
        try:
            result = await self._experience_sharer.flush_pending_uploads(skill_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[SkillEvolutionRail] flush_pending_uploads failed for skill=%s: %s",
                skill_name,
                exc,
            )
            return
        if not result.ok and result.reason:
            logger.warning(
                "[SkillEvolutionRail] share upload rejected for skill=%s: %s",
                skill_name,
                result.reason,
            )

    async def _upload_approved_records_for_sharing(
        self,
        pending: Any,
        request_id: str,
    ) -> None:
        if not self.is_sharing_enabled or self._share_stager is None:
            return
        try:
            await self._stage_records_for_share(
                skill_name=pending.skill_name,
                messages=getattr(pending, "messages", None),
                records=list(pending.payload),
            )
            await self._flush_share_uploads(pending.skill_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[SkillEvolutionRail] approve share staging failed for skill=%s (request=%s): %s",
                pending.skill_name,
                request_id,
                exc,
            )

    async def _sharing_after_auto_approved(
        self: "SkillEvolutionRail",
        *,
        skill_name: str,
        staged_request: ExperienceApprovalRequest,
    ) -> None:
        pending = staged_request.pending_change
        records = list(pending.payload) if pending is not None else []
        if not records:
            return
        messages = getattr(pending, "messages", None) if pending is not None else None
        await self._stage_records_for_share(
            skill_name=skill_name,
            messages=messages,
            records=records,
        )
        await self._flush_share_uploads(skill_name)

    async def _download_shared_experiences(
        self,
        parsed_messages: List[dict],
        involved_skills: List[str],
        *,
        incremental_messages: Optional[List[dict]] = None,
    ) -> Dict[str, List[EvolutionRecord]]:
        if not self.is_sharing_enabled or self._keyword_extractor is None:
            return {}
        if not involved_skills:
            return {}

        messages_for_excerpt = incremental_messages if incremental_messages is not None else parsed_messages
        excerpt = self._extract_conversation_excerpt(messages_for_excerpt)
        if not excerpt:
            return {}

        try:
            query = await self._keyword_extractor.extract_query_keywords(feedback_excerpt=excerpt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SkillEvolutionRail] keyword extraction failed: %s", exc)
            return {}

        if not query.keywords:
            return {}

        result: Dict[str, List[EvolutionRecord]] = {}
        for skill_name in involved_skills:
            if self._experience_sharer is None:
                continue
            try:
                skill_id = await self._experience_sharer.resolve_skill_id(skill_name)
                if not skill_id:
                    logger.debug(
                        "[SkillEvolutionRail] skip shared download for skill=%s: skill_id unavailable",
                        skill_name,
                    )
                    continue
                bundles = await self._experience_sharer.download_relevant(
                    skill_id=skill_id,
                    query=query,
                    top_k=self._sharing_download_top_k,
                    skill_name=skill_name,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[SkillEvolutionRail] download_relevant failed for skill=%s: %s",
                    skill_name,
                    exc,
                )
                continue

            records: List[EvolutionRecord] = []
            for bundle in bundles or []:
                for shared_exp in bundle.experiences:
                    marker = f"\n[shared origin={bundle.bundle_id} skill_id={bundle.skill_id}]"
                    shared_exp.record.context = (shared_exp.record.context or "") + marker
                    records.append(shared_exp.record)
            if records:
                result[skill_name] = records
        return result

    async def _filter_duplicate_shared_records(
        self: "SkillEvolutionRail",
        skill_name: str,
        shared_records: List[EvolutionRecord],
    ) -> List[EvolutionRecord]:
        if not shared_records:
            return []

        existing_desc = await self._evolution_store.get_pending_records(skill_name, EvolutionTarget.DESCRIPTION)
        existing_body = await self._evolution_store.get_pending_records(skill_name, EvolutionTarget.BODY)
        existing_records = existing_desc + existing_body
        if not existing_records:
            return shared_records
        return await self._llm_check_duplicates(skill_name, shared_records, existing_records)

    async def _llm_check_duplicates(
        self: "SkillEvolutionRail",
        skill_name: str,
        shared_records: List[EvolutionRecord],
        existing_records: List[EvolutionRecord],
    ) -> List[EvolutionRecord]:
        llm = getattr(self._evolver, "llm", None)
        model = getattr(self._evolver, "model", None)
        if llm is None or not model:
            return shared_records

        prompt = self._build_duplicate_check_prompt(skill_name, shared_records, existing_records)
        try:
            from openjiuwen.agent_evolving.optimizer.llm_resilience import (
                invoke_text_with_retry_and_prompt,
            )

            raw_response, _ = await invoke_text_with_retry_and_prompt(
                llm=llm,
                model=model,
                prompt=prompt,
                retry_prompt=prompt,
                policy=GENERATE_RECORDS_LLM_POLICY,
            )
            return self._parse_duplicate_check_response(shared_records, raw_response)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[SkillEvolutionRail] LLM duplicate check failed for skill=%s: %s",
                skill_name,
                exc,
            )
            return shared_records

    def _build_duplicate_check_prompt(
        self,
        skill_name: str,
        shared_records: List[EvolutionRecord],
        existing_records: List[EvolutionRecord],
    ) -> str:
        del skill_name
        existing_summary = [
            f"- [{record.id}] [{record.change.target.value}] [{record.change.section}]\n  {record.change.content[:500]}"
            for record in existing_records
        ]
        shared_summary = [
            f"- [{record.id}] [{record.change.target.value}] [{record.change.section}]\n  {record.change.content[:500]}"
            for record in shared_records
        ]
        if (self._language or "cn") == "cn":
            return (
                "你是一个经验去重专家。判断下载的共享经验是否与本地已有经验重复。\n\n"
                f"## 本地已有经验\n{chr(10).join(existing_summary)}\n\n"
                f"## 下载的共享经验\n{chr(10).join(shared_summary)}\n\n"
                '输出 JSON 数组：[{"record_id": "...", "decision": "keep|duplicate", "reason": "..."}]'
            )
        return (
            "You are an experience deduplication expert.\n\n"
            f"## Existing Local Experiences\n{chr(10).join(existing_summary)}\n\n"
            f"## Downloaded Shared Experiences\n{chr(10).join(shared_summary)}\n\n"
            'Output JSON array: [{"record_id": "...", "decision": "keep|duplicate", "reason": "..."}]'
        )

    @staticmethod
    def _parse_duplicate_check_response(
        shared_records: List[EvolutionRecord],
        raw_response: str,
    ) -> List[EvolutionRecord]:
        json_match = re.search(r"\[\s*\{.*?\}\s*\]", raw_response, re.DOTALL)
        if not json_match:
            return shared_records
        try:
            decisions = json.loads(json_match.group())
        except json.JSONDecodeError:
            return shared_records

        decision_map = {
            str(item.get("record_id", "")): str(item.get("decision", "keep"))
            for item in decisions
            if isinstance(item, dict)
        }
        return [record for record in shared_records if decision_map.get(record.id, "keep") != "duplicate"]

    _FAILURE_KEYWORDS = re.compile(
        r"error(?!\s*=\s*None)|exception|traceback|failed|failure|timeout|timed out"
        r"|errno|connectionerror|oserror|valueerror|typeerror"
        r"|错误|异常|失败|超时"
        r"|no such file|permission denied|access denied"
        r"|command not found|not recognized"
        r"|module not found"
        r"|econnrefused|econnreset|enoent|enotfound"
        r"|npm err!",
        re.IGNORECASE,
    )

    @classmethod
    def _extract_conversation_excerpt(
        cls,
        messages: List[dict],
        max_chars: int = 400,
    ) -> str:
        user_queries: list[str] = []
        failed_tool_results: list[str] = []
        tool_calls_summary: list[str] = []
        assistant_responses: list[str] = []
        tool_call_id_to_name: dict[str, str] = {}

        for msg in messages:
            role = msg.get("role", "")
            if role == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    user_queries.append(content[:max_chars])
            elif role == "assistant":
                for tc in msg.get("tool_calls", []) or []:
                    tc_id = tc.get("id", "")
                    tc_name = tc.get("name", "")
                    if tc_id and tc_name:
                        tool_call_id_to_name[tc_id] = tc_name
                    if tc_name:
                        tool_calls_summary.append(f"[Tool: {tc_name}]")
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    assistant_responses.append(content[:max_chars])
            elif role in ("tool", "function"):
                content_str = str(msg.get("content", "") or "")
                tool_name = msg.get("name") or msg.get("tool_name") or ""
                tool_call_id = msg.get("tool_call_id", "")
                if not tool_name and tool_call_id:
                    tool_name = tool_call_id_to_name.get(tool_call_id, "")
                if cls._FAILURE_KEYWORDS.search(content_str) and content_str.strip():
                    prefix = f"[ERROR in {tool_name}]: " if tool_name else "[ERROR]: "
                    failed_tool_results.append(prefix + content_str[: max_chars * 2])

        priority_parts: list[str] = []
        if user_queries:
            priority_parts.append("=== USER QUERIES ===")
            priority_parts.extend(user_queries[:3])
        if failed_tool_results:
            priority_parts.append("\n=== FAILED TOOL EXECUTIONS ===")
            priority_parts.extend(failed_tool_results[:5])
        if tool_calls_summary:
            priority_parts.append("\n=== TOOL CALLS ===")
            priority_parts.extend(tool_calls_summary[:10])
        return "\n".join(priority_parts)[: max_chars * 3]


__all__ = ["SkillEvolutionSharingMixin"]
