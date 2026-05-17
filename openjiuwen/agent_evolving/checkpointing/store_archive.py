# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Private archive/create helpers for ``EvolutionStore``."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from openjiuwen.agent_evolving.checkpointing.types import EvolutionLog
from openjiuwen.core.common.logging import logger

_EVOLUTION_FILENAME = "evolutions.json"


class StoreArchiveHelper:
    """Encapsulates archive, clear, and create-skill operations."""

    def __init__(self, store: Any) -> None:
        self._store = store

    @staticmethod
    def archive_dir(skill_dir: Path) -> Path:
        archive = skill_dir / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        return archive

    @staticmethod
    def ts_suffix() -> str:
        return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")

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
---

# {name}

{body}
"""
        skill_md_path = skill_dir / "SKILL.md"
        await self._store.write_file_text(skill_md_path, skill_md_content)

        empty_log = EvolutionLog.empty(skill_id=name)
        await self._store.save_evolution_log(name, empty_log, skill_dir=skill_dir)

        evo_dir = skill_dir / "evolution"
        evo_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "[EvolutionStore] created new skill '%s' at %s",
            name,
            skill_dir,
        )
        return skill_dir

    async def archive_skill_body(self, name: str) -> Optional[str]:
        skill_dir = self._store.resolve_skill_dir(name)
        if skill_dir is None:
            return None
        md_path = self._store.find_skill_md(skill_dir)
        if md_path is None:
            return None
        archive = self.archive_dir(skill_dir)
        suffix = self.ts_suffix()
        dest = archive / f"SKILL.v{suffix}.md"
        content = await self._store.read_file_text(md_path)
        await self._store.write_file_text(dest, content)
        logger.info("[EvolutionStore] archived %s -> %s", md_path.name, dest.name)
        return dest.name

    async def archive_evolutions(self, name: str) -> Optional[str]:
        skill_dir = self._store.resolve_skill_dir(name)
        if skill_dir is None:
            return None
        evo_path = skill_dir / _EVOLUTION_FILENAME
        if not evo_path.is_file():
            return None
        archive = self.archive_dir(skill_dir)
        suffix = self.ts_suffix()
        dest = archive / f"evolutions.v{suffix}.json"
        content = await self._store.read_file_text(evo_path)
        await self._store.write_file_text(dest, content)
        logger.info("[EvolutionStore] archived evolutions -> %s", dest.name)
        return dest.name

    async def clear_evolutions(self, name: str) -> None:
        empty_log = EvolutionLog.empty(skill_id=name)
        await self._store.save_evolution_log(name, empty_log)
        await self._store.render_evolution_markdown(name)
        logger.info("[EvolutionStore] cleared evolutions for skill=%s", name)

    def list_archives(self, name: str) -> List[str]:
        skill_dir = self._store.resolve_skill_dir(name)
        if skill_dir is None:
            return []
        archive = skill_dir / "archive"
        if not archive.is_dir():
            return []
        files = sorted(archive.iterdir(), key=lambda p: p.name, reverse=True)
        return [f.name for f in files if f.is_file()]
