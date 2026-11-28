# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify PCS accounting through model callbacks and real fetch rounds."""

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from openjiuwen.core.context_engine.context.compression_scope import context_compression_operation
from openjiuwen.core.foundation.llm import (
    AssistantMessage, AssistantMessageChunk, Model, ModelClientConfig, ModelRequestConfig,
)
from openjiuwen.core.foundation.llm import model as model_module
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata
from openjiuwen.core.runner.callback.events import LLMCallEvents
from openjiuwen.core.runner.callback.utils import trigger
from openjiuwen.harness.personal_context import PersonalContext, personal_context as runtime
from openjiuwen.harness.personal_context import _run_token_usage_store as storage
from openjiuwen.harness.personal_context._run_token_usage import (
    model_usage_phase, run_usage_scope, summarize_run_usage,
)


def _summary(total, *, estimated=False, incomplete=False):
    return {"total_tokens": total, "has_estimate": estimated, "incomplete": incomplete}


def _model_config():
    return ModelClientConfig(client_provider="OpenAI", api_base="https://example.invalid/v1", api_key="test-key")


def _build_model(monkeypatch, *, invoke=None, stream=None):
    monkeypatch.setattr(model_module, "create_model_client", lambda **_: SimpleNamespace(invoke=invoke, stream=stream))
    return Model(_model_config(), ModelRequestConfig(model="configured-model"))


