# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Owned skill-root mounts and validation of required skill resources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from openjiuwen.core.sys_operation import SysOperation

if TYPE_CHECKING:
    from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail


def split_skill_front_matter(text: str) -> tuple[dict, str] | None:
    """Parse the native SKILL.md frontmatter format without reading its body."""
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    _, yaml_block, body = parts
    return yaml.safe_load(yaml_block) or {}, body.lstrip()


@dataclass
class SkillRootMount:
    """A mount releases a root only when it introduced that root itself."""

    rail: SkillUseRail
    root: Path
    owned: bool

    def ensure_mounted(self) -> None:
        """Restore a detached required root without discarding prior ownership."""
        renewed = self.rail.mount_skill_root(self.root)
        self.owned = self.owned or renewed.owned

    def unmount(self) -> None:
        """Release an owned root once; pre-existing roots remain mounted."""
        if self.owned:
            self.rail.unmount_skill_root(self.root)
            self.owned = False


class SkillResourceValidator:
    """Check a required skill through the host's existing filesystem policy."""

    def __init__(self, name: str, directory: Path) -> None:
        self.name = name
        self.path = directory.resolve() / "SKILL.md"
        self._verified_signature: tuple[int, int, int] | None = None

    async def validate(self, operation: SysOperation) -> None:
        """Validate readable metadata, rechecking when the file or backend changes."""
        try:
            stat = self.path.stat()
            signature = (id(operation), stat.st_mtime_ns, stat.st_size)
            if signature == self._verified_signature:
                return
            result = await operation.fs().read_file(
                str(self.path),
                mode="text",
                encoding="utf-8",
                head=64,
                only_read=True,
            )
            if result.code != 0:
                raise ValueError(result.message)
            parsed = split_skill_front_matter(result.data.content)
            metadata = parsed[0] if parsed is not None else None
            if not isinstance(metadata, dict) or metadata.get("name") != self.name:
                raise ValueError("invalid SKILL.md frontmatter")
            if not isinstance(metadata.get("description"), str) or not metadata["description"].strip():
                raise ValueError("invalid SKILL.md frontmatter")
        except Exception as exc:
            raise ValueError(f"is not readable: {self.path}: {exc}") from exc
        self._verified_signature = signature

    def clear(self) -> None:
        """Forget validation when the resource is detached from an agent."""
        self._verified_signature = None
