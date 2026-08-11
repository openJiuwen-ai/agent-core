# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Private archive/create helpers for ``EvolutionStore``."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

from openjiuwen.agent_evolving.checkpointing.changelog import (
    CHANGELOG_FILENAME,
    ClassifiedChangelogEntry,
    empty_changelog_template,
    merge_changelog_for_release,
    utc_today_iso,
)
from openjiuwen.agent_evolving.checkpointing.types import EvolutionLog, EvolutionRecord
from openjiuwen.agent_evolving.checkpointing.versioning import (
    aggregate_version_bump,
    bump_semver,
    parse_semver,
)
from openjiuwen.agent_evolving.utils import split_markdown_frontmatter
from openjiuwen.core.common.logging import logger

_EVOLUTION_FILENAME = "evolutions.json"
_DEFAULT_VERSION = "v1.0.0"


class StoreArchiveHelper:
    """Encapsulates archive, clear, create-skill, and SemVer helpers."""

    def __init__(self, store: Any) -> None:
        self._store = store

    @staticmethod
    def archive_dir(skill_dir: Path) -> Path:
        archive = skill_dir / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        return archive

    @staticmethod
    def archive_version_key(version: str) -> str:
        """Normalize a SemVer string to archive key ``vMAJOR.MINOR.PATCH``."""
        text = (version or "").strip()
        if text[:1] in ("v", "V"):
            text = text[1:].strip()
        return f"v{text}"

    @classmethod
    def archive_paths(cls, archive: Path, version: str) -> Tuple[Path, Path]:
        key = cls.archive_version_key(version)
        return archive / f"SKILL.{key}.md", archive / f"evolutions.{key}.json"

    @classmethod
    def body_archive_name_for_version(cls, version: str) -> str:
        return f"SKILL.{cls.archive_version_key(version)}.md"

    @staticmethod
    def is_body_archive_filename(archive_name: str) -> bool:
        return (
            archive_name.startswith("SKILL.v")
            and archive_name.endswith(".md")
            and archive_name != "SKILL.md"
        )

    @staticmethod
    def is_valid_skill_archive_name(archive_name: str) -> bool:
        path = Path(archive_name)
        return bool(archive_name) and path.name == archive_name and ".." not in path.parts

    @classmethod
    def normalize_body_archive_name(cls, version_or_name: str) -> Optional[str]:
        """Accept ``SKILL.v1.0.0.md`` or bare ``1.0.0`` / ``v1.0.0``; reject invalid."""
        if not version_or_name or not cls.is_valid_skill_archive_name(version_or_name):
            return None
        if version_or_name.endswith(".md"):
            if not cls.is_body_archive_filename(version_or_name):
                return None
            version_key = version_or_name.removeprefix("SKILL.").removesuffix(".md")
            bare = version_key[1:] if version_key[:1] in ("v", "V") else version_key
            major, minor, patch = parse_semver(version_key)
            if bare != f"{major}.{minor}.{patch}":
                return None
            return version_or_name
        if "/" in version_or_name or "\\" in version_or_name:
            return None
        stripped = version_or_name.strip()
        bare = stripped[1:] if stripped[:1] in ("v", "V") else stripped
        major, minor, patch = parse_semver(stripped)
        if bare != f"{major}.{minor}.{patch}":
            return None
        return cls.body_archive_name_for_version(version_or_name)

    @classmethod
    def paired_evolution_archive_name(cls, body_archive_name: str) -> Optional[str]:
        if not cls.is_valid_skill_archive_name(body_archive_name):
            return None
        if not cls.is_body_archive_filename(body_archive_name):
            return None
        version_key = body_archive_name.removeprefix("SKILL.").removesuffix(".md")
        if not version_key:
            return None
        return f"evolutions.{version_key}.json"

    def archive_version_exists(self, archive: Path, version: str) -> bool:
        body_path, evo_path = self.archive_paths(archive, version)
        return body_path.exists() or evo_path.exists()

    @staticmethod
    def extract_version_from_skill_md(content: str) -> Optional[str]:
        front_matter, _ = split_markdown_frontmatter(content)
        if front_matter is None:
            return None
        for line in front_matter.strip().split("\n"):
            if line.startswith("version:"):
                value = line.split(":", 1)[1].strip().strip('"').strip("'")
                return value or None
        return None

    async def resolve_current_version(
        self,
        name: str,
        *,
        subject_kind: Optional[str] = None,
        skill_dir: Optional[Path] = None,
        evo_log: Optional[EvolutionLog] = None,
    ) -> str:
        """Resolve current skill version: frontmatter -> evolutions.json -> v1.0.0."""
        resolved_dir = skill_dir or self._store.resolve_skill_dir(name, subject_kind=subject_kind)
        if resolved_dir is not None:
            skill_md_path = self._store.find_skill_md(resolved_dir)
            if skill_md_path is not None:
                content = await self._store.read_file_text(skill_md_path)
                frontmatter_version = self.extract_version_from_skill_md(content)
                if frontmatter_version:
                    return frontmatter_version
        log = evo_log
        if log is None:
            log = await self._store.load_full_evolution_log(name, subject_kind=subject_kind)
        if log.version:
            return log.version
        return _DEFAULT_VERSION

    async def set_skill_md_version(self, skill_dir: Path, version: str) -> None:
        """Write or update ``version:`` in SKILL.md YAML front-matter."""
        skill_md_path = self._store.find_skill_md(skill_dir)
        if skill_md_path is None:
            return

        content = await self._store.read_file_text(skill_md_path)
        front_matter, body = split_markdown_frontmatter(content)
        if front_matter is not None:
            lines = front_matter.strip("\n").split("\n") if front_matter.strip("\n") else []
            updated = False
            for idx, line in enumerate(lines):
                if line.startswith("version:"):
                    lines[idx] = f"version: {version}"
                    updated = True
                    break
            if not updated:
                lines.append(f"version: {version}")
            new_front = "\n".join(lines)
            # Keep body as returned by the splitter (already excludes closing fence).
            if body.startswith("\n") or body.startswith("\r\n") or not body:
                new_content = f"---\n{new_front}\n---{body}"
            else:
                new_content = f"---\n{new_front}\n---\n{body}"
            await self._store.write_file_text(skill_md_path, new_content)
            return

        new_content = f"---\nversion: {version}\n---\n{content}"
        await self._store.write_file_text(skill_md_path, new_content)

    async def bump_version_for_rebuild(
        self,
        name: str,
        *,
        subject_kind: Optional[str] = None,
        entries: Optional[List[EvolutionRecord]] = None,
    ) -> Optional[str]:
        """Aggregate experience types and bump SemVer once for a rebuild."""
        skill_dir = self._store.resolve_skill_dir(name, subject_kind=subject_kind)
        if skill_dir is None:
            return None

        evo_log = await self._store.load_full_evolution_log(name, subject_kind=subject_kind)
        bump_entries = evo_log.entries if entries is None else list(entries)
        level = aggregate_version_bump(bump_entries)
        if level is None:
            return None

        current = await self.resolve_current_version(
            name,
            subject_kind=subject_kind,
            skill_dir=skill_dir,
            evo_log=evo_log,
        )
        new_version = bump_semver(current, level)
        evo_log.version = new_version
        evo_log.updated_at = datetime.now(tz=timezone.utc).isoformat()
        await self.set_skill_md_version(skill_dir, new_version)
        await self._store.save_evolution_log(name, evo_log, skill_dir=skill_dir, subject_kind=subject_kind)
        logger.info(
            "[EvolutionStore] rebuild bumped skill=%s version %s -> %s (%s)",
            name,
            current,
            new_version,
            level.value,
        )
        return new_version

    async def append_changelog_for_rebuild(
        self,
        name: str,
        version: str,
        classified_entries: List[ClassifiedChangelogEntry],
        *,
        subject_kind: Optional[str] = None,
        release_date: Optional[str] = None,
    ) -> bool:
        """Insert a version section into changelog.md for a rebuild release."""
        skill_dir = self._store.resolve_skill_dir(name, subject_kind=subject_kind)
        if skill_dir is None:
            return False
        version_text = (version or "").strip()
        if not version_text:
            return False

        changelog_path = skill_dir / CHANGELOG_FILENAME
        existing = ""
        if changelog_path.exists():
            existing = await self._store.read_file_text(changelog_path)

        day = release_date or utc_today_iso()
        updated = merge_changelog_for_release(
            existing,
            version_text,
            classified_entries,
            release_date=day,
        )
        if updated is None:
            logger.info(
                "[EvolutionStore] changelog already has version %s for skill=%s; skip",
                version_text,
                name,
            )
            return False

        await self._store.write_file_text(changelog_path, updated)
        logger.info(
            "[EvolutionStore] wrote changelog version %s for skill=%s (%d entries)",
            version_text,
            name,
            len(classified_entries),
        )
        return True

    async def create_skill(
        self,
        name: str,
        description: str,
        body: str,
        frontmatter: Optional[str] = None,
    ) -> Optional[Path]:
        if not name or not re.match(r"^[a-zA-Z0-9_-]+$", name):
            logger.error("[EvolutionStore] create_skill: invalid name %r", name)
            return None
        if ".." in name or "/" in name or "\\" in name:
            logger.error("[EvolutionStore] create_skill: path traversal attempt in name %r", name)
            return None

        skill_dir = self._store.resolve_skill_dir(name, create=True)
        if skill_dir is None:
            logger.error("[EvolutionStore] create_skill: cannot resolve skill dir for %s", name)
            return None

        if skill_dir.exists():
            logger.error(
                "[EvolutionStore] create_skill: skill '%s' already exists at %s; "
                "use update operations instead of create",
                name,
                skill_dir,
            )
            return None

        skill_dir.mkdir(parents=True, exist_ok=True)

        if frontmatter:
            skill_md_content = f"{frontmatter}\n\n# {name}\n\n{body}\n"
        else:
            skill_md_content = f"""---
name: {name}
description: {description}
version: {_DEFAULT_VERSION}
---

# {name}

{body}
"""
        skill_md_path = skill_dir / "SKILL.md"
        await self._store.write_file_text(skill_md_path, skill_md_content)

        empty_log = EvolutionLog.empty(skill_id=name)
        empty_log.version = _DEFAULT_VERSION
        await self._store.save_evolution_log(name, empty_log, skill_dir=skill_dir)

        evo_dir = skill_dir / "evolution"
        evo_dir.mkdir(parents=True, exist_ok=True)

        changelog_path = skill_dir / CHANGELOG_FILENAME
        await self._store.write_file_text(changelog_path, empty_changelog_template())

        logger.info(
            "[EvolutionStore] created new skill '%s' at %s",
            name,
            skill_dir,
        )
        return skill_dir

    async def archive_skill_body(
        self,
        name: str,
        *,
        subject_kind: Optional[str] = None,
        version: Optional[str] = None,
    ) -> Optional[str]:
        skill_dir = self._store.resolve_skill_dir(name, subject_kind=subject_kind)
        if skill_dir is None:
            return None
        md_path = self._store.find_skill_md(skill_dir)
        if md_path is None:
            return None
        archive = self.archive_dir(skill_dir)
        archive_version = version or await self.resolve_current_version(
            name, subject_kind=subject_kind, skill_dir=skill_dir,
        )
        dest, _ = self.archive_paths(archive, archive_version)
        if dest.exists():
            logger.warning(
                "[EvolutionStore] archive body skipped for skill=%s version=%s (already exists)",
                name,
                self.archive_version_key(archive_version),
            )
            return dest.name
        content = await self._store.read_file_text(md_path)
        await self._store.write_file_text(dest, content)
        logger.info("[EvolutionStore] archived %s -> %s", md_path.name, dest.name)
        return dest.name

    async def archive_evolutions(
        self,
        name: str,
        *,
        subject_kind: Optional[str] = None,
        version: Optional[str] = None,
    ) -> Optional[str]:
        """Write an empty paired evolutions archive (never copy live entries).

        Live ``evolutions.json`` is left unchanged. Rollback restores only
        ``SKILL.md`` and clears live evolutions, so archived evo files are
        kept as empty SemVer pair markers.
        """
        skill_dir = self._store.resolve_skill_dir(name, subject_kind=subject_kind)
        if skill_dir is None:
            return None
        archive = self.archive_dir(skill_dir)
        archive_version = version or await self.resolve_current_version(
            name, subject_kind=subject_kind, skill_dir=skill_dir,
        )
        version_key = self.archive_version_key(archive_version)
        _, dest = self.archive_paths(archive, archive_version)
        if dest.exists():
            logger.warning(
                "[EvolutionStore] archive evolutions skipped for version=%s (already exists)",
                version_key,
            )
            return dest.name
        empty_log = EvolutionLog.empty(skill_id=name)
        empty_log.version = version_key
        content = json.dumps(empty_log.to_dict(), ensure_ascii=False, indent=2)
        await self._store.write_file_text(dest, content)
        logger.info("[EvolutionStore] archived evolutions -> %s (empty)", dest.name)
        return dest.name

    async def archive_current_state(
        self,
        name: str,
        *,
        subject_kind: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        skill_dir = self._store.resolve_skill_dir(name, subject_kind=subject_kind)
        if skill_dir is None:
            return None, None
        archive = self.archive_dir(skill_dir)
        version = await self.resolve_current_version(name, subject_kind=subject_kind, skill_dir=skill_dir)
        if self.archive_version_exists(archive, version):
            body_path, evo_path = self.archive_paths(archive, version)
            logger.warning(
                "[EvolutionStore] archive skipped for skill=%s version=%s (already exists)",
                name,
                self.archive_version_key(version),
            )
            return (
                body_path.name if body_path.exists() else None,
                evo_path.name if evo_path.exists() else None,
            )
        body_archive = await self.archive_skill_body(name, subject_kind=subject_kind, version=version)
        evo_archive = await self.archive_evolutions(name, subject_kind=subject_kind, version=version)
        return body_archive, evo_archive

    async def clear_evolutions(
        self,
        name: str,
        *,
        subject_kind: Optional[str] = None,
        retain_version: Optional[str] = None,
    ) -> None:
        skill_dir = self._store.resolve_skill_dir(name, subject_kind=subject_kind)
        if retain_version:
            version = retain_version
        elif skill_dir is not None:
            evo_log = await self._store.load_full_evolution_log(name, subject_kind=subject_kind)
            version = await self.resolve_current_version(
                name, subject_kind=subject_kind, skill_dir=skill_dir, evo_log=evo_log,
            )
        else:
            version = _DEFAULT_VERSION
        empty_log = EvolutionLog.empty(skill_id=name)
        empty_log.version = version
        await self._store.save_evolution_log(name, empty_log, skill_dir=skill_dir, subject_kind=subject_kind)
        # Do not call render_evolution_markdown here: empty entries would strip the
        # evolution-index from SKILL.md via clear_rendered_outputs, which breaks
        # rollback that just restored an archived body.
        logger.info(
            "[EvolutionStore] cleared evolutions for skill=%s (version=%s)",
            name,
            version,
        )

    @classmethod
    def _archive_filename_version_key(cls, filename: str) -> Optional[Tuple[int, int, int]]:
        """Extract a comparable SemVer triple from ``SKILL.vX.Y.Z.md`` / ``evolutions.vX.Y.Z.json``."""
        name = Path(filename).name
        version_key: Optional[str] = None
        if cls.is_body_archive_filename(name):
            version_key = name.removeprefix("SKILL.").removesuffix(".md")
        elif name.startswith("evolutions.v") and name.endswith(".json"):
            version_key = name.removeprefix("evolutions.").removesuffix(".json")
        if not version_key:
            return None
        bare = version_key[1:] if version_key[:1] in ("v", "V") else version_key
        major, minor, patch = parse_semver(version_key)
        if bare != f"{major}.{minor}.{patch}":
            return None
        return major, minor, patch

    @classmethod
    def _archive_file_sort_key(cls, path: Path) -> Tuple[int, Tuple[int, int, int], str]:
        """Newest SemVer first; non-semver files last; tie-break by name for stability."""
        version = cls._archive_filename_version_key(path.name)
        if version is None:
            return (1, (0, 0, 0), path.name)
        major, minor, patch = version
        return (0, (-major, -minor, -patch), path.name)

    def list_archives(self, name: str, *, subject_kind: Optional[str] = None) -> List[str]:
        """List archive filenames for a skill, newest SemVer first."""
        skill_dir = self._store.resolve_skill_dir(name, subject_kind=subject_kind)
        if skill_dir is None:
            return []
        archive = skill_dir / "archive"
        if not archive.is_dir():
            return []
        files = [f for f in archive.iterdir() if f.is_file()]
        files.sort(key=self._archive_file_sort_key)
        return [f.name for f in files]
