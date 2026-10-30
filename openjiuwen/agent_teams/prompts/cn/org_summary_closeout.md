## 汇总提交恢复

汇总任务 {{summary_task_id}}（execution_id={{execution_id}}）已返回，但尚未正式提交。这是唯一一次收尾补偿轮。

不要重新研究、创建任务或重复起草。使用 `org_summary_get_inputs` 读取绑定的已验收来源，检查已有文件并校验交付物。随后调用 `org_summary_complete`，传入 `summary_task_id`，将完整正文放入 `output_context.description`，可选提供 `result_uri`，并填写 `output_abstract`。

当前成员工作区的真实路径：`{{workspace_path}}`。只能使用 `read_file` 读取已知的草稿文件，以及上述两个汇总工具。禁止执行 shell、搜索目录或下发新的内部任务。无需为了可选的 `result_uri` 再写文件；正文符合要求即可正式提交。不满足要求时说明缺陷和阻塞原因。

使用 `org_summary_complete`，不要使用 `org_update_task(action='complete')`。写出文件或 Team 进入 idle 都不代表正式提交。如果无法提交，报告具体阻塞原因，不要宣称完成。
