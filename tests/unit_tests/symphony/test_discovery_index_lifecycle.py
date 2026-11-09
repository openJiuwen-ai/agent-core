from __future__ import annotations
from openjiuwen.symphony.config import RetrievalBuildConfig, RetrievalConfig
"""Verify the shared build/Discovery boundary after legacy retrieval retirement."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.symphony.agent import AgenticSkillRetrievalToolkit, SkillRecord
from openjiuwen.symphony.discovery import InstalledSkillsDirectoryToolkit, SkillFS
from openjiuwen.symphony.discovery.models import SkillRecord as DiscoveryRecord
from openjiuwen.symphony.discovery.skillfs import capture_index_snapshot
from openjiuwen.symphony.retrieval.search.artifacts import load_retriever_index


@pytest.fixture
def built_index(tmp_path):
    records = []
    inventory = []
    for name in ("alpha", "beta", "hidden"):
        directory = tmp_path / "sources" / name
        directory.mkdir(parents=True)
        path = directory / "SKILL.md"
        body = f"# {name}\nProcess invoice documents with {name}.\n"
        path.write_text(body, encoding="utf-8")
        digest = hashlib.sha256(body.encode()).hexdigest()
        records.append(SkillRecord(
            name=name, worker_id=name, description=f"{name} invoice processor",
            skill_md_path=str(path), content=body, content_hash=digest,
        ))
        inventory.append(DiscoveryRecord(
            worker_id=name, name=name, description=f"{name} invoice processor",
            skill_file=str(path), source_root=str(directory), content_hash=digest,
        ))
    create = AsyncMock(return_value=SimpleNamespace(
        content=json.dumps({"skills/documents": ["alpha", "beta", "hidden"]}),
        finish_reason="stop", usage_metadata={"input_tokens": 10, "output_tokens": 5},
    ))
    client = SimpleNamespace(invoke=create, model_name="fixture-model")
    builder = AgenticSkillRetrievalToolkit(
        index_root=tmp_path / "artifacts", skills=records,
        model=client,
        config=RetrievalBuildConfig(task={"allow_fallback_tree": False}),
    )
    result = builder.build_index(force=True)
    assert result["success"] is True, result
    # Inspect the committed files independently from the build response.
    loaded = load_retriever_index(builder.index_dir)
    assert {record.worker_id for record in loaded.catalog_records} == {"alpha", "beta", "hidden"}
    assert all(record.branch_path == ("documents",) for record in loaded.catalog_records)
    commit = json.loads((builder.index_dir / "commit.json").read_text())
    assert set(commit["source_file_hashes"]) == {"alpha", "beta", "hidden"}
    create.assert_called_once()
    assert create.call_args.kwargs["timeout"] == 420.0
    return builder, inventory, create


@pytest.mark.asyncio
async def test_one_shot_build_feeds_discovery_search_visibility_and_pagination(built_index):
    builder, inventory, _ = built_index
    environment = SkillFS(
        lambda: inventory, settings=RetrievalConfig(use_existing_index=True),
        artifact_root=builder.index_root, visible_skill_names={"alpha", "beta"},
        pin_index_revision=True,
    )
    assert environment.artifact.layout == "tree"
    assert environment.artifact.index_state == "fresh"
    assert {entry.worker_id for entry in environment.prompt_snapshot().entries} == {"alpha", "beta"}
    restored = type(environment.pinned_index_snapshot).from_dict(environment.pinned_index_snapshot.to_dict())
    assert restored == environment.pinned_index_snapshot

    toolkit = InstalledSkillsDirectoryToolkit(environment, session_scope="retirement-regression")
    try:
        assert len(toolkit.get_tools()) == 1
        first = await toolkit.skill_index("list", category="documents", limit=1)
        assert first.detailed_output["ok"]
        assert not first.detailed_output["result_complete"]
        cursor = first.detailed_output["next_cursor"]
        second = await toolkit.skill_index("list", cursor=cursor)
        assert second.detailed_output["result_complete"]
        shown = set(first.detailed_output["observed_skill_ids"]) | set(second.detailed_output["observed_skill_ids"])
        assert shown == {"alpha", "beta"}

        result = await toolkit.skill_index("search", query="invoice")
        assert set(result.detailed_output["observed_skill_ids"]) == {"alpha", "beta"}
        assert "invoice" in result.data["content"]
        assert "hidden" not in result.data["content"]
    finally:
        await toolkit.aclose()


def test_discovery_recovers_committed_backup_after_interrupted_publish(built_index):
    builder, inventory, _ = built_index
    expected = capture_index_snapshot(builder.index_root)
    backup = builder.index_root / ".index.backup-regression"
    builder.index_dir.replace(backup)

    environment = SkillFS(
        lambda: inventory, settings=RetrievalConfig(use_existing_index=True),
        artifact_root=builder.index_root,
    )
    assert environment.artifact.layout == "tree"
    assert environment.artifact.index_state == "fresh"
    assert builder.index_dir.is_dir() and not backup.exists()
    assert capture_index_snapshot(builder.index_root) == expected


def test_failed_rebuild_preserves_committed_files_and_discovery(built_index):
    builder, inventory, create = built_index
    previous = {path.name: path.read_bytes() for path in builder.index_dir.iterdir() if path.is_file()}
    create.side_effect = RuntimeError("fixture model unavailable")
    result = builder.build_index(force=True)
    assert not result["success"]
    assert result["data"]["previous_index_preserved"]
    assert {path.name: path.read_bytes() for path in builder.index_dir.iterdir() if path.is_file()} == previous
    environment = SkillFS(
        lambda: inventory, settings=RetrievalConfig(use_existing_index=True), artifact_root=builder.index_root,
    )
    assert environment.artifact.index_state == "fresh"
    assert environment.search_content(inventory, "invoice", case_insensitive=True, fixed_strings=True) == (
        "alpha", "beta", "hidden",
    )


@pytest.mark.asyncio
async def test_missing_index_keeps_live_inventory_search_and_prompt(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("Invoice document processing", encoding="utf-8")
    record = DiscoveryRecord("invoice", "Invoice", "Process invoices", str(path), content_hash="fixture")
    environment = SkillFS(
        lambda: [record], settings=RetrievalConfig(use_existing_index=True), artifact_root=tmp_path / "missing",
    )
    assert environment.artifact.layout == "flat" and environment.artifact.index_state == "missing"
    assert environment.prompt_snapshot().entries[0].worker_id == "invoice"
    toolkit = InstalledSkillsDirectoryToolkit(environment, session_scope="missing-index")
    try:
        result = await toolkit.skill_index("search", query="Invoice")
        assert result.detailed_output["observed_skill_ids"] == ["invoice"]
        assert "Process invoices" in result.data["content"]
    finally:
        await toolkit.aclose()
