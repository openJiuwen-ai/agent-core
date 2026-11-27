from __future__ import annotations

import asyncio
import hashlib
import os
import threading
import uuid
from contextlib import nullcontext
from pathlib import Path

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.harness.personal_context import context_capacity as capacity
from openjiuwen.harness.personal_context import context_pipeline as pipeline
from openjiuwen.harness.personal_context import context_merge as merge
from openjiuwen.harness.personal_context.context_merge import merge_context_candidate
from openjiuwen.harness.personal_context.context_retirement import active_sandbox_ids, retire_page
from openjiuwen.harness.personal_context.context_retirement import register_sandbox, release_sandbox
from openjiuwen.harness.personal_context.context_usage import context_update_lock
from tests.unit_tests.harness.personal_context.test_context_pipeline_profiles import (
    _batch,
    _config,
    _write_atomic_source,
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _trees(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    live, source = tmp_path / "workspace/context", tmp_path / "workspace/source-meta"
    for root in (baseline, candidate, live):
        _write(root, "notes/old.md", "# Old\n\nOriginal knowledge.\n")
    return baseline, candidate, live, source


def test_unchanged_live_tree_skips_sandbox_hashes_and_body_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    hash_file = pipeline._hash_file
    hashed = []

    def record_hash(path: Path) -> str:
        hashed.append(path)
        return hash_file(path)

    def fail_read(*args, **kwargs):
        raise AssertionError("unchanged live Context must not load Markdown bodies")

    monkeypatch.setattr(Path, "read_text", fail_read)
    monkeypatch.setattr(pipeline, "_hash_file", record_hash)
    prepared = merge.prepare_context_merge(baseline, candidate)
    assert not merge_context_candidate(
        baseline, candidate, live, source_root=source, relocations={}, prepared=prepared,
    )
    assert hashed == [baseline / "notes/old.md", live / "notes/old.md"]


def test_live_change_with_same_size_and_timestamp_is_merged(tmp_path: Path) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    target = live / "notes/old.md"
    before = target.stat()
    _write(live, "notes/old.md", "# Old\n\nModified knowledge.\n")
    assert target.stat().st_size == before.st_size
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert merge_context_candidate(baseline, candidate, live, source_root=source, relocations={})
    assert "Modified knowledge" in (candidate / "notes/old.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("edit", ["none", "related", "move", "knowledge"])
def test_deleted_page_only_survives_its_own_knowledge_edit(tmp_path: Path, edit: str) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    relocations = {}
    target = "notes/old.md"
    if edit == "related":
        _write(candidate, target, "# Old\n\nOriginal knowledge.\n\n"
               "<!-- personal-context-related:start -->\nAutomatically related.\n"
               "<!-- personal-context-related:end -->\n")
    elif edit == "move":
        target = "moved/old.md"
        _write(candidate, target, (candidate / "notes/old.md").read_text(encoding="utf-8"))
        (candidate / "notes/old.md").unlink()
        relocations["notes/old.md"] = target
    elif edit == "knowledge":
        _write(candidate, target, "# Old\n\nNew knowledge from this run.\n")
    (live / "notes/old.md").unlink()

    merge_context_candidate(baseline, candidate, live, source_root=source, relocations=relocations)

    assert (candidate / target).exists() is (edit == "knowledge")


def test_unreported_unchanged_move_does_not_revive_deleted_page(tmp_path: Path) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    (candidate / "notes/old.md").rename(candidate / "notes/renamed.md")
    (live / "notes/old.md").unlink()
    merge_context_candidate(baseline, candidate, live, source_root=source, relocations={})
    assert not (candidate / "notes/renamed.md").exists()


@pytest.mark.parametrize(("original", "edited"), [
    ("# Snippet\n\n```yaml\nroles:\n  admin: true\n```\n",
     "# Snippet\n\n```yaml\nroles:\nadmin: true\n```\n"),
    ("# Identifier\n\n名称为ＡＢＣ。\n", "# Identifier\n\n名称为ABC。\n"),
])
def test_real_knowledge_characters_and_code_indentation_survive_retirement(
    tmp_path: Path, original: str, edited: str,
) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    _write(baseline, "notes/old.md", original)
    _write(candidate, "notes/old.md", edited)
    (live / "notes/old.md").unlink()
    merge_context_candidate(baseline, candidate, live, source_root=source, relocations={})
    assert (candidate / "notes/old.md").read_text(encoding="utf-8") == edited


def test_move_with_only_rebased_source_link_does_not_revive_retired_body(tmp_path: Path) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    source_id = _write_atomic_source(source)
    _write(baseline, "notes/old.md", f"# Old\n\nKnowledge [source](../../source-meta/{source_id}.md).\n")
    _write(candidate, "archive/nested/old.md",
           f"# Old\n\nKnowledge [source](../../../source-meta/{source_id}.md).\n")
    (candidate / "notes/old.md").unlink()
    (live / "notes/old.md").unlink()
    merge_context_candidate(
        baseline, candidate, live, source_root=source,
        relocations={"notes/old.md": "archive/nested/old.md"},
    )
    assert not (candidate / "archive/nested/old.md").exists()


def test_automatic_related_block_removal_does_not_revive_retired_body(tmp_path: Path) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    original = "# Old\n\nOriginal knowledge.\n"
    with_related = pipeline._replace_managed_block(
        original, start=pipeline._RELATED_START, end=pipeline._RELATED_END,
        body="Automatically related content.", default_heading="Old",
    )
    _write(baseline, "notes/old.md", with_related)
    without_related = pipeline._replace_managed_block(
        with_related, start=pipeline._RELATED_START, end=pipeline._RELATED_END,
        body=None, default_heading="Old",
    )
    _write(candidate, "notes/old.md", without_related)
    (live / "notes/old.md").unlink()
    merge_context_candidate(baseline, candidate, live, source_root=source, relocations={})
    assert not (candidate / "notes/old.md").exists()


def test_live_new_directory_and_crosslinks_survive_merge(tmp_path: Path) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    source_id = _write_atomic_source(source)
    for root in (baseline, candidate, live):
        _write(root, "notes/old.md", f"# Old\n\nOriginal [source](../../source-meta/{source_id}.md).\n")
        pipeline._render_context_navigation(root)
    _write(live, "new/topic.md", "# Topic\n\nNew knowledge from [detail](detail.md).\n")
    _write(live, "new/detail.md", f"# Detail\n\nNew [source](../../source-meta/{source_id}.md).\n")
    pipeline._render_context_navigation(live)

    merge_context_candidate(baseline, candidate, live, source_root=source, relocations={})

    assert (candidate / "new/topic.md").read_bytes() == (live / "new/topic.md").read_bytes()
    assert (candidate / "new/detail.md").read_bytes() == (live / "new/detail.md").read_bytes()
    pipeline._validate_reference_graph(candidate, final_context_root=live, source_root=source, repairable=False)


@pytest.mark.parametrize("managed", [False, True])
def test_unreported_move_matches_identity_before_reused_old_path(tmp_path: Path, managed: bool) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    source_id = _write_atomic_source(source)
    original = f"# Old\n\nOriginal [source](../../source-meta/{source_id}.md).\n"
    if managed:
        original += f"\n<!-- personal-context-managed-source: {source_id} -->\n"
    for root in (baseline, candidate, live):
        _write(root, "notes/old.md", original)
        pipeline._render_context_navigation(root)
    _write(candidate, "notes/renamed.md", original)
    replacement = f"# New\n\nFresh knowledge [source](../../source-meta/{source_id}.md).\n"
    _write(candidate, "notes/old.md", replacement)
    pipeline._render_context_navigation(candidate)
    (live / "notes/old.md").unlink()
    pipeline._render_context_navigation(live)

    merge_context_candidate(baseline, candidate, live, source_root=source, relocations={})

    assert not (candidate / "notes/renamed.md").exists()
    assert (candidate / "notes/old.md").read_text(encoding="utf-8") == replacement
    pipeline._validate_reference_graph(candidate, final_context_root=live, source_root=source, repairable=False)


@pytest.mark.parametrize("marked_count", [0, 1, 2])
@pytest.mark.parametrize("edit_after_move", [False, True])
@pytest.mark.parametrize("other", ["notes/other.md", "notes/sub/other.md"])
def test_retirement_follows_page_identity_when_same_text_pages_swap_paths(
    tmp_path: Path, marked_count: int, edit_after_move: bool, other: str,
) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    old = "notes/old.md"
    source_ids = [_write_atomic_source(source, locator=f"file:///{name}") for name in (old, other)]

    def body(index: int, relative: str) -> str:
        source_id = source_ids[index]
        link = os.path.relpath(source / f"{source_id}.md", (live / relative).parent).replace(os.sep, "/")
        marker = f"<!-- personal-context-managed-source: {source_id} -->" if index < marked_count else ""
        return f"# Note\n\nKnowledge [source]({link}).\n\n{marker}\n"

    for index, name in enumerate((old, other)):
        for root in (baseline, live):
            _write(root, name, body(index, name))
    moved = body(0, other) + ("\nNewly verified fact.\n" if edit_after_move else "")
    _write(candidate, old, body(1, old))
    _write(candidate, other, moved)
    (live / old).unlink()

    merge_context_candidate(baseline, candidate, live, source_root=source, relocations={})

    assert (candidate / old).read_text(encoding="utf-8") == body(1, old)
    if edit_after_move:
        assert (candidate / other).read_text(encoding="utf-8") == moved
    else:
        assert not (candidate / other).exists()


@pytest.mark.parametrize(("edit_after_move", "known_moves"), [(False, False), (True, False), (False, True)])
def test_retirement_with_identical_unmarked_pages_keeps_ambiguous_sandbox_work(
    tmp_path: Path, edit_after_move: bool, known_moves: bool,
) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    source_id = _write_atomic_source(source)
    body = f"# Note\n\nShared knowledge [source](../../source-meta/{source_id}.md).\n"
    old, other = "notes/old.md", "notes/other.md"
    for root in (baseline, candidate, live):
        for relative in (old, other):
            _write(root, relative, body)
    temporary = candidate / "notes/temp.md"
    (candidate / old).rename(temporary)
    (candidate / other).rename(candidate / old)
    temporary.rename(candidate / other)
    moved = body + ("\nNew fact.\n" if edit_after_move else "")
    _write(candidate, other, moved)
    (live / old).unlink()

    merge_context_candidate(baseline, candidate, live, source_root=source,
                            relocations={old: other, other: old} if known_moves else {})

    if known_moves or edit_after_move:
        assert (candidate / old).read_text(encoding="utf-8") == body
    else:
        assert not (candidate / old).exists()
    if known_moves:
        assert not (candidate / other).exists()
    else:
        assert (candidate / other).read_text(encoding="utf-8") == moved


def test_cleanup_of_retired_link_keeps_survivor_even_when_it_now_equals_retired_page(tmp_path: Path) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    source_id = _write_atomic_source(source)
    retired = f"# Note\n\nRead more. [source](../../source-meta/{source_id}.md)\n"
    survivor = retired.replace("more.", "[more](old.md).")
    for root in (baseline, candidate, live):
        _write(root, "notes/old.md", retired)
        _write(root, "notes/survivor.md", survivor)
    (live / "notes/old.md").unlink()
    _write(live, "notes/survivor.md", retired)

    merge_context_candidate(baseline, candidate, live, source_root=source, relocations={})

    assert not (candidate / "notes/old.md").exists()
    published = (candidate / "notes/survivor.md").read_text(encoding="utf-8")
    assert f"Read more. [source](../../source-meta/{source_id}.md)" in published
    assert "[more](old.md)" not in published
    assert pipeline._source_ids_reachable_from_page(
        candidate, final_context_root=live, source_root=source, page_relative="notes/survivor.md",
    ) == {source_id}
    pipeline._validate_reference_graph(candidate, final_context_root=live, source_root=source, repairable=False)


@pytest.mark.parametrize("update_related", [False, True])
def test_retiring_a_page_keeps_its_same_text_neighbor_at_its_own_path(
    tmp_path: Path, update_related: bool,
) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    for name in ("old", "survivor"):
        source_id = _write_atomic_source(source, locator=f"file:///{name}.md")
        body = f"# Note\n\nKnowledge [source](../../source-meta/{source_id}.md).\n"
        for root in (baseline, candidate, live):
            _write(root, f"notes/{name}.md", body)
    (live / "notes/old.md").unlink()
    survivor = "notes/survivor.md"
    if update_related:
        _write(live, survivor, pipeline._replace_managed_block(
            (live / survivor).read_text(encoding="utf-8"), start=pipeline._RELATED_START,
            end=pipeline._RELATED_END, body="Related material.", default_heading="Note",
        ))
    _write(candidate, "notes/new.md", "# New\n\nUnrelated new knowledge.\n")
    relocations = {}

    merge_context_candidate(baseline, candidate, live, source_root=source, relocations=relocations)

    assert not (candidate / "notes/old.md").exists()
    assert (candidate / survivor).read_bytes() == (live / survivor).read_bytes()
    assert (candidate / "notes/new.md").read_text(encoding="utf-8") == "# New\n\nUnrelated new knowledge.\n"
    assert not relocations


def test_unrelated_retirement_keeps_valid_sandbox_maintenance(tmp_path: Path) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    for root in (baseline, candidate, live):
        _write(root, "notes/removed.md", "# Removed\n\nOther knowledge.\n")
    _write(candidate, "notes/new.md", "# New\n\nNew knowledge.\n")
    _write(candidate, "notes/old.md", "# Old\n\nOriginal knowledge.\n\n"
           "<!-- personal-context-related:start -->\n[New](new.md)\n"
           "<!-- personal-context-related:end -->\n")
    (live / "notes/removed.md").unlink()
    merge_context_candidate(baseline, candidate, live, source_root=source, relocations={})
    assert "[New](new.md)" in (candidate / "notes/old.md").read_text(encoding="utf-8")
    assert not (candidate / "notes/removed.md").exists()


@pytest.mark.parametrize("sandbox_edit", [False, True])
def test_live_knowledge_is_kept_unless_sandbox_also_edits_it(tmp_path: Path, sandbox_edit: bool) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    _write(live, "notes/old.md", "# Old\n\nExternal edit.\n")
    _write(live, "notes/new.md", "# New\n\nExternal new knowledge.\n")
    if sandbox_edit:
        _write(candidate, "notes/old.md", "# Old\n\nSandbox edit.\n")
    merge_context_candidate(baseline, candidate, live, source_root=source, relocations={})
    assert (candidate / "notes/old.md").read_text(encoding="utf-8") == (
        "# Old\n\nSandbox edit.\n" if sandbox_edit else "# Old\n\nExternal edit.\n"
    )
    assert "External new knowledge" in (candidate / "notes/new.md").read_text(encoding="utf-8")


def test_deleted_links_preserve_text_and_verifiable_source_chain(tmp_path: Path) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    source_id = _write_atomic_source(source)
    for root in (baseline, candidate, live):
        _write(root, "notes/old.md",
               f"# Old\n\n[Retired](previous.md). [Source](../../source-meta/{source_id}.md)\n")
        _write(root, "notes/kept.md", "# Kept\n\nRead [old knowledge](old.md).\n")
    _write(candidate, "notes/kept.md", "# Kept\n\nUpdated knowledge cites [old knowledge](old.md).\n")
    (live / "notes/old.md").unlink()
    _write(live, "notes/kept.md", "# Kept\n\nRead old knowledge.\n")

    merge_context_candidate(baseline, candidate, live, source_root=source, relocations={})

    assert not (candidate / "notes/old.md").exists()
    markdown = (candidate / "notes/kept.md").read_text(encoding="utf-8")
    assert "Updated knowledge cites old knowledge." in markdown
    assert source_id in markdown
    pipeline._validate_reference_graph(
        candidate, final_context_root=live, source_root=source, repairable=False,
    )


def test_merge_keeps_live_body_with_an_existing_missing_reference(tmp_path: Path) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    source_id = _write_atomic_source(source)
    for root in (baseline, candidate, live):
        _write(root, "notes/old.md", f"# Old\n\n[Source](../../source-meta/{source_id}.md)\n")
        pipeline._render_context_navigation(root)
    _write(live, "notes/new.md", "# Live knowledge\n\n[Old](old.md) and [Retired](absent.md).\n")
    pipeline._render_context_navigation(live)

    assert merge_context_candidate(baseline, candidate, live, source_root=source, relocations={})
    assert (candidate / "notes/new.md").read_bytes() == (live / "notes/new.md").read_bytes()
    pipeline._validate_reference_graph(
        candidate, final_context_root=live, source_root=source, repairable=False, changed_paths={"notes/new.md"},
    )


@pytest.mark.asyncio
async def test_unrelated_live_change_cannot_hide_a_target_deleted_by_the_sandbox(tmp_path: Path) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    source_id = _write_atomic_source(source)
    for root in (baseline, candidate, live):
        _write(root, "notes/old.md", f"# Old\n[Source](../../source-meta/{source_id}.md)\n")
        _write(root, "notes/kept.md", "# Kept\n[Old](old.md)\n")
        pipeline._render_context_navigation(root)
    (candidate / "notes/old.md").unlink()
    pipeline._render_context_navigation(candidate)
    _write(live, "notes/new.md", "# Concurrent page\n[Old](old.md)\n")
    pipeline._render_context_navigation(live)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    candidate.rename(sandbox / "context")
    service = pipeline.ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    with pytest.raises(BaseError, match="reference target is missing"):
        await service._publish_processed(
            service_id="local", run_id="run", batch=_batch(),
            processed={"_filesystem_candidate_prepared": True, "_filesystem_candidate_profile": "rules",
                       "_agent_changed_context_paths": {"notes/old.md"}},
            sandbox=sandbox, alias_targets={}, baseline_root=baseline,
        )
    assert (live / "notes/old.md").exists()


@pytest.mark.parametrize("bad_link", ["../../../../outside.md", "../../source-meta/src_" + "f" * 32 + ".md"])
def test_merge_does_not_accept_invalid_source_or_path(tmp_path: Path, bad_link: str) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    _write(candidate, "notes/new.md", f"# New\n\nKnowledge [bad]({bad_link}).\n")
    (live / "notes/old.md").unlink()
    with pytest.raises(BaseError):
        merge_context_candidate(baseline, candidate, live, source_root=source, relocations={})


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", [False, True])
async def test_finish_publishes_against_live_tree_and_cleans_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edit: bool,
) -> None:
    service = pipeline.ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    await service._process_batch_event("local", "first", _batch())
    await service._finish_run_event("local", "first")
    live = tmp_path / "workspace/context"
    old = next(page for page in pipeline._context_ordinary_pages(live))
    relative = old.relative_to(live).as_posix()
    next_batch = _batch(content="Fresh knowledge.", original_ref="file:///notes/two")
    await service._process_batch_event("local", "second", next_batch)
    prepared, proceed = asyncio.Event(), asyncio.Event()
    original = service._filesystem_with_fallback
    captured: list[Path] = []

    async def pause_after_arranging(**kwargs):
        captured.append(kwargs["baseline_root"])
        result = await original(**kwargs)
        candidate = kwargs["sandbox"] / "context"
        old_candidate = next(
            page for page in pipeline._context_ordinary_pages(candidate)
            if "First paragraph." in page.read_text(encoding="utf-8")
        )
        if edit:
            with old_candidate.open("a", encoding="utf-8") as handle:
                handle.write("\nAdditional knowledge from this run.\n")
        prepared.set()
        await proceed.wait()
        return result

    monkeypatch.setattr(service, "_filesystem_with_fallback", pause_after_arranging)
    completion = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(service._process_queue_item(("finish", "local", "second", None, completion)))
    await asyncio.wait_for(prepared.wait(), timeout=5)
    assert captured[0].is_relative_to(tmp_path / "state")
    assert not captured[0].is_relative_to(tmp_path / "workspace/sandboxes")
    async with context_update_lock(tmp_path):
        receipt = retire_page(tmp_path, relative)
        pipeline._render_context_navigation(live)
    assert Path(receipt["body_path"]).is_file()
    assert (captured[0] / relative).exists()
    proceed.set()
    await asyncio.wait_for(task, timeout=10)
    pages = [page.read_text(encoding="utf-8") for page in pipeline._context_ordinary_pages(live)]
    assert any("Additional knowledge" in text for text in pages) is edit
    assert any("First paragraph." in text for text in pages) is edit
    capacity.maintain_capacity(tmp_path, limit_bytes=1_000_000)
    assert not captured[0].exists()
    assert not list((tmp_path / "state").glob("context-baseline-*"))
    assert active_sandbox_ids(tmp_path) == ()


@pytest.mark.asyncio
async def test_second_pipeline_preserves_active_sandbox_and_baseline(tmp_path: Path) -> None:
    service = pipeline.ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    await service._process_batch_event("local", "active", _batch())
    state = service._run_states[("local", "active")]
    baseline = service._create_context_baseline(state["sandbox_lease"][0])
    _write(baseline, "notes/page.md", "# Baseline\n")
    second = pipeline.ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    await second.start()
    try:
        capacity.maintain_capacity(tmp_path, limit_bytes=1_000_000)
        assert state["sandbox"].is_dir()
        assert (baseline / "notes/page.md").is_file()
    finally:
        await second.stop(timeout_seconds=1)
        await service._cleanup_run_state(("local", "active"))
    capacity.maintain_capacity(tmp_path, limit_bytes=1_000_000)
    assert not baseline.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("hold_baseline_file", [False, True])
async def test_cancelled_arrangement_keeps_lease_for_retain_and_discards_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hold_baseline_file: bool,
) -> None:
    service = pipeline.ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    await service._process_batch_event("local", "active", _batch())
    original = service._filesystem_with_fallback
    entered = asyncio.Event()
    baselines = []

    async def waiting(**kwargs):
        baselines.append(kwargs["baseline_root"])
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_filesystem_with_fallback", waiting)
    task = asyncio.create_task(service._finish_run_event("local", "active"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    lease_ids = active_sandbox_ids(tmp_path)
    old_copy = baselines[0]

    async def retain(**kwargs):
        assert kwargs["baseline_root"] != old_copy
        assert old_copy.is_dir()
        return await original(**kwargs)

    with (old_copy / "held.md").open("w") if hold_baseline_file else nullcontext():
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert active_sandbox_ids(tmp_path) == lease_ids
        capacity.maintain_capacity(tmp_path, limit_bytes=1_000_000)
        assert old_copy.is_dir()
        monkeypatch.setattr(service, "_filesystem_with_fallback", retain)
        await service._finish_run_event("local", "active", retaining=True)
    assert active_sandbox_ids(tmp_path) == ()
    assert pipeline._context_ordinary_pages(tmp_path / "workspace/context")
    capacity.maintain_capacity(tmp_path, limit_bytes=1_000_000)
    assert not list((tmp_path / "state").glob("*context-baseline-*"))


@pytest.mark.asyncio
async def test_published_baseline_cleanup_failure_releases_lease_and_keeps_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = []
    service = pipeline.ContextPipelineService(
        home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue(),
        result_callback=lambda *args: results.append(args),
    )
    await service._process_batch_event("local", "active", _batch())
    completion = asyncio.get_running_loop().create_future()
    await service._process_queue_item(("finish", "local", "active", None, completion))
    assert completion.result() is None
    assert len(results) == 1
    assert pipeline._context_ordinary_pages(tmp_path / "workspace/context")
    assert active_sandbox_ids(tmp_path) == ()
    assert service._run_states == {}
    stale, = (tmp_path / "state").glob("context-baseline-*")
    remove_tree = capacity._remove_tree

    def deny_cleanup(path):
        if path == stale:
            raise PermissionError("simulated file still open")
        return remove_tree(path)

    with monkeypatch.context() as locked:
        locked.setattr(capacity, "_remove_tree", deny_cleanup)
        assert capacity.maintain_capacity(tmp_path, limit_bytes=1_000_000)["status"] == "succeeded"
        assert stale.is_dir()
    assert capacity.maintain_capacity(tmp_path, limit_bytes=1_000_000)["status"] == "succeeded"
    assert not stale.exists()


@pytest.mark.asyncio
async def test_new_input_does_not_wait_for_an_existing_normal_update(tmp_path: Path) -> None:
    service = pipeline.ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    async with context_update_lock(tmp_path):
        await asyncio.wait_for(service._process_batch_event("local", "input", _batch()), timeout=2)
        assert service._run_states[("local", "input")]["batch_count"] == 1
        assert active_sandbox_ids(tmp_path)
    await service._cleanup_run_state(("local", "input"))
    assert active_sandbox_ids(tmp_path) == ()


def test_startup_observes_registration_after_its_initial_active_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = pipeline.ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    sandbox = service._run_sandbox_path("local", "input")
    relative = sandbox.relative_to(service._sandboxes_root).as_posix()
    sandbox_id = hashlib.sha256(relative.encode("utf-8")).hexdigest() + "-" + uuid.uuid4().hex
    leases = []
    sandbox.mkdir(parents=True)

    def register_after_snapshot(home):
        snapshot = active_sandbox_ids(home)
        if not leases:
            # Another instance finishes cleaning the abandoned incarnation.
            # A new run immediately reuses its fixed service/run path.
            sandbox.rmdir()
            leases.append(register_sandbox(home, sandbox_id))
            sandbox.mkdir(parents=True)
            _write(sandbox, "inputs/kept.md", "Completed input.\n")
            _write(home / "state" / f"context-baseline-{sandbox_id}", "notes/kept.md", "Active baseline.\n")
        return snapshot

    monkeypatch.setattr(pipeline, "active_sandbox_ids", register_after_snapshot)
    try:
        service._cleanup_stale_run_sandboxes()
        assert (sandbox / "inputs/kept.md").read_text(encoding="utf-8") == "Completed input.\n"
        assert (tmp_path / "state" / f"context-baseline-{sandbox_id}" / "notes/kept.md").is_file()
    finally:
        for lease in leases:
            release_sandbox(lease)


@pytest.mark.asyncio
async def test_slow_stale_sweep_allows_second_instance_to_finish_a_new_run(tmp_path, monkeypatch):
    stale = tmp_path / "workspace/sandboxes/local/abandoned"
    _write(stale, "inputs/old.md", "Abandoned input.\n")
    first = pipeline.ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    second = pipeline.ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    entered, resume = threading.Event(), threading.Event()
    remove_tree = pipeline._remove_tree

    def pause(path):
        if path == stale:
            entered.set()
            assert resume.wait(5)
        return remove_tree(path)

    monkeypatch.setattr(pipeline, "_remove_tree", pause)
    starting = asyncio.create_task(first.start())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.wait_for(second.start(), 2)
        await second._process_batch_event("local", "new", _batch())
        await asyncio.wait_for(second._finish_run_event("local", "new"), 2)
        assert pipeline._context_ordinary_pages(tmp_path / "workspace/context")
    finally:
        resume.set()
        await starting
        await first.stop(timeout_seconds=1)
        await second.stop(timeout_seconds=1)
    assert not stale.exists()


@pytest.mark.asyncio
async def test_slow_baseline_maintenance_does_not_block_second_startup_or_active_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = pipeline.ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    await first.start()
    await first._process_batch_event("local", "active", _batch())
    stale = tmp_path / "state" / ("context-baseline-" + "0" * 64 + "-" + "1" * 32)
    _write(stale, "notes/old.md", "Abandoned baseline.\n")
    second = pipeline.ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    entered, resume = threading.Event(), threading.Event()
    remove_tree = capacity._remove_tree

    def blocked_cleanup(path):
        if path == stale:
            entered.set()
            assert resume.wait(5)
        return remove_tree(path)

    monkeypatch.setattr(capacity, "_remove_tree", blocked_cleanup)
    maintenance = asyncio.create_task(asyncio.to_thread(capacity.maintain_capacity, tmp_path, limit_bytes=1_000_000))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.wait_for(second.start(), timeout=2)
        completion = asyncio.get_running_loop().create_future()
        await first._input_queue.put(("finish", "local", "active", None, completion))
        await asyncio.wait_for(asyncio.shield(completion), timeout=3)
        assert not resume.is_set()
        assert pipeline._context_ordinary_pages(tmp_path / "workspace/context")
    finally:
        resume.set()
        await maintenance
        await first.stop(timeout_seconds=1)
        await second.stop(timeout_seconds=1)
    assert not stale.exists()


@pytest.mark.asyncio
async def test_agent_fallback_uses_same_snapshot_after_concurrent_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = pipeline.ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    await service._process_batch_event("local", "first", _batch())
    await service._finish_run_event("local", "first")
    live = tmp_path / "workspace/context"
    old = pipeline._context_ordinary_pages(live)[0].relative_to(live).as_posix()
    service._config = _config("agent")
    await service._process_batch_event("local", "second", _batch(
        content="New fallback knowledge.", original_ref="file:///notes/two",
    ))
    attempted = []

    async def failed_agent(**kwargs):
        assert (kwargs["sandbox_path"] / "context" / old).is_file()
        async with context_update_lock(tmp_path):
            retire_page(tmp_path, old)
            pipeline._render_context_navigation(live)
        attempted.append("agent")
        raise pipeline._pipeline_error("model output requires fallback")

    async def failed_balanced(**kwargs):
        assert (kwargs["sandbox"] / "context" / old).is_file()
        attempted.append("balanced")
        raise pipeline._pipeline_error("model output requires fallback")

    monkeypatch.setattr(pipeline, "run_personal_context_agent", failed_agent)
    monkeypatch.setattr(service, "_filesystem_balanced_model_attempt", failed_balanced)
    completion = asyncio.get_running_loop().create_future()
    await service._process_queue_item(("finish", "local", "second", None, completion))
    assert attempted == ["agent", "balanced"]
    assert not (live / old).exists()
    assert any("New fallback knowledge" in page.read_text(encoding="utf-8")
               for page in pipeline._context_ordinary_pages(live))
    capacity.maintain_capacity(tmp_path, limit_bytes=1_000_000)
    assert not list((tmp_path / "state").glob("context-baseline-*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("has_source", [True, False])
async def test_publication_repairs_only_a_verifiable_deleted_source_chain(
    tmp_path: Path, has_source: bool,
) -> None:
    baseline, candidate, live, source = _trees(tmp_path)
    source_id = _write_atomic_source(source)
    for root in (baseline, candidate, live):
        _write(root, "notes/old.md", f"# Old\n\nSource [original](../../source-meta/{source_id}.md).\n")
        _write(root, "notes/kept.md", "# Kept\n\nOriginal knowledge.\n")
        pipeline._render_context_navigation(root)
    text = "# Kept\n\nNew knowledge"
    if has_source:
        text += " from [old knowledge](old.md)."
    _write(candidate, "notes/kept.md", text + "\n")
    (live / "notes/old.md").unlink()
    pipeline._render_context_navigation(live)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    candidate.rename(sandbox / "context")
    service = pipeline.ContextPipelineService(home=tmp_path, config=_config("rules"), input_queue=asyncio.Queue())
    publication = service._publish_processed(
        service_id="local", run_id="run", batch=_batch(),
        processed={"_filesystem_candidate_prepared": True, "_filesystem_candidate_profile": "rules",
                   "_agent_changed_context_paths": {"notes/kept.md"}},
        sandbox=sandbox, alias_targets={}, baseline_root=baseline,
    )
    if has_source:
        await publication
        assert not (live / "notes/old.md").exists()
        assert "New knowledge from old knowledge." in (live / "notes/kept.md").read_text(encoding="utf-8")
        pipeline._validate_reference_graph(live, final_context_root=live, source_root=source, repairable=False)
    else:
        with pytest.raises(BaseError, match="without an atomic source"):
            await publication
        assert "Original knowledge" in (live / "notes/kept.md").read_text(encoding="utf-8")
