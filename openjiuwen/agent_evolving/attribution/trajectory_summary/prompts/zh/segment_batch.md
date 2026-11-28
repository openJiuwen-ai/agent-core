# 短 Turn 批量请求：逐 Segment 独立返回

本次请求包含同一主体相邻短 Turn 的多个 Segment。它们只是共用一次请求，不是同一个执行范围，也不是一次整体归并。

批量输出规则覆盖通用规则中顶层 narrative / problems 的提交形式：只调用一次 report_summary，顶层返回 results 数组，每个输入 Segment 必须且只返回一项，包含照抄的 segment_id、非空 narrative 和 problems（无实质问题时为 []）。不要在顶层返回 narrative 或 display_summary。可按输入顺序输出，不能漏项、重复项、合并不同 Segment 的结果或编造 segment_id。

reference 是共享任务背景；segments 中每一项的 primary 才是该 Segment 自己的节点。分别应用原节点摘要规则：问题证据及恢复节点仅能引用所属 Segment 的 primary；状态以该 Segment 结束时为准，不能利用同批后续 Turn 的信息将前一个 Segment 提前标为已恢复或交付成功。后续归并阶段才处理跨块的恢复与关联。

每项只保留关键操作、实质结果、验证缺口和局部结束状态，不复述同批其他项，不为批量请求额外撰写整体总结。
