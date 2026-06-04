# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord
from openjiuwen.agent_evolving.sharing import (
    LocalFileBackend,
    QueryKeywords,
    SharedExperience,
    ShareStager,
    SharingMeta,
    SkillPackageMeta,
    ensure_skill_id_in_content,
    pack_skill_directory,
)
from openjiuwen.agent_evolving.sharing.experience_sharer import ExperienceSharer
from openjiuwen.agent_evolving.sharing.hub_client import ExperienceHubClient
from openjiuwen.agent_evolving.sharing.keyword_extractor import KeywordExtractor
from openjiuwen.agent_evolving.sharing.types import SharedSkillBundle
from openjiuwen.agent_evolving.signal.base import EvolutionTarget


def _make_record(*, score: float = 0.8, source: str = "user_correction") -> EvolutionRecord:
    patch = EvolutionPatch(
        section="Troubleshooting",
        action="append",
        content="## Fix\n- check bounds",
        target=EvolutionTarget.BODY,
        keywords=["IndexError", "bounds"],
        summary="check loop upper bound",
    )
    return EvolutionRecord.make(source=source, context="ctx", change=patch, score=score)


def _write_skill_dir(base: Path, name: str, *, body: str = "# Skill\n") -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\n{body}",
        encoding="utf-8",
    )
    scripts = skill_dir / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "helper.py").write_text("print('ok')\n", encoding="utf-8")
    return skill_dir


@pytest.mark.asyncio
async def test_local_file_backend_upload_and_download(tmp_path):
    backend = LocalFileBackend(hub_path=tmp_path / "hub")
    skill_id = "sk_testupload01"
    record = _make_record()
    shared = SharedExperience(
        record=record,
        keywords=["IndexError", "bounds"],
        summary="check loop upper bound",
        sharing_meta=SharingMeta(skill_name="python-debug"),
    )
    bundle = SharedSkillBundle.make(skill_name="python-debug", experiences=[shared])
    bundle.skill_id = skill_id
    package_bytes = pack_skill_directory(_write_skill_dir(tmp_path, "python-debug"))
    await backend.upload_skill_package(
        skill_id,
        package_bytes,
        SkillPackageMeta(skill_id=skill_id, skill_name="python-debug", description="debug"),
    )
    upload_result = await backend.upload_bundle(bundle)
    assert upload_result.ok is True
    assert upload_result.bundle_id == bundle.bundle_id

    results = await backend.download_bundles(
        skill_id,
        QueryKeywords(keywords=["IndexError", "bounds"], intent="debug"),
        top_k=3,
    )
    assert len(results) == 1
    assert results[0].bundle_id == bundle.bundle_id


@pytest.mark.asyncio
async def test_different_skill_ids_do_not_collide(tmp_path):
    backend = LocalFileBackend(hub_path=tmp_path / "hub")

    skill_id_a = "sk_testskillid01"
    skill_id_b = "sk_testskillid02"

    bundle_a = SharedSkillBundle.make(
        skill_name="ppt-creator",
        experiences=[
            SharedExperience(
                record=_make_record(),
                keywords=["layout", "slide"],
                summary="fix slide layout",
            )
        ],
    )
    bundle_a.skill_id = skill_id_a

    bundle_b = SharedSkillBundle.make(
        skill_name="ppt-creator",
        experiences=[
            SharedExperience(
                record=_make_record(source="execution_failure"),
                keywords=["font", "theme"],
                summary="fix font theme",
            )
        ],
    )
    bundle_b.skill_id = skill_id_b

    assert (await backend.upload_bundle(bundle_a)).ok is True
    assert (await backend.upload_bundle(bundle_b)).ok is True

    results_a = await backend.download_bundles(
        skill_id_a,
        QueryKeywords(keywords=["layout", "slide"], intent="ppt"),
        top_k=3,
    )
    results_b = await backend.download_bundles(
        skill_id_b,
        QueryKeywords(keywords=["font", "theme"], intent="ppt"),
        top_k=3,
    )

    assert len(results_a) == 1
    assert len(results_b) == 1
    assert results_a[0].bundle_id == bundle_a.bundle_id
    assert results_b[0].bundle_id == bundle_b.bundle_id


