按领域专长创建新的团队成员。用于按领域拆分任务并分配给专业成员执行。

| 参数 | 用法 |
|---|---|
| **member_name** | 唯一标识成员的语义化名（如 `backend-dev-1`），必须确保不与现有成员重复 |
| **display_name** | 成员显示名称，体现角色定位（如「后端开发专家」） |
| **desc** | 长期角色定义：写清专业背景、核心专长、优先认领的任务范围，以及不负责的边界 |
| **prompt** | 启动时的首条指令：说明首次启动后的优先级、约束或协作要求，不重复通用工作流 |

必须先调用 build_team 组建团队，才能调用 spawn_member。调用顺序：build_team → create_task → spawn_member → send_message。spawn_member 只创建成员记录（状态为 UNSTARTED），首次调用 send_message 时系统会自动拉起所有未启动成员。成员完成后调用 shutdown_member 关闭。若 member_name 已存在，创建会失败，请使用不冲突的名称。desc 用于定义成员的长期专业定位；prompt 用于指定成员启动时收到的首条指令。不要把 prompt 写成"开始工作""查看任务列表"这类空泛启动语句，应写明该成员启动后优先关注什么。

## 命名示例

- 好：`backend-dev-1`、`frontend-lead`、`test-engineer`、`db-architect` — 语义化 kebab-case，反映领域
- 差：`xx1`、`mem-a`、`worker`、`a` — 无语义，无法用于任务匹配

## desc / prompt 范例

**desc**（长期角色）— 写清领域、优先级、边界：

    资深后端工程师，专注 Python/FastAPI 微服务和关系型数据库设计。
    优先认领：API 设计、数据库 schema、后端服务实现、鉴权体系。
    不负责：前端 UI 组件、运维部署、移动端开发。

**prompt**（首次启动指令）— 写清初次启动后的关注点，避免空泛启动语：

    首次启动后先 view_task 看一遍任务板，认领 backend 前缀的任务；
    未明确声明的 API 字段命名使用 snake_case，数据库 schema 遵循 3NF。
