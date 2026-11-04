# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compaction decisions must precede mutation, persistence and resource publication."""

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.context_engine import (
    CompactionCandidate,
    CompactionError,
    CompactionSkipped,
    ContextEngine,
    ContextEngineConfig,
)
from openjiuwen.core.context_engine.compaction import message_fingerprint
from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import FullCompactProcessorConfig
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.core.session.agent import Session


async def _engine(tmp_path=None, *, sys_operation=None):
    engine = ContextEngine(
        ContextEngineConfig(enable_tiktoken_counter=False, enable_openrouter_model_context_window_tokens=False),
        workspace=SimpleNamespace(root_path=str(tmp_path)) if tmp_path else None,
        sys_operation=sys_operation,
    )
    context = await engine.create_context(session=Session(session_id="default_session_id"), history_messages=[
        UserMessage(content="Keep project constraints. " * 200),
        AssistantMessage(content="Investigated files and interfaces. " * 200),
        UserMessage(content="Write tests next. " * 200),
        AssistantMessage(content="Current work remains pending."),
    ], processors=[("FullCompactProcessor", FullCompactProcessorConfig(
        trigger_total_tokens=1, messages_to_keep=1, compression_call_max_tokens=100000,
    ))])
    calls = []
    context.get_session_ref().commit = AsyncMock()
    class Model:
        async def invoke(self, *args, **kwargs):
            messages = kwargs.get("messages", args[0] if args else None)
            calls.append(messages)
            return AssistantMessage(content="Constraints retained. Tests pending.")
    context._processors[0]._model = Model()
    return engine, context, calls


@pytest.mark.asyncio
async def test_post_next_skip_does_not_change_live_messages_or_model_config(tmp_path):
    engine, context, calls = await _engine(tmp_path)
    before = message_fingerprint(context.get_messages())
    model = context._processors[0]._model
    async def hook(request, next_handler):
        candidate = await next_handler(replace(request, instructions="Keep API contracts"))
        assert isinstance(candidate, CompactionCandidate)
        assert message_fingerprint(context.get_messages()) == before
        return CompactionSkipped("keep complete diagnostic context")
    engine.register_compaction_hook(hook)
    result = await engine.compact_context()
    assert result.skip == "keep complete diagnostic context"
    assert message_fingerprint(context.get_messages()) == before
    assert context._processors[0]._model is model
    assert len(calls) == 1
    assert "Keep API contracts" in calls[0][0].content
    assert not list(tmp_path.rglob("*"))


@pytest.mark.asyncio
async def test_final_custom_result_is_installed_after_entire_hook():
    engine, context, calls = await _engine()
    before = message_fingerprint(context.get_messages())
    async def hook(request, next_handler):
        await next_handler(request)
        assert message_fingerprint(context.get_messages()) == before
        return CompactionCandidate((UserMessage(content="user-selected summary"),), 1, 2)
    engine.register_compaction_hook(hook)
    result = await engine.compact_context()
    assert context.get_messages()[0].content == "user-selected summary"
    assert result.tokens_before != 1 and result.tokens_after != 2
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_precompute_does_not_install_and_matching_candidate_is_reused():
    engine, context, calls = await _engine()
    seen = []
    async def hook(request, next_handler):
        seen.append(request.trigger)
        return await next_handler(request)
    engine.register_compaction_hook(hook)
    before = message_fingerprint(context.get_messages())
    candidate = await engine.compact_context(trigger="precompute")
    assert candidate.tokens_after is None
    assert message_fingerprint(context.get_messages()) == before
    await engine.compact_context(trigger="plugin")
    assert seen == ["precompute", "plugin"]
    assert len(calls) == 1
    assert message_fingerprint(context.get_messages()) != before


@pytest.mark.asyncio
async def test_native_auto_add_does_not_enter_staged_compaction_hook():
    engine, context, calls = await _engine()
    context._processors[0].trigger_add_messages = AsyncMock(return_value=True)
    hook = AsyncMock(side_effect=AssertionError("native ADD must keep its original path"))
    engine.register_compaction_hook(hook)
    await context.add_messages(UserMessage(content="new input"))
    assert [message.content for message in context.get_messages()].count("new input") == 1
    hook.assert_not_awaited()
    assert len(calls) == 1
    assert "Tests pending" in " ".join(message.content for message in context.get_messages())


