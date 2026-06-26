# Agent Team Tools

## Module Map

### Tool implementation (domain split)

| File | Owns |
|---|---|
| `tool_base.py` | `TeamTool` ABC, `MappedToolOutput` |
| `tool_permissions.py` | Permission sets (`LEADER_*`, `MEMBER_*`, `SHARED_TOOLS`, `HUMAN_AGENT_TOOLS`), `_MEMBER_NAME_PATTERN` |
| `tool_team.py` | `BuildTeamTool`, `CleanTeamTool` |
| `tool_member.py` | `_SpawnToolBase`, `SpawnTeammateTool`, `SpawnHumanAgentTool`, `SpawnBridgeAgentTool`, `SpawnExternalCliTool`, `ShutdownMemberTool`, `ApprovePlanTool`, `ApproveToolCallTool`, `ListMembersTool` |
| `tool_task.py` | `TaskCreateTool`, `ViewTaskToolV2`, `UpdateTaskTool`, `SubmitPlanTool`, `ClaimTaskTool`, `MemberCompleteTaskTool` |
| `tool_message.py` | `SendMessageTool` (point-to-point, multicast, broadcast) |
| `tool_factory.py` | `create_team_tools` factory, `_wrap_invoke_with_logging` |
| `team_tools.py` | Backward-compat re-export shim — re-exports all public symbols from the domain files above; existing `from ... team_tools import ...` call sites continue to work unchanged |

### Infrastructure

| File | Owns |
|---|---|
| `team.py` | `TeamBackend` — the backend object every tool talks to (spawn/shutdown/clean/approve, roster queries, cleanup-path registry). `startup_member` (single UNSTARTED→STARTING CAS + spawn), `startup` (batch via `startup_member`), `_spawn_and_publish` (shared helper). `approve_tool` writes `protocol="json"` DB message as interrupt-resolving fallback delivery |
| `task_manager.py` | `TeamTaskManager` — add/claim/complete/reset/cancel/approve_plan, event publishing, dependency refresh |
| `message_manager.py` | `TeamMessageManager` — point-to-point + broadcast send, read-state queries |
| `database/` | `TeamDatabase` + per-table DAOs over a shared `DbSessions` (read/write session split — see *Database concurrency* below). SQL layer for static + per-session dynamic tables |
| `memory_database.py` | In-memory backend variant for tests |
| `models.py` | `Team`, `TeamMember` static tables + dynamic per-session `TeamTask*` / `TeamMessage*` factories |
| `member_options.py` | `TeamMemberOptions` / `MemberModelRef` / `MemberWorktreeOptions` structured options helpers (load/dump/build/merge/get_member_model_ref/get_member_permissions_override). Replaces legacy `model_ref_json` column with unified `options` JSON |
| `structured_output_tool.py` | `StructuredOutputTool` (`input_params=schema_json`, captures `captured`) + `StructuredOutputFinishRail` (force-finish a round once captured). The generic structured-output tool for any agent with no native `response_format`; reused by swarmflow workers/sessions and tiny agents (`tiny_agent.py`) |
| `locales/` | i18n strings (`cn.py`, `en.py`) and Markdown description files (`descs/<lang>/<tool>.md`) |

Tools never reach into `TeamDatabase` directly — they go through `TeamBackend` or one of the managers so event publication and state transitions stay centralised.

### Database concurrency (SQLite write serialisation)

SQLite allows a single writer at a time (a database-level lock). The
in-process team runtime shares one engine + connection pool across every
member, so the DB layer is built around SQLite's model instead of fighting
it with a pool of would-be parallel writers:

- **`DbSessions` (`database/engine.py`)** — one provider shared by all four
  DAOs, exposing `read()` / `write()` async-context accessors. `write()`
  holds a process-wide `asyncio.Lock`; `read()` does not. Only one
  connection is ever on the write path (no busy-timeout back-off pinning a
  checked-out connection), and the rest of the pool serves concurrent WAL
  reads. This is what stops `QueuePool limit ... timed out` exhaustion under
  multi-member load. The write lock is **non-reentrant**: when a public
  write delegates to another (`add_task_with_bidirectional_dependencies` →
  `mutate_dependency_graph`, `verify_and_fix_task_consistency` →
  `_verify_and_fix_blocked_tasks`), only the innermost session opener takes
  the lock.
- **PRAGMA (`engine.py` `connect` event)** — file-backed SQLite runs
  `journal_mode=WAL` (database-level, set once on first connect) +
  `synchronous=NORMAL` (connection-level, set on every connect). NORMAL is
  the safe, high-throughput WAL pairing; FULL's per-commit fsync is the
  throughput ceiling.
