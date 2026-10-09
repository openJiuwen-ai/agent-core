Organization {{organization_id}} task {{task_id}} expired while unclaimed: {{failure_reason}}. It is FAILED + EXPIRED and must not be reopened.
You are its creator. Use org_view_tasks to inspect it and its parent (parent_task_id={{parent_task_id}}), then improve the description, required capabilities or decomposition.
If still needed and the parent is not terminal, call org_create_task with recreation_request_id="{{message_id}}" and the new title, description, required_capabilities and other task inputs. Omit task_id or use a new one.
The system preserves the parent and derives the original repair target {{repairs_task_id}} for children; a root becomes a new root. Retrying the same recreation_request_id does not create another task.
Do not duplicate an active or accepted repair. If the repair budget is exhausted, follow existing failure handling to fail the parent with the reason. Do not create children for a terminal parent.
After handling, call org_ack_leader_message(message_id="{{message_id}}").
