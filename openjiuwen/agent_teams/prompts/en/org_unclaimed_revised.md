Organization {{organization_id}} task {{task_id}} has a revised description, version {{description_revision}}. Its claim deadline is {{deadline_at}} (epoch milliseconds).
Read the latest description with org_view_tasks(action="get", task_id="{{task_id}}"). If still claimable and all required capabilities match, call org_claim_task and follow normal task execution.
Finish this evaluation if the task is already claimed, delegated, expired, or required capabilities do not match.
After evaluating, call org_ack_leader_message(message_id="{{message_id}}").
