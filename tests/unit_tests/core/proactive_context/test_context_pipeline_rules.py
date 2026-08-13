from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

import pytest

import openjiuwen.core.proactive_context.context_pipeline as context_pipeline
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.proactive_context.config import PCSConfig
from openjiuwen.core.proactive_context.context_pipeline import ContextPipelineService
from openjiuwen.core.proactive_context.models import FetchBatch, RawChangeItem
from openjiuwen.core.proactive_context.source_metadata import read_source_metadata, source_id_for_locator


def _config() -> PCSConfig:
    return PCSConfig.from_dict(
        {
            "enabled": True,
            "fetching_enabled": True,
            "strategy_profile": "rules",
            "model_client": None,
            "model_request": None,
            "fetch_services": [],
        }
    )


def _batch(*items: dict[str, object], batch_id: str = "batch-1") -> FetchBatch:
    return FetchBatch(
        batch_id=batch_id,
        items=[RawChangeItem(**item) for item in items],
    )


def _item(
    logical_id: str = "notes/one",
    *,
    revision_id: str = "rev-1",
    content: str = "First paragraph.\n\nSecond paragraph.",
    operation: str = "upsert",
    original_ref: str | None = None,
    title: str = "One",
) -> dict[str, object]:
    return {
        "logical_id": logical_id,
        "revision_id": revision_id,
        "operation": operation,
        "title": title,
        "content": content if operation == "upsert" else None,
        "original_ref": original_ref or f"file:///{logical_id}",
        "metadata": {"kind": "note"},
        "raw_snapshot": "raw source" if operation == "upsert" else None,
    }


async def _put_event(
    queue: asyncio.Queue[object],
    tag: str,
    service_id: str,
    run_id: str,
    payload: FetchBatch | None,
) -> None:
    completion = asyncio.get_running_loop().create_future()
    await queue.put((tag, service_id, run_id, payload, completion))
    await asyncio.wait_for(asyncio.shield(completion), timeout=2)


async def _submit_run(
    queue: asyncio.Queue[object],
    service_id: str,
    run_id: str,
    *batches: FetchBatch,
) -> None:
    for batch in batches:
        await _put_event(queue, "batch", service_id, run_id, batch)
    await _put_event(queue, "finish", service_id, run_id, None)


async def _cancel_consumer_twice_while_io_is_blocked(
    service: ContextPipelineService,
    completion: asyncio.Future[None],
    *,
    release_worker: threading.Event,
    cleanup_started: threading.Event,
) -> None:
    consumer = service._consumer_task
    assert consumer is not None
    callback_count = 0
    callback_called = asyncio.Event()

    def record_completion(done: asyncio.Future[None]) -> None:
        nonlocal callback_count
        assert done is completion
        callback_count += 1
        callback_called.set()

    completion.add_done_callback(record_completion)
    try:
        consumer.cancel()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(consumer), timeout=0.05)
        consumer.cancel()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(consumer), timeout=0.05)
        assert not cleanup_started.is_set()
    finally:
        release_worker.set()

    with pytest.raises(asyncio.CancelledError):
        await consumer
    await asyncio.wait_for(callback_called.wait(), timeout=2)
    assert callback_count == 1
    assert completion.done()
    assert completion.exception() is not None


