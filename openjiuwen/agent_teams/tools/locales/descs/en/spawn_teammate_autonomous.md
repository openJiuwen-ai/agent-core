Create a new LLM teammate with domain expertise. Members are long-lived entities attached to the team — request batches will keep changing, but a member's professional setup and working conventions stay stable and are reused across requests.

| Parameter | Visibility | Usage |
|---|---|---|
| **member_name** | public | Unique semantic slug (e.g. `backend-dev-1`, DNS-label-style kebab-case); **must start with a lowercase letter; the rest may be lowercase letters, digits, or hyphen**; must not collide with any existing member |
| **display_name** | public | Human-readable role label (e.g. "Backend Developer Expert") |
| **desc** | public | Long-term role definition: professional background, core expertise, owned domains, and boundaries. **Do not put current-request work here.** This is injected into every other member's system prompt — never put private or sensitive content here |
| **prompt** | **private** | Long-term working conventions injected only into this member's own system prompt. Hidden goals, internal constraints, or sensitive directives meant only for this member belong here. **Do not put current-request work here.** |
| **model_name** | internal | Optional model suggestion (never enters any LLM context) |

## Information Visibility (read before writing each field)

- **Public fields** (`member_name` / `display_name` / `desc`) are rendered into the Relationships section of every other member's system prompt and returned by `list_members`. Treat them as the **team-wide roster**.
- **Private field** (`prompt`) is injected only into the new member's own system prompt. No other member reads it, and it is not returned by `list_members`.
- When writing `display_name` / `desc`, **never expose private information**, including your internal assessment, trust level, hidden constraints, sensitive goals, internal codenames, or confidential cross-member comparisons.
- Put private guidance and member-only boundaries into `prompt`. Keep `desc` to the role identity every teammate should know, so peers can route work and ask for help.

You must call build_team before calling spawn_teammate. spawn_teammate only creates the member record (status: UNSTARTED); after the member is in place, follow the branch already selected in the system prompt: use `send_message` to start participation on the **Debate branch**, and use `create_task` only on the **Task-collaboration branch**. Members must exist before messages or tasks can land on them. This order holds per batch, not once globally — during task collaboration, a research member may establish background before the remaining members and tasks are created. Startup depends on the team's dispatch mode. Call shutdown_member when the member is done. If member_name already exists, pick a non-conflicting name.

**Both desc and prompt describe long-term properties and must not be bound to a specific request.** desc captures who this role is and what domains it owns; prompt captures stable working conventions and is read only by the member. Do not put a concrete request goal, task ID, task name, or to-do list into either field — request-specific information is delivered through create_task / send_message. Do not write generic startup filler such as "start working" or "check the task list" either.

## Naming Examples

- Good: `backend-dev-1`, `frontend-lead`, `test-engineer`, `db-architect`, `devops-1`, `qa-lead` — semantic kebab-case that reflects domain
- Bad: `xx1`, `mem-a`, `worker`, `a` — no semantics, useless for routing

**Required syntax**: DNS-label style — start with a lowercase ASCII letter (`a-z`); the rest may be lowercase letters, digits (`0-9`), or hyphen (`-`). **Uppercase, underscore (`_`), whitespace, and non-ASCII characters are rejected.** member_name is also a routing key and filesystem path segment. Hyphens match common k8s/docker naming and avoid shell-variable ambiguity.

**Avoiding collisions**:
- Multiple members in one domain: add a numeric suffix — `backend-dev-1`, `backend-dev-2`
- Different roles or seniority: `backend-lead` vs `backend-dev-1`; `frontend-senior` vs `frontend-junior`
- Avoid generic names such as `worker` or `helper`; they give no expertise signal

## desc / prompt Examples

**desc** (long-term role; no current-request content):

    Senior backend engineer focused on Python/FastAPI microservices and
    relational database design. Owns API design, schemas, backend services,
    authentication and permissions; does not own frontend, deployment, or mobile.

**prompt** (cross-request conventions; no current-request content):

    Default API fields to snake_case and relational schemas to 3NF.
    Validate every external interface and use a uniform error response.
    Align cross-domain dependencies with the relevant member before implementation;
    when an approach is uncertain, list options and tradeoffs first.
