"""Merge queued team input into a steer; task ownership stays on the task board."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass


def metadata(row):
    value = getattr(row, "meta", None)
    return json.loads(value) if isinstance(value, str) and value else (value or {})


@dataclass
class FollowUpMergeBatch:
    token: str
    content: str
    runtime_sequences: set[int]
    message_ids: list[str]


class TeamFollowUpMerger:
    """Delivery receipts only; the native supervisor owns its input FIFO."""

    def __init__(self, message_manager):
        self.manager = message_manager

    async def prepare(self, steer_content, steer_id, queued, *, allow_mailbox=True):
        manager = self.manager
        member = manager.member_name
        rows = await manager.get_team_messages(manager.team_name)
        by_id = {row.message_id: row for row in rows}
        trigger = by_id.get(steer_id)
        cutoff = trigger.timestamp if trigger is not None else max((r.timestamp for r in rows), default=0)
        runtime = {}
        synthetic = []
        for item in queued:
            message_id = item.delivery_id
            if message_id is None:
                message_id = str(uuid.uuid4())
                await manager.db.message.create_message(
                    message_id, manager.team_name, "runtime", str(item.content),
                    to_member_name=member, is_read=True,
                    meta={"private_delivery_mode": "follow_up", "runtime_merge_source": True,
                          "delivery_feedback": {member: {"state": "submitted", "mode": "follow_up"}}},
                )
                by_id[message_id] = await manager.db.message.get_message(message_id)
                synthetic.append(message_id)
            runtime[message_id] = item.seq
        ids = []
        for row in sorted(by_id.values(), key=lambda row: (row.timestamp, row.message_id)):
            if row.message_id == steer_id:
                continue
            meta = metadata(row)
            entry = meta.get("delivery_feedback", {}).get(member, {})
            mode = entry.get("mode") or meta.get("private_delivery_mode")
            addressed = row.to_member_name == member or (row.broadcast and member in meta.get("delivery_feedback", {}))
            mailbox = (allow_mailbox and addressed and row.timestamp <= cutoff and mode == "follow_up"
                       and not meta.get("template") and not entry.get("handled")
                       and entry.get("state", "pending") in {"pending", "deferred", "merging"})
            if row.message_id in runtime or mailbox:
                ids.append(row.message_id)
        if not ids:
            return None
        token = str(uuid.uuid4())
        accepted = await manager.db.message.reserve_merge(ids, member, token, steer_id, set(runtime), set(synthetic)) if ids else []
        if not accepted:
            return None
        contexts = []
        for message_id in accepted:
            row = by_id[message_id]
            meta = metadata(row)
            entry = meta.get("delivery_feedback", {}).get(member, {})
            contexts.append({"message_id": message_id, "sender": row.from_member_name,
                             "original_mode": "follow_up", "timestamp": row.timestamp,
                             "target_tasks": entry.get("target_tasks", meta.get("target_tasks", {})),
                             "content": row.content})
        policy = (
            "FOLLOW_UP_CONTEXT: These earlier follow-up messages are included for context, NOT proof of "
            "execution or completion. Judge their relevance against the current task board, contract version, "
            "and newer authoritative instructions. Keep explicit after-task timing. A new steer alone does "
            "not supersede every earlier request. If information is obsolete, use the latest applicable "
            "requirements; if instructions conflict, ask the leader. Use the existing task board to track "
            "work. Do not create a second todo queue, request receipts, or acknowledge each merged message."
        )
        content = json.dumps({"follow_up_policy": policy, "follow_up_context": contexts,
                              "current_steer": {"message_id": steer_id, "content": steer_content}}, ensure_ascii=False)
        return FollowUpMergeBatch(token, content, {runtime[mid] for mid in accepted if mid in runtime}, accepted)

    async def finish(self, batch, *, accepted):
        await self.manager.db.message.finish_merge(batch.message_ids, self.manager.member_name,
                                                   batch.token, accepted=accepted)