@pytest.mark.asyncio
async def test_rules_pipeline_publishes_after_root_description(tmp_path: Path) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    await service.start()

    await _submit_run(queue, "local", "run-1", _batch(_item()))

    context_root = tmp_path / "workspace" / "context"
    description = context_root / "description.md"
    assert description.read_text(encoding="utf-8").strip()
    assert (context_root / "sources" / "local" / "description.md").is_file()
    pages = [path for path in context_root.rglob("*.md") if path.name != "description.md"]
    assert len(pages) == 1
    page_text = pages[0].read_text(encoding="utf-8")
    assert "pcs_logical_ids" not in page_text
    assert "Source / Evidence" not in page_text
    assert "First paragraph." in page_text
    assert "[[ref:" not in page_text
    assert "../../../source-meta/src_" in page_text
    source_metadata = list((tmp_path / "workspace" / "source-meta").glob("src_*.md"))
    assert len(source_metadata) == 1
    context_pipeline._validate_reference_graph(
        context_root,
        final_context_root=context_root,
        source_root=tmp_path / "workspace" / "source-meta",
        repairable=False,
    )
    assert not (tmp_path / "workspace" / "source-proofs").exists()
    assert not list((tmp_path / "workspace" / "sandboxes").rglob("*"))

    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_successful_run_persists_only_context_and_atomic_source_metadata(tmp_path: Path) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    await service.start()

    await _submit_run(queue, "local", "run-1", _batch(_item()))

    workspace = tmp_path / "workspace"
    assert (workspace / "context" / "description.md").is_file()
    assert list((workspace / "source-meta").glob("src_*.md"))
    assert not (workspace / "source-proofs").exists()
    assert not any((workspace / "sandboxes").rglob("content.md"))
    assert not any((workspace / "sandboxes").rglob("raw.*"))
    assert not any((workspace / "sandboxes").rglob("blocks.jsonl"))
    assert not (tmp_path / "materialized-sources").exists()

    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_startup_removes_only_stale_pcs_content_roots(tmp_path: Path) -> None:
    user_source = tmp_path / "user-source"
    user_source.mkdir()
    user_file = user_source / "notes.md"
    user_file.write_text("user-owned", encoding="utf-8")
    stale_sandbox = tmp_path / "workspace" / "sandboxes" / "local" / "old-run"
    stale_sandbox.mkdir(parents=True)
    (stale_sandbox / "content.md").write_text("temporary", encoding="utf-8")
    legacy_proof = tmp_path / "workspace" / "source-proofs" / "old" / "proof"
    legacy_proof.mkdir(parents=True)
    (legacy_proof / "raw-snapshot.txt").write_text("temporary", encoding="utf-8")
    stale_materialized = tmp_path / "materialized-sources" / "github" / "service" / "candidate"
    stale_materialized.mkdir(parents=True)
    (stale_materialized / "README.md").write_text("temporary", encoding="utf-8")
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=asyncio.Queue())

    await service.start()

    assert not stale_sandbox.exists()
    assert not (tmp_path / "workspace" / "source-proofs").exists()
    assert not (tmp_path / "materialized-sources").exists()
    assert user_file.read_text(encoding="utf-8") == "user-owned"
    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_rules_pipeline_delete_removes_page_but_keeps_source_metadata(tmp_path: Path) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    await service.start()

    await _submit_run(queue, "local", "run-1", _batch(_item()))
    source_metadata = list((tmp_path / "workspace" / "source-meta").glob("src_*.md"))
    assert len(source_metadata) == 1

    await _submit_run(queue, "local", "run-2", _batch(_item(operation="delete"), batch_id="batch-2"))
    context_root = tmp_path / "workspace" / "context"
    assert not [path for path in context_root.rglob("*.md") if path.name != "description.md"]
    assert source_metadata[0].is_file()
    assert not (tmp_path / "workspace" / "source-proofs").exists()
    assert description_has_empty_summary(context_root / "description.md")

    await service.stop(timeout_seconds=1)


