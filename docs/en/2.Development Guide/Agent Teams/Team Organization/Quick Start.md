# Team Organization Quick Start

This example joins two already active Agent Teams to one Organization. Production applications commonly let Leaders use injected `org_*` tools, while hosts may call the Python lifecycle API directly.

## Prerequisites

1. Build and activate Teams as described in the [AgentTeams guide](../AgentTeams.md).
2. The owner and invited Team use the same `TeamDatabase` instance.
3. Both Teams are active in the same session and `TeamRuntimeManager` pool.

## Create an Organization

```python
runtime = team_runtime_manager.organization_runtime_manager

organization = await runtime.create_organization(
    organization_id="product-release",
    owner_team_id="planning-team",
    session_id="session-001",
    display_name="Product release organization",
)

await runtime.invite_team(
    organization_id=organization.organization_id,
    inviter_team_id="planning-team",
    target_team_id="engineering-team",
    session_id="session-001",
)
```

An `organization_id` must be a safe single path segment. Only the Owner Team can invite members or dissolve the Organization, and one Team cannot belong to two Organizations at the same time.

After binding, the Leaders receive organization control, task, and inbox tools. A useful Leader prompt is:

```text
Create and claim the root task "Prepare the product release". Select HIERARCHICAL aggregation.
Create "Implement the release API" as a child requiring backend capability and delegate it to engineering-team.
Do not complete the parent until every direct child has passed review.
```

Use Leader tools such as `org_create_task`, `org_claim_task`, and `org_update_task` for collaboration so identity, membership, capability, and task-tree rules are validated consistently. A host that must create client-originated roots can use `TeamOrganizationManager.task_pool` in its integration layer; application business logic should not bypass Leader tools routinely.

## Inspect and dissolve

Leaders use `org_view_organization` and `org_view_tasks`. A host can call `runtime.get_organization()` and, when the whole collaboration boundary is no longer needed, `runtime.dissolve_organization()`.

Dissolution unbinds members, stops the Organization Summary Team, and removes persistent Organization records. Do not dissolve an Organization merely because one root task completed; it can be reused for later work.
