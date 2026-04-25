---
description: Logging conventions for agent-core. Enforces the single-entry logger, lazy placeholders, structured events, async context propagation, and config-reset workflow.
language: english
paths:
  - "openjiuwen/**/*.py"
---

# Logging Rules

All logging inside the framework **must** go through
`openjiuwen.core.common.logging`. See
`openjiuwen/core/common/logging/CLAUDE.md` for design details — this file
only lists enforceable hard rules.

## Single Entry Point

- Library code imports named loggers from `openjiuwen.core.common.logging`:
  `agent_logger` / `workflow_logger` / `llm_logger` / `tool_logger` /
  `memory_logger` / `session_logger` / `retrieval_logger` / `team_logger` / ...
- When no existing namespace fits, use `LogManager.get_logger("<name>")`, or
  add a new `LazyLogger` in `logging/__init__.py`.
- **Forbidden**: `print()`, `logging.getLogger(__name__)`, and direct
  `loguru.logger` references — all three bypass the configuration layer and
  backend switching.
- Exceptions: tests use `test_logger`; CLI scripts that drive stdin/stdout
  interaction may use `print`.

## Formatting and Levels

- Use **lazy placeholders**, not f-strings:
  `logger.debug("got %s items", count)` instead of
  `logger.debug(f"got {count} items")`. Formatting only runs when the level
  is active; f-strings evaluate unconditionally.
- Both `{}` and `%s` placeholders are supported (see
  `base_impl.StructuredLoggerMixin._auto_format_message`); do not mix them
  in a single message.
- For exception paths use `logger.exception("msg")` — do not assemble
  `traceback.format_exc()` manually.

## Structured Events

- Emit structured events through `create_log_event(event_type, **fields)`.
  Undeclared fields are silently dropped with a warning.
- **Add new fields to the dataclass in `events.py` first.** Stuffing them
  into `extra={...}` is wrong.
- For new event types: add enum values in `LogEventType` / `ModuleType`
  and map them to the corresponding dataclass in `EVENT_CLASS_MAP`.
  External modules register custom types via
  `register_event_class(str_key, cls)`.
- Always sanitize payloads with `sanitize_event_for_logging`; do not
  re-implement the redaction list in call sites or backends.

## Async Context

- Propagate `trace_id` / `member_id` via `set_session_id()` /
  `set_member_id()` (backed by `contextvars`, isolated across
  `asyncio.Task`).
- **Forbidden**: storing log context in `threading.local()` — it leaks
  across coroutines.
- Do not hand-inject `trace_id=...` in library code;
  `StructuredLoggerMixin` fills it automatically. Pass it explicitly only
  when overriding.

## Configuration Changes

- Swap the live logging config via `configure_log(path)` or
  `configure_log_config(dict)`. Both call `LogManager.reset()` so
  `LazyLogger` instances rebind on next use.
- **Forbidden**: mutating attributes on the `log_config` singleton, or
  calling `LogManager.reset()` from non-test code.

## Don'ts

- **Don't stash a `LoggerProtocol` instance on a long-lived object before
  config is loaded.** Module-level code should use `LazyLogger`; class-level
  code should resolve via `LogManager.get_logger` on demand. Cached loggers
  survive `LogManager.reset()` and point at dead handlers.
- **Don't add a new backend without registering it end-to-end.** The chain
  is `_BACKEND_LOADERS` + `_BACKEND_LOGGER_BUILDERS` in `log_config.py`,
  `normalize_logging_config` in `log_levels.py`, and
  `_get_logger_class_for_backend` in `manager.py`. Missing any one of them
  yields a silent fallback to `default`.
- **Don't open log files directly.** File sinks must go through
  `normalize_and_validate_log_path` so the `is_sensitive_path` guard runs.
