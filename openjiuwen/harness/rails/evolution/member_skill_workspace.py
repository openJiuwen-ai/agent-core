# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Copy-on-write helpers for member-scoped Skill evolution."""

from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path

import portalocker

_SAFE_SKILL_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def ensure_member_skill_copy(
    *,
    member_skills_dir: str | Path,
    global_skills_dir: str | Path,
    skill_name: str,
) -> Path:
    """Materialize a global Skill as a member-owned copy before mutation.

    Existing real member directories are preserved. Links into the global
    store are atomically replaced so member evolution cannot mutate the global
    Skill through a symlink or Windows junction.
    """
    normalized_name = str(skill_name or "").strip()
    if not normalized_name or _SAFE_SKILL_NAME.fullmatch(normalized_name) is None:
        raise ValueError(f"unsafe Skill name: {skill_name!r}")

    member_root = Path(member_skills_dir).expanduser()
    global_root = Path(global_skills_dir).expanduser().resolve()
    source = global_root / normalized_name
    if not source.is_dir() or not (source / "SKILL.md").is_file():
        raise FileNotFoundError(
            f"global Skill '{normalized_name}' does not exist: {source}"
        )

    member_root.mkdir(parents=True, exist_ok=True)
    destination = member_root / normalized_name
    lock_path = member_root / ".member-skill-copy.lock"
    with portalocker.Lock(str(lock_path), timeout=30):
        if destination.is_dir():
            try:
                if destination.resolve() != source.resolve():
                    return destination
            except OSError:
                pass

        temporary = member_root / f".{normalized_name}.copy-{uuid.uuid4().hex}"
        try:
            shutil.copytree(
                source,
                temporary,
                symlinks=False,
                copy_function=shutil.copy2,
                dirs_exist_ok=False,
            )
            if os.path.lexists(destination):
                _remove_member_skill_link(destination)
            if os.path.lexists(destination):
                # A real directory appeared while the copy was prepared.
                return destination
            os.replace(temporary, destination)
            return destination
        finally:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)


def _remove_member_skill_link(path: Path) -> None:
    """Remove only a directory link; never delete a real member directory."""
    is_junction = getattr(path, "is_junction", lambda: False)()
    if path.is_symlink():
        path.unlink()
    elif is_junction:
        os.rmdir(path)


__all__ = ["ensure_member_skill_copy"]
