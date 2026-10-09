from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from openjiuwen.harness.personal_context import PersonalContext
from openjiuwen.harness.personal_context import context_pipeline as pipeline
from openjiuwen.harness.personal_context import personal_context as runtime
from openjiuwen.harness.personal_context.models import RawChangeItem
from openjiuwen.harness.personal_context.source_metadata import upsert_source_metadata


def _write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_publication_counts_include_directory_nodes_and_ignore_non_markdown(tmp_path):
    before = pipeline._snapshot_context_nodes(tmp_path)
    _write(tmp_path, "description.md", "# Root\n")
    _write(tmp_path, "topic/description.md", "# Topic\n")
    _write(tmp_path, "topic/document.md", "# Document\nBody")
    _write(tmp_path, "index.json", "{}")
    assert pipeline._count_published_nodes(before, pipeline._snapshot_context_nodes(tmp_path)) == {
        "created_node_count": 3, "updated_node_count": 0, "no_new_content": False,
    }


def test_publication_counts_do_not_call_a_move_new_content(tmp_path):
    _write(tmp_path, "one.md", "# Document\nBody")
    before = pipeline._snapshot_context_nodes(tmp_path)
    (tmp_path / "one.md").rename(tmp_path / "renamed.md")
    result = pipeline._count_published_nodes(before, pipeline._snapshot_context_nodes(tmp_path))
    assert result["created_node_count"] == result["updated_node_count"] == 0
    assert result["no_new_content"] is True


def test_reclustered_page_with_rebased_links_is_updated_not_new(tmp_path):
    root = tmp_path / "context"
    source = upsert_source_metadata(
        tmp_path / "source-meta",
        RawChangeItem(logical_id="one", revision_id="1", operation="upsert", title="Page",
                      content="Body.", original_ref="https://example.test/one"),
        provider="local_files", service_id="notes", observed_at="2026-09-27T00:00:00Z",
    )
    markdown = f"# Page\n\n[来源](../../source-meta/{source}.md)\n\nBody.\n"
    _write(root, "old/page.md", markdown)
    before = pipeline._snapshot_context_nodes(root)
    rewritten = pipeline._rewrite_context_markdown_links(
        markdown, context_root=root, source_root=tmp_path / "source-meta",
        old_page_relative="old/page.md", new_page_relative="new/deep/renamed.md",
        mapping={"old/page.md": "new/deep/renamed.md"},
    )
    assert "../../../source-meta/" in rewritten
    _write(root, "new/deep/renamed.md", rewritten)
    (root / "old/page.md").unlink()
    result = pipeline._count_published_nodes(before, pipeline._snapshot_context_nodes(root))
    assert result["created_node_count"] == 0
    assert result["updated_node_count"] == 1


def test_publication_counts_separate_updated_and_removed_nodes(tmp_path):
    _write(tmp_path, "one.md", "# Document\nBefore")
    before = pipeline._snapshot_context_nodes(tmp_path)
    _write(tmp_path, "one.md", "# Document\nAfter")
    result = pipeline._count_published_nodes(before, pipeline._snapshot_context_nodes(tmp_path))
    assert result == {"created_node_count": 0, "updated_node_count": 1, "no_new_content": False}
    (tmp_path / "one.md").unlink()
    assert pipeline._count_published_nodes(before, {})["no_new_content"] is False


@pytest.mark.parametrize(("old_target", "new_target"), [
    ("../A/description.md", "../B/description.md"),
    ("../../source-meta/src_A.md?ref=a/b#c/d", "../../source-meta/src_B.md?ref=a/b#c/d"),
])
def test_different_link_identity_is_not_mistaken_for_a_move(tmp_path, old_target, new_target):
    _write(tmp_path, "old/page.md", f"# Page\n[Link]({old_target})")
    before = pipeline._snapshot_context_nodes(tmp_path)
    (tmp_path / "old/page.md").unlink()
    _write(tmp_path, "new/page.md", f"# Page\n[Link]({new_target})")
    result = pipeline._count_published_nodes(before, pipeline._snapshot_context_nodes(tmp_path))
    assert result == {"created_node_count": 1, "updated_node_count": 0, "no_new_content": False}


