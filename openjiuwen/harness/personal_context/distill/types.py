"""Shared types for distill corpus and analysis."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CorpusMessage:
    """One IM-like message used as distill evidence."""

    id: str
    channel_id: str
    conversation_id: str
    content_text: str
    sent_at_ms: int
    is_self: bool | None
    sender_account: str | None = None
    sender_name: str | None = None
    learning_eligible: int = 1


@dataclass(frozen=True, slots=True)
class DistillCandidates:
    persona_md: str
    work_md: str
    # Honest provenance from the analyzer: how many messages actually reached
    # the prompt, and how large the materials user message was (None = a
    # custom analyzer that does not report).
    prompt_message_count: int | None = None
    materials_chars: int | None = None
