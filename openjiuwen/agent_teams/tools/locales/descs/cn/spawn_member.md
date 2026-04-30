按领域专长创建新的团队成员。成员是跟随团队长期存在的实体，团队的任务批次会不断变化，但成员的专业设定与工作约定保持稳定，可以跨任务复用。

| 参数 | 用法 |
|---|---|
| **member_name** | 唯一标识成员的语义化名（如 `backend-dev-1`），必须确保不与现有成员重复 |
| **display_name** | 成员显示名称，体现角色定位（如「后端开发专家」） |
| **desc** | 长期角色定义：写清专业背景、核心专长、负责的领域范围以及不负责的边界。**不要写当前批次的具体任务** |
| **prompt** | 长期工作约定：写清该成员稳定遵循的工作风格、技术偏好或协作约束。**不要写当前批次的任务安排** |

必须先调用 build_team 组建团队，才能调用 spawn_member。调用顺序：build_team → create_task → spawn_member → send_message。spawn_member 只创建成员记录（状态为 UNSTARTED），首次调用 send_message 时系统会自动拉起所有未启动成员。成员完成后调用 shutdown_member 关闭。若 member_name 已存在，创建会失败，请使用不冲突的名称。

**desc 与 prompt 都是长期内容，不绑定到具体任务**。desc 描述这个角色"是谁、能做什么、负责哪类工作"；prompt 描述这个角色"工作时长期遵循什么约定"（如代码风格、命名规范、协作习惯）。任何具体任务的目标、ID、名称、清单都不要写入这两个字段——这些信息通过 create_task / send_message 在每次任务时下发。同时也不要把 prompt 写成"开始工作""查看任务列表"这类空泛启动语句。

## 命名示例

- 好：`backend-dev-1`、`frontend-lead`、`test-engineer`、`db-architect`、`devops-1`、`qa-lead` — 语义化 kebab-case，反映领域
- 差：`xx1`、`mem-a`、`worker`、`a` — 无语义，无法用于任务匹配

**推荐语法**：小写字母、数字、连字符（`-`）组成 kebab-case；首字符必须是字母；长度 3–32 字符。member_name 会作为消息路由和文件路径的一部分，避免空格、下划线首字符、大写或其他特殊字符。

**防冲突建议**：
- 同领域多成员：加数字后缀 — `backend-dev-1`、`backend-dev-2`
- 同领域不同角色/资历：用角色词区分 — `backend-lead` vs `backend-dev-1`；`frontend-senior` vs `frontend-junior`
- 跨领域避免通用词（`worker`、`helper`）— 它们无法从 name 反推专长，任务路由完全依赖 desc

## desc / prompt 范例

**desc**（长期角色）— 写清领域、专长、边界，不涉及当前任务：

    资深后端工程师，专注 Python/FastAPI 微服务和关系型数据库设计。
    专长领域：API 设计、数据库 schema、后端服务实现、鉴权与权限体系。
    不负责：前端 UI 组件、运维部署、移动端开发。

**prompt**（长期工作约定）— 写跨任务复用的工作偏好与协作约束，不涉及当前任务：

    API 字段命名默认使用 snake_case；数据库 schema 默认遵循 3NF。
    所有对外接口必须包含输入校验与统一的错误响应。
    跨领域依赖（前端契约、部署细节）先与对应成员对齐再实现，
    遇到方案不确定时先列出选项与权衡再动手。
