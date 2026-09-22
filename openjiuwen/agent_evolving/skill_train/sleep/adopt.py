# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Adopt staged sleep proposals via EvolutionStore SemVer + changelog."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Sequence

from openjiuwen.agent_evolving.checkpointing.changelog import (
    CHANGELOG_FILENAME,
    classify_records_for_changelog,
)
from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.agent_evolving.skill_train.sleep.evolution_records import edits_to_evolution_records
from openjiuwen.agent_evolving.skill_train.sleep.staging import (
    list_skill_proposal_edits,
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


async def _finalize_version_and_changelog(
    store: EvolutionStore,
    skill_name: str,
    records: Sequence[EvolutionRecord],
) -> Optional[str]:
    """Mirror experience rebuild finalize: bump → changelog → clear entries."""
    new_version = await store.bump_version_for_rebuild(skill_name, entries=list(records))
    if not new_version:
        logger.warning(
            "[skill_sleep] no SemVer bump for skill=%s (empty/skip-only records)",
            skill_name,
        )
        return None
    classified = await classify_records_for_changelog(records)
    if classified:
        written = await store.append_changelog_for_rebuild(skill_name, new_version, classified)
        if not written:
            logger.info(
                "[skill_sleep] changelog unchanged for skill=%s version=%s",
                skill_name,
                new_version,
            )
    await store.clear_evolutions(skill_name, retain_version=new_version)
    return new_version


async def publish_skill_content_async(
    store: EvolutionStore,
    skill_name: str,
    content: str,
    *,
    staging_dir: str = "",
    description: str = "Skill consolidated by skill_train sleep",
    applied_edits: Optional[Sequence[Any]] = None,
) -> AdoptResult:
    """Archive (if exists), write content, bump SemVer, append changelog."""
    name = (skill_name or "").strip()
    if not name:
        raise ValueError("skill_name is required")
    archived_body: Optional[str] = None
    archived_evo: Optional[str] = None
    previous_version = "0.0.0"
    records = edits_to_evolution_records(applied_edits, skill_name=name)

    if store.skill_exists(name):
        previous_version = await store.resolve_current_version(name)
        archived_body, archived_evo = await store.archive_current_state(name)
        # Snapshot live body before overwrite so a failed SemVer bump can roll back.
        prior_content = await store.read_skill_content(name) or ""
        ok = await store.write_skill_content(name, content)
        if not ok:
            raise RuntimeError(f"failed to write skill content for '{name}'")
        try:
            new_version = await _finalize_version_and_changelog(store, name, records)
            if not new_version:
                raise RuntimeError(f"failed to bump skill version for '{name}'")
        except Exception:
            restored = await store.write_skill_content(name, prior_content)
            if restored:
                logger.warning(
                    "[skill_sleep] rolled back skill=%s to prior content after SemVer bump failure",
                    name,
                )
            else:
                logger.error(
                    "[skill_sleep] rollback failed for skill=%s after SemVer bump failure",
                    name,
                )
            raise
    else:
        created = await store.create_skill(name, description, body="")
        if created is None:
            raise RuntimeError(f"failed to create skill '{name}'")
        ok = await store.write_skill_content(name, content)
        if not ok:
            raise RuntimeError(f"failed to write initial skill content for '{name}'")
        previous_version = await store.resolve_current_version(name)
        new_version = await _finalize_version_and_changelog(store, name, records)
        if not new_version:
            # create_skill left a default version; keep it if bump could not run.
            new_version = previous_version

    logger.info(
        "[skill_sleep] published skill=%s version %s -> %s staging=%s changelog=%s",
        name,
        previous_version,
        new_version,
        staging_dir or "-",
        CHANGELOG_FILENAME,
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
    edits_by_skill = list_skill_proposal_edits(staging_path)
    name = (skill_name or "").strip()
    if name and name in proposals:
        return await publish_skill_content_async(
            store,
            name,
            proposals[name],
            staging_dir=str(staging_path),
            description=description,
            applied_edits=edits_by_skill.get(name),
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
        applied_edits=edits_by_skill.get(name),
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
    edits_by_skill = list_skill_proposal_edits(staging_path)
    results: List[AdoptResult] = []
    for name, body in proposals.items():
        results.append(
            await publish_skill_content_async(
                store,
                name,
                body,
                staging_dir=str(staging_path),
                description=description,
                applied_edits=edits_by_skill.get(name),
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
    applied_edits: Optional[Sequence[Any]] = None,
) -> AdoptResult:
    return _run(
        publish_skill_content_async(
            store,
            skill_name,
            content,
            staging_dir=staging_dir,
            description=description,
            applied_edits=applied_edits,
        )
    )
