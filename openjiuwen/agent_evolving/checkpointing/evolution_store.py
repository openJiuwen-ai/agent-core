# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""File-system IO layer for online skill evolution data."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from openjiuwen.agent_evolving.checkpointing.skill_package import (
    ensure_skill_id_in_content,
    pack_skill_directory,
    read_skill_id_from_content,
    unpack_skill_package,
)
from openjiuwen.agent_evolving.checkpointing.store_archive import StoreArchiveHelper
from openjiuwen.agent_evolving.checkpointing.store_projection import StoreProjectionHelper
from openjiuwen.agent_evolving.checkpointing.store_records import StoreRecordsHelper
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionLog,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, raise_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.sys_operation import SysOperation

_EVOLUTION_FILENAME = "evolutions.json"
_TOTAL_WARNING_THRESHOLD = 30
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
        self._skill_semantic_locks: Dict[str, asyncio.Lock] = {}
        self._records = StoreRecordsHelper(self)
        self._projection = StoreProjectionHelper(self)
        self._archive = StoreArchiveHelper(self)

    def _get_skill_lock(self, skill_name: str) -> asyncio.Lock:
        """Get or create a lock for skill-level Read-Modify-Write operations.

        Uses dict.setdefault to guarantee atomicity.
        Avoids TOCTOU race: two coroutines simultaneously checking
        if a key exists and each creating a new Lock.
        """
        return self._skill_semantic_locks.setdefault(skill_name, asyncio.Lock())

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
        return self.resolve_skill_dir(name) is not None

    def skill_definition_exists(self, name: str) -> bool:
        """Return True only when the skill directory contains ``SKILL.md``."""
        skill_dir = self.resolve_skill_dir(name)
        return skill_dir is not None and (skill_dir / "SKILL.md").is_file()

    async def read_skill_content(self, name: str, *, strict: bool = False) -> str:
        """Read SKILL.md content for one skill."""
        skill_dir = self.resolve_skill_dir(name)
        if skill_dir is None:
            if strict:
                raise_error(
                    StatusCode.TOOLCHAIN_EVOLVING_SKILL_DEFINITION_NOT_FOUND,
                    error_msg=f"skill '{name}' does not exist",
                )
            return ""
        md_path = skill_dir / "SKILL.md" if strict else self._find_skill_md(skill_dir)
        if strict and not md_path.is_file():
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_SKILL_DEFINITION_NOT_FOUND,
                error_msg=f"skill '{name}' is missing SKILL.md",
            )
        if md_path is None:
            return ""
        return await self.read_file_text(md_path)

    async def read_pristine_skill_content(self, name: str) -> str:
        """Read SKILL.md without the auto-injected evolution-index block.

        Used by the cross-user sharing path so the hub persists the author's
        original content, not the locally rendered evolution summary index.
        """
        content = await self.read_skill_content(name)
        if not content:
            return ""
        stripped = _EVOLUTION_INDEX_PATTERN.sub("", content)
        return stripped.rstrip() + "\n"

    async def read_skill_id(self, name: str) -> str:
        """Return ``skill_id`` from SKILL.md frontmatter without modifying the file."""
        content = await self.read_skill_content(name)
        if not content:
            return ""
        return read_skill_id_from_content(content)

    async def ensure_skill_id(self, name: str) -> str:
        """Ensure SKILL.md has a ``skill_id`` in frontmatter; persist when newly assigned."""
        skill_dir = self.resolve_skill_dir(name)
        if skill_dir is None:
            return ""
        md_path = self._find_skill_md(skill_dir)
        if md_path is None:
            return ""
        content = await self.read_file_text(md_path)
        if not content:
            return ""
        updated, skill_id = ensure_skill_id_in_content(content)
        if updated != content:
            await self.write_file_text(md_path, updated)
            logger.info("[EvolutionStore] assigned skill_id=%s for skill=%s", skill_id, name)
        return skill_id

    async def pack_skill_for_sharing(self, name: str) -> bytes:
        """Build a tarball of the skill directory for hub upload."""
        skill_dir = self.resolve_skill_dir(name)
        if skill_dir is None:
            return b""
        md_path = self._find_skill_md(skill_dir)
        if md_path is None:
            return pack_skill_directory(skill_dir)
        pristine = await self.read_pristine_skill_content(name)
        if not pristine:
            return pack_skill_directory(skill_dir)
        relpath = str(md_path.relative_to(skill_dir)).replace("\\", "/")
        return pack_skill_directory(
            skill_dir,
            skill_md_relpath=relpath,
            skill_md_content=pristine,
        )

    async def install_skill_package(
        self,
        package_bytes: bytes,
        *,
        skill_name: Optional[str] = None,
    ) -> Optional[Path]:
        """Extract a hub skill package into the local skills directory."""
        if not package_bytes:
            return None

        import io
        import tarfile

        buffer = io.BytesIO(package_bytes)
        with tarfile.open(fileobj=buffer, mode="r:gz") as archive:
            members = archive.getmembers()
            top_level_names = {
                member.name.split("/")[0] for member in members if member.name and not member.name.startswith("/")
            }
            resolved_name = (skill_name or "").strip()
            if not resolved_name:
                if len(top_level_names) == 1:
                    resolved_name = next(iter(top_level_names))
                else:
                    for member in members:
                        if member.name.endswith("SKILL.md"):
                            resolved_name = member.name.split("/")[0]
                            break
            if not resolved_name:
                logger.warning("[EvolutionStore] install_skill_package: cannot infer skill name")
                return None

            dest_dir = self.resolve_skill_dir(resolved_name, create=True)
            if dest_dir is None:
                return None
            if dest_dir.exists() and any(dest_dir.iterdir()):
                logger.warning(
                    "[EvolutionStore] install_skill_package: skill directory already exists: %s",
                    dest_dir,
                )
                return None

            unpack_skill_package(package_bytes, dest_dir)
            logger.info("[EvolutionStore] installed skill package to %s", dest_dir)
            return dest_dir

    def resolve_skill_dir(self, name: str, create: bool = False) -> Optional[Path]:
        candidates = [base / name for base in self._base_dirs]
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        if create and self._base_dirs:
            return self._base_dirs[0] / name
        return None

    def find_skill_md(self, skill_dir: Path) -> Optional[Path]:
        """Return the markdown file used as the skill entrypoint."""
        return self._find_skill_md(skill_dir)

    @staticmethod
    def _find_skill_md(skill_dir: Path) -> Optional[Path]:
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            return skill_md
        md_files = list(skill_dir.glob("*.md"))
        return md_files[0] if md_files else None

    async def read_file_text(self, path: Path) -> str:
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

    async def write_file_text(self, path: Path, content: str) -> None:
        """Write a text file, routing through sys_operation when available."""
        try:
            if self.sys_operation is not None:
                result = await self.sys_operation.fs().write_file(
                    str(path), content=content, mode="text", encoding="utf-8", prepend_newline=False
                )
                if getattr(result, "code", 0) != 0:
                    message = getattr(result, "message", "")
                    raise_error(
                        StatusCode.TOOLCHAIN_EVOLVING_SKILL_STORE_EXECUTION_ERROR,
                        error_msg=f"failed to write {path}: {message}",
                    )
            else:
                path.write_text(content, encoding="utf-8")
        except BaseError:
            raise
        except Exception as exc:
            logger.error("[EvolutionStore] write %s failed: %s", path, exc)
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_SKILL_STORE_EXECUTION_ERROR,
                error_msg=f"failed to write {path}: {exc}",
                cause=exc,
            )

    async def write_skill_content(self, name: str, content: str) -> bool:
        """Write full SKILL.md content for a skill.

        Args:
            name: Skill name
            content: Complete SKILL.md content to write

        Returns:
            True on success, False on failure
        """
        skill_dir = self.resolve_skill_dir(name)
        if skill_dir is None:
            logger.warning("[EvolutionStore] write_skill_content: skill '%s' not found", name)
            return False

        skill_md_path = self._find_skill_md(skill_dir)
        if skill_md_path is None:
            # Try default path
            skill_md_path = skill_dir / "SKILL.md"

        try:
            await self.write_file_text(skill_md_path, content)
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
        evo_log = await self.load_full_evolution_log(name)
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
        async with self._get_skill_lock(name):
            evo_log = await self._records.append_record_transactional(name, record)
            if evo_log is None:
                return
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

    async def load_full_evolution_log(self, name: str) -> EvolutionLog:
        return await self._records.load_full_evolution_log(name)

    async def save_evolution_log(
        self,
        name: str,
        evo_log: EvolutionLog,
        *,
        skill_dir: Optional[Path] = None,
    ) -> None:
        """Persist one evolution log through the public store facade."""
        await self._records.save_evolution_log(name, evo_log, skill_dir=skill_dir)

    async def get_pending_records(
        self,
        name: str,
        target: Optional[EvolutionTarget] = None,
    ) -> List[EvolutionRecord]:
        return (await self.load_evolution_log(name, target)).pending_entries

    async def render_evolution_markdown(self, name: str) -> None:
        await self._projection.render_evolution_markdown(name)

    async def format_desc_experience_text(self, name: str, max_items: int = 5) -> str:
        return await self._projection.format_desc_experience_text(name, max_items=max_items)

    async def format_all_desc_experiences(self, names: List[str]) -> Dict[str, str]:
        return await self._projection.format_all_desc_experiences(names)

    async def format_body_experience_text(self, name: str) -> str:
        return await self._projection.format_body_experience_text(name)

    async def list_pending_summary(self, names: List[str]) -> str:
        return await self._projection.list_pending_summary(names)

    async def update_record_scores(
        self,
        name: str,
        updates: Dict[str, Dict[str, Any]],
    ) -> int:
        async with self._get_skill_lock(name):
            return await self._records.update_record_scores(name, updates)

    async def get_records_by_score(
        self,
        name: str,
        min_score: Optional[float] = None,
    ) -> List[EvolutionRecord]:
        return await self._records.get_records_by_score(name, min_score=min_score)

    async def delete_records(self, name: str, record_ids: List[str]) -> int:
        async with self._get_skill_lock(name):
            return await self._records.delete_records(name, record_ids)

    async def mark_records_applied(self, name: str, record_ids: List[str]) -> int:
        async with self._get_skill_lock(name):
            return await self._records.mark_records_applied(name, record_ids)

    async def merge_records(
        self,
        name: str,
        primary_id: str,
        remove_ids: List[str],
        new_content: str,
        new_score: Optional[float] = None,
    ) -> Optional[EvolutionRecord]:
        async with self._get_skill_lock(name):
            return await self._records.merge_records(
                name,
                primary_id,
                remove_ids,
                new_content,
                new_score=new_score,
            )

    async def update_record_content(
        self,
        name: str,
        record_id: str,
        new_content: str,
        new_score: Optional[float] = None,
    ) -> Optional[EvolutionRecord]:
        async with self._get_skill_lock(name):
            return await self._records.update_record_content(
                name,
                record_id,
                new_content,
                new_score=new_score,
            )

    async def create_skill(
        self,
        name: str,
        description: str,
        body: str,
        frontmatter: Optional[str] = None,
    ) -> Optional[Path]:
        return await self._archive.create_skill(name, description, body, frontmatter=frontmatter)

    async def list_skill_names_with_descriptions(self) -> List[Tuple[str, str]]:
        """List all skills with their descriptions.

        Returns:
            List of (skill_name, description) tuples
        """
        result: List[Tuple[str, str]] = []
        for name in self.list_skill_names():
            content = await self.read_skill_content(name)
            description = self.extract_description_from_skill_md(content)
            result.append((name, description))
        return result

    @staticmethod
    def extract_description_from_skill_md(content: str) -> str:
        return StoreProjectionHelper.extract_description_from_skill_md(content)

    # ── Governance primitives (archive / clear / rollback) ──

    async def archive_skill_body(self, name: str) -> Optional[str]:
        return await self._archive.archive_skill_body(name)

    async def archive_evolutions(self, name: str) -> Optional[str]:
        return await self._archive.archive_evolutions(name)

    async def clear_evolutions(self, name: str) -> None:
        await self._archive.clear_evolutions(name)

    def list_archives(self, name: str) -> List[str]:
        return self._archive.list_archives(name)
