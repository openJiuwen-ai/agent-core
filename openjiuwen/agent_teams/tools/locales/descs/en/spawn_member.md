Create a new team member with domain expertise. Members are long-lived entities attached to the team — task batches will keep changing, but a member's professional setup and working conventions stay stable and are reused across tasks.

| Parameter | Visibility | Usage |
|---|---|---|
| **member_name** | public | Unique semantic slug (e.g. `backend-dev-1`, DNS-label-style kebab-case); **must start with a lowercase letter; the rest may be lowercase letters, digits, or hyphen**; must not collide with any existing member |
| **display_name** | public | Human-readable role label (e.g. "Backend Developer Expert") |
| **desc** | public | Long-term role definition: professional background, core expertise, the domains this member owns, and the boundaries it does not own. **Do not put current-batch tasks here.** This field is injected into every other member's system prompt — never put private or sensitive content here |
| **role_type** | internal | Optional; drives framework wiring, never rendered into any member's prompt text. `teammate` (default) = regular LLM teammate; `human_agent` = human collaborator driven via HumanAgentInbox |
| **prompt** | **private** | Long-term working conventions, injected only into this member's own system prompt: stable working style, technical preferences, collaboration constraints. Hidden goals, internal constraints, or sensitive directives meant only for this member belong here. **Do not put current-batch tasks here.** Forbidden when `role_type='human_agent'` |
| **model_name** | internal | Optional model suggestion (never enters any LLM context). Forbidden when `role_type='human_agent'` |

## Information Visibility (read before writing each field)

- **Public fields** (`member_name` / `display_name` / `desc`) are rendered into the *Relationships* section of every other member's system prompt, and returned by the `list_members` tool to all members allowed to call it. Treat them as the **team-wide roster** — anything you wouldn't say in front of the whole team must not go here.
- **Private field** (`prompt`) is injected only into the new member's own system prompt. No other member ever reads it, and it is **not** returned by `list_members`.
- When writing `display_name` / `desc`, **never expose private information**, including but not limited to:
  - Your internal assessment, trust level, or capability rating for this member
  - Hidden constraints, sensitive goals, or internal codenames you only want this member to follow
  - Cross-team / cross-member confidential strategy or comparisons
- Put "private guidance for this member only" and "boundaries only they need to know" into `prompt`. Keep `desc` to the **role identity every teammate must know**, so peers can route tasks and ask for help against it.

## role_type usage

- **`teammate` (default)**: regular LLM member; supply `desc` and `prompt`, optionally `model_name`. The framework starts a DeepAgent according to the model config.
- **`human_agent`**: human member driven by the real user via HumanAgentInbox. **Rejects** `model_name` and `prompt` (managed by the framework template) — passing them raises an error immediately. Requires `TeamAgentSpec.enable_hitt=True` and the current `build_team` instance to leave HITT engaged. `desc` / `display_name` are still honoured for presentation and persisted persona.
- **`external_cli`**: third-party CLI agent member (claudecode / codex / ...) whose brain is the CLI subprocess rather than a local LLM. Requires `cli_agent` (the CLI kind, e.g. `claude` / `codex`) and `desc` (the member persona); **rejects** `model_name` / `prompt`. `cli_agent` must match a static config pre-declared in `TeamAgentSpec.external_cli_agents` (launch command, working directory and team MCP tool injection all live there). The framework launches the CLI subprocess from that config and auto-injects the team collaboration tools (read_inbox / claim_task / send_message / ...).

You must call build_team before calling spawn_member. Call order: build_team → create_task → spawn_member → send_message. spawn_member only creates the member record (status: UNSTARTED); on the first send_message call the system automatically starts every unstarted member. Call shutdown_member when the member is done. If member_name already exists, creation fails — pick a non-conflicting name.

**Both desc and prompt describe long-term properties and must not be bound to specific tasks.** desc captures "who this role is, what it can do, which areas it owns" and is read by every teammate; prompt captures "what working conventions this role always follows" (code style, naming, collaboration habits, etc.) and is read only by the member themselves. Do not put any concrete task goal, task ID, task name, or to-do list into either field — that information is delivered per-task via create_task / send_message. Equally, do not write prompt as generic startup filler such as "start working" or "check the task list".

## Naming Examples

- Good: `backend-dev-1`, `frontend-lead`, `test-engineer`, `db-architect`, `devops-1`, `qa-lead` — semantic kebab-case, reflects domain
- Bad: `xx1`, `mem-a`, `worker`, `a` — no semantics, useless for task routing

**Required syntax**: DNS-label style — must start with a lowercase ASCII letter (`a-z`); the rest may be lowercase letters, digits (`0-9`), or hyphen (`-`). **Uppercase, underscore (`_`), whitespace, and any non-ASCII characters (CJK, etc.) are rejected** — the tool fails fast on any violation. `member_name` doubles as a message-routing key and a filesystem path segment, so non-ASCII / uppercase / underscore would break routing and produce unreadable directory layouts. The hyphen choice matches the convention used by k8s pods and docker containers, and avoids being mistaken for a shell variable (`$foo_bar`).

**Avoiding collisions**:
- Multiple members in one domain: add a numeric suffix — `backend-dev-1`, `backend-dev-2`
- Different roles/seniority within a domain: use a role token — `backend-lead` vs `backend-dev-1`; `frontend-senior` vs `frontend-junior`
- Across domains, avoid generic words (`worker`, `helper`) — they give no hint of expertise, so task routing has to rely entirely on `desc`

## desc / prompt Examples

**desc** (long-term role) — domain, expertise, and boundaries; no current-task content:

    Senior backend engineer, focused on Python/FastAPI microservices and
    relational database design.
    Expertise: API design, database schema, backend service implementation,
    auth and permission systems.
    Not responsible for: frontend UI components, ops/deployment, mobile.

**prompt** (long-term working conventions) — cross-task working preferences and
collaboration constraints; no current-task content:

    Default to snake_case for API field naming; default to 3NF for database
    schemas. Every external interface must have input validation and a
    unified error response.
    For cross-domain dependencies (frontend contract, deployment details),
    align with the corresponding member before implementing. When the
    approach is uncertain, list options and trade-offs before coding.
