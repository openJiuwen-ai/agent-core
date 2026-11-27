# Shared conversation data and storage

`openjiuwen.core.context_engine` exports `ConversationKey`, `ConversationSnapshot`,
`ConversationBinding`, and `SessionConversationStore`.

ContextEngine loads and saves shared windows through an explicit binding. The host must hold its Session operation
lock across execution and final persistence.
Swarm host injection and shared lifecycle operations are not yet integrated.

## Data and store interface

```python
ConversationKey(session_id: str, conversation_id: str)
ConversationSnapshot(*, schema_version: int = 1,
                     messages: list[BaseMessage] = ...,
                     offload_messages: dict[str, list[BaseMessage]] = ...)
ConversationBinding(key: ConversationKey, store: SessionConversationStore)

async def create(key: ConversationKey) -> ConversationSnapshot: ...
async def read(key: ConversationKey) -> ConversationSnapshot: ...
async def commit(key: ConversationKey, snapshot: ConversationSnapshot) -> None: ...
```

Both IDs are required. A host may choose `"main"` for its main conversation and separate IDs for children.
The configured provider supplies tenant scope. A snapshot preserves structured messages, metadata, and offload buffers,
including pending tool calls. Model, tool, and system prompt configuration are separate. The store can retain file
references, but bound ContextEngine execution accepts provider-owned offload buffers and rejects private filesystem references.

Use `create` at the host's new conversation boundary. Existing records are read without clearing them.
`read` and `commit` raise `CONTEXT_CONVERSATION_NOT_FOUND` for missing records and never import an old checkpoint.
`read` and `create` raise `CONTEXT_MESSAGE_INVALID` for invalid records or unsupported schemas. Provider failures raise
`CONTEXT_EXECUTION_ERROR` with their cause. `commit` replaces the whole record after checking only that it exists; it
does not read and decode the stored content, so callers commit a window they loaded themselves under the Session
operation lock. There is no automatic retry; a failed acknowledgement does not prove that the write failed.

## Provider and Session binding

`InMemoryCheckpointer.conversation_store()` and `PersistenceCheckpointer.conversation_store()` return their owned store.
Persistence reuses the configured KV provider and pickle codec, including existing SQLite/shelve configuration.
Other checkpointers default to `CHECKPOINTER_CONFIG_ERROR`; no new abstract method is required of custom implementations.

```python
from openjiuwen.core.context_engine import ConversationBinding, ConversationKey
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointerProvider


async def create_new_session(card):
    checkpointer = await PersistenceCheckpointerProvider().create({
        "db_type": "sqlite", "db_path": "checkpoint.db",
    })
    store = checkpointer.conversation_store()
    key = ConversationKey("product-session", "main")
    await store.create(key)  # The host supplies its operation lock and new Session boundary.
    return create_agent_session(
        session_id="executor-session", card=card, checkpointer=checkpointer,
        conversation_binding=ConversationBinding(key, store),
    )
```

Call `Session.bind_conversation(binding, *, context_id="default_context_id")` before `pre_run`.
The constructor argument binds the default context; additional contexts can be bound explicitly.
Dots in context IDs are normalized to underscores using the engine's existing rule; ConversationKey remains literal.
Repeating the same key and store is idempotent; redirecting a binding fails.
`Session.get_conversation_binding(context_id)` returns the binding or None.

## ContextEngine data path

While the host holds its Session operation lock:

```python
from openjiuwen.core.foundation.llm import UserMessage


async def append_input(engine, session):
    context = await engine.create_context(session=session)
    await context.add_messages(UserMessage(content="new input"))
    await engine.save_contexts(session)
    await session.commit()
```

`create_context` reads the current shared record even on a cache hit, ignoring checkpoint context and `history_messages`.
It never creates a missing record. Processors, model budgets, system prompts, and tools remain owned by the current
executor, and usage is recomputed for its model.

The shared record is written at fixed points: before each model request (`get_context_window`, including window
processing and compression results), when active compression completes, and at the final save that ends a run.
`add_messages` changes only the in-memory working copy and does not write per message; a host that appends outside a
run saves explicitly, as in the example above. If the process dies between two model requests, the reply and tool
results produced after the last request are not in the record.
Read-only windows, no-op compression and repeated flushes do not rewrite an unchanged record. The existing local
message revision also tracks offload edits, clear and restore; its saved position is not a persistent version or CAS. Inputs enqueued during
active compression retain the existing batching behavior and are saved by the compression operation holding the
existing processor lock. New appends during the commit phase wait for that lock. `add_messages` and the synchronous
`set_messages/pop_messages/offload_messages` stage changes for the next model request or an explicit
`await engine.save_contexts(session)` / `await context.save_conversation()`. No background writes are scheduled.

Bound contexts are excluded from executor checkpoint save/recovery. Partial shared saves preserve other local contexts.
Shared offloads use the `in_memory` placeholder representation, with original buffers persisted in the provider so they
survive executor eviction. Loading a window requires complete tool call/result sequences; an active working copy can
hold pending calls and then append their results, and apart from a registered approval interrupt every call in the
written record has its result.

After a read, commit, or processor operation fails or is cancelled, a successful refresh is required before further
model requests or saves. An uncertain acknowledgement never triggers a stale write or tool replay. Input persistence
failure prevents the model call. Unbound callers retain their checkpoint and offload behavior.

`checkpointer.release` clears executor checkpoints and preserves shared records. Shared deletion, archive, fork,
and rewind are not yet integrated with host lifecycle operations. Migration of historical Sessions requires separate handling.
