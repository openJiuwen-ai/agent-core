from __future__ import annotations

from openjiuwen.harness.personal_context.im import (
    ImLearningCursor,
    ImLearningMessage,
    ImLearningSource,
    ImLearningTarget,
    ImMessageBatch,
)


class _FakeSource:
    async def fetch_messages(
        self,
        target: ImLearningTarget,
        cursor: ImLearningCursor | None = None,
    ) -> ImMessageBatch:
        del cursor
        message = ImLearningMessage(
            channel_id=target.channel_id,
            msg_id="m1",
            conversation_external_id=target.external_id,
            content_text="hello",
            sent_at=1000,
            sender_account="alice",
        )
        return ImMessageBatch(
            messages=(message,),
            next_cursor=ImLearningCursor(message_id="m1", query_direction=0, count=50),
        )


def test_im_learning_source_protocol_accepts_host_implementation() -> None:
    source: ImLearningSource = _FakeSource()
    assert source is not None


def test_im_message_batch_is_read_only_contract() -> None:
    batch = ImMessageBatch(messages=())
    assert batch.messages == ()
    assert batch.next_cursor is None
