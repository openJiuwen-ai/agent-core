# Aggregation and Expert Teams

New root tasks default to `SUMMARY_TEAM`. After claiming a root task and before decomposing it, the Root Leader must still explicitly confirm the mode and may select `HIERARCHICAL` when appropriate.

## HIERARCHICAL

Use `HIERARCHICAL` only when the Root Team performs the substantial core of the task and can independently make the final judgment, while other Teams' children provide only small, local supporting pieces. Each responsible parent Team reviews direct children and progressively integrates accepted outputs; the Root Team delivers the result. Coordination alone is not a reason to choose this mode.

## SUMMARY_TEAM

`SUMMARY_TEAM` is the default preference when multiple Teams own independent parts or specialist domains that require a cross-domain final judgment, such as parallel investment, legal, technical, and market due diligence. It uses one lazily created, reusable Summary Team per Organization:

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
