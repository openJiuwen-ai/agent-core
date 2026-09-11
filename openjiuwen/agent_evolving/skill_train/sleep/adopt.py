# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Adopt staged sleep proposals via EvolutionStore SemVer."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.versioning import VersionBump, bump_semver
from openjiuwen.agent_evolving.skill_train.sleep.staging import (
    list_skill_proposals,
    load_manifest,
    read_proposed_skill,
)
from openjiuwen.core.common.logging import logger


@dataclass
class AdoptResult:
    skill_name: str
    staging_dir: str
    previous_version: str
    new_version: str
    archived_body: Optional[str]
    archived_evolutions: Optional[str]


def _run(coro: Any) -> Any:
    """Run coroutine from sync context; fail clearly if already inside a loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "adopt_staged_skill() cannot be called from a running event loop; "
        "use await adopt_staged_skill_async(...) instead"
    )


async def _bump_minor_version(store: EvolutionStore, skill_name: str) -> tuple[str, str]:
    current = await store.resolve_current_version(skill_name)
    new_version = bump_semver(current, VersionBump.MINOR)
    skill_dir = store.resolve_skill_dir(skill_name)
    if skill_dir is None:
        raise RuntimeError(f"skill '{skill_name}' directory not found after write")
    await store._archive.set_skill_md_version(skill_dir, new_version)
    evo_log = await store.load_full_evolution_log(skill_name)
    evo_log.version = new_version
    await store.save_evolution_log(skill_name, evo_log, skill_dir=skill_dir)
    return current, new_version


async def publish_skill_content_async(
    store: EvolutionStore,
    skill_name: str,
    content: str,
    *,
    staging_dir: str = "",
    description: str = "Skill consolidated by skill_train sleep",
) -> AdoptResult:
    """Archive (if exists), write content, bump MINOR SemVer."""
    name = (skill_name or "").strip()
    if not name:
        raise ValueError("skill_name is required")
    archived_body: Optional[str] = None
    archived_evo: Optional[str] = None
    previous_version = "0.0.0"

    if store.skill_exists(name):
        previous_version = await store.resolve_current_version(name)
        archived_body, archived_evo = await store.archive_current_state(name)
        ok = await store.write_skill_content(name, content)
        if not ok:
            raise RuntimeError(f"failed to write skill content for '{name}'")
        previous_version, new_version = await _bump_minor_version(store, name)
    else:
        created = await store.create_skill(name, description, body="")
        if created is None:
            raise RuntimeError(f"failed to create skill '{name}'")
        ok = await store.write_skill_content(name, content)
        if not ok:
            raise RuntimeError(f"failed to write initial skill content for '{name}'")
        new_version = await store.resolve_current_version(name)

    logger.info(
        "[skill_sleep] published skill=%s version %s -> %s staging=%s",
        name,
        previous_version,
        new_version,
        staging_dir or "-",
    )
    return AdoptResult(
        skill_name=name,
        staging_dir=staging_dir,
        previous_version=previous_version,
        new_version=new_version,
        archived_body=archived_body,
        archived_evolutions=archived_evo,
    )


async def adopt_staged_skill_async(
    staging_dir: Path | str,
    *,
    store: EvolutionStore,
    skill_name: Optional[str] = None,
    description: str = "Skill consolidated by skill_train sleep",
) -> AdoptResult:
    """Adopt one skill from staging (legacy single-file or named proposal)."""
    staging_path = Path(staging_dir)
    proposals = list_skill_proposals(staging_path)
    name = (skill_name or "").strip()
    if name and name in proposals:
        return await publish_skill_content_async(
            store,
            name,
            proposals[name],
            staging_dir=str(staging_path),
            description=description,
        )

    manifest = load_manifest(staging_path)
    name = name or str(manifest.get("skill_name") or "").strip()
    if not name:
        raise ValueError("skill_name is required for adopt")
    if name in proposals:
        body = proposals[name]
    elif manifest.get("has_proposed_skill"):
        body = read_proposed_skill(staging_path)
        expected = str(manifest.get("proposed_skill_sha256") or "")
        if expected:
            actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if actual != expected:
                raise ValueError("proposed_SKILL.md sha256 does not match staging manifest")
    else:
        raise ValueError(f"staging {staging_path} has no proposed skill for '{name}'")

    return await publish_skill_content_async(
        store,
        name,
        body,
        staging_dir=str(staging_path),
        description=description,
    )


async def adopt_all_staged_skills_async(
    staging_dir: Path | str,
    *,
    store: EvolutionStore,
    description: str = "Skill consolidated by skill_train sleep",
) -> List[AdoptResult]:
    """Publish every skill proposal present in a staging directory."""
    staging_path = Path(staging_dir)
    proposals = list_skill_proposals(staging_path)
    results: List[AdoptResult] = []
    for name, body in proposals.items():
        results.append(
            await publish_skill_content_async(
                store,
                name,
                body,
                staging_dir=str(staging_path),
                description=description,
            )
        )
    return results


def adopt_staged_skill(
    staging_dir: Path | str,
    *,
    store: EvolutionStore,
    skill_name: Optional[str] = None,
    description: str = "Skill consolidated by skill_train sleep",
) -> AdoptResult:
    """Sync wrapper around :func:`adopt_staged_skill_async`."""
    return _run(
        adopt_staged_skill_async(
            staging_dir,
            store=store,
            skill_name=skill_name,
            description=description,
        )
    )


def adopt_all_staged_skills(
    staging_dir: Path | str,
    *,
    store: EvolutionStore,
    description: str = "Skill consolidated by skill_train sleep",
) -> List[AdoptResult]:
    return _run(
        adopt_all_staged_skills_async(
            staging_dir,
            store=store,
            description=description,
        )
    )


def publish_skill_content(
    store: EvolutionStore,
    skill_name: str,
    content: str,
    *,
    staging_dir: str = "",
    description: str = "Skill consolidated by skill_train sleep",
) -> AdoptResult:
    return _run(
        publish_skill_content_async(
            store,
            skill_name,
            content,
            staging_dir=staging_dir,
            description=description,
        )
    )