@pytest.mark.asyncio
async def test_native_auto_get_calls_original_processor_without_staged_hook():
    engine, context, calls = await _engine()
    context._processors[0].trigger_get_context_window = AsyncMock(return_value=True)
    async def original_processor(native, window, **kwargs):
        window.context_messages = [UserMessage(content="original processor result")]
        return None, window
    context._processors[0].on_get_context_window = AsyncMock(side_effect=original_processor)
    hook = AsyncMock(side_effect=AssertionError("native GET must keep its original path"))
    engine.register_compaction_hook(hook)
    window = await context.get_context_window()
    hook.assert_not_awaited()
    context._processors[0].on_get_context_window.assert_awaited_once()
    assert window.context_messages[0].content == "original processor result"
    assert not calls


@pytest.mark.asyncio
async def test_compaction_reentry_fails_before_waiting_for_own_lock():
    engine, context, calls = await _engine()
    async def hook(request, next_handler):
        with pytest.raises(CompactionError, match="COMPACT_REENTRANT"):
            await engine.compact_context()
        return CompactionSkipped("done")
    engine.register_compaction_hook(hook)
    await asyncio.wait_for(engine.compact_context(), 2)


@pytest.mark.asyncio
async def test_continuation_reuses_same_request_and_rejects_changed_request():
    engine, context, calls = await _engine()
    async def hook(request, next_handler):
        first = await next_handler(request)
        second = await next_handler(request)
        assert first == second
        with pytest.raises(CompactionError, match="COMPACT_CONTINUATION_REUSED"):
            await next_handler(replace(request, instructions="changed"))
        return second
    engine.register_compaction_hook(hook)
    await engine.compact_context()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_source_version_change_is_not_overwritten():
    engine, context, calls = await _engine()
    async def hook(request, next_handler):
        candidate = await next_handler(request)
        context.set_messages([UserMessage(content="new authoritative state")])
        return candidate
    engine.register_compaction_hook(hook)
    with pytest.raises(CompactionError, match="COMPACT_STALE"):
        await engine.compact_context()
    assert context.get_messages()[0].content == "new authoritative state"


@pytest.mark.asyncio
async def test_cancel_before_install_keeps_context_and_releases_operation():
    engine, context, calls = await _engine()
    entered = asyncio.Event()
    async def hook(request, next_handler):
        await next_handler(request)
        entered.set()
        await asyncio.Event().wait()
    remove = engine.register_compaction_hook(hook)
    before = message_fingerprint(context.get_messages())
    task = asyncio.create_task(engine.compact_context())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert message_fingerprint(context.get_messages()) == before
    assert not context._processor_lock.locked()
    remove()
    remove()
    assert engine._compaction.hook is None


@pytest.mark.asyncio
async def test_offload_files_are_published_only_for_a_final_kept_candidate(tmp_path):
    from openjiuwen.core.context_engine.processor.base import ContextProcessor
    engine, context, calls = await _engine(tmp_path)
    async def prepare(draft):
        replacement = await ContextProcessor.offload_messages(
            context._processors[0], "user", "archived input", draft.get_messages(), context=draft,
        )
        draft.set_messages([replacement])
    async def veto(request, next_handler):
        await next_handler(request)
        return CompactionSkipped("not yet")
    remove = engine.register_compaction_hook(veto)
    await engine._compaction.run(context, "manual", None, prepare)
    assert not list(tmp_path.rglob("*.json"))
    remove()
    result = await engine._compaction.run(context, "manual", None, prepare)
    archives = list(tmp_path.rglob("*.json"))
    assert len(archives) == 1
    assert "Keep project constraints" in archives[0].read_text(encoding="utf-8")
    assert "jiuwen-compact-" not in result.messages[0].content
    assert str(tmp_path) in result.messages[0].content


@pytest.mark.asyncio
async def test_draft_session_memory_updates_are_not_published_on_skip():
    engine, context, calls = await _engine()
    session = context.get_session_ref()
    session.update_state({"memory_anchor": "original"})
    async def prepare(draft):
        draft.get_session_ref().update_state({"memory_anchor": "prepared"})
        draft.set_messages([UserMessage(content="summary")])
    async def hook(request, next_handler):
        await next_handler(request)
        assert session.get_state("memory_anchor") == "original"
        return CompactionSkipped("keep")
    remove = engine.register_compaction_hook(hook)
    await engine._compaction.run(context, "manual", None, prepare)
    assert session.get_state("memory_anchor") == "original"
    remove()
    await engine._compaction.run(context, "manual", None, prepare)
    assert session.get_state("memory_anchor") == "prepared"


