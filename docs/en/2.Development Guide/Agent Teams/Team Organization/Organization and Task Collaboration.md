# Organization and Task Collaboration

The Organization Task Pool stores cross-Team work as a task tree. Formal assignments belong in the task pool; Leader messages are for negotiation, dependencies, and blockers.

## Roles

- **Owner Team** creates the Organization, invites members, and dissolves it.
- **Root Leader** is the Leader of the Team that claimed the current root task and selects its aggregation mode.
- **Member Team** claims matching open work or executes delegated work.
- **Parent-task creator Team** reviews its direct children and chooses repair actions.

Owner, root-task creator, and Root Leader may be different Teams.

## Tool catalog

Team Organization defines 24 Leader tools. The complete catalog belongs in the guide so developers can understand the capability and prompting boundary; detailed fields and return schemas remain code/API-reference material.

### Organization control

| Tool | Purpose | Main constraint |
|------|---------|-----------------|
| `org_create_organization` | Create an Organization owned by the active Team | Safe ID; a Team cannot join two Organizations |
| `org_invite_team` | Invite an active same-session Team | The target shares the owner's `TeamDatabase` |
| `org_dissolve_organization` | Unbind members and delete Organization data | Owner only; not a root-task completion action |
| `org_list_available_teams` | List active Teams that can be invited | Active Teams only |
| `org_list_configured_teams` | List dormant host-configured Teams | Requires host activation support |
| `org_activate_and_invite_team` | Activate a configured Team and invite it | For configured Teams, not AgentGroup templates |
| `org_list_expert_groups` | Discover host-validated AgentGroups | Metadata only; does not launch a Team |
| `org_create_and_invite_expert_team` | Launch an expert Team and add it | Owner only; launch must roll back on bind failure |
| `org_view_organization` | View the Organization and registered Leaders | Caller must have access to the Organization |

### Member Leader collaboration

| Tool | Purpose | Main constraint |
|------|---------|-----------------|
| `org_view_tasks` | View tasks and relevant notifications | Read-only |
| `org_create_task` | Create roots, children, repairs, or delegated work | Non-empty capabilities; one active ordinary root |
| `org_claim_task` | Atomically claim open work | `OPEN` tasks only |
| `org_delegate_task` | Delegate assigned non-root work | Target is an Organization Team ID, not a Teammate |
| `org_update_task` | Start, complete, fail, revise, or select root aggregation | Explicit failure reason; Root Leader controls aggregation |
| `org_view_child_tasks` | View direct children and latest reviews | Used for parent completion gates |
| `org_view_pending_reviews` | List children waiting for this Team's review | Authorized reviews only |
| `org_review_task` | Accept, reject, or request revision | Reviewer is the parent-task creator Team |
| `org_send_leader_message` | Persist a direct or broadcast Leader message | Use tasks for formal assignment |
| `org_get_leader_message` | Read one inbox message | Does not acknowledge it |
| `org_list_leader_messages` | List this Team's Leader inbox | Does not acknowledge messages |
| `org_ack_leader_message` | Confirm handling after the required action | Acknowledge only after success |
| `org_create_summary_execution` | Freeze sources and create one summary execution | Root Leader only; `SUMMARY_TEAM` roots only |

### Summary Leader

| Tool | Purpose | Main constraint |
|------|---------|-----------------|
| `org_summary_get_inputs` | Read the accepted, bound source snapshot | Sources are read-only |
| `org_summary_complete` | Submit the final result and complete summary work | Artifact URI, when present, is under `summary/` |

## Task states

| State | Meaning |
|-------|---------|
| `OPEN` | Available for a capable member Team |
| `CLAIMED` | Claimed by a Team |
| `DELEGATED` | Assigned to a specific member Team |
| `IN_PROGRESS` | Execution has started |
| `WAITING_SOURCES` | A Summary Task is waiting for its fixed sources |
| `COMPLETED` | Successfully completed |
| `FAILED` | Ended with an explicit reason |

`CANCELLED` and `EXPIRED` are failure codes on `FAILED`, not separate states. Other codes include `EXECUTION_FAILED`, `SOURCE_FAILED`, and `SUMMARY_PROVISION_FAILED`.

Leaders use `org_view_tasks`, `org_create_task`, `org_claim_task`, `org_delegate_task`, and `org_update_task`. Organization `team_id` values identify member Teams, never Teammates inside a Team.

## Review and repair

Completing a child creates a review for its parent-task creator. A review is `ACCEPTED`, `REJECTED`, or `NEEDS_REVISION`. A parent cannot complete until its direct children satisfy the completion gate.

Create an explicit repair with `org_create_task(repairs_task_id=...)` after a failure or rejected result. An accepted repair can replace the failed original for the parent gate. Optional `retry_count` and `retry_limit` metadata enforce a repair budget.

## Unclaimed tasks

New tasks snapshot the Organization's unclaimed-task policy. They progress through `INITIAL_WAIT`, `REVISION_PENDING`, `POST_REVISION_WAIT`, and `CLOSED`. The creator revises a requested description with `org_update_task(action="revise_description")`; if no Team claims the revised work, the task ends as `FAILED/EXPIRED`.