- **Batched writes** — each `COMMIT` is one fsync, the dominant write cost.
  `MessageDao.mark_messages_read` marks a whole mailbox drain in one
  transaction (one fsync) instead of one per message; the coordination
  `MessageHandler` collects delivered ids and flushes them once.
- **`retry_on_locked` (`engine.py`)** — bounded back-off for a transient
  `database is locked`; the sleep runs outside the session block so a retry
  never pins a checked-out connection. Rare once writes are serialised —
  only WAL-checkpoint edges or a foreign process touching the same file.

Applicability: this serialisation targets the in-process (single event
loop) runtime. A genuinely high-concurrency-write deployment should use the
PostgreSQL / MySQL backend (`engine.py`), not SQLite.

## Tool Catalogue & Role Filters

`create_team_tools(role=..., teammate_mode=..., lifecycle=..., exclude_tools=..., lang=...)` is the single entry point. It builds every tool once and filters by role.

| Tool | Leader | Teammate | Notes |
|---|---|---|---|
| `build_team` | ✓ | | entry point — description carries the full workflow |
| `clean_team` | ✓ (temporary only) | | requires every teammate shutdown first; not wired for `lifecycle="persistent"` (operator tears those down via SDK facades) |
| `spawn_teammate` | ✓ | | spawn an ordinary LLM teammate; optional `model_config_allocator` callback; flat schema `member_name`/`display_name`/`desc`/`prompt?`/`model_name?`. Always wired |
| `spawn_human_agent` | ✓ | | spawn a HITT human member; schema is `member_name`/`display_name`/`desc` only (no `model_name`/`prompt`); wired only when `hitt_enabled()` |
| `spawn_bridge_agent` | ✓ | | spawn a bridge to a remote agent; `desc` doubles as the connect briefing; optional `mailbox_inject_mode`/`protocol`/`adapter_config`/`model_name`; wired only when `bridge_enabled()` |
| `spawn_external_cli` | ✓ | | spawn a third-party CLI teammate; requires `cli_agent` (a kind declared in `TeamAgentSpec.external_cli_agents`) + `desc`; wired only when `external_cli_kinds()` is non-empty |
| `shutdown_member` | ✓ | | `force=True` skips the normal shutdown sequence |
| `approve_plan` | ✓ (plan_mode only) | | wired only when `teammate_mode == "plan_mode"` |
| `approve_tool` | ✓ (plan_mode only) | | same gating as `approve_plan` |
| `list_members` | ✓ | | excludes the caller from the result |
| `create_task` | ✓ | | auto-routes `depended_by`-bearing specs to `add_with_priority`; single-spec returns `brief()`, batch returns `tasks`+`failures` |
| `update_task` | ✓ | | one tool handles title/content edit, cancel, assign (with reassignment reset), and `add_blocked_by` |
| `view_task` | ✓ | ✓ | `action ∈ {list, get, claimable}`; default `list` |
| `claim_task` | | ✓ | `status ∈ {claimed, completed}`; completion path appends a next-step nudge |
| `send_message` | ✓ | ✓ | `to == "*"` → broadcast; leader call auto-starts UNSTARTED members. Also attached to `human_agent` as a user-driven relay channel — the HITT prompt section forbids autonomous use; only user-issued "tell `<member>` …" instructions may trigger it. |
| `member_complete_task` | | | `human_agent` only — self-only task completion |
| `workspace_meta` | ✓ | ✓ | workspace lock + version history |
| `async_tasks_list` | ✓ | | list background async tasks; leader-only, always wired |
| `async_task_output` | ✓ | | fetch a task's full output (`block`/`timeout`; reads disk spill) |
| `async_task_cancel` | ✓ | | cancel a still-running background async task |

Plan-mode gating is enforced in the factory:

```python
if role == "leader" and teammate_mode != "plan_mode":
    allowed = allowed - {"approve_plan", "approve_tool"}
```

Persistent-team gating is enforced in the same factory, right after:

```python
if lifecycle == "persistent":
    allowed = allowed - {"clean_team"}
```

Rationale: persistent teams live across rounds and are torn down by the
operator through SDK facades (`delete_agent_team` etc.). Exposing a
leader-callable `clean_team` mid-round would race the runtime pool
invariants and silently de-register a team the operator still considers
live. Temporary teams keep the tool — they have no external operator;
the leader is the only one who can wind them down.

