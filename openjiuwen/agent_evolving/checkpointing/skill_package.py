# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pack/unpack skill directories and manage ``skill_id`` in SKILL.md frontmatter."""

from __future__ import annotations

import io
import tarfile
import uuid
from pathlib import Path
from typing import Iterable, Set, Tuple

from openjiuwen.agent_evolving.utils import parse_top_level_frontmatter

_EXCLUDE_DIR_NAMES: Set[str] = {"evolution", "archive", "__pycache__", ".git"}
_EXCLUDE_FILE_NAMES: Set[str] = {"evolutions.json"}


def new_skill_id() -> str:
    """Return a new globally unique skill identifier."""
    return f"sk_{uuid.uuid4().hex[:12]}"


def read_skill_id_from_content(content: str) -> str:
    """Read ``skill_id`` from SKILL.md frontmatter, or return empty string."""
    frontmatter = parse_top_level_frontmatter(content)
    return (frontmatter.get("skill_id") or "").strip()


def ensure_skill_id_in_content(content: str) -> Tuple[str, str]:
    """Ensure frontmatter contains ``skill_id``; return ``(updated_content, skill_id)``."""
    existing = read_skill_id_from_content(content)
    if existing:
        return content, existing

    skill_id = new_skill_id()
    stripped = content.lstrip("\ufeff")
    if stripped.startswith("---"):
        closing = stripped.find("---", 3)
        if closing != -1:
            head = stripped[:closing]
            tail = stripped[closing:]
            if not head.endswith("\n"):
                head += "\n"
            head += f"skill_id: {skill_id}\n"
            updated = head + tail
            if updated != content:
                return updated, skill_id
            return content, skill_id

    updated = f"---\nskill_id: {skill_id}\n---\n\n{content.lstrip()}"
    return updated.rstrip() + "\n", skill_id


def _should_pack_relative(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return False
    if parts[0] in _EXCLUDE_DIR_NAMES:
        return False
    if relative.name in _EXCLUDE_FILE_NAMES:
        return False
    if relative.name.startswith("."):
        return False
    return True


def pack_skill_directory(
    skill_dir: Path,
    *,
    skill_md_relpath: str | None = None,
    skill_md_content: str | None = None,
) -> bytes:
    """Tar-gzip a skill directory, excluding evolution-local artifacts.

    When ``skill_md_relpath`` and ``skill_md_content`` are provided, the tarball
    uses the supplied SKILL.md body instead of the on-disk file. Hub sharing uses
    this to omit locally projected evolution index blocks from the uploaded package.
    """
    root = skill_dir.resolve()
    override_arcname = skill_md_relpath.replace("\\", "/") if skill_md_relpath else None
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if not _should_pack_relative(relative):
                continue
            arcname = str(relative).replace("\\", "/")
            if override_arcname and skill_md_content is not None and arcname == override_arcname:
                payload = skill_md_content.encode("utf-8")
                info = tarfile.TarInfo(name=arcname)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
                continue
            archive.add(path, arcname=arcname)
    return buffer.getvalue()


def unpack_skill_package(package_bytes: bytes, dest_dir: Path) -> None:
    """Extract a skill package tarball into ``dest_dir`` (created if needed)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO(package_bytes)
    with tarfile.open(fileobj=buffer, mode="r:gz") as archive:
        if hasattr(tarfile, "data_filter"):
            archive.extractall(dest_dir, filter="data")
        else:
            archive.extractall(dest_dir)


def list_packable_files(skill_dir: Path) -> Iterable[Path]:
    """List files that would be included in a sharing package (for tests/diagnostics)."""
    root = skill_dir.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if _should_pack_relative(relative):
            yield path


__all__ = [
    "ensure_skill_id_in_content",
    "list_packable_files",
    "new_skill_id",
    "pack_skill_directory",
    "read_skill_id_from_content",
    "unpack_skill_package",
]
