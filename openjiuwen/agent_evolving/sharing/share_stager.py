# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ShareStager: screens and stages experiences for cross-user sharing.

Boundary discipline: this component never writes ``evolutions.json``;
``screen_and_stage()`` only decides whether each record enters the
*share pool* via local QC, wraps passing records, and pushes them into
the sharer's upload queue.  Rejection by the share-side QC has no effect
on local persistence — the caller (``SkillEvolutionRail``) is
responsible for persisting records through its existing auto-save /
approval path.

Duplicate detection and other hub-side acceptance rules are enforced
when the sharer uploads to the backend, not here.
"""

from __future__ import annotations

import copy
from typing import List, Optional, Tuple

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.agent_evolving.sharing.experience_sharer import ExperienceSharer
from openjiuwen.agent_evolving.sharing.keyword_extractor import KeywordExtractor
from openjiuwen.agent_evolving.sharing.types import (
    SharedExperience,
    SharingMeta,
    StagingResult,
)
from openjiuwen.agent_evolving.signal.from_conv import _FAILURE_KEYWORDS
from openjiuwen.core.common.logging import logger


def _messages_has_successful_tool(messages: Optional[List[dict]]) -> bool:
    """Return True iff messages contain any successful tool result.

    The QC ``infeasible`` gate is conservative: a record whose source is
    ``execution_failure`` is dropped from the share pool only when there
    is no successful tool execution at all in the conversation.  We don't
    try to match by tool_name because the recovery often happens through
    a different tool (read_file -> bash, etc.).
    """
    if not messages:
        return False
    for msg in messages:
        if msg.get("role") not in ("tool", "function"):
            continue
        content = str(msg.get("content", "") or "")
        if not content.strip():
            continue
        if _FAILURE_KEYWORDS.search(content):
            continue
        return True
    return False


class ShareStager:
    """Screen, wrap and stage experiences for sharing.

    Args:
        keyword_extractor: Used to lift keywords/summary out of
            patches parsed from the optimizer output.
        sharer: Receives the post-QC experiences via ``stage_for_upload``.
        qc_score_threshold: Records below this score are dropped from the
            share pool (still flow through to local persistence).
        source_user_id: Optional identifier for the uploading user.
    """

    def __init__(
        self,
        keyword_extractor: KeywordExtractor,
        sharer: ExperienceSharer,
        qc_score_threshold: float = 0.6,
        source_user_id: Optional[str] = None,
    ) -> None:
        self._keyword_extractor = keyword_extractor
        self._sharer = sharer
        self._qc_score_threshold = qc_score_threshold
        self._source_user_id = source_user_id

    @property
    def qc_score_threshold(self) -> float:
        return self._qc_score_threshold

    async def screen_and_stage(
        self,
        skill_name: str,
        records: List[EvolutionRecord],
        messages: Optional[List[dict]] = None,
    ) -> StagingResult:
        """Run QC → wrap → stage pipeline for sharing.

        This method does **not** write ``evolutions.json`` and does **not**
        upload.  It only screens records through quality gates, wraps
        passing ones as :class:`SharedExperience`, and pushes them into
        the sharer's pending-upload queue.
        """
        if not records:
            return StagingResult.empty()

        staged: List[SharedExperience] = []
        dropped: List[Tuple[EvolutionRecord, str]] = []

        for record in records:
            keywords, summary = KeywordExtractor.parse_from_optimizer_output(record.change)
            drop_reason = self._qc(record, messages)
            if drop_reason is not None:
                dropped.append((record, drop_reason))
                logger.info(
                    "[ShareStager] share-QC dropped record=%s reason=%s skill=%s",
                    record.id,
                    drop_reason,
                    skill_name,
                )
                continue
            wrapped = self._wrap(
                record=record,
                keywords=keywords,
                summary=summary,
                skill_name=skill_name,
            )
            self._sharer.stage_for_upload(skill_name, wrapped)
            staged.append(wrapped)

        return StagingResult(
            staged_for_share=staged,
            dropped_for_share=dropped,
        )

    def _qc(
        self,
        record: EvolutionRecord,
        messages: Optional[List[dict]],
    ) -> Optional[str]:
        """Return a drop_reason string, or ``None`` when the record passes QC."""
        if record.source == "execution_failure" and not _messages_has_successful_tool(messages):
            return "execution failure without successful follow-up tool call"

        if (record.score or 0.0) < self._qc_score_threshold:
            return f"score {(record.score or 0.0):.2f} below threshold {self._qc_score_threshold:.2f}"

        return None

    def _wrap(
        self,
        record: EvolutionRecord,
        keywords: List[str],
        summary: str,
        skill_name: str,
    ) -> SharedExperience:
        meta = SharingMeta(
            skill_name=skill_name,
            skill_version=record.skill_version or "",
            upload_trigger="user_approval",
            source_user_id=self._source_user_id,
            confidence=float(record.score or 0.0),
        )
        return SharedExperience(
            record=copy.deepcopy(record),
            keywords=list(keywords),
            summary=summary,
            sharing_meta=meta,
        )


__all__ = ["ShareStager"]