`TeamBackend.__init__` takes a keyword-only `on_team_cleaned` async
callback fired **only** on the `clean_team` success path (best-effort —
a raising callback is logged, not propagated). The hosting `TeamAgent`
wires it to latch `TeamAgentState.team_cleaned` synchronously inside the
leader's round, which is how a temporary-team leader ends its own stream
after `clean_team` (the leader ignores its own `TeamCleanedEvent`). See
`docs/specs/S_08` + `docs/features/F_10`.

`TeamBackend.__init__` also takes keyword-only `on_team_built`, fired
after `build_team` creates the DB row and initial members. The hosting
`TeamAgent` uses `on_team_built` / `on_team_cleaned` to persist checkpoint
`db_state` (`created` / `cleaned`) while keeping checkpoint writes inside
agent-core rather than the outer caller. `on_team_cleaned` fires
immediately after the team DB row is deleted, before best-effort
filesystem cleanup and event publishing.

Worktree tools (`enter_worktree`, `exit_worktree`) live in `openjiuwen.harness.tools.worktree` for non-team callers. Team teammate worktree isolation is created by the leader-side spawn host through `isolation="worktree"` and is not mounted as manual teammate tools. There is nothing left to maintain in `tools/locales/descs/` for these two tools.

## Tool Design Principles

Reference: `prompt_design.md` / `system_prompt_now.md` next to this file for the live description-writing examples.

### Description = Behavior Contract

Tool description is not just a feature summary — it is a **behavior contract** for the LLM. Every description should include:

1. **Use-case enumeration** — when to call this tool (e.g. "for task notifications, progress reports, escalation of blockers")
2. **Behavioral constraints** — what NOT to do (e.g. "your plain text output is NOT visible to other agents", "refer to members by name, never by internal ID")
3. **Cost signals** — mark expensive operations explicitly (e.g. "broadcast — expensive, linear in team size")

### Entry-Point Tools Carry the Workflow

For the "entry-point" tool of a subsystem (e.g. `build_team` for team collaboration), the description should contain the **full workflow specification** — call order, design principles, state machine, notification semantics, idle handling — not just a feature summary. Rationale (validated by `BuildTeamTool`):

- The LLM sees the workflow in context when it's about to call the tool, not buried in a system prompt it may have forgotten.
- Avoids duplicating workflow steps across system prompt and tool description.
- The system prompt (`leader_policy.md`) stays focused on **role identity and decision principles**; the tool description owns **operational procedure**.

Concrete sections for an entry-point tool description:

| Section | Purpose |
|---|---|
| Call order | Prevent wrong sequencing (e.g. "build_team before create_task before spawn_teammate") |
| Design principles | Constrain how the LLM designs tasks (goal-oriented, single-owner, coarse-grained) |
| Workflow steps | Full numbered procedure from start to shutdown |
| Notification semantics | "Messages auto-delivered, don't poll" — kill the LLM's urge to busy-wait |
| Idle state | "Idle is normal, not an error" — prevent over-reaction to expected silence |

### Anti-Pattern Correction in Descriptions

LLMs have predictable failure modes. Tool descriptions should proactively address them:

| Pattern | Example | Why it works |
|---|---|---|
| **Encourage action** | "Call as soon as you have a goal — don't hesitate" | Lowers decision paralysis |
| **Kill busy-waiting** | "Don't poll for replies, system notifies you" | Prevents token-wasting loops |
| **Normalize silence** | "Idle is normal, not an error" | Stops premature shutdown/nudging |
| **Force the channel** | "Plain text is NOT visible — you MUST call this tool" | Ensures correct communication path |
| **Signal cost** | "Broadcast — expensive, linear in team size" | Discourages overuse |

### Parameter Merging

If two parameters are hard for the LLM to distinguish semantically, merge them. Example: `desc` ("what the team does") and `prompt` ("directives for members") were merged into a single `desc` field — the LLM consistently confused the boundary, and a single field performs equally well. The DB column for the removed parameter is kept nullable for backward compatibility.

### One Tool, One Concern — But Don't Split What Differs Only by a Parameter

If two tools share the same schema structure and only differ by one parameter value (e.g. `send_message` vs `broadcast_message` differ only by `to: name` vs `to: "*"`), merge them into one tool. Use the parameter to dispatch, not a separate tool registration. `update_task` is the canonical example — it collapses cancel / edit / assign / add-dep into one call with optional fields.

### Input Validation

Validate at the tool boundary, fail fast with clear errors:

