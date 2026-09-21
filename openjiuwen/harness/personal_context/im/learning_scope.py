"""Learning scope helpers for ``learning_eligible`` tagging at persist time.

The wire DTO (``ImLearningMessage``) carries no ``learning_eligible`` field
(decision D6): the tag is computed here in openjiuwen, immediately before
``persist_batch``.  Messages outside the learning window still land in the
corpus (tagged 0); only new collection stops when a target is removed from
the whitelist, per the review plan §5.4.
"""

from __future__ import annotations

from openjiuwen.harness.personal_context.im.models import ImLearningMessage, ImLearningTarget

TargetKey = tuple[str, str, str]  # (channel_id, kind, external_id)


def target_key(target: ImLearningTarget) -> TargetKey:
    """Stable identity of a learning target."""
    return (target.channel_id, target.kind, target.external_id)


def build_target_keys(targets: list[ImLearningTarget] | tuple[ImLearningTarget, ...]) -> set[TargetKey]:
    return {target_key(item) for item in targets}


def compute_learning_eligible(
    *,
    target: ImLearningTarget,
    message: ImLearningMessage,
    whitelist_keys: set[TargetKey],
    since_ms: int | None,
) -> int:
    """Return 1 if the message counts as distill corpus, else 0.

    Rules (mirrors the JiuwenSpirit semantics):
    - target not in the whitelist -> 0 (kept in corpus, not distilled);
    - ``since_ms`` set and ``sent_at < since_ms`` -> 0;
    - otherwise 1.

    Today the fetch provider only walks whitelist targets, so the first
    branch is structurally always True; the computation is kept for the
    future hosting ∪ learning union collection (Q3 / AS-04).
    """
    if target_key(target) not in whitelist_keys:
        return 0
    if since_ms is not None and int(message.sent_at or 0) < int(since_ms):
        return 0
    return 1


def compute_eligible_map(
    *,
    target: ImLearningTarget,
    messages: list[ImLearningMessage] | tuple[ImLearningMessage, ...],
    whitelist_keys: set[TargetKey],
    since_ms: int | None,
) -> dict[str, int]:
    """Compute msg_id -> eligible for a whole fetched batch."""
    return {
        msg.msg_id: compute_learning_eligible(
            target=target, message=msg, whitelist_keys=whitelist_keys, since_ms=since_ms
        )
        for msg in messages
        if msg.msg_id
    }


__all__ = [
    "TargetKey",
    "build_target_keys",
    "compute_eligible_map",
    "compute_learning_eligible",
    "target_key",
]
