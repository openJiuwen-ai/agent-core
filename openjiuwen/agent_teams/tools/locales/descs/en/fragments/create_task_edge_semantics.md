## Two Usage Scenarios

1. **Batch-create a task graph/subgraph** (initial DAG at project start, or new tasks discovered during work): pass the whole batch in one call and express edges among them with **depends_on only** — depends_on may reference tasks in the same batch (order-independent, forward references allowed) or tasks already on the board. Do not point depended_by at tasks of the same batch; that is a redundant representation of the same edge and is rejected.
2. **Insert into an existing chain** (a missing prerequisite surfaces): the new task lists the **existing tasks that must wait for it via depended_by**; those tasks automatically become blocked until the new task completes. depended_by may only reference tasks already on the board.

The whole call is **atomic**: either every task is created or none is, with the concrete failure reason (cycle, id collision, missing referenced task, ...).
