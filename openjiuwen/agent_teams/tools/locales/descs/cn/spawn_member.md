按领域专长创建新的团队成员。成员是跟随团队长期存在的实体，团队的任务批次会不断变化，但成员的专业设定与工作约定保持稳定，可以跨任务复用。

| 参数 | 可见性 | 用法 |
|---|---|---|
| **member_name** | 公开 | 唯一标识成员的语义化名（如 `backend-dev-1`，DNS label 风格 kebab-case），**首字符必须是小写字母，其余仅允许小写字母、数字和连字符**，必须确保不与现有成员重复 |
| **display_name** | 公开 | 成员显示名称，体现角色定位（如「后端开发专家」） |
| **desc** | 公开 | 长期角色定义：写清专业背景、核心专长、负责的领域范围以及不负责的边界。**不要写当前批次的具体任务**。该字段会被注入到所有其他成员的 system prompt 中，禁止写入私密或敏感信息 |
| **role_type** | 内部 | 可选；决定 framework 装配方式（不进入任何成员的 prompt 文本）。`teammate`（默认）= 普通 LLM 队友；`human_agent` = 人类成员，由真人通过 HumanAgentInbox 驱动 |
| **prompt** | **私有** | 长期工作约定，仅注入该成员自己的 system prompt：写清该成员稳定遵循的工作风格、技术偏好或协作约束。任何只该让本成员知道的隐藏目标 / 内部约束 / 敏感细节都放这里。**不要写当前批次的任务安排**。`role_type=human_agent` 时禁止传入 |
| **model_name** | 内部 | 可选；建议使用的模型名称（不进入任何 LLM 上下文）。`role_type=human_agent` 时禁止传入 |

## 信息可见性边界（创建成员时务必区分）

- **公开字段**（`member_name` / `display_name` / `desc`）：会注入到团队中**每个其他成员**的 system prompt 的「成员关系」一节，并通过 `list_members` 工具返回给所有可调用该工具的成员。这些是**全员可见的花名册信息**。
- **私有字段**（`prompt`）：仅注入到**该成员自己**的 system prompt，**不**会被任何其他成员读到，也**不**会出现在 `list_members` 返回中。
- 你（leader）在写 `display_name` / `desc` 时**禁止暴露任何私有信息**，包括但不限于：
  - 你对该成员的内部考量、能力评价、信任级别
  - 仅希望该成员遵守的隐藏约束、敏感目标、内部代号
  - 跨团队 / 跨成员的机密策略与对照关系
- 把"对该成员的私下叮嘱""仅他需要知道的边界与约束"统一放进 `prompt`。`desc` 只描述**让全员都该知道的角色身份**，让其他成员可以据此向他派任务 / 请协助。

## role_type 用法

- **`teammate`（默认）**：常规 LLM 成员，必须给出 `desc` 与 `prompt`，可选 `model_name`。框架按 model 配置启动 DeepAgent。
- **`human_agent`**：人类成员，由真人通过 HumanAgentInbox 驱动，**不接受** `model_name` 与 `prompt`（由框架内置模板托管），传入这两个字段会立刻报错。需要 `TeamAgentSpec.enable_hitt=True` 且当前 `build_team` 实例未禁用 HITT，否则被拒绝。`desc` / `display_name` 用作展示与持久化人设。

必须先调用 build_team 组建团队，才能调用 spawn_member。调用顺序：build_team → create_task → spawn_member → send_message。spawn_member 只创建成员记录（状态为 UNSTARTED），首次调用 send_message 时系统会自动拉起所有未启动成员。成员完成后调用 shutdown_member 关闭。若 member_name 已存在，创建会失败，请使用不冲突的名称。

**desc 与 prompt 都是长期内容，不绑定到具体任务**。desc 描述这个角色"是谁、能做什么、负责哪类工作"，会被全员读到；prompt 描述这个角色"工作时长期遵循什么约定"（如代码风格、命名规范、协作习惯），仅本人可见。任何具体任务的目标、ID、名称、清单都不要写入这两个字段——这些信息通过 create_task / send_message 在每次任务时下发。同时也不要把 prompt 写成"开始工作""查看任务列表"这类空泛启动语句。

## 命名示例

- 好：`backend-dev-1`、`frontend-lead`、`test-engineer`、`db-architect`、`devops-1`、`qa-lead` — 语义化 kebab-case，反映领域
- 差：`xx1`、`mem-a`、`worker`、`a` — 无语义，无法用于任务匹配

**强制语法**：DNS label 风格 — 首字符必须是小写英文字母（`a-z`），其后仅允许小写字母、数字（`0-9`）和连字符（`-`）。**禁止大写字母、下划线（`_`）、空白以及中文等任何非 ASCII 字符** —— 违反者 tool 会直接拒绝。member_name 同时作为消息路由键和文件路径片段，CJK / 大写 / 下划线会破坏路由并产生不可读的目录布局。选用连字符与 k8s pod / docker container 等业界资源命名约定保持一致，shell 中也不会与环境变量 `$xxx` 混淆。

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