def description_has_empty_summary(path: Path) -> bool:
    return "No context documents" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_pipeline_failure_sets_completion_exception_and_cleans_sandbox(tmp_path: Path) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    await service.start()

    completion = asyncio.get_running_loop().create_future()
    invalid = _batch(_item())
    await queue.put(("batch", "../escape", "run-1", invalid, completion))
    with pytest.raises(Exception):
        await asyncio.wait_for(completion, timeout=2)
    assert not list((tmp_path / "workspace" / "sandboxes").rglob("*"))

    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_tagged_batch_waits_for_processing_and_finish_publishes_once(tmp_path: Path) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    await service.start()
    batch_completion = asyncio.get_running_loop().create_future()
    finish_completion = asyncio.get_running_loop().create_future()
    try:
        await queue.put(("batch", "local", "run-1", _batch(_item()), batch_completion))
        await asyncio.wait_for(asyncio.shield(batch_completion), timeout=0.5)
        assert not (tmp_path / "workspace" / "context" / "description.md").exists()

        await queue.put(("finish", "local", "run-1", None, finish_completion))
        await asyncio.wait_for(asyncio.shield(finish_completion), timeout=2)
        assert (tmp_path / "workspace" / "context" / "description.md").is_file()
    finally:
        for completion in (batch_completion, finish_completion):
            if not completion.done():
                completion.cancel()
        await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_batch_processing_never_creates_a_temporary_processing_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    run_root = tmp_path / "workspace" / "sandboxes" / "local" / "run-no-processing-tmp"
    original_process = service._process_with_fallback

    async def inspect_processing(batch: FetchBatch, *unexpected: object) -> dict[str, object]:
        assert unexpected == ()
        assert not (run_root / "tmp").exists()
        return await original_process(batch)

    monkeypatch.setattr(service, "_process_with_fallback", inspect_processing)
    await service.start()
    completion = asyncio.get_running_loop().create_future()
    try:
        await queue.put(("batch", "local", "run-no-processing-tmp", _batch(_item()), completion))
        await asyncio.wait_for(asyncio.shield(completion), timeout=2)
        assert sorted(path.name for path in run_root.iterdir()) == ["inputs"]
        assert not (run_root / "tmp").exists()
    finally:
        if not completion.done():
            completion.cancel()
        await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_two_batches_share_run_inputs_and_finish_publishes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    published: list[tuple[str, str]] = []
    original_publish = service._publish_processed

    async def record_publish(**kwargs: object) -> None:
        sandbox = kwargs["sandbox"]
        assert isinstance(sandbox, Path)
        assert (sandbox / "inputs" / "briefing.md").is_file()
        assert (sandbox / "inputs" / "briefing.json").is_file()
        await original_publish(**kwargs)  # type: ignore[arg-type]
        published.append((str(kwargs["service_id"]), str(kwargs["run_id"])))

    monkeypatch.setattr(service, "_publish_processed", record_publish)
    await service.start()
    large_content = "full source body " * 20_000
    first_batch = _batch(_item(content=large_content), batch_id="batch-1")
    second_batch = _batch(
        _item("notes/two", revision_id="rev-2", content="Second source."),
        _item("notes/deleted", revision_id="rev-3", operation="delete"),
        batch_id="batch-2",
    )
    try:
        await _put_event(queue, "batch", "local", "run-shared", first_batch)
        await _put_event(queue, "batch", "local", "run-shared", second_batch)

        run_root = tmp_path / "workspace" / "sandboxes" / "local" / "run-shared"
        assert run_root.is_dir()
        assert {path.name for path in (run_root / "inputs" / "records").iterdir()} == {"batch-1", "batch-2"}
        assert {path.name for path in (run_root / "inputs" / "processed").iterdir()} == {
            "batch-1",
            "batch-2",
        }
        assert (run_root / "inputs" / "deleted" / "batch-1.json").is_file()
        assert json.loads((run_root / "inputs" / "deleted" / "batch-2.json").read_text(encoding="utf-8")) == [
            "notes/deleted"
        ]
        assert len(list((run_root / "inputs" / "records" / "batch-1").rglob("content.md"))) == 1
        assert len(list((run_root / "inputs" / "records" / "batch-1").rglob("context.md"))) == 1
        assert len(list((run_root / "inputs" / "processed" / "batch-1").rglob("record.json"))) == 1
        assert not (run_root / "inputs" / "briefing.md").exists()
        assert not (tmp_path / "workspace" / "context" / "description.md").exists()
        assert large_content not in repr(service._run_states)
        assert not any(
            isinstance(value, FetchBatch) for state in service._run_states.values() for value in state.values()
        )

        await _put_event(queue, "finish", "local", "run-shared", None)

        assert published == [("local", "run-shared")]
        assert (tmp_path / "workspace" / "context" / "description.md").is_file()
        assert not run_root.exists()
        assert service._run_states == {}
    finally:
        await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_source_ref_alias_is_monotonic_per_run_and_restarts_for_another_run(tmp_path: Path) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    first_locator = "https://example.test/source/one"
    await service.start()
    try:
        await _put_event(
            queue,
            "batch",
            "local",
            "run-alias",
            _batch(
                _item("notes/one", original_ref=first_locator, title="Same title"),
                _item("notes/two", original_ref="https://example.test/source/two", title="Same title"),
                batch_id="batch-1",
            ),
        )
        await _put_event(
            queue,
            "batch",
            "local",
            "run-alias",
            _batch(
                _item("notes/one-copy", original_ref=first_locator),
                _item("notes/three", original_ref="https://example.test/source/three"),
                _item(
                    "notes/deleted",
                    operation="delete",
                    original_ref="https://example.test/source/deleted",
                ),
                batch_id="batch-2",
            ),
        )

        run_root = tmp_path / "workspace" / "sandboxes" / "local" / "run-alias"
        first_documents = [
            path.read_text(encoding="utf-8")
            for path in sorted((run_root / "inputs" / "processed" / "batch-1").rglob("context-document.md"))
        ]
        second_documents = [
            path.read_text(encoding="utf-8")
            for path in sorted((run_root / "inputs" / "processed" / "batch-2").rglob("context-document.md"))
        ]
        assert first_documents[0].startswith("[[ref:0]]\n\n")
        assert first_documents[1].startswith("[[ref:1]]\n\n")
        assert second_documents[0].startswith("[[ref:0]]\n\n")
        assert second_documents[1].startswith("[[ref:2]]\n\n")

        state = service._run_states[("local", "run-alias")]
        aliases = state.get("source_alias_by_id")
        logical_sources = state.get("source_id_by_logical_id")
        assert isinstance(aliases, dict) and len(aliases) == 4
        assert isinstance(logical_sources, dict) and len(logical_sources) == 5
        source_files = sorted((tmp_path / "workspace" / "source-meta").glob("*.md"))
        assert len(source_files) == 4
        assert all("raw source" not in path.read_text(encoding="utf-8") for path in source_files)

        await _put_event(queue, "abort", "local", "run-alias", None)
        assert ("local", "run-alias") not in service._run_states

        await _put_event(
            queue,
            "batch",
            "local",
            "run-other",
            _batch(_item("notes/four", original_ref="https://example.test/source/four"), batch_id="batch-other"),
        )
        other_root = tmp_path / "workspace" / "sandboxes" / "local" / "run-other"
        other_document = next((other_root / "inputs" / "processed").rglob("context-document.md"))
        assert other_document.read_text(encoding="utf-8").startswith("[[ref:0]]\n\n")
        await _put_event(queue, "abort", "local", "run-other", None)
        assert service._run_states == {}
    finally:
        await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_pipeline_keeps_distinct_provider_canonical_local_file_locators(tmp_path: Path) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    locators = ("file:///private/notes/one.md", "file:///private/notes/two.md")
    await service.start()
    try:
        await _put_event(
            queue,
            "batch",
            "local",
            "run-local-locators",
            _batch(
                _item("notes/one", original_ref=locators[0]),
                _item("notes/two", original_ref=locators[1]),
            ),
        )

        source_root = tmp_path / "workspace" / "source-meta"
        assert {path.stem for path in source_root.glob("*.md")} == {
            source_id_for_locator(locator) for locator in locators
        }
        assert {
            str(read_source_metadata(source_root / f"{source_id_for_locator(locator)}.md")["locator"])
            for locator in locators
        } == set(locators)
        sandbox_metadata = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (tmp_path / "workspace" / "sandboxes" / "local" / "run-local-locators").rglob("metadata.json")
        )
        assert all(locator not in sandbox_metadata for locator in locators)
        await _put_event(queue, "abort", "local", "run-local-locators", None)
    finally:
        await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_abort_cleans_only_the_selected_run(tmp_path: Path) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    await service.start()
    try:
        await _put_event(queue, "batch", "local", "run-one", _batch(_item(), batch_id="one"))
        await _put_event(
            queue,
            "batch",
            "local",
            "run-two",
            _batch(_item("notes/two", revision_id="rev-2"), batch_id="two"),
        )

        await _put_event(queue, "abort", "local", "run-one", None)

        sandboxes = tmp_path / "workspace" / "sandboxes" / "local"
        assert not (sandboxes / "run-one").exists()
        assert (sandboxes / "run-two").is_dir()
        assert ("local", "run-one") not in service._run_states
        assert ("local", "run-two") in service._run_states

        await _put_event(queue, "abort", "local", "run-two", None)
        await _put_event(queue, "abort", "local", "run-two", None)
        assert service._run_states == {}
    finally:
        await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_transient_run_cleanup_failure_keeps_state_for_second_abort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    run_root = tmp_path / "workspace" / "sandboxes" / "local" / "run-retry"
    real_rmtree = context_pipeline.shutil.rmtree
    attempts = 0

    def transient_rmtree(path: str | os.PathLike[str], *args: object, **kwargs: object) -> None:
        nonlocal attempts
        if Path(path) == run_root and attempts == 0:
            attempts += 1
            raise PermissionError("transient Windows handle")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(context_pipeline.shutil, "rmtree", transient_rmtree)
    await service.start()
    try:
        await _put_event(queue, "batch", "local", "run-retry", _batch(_item()))
        first_abort = asyncio.get_running_loop().create_future()
        await queue.put(("abort", "local", "run-retry", None, first_abort))
        with pytest.raises(BaseError):
            await asyncio.wait_for(asyncio.shield(first_abort), timeout=2)

        assert ("local", "run-retry") in service._run_states
        assert run_root.is_dir()

        await _put_event(queue, "abort", "local", "run-retry", None)
        assert ("local", "run-retry") not in service._run_states
        assert not run_root.exists()
    finally:
        await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_finish_symlink_failure_preserves_external_target_and_allows_repaired_abort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    await service.start()
    run_root = tmp_path / "workspace" / "sandboxes" / "local" / "run-symlink"
    held_run = tmp_path / "held-controlled-run"
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("do not touch", encoding="utf-8")
    simulated_detection = {"blocked": False}
    try:
        await _put_event(queue, "batch", "local", "run-symlink", _batch(_item()))
        run_root.replace(held_run)
        try:
            os.symlink(outside, run_root, target_is_directory=True)
        except OSError:
            # Windows without Developer Mode cannot create a real symlink.
            # Keep the same fail-closed branch deterministic by simulating the
            # detector result while retaining a normal controlled run tree.
            held_run.replace(run_root)
            simulated_detection["blocked"] = True
            original_assert = context_pipeline._assert_no_symlinks

            def reject_run_symlink(path: Path) -> None:
                if simulated_detection["blocked"] and Path(path) == run_root:
                    raise context_pipeline._publish_error("symlink entry is not allowed")
                original_assert(path)

            monkeypatch.setattr(context_pipeline, "_assert_no_symlinks", reject_run_symlink)

        finish = asyncio.get_running_loop().create_future()
        await queue.put(("finish", "local", "run-symlink", None, finish))
        with pytest.raises(BaseError):
            await asyncio.wait_for(asyncio.shield(finish), timeout=2)

        assert marker.read_text(encoding="utf-8") == "do not touch"
        assert ("local", "run-symlink") in service._run_states

        if run_root.is_symlink():
            run_root.unlink()
            held_run.replace(run_root)
        else:
            simulated_detection["blocked"] = False
        await _put_event(queue, "abort", "local", "run-symlink", None)
        assert not run_root.exists()
        assert ("local", "run-symlink") not in service._run_states
        assert marker.read_text(encoding="utf-8") == "do not touch"
    finally:
        if run_root.is_symlink():
            run_root.unlink()
        if held_run.exists():
            held_run.replace(run_root)
        await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_large_run_io_helpers_execute_outside_event_loop_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    loop_thread = threading.get_ident()
    observed: dict[str, list[int]] = {}

    def wrap_method(name: str) -> None:
        original = getattr(service, name)

        def wrapped(*args: object, **kwargs: object) -> object:
            observed.setdefault(name, []).append(threading.get_ident())
            return original(*args, **kwargs)

        monkeypatch.setattr(service, name, wrapped)

    for method_name in (
        "_cleanup_stale_run_sandboxes",
        "_write_batch_records",
        "_write_processed_batch",
        "_load_run_processed",
        "_write_run_briefing",
    ):
        wrap_method(method_name)

    run_root = tmp_path / "workspace" / "sandboxes" / "local" / "run-threaded"
    real_rmtree = context_pipeline.shutil.rmtree

    def record_rmtree(path: str | os.PathLike[str], *args: object, **kwargs: object) -> None:
        if Path(path) == run_root:
            observed.setdefault("run_rmtree", []).append(threading.get_ident())
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(context_pipeline.shutil, "rmtree", record_rmtree)
    await service.start()
    await _submit_run(queue, "local", "run-threaded", _batch(_item(content="large body " * 50_000)))
    await service.stop(timeout_seconds=1)

    expected = {
        "_cleanup_stale_run_sandboxes",
        "_write_batch_records",
        "_write_processed_batch",
        "_load_run_processed",
        "_write_run_briefing",
        "run_rmtree",
    }
    assert expected.issubset(observed)
    assert all(thread_id != loop_thread for name in expected for thread_id in observed[name])


