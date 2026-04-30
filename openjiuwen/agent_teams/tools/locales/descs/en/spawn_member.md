Create a new team member with domain expertise. Members are long-lived entities attached to the team — task batches will keep changing, but a member's professional setup and working conventions stay stable and are reused across tasks.

| Parameter | Usage |
|---|---|
| **member_name** | Unique semantic slug (e.g. `backend-dev-1`); must not collide with any existing member |
| **display_name** | Human-readable role label (e.g. "Backend Developer Expert") |
| **desc** | Long-term role definition: professional background, core expertise, the domains this member owns, and the boundaries it does not own. **Do not put current-batch tasks here** |
| **prompt** | Long-term working conventions: stable working style, technical preferences, or collaboration constraints this member follows. **Do not put current-batch task assignments here** |

You must call build_team before calling spawn_member. Call order: build_team → create_task → spawn_member → send_message. spawn_member only creates the member record (status: UNSTARTED); on the first send_message call the system automatically starts every unstarted member. Call shutdown_member when the member is done. If member_name already exists, creation fails — pick a non-conflicting name.

**Both desc and prompt describe long-term properties and must not be bound to specific tasks.** desc captures "who this role is, what it can do, which areas it owns"; prompt captures "what working conventions this role always follows" (code style, naming, collaboration habits, etc.). Do not put any concrete task goal, task ID, task name, or to-do list into either field — that information is delivered per-task via create_task / send_message. Equally, do not write prompt as generic startup filler such as "start working" or "check the task list".

## Naming Examples

- Good: `backend-dev-1`, `frontend-lead`, `test-engineer`, `db-architect`, `devops-1`, `qa-lead` — semantic kebab-case, reflects domain
- Bad: `xx1`, `mem-a`, `worker`, `a` — no semantics, useless for task routing

**Recommended syntax**: lowercase letters, digits, and hyphens (`-`) in kebab-case; first character must be a letter; length 3–32. Since `member_name` feeds message routing and file paths, avoid spaces, leading underscores, uppercase letters, and other special characters.

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
