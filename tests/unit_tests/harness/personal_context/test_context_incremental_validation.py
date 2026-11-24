"""Incremental publication must not inherit unrelated maintenance failures."""

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from openjiuwen.harness.personal_context import context_pipeline as pipeline
from openjiuwen.harness.personal_context import context_usage as usage
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context.config import PersonalContextConfig
from openjiuwen.harness.personal_context.models import FetchBatch, RawChangeItem
from openjiuwen.harness.personal_context.source_metadata import upsert_source_metadata


def _source(home: Path, name: str) -> str:
    return upsert_source_metadata(
        home / "workspace/source-meta",
        RawChangeItem(
            logical_id=name, revision_id="1", operation="upsert", title=name,
            content="Source " + name, original_ref="file:///incremental/" + name,
        ),
        provider="local_files", service_id="local", observed_at="2026-11-24T00:00:00Z",
    )


def _tree(home: Path) -> tuple[Path, str, str]:
    root = home / "workspace/context"
    old_source, new_source = _source(home, "old"), _source(home, "new")
    for topic in ("old", "new"):
        (root / topic).mkdir(parents=True)
    (root / "description.md").write_text(
        "# Root\n[Old](old/description.md)\n[New](new/description.md)\n", encoding="utf-8",
    )
    (root / "old/description.md").write_text("# Old\n", encoding="utf-8")
    (root / "old/orphan.md").write_text(
        f"# Old retained page\n[Source](../../source-meta/{old_source}.md)\n", encoding="utf-8",
    )
    (root / "new/description.md").write_text("# New\n[New](new.md)\n", encoding="utf-8")
    (root / "new/new.md").write_text(
        f"# New page\n[Source](../../source-meta/{new_source}.md)\n", encoding="utf-8",
    )
    return root, old_source, new_source


def _graph(home: Path, root: Path, changed: set[str], **kwargs) -> None:
    pipeline._validate_reference_graph(
        root, final_context_root=root, source_root=home / "workspace/source-meta",
        repairable=False, changed_paths=changed, **kwargs,
    )


@pytest.mark.level0
def test_unrelated_orphan_does_not_block_changed_page(tmp_path):
    root, _, _ = _tree(tmp_path)
    changed = {"new/new.md", "new/description.md", "description.md"}
    pipeline._validate_candidate(root, source_root=tmp_path / "workspace/source-meta", changed_paths=changed)
    _graph(tmp_path, root, changed)
    with pytest.raises(BaseError, match="coverage is incomplete"):
        pipeline._validate_description_coverage(root)
    assert "orphan.md" not in (root / "old/description.md").read_text(encoding="utf-8")


@pytest.mark.level1
@pytest.mark.parametrize("operation", ["delete", "move", "source_chain"])
def test_unchanged_reverse_reference_is_validated(tmp_path, operation):
    root, _, _ = _tree(tmp_path)
    target = root / "new/new.md"
    dependent = root / "old/orphan.md"
    dependent.write_text("# Dependent\n[Next](../new/new.md)\n", encoding="utf-8")
    if operation == "delete":
        target.unlink()
        expected = "reference target is missing"
    elif operation == "move":
        target.rename(root / "new/moved.md")
        expected = "reference target is missing"
    else:
        dependent.write_text("# Dependent\n[Next](second.md)\n", encoding="utf-8")
        (root / "old/second.md").write_text("# Intermediate\n[Next](../new/new.md)\n", encoding="utf-8")
        target.write_text("# No source remains\n", encoding="utf-8")
        expected = "without an atomic source"
    with pytest.raises(BaseError, match=expected) as failure:
        _graph(tmp_path, root, {"new/new.md", "new/moved.md"})
    if operation == "source_chain":
        assert "old/second.md" in str(failure.value)
        assert "old/orphan.md" in str(failure.value)


@pytest.mark.level1
def test_changed_source_is_validated_even_without_body_diff(tmp_path):
    root, old_source, _ = _tree(tmp_path)
    (tmp_path / f"workspace/source-meta/{old_source}.md").unlink()
    with pytest.raises(BaseError, match="source"):
        _graph(tmp_path, root, set(), changed_source_ids={old_source})


@pytest.mark.level0
def test_unrelated_old_reference_error_does_not_expand_from_changed_root(tmp_path):
    root, _, _ = _tree(tmp_path)
    old_page = root / "old/orphan.md"
    old_page.write_text("# Old page\n[Already missing](missing.md)\n", encoding="utf-8")
    _graph(tmp_path, root, {"description.md", "new/new.md", "new/description.md"})
    assert old_page.read_text(encoding="utf-8") == "# Old page\n[Already missing](missing.md)\n"


