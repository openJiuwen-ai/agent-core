# Aggregation and Expert Teams

After claiming a root task and before decomposing it, the Root Leader selects how accepted work becomes the final result.

## HIERARCHICAL

`HIERARCHICAL` is the default. Each parent-task creator reviews direct children and progressively integrates accepted outputs. The Root Team owns the final delivery.

## SUMMARY_TEAM

`SUMMARY_TEAM` uses one lazily created, reusable Summary Team per Organization:

1. Select the mode with `org_update_task(action="set_aggregation_mode")`.
2. Create every source task, including sources owned by the Root Team.
3. Call `org_create_summary_execution` once with the complete `source_task_ids` set.
4. The runtime provisions or reuses the Summary Team and delegates the Summary Task.
5. Once required sources are complete and accepted, the Summary Team is awakened.
6. Its Leader reads the snapshot with `org_summary_get_inputs` and finishes with `org_summary_complete`.

The source set is immutable. A Summary Team cannot create, claim, delegate, or review ordinary organization tasks.

## Expert Teams

An AgentGroup is a template, not a running member. Hosts implement `ExpertGroupCatalog` to discover validated packages and `ExpertTeamLauncher` to combine one package with shared model, storage, transport, and workspace defaults.

The Owner Leader uses `org_list_expert_groups` and `org_create_and_invite_expert_team`. The host must roll back a launched Team if binding fails. Multiple instances may come from the same AgentGroup, so package names are not Team IDs.

agent-core owns the coordination contracts. The host owns package paths, Team construction, model defaults, lifecycle, and UI progress delivery.