@pytest.mark.asyncio
async def test_invoke_counts_provider_total_once_and_records_routed_retry(monkeypatch):
    async def invoke(**_kwargs):
        for model in ("first-route", "backup-route"):
            await trigger(LLMCallEvents.LLM_ROUTE_ATTEMPT, route_metadata={"provider": "OpenAI", "model_name": model})
        usage = UsageMetadata(input_tokens=20, output_tokens=5, total_tokens=28, cache_tokens=10, reasoning_tokens=2)
        await trigger(LLMCallEvents.LLM_OUTPUT, usage=usage)
        return AssistantMessage(content="response", usage_metadata=usage, response_model="backup-model-version")

    model = _build_model(monkeypatch, invoke=invoke)
    run = {}
    with run_usage_scope(run), model_usage_phase("agent"):
        assert (await model.invoke(messages="request")).content == "response"
    assert summarize_run_usage(run)[0] == _summary(28, incomplete=True)
    by_model = {group["model"]: group for group in summarize_run_usage(run)[1]}
    assert by_model["first-route"]["incomplete_attempts"] == 1
    assert by_model["backup-model-version"]["actual_tokens"] == 28
    assert by_model["backup-model-version"]["incomplete_attempts"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", [False, True])
async def test_stream_merges_partial_and_duplicate_usage_even_if_cancelled(monkeypatch, interrupt):
    awaiting_chunk = asyncio.Event()

    async def stream(**_kwargs):
        yield AssistantMessageChunk(
            usage_metadata=UsageMetadata(
                input_tokens=40, output_tokens=1, total_tokens=41, model_name="configured-model",
            ),
            response_model="actual-model-version",
        )
        for output in (3, 7, 7):
            yield AssistantMessageChunk(usage_metadata=UsageMetadata(
                output_tokens=output, total_tokens=output, model_name="configured-model",
            ))
        if interrupt:
            awaiting_chunk.set()
            await asyncio.Event().wait()

    model = _build_model(monkeypatch, stream=stream)
    run = {}

    async def read():
        with run_usage_scope(run), model_usage_phase("agent"):
            return [chunk async for chunk in model.stream(messages="request")]

    task = asyncio.create_task(read())
    if interrupt:
        await asyncio.wait_for(awaiting_chunk.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert len(await task) == 4
    summary, groups = summarize_run_usage(run)
    assert summary == _summary(47, incomplete=interrupt)
    assert groups[0]["model"] == "actual-model-version"


@pytest.mark.asyncio
async def test_usage_estimates_skip_tools_and_default_zero_is_not_actual(monkeypatch):
    async def invoke(**_kwargs):
        return AssistantMessage(content="estimated text", usage_metadata=UsageMetadata())

    model = _build_model(monkeypatch, invoke=invoke)
    run = {}
    with run_usage_scope(run), model_usage_phase("semantic_enrichment"):
        await model.invoke(messages="short prompt")
    summary, groups = summarize_run_usage(run)
    assert summary == _summary(8, estimated=True)
    assert groups[0]["actual_tokens"] == 0

    run = {}
    with run_usage_scope(run), model_usage_phase("agent"):
        with context_compression_operation("test-operation"):
            await model.invoke(messages="short prompt", tools=[{"type": "function"}])
    summary, groups = summarize_run_usage(run)
    assert summary == _summary(4, estimated=True, incomplete=True)
    assert groups[0]["phase"] == "compression"


@pytest.mark.asyncio
async def test_failed_request_is_unknown_and_a_later_call_is_independent(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def invoke(messages, **_kwargs):
        if messages == "pause":
            entered.set()
            await release.wait()
        if messages == "failure":
            raise OSError("test request failure")
        total = 10 if messages == "pause" else 30
        return AssistantMessage(content="response", usage_metadata=UsageMetadata(total_tokens=total))

    model = _build_model(monkeypatch, invoke=invoke)

    async def measure(run, messages):
        with run_usage_scope(run), model_usage_phase("agent"):
            await model.invoke(messages=messages)

    first, second = {}, {}
    with pytest.raises(OSError):
        await measure(first, "failure")
    assert summarize_run_usage(first)[0] == _summary(None, incomplete=True)
    waiting = asyncio.create_task(measure(first, "pause"))
    await asyncio.wait_for(entered.wait(), timeout=2)
    await model.invoke(messages="ordinary session")
    await measure(second, "next service")
    release.set()
    await waiting
    assert summarize_run_usage(first)[0] == _summary(10, incomplete=True)
    assert summarize_run_usage(second)[0] == _summary(30)


def _config(source, *, mode="rules"):
    config = {
        "collection_enabled": True,
        "agent_use_enabled": False,
        "strategy_profile": mode,
        "fetch_services": [{
            "service_id": "notes", "provider": "local_files", "enabled": False,
            "interval_seconds": 3600, "time_range": {"mode": "all"},
            "source": {"root_dir": str(source)}, "credentials": {},
        }],
    }
    if mode != "rules":
        config.update(model_client=_model_config(), model_request=ModelRequestConfig(model="configured-model"))
    return PersonalContext.Config.from_dict(config)


async def _complete(core):
    initial = await core.run_fetch(service_id="notes")
    await asyncio.wait_for(asyncio.gather(*core._manual_fetch_tasks.values()), timeout=10)
    return await core.get_fetch_run_status("notes", run_id=initial["runs"][0]["run_id"])


async def _until(condition):
    async with asyncio.timeout(3):
        while not condition():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_real_fetch_passes_usage_across_pipeline_and_preserves_old_history(tmp_path, monkeypatch):
    async def invoke(messages, **_kwargs):
        payload = json.loads(messages[0].content.split("\n", 1)[1])
        total = 44 if "items" in payload else 66
        return AssistantMessage(content="{}", usage_metadata=UsageMetadata(total_tokens=total), response_model="actual")

    _build_model(monkeypatch, invoke=invoke)
    source, home = tmp_path / "notes", tmp_path / "runtime"
    source.mkdir()
    (source / "note.md").write_text("# Coffee\nBeans are sorted and roasted for brewing.", encoding="utf-8")
    core = PersonalContext(home=home)
    config = _config(source, mode="balanced")
    await core.set_configuration(config)
    await core.start_collection()
    try:
        run = await _complete(core)
        assert run["run_state"] == "succeeded"
        assert run["created_node_count"] > 0
        # One semantic request and one presentation request for each depth (topic and root).
        assert run["token_usage"] == _summary(176)
        assert (await core.snapshot()).fetch_run_progress["notes"]["token_usage"] == _summary(176)
        usage_path = storage._usage_path(home, "notes")
        await _until(usage_path.is_file)
        data = json.loads(usage_path.read_text(encoding="utf-8"))
        detail = data["runs"][run["run_id"]]["groups"]
        assert {(group["phase"], group["model"], group["actual_tokens"]) for group in detail} == {
            ("semantic_enrichment", "actual", 44), ("directory_description", "actual", 132),
        }
        assert "token_usage" not in core._read_run_history("notes")[0]
    finally:
        await core.stop_collection()
        core.shutdown()

    restarted = PersonalContext(home=home)
    try:
        await restarted.set_configuration(config)
        await _until(lambda: "token_usage" in restarted._fetch_run_history["notes"][0])
        assert await restarted.get_fetch_run_status("notes", run_id=run["run_id"]) == run
    finally:
        restarted.shutdown()


@pytest.mark.asyncio
async def test_slow_optional_disk_write_does_not_block_fetch_or_publish_late(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    original_fsync = storage.os.fsync
    original_save = storage.save_run_usage

    def slow_fsync(fd):
        if threading.current_thread().name == "pcs-run-usage":
            started.set()
            release.wait(timeout=5)
        original_fsync(fd)

    def tracked_save(*args):
        try:
            original_save(*args)
        finally:
            completed.set()

    monkeypatch.setattr(storage.os, "fsync", slow_fsync)
    monkeypatch.setattr(runtime, "save_run_usage", tracked_save)
    source, home = tmp_path / "notes", tmp_path / "runtime"
    source.mkdir()
    core = PersonalContext(home=home)
    await core.set_configuration(_config(source))
    await core.start_collection()
    try:
        run = await _complete(core)
        await _until(started.is_set)
        assert run["run_state"] == "succeeded"
        assert run["token_usage"] == _summary(0)
        await asyncio.wait_for(core.stop_collection(), timeout=1)
        await asyncio.sleep(storage._QUEUE_TIMEOUT + 0.05)
        release.set()
        await _until(completed.is_set)
        assert not storage._usage_path(home, "notes").exists()
    finally:
        release.set()
        await core.stop_collection()
        core.shutdown()


@pytest.mark.asyncio
async def test_deleted_service_usage_is_removed_after_a_slow_replace(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    original_replace = storage.os.replace
    original_save = storage.save_run_usage

    def slow_replace(source, destination):
        if threading.current_thread().name == "pcs-run-usage":
            started.set()
            release.wait(timeout=5)
        original_replace(source, destination)

    def tracked_save(*args):
        try:
            original_save(*args)
        finally:
            completed.set()

    monkeypatch.setattr(storage.os, "replace", slow_replace)
    monkeypatch.setattr(runtime, "save_run_usage", tracked_save)
    source, home = tmp_path / "notes", tmp_path / "runtime"
    source.mkdir()
    core = PersonalContext(home=home)
    await core.set_configuration(_config(source))
    await core.start_collection()
    try:
        await _complete(core)
        await _until(started.is_set)
        await asyncio.wait_for(core.stop_collection(), timeout=1)
        core.remove_fetch_run_history("notes")
        core._discard_run_token_usage("notes")
        await asyncio.sleep(storage._QUEUE_TIMEOUT + 0.05)
        release.set()
        await _until(completed.is_set)
        await _until(lambda: not storage._usage_path(home, "notes").exists())
    finally:
        release.set()
        await core.stop_collection()
        core.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", [False, True])
async def test_failed_and_stopped_fetches_keep_usage_from_before_interruption(tmp_path, monkeypatch, stop):
    observed = asyncio.Event()

    async def invoke(**_kwargs):
        return AssistantMessage(content="response", usage_metadata=UsageMetadata(total_tokens=83))

    model = _build_model(monkeypatch, invoke=invoke)

    async def prepare_run(_provider, **_kwargs):
        with model_usage_phase("provider"):
            await model.invoke(messages="request")
        observed.set()
        if stop:
            await asyncio.Event().wait()
        raise OSError("test source failure")

    monkeypatch.setattr(runtime._PROVIDER_TYPES["local_files"], "prepare_run", prepare_run)
    source, home = tmp_path / "notes", tmp_path / "runtime"
    source.mkdir()
    core = PersonalContext(home=home)
    await core.set_configuration(_config(source))
    await core.start_collection()
    try:
        started = await core.run_fetch(service_id="notes")
        tasks = list(core._manual_fetch_tasks.values())
        await asyncio.wait_for(observed.wait(), timeout=2)
        if stop:
            await asyncio.wait_for(core.stop_fetch_run("notes"), timeout=2)
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
        run_id = started["runs"][0]["run_id"]
        record = await core.get_fetch_run_status("notes", run_id=run_id)
        assert record["run_state"] == ("cancelled" if stop else "failed")
        assert record["token_usage"] == _summary(83)
        await _until(lambda: run_id in storage.read_usage_summaries(home, "notes"))
        assert core._read_cursor("notes") is None
    finally:
        await core.stop_collection()
        core.shutdown()


@pytest.mark.asyncio
async def test_usage_follows_history_retention_and_service_deletion(tmp_path):
    source, home = tmp_path / "notes", tmp_path / "runtime"
    source.mkdir()
    core = PersonalContext(home=home)
    await core.set_configuration(_config(source))
    await core.start_collection()
    try:
        retained_ids = []
        for _ in range(6):
            record = await _complete(core)
            run_id = record["run_id"]
            retained_ids.append(run_id)
            await _until(lambda: run_id in storage.read_usage_summaries(home, "notes"))
        assert set(storage.read_usage_summaries(home, "notes")) == set(retained_ids[-5:])
        saved = core.remove_fetch_run_history("notes")
        core.restore_fetch_run_history("notes", saved)
        assert (await core.get_fetch_run_status("notes"))["runs"][0]["token_usage"] == _summary(0)
        core.remove_fetch_run_history("notes")
        core._discard_run_token_usage("notes")
        await _until(lambda: not storage._usage_path(home, "notes").exists())
    finally:
        await core.stop_collection()
        core.shutdown()
