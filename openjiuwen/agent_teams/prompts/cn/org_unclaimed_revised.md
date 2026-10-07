组织 {{organization_id}} 的任务 {{task_id}} 已补充描述，当前版本为 {{description_revision}}，认领截止时间为 {{deadline_at}}（epoch 毫秒）。
调用 org_view_tasks(action="get", task_id="{{task_id}}") 读取最新描述。如果任务仍可认领且所需能力均匹配，调用 org_claim_task 认领，然后按正常任务流程执行。
如果任务已被认领、委派、过期或所需能力不匹配，结束此次评估。
评估处理完成后调用 org_ack_leader_message(message_id="{{message_id}}")。
