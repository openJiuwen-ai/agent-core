# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native compaction decisions and an instance-local, deferred commit boundary."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import shutil
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openjiuwen.core.foundation.llm import BaseMessage, SystemMessage

CompactionTrigger = Literal["manual", "auto", "plugin", "precompute"]


class CompactionError(RuntimeError):
    """A compaction failure whose code distinguishes admission from commit."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CompactionRequest:
    session_id: str
    context_id: str
    trigger: CompactionTrigger
    messages: tuple[BaseMessage, ...]
    instructions: str | None = None


@dataclass(frozen=True)
class CompactionCandidate:
    messages: tuple[BaseMessage, ...]
    tokens_before: int | None = None
    tokens_after: int | None = None


@dataclass(frozen=True)
class CompactionSkipped:
    skip: str


CompactionDecision = CompactionCandidate | CompactionSkipped
CompactionNext = Callable[[CompactionRequest], Awaitable[CompactionDecision]]
CompactionHook = Callable[[CompactionRequest, CompactionNext], Awaitable[CompactionDecision]]


def message_fingerprint(messages) -> str:
    """Fingerprint complete native messages, including private message metadata."""
    data = [message.model_dump(mode="json") for message in messages]
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_compaction_messages(messages, source):
    """Reject forged tool outcomes and new unfinished calls before installation."""

    def inspect(values):
        pending = {}
        seen = set()
        for message in values:
            for call in getattr(message, "tool_calls", None) or []:
                if call.id in seen:
                    raise CompactionError("COMPACT_INVALID_RESULT")
                seen.add(call.id)
                pending[call.id] = call.model_dump()
            if message.role == "tool":
                if getattr(message, "tool_call_id", None) not in pending:
                    raise CompactionError("COMPACT_INVALID_RESULT")
                pending.pop(message.tool_call_id)
        return pending

    original_pending = inspect(source)
    for call_id, call in inspect(messages).items():
        if original_pending.get(call_id) != call:
            raise CompactionError("COMPACT_INVALID_RESULT")


class _DraftSession:
    """Read-through snapshot with deferred Session state writes."""

    def __init__(self, session) -> None:
        self._session = session
        self._snapshot = copy.deepcopy(session.get_state() or {})
        self.updates = {}

    def get_session_id(self):
        return self._session.get_session_id()

    def get_state(self, key=None):
        if isinstance(key, str) and key in self.updates:
            return copy.deepcopy(self.updates[key])
        if key is None:
            return copy.deepcopy({**self._snapshot, **self.updates})
        if isinstance(key, str):
            return copy.deepcopy(self._snapshot.get(key))
        raise CompactionError("COMPACT_UNSUPPORTED")

    def update_state(self, values):
        self.updates.update(copy.deepcopy(values))


class _InstructionModel:
    """Apply per-call instructions without mutating the shared model or prompt."""

    def __init__(self, model, instructions, failures) -> None:
        self.model = model
        self.instructions = instructions
        self.failures = failures

    def __getattr__(self, name):
        return getattr(self.model, name)

    async def invoke(self, *args, **kwargs):
        positional = list(args)
        messages = positional[0] if positional else kwargs.get("messages")
        if messages is not None and self.instructions:
            messages = [message.model_copy(deep=True) for message in messages]
            requirement = "\n\nAdditional compaction requirements:\n" + self.instructions
            if messages and isinstance(messages[0], SystemMessage) and isinstance(messages[0].content, str):
                messages[0].content += requirement
            else:
                messages.insert(0, SystemMessage(content=requirement.strip()))
            if positional:
                positional[0] = messages
            else:
                kwargs["messages"] = messages
        try:
            return await self.model.invoke(*positional, **kwargs)
        except Exception as error:
            self.failures.append(error)
            raise error


class CompactionDraft:
    """Own isolated buffers, processor copies, Session writes and staged files."""

    def __init__(self, context, messages, instructions, processor_types=None) -> None:
        self.original = context
        self.directory = tempfile.TemporaryDirectory(prefix="jiuwen-compact-")
        self.root = Path(self.directory.name)
        self.workspace = Path(context.workspace_dir()).resolve() if context.workspace_dir() else None
        self.failures = []
        self.seeded_files = set()
        self.context = context.fork_compaction(
            messages,
            workspace=str(self.root),
            session_ref=_DraftSession(context.get_session_ref()) if context.get_session_ref() else None,
            instructions=instructions,
            failures=self.failures,
            processor_types=processor_types,
        )
        if self.workspace is not None:
            relative = Path("context") / (context.session_id() + "_context") / "session_memory" / "session_context.md"
            original_notes = self.workspace / relative
            if original_notes.is_file():
                target = self.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original_notes, target)
                self.seeded_files.add(relative)

    def normalized(self, messages):
        """Translate staged resource references to their eventual workspace paths."""

        def convert(value):
            if isinstance(value, str) and self.workspace is not None:
                return value.replace(str(self.root), str(self.workspace))
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            if isinstance(value, list):
                return [convert(item) for item in value]
            return value

        return tuple(type(message).model_validate(convert(message.model_dump())) for message in messages)

    async def publish_files(self, messages):
        """Publish only resources referenced by the final installed messages."""
        serialized = json.dumps([message.model_dump(mode="json") for message in messages], ensure_ascii=False)
        recall_ids = set(re.findall(r"COMPRESSION_RECALL:\s*id=([^\]\s]+)", serialized))
        files = list(self.root.rglob("*"))
        for source in files:
            if not source.is_file():
                continue
            relative = source.relative_to(self.root)
            if relative in self.seeded_files:
                continue
            referenced = source.stem in serialized or any(part in recall_ids for part in relative.parts)
            if referenced:
                if self.workspace is None:
                    raise CompactionError("COMPACT_UNSUPPORTED")
                target = self.workspace / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise CompactionError("COMPACT_RESOURCE_CONFLICT")
                sys_operation = self.original.sys_operation()
                if sys_operation is None:
                    shutil.copyfile(source, target)
                else:
                    result = await sys_operation.fs().write_file(
                        str(target), source.read_text(encoding="utf-8")
                    )
                    if result.code != 0:
                        raise CompactionError("COMPACT_RESOURCE_WRITE_FAILED")

    def close(self):
        self.directory.cleanup()


class CompactionCoordinator:
    """One hook and one ephemeral candidate per engine/context, never process-global."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.hook = None
        self.cache = {}
        self.active = set()
        self.generation = 0

    def register(self, hook):
        if not callable(hook):
            raise TypeError("compaction hook must be callable")
        if self.hook is not None:
            raise CompactionError("COMPACT_HOOK_ALREADY_REGISTERED")
        self.hook = hook
        self.generation += 1

        def remove():
            if self.hook is hook:
                self.hook = None
                self.generation += 1
                self.clear()

        return remove

    def clear(self):
        for cached in self.cache.values():
            cached[3].close()
        self.cache.clear()

    async def run(self, context, trigger, instructions, prepare, *, messages=None, persist=None, processor_types=None):
        """Run a host decision and install only after the entire hook returns."""
        from openjiuwen.core.context_engine.context.context_utils import ContextUtils

        key = (context.session_id(), context.context_id())
        if key in self.active:
            raise CompactionError("COMPACT_REENTRANT")
        if trigger not in ("manual", "auto", "plugin", "precompute"):
            raise ValueError("invalid compaction trigger")
        if instructions is not None and not isinstance(instructions, str):
            raise TypeError("instructions must be a string or None")
        instructions = (instructions or "").strip() or None
        source = copy.deepcopy(list(context.get_messages() if messages is None else messages))
        fingerprint = message_fingerprint(context.get_messages())
        request = CompactionRequest(*key, trigger, tuple(copy.deepcopy(source)), instructions)
        hook_generation = self.generation
        draft = None
        prepared = None
        prepared_key = None
        activity = (key, "precompute") if trigger == "precompute" else key
        if activity in self.active:
            raise CompactionError("COMPACT_BUSY")
        self.active.add(activity)
        started_at = time.time()
        operation_id = uuid.uuid4().hex

        def dependencies():
            return context.compaction_dependencies(processor_types)

        dependency_key = dependencies()
        try:

            async def default(value):
                nonlocal draft, prepared, prepared_key
                if not isinstance(value, CompactionRequest) or (value.session_id, value.context_id, value.trigger) != (
                    request.session_id,
                    request.context_id,
                    request.trigger,
                ):
                    raise CompactionError("COMPACT_INVALID_RESULT")
                current_key = (message_fingerprint(value.messages), value.instructions)
                if prepared_key is not None:
                    if prepared_key != current_key:
                        raise CompactionError("COMPACT_CONTINUATION_REUSED")
                    return copy.deepcopy(prepared)
                prepared_key = current_key
                ContextUtils.validate_messages(list(value.messages))
                cached = self.cache.pop(key, None)
                if cached is not None:
                    old_source, old_instructions, old_result, old_draft, old_dependencies = cached
                    source_length = len(old_source)
                    prefix = list(value.messages[:source_length])
                    suffix = list(value.messages[source_length:])
                    deps_match = old_dependencies == dependency_key
                    instructions_match = old_instructions == value.instructions
                    prefix_match = message_fingerprint(prefix) == message_fingerprint(old_source)
                    suffix_clean = not any(
                        getattr(message, "tool_calls", None) or message.role == "tool" for message in suffix
                    )
                    if all((deps_match, instructions_match, prefix_match, suffix_clean)):
                        draft = old_draft
                        prepared = CompactionCandidate(tuple([*old_result.messages, *copy.deepcopy(suffix)]))
                        return copy.deepcopy(prepared)
                    old_draft.close()
                draft = CompactionDraft(context, value.messages, value.instructions, processor_types)
                await prepare(draft.context)
                if draft.failures:
                    raise draft.failures[0]
                native = draft.normalized(draft.context.get_messages())
                if message_fingerprint(native) == message_fingerprint(value.messages):
                    prepared = CompactionSkipped("No compressible conversation content")
                else:
                    prepared = CompactionCandidate(native)
                return copy.deepcopy(prepared)

            decision = await self.hook(request, default) if self.hook is not None else await default(request)
            if isinstance(decision, CompactionSkipped):
                if not isinstance(decision.skip, str) or not 1 <= len(decision.skip.strip()) <= 512:
                    raise CompactionError("COMPACT_INVALID_RESULT")
                return CompactionSkipped(decision.skip.strip())
            if not isinstance(decision, CompactionCandidate):
                raise CompactionError("COMPACT_INVALID_RESULT")
            final = copy.deepcopy(list(decision.messages))
            ContextUtils.validate_messages(final)
            if not all(isinstance(message, BaseMessage) for message in final):
                raise CompactionError("COMPACT_INVALID_RESULT")
            validate_compaction_messages(final, source)
            if hook_generation != self.generation or dependency_key != dependencies():
                raise CompactionError("COMPACT_STALE")
            current = context.get_messages()
            if message_fingerprint(current) != fingerprint and not (
                trigger == "precompute" and message_fingerprint(current[: len(source)]) == fingerprint
            ):
                raise CompactionError("COMPACT_STALE")
            counter = context.token_counter()
            before = counter.count_messages(source) if counter else None
            after = counter.count_messages(final) if counter and trigger != "precompute" else None
            result = CompactionCandidate(tuple(final), before, after)
            if trigger == "precompute":
                if draft is None:
                    draft = CompactionDraft(context, source, instructions, processor_types)
                old = self.cache.pop(key, None)
                if old is not None:
                    old[3].close()
                self.cache[key] = (source, instructions, result, draft, dependency_key)
                draft = None
                return result
            if message_fingerprint(final) == message_fingerprint(source):
                return CompactionSkipped("Conversation is unchanged")
            committed = False

            async def install():
                nonlocal committed
                if draft is not None:
                    await draft.publish_files(final)
                if (
                    hook_generation != self.generation
                    or message_fingerprint(context.get_messages()) != fingerprint
                    or self.engine.get_context(context_id=key[1], session_id=key[0]) is not context
                ):
                    raise CompactionError("COMPACT_STALE")
                context.set_messages(final)
                if draft is not None:
                    context.restore_compaction_state(
                        draft.context,
                        restore_processors=message_fingerprint(final) == message_fingerprint(
                            draft.normalized(draft.context.get_messages())
                        ),
                    )
                if draft is None or message_fingerprint(final) != message_fingerprint(
                    draft.normalized(draft.context.get_messages())
                ):
                    from openjiuwen.core.context_engine.context.session_memory_manager import (
                        invalidate_session_memory_anchor,
                    )

                    invalidate_session_memory_anchor(context.get_session_ref())
                if persist is not None:
                    await asyncio.wait_for(persist(), timeout=30)
                committed = True
                original_ids = {message_fingerprint([message]) for message in source}
                summary = "\n".join(
                    str(message.content)
                    for message in final
                    if message.role != "system" and message_fingerprint([message]) not in original_ids
                )
                receipt = {
                    "operation_id": operation_id,
                    "trigger": trigger,
                    "status": "completed",
                    "compact_summary": summary,
                    "before": {"tokens": before},
                    "after": {"tokens": after},
                }
                from openjiuwen.core.context_engine.context.processor_state_recorder import ContextProcessorStateInput

                await context.record_compaction(
                    receipt,
                    ContextProcessorStateInput(
                        operation_id=operation_id,
                        status="completed",
                        phase="active_compress",
                        trigger=trigger,
                        processor=None,
                        reason="compaction_committed",
                        before_messages=source,
                        after_messages=final,
                        started_at=started_at,
                        ended_at=time.time(),
                        error=None,
                        messages_to_modify=list(range(len(source))),
                        force=trigger != "auto",
                        context_max=context.context_window_tokens(),
                        compact_summary=summary,
                    )
                )

            task = asyncio.create_task(install())
            try:
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError as cancelled:
                    await task
                    raise cancelled
            except Exception as error:
                if committed:
                    raise CompactionError("COMPACT_NOTIFICATION_FAILED") from error
                if message_fingerprint(context.get_messages()) != fingerprint:
                    context.mark_compaction_uncertain()
                    raise CompactionError("COMPACT_COMMIT_UNCERTAIN") from error
                raise error
            return result
        finally:
            self.active.discard(activity)
            if draft is not None:
                draft.close()