def test_rebased_reference_keeps_query_and_fragment_with_slashes(tmp_path):
    _write(tmp_path, "old/page.md", '# Page\n[Link](<../A/description.md?ref=a/b#c/d> "Title")')
    before = pipeline._snapshot_context_nodes(tmp_path)
    (tmp_path / "old/page.md").unlink()
    _write(tmp_path, "new/deep/page.md", '# Page\n[Link](<../../A/description.md?ref=a/b#c/d> "Title")')
    result = pipeline._count_published_nodes(before, pipeline._snapshot_context_nodes(tmp_path))
    assert result == {"created_node_count": 0, "updated_node_count": 1, "no_new_content": False}


def test_unchanged_link_text_with_new_destination_is_not_a_negative_update(tmp_path):
    _write(tmp_path, "old/page.md", "# Page\n[Link](description.md)")
    before = pipeline._snapshot_context_nodes(tmp_path)
    (tmp_path / "old/page.md").unlink()
    _write(tmp_path, "new/page.md", "# Page\n[Link](description.md)")
    result = pipeline._count_published_nodes(before, pipeline._snapshot_context_nodes(tmp_path))
    assert result == {"created_node_count": 1, "updated_node_count": 0, "no_new_content": False}


def test_tracked_moves_compose_and_include_directory_references(tmp_path):
    _write(tmp_path, "old/description.md", "# Old topic")
    _write(tmp_path, "old/one.md", "# Same page\n[Topic](description.md)")
    _write(tmp_path, "old/two.md", "# Same page\n[Topic](description.md)")
    before = pipeline._snapshot_context_nodes(tmp_path)
    relocations = {}
    pipeline._record_context_relocations(relocations, {
        "old/one.md": "middle/one.md", "old/two.md": "middle/two.md",
        "old/description.md": "middle/description.md",
    })
    pipeline._record_context_relocations(relocations, {
        "middle/one.md": "new/one.md", "middle/two.md": "new/two.md",
        "middle/description.md": "new/description.md",
    })
    (tmp_path / "old").rename(tmp_path / "new")
    _write(tmp_path, "new/description.md", "# New topic")
    assert relocations["old/description.md"] == "new/description.md"
    result = pipeline._count_published_nodes(
        before, pipeline._snapshot_context_nodes(tmp_path), relocations=relocations,
    )
    assert result == {"created_node_count": 0, "updated_node_count": 1, "no_new_content": False}


def test_tracked_move_can_reuse_another_moved_pages_original_path():
    before = {"a.md": ("A", "A"), "b.md": ("B", "B")}
    after = {"c.md": ("A", "A"), "a.md": ("B", "B")}
    result = pipeline._count_published_nodes(before, after, relocations={"a.md": "c.md", "b.md": "a.md"})
    assert result == {"created_node_count": 0, "updated_node_count": 0, "no_new_content": True}


def _config(source: Path, services: tuple[str, ...] = ("notes",)):
    return PersonalContext.Config.from_dict({
        "collection_enabled": True,
        "agent_use_enabled": False,
        "strategy_profile": "rules",
        "fetch_services": [{
            "service_id": name, "provider": "local_files", "enabled": False,
            "interval_seconds": 3600, "time_range": {"mode": "all"},
            "source": {"root_dir": str(source / name)}, "credentials": {},
        } for name in services],
    })


async def _run(core: PersonalContext, name: str = "notes") -> dict:
    accepted = await core.run_fetch(service_id=name)
    await asyncio.gather(*list(core._manual_fetch_tasks.values()))
    return await core.get_fetch_run_status(name, run_id=accepted["runs"][0]["run_id"])


