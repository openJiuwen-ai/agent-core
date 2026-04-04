# Agent Team Tools

## Tool Design Principles

Reference: Claude Code `TeamCreateTool` / `SendMessageTool` prompt design docs in this directory.

### Description = Behavior Contract

Tool description is not just a feature summary — it is a **behavior contract** for the LLM.
Every description should include:

1. **Use-case enumeration** — when to call this tool (e.g. "for task notifications, progress reports, escalation of blockers")
2. **Behavioral constraints** — what NOT to do (e.g. "your plain text output is NOT visible to other agents", "refer to members by name, never by internal ID")
3. **Cost signals** — mark expensive operations explicitly (e.g. "broadcast — expensive, linear in team size")

### Entry-Point Tools Carry the Workflow

For the "entry-point" tool of a subsystem (e.g. `build_team` for team collaboration),
the description should contain the **full workflow specification** — call order, design
principles, state machine, notification semantics, idle handling — not just a feature
summary. Rationale (validated by Claude Code's `TeamCreateTool`):

- The LLM sees the workflow in context when it's about to call the tool, not buried
  in a system prompt it may have forgotten.
- Avoids duplicating workflow steps across system prompt and tool description.
- The system prompt (`leader_policy.md`) stays focused on **role identity and decision
  principles**; the tool description owns **operational procedure**.

Concrete sections for an entry-point tool description:

| Section | Purpose |
|---|---|
| Call order | Prevent wrong sequencing (e.g. "build_team before task_manager before spawn_member") |
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

If two parameters are hard for the LLM to distinguish semantically, merge them.
Example: `desc` ("what the team does") and `prompt` ("directives for members") were
merged into a single `desc` field — the LLM consistently confused the boundary, and
a single field performs equally well. The DB column for the removed parameter is kept
nullable for backward compatibility.

### One Tool, One Concern — But Don't Split What Differs Only by a Parameter

If two tools share the same schema structure and only differ by one parameter value
(e.g. `send_message` vs `broadcast_message` differ only by `to: name` vs `to: "*"`),
merge them into one tool. Use the parameter to dispatch, not a separate tool registration.

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

Always return meaningful `data` on success, not bare `ToolOutput(success=True)`.
Include `type` to distinguish dispatch paths (e.g. `"message"` vs `"broadcast"`),
and echo key routing info (`from`, `to`, `summary`) for logging and downstream processing.

### Exception Handling

Wrap `invoke` body in `try/except` to catch unexpected errors from backend services.
Log the exception via `team_logger.error`, return `ToolOutput(success=False, error=...)`.
Never let an unhandled exception propagate — tool invocations must always return a `ToolOutput`.

## i18n

Locale files live in `locales/` — flat `STRINGS` dict per language (`cn.py`, `en.py`).

- Key convention: `tool_name._desc` for ToolCard description, `tool_name.param` for params, `tool_name.nested.param` for nested schema params.
- Multi-line descriptions use `"""\` (triple-quote with backslash continuation), not parenthesized string concatenation.
- `make_translator(lang)` returns a closure — no global state, safe for concurrent multi-language use in one process.
- All tool constructors take `t: Translator` as a parameter.
- Missing keys raise `KeyError` at construction time — fail loud, not silent.

## Prompt Layering: Tool Description vs System Prompt

| Layer | Owns | Example file |
|---|---|---|
| Tool description (`locales/`) | Operational procedure, call order, workflow steps, anti-patterns, usage scenarios | `build_team._desc` |
| System prompt (`prompts/`) | Role identity, decision principles, state transitions | `leader_policy.md` |

Rule: **don't duplicate content across layers**. If the workflow lives in the tool
description, the system prompt should not repeat it.

## ToolCard ID Convention

All team tool IDs use `team.{name}` format (e.g. `team.send_message`, `team.task_manager`).
