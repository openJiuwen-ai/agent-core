# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared conversations on an existing KV provider, outside checkpoint namespaces."""
import base64
from urllib.parse import quote

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.graph.store import create_serializer
from openjiuwen.core.context_engine.schema.conversation import ConversationKey, ConversationSnapshot


class KVSessionConversationStore:
    def __init__(self, kv_store: BaseKVStore) -> None:
        self._kv_store = kv_store
        self._serde = create_serializer("pickle")

    @staticmethod
    def _storage_key(key: ConversationKey) -> str:
        # No literal ':' means executor release('<session_id>:') cannot match,
        # even when a Session itself is named 'conversation' or contains ':'.
        return f"conversation/{quote(key.session_id, safe='')}/{quote(key.conversation_id, safe='')}"

    def _encode(self, snapshot: ConversationSnapshot) -> str:
        try:
            # Serialising is the copy; no await separates validation from it.
            dump_type, blob = self._serde.dumps_typed(snapshot.to_state(copy=False))
            # A text envelope works for both ordinary and exclusive KV writes.
            return dump_type + ":" + base64.b64encode(blob).decode("ascii")
        except BaseError:
            raise
        except Exception as exc:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR, error_msg="conversation encoding failed", cause=exc,
            ) from exc

    async def create(self, key: ConversationKey) -> ConversationSnapshot:
        """Create once, preserving an existing record, including corrupt records."""
        try:
            storage_key = self._storage_key(key)
            if await self._kv_store.get(storage_key) is None:
                await self._kv_store.exclusive_set(storage_key, self._encode(ConversationSnapshot()))
        except Exception as exc:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR, error_msg="conversation creation failed", cause=exc,
            ) from exc
        return await self.read(key)

    async def read(self, key: ConversationKey) -> ConversationSnapshot:
        try:
            blob = await self._kv_store.get(self._storage_key(key))
        except Exception as exc:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR, error_msg="conversation read failed", cause=exc,
            ) from exc
        if blob is None:
            raise build_error(
                StatusCode.CONTEXT_CONVERSATION_NOT_FOUND, error_msg="conversation record does not exist",
            )
        try:
            if isinstance(blob, bytes):
                blob = blob.decode("ascii")
            dump_type, payload = blob.split(":", 1)
            state = self._serde.loads_typed((dump_type, base64.b64decode(payload, validate=True)))
            if not isinstance(state, dict) or set(state) != {"schema_version", "messages", "offload_messages"}:
                raise build_error(StatusCode.CONTEXT_MESSAGE_INVALID, error_msg="invalid conversation record")
            snapshot = ConversationSnapshot(**state)
            snapshot.to_state(copy=False)  # validation only
            return snapshot
        except BaseError:
            raise
        except Exception as exc:
            raise build_error(
                StatusCode.CONTEXT_MESSAGE_INVALID, error_msg="invalid conversation encoding", cause=exc,
            ) from exc

    async def commit(self, key: ConversationKey, snapshot: ConversationSnapshot) -> None:
        """Replace one record atomically; caller excludes other operations.

        The caller commits a window it loaded from this record under the Session
        operation lock, so the record is not decoded again here. Only a missing
        record is refused: a commit must not create one.
        """
        payload = self._encode(snapshot)
        try:
            storage_key = self._storage_key(key)
            exists = await self._kv_store.exists(storage_key)
        except Exception as exc:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR, error_msg="conversation commit failed", cause=exc,
            ) from exc
        if not exists:
            raise build_error(
                StatusCode.CONTEXT_CONVERSATION_NOT_FOUND, error_msg="conversation record does not exist",
            )
        try:
            await self._kv_store.set(storage_key, payload)
        except Exception as exc:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR, error_msg="conversation commit failed", cause=exc,
            ) from exc