@pytest.mark.asyncio
async def test_real_run_reports_committed_nodes_no_change_empty_and_updated(tmp_path):
    source, home = tmp_path / "input", tmp_path / "runtime"
    _write(source, "notes/note.md", "# Coffee\nCoffee beans need careful roasting.")
    core = PersonalContext(home=home)
    await core.set_configuration(_config(source))
    await core.start_collection()
    try:
        first = await _run(core)
        nodes = list((home / "workspace/context").rglob("*.md"))
        assert first["created_node_count"] == len(nodes) > 0
        assert first["updated_node_count"] == 0
        assert first["no_new_content"] is False
        repeated = await _run(core)
        assert repeated["created_node_count"] == repeated["updated_node_count"] == 0
        assert repeated["no_new_content"] is True
        _write(source, "notes/empty.txt", "")
        empty = await _run(core)
        assert empty["completed_items"] == 1
        assert empty["created_node_count"] == 0
        assert empty["no_new_content"] is True
        _write(source, "notes/note.md", "# Coffee\nCoffee beans need careful roasting and low humidity storage.")
        updated = await _run(core)
        assert updated["created_node_count"] == 0
        assert updated["updated_node_count"] > 0
        assert updated["no_new_content"] is False
        snapshot = await core.snapshot()
        assert snapshot.fetch_run_progress["notes"]["updated_node_count"] == updated["updated_node_count"]
        assert core._read_run_history("notes")[0] == updated
    finally:
        await core.stop_collection()


@pytest.mark.asyncio
async def test_failed_publication_does_not_report_uncommitted_nodes(tmp_path, monkeypatch):
    source = tmp_path / "input"
    _write(source, "notes/note.md", "# Coffee\nCoffee roasting.")
    core = PersonalContext(home=tmp_path / "runtime")
    await core.set_configuration(_config(source))
    await core.start_collection()

    def fail_commit(*_args):
        raise OSError("synthetic commit failure")

    monkeypatch.setattr(pipeline, "_commit_context_tree", fail_commit)
    try:
        failed = await _run(core)
        assert failed["run_state"] == "failed"
        assert failed["created_node_count"] == failed["updated_node_count"] == 0
        assert failed["no_new_content"] is False
    finally:
        await core.stop_collection()


@pytest.mark.asyncio
async def test_existing_page_updated_into_another_directory_is_not_new(tmp_path, monkeypatch):
    source, home = tmp_path / "input", tmp_path / "runtime"
    _write(source, "notes/note.md", "# Coffee\nCoffee beans need careful roasting.")
    core = PersonalContext(home=home)
    await core.set_configuration(_config(source))
    await core.start_collection()
    try:
        await _run(core)
        context_root = home / "workspace/context"
        _write(context_root, "destination/description.md", "# Destination\n")

        async def choose_new_directory(context, **_kwargs):
            return context / "destination"

        monkeypatch.setattr(pipeline, "_rules_update_target_directory", choose_new_directory)
        _write(source, "notes/note.md", "# Coffee\nCoffee beans need careful roasting and dry storage.")
        updated = await _run(core)
        assert updated["run_state"] == "succeeded"
        assert updated["created_node_count"] == 0
        assert updated["updated_node_count"] > 0
    finally:
        await core.stop_collection()


@pytest.mark.asyncio
async def test_stopping_a_real_run_keeps_counts_for_retained_publication(tmp_path, monkeypatch):
    source, home = tmp_path / "input", tmp_path / "runtime"
    _write(source, "notes/note.md", "# Coffee\nCoffee roasting.")
    entered = asyncio.Event()
    original = pipeline.ContextPipelineService._filesystem_with_fallback

    async def pause_first_filesystem(self, *args, **kwargs):
        if not entered.is_set():
            entered.set()
            await asyncio.Future()
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(pipeline.ContextPipelineService, "_filesystem_with_fallback", pause_first_filesystem)
    core = PersonalContext(home=home)
    await core.set_configuration(_config(source))
    await core.start_collection()
    try:
        accepted = await core.run_fetch(service_id="notes")
        await asyncio.wait_for(entered.wait(), timeout=5)
        await core.stop_fetch_run("notes")
        result = await core.get_fetch_run_status("notes", run_id=accepted["runs"][0]["run_id"])
        assert result["run_state"] == "cancelled"
        assert result["created_node_count"] == len(list((home / "workspace/context").rglob("*.md"))) > 0
        assert result["no_new_content"] is False
        assert core._read_run_history("notes")[0] == result
    finally:
        await core.stop_collection()


