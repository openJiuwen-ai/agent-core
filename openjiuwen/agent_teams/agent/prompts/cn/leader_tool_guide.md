
## 可用工具

### 团队管理
- `build_team(team_name, team_desc, leader_name, leader_desc)`: 组建团队并设置协作目标，同时注册 Leader 为团队成员。**场景**: 接收到任务目标后第一步调用
- `clean_team()`: 解散团队，清理所有资源。仅在所有成员已关闭后可调用。**场景**: 临时团队完成全部目标后解散

### 成员管理
- `spawn_member(member_id, name, desc, prompt)`: 创建新的团队成员。desc 设定人设（专业背景、领域专长、行为风格），prompt 为启动指令（引导成员读取任务列表和消息，不要直接分配任务）。**场景**: 项目初始按领域创建成员，或执行中补充新领域专家
- `shutdown_member(member_id, force?)`: 关闭团队成员。**场景**: 成员完成所有任务后释放资源，或持续无法交付时强制关闭
- `approve_plan(member_id, approved, feedback?)`: 审批成员提交的计划。**场景**: 成员提交执行计划后审核

### 任务管理 (`task_manager`)
统一的任务管理工具，通过 `action` 参数区分操作。**任务应聚焦于可交付成果和验收标准，不要规划具体执行步骤**：
- **添加任务** (`action="add"`): 通过 `tasks` 数组传入（单个任务也用数组包裹）。每个 task 含 title、content（写目标和验收标准），可选 task_id、depends_on（前置依赖）。**场景**: 项目初始批量创建任务 DAG，或执行中补充新任务
- **插入任务** (`action="insert"`): 将任务插入已有 DAG 中间。传 title、content，可选 task_id、depends_on、depended_by（反向依赖，让现有任务等待本任务完成）。**场景**: 发现遗漏的前置任务需要插入已有依赖链中
- **更新任务** (`action="update"`): 传 task_id、title/content。若任务已被认领，系统会自动取消该成员执行并重置任务状态。**场景**: 根据成员反馈调整任务范围或补充细节
- **取消任务** (`action="cancel"`): 传 task_id。若任务已被认领，系统会自动取消该成员执行。**场景**: 需求变更导致任务不再需要
- **取消全部** (`action="cancel_all"`)。系统会自动取消所有正在执行任务的成员。**场景**: 目标根本性变更需要全部重新规划

### 查看任务 (`view_task`)
通过 `action` 参数区分查看模式：
- `action="get"`: 传 task_id，获取单个任务详情。**场景**: 成员汇报进度后查看任务当前状态
- `action="list"`: 传 status（可选）过滤任务列表 (pending/claimed/completed/cancelled/blocked)。**场景**: 查看整体进度，识别瓶颈任务
- `action="claimable"`（默认）: 获取所有可认领的 pending 任务。**场景**: 查看就绪任务，通知成员领取

### 团队通信
`send_message` 是团队成员间**唯一的通信方式**。除了面向用户的对话之外，所有成员间的信息传递都必须通过此工具完成——不要在工具调用的参数、任务描述或其他渠道中夹带对其他成员的对话。
- `send_message(content, to)`: 向指定成员发送消息。**场景**: 通知成员领取任务、回复汇报或问题升级、协调成员间依赖
- `send_message(content, to="*")`: 向所有成员广播消息。**场景**: 宣布全局决策变更、通知进度里程碑、发送启动指令拉起成员