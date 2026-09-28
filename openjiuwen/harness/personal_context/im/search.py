"""Storage-agnostic search contract for the IM learning corpus.

This module must stay free of storage imports: no sqlite3, no SQL.
Implementations live in sibling modules (``sqlite_search.py``); the
agent-facing tool wraps this port (``search_tool.py``).

Contract decisions:

- keyword is required — browse-style queries without a keyword are out
  of scope.
- only messages inside the learning scope (``learning_eligible = 1``) are
  ever returned; the switch is not exposed to callers.
- ``conversation_refs`` are human-facing references (external_id or
  title substring); resolving them is the implementation's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ImSearchQuery:
    """One keyword-driven search over the learning-eligible IM corpus.

    ``since_ms`` / ``until_ms`` form a closed interval.  ``limit`` is capped
    by implementations (SQLite implementation caps at 50); paging is
    ``(limit, offset)``.
    """

    keyword: str
    channel_id: str | None = None
    conversation_refs: tuple[str, ...] = ()
    sender: str | None = None
    since_ms: int | None = None
    until_ms: int | None = None
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True)
class ImSearchHit:
    """One matched original message, enriched for agent consumption."""

    message_id: str
    channel_id: str
    conversation_id: str
    conversation_title: str | None
    sender_account: str | None
    sender_name: str | None
    is_self: bool | None
    sent_at: int
    content_text: str


class ImSearchPort(Protocol):
    """Search the learning-eligible IM corpus (storage-agnostic).

    Contract shared by every implementation:

    - only messages inside the learning scope are returned;
    - an empty ``keyword`` is a caller error;
    - returns ``(hits, total, truncated)``: ``total`` counts all matches of
      the full filter combination, ``truncated`` marks that more matches
      exist beyond the current ``(limit, offset)`` page.
    """

    def search(self, query: ImSearchQuery) -> tuple[list[ImSearchHit], int, bool]:
        ...


__all__ = ["ImSearchHit", "ImSearchPort", "ImSearchQuery"]