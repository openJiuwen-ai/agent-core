# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""File-system IO layer for online skill evolution data."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Union

from openjiuwen.agent_evolving.online.schema import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionLog,
    EvolutionTarget,
)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.sys_operation import SysOperation

_EVOLUTION_FILENAME = "evolutions.json"


class EvolutionStore:
    """Store and load evolution records for skill directories."""

    # ── Initialization ────────────────────────────────────────────────

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

    # ── Skill discovery / read ────────────────────────────────────────

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
                    str(path), content=content, mode="text", encoding="utf-8",
                )
                if getattr(result, "code", 0) != 0:
                    logger.warning("[EvolutionStore] failed to write %s: %s", path, result.message)
            else:
                path.write_text(content, encoding="utf-8")
        except Exception as exc:
            logger.error("[EvolutionStore] write %s failed: %s", path, exc)

    # ── Evolution log CRUD ────────────────────────────────────────────

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
                entries=[
                    record for record in evo_log.entries
                    if record.change.target == target
                ],
            )
        return evo_log

    async def append_record(self, name: str, record: EvolutionRecord) -> None:
        """Append or merge one evolution record to evolutions.json."""
        skill_dir = self._resolve_skill_dir(name, create=True)
        if skill_dir is None:
            return

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

    # ── Pending record queries ────────────────────────────────────────

    async def get_pending_records(
        self,
        name: str,
        target: Optional[EvolutionTarget] = None,
    ) -> List[EvolutionRecord]:
        return (await self.load_evolution_log(name, target)).pending_entries

    # ── Solidify / apply ──────────────────────────────────────────────

    async def solidify(self, name: str) -> int:
        """Inject pending body records into SKILL.md and mark as applied."""
        skill_dir = self._resolve_skill_dir(name)
        if skill_dir is None:
            return 0

        evo_log = await self._load_full_evolution_log(name)
        pending = [
            record
            for record in evo_log.pending_entries
            if record.change.target == EvolutionTarget.BODY
        ]
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

    # ── Formatting / display ──────────────────────────────────────────

    async def format_desc_experience_text(self, name: str) -> str:
        pending = await self.get_pending_records(name, EvolutionTarget.DESCRIPTION)
        if not pending:
            return ""
        return "\n".join(f"- {record.change.content}" for record in pending)

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
                target_tag = (
                    "description"
                    if record.change.target == EvolutionTarget.DESCRIPTION
                    else "body"
                )
                content = record.change.content
                title = content.split("\n")[0] if "\n" in content else content[:50]
                lines.append(f"   - [{target_tag}] **{title}**: ")
                if "\n" in content:
                    body_lines = content.split("\n")[1:]
                    if body_lines:
                        summary = " ".join(
                            line.strip().lstrip("- ")
                            for line in body_lines
                            if line.strip()
                        )
                        lines.append(f"    {summary[:100].replace('**', '')}")
            lines.append("")
        if not lines:
            return "当前所有 Skill 暂无演进信息。"
        return "\n".join(lines)
