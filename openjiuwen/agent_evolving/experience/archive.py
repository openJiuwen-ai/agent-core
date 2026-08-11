# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Paired archive service for skill evolution state."""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from openjiuwen.agent_evolving.checkpointing.types import EvolutionLog
from openjiuwen.agent_evolving.checkpointing.versioning import parse_semver
from openjiuwen.agent_evolving.experience.draft_schema import normalize_subject
from openjiuwen.agent_evolving.utils import split_markdown_frontmatter
from openjiuwen.core.common.logging import logger

_EVOLUTION_FILENAME = "evolutions.json"
_LATEST_VERSION = "latest"
_SKILL_ARCHIVE_PREFIX = "SKILL."
_SKILL_ARCHIVE_SUFFIX = ".md"
_EVOLUTION_ARCHIVE_PREFIX = "evolutions."
_EVOLUTION_ARCHIVE_SUFFIX = ".json"
_DEFAULT_VERSION = "v1.0.0"
DEFAULT_ARCHIVE_KEEP_LATEST = 10


@dataclass(frozen=True)
class EvolutionArchivePair:
    """A restorable ``SKILL.md`` and ``evolutions.json`` archive pair."""

    version: str
    skill_archive: Path
    evolution_archive: Path

    @property
    def skill_archive_name(self) -> str:
        return self.skill_archive.name

    @property
    def evolution_archive_name(self) -> str:
        return self.evolution_archive.name

    def to_payload(self) -> dict[str, str]:
        return {
            "version": self.version,
            "skill_archive": self.skill_archive_name,
            "evolution_archive": self.evolution_archive_name,
        }


