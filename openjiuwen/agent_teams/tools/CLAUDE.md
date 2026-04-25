# Agent Team Tools

## Module Map

| File | Owns |
|---|---|
| `team_tools.py` | `TeamTool` base, `MappedToolOutput`, every `TeamTool` subclass, `create_team_tools` factory, permission sets (`LEADER_*`, `MEMBER_*`, `SHARED_TOOLS`) |
| `team.py` | `TeamBackend` — the backend object every tool talks to (spawn/shutdown/clean/approve, roster queries, cleanup-path registry) |
| `task_manager.py` | `TeamTaskManager` — add/claim/complete/reset/cancel/approve_plan, event publishing, dependency refresh |
| `message_manager.py` | `TeamMessageManager` — point-to-point + broadcast send, read-state queries |
| `database.py` | `TeamDatabase` — SQL layer (static + per-session dynamic tables) |
| `memory_database.py` | In-memory backend variant for tests |
| `models.py` | `Team`, `TeamMember` static tables + dynamic per-session `TeamTask*` / `TeamMessage*` factories |
| `locales/` | i18n strings (`cn.py`, `en.py`) and Markdown description files (`descs/<lang>/<tool>.md`) |

Tools never reach into `TeamDatabase` directly — they go through `TeamBackend` or one of the managers so event publication and state transitions stay centralised.

## Tool Catalogue & Role Filters

`create_team_tools(role=..., teammate_mode=..., exclude_tools=..., lang=...)` is the single entry point. It builds every tool once and filters by role.

| Tool | Leader | Teammate | Notes |
|---|---|---|---|
| `build_team` | ✓ | | entry point — description carries the full workflow |
| `clean_team` | ✓ | | requires every teammate shutdown first |
| `spawn_member` | ✓ | | takes optional `model_config_allocator` callback |
| `shutdown_member` | ✓ | | `force=True` skips the normal shutdown sequence |
| `approve_plan` | ✓ (plan_mode only) | | wired only when `teammate_mode == "plan_mode"` |
| `approve_tool` | ✓ (plan_mode only) | | same gating as `approve_plan` |
| `list_members` | ✓ | | excludes the caller from the result |
| `create_task` | ✓ | | auto-routes `depended_by`-bearing specs to `add_with_priority`; single-spec returns `brief()`, batch returns `tasks`+`failures` |
| `update_task` | ✓ | | one tool handles title/content edit, cancel, assign (with reassignment reset), and `add_blocked_by` |
| `view_task` | ✓ | ✓ | `action ∈ {list, get, claimable}`; default `list` |
| `claim_task` | | ✓ | `status ∈ {claimed, completed}`; completion path appends a next-step nudge |
| `send_message` | ✓ | ✓ | `to == "*"` → broadcast; leader call auto-starts UNSTARTED members |
| `workspace_meta` | ✓ | ✓ | workspace lock + version history |

Plan-mode gating is enforced in the factory:

```python
if role == "leader" and teammate_mode != "plan_mode":
    allowed = allowed - {"approve_plan", "approve_tool"}
```

Worktree tools (`enter_worktree`, `exit_worktree`) have locale files under `descs/` but are currently commented out in the permission set — keep the Markdown descriptions in sync if they are re-enabled.

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
| Call order | Prevent wrong sequencing (e.g. "build_team before create_task before spawn_member") |
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

`map_result` strategies in this module:

| Pattern | Tools | Strategy |
|---|---|---|
| **Pure text** | `build_team`, `clean_team`, `spawn_member`, `shutdown_member`, `approve_*` | Confirmation sentence — minimal tokens |
| **Structured text lines** | `list_members`, `view_task` (list), `create_task` (batch) | One entity per line, dense format |
| **Detail text** | `view_task` (get) | Full fields with labeled lines |
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
- Files are loaded via `PromptTemplate` (same as `agent/prompts/`) and cached with `@cache`.
- Supports `{{placeholder}}` interpolation — pass keyword arguments through `t("tool", param="value")`.
- When migrating a `_desc` from `STRINGS` to a `.md` file, delete the dict entry and leave a comment.

Current `descs/` population: `approve_plan`, `approve_tool`, `build_team`, `claim_task`, `clean_team`, `create_task`, `enter_worktree`, `exit_worktree`, `list_members`, `send_message`, `shutdown_member`, `spawn_member`, `update_task`, `view_task`, `workspace_meta`.

## Prompt Layering: Tool Description vs System Prompt

| Layer | Owns | Example file |
|---|---|---|
| Tool description (`locales/descs/`) | Operational procedure, call order, workflow steps, anti-patterns, usage scenarios | `build_team.md` |
| System prompt (`agent/prompts/`) | Role identity, decision principles, state transitions | `leader_policy.md` |

Rule: **don't duplicate content across layers**. If the workflow lives in the tool description, the system prompt should not repeat it.

### Unified Read Tools: Action Dispatch with Tiered Output

When merging multiple read-only tools into one (e.g. `TaskList` + `TaskGet` → `view_task`), use an `action` enum to dispatch, and **tier the output by action**:

- **list action** — summary view: return only routing/identity fields (id, title, status, assignee) plus dependency edges (`blocked_by`). Omit heavyweight fields like `content` and internal fields like `team_id`. This keeps token cost low for the common "scan all tasks" call.
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

- `MemberOpResult` — `ok`, `reason`; used by `spawn_member`, `shutdown_member`.
- `TaskCreateResult` — `ok`, `reason`, plus `task` which proxies attribute access via `__getattr__` so old `result.task_id` call sites still work.
- `TaskOpResult` — `ok`, `reason`; used by `claim`, `complete`, `reset`, `assign`, `update_task`, `approve_plan`, `add_dependencies`.

Tool `invoke()` must propagate `result.reason` into `ToolOutput.error` on failure — that's the channel the LLM uses to diagnose what went wrong. Returning a generic "Operation failed" here swallows the backend's diagnostic.

## ToolCard ID Convention

All team tool IDs use `team.{name}` format (e.g. `team.send_message`, `team.create_task`). Keep this consistent when adding a new tool — downstream wiring (rails, logging, UI labels) parses the prefix.