@pytest.mark.level0
def test_inherited_navigation_is_repaired_before_agent_baseline(tmp_path):
    root, old_source, _ = _tree(tmp_path)
    retained = (root / "old/orphan.md").read_bytes()
    pipeline._repair_inherited_navigation(root, source_ids={old_source})
    assert "orphan.md" in (root / "old/description.md").read_text(encoding="utf-8")
    assert (root / "old/orphan.md").read_bytes() == retained
    baseline = pipeline._snapshot_managed_files(root)
    (root / "old/description.md").unlink()
    with pytest.raises(BaseError, match="removed a description"):
        pipeline._validate_agent_candidate(
            root, baseline=baseline, changed_paths=pipeline._changed_context_paths(root, baseline),
            source_root=tmp_path / "workspace/source-meta",
        )


@pytest.mark.level1
def test_agent_cannot_remove_a_link_repaired_before_its_baseline(tmp_path):
    original, old_source, _ = _tree(tmp_path)
    root = tmp_path / "sandbox/context"
    shutil.copytree(original, root)
    pipeline._repair_inherited_navigation(root, source_ids={old_source})
    baseline = pipeline._snapshot_managed_files(root)
    (root / "old/description.md").write_text("# Old\n", encoding="utf-8")
    with pytest.raises(BaseError, match="coverage is incomplete"):
        pipeline._validate_agent_candidate(
            root, baseline=baseline, changed_paths=pipeline._changed_context_paths(root, baseline),
            baseline_root=original, final_context_root=original, source_root=tmp_path / "workspace/source-meta",
        )
    with pytest.raises(BaseError, match="does not link every direct child"):
        pipeline._validate_reference_graph(
            root, final_context_root=original, source_root=tmp_path / "workspace/source-meta",
            repairable=True, changed_paths=pipeline._changed_context_paths(root, baseline),
            baseline_root=original, baseline=baseline,
        )


@pytest.mark.level1
@pytest.mark.parametrize("inherited", [False, True])
@pytest.mark.parametrize("validator", ["agent", "final", "graph"])
def test_directory_changes_are_checked_without_rejecting_inherited_empty_directory(tmp_path, inherited, validator):
    original, _, _ = _tree(tmp_path)
    if inherited:
        (original / "old/empty-topic").mkdir()
    root = tmp_path / "sandbox/context"
    shutil.copytree(original, root)
    baseline = pipeline._snapshot_managed_files(root)
    if not inherited:
        (root / "old/empty-topic").mkdir()
    with (root / "new/new.md").open("a", encoding="utf-8") as handle:
        handle.write("\nNew fact.\n")
    changed = pipeline._changed_context_paths(root, baseline)

    def validate():
        if validator == "agent":
            pipeline._validate_agent_candidate(
                root, baseline=baseline, changed_paths=changed, baseline_root=original,
                final_context_root=original, source_root=tmp_path / "workspace/source-meta",
            )
        elif validator == "final":
            pipeline._validate_candidate(
                root, changed_paths=changed, final_context_root=original, source_root=tmp_path / "workspace/source-meta",
            )
        else:
            pipeline._validate_reference_graph(
                root, changed_paths=changed, baseline_root=original, baseline=baseline,
                final_context_root=original, source_root=tmp_path / "workspace/source-meta", repairable=True,
            )

    if inherited:
        validate()
    else:
        with pytest.raises(BaseError, match="missing|coverage|direct child"):
            validate()


@pytest.mark.level1
@pytest.mark.parametrize("text", ["# Changed\n[Escape](../../../../secret.md)\n", "# Changed\n[Missing](gone.md)\n"])
def test_changed_page_still_rejects_unsafe_or_missing_targets(tmp_path, text):
    root, _, _ = _tree(tmp_path)
    (root / "new/new.md").write_text(text, encoding="utf-8")
    with pytest.raises(BaseError, match="reference"):
        _graph(tmp_path, root, {"new/new.md"})


@pytest.mark.level1
def test_agent_new_page_does_not_inherit_unrelated_old_omission_in_same_directory(tmp_path):
    original, _, new_source = _tree(tmp_path)
    root = tmp_path / "sandbox/context"
    shutil.copytree(original, root)
    baseline = pipeline._snapshot_managed_files(root)
    (root / "old/added.md").write_text(
        f"# Added\n[Source](../../source-meta/{new_source}.md)\n", encoding="utf-8",
    )
    (root / "old/description.md").write_text("# Old\n[Added](added.md)\n", encoding="utf-8")
    changed = pipeline._changed_context_paths(root, baseline)
    pipeline._validate_agent_candidate(
        root, baseline=baseline, changed_paths=changed, baseline_root=original,
        final_context_root=original, source_root=tmp_path / "workspace/source-meta",
    )
    pipeline._validate_reference_graph(
        root, final_context_root=original, source_root=tmp_path / "workspace/source-meta",
        repairable=True, changed_paths=changed, baseline_root=original, baseline=baseline,
    )
    (root / "new/description.md").write_text("# New\n", encoding="utf-8")
    with pytest.raises(BaseError, match="coverage is incomplete"):
        pipeline._validate_agent_candidate(
            root, baseline=baseline, changed_paths=pipeline._changed_context_paths(root, baseline),
            baseline_root=original, final_context_root=original, source_root=tmp_path / "workspace/source-meta",
        )


