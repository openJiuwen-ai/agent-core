# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Private projection/rendering helpers for ``EvolutionStore``."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord, EvolutionTarget
from openjiuwen.core.common.logging import logger

_EVOLUTION_INDEX_PATTERN = re.compile(
    r"<!-- evolution-index-start -->.*?<!-- evolution-index-end -->",
    re.DOTALL,
)
_MAX_INJECT_DESC = 5


class StoreProjectionHelper:
    """Encapsulates markdown projection and pending-record formatting."""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def render_evolution_markdown(self, name: str) -> None:
        skill_dir = self._store.resolve_skill_dir(name)
        if skill_dir is None:
            return

        evo_log = await self._store.load_full_evolution_log(name)
        active_entries = [r for r in evo_log.entries if not r.change.skip_reason]
        if not active_entries:
            await self.clear_rendered_outputs(skill_dir)
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
            await self.render_section_file(evo_dir, section, records)

        if script_entries:
            scripts_dir = evo_dir / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            await self.render_script_index(scripts_dir, script_entries)

        await self.update_skill_md_index(skill_dir, active_entries)
        logger.info("[EvolutionStore] rendered markdown for skill '%s' (%d entries)", name, len(active_entries))

    async def clear_rendered_outputs(self, skill_dir: Path) -> None:
        """Remove generated projection files and stale SKILL.md index blocks."""
        evo_dir = skill_dir / "evolution"
        if evo_dir.exists():
            for path in sorted(evo_dir.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass

        skill_md_path = self._store.find_skill_md(skill_dir)
        if skill_md_path is None:
            return

        content = await self._store.read_file_text(skill_md_path)
        if not content or not _EVOLUTION_INDEX_PATTERN.search(content):
            return

        cleaned = _EVOLUTION_INDEX_PATTERN.sub("", content)
        cleaned = cleaned.rstrip() + "\n"
        await self._store.write_file_text(skill_md_path, cleaned)

    async def render_section_file(
        self,
        evo_dir: Path,
        section: str,
        records: List[EvolutionRecord],
    ) -> None:
        lines = [
            f"# {section}",
            "",
            "> Auto-generated from evolutions.json. Do not edit directly.",
            "",
        ]
        for record in records:
            parts = record.change.content.split("\n", 1) if record.change.content else [""]
            lines.append(f'<a id="{record.id}"></a>')
            lines.append(f"### [{record.id}] {self._record_summary(record)}")
            if record.summary and record.change.content.strip():
                lines.append(record.change.content.rstrip())
            elif len(parts) > 1 and parts[1].strip():
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
        await self._store.write_file_text(evo_dir / filename, "\n".join(lines))

    async def render_script_index(
        self,
        scripts_dir: Path,
        entries: List[EvolutionRecord],
    ) -> None:
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
        await self._store.write_file_text(scripts_dir / "_index.md", "\n".join(lines))

    async def update_skill_md_index(
        self,
        skill_dir: Path,
        entries: List[EvolutionRecord],
    ) -> None:
        skill_md_path = self._store.find_skill_md(skill_dir)
        if skill_md_path is None:
            return

        body_count = desc_count = script_count = 0
        for record in entries:
            target = record.change.target
            if target == EvolutionTarget.BODY:
                body_count += 1
            elif target == EvolutionTarget.DESCRIPTION:
                desc_count += 1
            elif target == EvolutionTarget.SCRIPT:
                script_count += 1

        total = len(entries)
        parts = ", ".join(
            f"{v} {k}" for k, v in [("body", body_count), ("description", desc_count), ("script", script_count)] if v
        )

        narrative_entries = [record for record in entries if record.change.target != EvolutionTarget.SCRIPT]
        script_entries = [record for record in entries if record.change.target == EvolutionTarget.SCRIPT]
        experience_index_lines = self._format_experience_index_table(narrative_entries)
        script_table_lines = self._format_script_assets_table(script_entries)

        now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        index_block = "\n".join(
            [
                "<!-- evolution-index-start -->",
                "## Evolution Experiences",
                "",
                (
                    "Use this section as an index of lessons learned from previous executions. "
                    "Before applying this skill, check whether the current task matches any listed experience "
                    "summary. If it matches, read the linked detail section first and use the guidance while "
                    "planning and executing the task."
                ),
                "",
                (
                    "For narrative guidance, read the relevant `evolution/*.md#...` detail section. "
                    "For reusable helper code, first review `evolution/scripts/_index.md`, then inspect "
                    "the specific script source before adapting or running it. Scripts are implementation "
                    "aids, not mandatory steps."
                ),
                "",
                f"This skill has accumulated **{total}** evolution experiences ({parts}).",
                "",
                *experience_index_lines,
                *script_table_lines,
                f"*Last updated: {now}*",
                "<!-- evolution-index-end -->",
            ]
        )

        content = await self._store.read_file_text(skill_md_path)
        if _EVOLUTION_INDEX_PATTERN.search(content):
            content = _EVOLUTION_INDEX_PATTERN.sub(index_block, content)
        else:
            content = content.rstrip() + "\n\n" + index_block + "\n"

        await self._store.write_file_text(skill_md_path, content)

    @staticmethod
    def _section_filename(section: str) -> str:
        return section.lower().replace(" ", "_") + ".md"

    @classmethod
    def _record_summary(cls, record: EvolutionRecord) -> str:
        if record.summary:
            return cls._normalize_summary_text(record.summary)
        if record.change.target == EvolutionTarget.SCRIPT and record.change.script_purpose:
            return cls._normalize_summary_text(record.change.script_purpose)
        first_line = record.change.content.splitlines()[0] if record.change.content else ""
        return cls._normalize_summary_text(first_line) or record.id

    @staticmethod
    def _normalize_summary_text(text: str, max_chars: int = 96) -> str:
        value = text.strip()
        value = re.sub(r"^#{1,6}\s*", "", value)
        value = value.replace("|", " ")
        value = re.sub(r"\s+", " ", value).strip()
        if len(value) > max_chars:
            return value[: max_chars - 3].rstrip() + "..."
        return value

    @classmethod
    def _format_experience_index_table(cls, records: List[EvolutionRecord]) -> List[str]:
        if not records:
            return []

        ordered = sorted(records, key=lambda record: record.timestamp, reverse=True)
        ordered = sorted(ordered, key=lambda record: record.score, reverse=True)
        ordered = sorted(ordered, key=lambda record: record.change.section)
        lines = [
            "### Experience Index",
            "",
            "| Summary | Type | Score | Detail |",
            "|---------|------|-------|--------|",
        ]
        for record in ordered:
            detail_path = f"evolution/{cls._section_filename(record.change.section)}#{record.id}"
            lines.append(
                f"| {cls._record_summary(record)} | {record.change.section} | {record.score:.2f} | "
                f"[{detail_path}]({detail_path}) |"
            )
        lines.append("")
        return lines

    @classmethod
    def _format_script_assets_table(cls, records: List[EvolutionRecord]) -> List[str]:
        if not records:
            return []

        ordered = sorted(records, key=lambda record: record.timestamp, reverse=True)
        ordered = sorted(ordered, key=lambda record: record.score, reverse=True)
        lines = [
            "### Script Assets",
            "",
            "| Summary | Language | Score | Index | Source |",
            "|---------|----------|-------|-------|--------|",
        ]
        for record in ordered:
            filename = record.change.script_filename or record.id
            source = f"evolution/scripts/{filename}"
            lines.append(
                f"| {cls._record_summary(record)} | {record.change.script_language or 'unknown'} | "
                f"{record.score:.2f} | [evolution/scripts/_index.md](evolution/scripts/_index.md) | "
                f"[{source}]({source}) |"
            )
        lines.append("")
        return lines

    async def format_desc_experience_text(self, name: str, max_items: int = _MAX_INJECT_DESC) -> str:
        pending = await self._store.get_pending_records(name, EvolutionTarget.DESCRIPTION)
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
        pending = await self._store.get_pending_records(name, EvolutionTarget.BODY)
        if not pending:
            return ""
        lines = [f"\n\n# Skill '{name}' body 演进经验\n"]
        for index, record in enumerate(pending):
            lines.append(f"{index + 1}. **[{record.change.section}]** {record.change.content}")
        return "\n".join(lines)

    async def list_pending_summary(self, names: List[str]) -> str:
        lines: List[str] = []
        count = 0
        for name in names:
            desc_pending = await self._store.get_pending_records(name, EvolutionTarget.DESCRIPTION)
            body_pending = await self._store.get_pending_records(name, EvolutionTarget.BODY)
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

    @staticmethod
    def extract_description_from_skill_md(content: str) -> str:
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
