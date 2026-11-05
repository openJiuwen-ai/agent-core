按一个已安装的专家模板拉起一名队友。新成员带着该专家的人设、技能和工具运行，你不需要自己写角色设定；与 `spawn_teammate` 一样只创建成员记录（状态 UNSTARTED），之后由 `create_task` / `send_message` 等既有触发点拉起。

## 参数

| 参数 | 可见性 | 用法 |
|---|---|---|
| **expert** | 内部 | **必填**。单个专家的**包名**（`view_experts` 的 `name`）；给了 `group` 时是该团内的**成员名** |
| **group** | 内部 | 专家团名。省略表示 `expert` 是单个专家；拉起团内成员时必填（成员名只在团内唯一） |
| **member_name** | 公开 | **必填**。团队内唯一的成员名（如 `finance-1`、`clip-2`），**首字符必须是小写字母，其余仅允许小写字母、数字和连字符**。同一个专家可以按不同名字拉起多份 |
| **display_name** | 公开 | 可选。省略时用专家的显示名；团队里已有同显示名的成员时自动追加「（member_name）」 |
| **desc** | 公开 | 可选。**补充**的公开描述，追加在专家自带的描述之后（不替换）。用来写本实例与同类实例的分工差异，如「负责第 1–10 镜」 |
| **prompt** | **私有** | 可选。**补充**的长期约定，追加在专家人设之后（不替换）。不要复述人设，也不要写具体任务 |
| **model_name** | 内部 | 可选。仅当用户明确指定模型时填写，规则同 `spawn_teammate`；外部运行时专家同 `spawn_teammate` 填写 `cli_agent` 时 |
| **external_cli** | 内部 | 可选对象，**只对外部运行时专家有效**（`runtime` 不是 `native` 的专家）；本地专家传入会报错。字段含义与校验同 `spawn_teammate.external_cli` |
| **isolation** | 内部 | 可选，同 `spawn_teammate`（`"worktree"`）。外部运行时专家不支持 |
| **permissions** | 内部 | 可选，同 `spawn_teammate`（只能收紧）。外部运行时专家不支持 |

## 使用方式

1. 用 `view_experts` / `view_expert_groups` 找到要拉的专家，确认 `spawnable`。
2. 调 `spawn_expert`；同一个专家需要多个实例时，换不同的 `member_name`，并用 `desc` 写清各自负责什么。
3. 用 `create_task` / `send_message` 派活。

- 专家团的 `leader` 不能作为队友拉起。
- 本团队由某个专家团组建时，`group` 写该团即可基于团内成员再拉同类型实例：新成员与团里预定义的同名成员完全同源，不重新读取磁盘上的包。
- 外部运行时专家要求对应的 CLI 类型已在本团队声明（与 `spawn_teammate` 的 `cli_agent` 相同）；模型、认证回退、内置模型规则也与 `spawn_teammate` 填写 `cli_agent` 时相同，其中认证回退模型和内置模型写在 `external_cli` 对象里。
- 拉起时会把专家的能力快照记进成员记录：之后专家包被升级或卸载，已拉起的成员仍按拉起时的版本运行。