class EvolutionArchiveService:
    """Own paired archive invariants for evolution rollback lifecycle."""

    def __init__(self, *, store: Any, keep_latest: int = DEFAULT_ARCHIVE_KEEP_LATEST) -> None:
        self._store = store
        self._keep_latest = keep_latest

    def list_pairs(
        self,
        subject: str | dict[str, Any] | Any,
        *,
        subject_kind: Optional[str] = None,
    ) -> list[EvolutionArchivePair]:
        """List complete archive pairs for a subject, newest SemVer first."""
        name, kind = self._subject_name_and_kind(subject, subject_kind=subject_kind)
        skill_dir = self._store.resolve_skill_dir(name, subject_kind=kind)
        if skill_dir is None:
            return []
        archive_dir = skill_dir / "archive"
        if not archive_dir.is_dir():
            return []

        archive_names = set(self._store.list_archives(name, subject_kind=kind))
        pairs: list[EvolutionArchivePair] = []
        for filename in archive_names:
            version = self._version_from_skill_archive_name(filename)
            if version is None:
                continue
            evolution_name = self._evolution_archive_name(version)
            if evolution_name not in archive_names:
                continue
            skill_path = archive_dir / filename
            evo_path = archive_dir / evolution_name
            if not skill_path.is_file() or not evo_path.is_file():
                continue
            pairs.append(
                EvolutionArchivePair(
                    version=version,
                    skill_archive=skill_path,
                    evolution_archive=evo_path,
                )
            )
        pairs.sort(key=lambda pair: parse_semver(pair.version), reverse=True)
        return pairs

    async def archive_current_pair(
        self,
        subject: str | dict[str, Any] | Any,
        *,
        subject_kind: Optional[str] = None,
        version: Optional[str] = None,
    ) -> Optional[EvolutionArchivePair]:
        """Archive current ``SKILL.md`` with an empty paired evolutions archive.

        Evolution archives are always empty by design (never copy live entries).
        Missing current ``evolutions.json`` is initialized with an empty log so
        every archive version remains a complete rollback target pair marker.
        Live ``evolutions.json`` is left unchanged by this method.

        If the target SemVer pair already exists, skip writing and return the
        existing pair (idempotent).
        """
        name, kind = self._subject_name_and_kind(subject, subject_kind=subject_kind)
        skill_dir = self._store.resolve_skill_dir(name, subject_kind=kind)
        if skill_dir is None:
            return None
        skill_md = self._store.find_skill_md(skill_dir) or skill_dir / "SKILL.md"
        if not skill_md.is_file():
            return None

        evolution_log = skill_dir / _EVOLUTION_FILENAME
        if evolution_log.exists() and not evolution_log.is_file():
            return None
        await self._ensure_current_evolution_log(
            name,
            skill_dir=skill_dir,
            evolution_log=evolution_log,
            subject_kind=kind,
        )
        if not evolution_log.is_file():
            return None

        archive_dir = skill_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_version = version or await self._resolve_current_version(name, skill_dir, subject_kind=kind)
        version_key = self._archive_version_key(archive_version)
        skill_archive = archive_dir / self._skill_archive_name(version_key)
        evolution_archive = archive_dir / self._evolution_archive_name(version_key)

        if skill_archive.exists() or evolution_archive.exists():
            logger.warning(
                "[EvolutionArchiveService] archive skipped for subject=%s version=%s (already exists)",
                name,
                version_key,
            )
            if skill_archive.is_file() and evolution_archive.is_file():
                return EvolutionArchivePair(
                    version=version_key,
                    skill_archive=skill_archive,
                    evolution_archive=evolution_archive,
                )
            return None

        skill_content = await self._store.read_file_text(skill_md)
        empty_log = EvolutionLog.empty(skill_id=name)
        empty_log.version = version_key
        evolution_content = json.dumps(empty_log.to_dict(), ensure_ascii=False, indent=2)
        try:
            await self._store.write_file_text(skill_archive, skill_content)
            await self._store.write_file_text(evolution_archive, evolution_content)
        except Exception:
            self._remove_file(skill_archive)
            self._remove_file(evolution_archive)
            raise

        logger.info(
            "[EvolutionArchiveService] archived current pair: subject=%s kind=%s version=%s "
            "(evolutions archive empty)",
            name,
            kind or "default",
            version_key,
        )
        return EvolutionArchivePair(
            version=version_key,
            skill_archive=skill_archive,
            evolution_archive=evolution_archive,
        )

    async def rollback_to_pair(
        self,
        subject: str | dict[str, Any] | Any,
        pair_or_version: EvolutionArchivePair | str,
        *,
        subject_kind: Optional[str] = None,
        prune: bool = False,
    ) -> bool:
        """Restore archived ``SKILL.md`` and always clear live evolutions.

        Restores the archived skill body after first archiving the current
        state. Live ``evolutions.json`` is always cleared (empty entries,
        retained version). Evolution archives are empty by design, so their
        contents are never restored. ``render_evolution_markdown`` is not
        called so the restored ``SKILL.md`` is left unmodified.
        """
        name, kind = self._subject_name_and_kind(subject, subject_kind=subject_kind)
        pair = self._resolve_pair(subject, pair_or_version, subject_kind=kind)
        if pair is None:
            return False

        skill_dir = self._store.resolve_skill_dir(name, subject_kind=kind)
        if skill_dir is None:
            return False
        skill_md = self._store.find_skill_md(skill_dir) or skill_dir / "SKILL.md"
        evolution_log = skill_dir / _EVOLUTION_FILENAME
        if not self._current_target_paths_are_valid(skill_md=skill_md, evolution_log=evolution_log):
            return False
        if not pair.skill_archive.is_file() or not pair.evolution_archive.is_file():
            return False

        skill_content = await self._store.read_file_text(pair.skill_archive)
        if not skill_content:
            return False

        current_pair = await self.archive_current_pair(name, subject_kind=kind)
        if current_pair is None:
            return False

        await self._store.write_file_text(skill_md, skill_content)
        await self._store.clear_evolutions(name, subject_kind=kind)

        self._remove_file(pair.skill_archive)
        self._remove_file(pair.evolution_archive)
        logger.info(
            "[EvolutionArchiveService] removed consumed archive pair: version=%s", pair.version,
        )

        if prune:
            self.prune(name, subject_kind=kind)
        return True

    def prune(
        self,
        subject: str | dict[str, Any] | Any,
        *,
        subject_kind: Optional[str] = None,
        keep_latest: Optional[int] = None,
    ) -> int:
        """Prune old complete pairs and return the number of pairs removed."""
        keep = self._keep_latest if keep_latest is None else keep_latest
        keep = max(int(keep), 0)
        pairs = self.list_pairs(subject, subject_kind=subject_kind)
        if len(pairs) <= keep:
            return 0

        pruned = 0
        for pair in pairs[keep:]:
            self._remove_file(pair.skill_archive)
            self._remove_file(pair.evolution_archive)
            pruned += 1
        return pruned

    @classmethod
    def normalize_version(cls, raw: str) -> Optional[str]:
        """Normalize a user-facing archive version token to ``vMAJOR.MINOR.PATCH`` or ``latest``."""
        version = str(raw or "").strip()
        if not version:
            return None
        if version == _LATEST_VERSION:
            return _LATEST_VERSION
        if version.startswith(_SKILL_ARCHIVE_PREFIX) and version.endswith(_SKILL_ARCHIVE_SUFFIX):
            version = version[len(_SKILL_ARCHIVE_PREFIX):-len(_SKILL_ARCHIVE_SUFFIX)]
        return cls._archive_version_key_if_semver(version)

    def _resolve_pair(
        self,
        subject: str | dict[str, Any] | Any,
        pair_or_version: EvolutionArchivePair | str,
        *,
        subject_kind: Optional[str],
    ) -> Optional[EvolutionArchivePair]:
        if isinstance(pair_or_version, EvolutionArchivePair):
            return pair_or_version

        requested_version = self.normalize_version(pair_or_version)
        if requested_version is None:
            return None
        pairs = self.list_pairs(subject, subject_kind=subject_kind)
        if not pairs:
            return None
        if requested_version == _LATEST_VERSION:
            return pairs[0]
        return next((pair for pair in pairs if pair.version == requested_version), None)

    @staticmethod
    def _subject_name_and_kind(
        subject: str | dict[str, Any] | Any,
        *,
        subject_kind: Optional[str],
    ) -> tuple[str, Optional[str]]:
        if isinstance(subject, str):
            return subject, subject_kind
        normalized = normalize_subject(subject)
        return normalized.name, normalized.kind

    async def _ensure_current_evolution_log(
        self,
        name: str,
        *,
        skill_dir: Path,
        evolution_log: Path,
        subject_kind: Optional[str],
    ) -> None:
        if evolution_log.is_file():
            return
        await self._store.save_evolution_log(
            name,
            EvolutionLog.empty(skill_id=name),
            skill_dir=skill_dir,
            subject_kind=subject_kind,
        )

    async def _resolve_current_version(
        self,
        name: str,
        skill_dir: Path,
        *,
        subject_kind: Optional[str],
    ) -> str:
        resolve = getattr(self._store, "resolve_current_version", None)
        if callable(resolve):
            return await resolve(name, subject_kind=subject_kind)

        skill_md = self._store.find_skill_md(skill_dir) or skill_dir / "SKILL.md"
        if skill_md.is_file():
            content = await self._store.read_file_text(skill_md)
            frontmatter_version = self._extract_version_from_skill_md(content)
            if frontmatter_version:
                return frontmatter_version
        evo_log = await self._store.load_full_evolution_log(name, subject_kind=subject_kind)
        if evo_log.version:
            return evo_log.version
        return _DEFAULT_VERSION

    @staticmethod
    def _extract_version_from_skill_md(content: str) -> Optional[str]:
        front_matter, _ = split_markdown_frontmatter(content)
        if front_matter is None:
            return None
        for line in front_matter.strip().split("\n"):
            if line.startswith("version:"):
                value = line.split(":", 1)[1].strip().strip('"').strip("'")
                return value or None
        return None

    @staticmethod
    def _archive_version_key(version: str) -> str:
        """Normalize a SemVer string to archive key ``vMAJOR.MINOR.PATCH``."""
        text = (version or "").strip()
        if text[:1] in ("v", "V"):
            text = text[1:].strip()
        return f"v{text}"

    @classmethod
    def _archive_version_key_if_semver(cls, version: str) -> Optional[str]:
        text = (version or "").strip()
        if not text:
            return None
        bare = text[1:] if text[:1] in ("v", "V") else text
        major, minor, patch = parse_semver(text)
        if bare != f"{major}.{minor}.{patch}":
            return None
        return cls._archive_version_key(text)

    @staticmethod
    def _skill_archive_name(version: str) -> str:
        return f"{_SKILL_ARCHIVE_PREFIX}{version}{_SKILL_ARCHIVE_SUFFIX}"

    @staticmethod
    def _evolution_archive_name(version: str) -> str:
        return f"{_EVOLUTION_ARCHIVE_PREFIX}{version}{_EVOLUTION_ARCHIVE_SUFFIX}"

    @classmethod
    def _version_from_skill_archive_name(cls, filename: str) -> Optional[str]:
        if not filename.startswith(_SKILL_ARCHIVE_PREFIX) or not filename.endswith(_SKILL_ARCHIVE_SUFFIX):
            return None
        version = filename[len(_SKILL_ARCHIVE_PREFIX):-len(_SKILL_ARCHIVE_SUFFIX)]
        return cls._archive_version_key_if_semver(version)

    @staticmethod
    def _current_target_paths_are_valid(*, skill_md: Path, evolution_log: Path) -> bool:
        if not skill_md.is_file():
            return False
        if evolution_log.exists() and not evolution_log.is_file():
            return False
        return True

    @staticmethod
    def _remove_file(path: Path) -> None:
        with suppress(OSError):
            path.unlink(missing_ok=True)


__all__ = [
    "DEFAULT_ARCHIVE_KEEP_LATEST",
    "EvolutionArchivePair",
    "EvolutionArchiveService",
]
