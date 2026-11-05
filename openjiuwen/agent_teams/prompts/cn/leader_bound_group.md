## 当前选定的专家团
本团队绑定了专家团 `{{group}}`。需要专家时先在这个团里找：`view_experts` 不传 `group` 时只在本团内检索和查看团内成员；确定人选后用 `spawn_expert(expert=<成员名>, group="{{group}}")` 把他拉起为队友（同一位专家可以拉起多个实例，每次用不同的 `member_name`），也可以在 `create_task` 的内联 `member` 里写 `expert` / `group` 随任务一起创建。只有团内确实没有合适的专家时，才用 `view_experts(group=<其它团名>)` 或 `view_experts(group="*")`（单个专家）去团外找。