@pytest.mark.asyncio
async def test_cancelled_batch_waits_for_record_write_before_run_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    worker_started = threading.Event()
    release_worker = threading.Event()
    cleanup_started = threading.Event()
    run_root = tmp_path / "workspace" / "sandboxes" / "local" / "run-cancel-batch"
    original_write = service._write_batch_records
    real_rmtree = context_pipeline.shutil.rmtree

    def blocked_write(sandbox: Path, batch: FetchBatch) -> dict[str, str]:
        worker_started.set()
        if not release_worker.wait(timeout=5):
            raise AssertionError("test did not release record writer")
        return original_write(sandbox, batch)

    def record_cleanup(path: str | os.PathLike[str], *args: object, **kwargs: object) -> None:
        if Path(path) == run_root:
            cleanup_started.set()
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(service, "_write_batch_records", blocked_write)
    monkeypatch.setattr(context_pipeline.shutil, "rmtree", record_cleanup)
    await service.start()
    completion = asyncio.get_running_loop().create_future()
    await queue.put(("batch", "local", "run-cancel-batch", _batch(_item()), completion))
    assert await asyncio.to_thread(worker_started.wait, 2)

    await _cancel_consumer_twice_while_io_is_blocked(
        service,
        completion,
        release_worker=release_worker,
        cleanup_started=cleanup_started,
    )

    assert cleanup_started.is_set()
    assert not run_root.exists()
    assert ("local", "run-cancel-batch") not in service._run_states
    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_cancelled_finish_waits_for_finish_io_before_run_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    worker_started = threading.Event()
    release_worker = threading.Event()
    cleanup_started = threading.Event()
    run_root = tmp_path / "workspace" / "sandboxes" / "local" / "run-cancel-finish"
    real_rmtree = context_pipeline.shutil.rmtree

    def blocked_finish_io(sandbox: Path, state: dict[str, object]) -> dict[str, object]:
        del sandbox, state
        worker_started.set()
        if not release_worker.wait(timeout=5):
            raise AssertionError("test did not release finish I/O")
        raise OSError("late disk failure must not replace cancellation")

    def record_cleanup(path: str | os.PathLike[str], *args: object, **kwargs: object) -> None:
        if Path(path) == run_root:
            cleanup_started.set()
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(service, "_prepare_run_finish_io", blocked_finish_io)
    monkeypatch.setattr(context_pipeline.shutil, "rmtree", record_cleanup)
    await service.start()
    await _put_event(queue, "batch", "local", "run-cancel-finish", _batch(_item()))
    completion = asyncio.get_running_loop().create_future()
    await queue.put(("finish", "local", "run-cancel-finish", None, completion))
    assert await asyncio.to_thread(worker_started.wait, 2)

    await _cancel_consumer_twice_while_io_is_blocked(
        service,
        completion,
        release_worker=release_worker,
        cleanup_started=cleanup_started,
    )

    assert cleanup_started.is_set()
    assert not run_root.exists()
    assert ("local", "run-cancel-finish") not in service._run_states
    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_invalid_tagged_events_fail_only_their_completion_and_consumer_continues(tmp_path: Path) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    await service.start()
    completions: list[asyncio.Future[None]] = []
    try:
        invalid_items: list[object] = []
        for event in (
            ("unknown", "local", "run-invalid", None),
            ("batch", "local", "run-invalid"),
            ("batch", "local", "run-invalid", None),
        ):
            completion = asyncio.get_running_loop().create_future()
            completions.append(completion)
            invalid_items.append((*event, completion))

        for item, completion in zip(invalid_items, completions, strict=True):
            await queue.put(item)
            with pytest.raises(BaseError):
                await asyncio.wait_for(asyncio.shield(completion), timeout=0.5)
            assert service.is_running()

        abort_completion = asyncio.get_running_loop().create_future()
        completions.append(abort_completion)
        await queue.put(("abort", "local", "run-invalid", None, abort_completion))
        await asyncio.wait_for(asyncio.shield(abort_completion), timeout=0.5)
        assert service.is_running()
    finally:
        for completion in completions:
            if not completion.done():
                completion.cancel()
        await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_workspace_symlink_is_rejected_before_publication(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, workspace, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    with pytest.raises(BaseError):
        await service.start()
    assert not service.is_running()
    assert not (outside / "context").exists()
    assert not (outside / "source-proofs").exists()


@pytest.mark.asyncio
async def test_start_removes_controlled_stale_run_before_starting_consumer(tmp_path: Path) -> None:
    stale_run = tmp_path / "workspace" / "sandboxes" / "local" / "stale-run"
    stale_run.mkdir(parents=True)
    (stale_run / "unfinished.txt").write_text("unfinished", encoding="utf-8")
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)

    await service.start()

    assert service.is_running()
    assert not stale_run.exists()
    await service.stop(timeout_seconds=1)