@pytest.mark.asyncio
async def test_failed_session_commit_freezes_context_until_authoritative_reload():
    engine, context, calls = await _engine()
    context.get_session_ref().commit = AsyncMock(side_effect=OSError("storage failed"))
    with pytest.raises(CompactionError, match="COMPACT_COMMIT_UNCERTAIN"):
        await engine.compact_context()
    with pytest.raises(CompactionError, match="COMPACT_COMMIT_UNCERTAIN"):
        await context.add_messages(UserMessage(content="must wait for recovery"))
    with pytest.raises(CompactionError, match="COMPACT_COMMIT_UNCERTAIN"):
        await context.get_context_window()
    context.load_state({context.context_id(): {"messages": [UserMessage(content="reloaded durable state")]}})
    assert not context._compaction_uncertain


@pytest.mark.asyncio
async def test_cancellation_during_commit_waits_for_real_commit_and_keeps_receipt():
    engine, context, calls = await _engine()
    entered, release = asyncio.Event(), asyncio.Event()
    async def commit():
        entered.set()
        await release.wait()
    context.get_session_ref().commit = AsyncMock(side_effect=commit)
    task = asyncio.create_task(engine.compact_context())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert context._last_compaction["status"] == "completed"
    assert not context._compaction_uncertain
    context.get_session_ref().commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_default_model_failure_is_not_reported_as_successful_fallback():
    engine, context, calls = await _engine()
    context._processors[0]._model.invoke = AsyncMock(side_effect=OSError("model unavailable"))
    before = message_fingerprint(context.get_messages())
    with pytest.raises(OSError, match="model unavailable"):
        await engine.compact_context()
    assert message_fingerprint(context.get_messages()) == before


@pytest.mark.parametrize("name", [
    "MessageSummaryOffloader", "DialogueCompressor", "CurrentRoundCompressor",
    "RoundLevelCompressor", "SessionMemoryCompressor",
])
def test_installed_forked_processors_have_isolated_candidate_state(name):
    from openjiuwen.core.context_engine.processor import forked
    processor_type = getattr(forked, name)
    config_type = getattr(forked, name + "Config")
    processor = processor_type(config_type())
    candidate = processor.fork_compaction("keep project constraints", [])
    assert candidate is not processor
    assert candidate.config is not processor.config
    assert candidate.save_state() == processor.save_state()


@pytest.mark.parametrize("instructions", [None, "keep project constraints"])
def test_compaction_instructions_change_only_draft_memory_policy(instructions):
    from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import FullCompactProcessor
    from openjiuwen.core.context_engine.processor.forked import SessionMemoryCompressor, SessionMemoryCompressorConfig

    full = FullCompactProcessor(FullCompactProcessorConfig(session_memory_enabled=True))
    memory = SessionMemoryCompressor(SessionMemoryCompressorConfig(enabled=True))
    full_draft = full.fork_compaction(instructions, [])
    memory_draft = memory.fork_compaction(instructions, [])

    assert full._session_memory_enabled is True
    assert memory.config.enabled is True
    assert full_draft._session_memory_enabled is (instructions is None)
    assert memory_draft.config.enabled is (instructions is None)


@pytest.mark.asyncio
async def test_forked_executor_keeps_live_response_and_applies_draft_instructions():
    from openjiuwen.core.context_engine.processor.forked import DialogueCompressor, DialogueCompressorConfig
    from openjiuwen.core.context_engine.processor.forked.compressor.support.compression_executor import (
        CompressionError,
        CompressionRequest,
    )

    response = AssistantMessage(content="summary")
    model = SimpleNamespace(invoke=AsyncMock(return_value=response))
    processor = DialogueCompressor(DialogueCompressorConfig())
    processor.rebind_model(model=model)
    live_executor = processor._compression_executor
    request = CompressionRequest(prompt="summarize", context_messages=[UserMessage(content="history")])
    await live_executor.invoke(request)

    failures = []
    candidate = processor.fork_compaction("retain API contracts", failures)
    draft_executor = candidate._compression_executor
    assert draft_executor is not live_executor
    assert draft_executor.last_response is None
    assert live_executor.last_response is response

    await draft_executor.invoke(request)
    draft_messages = model.invoke.call_args.kwargs["messages"]
    assert "retain API contracts" in draft_messages[0].content
    assert request.context_messages[0].content == "history"

    model.invoke.side_effect = OSError("provider failed")
    with pytest.raises(CompressionError, match="provider failed"):
        await draft_executor.invoke(request)
    assert len(failures) == 1
    assert isinstance(failures[0], OSError)
    assert live_executor.last_response is response


