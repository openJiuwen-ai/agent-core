## Summary submission recovery

Summary Task {{summary_task_id}} (execution_id={{execution_id}}) returned without formal submission. This is the single closeout recovery turn.

Do not restart research, create tasks, or repeat drafting. Read the bound accepted sources with `org_summary_get_inputs`, inspect the existing artifacts, and verify the deliverable. Then call `org_summary_complete` with `summary_task_id`, the full report in `output_context.description`, optional `result_uri`, and `output_abstract`.

The current member workspace is `{{workspace_path}}`. Only `read_file`, `org_summary_get_inputs` and `org_summary_complete` are available. Read an already known draft; do not run shell commands, search directories or dispatch internal tasks. `result_uri` is optional: submit compliant full text directly, or explain concrete quality defects and blockers.

Use `org_summary_complete`, not `org_update_task(action='complete')`. Writing a file or reaching Team idle is not submission. If submission is impossible, report the concrete blocker; do not claim completion.