| Priority | What to check |
|---|---|
| 1 | Required fields non-empty |
| 2 | Entity existence (e.g. target member exists) |
| 3 | Business rules (e.g. task must be in correct status) |

Error message conventions:
- Validation errors: `"'field_name' is required"` — quote the field name, lowercase
- Entity errors: `"Member 'dev-1' not found"` — entity type capitalized, value quoted
- Operation failures: `"Failed to send message to 'dev-1'"` — `Failed to` prefix
- Internal exceptions: `"Internal error: {e}"` — catch-all, log the exception, don't expose stack traces

### Structured Success Output

Always return meaningful `data` on success, not bare `ToolOutput(success=True)`. Include `type` to distinguish dispatch paths (e.g. `"message"` vs `"broadcast"`), and echo key routing info (`from`, `to`, `summary`) for logging and downstream processing.

### Exception Handling

Wrap `invoke` body in `try/except` to catch unexpected errors from backend services. Log the exception via `team_logger.error`, return `ToolOutput(success=False, error=...)`. Never let an unhandled exception propagate — tool invocations must always return a `ToolOutput`.

## Result Mapping: `TeamTool.map_result` + `_wrap_invoke_with_logging`

`TeamTool` subclasses return a raw `ToolOutput` from `invoke()`. The factory runs every tool through `_wrap_invoke_with_logging` (see `team_tools.py:1037`), which:

1. Logs the inputs/outputs at debug level.
2. Calls `tool.map_result(output)` to produce model-facing text.
3. Wraps the result in `MappedToolOutput`, whose `__str__` returns the mapped text.

The ability layer renders tool results with `str(result)` — `MappedToolOutput.__str__` is what actually becomes the LLM-visible `ToolMessage.content`. `ToolOutput.data` is still present for programmatic consumers (events, logs).

> **External members reuse these exact tools (F_26).** The `mcp/` server and
> `skill/` CLI, in their `member` scope, expose the real `create_team_tools(role="teammate")`
> instances (`view_task` / `claim_task` / `send_message`) and return `str(await tool.invoke(...))`
> — so `map_result()` is the single source of LLM-facing text across in-process and
> external CLI members. Keep tool descriptions / `map_result` role-neutral enough to read
> well for a third-party CLI member too, not just an in-process DeepAgent.

`map_result` strategies in this module:

| Pattern | Tools | Strategy |
|---|---|---|
| **Pure text** | `build_team`, `clean_team`, the four `spawn_*` tools, `shutdown_member`, `approve_*` | Confirmation sentence — minimal tokens |
| **Structured text lines** | `list_members`, `view_task` (list), `create_task` (batch) | One entity per line, dense format |
| **Detail text** | `view_task` (get) | Full fields with labeled lines |
| **Time context** | `view_task` (list + get) | Both tiers render `updated_at` via `timefmt.format_time_context` as `<absolute local time> (<relative diff>)`. `map_result` can't take args, so it calls `get_current_time()` internally and guards on `updated_at is not None` |
| **Text + behavior guidance** | `claim_task` (completed) | Append `Call view_task now…` after task completion to sustain the autonomous task loop |
| **Default JSON** | `TeamTool` base | `json.dumps(output.data)` fallback for anything that forgot to override |

Design principles:
- **Token efficiency**: only send what the model needs for its next decision.
- **Behavior guidance injection**: keep these nudges in `map_result`, not in the descriptor — they fire only when the terminal state is reached.
- **Error semantics**: error results return plain error text, never `is_error`-style flags that could cascade to sibling tool cancellation.

## i18n

Locale files live in `locales/` — flat `STRINGS` dict per language (`cn.py`, `en.py`). See `locales/__init__.py` for the resolver.

