# Segment：原节点摘要

reference 提供任务背景，primary 是本块按顺序覆盖的轨迹节点。概括意图、关键操作、实质结果和局部结束状态，通常几个紧凑段落即可。发现局部实质问题时返回 kind、summary 和证据 node_ids；有依据时补充 state 和恢复证据。

用户拒绝不自动等于用户纠正或工具失败。user_correction 必须有真实外部用户纠正，不能把以 USER 角色呈现的团队指令当作用户纠正。USER 节点也可能是审批恢复、系统上下文或团队事件；同一轮请求的纯文本与包装表示不代表用户发送了两次。同一工具调用的重复表示不是重复执行。

node_ids 和 resolution_node_ids 必须照抄本块 primary 节点的完整 node_id；不能使用短编号、工具调用 ID、span ID 或其他块的证据。不要生成 source_problem_ids，程序会在问题提取后分配来源 ID。

工具大正文可能是预览：previewed_fields 表示原长度，source_truncated_fields 表示上游已丢失内容。不能声称检查了被省略的代码或证明实现正确。
