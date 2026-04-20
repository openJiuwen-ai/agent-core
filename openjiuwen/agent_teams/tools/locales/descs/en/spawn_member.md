Create a new team member with domain expertise. Used to split tasks by domain and assign them to specialized members for execution.

| Parameter | Usage |
|---|---|
| **member_name** | Unique member name (semantic slug). It must not conflict with any existing member (e.g., `backend-dev-1`) |
| **display_name** | Member display name that reflects the role (e.g., "Backend Developer Expert") |
| **desc** | Long-term role definition: describe professional background, core expertise, preferred task scope, and boundaries the member should not own |
| **prompt** | First startup instruction: define initial priorities, constraints, or coordination needs without repeating the generic workflow |

You must call build_team before calling spawn_member. Call order: build_team → create_task → spawn_member → send_message. spawn_member only creates the member record (status: UNSTARTED); on the first send_message call, the system automatically starts all unstarted members. Call shutdown_member after the member completes work. If member_name already exists, creation will fail — use a non-conflicting name. Use desc to define the member's long-term professional role; use prompt to specify the first instruction the member receives at startup. Do not write prompt as generic startup filler such as "start working" or "check the task list"; specify what this member should prioritize when it starts.

## Naming Examples

- Good: `backend-dev-1`, `frontend-lead`, `test-engineer`, `db-architect`, `devops-1`, `qa-lead` — semantic kebab-case, reflects domain
- Bad: `xx1`, `mem-a`, `worker`, `a` — no semantics, can't be used for task routing

**Recommended syntax**: lowercase letters, digits, and hyphens (`-`) in kebab-case; first character must be a letter; length 3–32. Since `member_name` feeds into message routing and file paths, avoid spaces, leading underscores, uppercase letters, and other special characters.

**Avoiding collisions**:
- Multiple members in one domain: add a numeric suffix — `backend-dev-1`, `backend-dev-2`
- Different roles/seniority within a domain: use a role token — `backend-lead` vs `backend-dev-1`; `frontend-senior` vs `frontend-junior`
- Across domains, avoid generic words (`worker`, `helper`) — they give no hint of expertise, so task routing has to rely entirely on `desc`

## desc / prompt Examples

**desc** (long-term role) — specify domain, priorities, and boundaries:

    Senior backend engineer, focused on Python/FastAPI microservices and
    relational database design.
    Preferred tasks: API design, database schema, backend service
    implementation, auth system.
    Not responsible for: frontend UI components, ops/deployment, mobile.

**prompt** (first-startup instruction) — specify what to focus on right
after startup, avoiding generic filler:

    After startup, call view_task first to survey the board. Claim tasks
    prefixed with "backend-". Use snake_case for API field names when
    unspecified; follow 3NF for database schemas.
