# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Private record persistence helpers for ``EvolutionStore``."""

from __future__ import annotations

import copy
import json
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionLog,
    EvolutionRecord,
    EvolutionTarget,
    UsageStats,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error
from openjiuwen.core.common.logging import logger

_EVOLUTION_FILENAME = "evolutions.json"
_LANG_TO_EXT = {
    "python": "py",
    "javascript": "js",
    "typescript": "ts",
    "shell": "sh",
    "bash": "sh",
}


class StoreRecordsHelper:
    """Encapsulates evolution record CRUD and persistence details."""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def persist_script(self, skill_dir: Path, record: EvolutionRecord) -> None:
        """Write script source code to a standalone file; replace content with a reference."""
        scripts_dir = skill_dir / "evolution" / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)

        lang = record.change.script_language or "py"
        ext = _LANG_TO_EXT.get(lang, lang)
        filename = record.change.script_filename or f"{record.id}_script.{ext}"
        script_path = scripts_dir / filename

        await self._store.write_file_text(script_path, record.change.content)
        logger.info("[EvolutionStore] persisted script %s for record %s", filename, record.id)

        record.change.script_filename = filename
        record.change.content = (
            f"Script: {filename}\n"
            f"Language: {record.change.script_language or 'unknown'}\n"
            f"Purpose: {record.change.script_purpose or ''}"
        )

    async def load_full_evolution_log(self, name: str) -> EvolutionLog:
        skill_dir = self._store.resolve_skill_dir(name)
        if skill_dir is None:
            return EvolutionLog.empty(skill_id=name)
        evo_path = skill_dir / _EVOLUTION_FILENAME
        if not evo_path.exists():
            return EvolutionLog.empty(skill_id=name)
        file_content = await self._store.read_file_text(evo_path)
        if not file_content:
            return EvolutionLog.empty(skill_id=name)
        try:
            data = json.loads(file_content)
            return EvolutionLog.from_dict(data)
        except Exception as exc:
            logger.warning("[EvolutionStore] parse %s failed: %s", evo_path.name, exc)
            return EvolutionLog.empty(skill_id=name)

    async def save_evolution_log(
        self,
        name: str,
        evo_log: EvolutionLog,
        *,
        skill_dir: Optional[Path] = None,
    ) -> None:
        target_dir = skill_dir or self._store.resolve_skill_dir(name, create=True)
        if target_dir is None:
            return

        target_dir.mkdir(parents=True, exist_ok=True)
        evo_path = target_dir / _EVOLUTION_FILENAME
        expected = evo_log.to_dict()
        content = json.dumps(expected, ensure_ascii=False, indent=2)
        await self._write_file_text_atomic(evo_path, content)

        readback = await self._store.read_file_text(evo_path)
        try:
            actual = json.loads(readback)
        except Exception as exc:
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_SKILL_STORE_EXECUTION_ERROR,
                error_msg=f"failed to read back {evo_path}: invalid JSON",
                cause=exc,
            )
        if actual != expected:
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_SKILL_STORE_EXECUTION_ERROR,
                error_msg=f"failed to read back {evo_path}: content mismatch",
            )

    async def _write_file_text_atomic(self, path: Path, content: str) -> None:
        tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            await self._store.write_file_text(tmp_path, content)
            tmp_path.replace(path)
        except Exception as exc:
            with suppress(OSError):
                tmp_path.unlink()
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_SKILL_STORE_EXECUTION_ERROR,
                error_msg=f"failed to atomically write {path}: {exc}",
                cause=exc,
            )

    async def append_record_transactional(
        self,
        name: str,
        record: EvolutionRecord,
        *,
        skill_dir: Optional[Path] = None,
    ) -> Optional[EvolutionLog]:
        """Append or merge one record and roll back all related files on failure."""
        target_dir = skill_dir or self._store.resolve_skill_dir(name, create=True)
        if target_dir is None:
            return None

        target_dir.mkdir(parents=True, exist_ok=True)
        evo_path = target_dir / _EVOLUTION_FILENAME
        had_log = evo_path.exists()
        old_log_content = evo_path.read_text(encoding="utf-8") if had_log else None
        projection_backups = self._snapshot_projection_files(target_dir)

        try:
            prepared_record = copy.deepcopy(record)
            if prepared_record.change.target == EvolutionTarget.SCRIPT:
                await self.persist_script(target_dir, prepared_record)

            evo_log = await self.load_full_evolution_log(name)
            self._append_or_merge_record(evo_log, prepared_record)
            evo_log.updated_at = datetime.now(tz=timezone.utc).isoformat()

            await self.save_evolution_log(name, evo_log, skill_dir=target_dir)
            await self._store.render_evolution_markdown(name)
        except Exception:
            await self._restore_projection_files(target_dir, projection_backups)
            await self._restore_text_file(evo_path, old_log_content if had_log else None)
            raise

        if prepared_record.change.target == EvolutionTarget.SCRIPT:
            record.change.script_filename = prepared_record.change.script_filename
            record.change.content = prepared_record.change.content

        logger.info(
            "[EvolutionStore] atomically wrote record %s for skill=%s",
            record.id,
            name,
        )
        return evo_log

    @staticmethod
    def _append_or_merge_record(evo_log: EvolutionLog, record: EvolutionRecord) -> None:
        merge_target = record.change.merge_target
        if not merge_target:
            evo_log.entries.append(record)
            return

        for idx, existing in enumerate(evo_log.entries):
            if existing.id == merge_target:
                evo_log.entries[idx] = record
                logger.info(
                    "[EvolutionStore] merged record %s replacing %s",
                    record.id,
                    merge_target,
                )
                return
        evo_log.entries.append(record)

    @staticmethod
    def _snapshot_projection_files(skill_dir: Path) -> Dict[Path, str]:
        backups: Dict[Path, str] = {}
        evo_dir = skill_dir / "evolution"
        if evo_dir.exists():
            for path in evo_dir.rglob("*"):
                if path.is_file():
                    backups[path] = path.read_text(encoding="utf-8")

        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            backups[skill_md] = skill_md.read_text(encoding="utf-8")
        return backups

    @classmethod
    async def _restore_projection_files(cls, skill_dir: Path, backups: Dict[Path, str]) -> None:
        evo_dir = skill_dir / "evolution"
        cls._remove_unbacked_files(evo_dir, backups)

        for path, content in backups.items():
            await cls._restore_text_file(path, content)

        cls._remove_empty_dirs(evo_dir)

    @staticmethod
    def _remove_unbacked_files(evo_dir: Path, backups: Dict[Path, str]) -> None:
        if not evo_dir.exists():
            return
        for path in evo_dir.rglob("*"):
            if path.is_file() and path not in backups:
                path.unlink()

    @staticmethod
    def _remove_empty_dirs(root: Path) -> None:
        if not root.exists():
            return
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_dir():
                with suppress(OSError):
                    path.rmdir()
        with suppress(OSError):
            root.rmdir()

    @staticmethod
    async def _restore_text_file(path: Path, content: Optional[str]) -> None:
        if content is None:
            if path.exists():
                path.unlink()
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    async def update_record_scores(
        self,
        name: str,
        updates: Dict[str, Dict[str, Any]],
    ) -> int:
        if not updates:
            return 0

        evo_log = await self.load_full_evolution_log(name)
        updated_count = 0

        for record in evo_log.entries:
            if record.id in updates:
                update_data = updates[record.id]
                if "score" in update_data:
                    record.score = update_data["score"]
                if "usage_stats" in update_data:
                    stats_data = update_data["usage_stats"]
                    if isinstance(stats_data, dict):
                        record.usage_stats = UsageStats.from_dict(stats_data)
                    elif isinstance(stats_data, UsageStats):
                        record.usage_stats = stats_data
                updated_count += 1

        if updated_count > 0:
            evo_log.updated_at = datetime.now(tz=timezone.utc).isoformat()
            await self.save_evolution_log(name, evo_log)
            logger.info(
                "[EvolutionStore] updated %d record score(s) for skill=%s",
                updated_count,
                name,
            )

        return updated_count

    async def get_records_by_score(
        self,
        name: str,
        min_score: Optional[float] = None,
    ) -> list[EvolutionRecord]:
        evo_log = await self.load_full_evolution_log(name)
        records = evo_log.entries
        if min_score is not None:
            records = [r for r in records if r.score >= min_score]
        return sorted(records, key=lambda r: r.score, reverse=True)

    async def delete_records(self, name: str, record_ids: list[str]) -> int:
        if not record_ids:
            return 0

        evo_log = await self.load_full_evolution_log(name)
        ids_set = set(record_ids)
        original_count = len(evo_log.entries)
        evo_log.entries = [r for r in evo_log.entries if r.id not in ids_set]
        deleted_count = original_count - len(evo_log.entries)

        if deleted_count > 0:
            evo_log.updated_at = datetime.now(tz=timezone.utc).isoformat()
            await self.save_evolution_log(name, evo_log)
            await self._store.render_evolution_markdown(name)
            logger.info(
                "[EvolutionStore] deleted %d record(s) for skill=%s",
                deleted_count,
                name,
            )

        return deleted_count

    async def mark_records_applied(self, name: str, record_ids: list[str]) -> int:
        if not record_ids:
            return 0

        evo_log = await self.load_full_evolution_log(name)
        ids_set = set(record_ids)
        updated_count = 0

        for record in evo_log.entries:
            if record.id in ids_set and not record.applied:
                record.applied = True
                updated_count += 1

        if updated_count > 0:
            evo_log.updated_at = datetime.now(tz=timezone.utc).isoformat()
            await self.save_evolution_log(name, evo_log)
            await self._store.render_evolution_markdown(name)
            logger.info(
                "[EvolutionStore] marked %d record(s) as applied for skill=%s",
                updated_count,
                name,
            )

        return updated_count

    async def merge_records(
        self,
        name: str,
        primary_id: str,
        remove_ids: list[str],
        new_content: str,
        new_score: Optional[float] = None,
    ) -> Optional[EvolutionRecord]:
        evo_log = await self.load_full_evolution_log(name)
        primary_record = None
        records_to_remove = []
        all_scores = []

        for record in evo_log.entries:
            if record.id == primary_id:
                primary_record = record
            elif record.id in remove_ids:
                records_to_remove.append(record)
                all_scores.append(record.score)

        if primary_record is None:
            logger.warning(
                "[EvolutionStore] merge_records: primary record %s not found",
                primary_id,
            )
            return None

        all_scores.append(primary_record.score)
        final_score = new_score if new_score is not None else max(all_scores)

        primary_record.change.content = new_content
        primary_record.summary = None
        primary_record.score = final_score
        primary_record.timestamp = datetime.now(tz=timezone.utc).isoformat()

        for record in records_to_remove:
            evo_log.entries.remove(record)

        evo_log.updated_at = datetime.now(tz=timezone.utc).isoformat()
        await self.save_evolution_log(name, evo_log)
        await self._store.render_evolution_markdown(name)

        logger.info(
            "[EvolutionStore] merged %d record(s) into %s for skill=%s",
            len(records_to_remove),
            primary_id,
            name,
        )
        return primary_record

    async def update_record_content(
        self,
        name: str,
        record_id: str,
        new_content: str,
        new_score: Optional[float] = None,
    ) -> Optional[EvolutionRecord]:
        evo_log = await self.load_full_evolution_log(name)
        target_record = None

        for record in evo_log.entries:
            if record.id == record_id:
                target_record = record
                break

        if target_record is None:
            logger.warning(
                "[EvolutionStore] update_record_content: record %s not found",
                record_id,
            )
            return None

        target_record.change.content = new_content
        target_record.summary = None
        if new_score is not None:
            target_record.score = new_score
        target_record.timestamp = datetime.now(tz=timezone.utc).isoformat()

        evo_log.updated_at = datetime.now(tz=timezone.utc).isoformat()
        await self.save_evolution_log(name, evo_log)
        await self._store.render_evolution_markdown(name)

        logger.info(
            "[EvolutionStore] updated record %s for skill=%s",
            record_id,
            name,
        )
        return target_record