@pytest.mark.asyncio
async def test_skill_package_upload_is_immutable(tmp_path):
    backend = LocalFileBackend(hub_path=tmp_path / "hub")
    skill_id = "sk_immutable001"
    first = pack_skill_directory(_write_skill_dir(tmp_path, "demo-a", body="# A\n"))
    second = pack_skill_directory(_write_skill_dir(tmp_path, "demo-b", body="# B\n"))
    meta = SkillPackageMeta(skill_id=skill_id, skill_name="demo", description="demo")

    await backend.upload_skill_package(skill_id, first, meta)
    await backend.upload_skill_package(skill_id, second, meta)

    downloaded = await backend.download_skill_package(skill_id)
    assert downloaded == first


@pytest.mark.asyncio
async def test_local_file_backend_rejects_duplicate_on_upload(tmp_path):
    backend = LocalFileBackend(hub_path=tmp_path / "hub")
    skill_id = "sk_duplicate001"
    record = _make_record()
    bundle = SharedSkillBundle.make(
        skill_name="python-debug",
        experiences=[
            SharedExperience(
                record=record,
                keywords=["IndexError", "bounds", "loop"],
                summary="check loop upper bound",
            )
        ],
    )
    bundle.skill_id = skill_id
    first = await backend.upload_bundle(bundle)
    assert first.ok is True

    duplicate_bundle = SharedSkillBundle.make(
        skill_name="python-debug",
        experiences=[
            SharedExperience(
                record=_make_record(),
                keywords=["IndexError", "bounds", "loop"],
                summary="another attempt",
            )
        ],
    )
    duplicate_bundle.skill_id = skill_id
    rejected = await backend.upload_bundle(duplicate_bundle)
    assert rejected.ok is False
    assert "overlap existing bundle" in rejected.reason


@pytest.mark.asyncio
async def test_experience_sharer_reports_duplicate_on_flush(tmp_path):
    backend = LocalFileBackend(hub_path=tmp_path / "hub")
    skills_root = tmp_path / "skills"
    _write_skill_dir(skills_root, "python-debug", body="# Skill\n")
    store = EvolutionStore(str(skills_root))

    skill_id = await store.ensure_skill_id("python-debug")
    existing = _make_record()
    bundle = SharedSkillBundle.make(
        skill_name="python-debug",
        experiences=[
            SharedExperience(
                record=existing,
                keywords=["IndexError", "bounds", "loop"],
                summary="check loop upper bound",
            )
        ],
    )
    bundle.skill_id = skill_id
    assert (await backend.upload_bundle(bundle)).ok is True

    async def _provider(skill_name: str):
        sid = await store.ensure_skill_id(skill_name)
        package = await store.pack_skill_for_sharing(skill_name)
        content = await store.read_pristine_skill_content(skill_name)
        description = store.extract_description_from_skill_md(content)
        return sid, package, skill_name, description

    sharer = ExperienceSharer(
        backend=backend,
        local_cache_dir=None,
        skill_sharing_context_provider=_provider,
    )
    stager = ShareStager(keyword_extractor=KeywordExtractor(), sharer=sharer)

    duplicate = EvolutionRecord.make(
        source="user_correction",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content="## Fix\n- check bounds again",
            target=EvolutionTarget.BODY,
            keywords=["IndexError", "bounds", "loop"],
            summary="check loop upper bound",
        ),
        score=0.8,
    )
    staged = await stager.screen_and_stage(
        skill_name="python-debug",
        records=[duplicate],
        messages=None,
    )
    assert staged.has_shareable

    upload_result = await sharer.flush_pending_uploads("python-debug")
    assert upload_result.ok is False
    assert "overlap existing bundle" in upload_result.reason