- Key convention: `tool_name._desc` for ToolCard description, `tool_name.param` for params, `tool_name.nested.param` for nested schema params.
- Multi-line descriptions use `"""\` (triple-quote with backslash continuation), not parenthesized string concatenation.
- `make_translator(lang)` returns a closure — no global state, safe for concurrent multi-language use in one process.
- All tool constructors take `t: Translator` as a parameter.
- Missing keys raise `KeyError` at construction time — fail loud, not silent.
- Missing `_desc` (no Markdown file and no dict entry) raises `FileNotFoundError` with both lookup paths in the message — the resolver tells you exactly what to create.

### Markdown description files

Long `_desc` entries can live in Markdown files under `locales/descs/<lang>/<tool_name>.md` instead of the `STRINGS` dict. Markdown files take precedence over dict entries when both exist. This is optional — short descriptions and parameter strings stay in `cn.py`/`en.py`.

- File naming: `descs/cn/build_team.md` → resolves as `build_team._desc` for lang `"cn"`.
- Files are loaded via `PromptTemplate` (same as `agent_teams/prompts/`) and cached with `@cache`.
- Supports `{{placeholder}}` interpolation — pass keyword arguments through `t("tool", param="value")`.
- When migrating a `_desc` from `STRINGS` to a `.md` file, delete the dict entry and leave a comment.

Current `descs/` population: `approve_plan`, `approve_tool`, `build_team`, `claim_task`, `clean_team`, `create_task`, `enter_worktree`, `exit_worktree`, `list_members`, `send_message`, `shutdown_member`, `spawn_bridge_agent`, `spawn_external_cli`, `spawn_human_agent`, `spawn_teammate`, `update_task`, `view_task`, `workspace_meta`, `async_tasks_list`, `async_task_output`, `async_task_cancel`.

## Prompt Layering: Tool Description vs System Prompt

| Layer | Owns | Example file |
|---|---|---|
| Tool description (`locales/descs/`) | Operational procedure, call order, workflow steps, anti-patterns, usage scenarios | `build_team.md` |
| System prompt (`agent_teams/prompts/`) | Role identity, decision principles, state transitions | `leader_policy.md` |

Rule: **don't duplicate content across layers**. If the workflow lives in the tool description, the system prompt should not repeat it.

### Unified Read Tools: Action Dispatch with Tiered Output

When merging multiple read-only tools into one (e.g. `TaskList` + `TaskGet` → `view_task`), use an `action` enum to dispatch, and **tier the output by action**:

- **list action** — summary view: return only routing/identity fields (id, title, status, assignee, `updated_at`) plus dependency edges (`blocked_by`). `updated_at` is a lightweight routing field (one int) the list view renders as a relative time so a member can spot a stalled task; omit heavyweight fields like `content` and internal fields like `team_id`. This keeps token cost low for the common "scan all tasks" call.
- **get action** — detail view: return full content plus bidirectional dependency info (`blocked_by` + `blocks`). This is the only path that returns `content`.
- **Default action** — choose the most common zero-parameter use case as the default. `view_task` defaults to `list`.

Reference implementation: `ViewTaskToolV2` with `action ∈ {list, get, claimable}`.

### Tool Description Structure for Read Tools

Read tool descriptions follow a 4-section structure:

| Section | Content |
|---|---|
| **When to Use** | Per-action use-case enumeration (action=list: "check progress, find bottlenecks"; action=get: "get requirements before starting work") |
| **Output** | Per-action field listing — explicitly state what is and isn't included (e.g. "list omits content") |
| **Tips** | Token cost guidance, dependency semantics, task ordering heuristics |
| **Teammate Workflow** | Numbered steps for the most common agent workflow (list → find → claim → get → work) |

### Return Schema with BaseModel

Tool return data must be defined as Pydantic BaseModel classes in `schema/task.py` (or the relevant `schema/` module), not as raw dicts:

- **Summary model** (`TaskSummary`) — lightweight fields for list views
- **Detail model** (`TaskDetail`) — full fields for single-entity views
- **List result model** (`TaskListResult`) — wraps summary list + count

Backend methods (`task_manager.py`) return typed models. Tool `invoke()` calls `model.model_dump()` (with `exclude_none=True` for detail views) before passing to `ToolOutput(data=...)`. This gives type safety at the boundary without coupling `ToolOutput.data` to a specific schema.

### Result Wrappers for Backend Methods

`TeamBackend` / `TeamTaskManager` methods don't return raw `bool` — they return result wrappers:

- `MemberOpResult` — `ok`, `reason`; used by the four `spawn_*` tools and `shutdown_member`.
- `TaskCreateResult` — `ok`, `reason`, plus `task` which proxies attribute access via `__getattr__` so old `result.task_id` call sites still work.
- `TaskOpResult` — `ok`, `reason`; used by `claim`, `complete`, `reset`, `assign`, `update_task`, `approve_plan`, `add_dependencies`.

Tool `invoke()` must propagate `result.reason` into `ToolOutput.error` on failure — that's the channel the LLM uses to diagnose what went wrong. Returning a generic "Operation failed" here swallows the backend's diagnostic.

## ToolCard ID Convention

All team tool IDs use `team.{name}` format (e.g. `team.send_message`, `team.create_task`). Keep this consistent when adding a new tool — downstream wiring (rails, logging, UI labels) parses the prefix.