@pytest.mark.asyncio
@pytest.mark.parametrize("write_code", [0, 1])
async def test_compaction_publishes_resources_through_live_sys_operation(tmp_path, write_code):
    fs = SimpleNamespace(write_file=AsyncMock(return_value=SimpleNamespace(code=write_code)))
    provider = SimpleNamespace(fs=lambda: fs)
    engine, context, _ = await _engine(tmp_path, sys_operation=provider)
    before = message_fingerprint(context.get_messages())

    async def prepare(draft):
        resource = Path(draft.workspace_dir()) / "archive.txt"
        resource.write_text("archived history", encoding="utf-8")
        draft.set_messages([UserMessage(content=f"summary archive: {resource}")])

    if write_code:
        with pytest.raises(CompactionError, match="COMPACT_RESOURCE_WRITE_FAILED"):
            await engine._compaction.run(context, "manual", None, prepare)
        assert message_fingerprint(context.get_messages()) == before
    else:
        await engine._compaction.run(context, "manual", None, prepare)
        assert str(tmp_path / "archive.txt") in context.get_messages()[0].content
    fs.write_file.assert_awaited_once_with(str(tmp_path / "archive.txt"), "archived history")
    assert context.sys_operation() is provider


@pytest.mark.asyncio
async def test_precomputed_prefix_keeps_new_appended_input_without_another_model_call():
    engine, context, calls = await _engine()
    # Exercise the explicit API without independently triggering legacy ADD compression.
    context._processors[0].trigger_add_messages = AsyncMock(return_value=False)
    async def hook(request, next_handler):
        return await next_handler(request)
    engine.register_compaction_hook(hook)
    await engine.compact_context(trigger="precompute")
    await context.add_messages(UserMessage(content="new append-only input"))
    await engine.compact_context()
    assert len(calls) == 1
    assert context.get_messages()[-1].content == "new append-only input"


@pytest.mark.asyncio
async def test_precompute_does_not_hold_the_live_processor_lock():
    engine, context, calls = await _engine()
    context._processors[0].trigger_add_messages = AsyncMock(return_value=False)
    entered, release = asyncio.Event(), asyncio.Event()
    original_invoke = context._processors[0]._model.invoke
    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original_invoke(*args, **kwargs)
    context._processors[0]._model.invoke = delayed
    async def hook(request, next_handler):
        return await next_handler(request)
    engine.register_compaction_hook(hook)
    task = asyncio.create_task(engine.compact_context(trigger="precompute"))
    await entered.wait()
    await asyncio.wait_for(context.add_messages(UserMessage(content="concurrent append")), 1)
    assert not context._processor_lock.locked()
    release.set()
    candidate = await task
    assert candidate.tokens_after is None
    assert context.get_messages()[-1].content == "concurrent append"
    engine.invalidate_compaction_candidates()


@pytest.mark.asyncio
async def test_model_change_invalidates_speculative_candidate():
    engine, context, calls = await _engine()
    await engine.compact_context(trigger="precompute")
    engine.update_model_context(model_name="changed-model")
    await engine.compact_context()
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["context", "engine"])
async def test_legacy_compact_does_not_enter_staged_hook(entry):
    engine, context, calls = await _engine()
    hook = AsyncMock(side_effect=AssertionError("legacy compression must not dispatch extensions"))
    engine.register_compaction_hook(hook)
    result = await (context.compress_context() if entry == "context" else engine.compress_context())
    assert result == "compressed"
    hook.assert_not_awaited()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_hook_removed_during_preparation_cannot_install_old_result():
    engine, context, calls = await _engine()
    before = message_fingerprint(context.get_messages())
    async def hook(request, next_handler):
        result = await next_handler(request)
        remove()
        return result
    remove = engine.register_compaction_hook(hook)
    with pytest.raises(CompactionError, match="COMPACT_STALE"):
        await engine.compact_context()
    assert message_fingerprint(context.get_messages()) == before
