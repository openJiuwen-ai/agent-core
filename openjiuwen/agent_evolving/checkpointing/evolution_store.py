# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""File-system IO layer for online skill evolution data."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionLog,
    EvolutionTarget,
    UsageStats,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.sys_operation import SysOperation

_EVOLUTION_FILENAME = "evolutions.json"
_TOTAL_WARNING_THRESHOLD = 30
_MAX_INJECT_DESC = 5
_INDEX_TOP_N = 3
_LANG_TO_EXT = {
    "python": "py",
    "javascript": "js",
    "typescript": "ts",
    "shell": "sh",
    "bash": "sh",
}
_EVOLUTION_INDEX_PATTERN = re.compile(
    r"<!-- evolution-index-start -->.*?<!-- evolution-index-end -->",
    re.DOTALL,
)


class EvolutionStore:
    """Store and load evolution records for skill directories."""

    def __init__(self, skills_base_dir: Union[str, List[str]]) -> None:
        self._base_dirs: List[Path] = self._normalize_base_dirs(skills_base_dir)
        if not self._base_dirs:
            raise ValueError("skills_base_dir is empty")
        self.sys_operation: Optional[SysOperation] = None

    @property
    def base_dirs(self) -> List[Path]:
        return list(self._base_dirs)

    @property
    def base_dir(self) -> Path:
        """Compatibility property: return first configured base dir."""
        return self._base_dirs[0]

    @classmethod
    def _normalize_base_dirs(cls, skills_base_dir: Union[str, List[str]]) -> List[Path]:
        if isinstance(skills_base_dir, str):
            parsed = cls._parse_base_dirs(skills_base_dir)
            raw_dirs = parsed if parsed else ([skills_base_dir.strip()] if skills_base_dir.strip() else [])
        else:
            raw_dirs: List[str] = []
            for item in skills_base_dir:
                if not isinstance(item, str):
                    continue
                parsed = cls._parse_base_dirs(item)
                if parsed:
                    raw_dirs.extend(parsed)
                elif item.strip():
                    raw_dirs.append(item.strip())

        normalized: List[Path] = []
        seen: set[str] = set()
        for raw_dir in raw_dirs:
            resolved = str(Path(raw_dir).expanduser().resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            normalized.append(Path(resolved))
        return normalized

    @staticmethod
    def _parse_base_dirs(raw: str) -> List[str]:
        text = raw.strip()
        if not text:
            return []
        normalized = text.replace(",", ";")
        return [item.strip() for item in normalized.split(";") if item.strip()]

    def list_skill_names(self) -> List[str]:
        """List all skill names under configured base directories."""
        names: List[str] = []
        seen: set[str] = set()
        for root in self._base_dirs:
            if not root.exists() or not root.is_dir():
                continue
            for item in sorted(root.iterdir(), key=lambda path: path.name):
                if not item.is_dir() or item.name.startswith("_"):
                    continue
                if item.name in seen:
                    continue
                seen.add(item.name)
                names.append(item.name)
        return names

    def skill_exists(self, name: str) -> bool:
        return self._resolve_skill_dir(name) is not None

    async def read_skill_content(self, name: str) -> str:
        """Read SKILL.md content for one skill."""
        skill_dir = self._resolve_skill_dir(name)
        if skill_dir is None:
            return ""
        md_path = self._find_skill_md(skill_dir)
        if md_path is None:
            return ""
        return await self._read_file_text(md_path)

    def _resolve_skill_dir(self, name: str, create: bool = False) -> Optional[Path]:
        candidates = [base / name for base in self._base_dirs]
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        if create and self._base_dirs:
            return self._base_dirs[0] / name
        return None

    @staticmethod
    def _find_skill_md(skill_dir: Path) -> Optional[Path]:
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            return skill_md
        md_files = list(skill_dir.glob("*.md"))
        return md_files[0] if md_files else None

    async def _read_file_text(self, path: Path) -> str:
        """Read a text file, routing through sys_operation when available."""
        try:
            if self.sys_operation is not None:
                result = await self.sys_operation.fs().read_file(str(path), mode="text", encoding="utf-8")
                if getattr(result, "code", 0) == 0:
                    data = getattr(result, "data", None)
                    content = getattr(data, "content", None) if data is not None else None
                    if content is None:
                        return ""
                    return content if isinstance(content, str) else str(content)
                else:
                    logger.warning("[EvolutionStore] failed to read %s: %s", path, result.message)
                    return ""
            return path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("[EvolutionStore] failed to read %s: %s", path, exc)
            return ""

    async def _write_file_text(self, path: Path, content: str) -> None:
        """Write a text file, routing through sys_operation when available."""
        try:
            if self.sys_operation is not None:
                result = await self.sys_operation.fs().write_file(
                    str(path),
                    content=content,
                    mode="text",
                    encoding="utf-8",
                    prepend_newline=False
                )
                if getattr(result, "code", 0) != 0:
                    logger.warning("[EvolutionStore] failed to write %s: %s", path, result.message)
            else:
                path.write_text(content, encoding="utf-8")
        except Exception as exc:
            logger.error("[EvolutionStore] write %s failed: %s", path, exc)

    async def write_skill_content(self, name: str, content: str) -> bool:
        """Write full SKILL.md content for a skill.

        This is the public API for SkillRewriter to write rewritten content.

        Args:
            name: Skill name
            content: Complete SKILL.md content to write

        Returns:
            True on success, False on failure
        """
        skill_dir = self._resolve_skill_dir(name)
        if skill_dir is None:
            logger.warning("[EvolutionStore] write_skill_content: skill '%s' not found", name)
            return False

        skill_md_path = self._find_skill_md(skill_dir)
        if skill_md_path is None:
            # Try default path
            skill_md_path = skill_dir / "SKILL.md"

        try:
            await self._write_file_text(skill_md_path, content)
            logger.info("[EvolutionStore] wrote SKILL.md for skill='%s'", name)
            return True
        except Exception as exc:
            logger.error("[EvolutionStore] write_skill_content failed for '%s': %s", name, exc)
            return False

    async def load_evolution_log(
        self,
        name: str,
        target: Optional[EvolutionTarget] = None,
    ) -> EvolutionLog:
        """Load evolution log for one skill; optionally filter by target."""
        evo_log = await self._load_full_evolution_log(name)
        if target is not None:
            evo_log = EvolutionLog(
                skill_id=evo_log.skill_id,
                version=evo_log.version,
                updated_at=evo_log.updated_at,
                entries=[record for record in evo_log.entries if record.change.target == target],
            )
        return evo_log

    async def append_record(self, name: str, record: EvolutionRecord) -> None:
        """Append or merge one evolution record to evolutions.json."""
        skill_dir = self._resolve_skill_dir(name, create=True)
        if skill_dir is None:
            return

        if record.change.target == EvolutionTarget.SCRIPT:
            await self._persist_script(skill_dir, record)

        evo_log = await self._load_full_evolution_log(name)
        merge_target = record.change.merge_target
        if merge_target:
            replaced = False
            for idx, existing in enumerate(evo_log.entries):
                if existing.id == merge_target:
                    evo_log.entries[idx] = record
                    replaced = True
                    logger.info(
                        "[EvolutionStore] merged record %s replacing %s",
                        record.id,
                        merge_target,
                    )
                    break
            if not replaced:
                evo_log.entries.append(record)
        else:
            evo_log.entries.append(record)

        evo_log.updated_at = datetime.now(tz=timezone.utc).isoformat()
        await self._save_evolution_log(name, evo_log, skill_dir=skill_dir)
        logger.info(
            "[EvolutionStore] wrote %s/%s (id=%s, target=%s)",
            name,
            _EVOLUTION_FILENAME,
            record.id,
            record.change.target.value,
        )

        total = len(evo_log.entries)
        if total >= _TOTAL_WARNING_THRESHOLD:
            logger.warning(
                "[EvolutionStore] skill '%s' has %d experiences, consider /evolve_simplify",
                name,
                total,
            )

        await self.render_evolution_markdown(name)

    async def _load_full_evolution_log(self, name: str) -> EvolutionLog:
        skill_dir = self._resolve_skill_dir(name)
        if skill_dir is None:
            return EvolutionLog.empty(skill_id=name)
        evo_path = skill_dir / _EVOLUTION_FILENAME
        if not evo_path.exists():
            return EvolutionLog.empty(skill_id=name)
        file_content = await self._read_file_text(evo_path)
        if not file_content:
            return EvolutionLog.empty(skill_id=name)
        try:
            data = json.loads(file_content)
            return EvolutionLog.from_dict(data)
        except Exception as exc:
            logger.warning("[EvolutionStore] parse %s failed: %s", evo_path.name, exc)
            return EvolutionLog.empty(skill_id=name)

    async def _save_evolution_log(
        self,
        name: str,
        evo_log: EvolutionLog,
        skill_dir: Optional[Path] = None,
    ) -> None:
        target_dir = skill_dir or self._resolve_skill_dir(name, create=True)
        if target_dir is None:
            return

        target_dir.mkdir(parents=True, exist_ok=True)
        evo_path = target_dir / _EVOLUTION_FILENAME
        await self._write_file_text(evo_path, json.dumps(evo_log.to_dict(), ensure_ascii=False, indent=2))

    async def get_pending_records(
        self,
        name: str,
        target: Optional[EvolutionTarget] = None,
    ) -> List[EvolutionRecord]:
        return (await self.load_evolution_log(name, target)).pending_entries

    async def solidify(self, name: str) -> int:
        """Inject pending body records into SKILL.md and mark as applied."""
        skill_dir = self._resolve_skill_dir(name)
        if skill_dir is None:
            return 0

        evo_log = await self._load_full_evolution_log(name)
        pending = [record for record in evo_log.pending_entries if record.change.target == EvolutionTarget.BODY]
        if not pending:
            return 0

        skill_md_path = self._find_skill_md(skill_dir)
        if skill_md_path is None:
            logger.warning("[EvolutionStore] solidify: SKILL.md not found (skill=%s)", name)
            return 0

        content = await self._read_file_text(skill_md_path)
        for record in pending:
            content = self._inject_section(content, record.change)
            record.applied = True

        await self._write_file_text(skill_md_path, content)
        evo_log.updated_at = datetime.now(tz=timezone.utc).isoformat()
        await self._save_evolution_log(name, evo_log, skill_dir=skill_dir)
        logger.info("[EvolutionStore] solidified %d body records (skill=%s)", len(pending), name)
        return len(pending)

    @staticmethod
    def _inject_section(content: str, patch: EvolutionPatch) -> str:
        section = patch.section
        addition = f"\n{patch.content}\n"
        header_pattern = re.compile(
            rf"(## {re.escape(section)}.*?)(\n## |\Z)",
            re.DOTALL,
        )
        matched = header_pattern.search(content)
        if matched:
            insert_pos = matched.start(2)
            return content[:insert_pos] + addition + content[insert_pos:]
        return content.rstrip() + f"\n\n## {section}\n{patch.content}\n"

    async def _persist_script(self, skill_dir: Path, record: EvolutionRecord) -> None:
        """Write script source code to a standalone file; replace content with a reference."""
        scripts_dir = skill_dir / "evolution" / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)

        lang = record.change.script_language or "py"
        ext = _LANG_TO_EXT.get(lang, lang)
        filename = record.change.script_filename or f"{record.id}_script.{ext}"
        script_path = scripts_dir / filename

        await self._write_file_text(script_path, record.change.content)
        logger.info("[EvolutionStore] persisted script %s for record %s", filename, record.id)

        record.change.script_filename = filename
        record.change.content = (
            f"Script: {filename}\n"
            f"Language: {record.change.script_language or 'unknown'}\n"
            f"Purpose: {record.change.script_purpose or ''}"
        )

    async def render_evolution_markdown(self, name: str) -> None:
        """Render all evolution entries to human-readable Markdown files."""
        skill_dir = self._resolve_skill_dir(name)
        if skill_dir is None:
            return

        evo_log = await self._load_full_evolution_log(name)
        active_entries = [r for r in evo_log.entries if not r.change.skip_reason]
        if not active_entries:
            return

        evo_dir = skill_dir / "evolution"
        evo_dir.mkdir(parents=True, exist_ok=True)

        section_groups: Dict[str, List[EvolutionRecord]] = {}
        script_entries: List[EvolutionRecord] = []
        for record in active_entries:
            if record.change.target == EvolutionTarget.SCRIPT:
                script_entries.append(record)
            else:
                section_groups.setdefault(record.change.section, []).append(record)

        for section, records in section_groups.items():
            await self._render_section_file(evo_dir, section, records)

        if script_entries:
            scripts_dir = evo_dir / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            await self._render_script_index(scripts_dir, script_entries)

        await self._update_skill_md_index(skill_dir, active_entries)
        logger.info("[EvolutionStore] rendered markdown for skill '%s' (%d entries)", name, len(active_entries))

    async def _render_section_file(
        self,
        evo_dir: Path,
        section: str,
        records: List[EvolutionRecord],
    ) -> None:
        """Write ``evolution/{section_lower}.md`` with all entries for *section*."""
        lines = [
            f"# {section}",
            "",
            "> Auto-generated from evolutions.json. Do not edit directly.",
            "",
        ]
        for record in records:
            parts = record.change.content.split("\n", 1) if record.change.content else [""]
            lines.append(f"### [{record.id}] {parts[0]}")
            if len(parts) > 1 and parts[1].strip():
                lines.append(parts[1].rstrip())
            applied_tag = " | applied" if record.applied else ""
            lines.extend(
                [
                    "",
                    f"*Source: {record.source} | {record.timestamp}{applied_tag}*",
                    "",
                    "---",
                    "",
                ]
            )

        filename = section.lower().replace(" ", "_") + ".md"
        await self._write_file_text(evo_dir / filename, "\n".join(lines))

    async def _render_script_index(
        self,
        scripts_dir: Path,
        entries: List[EvolutionRecord],
    ) -> None:
        """Write ``evolution/scripts/_index.md`` summarising all persisted scripts."""
        lines = [
            "# Script Index",
            "",
            "> Auto-generated from evolutions.json. Do not edit directly.",
            "",
            "| File | Language | Purpose | Source |",
            "|------|----------|---------|--------|",
        ]
        for record in entries:
            fname = record.change.script_filename or record.id
            lang = record.change.script_language or "unknown"
            purpose = record.change.script_purpose or ""
            date = record.timestamp[:10] if len(record.timestamp) >= 10 else record.timestamp
            lines.append(f"| [{fname}]({fname}) | {lang} | {purpose} | {date} |")
        lines.append("")
        await self._write_file_text(scripts_dir / "_index.md", "\n".join(lines))

    async def _update_skill_md_index(
        self,
        skill_dir: Path,
        entries: List[EvolutionRecord],
    ) -> None:
        """Insert or replace the evolution index block at the end of SKILL.md."""
        skill_md_path = self._find_skill_md(skill_dir)
        if skill_md_path is None:
            return

        body_count = desc_count = script_count = 0
        section_counts: Dict[str, int] = {}
        for record in entries:
            target = record.change.target
            if target == EvolutionTarget.BODY:
                body_count += 1
            elif target == EvolutionTarget.DESCRIPTION:
                desc_count += 1
            elif target == EvolutionTarget.SCRIPT:
                script_count += 1
            if target != EvolutionTarget.SCRIPT:
                section_counts[record.change.section] = section_counts.get(record.change.section, 0) + 1

        total = len(entries)
        parts = ", ".join(
            f"{v} {k}" for k, v in [("body", body_count), ("description", desc_count), ("script", script_count)] if v
        )

        # Top N high-score experiences
        scored = sorted(
            [e for e in entries if e.score >= 0.5],
            key=lambda e: e.score,
            reverse=True,
        )
        top = scored[:_INDEX_TOP_N]
        top_n_lines: List[str] = []
        if top:
            top_n_lines.append("### Top Experiences")
            top_n_lines.append("")
            for record in top:
                content_preview = record.change.content.split("\n")[0][:80]
                top_n_lines.append(f"- [{record.id}] (score={record.score:.2f}) {content_preview}")
            top_n_lines.append("")

        table_lines: List[str] = []
        for section, cnt in sorted(section_counts.items()):
            filename = section.lower().replace(" ", "_") + ".md"
            table_lines.append(f"| {section} | {cnt} | [→ evolution/{filename}](evolution/{filename}) |")
        if script_count:
            table_lines.append(
                f"| Scripts | {script_count} | [→ evolution/scripts/_index.md](evolution/scripts/_index.md) |"
            )

        now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        index_block = "\n".join(
            [
                "<!-- evolution-index-start -->",
                "## Evolution Experiences",
                "",
                f"This skill has accumulated **{total}** evolution experiences ({parts}).",
                "",
                *top_n_lines,
                "| Type | Count | Details |",
                "|------|-------|---------|",
                *table_lines,
                "",
                f"*Last updated: {now}*",
                "<!-- evolution-index-end -->",
            ]
        )

        content = await self._read_file_text(skill_md_path)
        if _EVOLUTION_INDEX_PATTERN.search(content):
            content = _EVOLUTION_INDEX_PATTERN.sub(index_block, content)
        else:
            content = content.rstrip() + "\n\n" + index_block + "\n"

        await self._write_file_text(skill_md_path, content)

    async def format_desc_experience_text(self, name: str, max_items: int = _MAX_INJECT_DESC) -> str:
        pending = await self.get_pending_records(name, EvolutionTarget.DESCRIPTION)
        if not pending:
            return ""
        pending.sort(key=lambda r: r.score, reverse=True)
        return "\n".join(f"- {record.change.content}" for record in pending[:max_items])

    async def format_all_desc_experiences(self, names: List[str]) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for name in names:
            text = await self.format_desc_experience_text(name)
            if text:
                result[name] = text
        return result

    async def format_body_experience_text(self, name: str) -> str:
        pending = await self.get_pending_records(name, EvolutionTarget.BODY)
        if not pending:
            return ""
        lines = [f"\n\n# Skill '{name}' body 演进经验\n"]
        for index, record in enumerate(pending):
            lines.append(f"{index + 1}. **[{record.change.section}]** {record.change.content}")
        return "\n".join(lines)

    async def list_pending_summary(self, names: List[str]) -> str:
        """Build summary text for all pending experiences."""
        lines: List[str] = []
        count = 0
        for name in names:
            desc_pending = await self.get_pending_records(name, EvolutionTarget.DESCRIPTION)
            body_pending = await self.get_pending_records(name, EvolutionTarget.BODY)
            all_pending = desc_pending + body_pending
            if not all_pending:
                continue

            count += 1
            lines.append(
                f"{count}. **{name}** - 共 {len(all_pending)} 条 pending 经验"
                f"（description: {len(desc_pending)}, body: {len(body_pending)}）"
            )
            for record in all_pending:
                target_tag = "description" if record.change.target == EvolutionTarget.DESCRIPTION else "body"
                content = record.change.content
                title = content.split("\n")[0] if "\n" in content else content[:50]
                lines.append(f"   - [{target_tag}] **{title}**: ")
                if "\n" in content:
                    body_lines = content.split("\n")[1:]
                    if body_lines:
                        summary = " ".join(line.strip().lstrip("- ") for line in body_lines if line.strip())
                        lines.append(f"    {summary[:100].replace('**', '')}")
            lines.append("")
        if not lines:
            return "当前所有 Skill 暂无演进信息。"
        return "\n".join(lines)

    async def update_record_scores(
        self,
        name: str,
        updates: Dict[str, Dict[str, Any]],
    ) -> int:
        """Batch update score and usage_stats for records.

        Args:
            name: Skill name
            updates: Dict mapping record_id to {"score": float, "usage_stats": dict}

        Returns:
            Number of records updated
        """
        if not updates:
            return 0

        evo_log = await self._load_full_evolution_log(name)
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
            await self._save_evolution_log(name, evo_log)
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
    ) -> List[EvolutionRecord]:
        """Return records sorted by score (descending).

        Args:
            name: Skill name
            min_score: Optional minimum score filter

        Returns:
            List of records sorted by score descending
        """
        evo_log = await self._load_full_evolution_log(name)
        records = evo_log.entries
        if min_score is not None:
            records = [r for r in records if r.score >= min_score]
        return sorted(records, key=lambda r: r.score, reverse=True)

    async def delete_records(self, name: str, record_ids: List[str]) -> int:
        """Delete specified records from evolution log.

        Args:
            name: Skill name
            record_ids: List of record IDs to delete

        Returns:
            Number of records deleted
        """
        if not record_ids:
            return 0

        evo_log = await self._load_full_evolution_log(name)
        ids_set = set(record_ids)
        original_count = len(evo_log.entries)
        evo_log.entries = [r for r in evo_log.entries if r.id not in ids_set]
        deleted_count = original_count - len(evo_log.entries)

        if deleted_count > 0:
            evo_log.updated_at = datetime.now(tz=timezone.utc).isoformat()
            await self._save_evolution_log(name, evo_log)
            await self.render_evolution_markdown(name)
            logger.info(
                "[EvolutionStore] deleted %d record(s) for skill=%s",
                deleted_count,
                name,
            )

        return deleted_count

    async def merge_records(
        self,
        name: str,
        primary_id: str,
        remove_ids: List[str],
        new_content: str,
        new_score: Optional[float] = None,
    ) -> Optional[EvolutionRecord]:
        """Merge multiple records into one.

        Args:
            name: Skill name
            primary_id: ID of the primary record to keep
            remove_ids: IDs of records to merge and remove
            new_content: New merged content
            new_score: Optional new score (defaults to max of merged records)

        Returns:
            The updated primary record, or None if not found
        """
        evo_log = await self._load_full_evolution_log(name)
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
        primary_record.score = final_score
        primary_record.timestamp = datetime.now(tz=timezone.utc).isoformat()

        for record in records_to_remove:
            evo_log.entries.remove(record)

        evo_log.updated_at = datetime.now(tz=timezone.utc).isoformat()
        await self._save_evolution_log(name, evo_log)
        await self.render_evolution_markdown(name)

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
        """Update content and optionally score of a single record.

        Args:
            name: Skill name
            record_id: ID of record to update
            new_content: New content
            new_score: Optional new score

        Returns:
            The updated record, or None if not found
        """
        evo_log = await self._load_full_evolution_log(name)
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
        if new_score is not None:
            target_record.score = new_score
        target_record.timestamp = datetime.now(tz=timezone.utc).isoformat()

        evo_log.updated_at = datetime.now(tz=timezone.utc).isoformat()
        await self._save_evolution_log(name, evo_log)
        await self.render_evolution_markdown(name)

        logger.info(
            "[EvolutionStore] updated record %s for skill=%s",
            record_id,
            name,
        )
        return target_record

    async def create_skill(
        self,
        name: str,
        description: str,
        body: str,
    ) -> Optional[Path]:
        """Create a new skill directory with SKILL.md and empty evolutions.json.

        Args:
            name: Skill name (directory name)
            description: Skill description for YAML front-matter
            body: Skill body content (instructions, examples, etc.)

        Returns:
            Path to the created skill directory, or None on failure
        """
        # Validate skill name to prevent path traversal
        if not name or not re.match(r"^[a-zA-Z0-9_-]+$", name):
            logger.error("[EvolutionStore] create_skill: invalid name %r", name)
            return None
        if ".." in name or "/" in name or "\\" in name:
            logger.error("[EvolutionStore] create_skill: path traversal attempt in name %r", name)
            return None

        skill_dir = self._resolve_skill_dir(name, create=True)
        if skill_dir is None:
            logger.error("[EvolutionStore] create_skill: cannot resolve skill dir for %s", name)
            return None

        # Refuse to overwrite an existing skill to prevent data loss.
        if skill_dir.exists():
            logger.error(
                "[EvolutionStore] create_skill: skill '%s' already exists at %s; "
                "use update operations instead of create",
                name,
                skill_dir,
            )
            return None

        skill_dir.mkdir(parents=True, exist_ok=True)

        # Create SKILL.md with YAML front-matter
        skill_md_content = f"""---
name: {name}
description: {description}
---

# {name}

{body}
"""
        skill_md_path = skill_dir / "SKILL.md"
        await self._write_file_text(skill_md_path, skill_md_content)

        # Create empty evolutions.json
        empty_log = EvolutionLog.empty(skill_id=name)
        await self._save_evolution_log(name, empty_log, skill_dir=skill_dir)

        # Create evolution/ subdirectory
        evo_dir = skill_dir / "evolution"
        evo_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "[EvolutionStore] created new skill '%s' at %s",
            name,
            skill_dir,
        )
        return skill_dir

    async def list_skill_names_with_descriptions(self) -> List[Tuple[str, str]]:
        """List all skills with their descriptions.

        Returns:
            List of (skill_name, description) tuples
        """
        result: List[Tuple[str, str]] = []
        for name in self.list_skill_names():
            content = await self.read_skill_content(name)
            description = self._extract_description_from_skill_md(content)
            result.append((name, description))
        return result

    @staticmethod
    def _extract_description_from_skill_md(content: str) -> str:
        """Extract description from SKILL.md YAML front-matter."""
        if not content.startswith("---"):
            return ""
        parts = content.split("---", 2)
        if len(parts) < 3:
            return ""
        front_matter = parts[1]
        for line in front_matter.strip().split("\n"):
            if line.startswith("description:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
        return ""