@pytest.mark.asyncio
async def test_stop_during_committed_worker_does_not_publish_twice_or_lose_counts(tmp_path, monkeypatch):
    source, home = tmp_path / "input", tmp_path / "runtime"
    _write(source, "notes/note.md", "# Coffee\nCoffee roasting.")
    committed, release = threading.Event(), threading.Event()
    original = pipeline._commit_context_tree
    calls = 0

    def commit_then_pause(*args):
        nonlocal calls
        calls += 1
        original(*args)
        committed.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(pipeline, "_commit_context_tree", commit_then_pause)
    core = PersonalContext(home=home)
    await core.set_configuration(_config(source))
    await core.start_collection()
    stop_task = None
    try:
        accepted = await core.run_fetch(service_id="notes")
        assert await asyncio.to_thread(committed.wait, 5)
        stop_task = asyncio.create_task(core.stop_fetch_run("notes"))

        async def wait_for_cancellation():
            # Observe the real task cancellation without replacing its cancel method.
            while not core._pipeline_service._active_event_task.cancelling():  # noqa: ASYNC110
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_cancellation(), timeout=3)
        release.set()
        await stop_task
        result = await core.get_fetch_run_status("notes", run_id=accepted["runs"][0]["run_id"])
        assert result["run_state"] == "cancelled"
        assert result["created_node_count"] == len(list((home / "workspace/context").rglob("*.md"))) > 0
        assert calls == 1
        assert result["no_new_content"] is False
    finally:
        release.set()
        if stop_task is not None:
            await asyncio.gather(stop_task, return_exceptions=True)
        await core.stop_collection()


@pytest.mark.asyncio
async def test_concurrent_services_count_only_their_own_publication(tmp_path):
    source, home = tmp_path / "input", tmp_path / "runtime"
    _write(source, "notes/a.md", "# Coffee\nCoffee beans and roasting.")
    _write(source, "other/b.md", "# Camera\nCamera shutter and exposure.")
    core = PersonalContext(home=home)
    await core.set_configuration(_config(source, ("notes", "other")))
    await core.start_collection()
    try:
        accepted = [await core.run_fetch(service_id=name) for name in ("notes", "other")]
        await asyncio.gather(*list(core._manual_fetch_tasks.values()))
        records = [await core.get_fetch_run_status(item["service_id"], run_id=item["run_id"])
                   for response in accepted for item in response["runs"]]
        assert all(record["run_state"] == "succeeded" for record in records)
        nodes = list((home / "workspace/context").rglob("*.md"))
        assert sum(record["created_node_count"] for record in records) == len(nodes)
    finally:
        await core.stop_collection()


@pytest.mark.asyncio
async def test_history_accepts_optional_result_and_keeps_legacy_unknown(tmp_path):
    core = PersonalContext(home=tmp_path)
    record = {
        **runtime._fetch_run_status("notes", run_state="succeeded"),
        "run_id": "a" * 32, "started_at": "2026-09-27T01:00:00Z", "finished_at": "2026-09-27T01:00:01Z",
    }
    current = {**record, "run_id": "b" * 32,
               "created_node_count": 3, "updated_node_count": 1, "no_new_content": False}
    core._write_run_history("notes", [current, record])
    loaded = core._read_run_history("notes")
    assert loaded[0] == current
    assert "created_node_count" not in loaded[1]
    path = core._run_history_path("notes")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["runs"][0]["created_node_count"] = -1
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(Exception, match="history is invalid"):
        core._read_run_history("notes")