@pytest.mark.asyncio
async def test_start_rejects_uncontrolled_stale_sandbox_entry(tmp_path: Path) -> None:
    unexpected = tmp_path / "workspace" / "sandboxes" / "not a service"
    unexpected.mkdir(parents=True)
    marker = unexpected / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)

    with pytest.raises(BaseError):
        await service.start()

    assert marker.is_file()
    assert not service.is_running()


@pytest.mark.asyncio
async def test_cancelled_stop_fails_active_and_queued_completions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    started = asyncio.Event()

    async def block_processing(batch: FetchBatch) -> dict[str, object]:
        del batch
        started.set()
        await asyncio.Event().wait()
        return {}

    monkeypatch.setattr(service, "_process_rules", block_processing)
    await service.start()

    active = asyncio.get_running_loop().create_future()
    queued = asyncio.get_running_loop().create_future()
    await queue.put(("batch", "local", "run-1", _batch(_item()), active))
    await asyncio.wait_for(started.wait(), timeout=2)
    await queue.put(("batch", "local", "run-1", _batch(_item(), batch_id="batch-2"), queued))

    stop_task = asyncio.create_task(service.stop(timeout_seconds=30))
    await asyncio.sleep(0)
    stop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop_task

    assert active.done()
    assert queued.done()
    assert active.exception() is not None
    assert queued.exception() is not None
    assert queue.empty()
    assert not service.is_running()


@pytest.mark.asyncio
async def test_stop_drains_queued_completions_with_exception(tmp_path: Path) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=8)
    service = ContextPipelineService(home=tmp_path, config=_config(), input_queue=queue)
    completion = asyncio.get_running_loop().create_future()
    await queue.put(("batch", "local", "run-1", _batch(_item()), completion))

    await service.stop(timeout_seconds=1)
    assert completion.done()
    assert completion.exception() is not None
