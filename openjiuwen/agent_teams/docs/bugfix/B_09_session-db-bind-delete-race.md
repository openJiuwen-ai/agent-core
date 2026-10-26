# Concurrent session cleanup can break another session's bind

## Metadata

| Item | Value |
|---|---|
| Date | 2026-10-26 |
| Severity | Medium; a concurrent cleanup can interrupt an unrelated session startup/stream |
| Scope | `agent_teams/tools/database/engine.py`, `agent_teams/tools/database/__init__.py`, `agent_teams/agent/session_manager.py`, `agent_teams/i18n.py`, `core/runner/team_runner.py`, focused unit tests |
| Tests | `test_database_concurrency.py`, `test_session_manager.py`, and `test_runner_team_runtime.py`: 98 passed |
| Reference | GitCode issue #4956 |

## Symptom

When one session binds while another session's tables are being deleted, a healthy session can fail to start. The error occurs during the legacy message-column migration, before the member runtime is ready.

## Root cause

The protocol and meta backfills used `Inspector.get_table_names()` to enumerate every `team_message_*` table in the database and then called `get_columns()` for each result. Session cleanup correctly targets only the requested session's tables, but it can delete one of those tables after the bind migration takes its table-name snapshot. `get_columns()` then raises SQLAlchemy `NoSuchTableError`.

`create_cur_session_tables()` retries lock, pool, and watchdog timeouts, but not this reflection race. The exception therefore escaped and aborted bind.

## Fix

1. `_ensure_message_protocol_column()` and `_ensure_message_meta_column()` now derive the current session's message table and only inspect that table. Another session's cleanup no longer intersects this bind's migration scan.
2. Each migration locally catches `NoSuchTableError` from `get_columns()` and skips the disappeared table. The exception is not added to the whole-DDL retry tuple.
3. `create_cur_session_tables()` now returns `True` on success and `False` when no session is bound or the retry budget is exhausted. `TeamDatabase` preserves that result.
4. `SessionManager.bind_session()` checks the result. On failure it resets the session context and live-session state, then raises a unified `AGENT_TEAM_EXECUTION_ERROR`.
5. Streaming and non-streaming callers use the existing framework exception path. The Runner does not create a DB-specific `chat.error` frame.

## Verification

Regression tests cover migration scope, legacy-column backfill, a table disappearing during `get_columns()`, explicit failure after retry exhaustion, SessionManager rollback of the context binding, and streaming error-frame conversion.

## Remaining boundary

The migration fix addresses cleanup of a different session. Same-session bind and delete still use different serialization paths (`DbSessions.write()` versus an engine transaction); if that lifecycle overlap is supported, it needs a separate locking decision.
