# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Track presented experiences and update their usage-based scores."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord, EvolutionTarget, UsageStats
from openjiuwen.agent_evolving.experience.scorer import ExperienceScorer, update_score
from openjiuwen.core.common.logging import logger


class ExperienceTracker:
    """Track presented experiences and update their scores."""

    def __init__(
        self,
        *,
        store: EvolutionStore,
        scorer: ExperienceScorer,
        eval_interval: int,
    ) -> None:
        self._store = store
        self._scorer = scorer
        self._eval_interval = eval_interval

    @staticmethod
    def get_session_presented_records(
        session: Any,
    ) -> List[tuple[str, EvolutionRecord, str]]:
        if session is None:
            return []
        return getattr(session, "_experience_tracker_presented_records", [])

    @staticmethod
    def set_session_presented_records(
        session: Any,
        records: List[tuple[str, EvolutionRecord, str]],
    ) -> None:
        if session is not None:
            setattr(session, "_experience_tracker_presented_records", records)

    @staticmethod
    def get_session_eval_counter(session: Any) -> int:
        if session is None:
            return 0
        return getattr(session, "_experience_tracker_eval_counter", 0)

    @staticmethod
    def set_session_eval_counter(session: Any, value: int) -> None:
        if session is not None:
            setattr(session, "_experience_tracker_eval_counter", value)

    async def record_presented(
        self,
        *,
        session: Any,
        skill_name: str,
        presentation_snippet: str,
    ) -> None:
        """Record BODY experiences that a non-rail presentation path displayed."""
        try:
            records = await self._store.get_records_by_score(
                skill_name,
                min_score=0.5,
            )
            if not records:
                return

            body_records = [r for r in records if self._is_body_record(r)]
            if not body_records:
                return

            updates: Dict[str, Dict[str, Any]] = {}
            now = datetime.now(tz=timezone.utc).isoformat()

            for record in body_records[:5]:
                existing_stats = record.usage_stats or UsageStats()
                new_stats = UsageStats(
                    times_presented=existing_stats.times_presented + 1,
                    times_used=existing_stats.times_used,
                    times_positive=existing_stats.times_positive,
                    times_negative=existing_stats.times_negative,
                    last_presented_at=now,
                    last_evaluated_at=existing_stats.last_evaluated_at,
                )
                updates[record.id] = {
                    "score": record.score,
                    "usage_stats": new_stats.to_dict(),
                }

            await self._store.update_record_scores(skill_name, updates)

            presented_entries: List[tuple[str, EvolutionRecord, str]] = []
            for record in body_records[:5]:
                if record.id in updates:
                    record.usage_stats = UsageStats.from_dict(updates[record.id]["usage_stats"])
                    presented_entries.append((skill_name, record, presentation_snippet))

            existing = self.get_session_presented_records(session)
            self.set_session_presented_records(session, existing + presented_entries)

            logger.debug(
                "[ExperienceTracker] tracked %d presented records for skill=%s",
                len(presented_entries),
                skill_name,
            )
        except Exception as exc:
            logger.debug("[ExperienceTracker] track presented records failed: %s", exc)

    async def record_presented_records(
        self,
        *,
        session: Any,
        skill_name: str,
        presentation_snippet: str,
        record_ids: List[str],
    ) -> None:
        """Record explicitly displayed BODY experience records."""
        if not record_ids:
            return

        try:
            evo_log = await self._store.load_full_evolution_log(skill_name)
            requested_ids = set(record_ids)
            body_records = [
                record for record in evo_log.entries if record.id in requested_ids and self._is_body_record(record)
            ]
            if not body_records:
                return

            updates: Dict[str, Dict[str, Any]] = {}
            now = datetime.now(tz=timezone.utc).isoformat()
            for record in body_records:
                existing_stats = record.usage_stats or UsageStats()
                new_stats = UsageStats(
                    times_presented=existing_stats.times_presented + 1,
                    times_used=existing_stats.times_used,
                    times_positive=existing_stats.times_positive,
                    times_negative=existing_stats.times_negative,
                    last_presented_at=now,
                    last_evaluated_at=existing_stats.last_evaluated_at,
                )
                updates[record.id] = {
                    "score": record.score,
                    "usage_stats": new_stats.to_dict(),
                }

            await self._store.update_record_scores(skill_name, updates)

            presented_entries: List[tuple[str, EvolutionRecord, str]] = []
            for record in body_records:
                if record.id in updates:
                    record.usage_stats = UsageStats.from_dict(updates[record.id]["usage_stats"])
                    presented_entries.append((skill_name, record, presentation_snippet))

            existing = self.get_session_presented_records(session)
            self.set_session_presented_records(session, existing + presented_entries)
        except Exception as exc:
            logger.debug("[ExperienceTracker] track explicit presented records failed: %s", exc)

    def consume_eval_state(
        self,
        session: Any,
    ) -> List[tuple[str, EvolutionRecord, str]]:
        """Consume records when the evaluation interval is reached."""
        counter = self.get_session_eval_counter(session)
        counter += 1
        presented_entries: List[tuple[str, EvolutionRecord, str]] = []
        if counter >= self._eval_interval:
            presented_entries = self.get_session_presented_records(session)
            self.set_session_presented_records(session, [])
            self.set_session_eval_counter(session, 0)
        else:
            self.set_session_eval_counter(session, counter)
        return presented_entries

    async def evaluate_presented(
        self,
        presented_entries: List[tuple[str, EvolutionRecord, str]],
    ) -> None:
        """Evaluate explicitly presented experiences and update their scores."""
        if not presented_entries:
            return

        try:
            by_skill_snippet: Dict[tuple[str, str], List[EvolutionRecord]] = {}
            for skill_name, record, snippet in presented_entries:
                key = (skill_name, snippet)
                by_skill_snippet.setdefault(key, []).append(record)

            for (skill_name, snippet), records in by_skill_snippet.items():
                eval_results = await self._scorer.evaluate(snippet, records)
                if not eval_results:
                    continue

                updates: Dict[str, Dict[str, Any]] = {}
                for result in eval_results:
                    record_id = result.get("record_id")
                    if not record_id:
                        continue
                    for record in records:
                        if record.id == record_id:
                            new_score = update_score(record, result)
                            if record.usage_stats is None:
                                record.usage_stats = UsageStats()
                            updates[record_id] = {
                                "score": new_score,
                                "usage_stats": record.usage_stats.to_dict(),
                            }
                            break

                if updates:
                    await self._store.update_record_scores(skill_name, updates)
                    logger.info(
                        "[ExperienceTracker] async evaluation updated %d record(s) for skill=%s",
                        len(updates),
                        skill_name,
                    )
        except Exception as exc:
            logger.warning("[ExperienceTracker] async evaluation failed: %s", exc, exc_info=True)

    @staticmethod
    def _is_body_record(record: EvolutionRecord) -> bool:
        target = getattr(record, "target", None)
        if target is None:
            target = record.change.target
        return target == EvolutionTarget.BODY


__all__ = ["ExperienceTracker"]
