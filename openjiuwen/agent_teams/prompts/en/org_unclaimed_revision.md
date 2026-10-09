Organization {{organization_id}} task {{task_id}} has not been claimed. You are its creator.
Read the current task with org_view_tasks(action="get", task_id="{{task_id}}").
If it is still OPEN / REVISION_PENDING, clarify inputs, scope, deliverables and acceptance criteria without changing its objective, before {{deadline_at}} (epoch milliseconds).
Call org_update_task(action="revise_description", task_id="{{task_id}}", request_id="{{request_id}}", expected_description_revision={{description_revision}}, description="the complete revised description").
Only one successful revision is allowed. Keep required capabilities unchanged. Stop revising if already claimed, delegated or expired.
After handling, call org_ack_leader_message(message_id="{{message_id}}"). Reading alone does not complete the revision.