@pytest.mark.asyncio
async def test_experience_sharer_uploads_initial_package_once(tmp_path):
    backend = LocalFileBackend(hub_path=tmp_path / "hub")
    skills_root = tmp_path / "skills"
    _write_skill_dir(skills_root, "python-debug")
    store = EvolutionStore(str(skills_root))

    async def _provider(skill_name: str):
        sid = await store.ensure_skill_id(skill_name)
        package = await store.pack_skill_for_sharing(skill_name)
        content = await store.read_pristine_skill_content(skill_name)
        description = store.extract_description_from_skill_md(content)
        return sid, package, skill_name, description

    sharer = ExperienceSharer(backend=backend, skill_sharing_context_provider=_provider)
    stager = ShareStager(keyword_extractor=KeywordExtractor(), sharer=sharer)

    record = _make_record()
    staged = await stager.screen_and_stage(
        skill_name="python-debug",
        records=[record],
        messages=None,
    )
    assert staged.has_shareable

    result = await sharer.flush_pending_uploads("python-debug")
    assert result.ok is True

    skill_id = await store.ensure_skill_id("python-debug")
    assert await backend.has_skill_package(skill_id)
    package = await backend.download_skill_package(skill_id)
    assert package


@pytest.mark.asyncio
async def test_search_skills_and_install(tmp_path):
    backend = LocalFileBackend(hub_path=tmp_path / "hub")
    publisher_root = tmp_path / "publisher"
    _write_skill_dir(publisher_root, "ppt-creator", body="# PPT Creator\nMake slides.\n")
    publisher_store = EvolutionStore(str(publisher_root))

    skill_id = await publisher_store.ensure_skill_id("ppt-creator")
    package = await publisher_store.pack_skill_for_sharing("ppt-creator")
    await backend.upload_skill_package(
        skill_id,
        package,
        SkillPackageMeta(
            skill_id=skill_id,
            skill_name="ppt-creator",
            description="Create presentations",
        ),
    )
    bundle = SharedSkillBundle.make(
        skill_name="ppt-creator",
        experiences=[
            SharedExperience(
                record=_make_record(),
                keywords=["ppt", "slide", "layout"],
                summary="fix slide layout",
            )
        ],
    )
    bundle.skill_id = skill_id
    assert (await backend.upload_bundle(bundle)).ok is True

    installer_root = tmp_path / "installer"
    installer_store = EvolutionStore(str(installer_root))
    client = ExperienceHubClient(backend, installer_store)

    results = await client.search_skills(
        QueryKeywords(keywords=["ppt", "slide"], intent="presentation"),
        top_k=3,
    )
    assert len(results) == 1
    assert results[0].skill_id == skill_id

    installed = await client.install_skill(skill_id)
    assert installed is not None
    assert (installed / "SKILL.md").is_file()
    assert (installed / "scripts" / "helper.py").is_file()
    installed_id = await installer_store.read_skill_id("ppt-creator")
    assert installed_id == skill_id


@pytest.mark.asyncio
async def test_share_stager_drops_execution_failure_without_successful_tool(tmp_path):
    backend = LocalFileBackend(hub_path=tmp_path / "hub")
    sharer = ExperienceSharer(backend=backend, local_cache_dir=None)
    stager = ShareStager(keyword_extractor=KeywordExtractor(), sharer=sharer)

    record = _make_record(source="execution_failure", score=0.8)
    failed_only = [
        {"role": "user", "content": "run it"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "tc_1", "name": "bash", "arguments": "{}"}],
        },
        {
            "role": "tool",
            "tool_call_id": "tc_1",
            "name": "bash",
            "content": "Error: command failed with exit code 1",
        },
    ]
    result = await stager.screen_and_stage(
        skill_name="python-debug",
        records=[record],
        messages=failed_only,
    )
    assert not result.has_shareable
    assert "execution failure without successful follow-up tool call" in result.dropped_for_share[0][1]


def test_ensure_skill_id_in_content_adds_frontmatter_field():
    content = "---\nname: demo\ndescription: d\n---\n\n# Body\n"
    updated, skill_id = ensure_skill_id_in_content(content)
    assert skill_id.startswith("sk_")
    assert f"skill_id: {skill_id}" in updated


def test_keyword_extractor_parse_from_patch():
    patch = EvolutionPatch(
        section="Troubleshooting",
        action="append",
        content="body",
        keywords=["a", "b"],
        summary="one line",
    )
    keywords, summary = KeywordExtractor.parse_from_optimizer_output(patch)
    assert keywords == ["a", "b"]
    assert summary == "one line"
