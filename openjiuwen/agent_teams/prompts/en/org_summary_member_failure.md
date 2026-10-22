## Summary member execution failed

Summary Task: {{summary_task_id}}; execution: {{execution_id}}.
Member: {{member_name}}; failure reason: {{reason}}.
Recovery budget exhausted: {{blocked}}.

Do not keep waiting for the failed member. Read existing internal task descriptions and their explicit
draft paths, and verify only against bound sources. If the draft is deliverable, complete or cancel
remaining internal work truthfully (never label failure as success), then immediately call
org_summary_complete with the full report and abstract. Otherwise request at most one focused revision,
requiring the report, abstract, artifact path and internal task completion.
When the recovery budget is exhausted, do not dispatch, poll or automatically retry; report the blocker
without claiming successful summary completion.
