"""DDL for the ``im_*`` table family owned by PersonalContext IM learning.

Design decisions (see ``DigitalAvatar/OJ02-OJ05迁移方案-v2.md``):

- No ``im_raw_records`` table (decision D2): the corpus never stores raw
  platform JSON payloads.  Messages can always be re-fetched through the
  learning source and ``normalize_batch`` is a pure function, so a buggy
  normalize can be fixed and the fetch re-run.
- No hosting tables (``im_turns`` / ``im_watermarks`` / ``im_panel_messages``):
  those belong to the AgentServer hosting pipeline, not to learning.
- ``im_learning_backfill`` carries the ``newest_seen_msg_id`` /
  ``newest_seen_sent_at`` columns (decision D3) used by the steady-state
  incremental fetch ("page down from newest until the seen boundary").
- ``im_stage_runs`` implements the generic staged run/lease state machine
  (decision in migration plan §5.3) shared by the fetch / index / distill
  stages.

Tables:
- im_conversations      Conversation registry (channel_id, external_id)
- im_messages           Message detail (single source of truth)
- im_changelog          Outbox (seq INTEGER AUTOINCREMENT)
- im_consumer_cursors   Consumer ack cursors
- im_messages_fts       FTS5 virtual table (normal, non-contentless)
- im_messages_fts_state rowid -> message_id mapping
- im_learning_backfill  Backfill state + resume cursor + newest-seen watermark
- im_stage_runs         Staged run/lease state machine
"""

from __future__ import annotations

import sqlite3

IM_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS im_conversations (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('group', 'user')),
    title TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    last_message_at_ms INTEGER,
    archived_at_ms INTEGER,
    visible_in_recent INTEGER NOT NULL DEFAULT 0 CHECK (visible_in_recent IN (0, 1)),
    unread_count INTEGER NOT NULL DEFAULT 0 CHECK (unread_count >= 0),
    UNIQUE (channel_id, external_id)
);

CREATE TABLE IF NOT EXISTS im_messages (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    sender_account TEXT,
    sender_name TEXT,
    content_text TEXT,
    content_type TEXT,
    sent_at INTEGER NOT NULL CHECK (sent_at >= 0),
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    is_self INTEGER,
    -- 1=可进蒸馏；0=学习范围外；NULL=未判定（蒸馏排除）。
    learning_eligible INTEGER DEFAULT 1 CHECK (
        learning_eligible IS NULL OR learning_eligible IN (0, 1)
    ),
    created_at INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES im_conversations(id) ON DELETE CASCADE,
    UNIQUE (channel_id, external_id)
);

CREATE TABLE IF NOT EXISTS im_changelog (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    op TEXT NOT NULL CHECK (op IN ('upsert', 'delete')),
    entity_type TEXT NOT NULL CHECK (entity_type = 'message'),
    entity_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    domain TEXT NOT NULL CHECK (domain = 'chat'),
    occurred_at INTEGER NOT NULL,
    emitted_at INTEGER NOT NULL,
    payload_ref TEXT,
    digest TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS im_consumer_cursors (
    consumer_id TEXT PRIMARY KEY,
    acked_seq INTEGER NOT NULL DEFAULT 0,
    lease_until INTEGER,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS im_messages_fts_state (
    rowid_alias INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    indexed_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_im_messages_conversation_sent
ON im_messages(conversation_id, sent_at);

CREATE INDEX IF NOT EXISTS idx_im_messages_channel_external
ON im_messages(channel_id, external_id);

CREATE INDEX IF NOT EXISTS idx_im_messages_learning_sent
ON im_messages(learning_eligible, sent_at);

CREATE INDEX IF NOT EXISTS idx_im_conversations_last_message
ON im_conversations(last_message_at_ms);

CREATE INDEX IF NOT EXISTS idx_im_changelog_domain_seq
ON im_changelog(domain, seq);

CREATE TABLE IF NOT EXISTS im_learning_backfill (
    channel_id TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('group', 'user')),
    external_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'complete', 'truncated')),
    truncated INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0, 1)),
    oldest_sent_at_ms INTEGER,
    -- 断点续传游标：上次拉到的最老消息的 msgId，回填期从此处继续向前翻。
    oldest_msg_id TEXT,
    -- 常态增量水位（D3）：上次已见的最新消息，常态翻页以此为终止边界。
    newest_seen_msg_id TEXT,
    newest_seen_sent_at INTEGER,
    query_count INTEGER,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (channel_id, target_kind, external_id)
);

CREATE TABLE IF NOT EXISTS im_stage_runs (
    id TEXT PRIMARY KEY,
    stage TEXT NOT NULL CHECK (stage IN ('fetch', 'index', 'distill')),
    -- source_key 打包为 "channel_id:target_kind:external_id"；全局阶段用 "-"。
    source_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'succeeded', 'failed')
    ),
    run_token TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at_ms INTEGER,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    window_key TEXT,
    started_at_ms INTEGER,
    finished_at_ms INTEGER,
    last_error TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_im_stage_runs_stage_source
ON im_stage_runs(stage, source_key, status);
"""

# FTS5 virtual table. We use a non-contentless FTS5 table because the
# changelog supports delete ops and contentless FTS5 DELETE requires
# SQLite >= 3.43 (the bundled runtime may be older).  ``seg`` is the only
# indexed column; original content is fetched back through
# ``im_messages_fts_state`` -> ``im_messages``.
IM_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS im_messages_fts USING fts5(
    seg
);
"""


def init_im_schema(conn: sqlite3.Connection) -> None:
    """Create the im_* tables on ``conn`` if missing.  Idempotent."""
    conn.executescript(IM_SCHEMA_SQL)
    conn.executescript(IM_FTS_SQL)
    conn.commit()


__all__ = ["init_im_schema", "IM_SCHEMA_SQL", "IM_FTS_SQL"]