@pytest.mark.level0
@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["rules", "balanced", "agent"])
async def test_touched_inherited_navigation_is_repaired_without_renewing_body(tmp_path, monkeypatch, profile):
    root, old_source, _ = _tree(tmp_path)
    usage.read_with_usage(tmp_path, "old/orphan.md", allowed=lambda: True)
    record = usage._load_usage(tmp_path)["pages"]["old/orphan.md"].copy()
    expected_fingerprint = usage._fingerprint(root, "old/orphan.md", {})
    observed_profiles = []
    observed_agent = []

    class Model:
        def __init__(self, **kwargs):
            pass

        async def invoke(self, messages, **kwargs):
            payload = json.loads(messages[0].content.split("\n", 1)[1])
            if "items" in payload:
                return json.dumps({"items": [{
                    "item_index": item["item_index"], "summary": "陶瓷烧制需要控制窑温。", "keywords": ["陶瓷工艺"],
                    "page_title": "陶瓷烧制工艺",
                } for item in payload["items"]]})
            return "{}"

    async def agent(*, sandbox_path, validate_result, **kwargs):
        candidate = sandbox_path / "context"
        assert "orphan.md" in (candidate / "old/description.md").read_text(encoding="utf-8")
        observed_agent.append(True)
        (candidate / "old/added.md").write_text("# New synthesis\n[[ref:0]]\n", encoding="utf-8")
        with (candidate / "old/description.md").open("a", encoding="utf-8") as handle:
            handle.write("\n[New synthesis](added.md)\n")
        assert validate_result("done", sandbox_path) == []
        return "done"

    monkeypatch.setattr(pipeline, "Model", Model)
    monkeypatch.setattr(pipeline, "run_personal_context_agent", agent)
    config = {"strategy_profile": profile, "fetch_services": []}
    if profile != "rules":
        config.update(
            model_client={"client_provider": "OpenAI", "api_key": "test", "api_base": "https://example.test"},
            model_request={"model": "test"},
        )
    queue = asyncio.Queue()
    service = pipeline.ContextPipelineService(
        home=tmp_path, config=PersonalContextConfig.from_dict(config), input_queue=queue,
        profile_callback=lambda _service, _run, actual: observed_profiles.append(actual),
    )
    await service.start()
    try:
        batch = FetchBatch(batch_id="touch-old-source", items=(RawChangeItem(
            logical_id="old", revision_id="2", operation="upsert", title="陶瓷烧制工艺",
            content="陶瓷烧制需要选择黏土并控制窑温。", original_ref="file:///incremental/old",
        ),))
        for stage, item in (("batch", batch), ("finish", None)):
            complete = asyncio.get_running_loop().create_future()
            await queue.put((stage, "local", "touch-old", item, complete))
            await asyncio.wait_for(asyncio.shield(complete), timeout=10)
        assert observed_profiles == [profile]
        assert observed_agent == ([True] if profile == "agent" else [])
        assert "orphan.md" in (root / "old/description.md").read_text(encoding="utf-8")
        assert usage._fingerprint(root, "old/orphan.md", {}) == expected_fingerprint
        current = usage._load_usage(tmp_path)["pages"]["old/orphan.md"]
        assert {key: current[key] for key in ("created_at", "updated_at", "last_read_at")} == {
            key: record[key] for key in ("created_at", "updated_at", "last_read_at")
        }
        assert (tmp_path / f"workspace/source-meta/{old_source}.md").is_file()
    finally:
        await service.stop(timeout_seconds=1)


@pytest.mark.level0
@pytest.mark.asyncio
async def test_normal_pipeline_can_publish_after_interrupted_cleanup(tmp_path):
    root, _, _ = _tree(tmp_path)
    retained = (root / "old/orphan.md").read_text(encoding="utf-8")
    queue = asyncio.Queue()
    service = pipeline.ContextPipelineService(
        home=tmp_path,
        config=PersonalContextConfig.from_dict({"strategy_profile": "rules", "fetch_services": []}),
        input_queue=queue,
    )
    await service.start()
    try:
        batch = FetchBatch(batch_id="new-batch", items=(RawChangeItem(
            logical_id="ceramics", revision_id="1", operation="upsert", title="陶瓷烧制工艺",
            content="陶瓷需要选择黏土并控制窑温。", original_ref="file:///incremental/ceramics.md",
        ),))
        for stage, item in (("batch", batch), ("finish", None)):
            complete = asyncio.get_running_loop().create_future()
            await queue.put((stage, "local", "new-run", item, complete))
            await asyncio.wait_for(asyncio.shield(complete), timeout=10)
        assert (root / "old/orphan.md").is_file()
        assert retained.splitlines()[1] in (root / "old/orphan.md").read_text(encoding="utf-8")
        assert any("陶瓷" in page.read_text(encoding="utf-8") for page in root.rglob("*.md"))
    finally:
        await service.stop(timeout_seconds=1)
