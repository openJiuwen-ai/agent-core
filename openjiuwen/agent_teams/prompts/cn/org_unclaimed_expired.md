组织 {{organization_id}} 的任务 {{task_id}} 因无人认领而过期：{{failure_reason}}。旧任务已为 FAILED + EXPIRED，不能复用。
你是创建者。调用 org_view_tasks 读取原任务及父任务（parent_task_id={{parent_task_id}}），调整描述、能力要求或拆分方式。
若仍需完成且父任务未终止，调用 org_create_task，传入 recreation_request_id="{{message_id}}" 以及新任务的 title、description、required_capabilities 等参数。使用新的 task_id 或省略它。
系统自动保留父任务并为子任务关联原始修复目标 {{repairs_task_id}}；根任务将创建为新的根任务。同一 recreation_request_id 的重试不会重复创建。
若已有有效或通过验收的修复任务，不要重复创建。若修复预算耗尽，按现有失败处理规则终止父任务并说明原因；父任务已经终止时不再创建子任务。
处理完成后调用 org_ack_leader_message(message_id="{{message_id}}")。
