查看本机**已安装**的单个专家（`agent_template` 包）。每个专家是一套现成的人设 + 技能 / 工具，可以用 `spawn_expert` 直接拉成队友，不用你自己写角色设定。

## 何时使用

- 用户点名要某类专家（「拉一个理财专家进来」）：先 `action="list"` 加 `query` 找到包名，再 `spawn_expert`。
- 拿不准某个专家擅长什么：`action="get"` 看完整描述、分类、标签、技能和运行时。

## 参数

| 参数 | 用法 |
|---|---|
| **action** | `list`（默认）列出全部；`get` 查看一个 |
| **query** | 仅 `list`：按相关度检索包名、显示名、标签、分类与描述，最相关的排在前面；包名与 `query` 完全一致（不区分大小写）的排第一。中文可直接用词语，不需要和原文完全一致 |
| **group** | 可选：在这个专家团内检索 / 查看——`list` 列出团内可拉起的成员（`leader` 除外），`get` 的 `name` 是成员名；填 `"*"` 只看不属于任何专家团的单个专家 |
| **name** | `get` 必填：专家的**包名**（list 结果里的 `name`，不是显示名）；在专家团内查看时是成员名 |

{{view_experts_bound_scope}}

## 输出

- `list`：每个专家一行，带 `query` 时按相关度排序——`name` / `display_name` / `category` / `runtime`（`native` 本地运行，或外部运行时 provider 名如 `claudecode`）/ 一句话描述。
- `get`：包名、显示名、完整描述、分类、标签、版本、运行时、技能名单、安装来源。不返回人设全文与工具清单。

只列本机已安装的专家；未安装的无法拉起。在专家团内列出的成员每行另带 `spawnable` / `predefined`，拉起时用 `spawn_expert(expert=<成员名>, group=<团名>)`。专家团本身（团信息、能力标签）用 `view_expert_groups` 查看。
