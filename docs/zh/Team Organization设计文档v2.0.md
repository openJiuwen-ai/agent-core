# Team Organization 开发文档



## 1. 目标、范围与非目标

Team Organization（简称 Organization）是在既有 Team 之上的跨 Team 协作层。每个 Team 的 Leader 以组织成员身份协作；Team 内部如何管理 teammate、拆分本地任务，不属于 Organization 的职责。

系统应提供：

- 组织创建、成员加入/退出和 owner 管理；
- 共享任务池：创建、认领、委派、开始、完成、失败、审核；
- 根任务可选择逐层责任汇总，或按需创建 Summary Team 的第三方汇总；
- 可持久化、可确认的 Member-to-Member inbox；
- 事件驱动的 Member 唤醒，以及重启后的补偿扫描；
- 可审计的任务、消息和事件记录。

本方案不规定：

- Team 内部 Leader/teammate 的调度算法；
- LLM 的业务决策或任务具体执行方式；
- 特定消息中间件、数据库或 Runner 框架；
- 跨数据库分布式事务。一个 Organization 的成员必须访问同一份组织数据存储。

## 2. 设计原则

1. **Leader 是组织协作主体。** 组织级命令只由 Leader 调用；Leader 再通过 Team 内部机制组织本地执行。
2. **持久化状态优先。** 任务、审核和消息先写入数据库；事件只负责通知和唤醒，不能作为唯一事实来源。
3. **状态变化可审计。** 每个影响任务或消息生命周期的动作都应留下活动事件。
4. **显式失败。** 任务失败是正式终态，必须带失败原因和可选产物上下文；不得仅记录日志后继续把任务当作正常进行。
5. **可靠 inbox。** 定向 Member 消息必须可查询、可确认、可在重启后重新投递，不能因一次 topic 投递而丢失。
6. **运行时只协调，不替代决策。** 运行时可以按事件唤醒 Member，但“是否认领、如何修复、是否回复”由 Member 的业务逻辑决定。
7. **根任务决定汇总方式。** Root Leader 在创建根任务时选择汇总模式；第三方汇总时，框架基于预设 `SummaryTeamSpec` 按需创建任务专属 Summary Team。

## 3. 总体模块图

```mermaid
flowchart TB
    U[用户 / 宿主应用] --> R[Team Runtime]
    R --> OR[Organization Runtime]

    subgraph Teams[多个已加入 Organization 的 Team]
        LA[Team A Leader]
        LB[Team B Leader]
        LS[按需创建的 Summary Team Member]
        TA[Team A 内部协作]
        TB[Team B 内部协作]
        LA <--> TA
        LB <--> TB
    end

    LA --> CT[组织管理工具]
    LB --> CT
    LA --> TT[组织任务工具]
    LB --> TT
    LA --> MT[Member Inbox 工具]
    LB --> MT
    LS --> TT

    CT --> OR
    TT --> TP[Organization Task Pool]
    MT --> MS[Organization Message Service]
    OR --> TP
    OR --> MS

    TP --> DB[(Organization Store)]
    MS --> DB
    TP --> EB[Event Bus]
    MS --> EB
    EB --> OR
    OR -->|排队后台 Member turn| LA
    OR -->|排队后台 Member turn| LB
    OR -->|排队后台 Member turn| LS
```

| 模块 | 职责 |
|---|---|
| Organization Runtime | 管理成员绑定、按需创建/回收 Summary Team、订阅事件、去重排队并执行后台 Member turn。 |
| Organization Task Pool | 维护任务状态机、分配、审核、汇总模式、汇总来源和活动审计。 |
| Organization Message Service | 保存 Member 消息、投递 inbox 事件、查询未读消息和确认已读。 |
| Organization Store | 保存组织、成员、任务、审核、消息和活动事件，是恢复时的真实来源。 |
| Event Bus | 抽象 Pub/Sub 通知通道；可替换为内存、消息队列或跨进程实现。 |
| Team Runtime / TeamBackend | 使 Leader 具备组织工具，并将组织级任务交给 Team 内部执行；在收到 Summary Team 创建请求时按预设 Spec 创建实例。 |

### 3.1 推荐文件架构
以下目录树是目标实现的职责边界：

```text
openjiuwen/agent_teams/organization/
├── __init__.py
├── schema.py
├── events.py
├── task_pool.py
├── message_service.py         
├── summary_team_factory.py    # 新增：按预设 Spec 创建和回收 Summary Team
├── manager.py
├── pool.py
├── runtime.py
└── tools.py
```

| 文件 | 主要功能 | 应暴露/承担的接口 | 不应承担的职责 |
|---|---|---|---|
| `__init__.py` | Organization 子包的公共入口。 | 导出领域模型、`OrganizationRuntime`、`OrganizationTaskPool`、`OrganizationMessageService` 等稳定 API。 | 不放业务逻辑，不导出私有工具基类。 |
| `schema.py` | 统一定义领域模型、状态枚举和存储记录。 | `Organization`、`OrganizationMember`、`OrganizationTask`、`TaskAggregationConfig`、`SummaryExecution`、`TaskReview`、`MemberMessage`、`MessageReceipt`、`TaskStatus`、`ReviewStatus`。 | 不读写数据库，不发布事件，不做运行时调度。 |
| `events.py` | 定义事件名、topic 规则和统一事件包装。 | `OrgTopic`、`OrgEventMessage`、任务/审核/消息事件 payload。 | 不保存事件正文，不决定业务状态。 |
| `task_pool.py` | 组织任务池和审核/汇总能力。 | 创建根任务并确定汇总模式、创建并定向委派 Summary Task、认领、委派、开始、完成、失败、审核、查询、汇总来源绑定；任务状态校验和任务审计。 | 不直接驱动 LLM/Leader turn；不保存 Leader inbox 的读取确认。 |
| `message_service.py` | 可靠 Member inbox。 | 发送、按 ID 获取、列出未读、确认收件状态、恢复未读消息；消息审计和定向/广播投递。 | 不承担任务分配、任务状态迁移或 Team 内部消息。 |
| `summary_team_factory.py` | 按框架预设 `SummaryTeamSpec` 创建任务专属 Summary Team。 | 创建/启动、注册动态 Member、恢复、停止和回收 `SummaryExecution`。 | 不决定汇总来源，不直接修改任务状态或执行汇总业务。 |
| `manager.py` | 单个 Organization 的轻量 facade。 | 创建/读取组织、注册成员、组合 Task Pool 和 Message Service、暴露订阅/发布入口。 | 不维护全局缓存，不管理多个 Team 的后台执行队列。 |
| `pool.py` | 进程内 manager registry。 | 按 `(store, organization_id, session_id)` 获取、清理或移除 manager。 | 不作为持久化来源；进程重启后不依赖其恢复状态。 |
| `runtime.py` | Organization 运行时协调器。 | 绑定/解绑 Team、协调 Summary Team 的创建/回收、订阅事件、消息去重、后台 Member turn 队列、恢复与补偿扫描。 | 不直接实现任务 SQL，也不替 Leader 作业务决策。 |
| `tools.py` | 将组织能力暴露给 Member 的结构化工具。 | 组织管理、任务协作、Member inbox 三类 `org_*` 工具及输入 schema。 | 不承载核心业务状态；调用应委托给 manager、task pool 或 message service。 |

与该目录协作但不属于其内部的模块：`agent_teams/runtime/` 负责 Team 生命周期，`agent_teams/messager/` 提供事件总线实现，`agent_teams/tools/` 提供 Team 内部工具基础设施。Organization 不应重新实现它们。

## 4. 领域模型与状态机

### 4.1 核心数据

| 实体 | 关键字段 | 说明 |
|---|---|---|
| Organization | `organization_id`、`owner_member_id`、`metadata` | 协作边界与 owner 信息；不持有常驻 Summary Member。 |
| OrganizationMember | `organization_id`、`member_id`、`capabilities` | 一个成员在组织中的身份及能力集合。 |
| OrganizationTask | `task_id`、`parent_task_id`、`root_task_id`、`status`、`assignment`、`aggregation`、`output_context` | 跨 Team 工作项，可组成任务树；根任务保存汇总模式。 |
| TaskReview | `task_id`、`reviewer_member_id`、`review_status`、`verdict` | 子任务完成后的验收记录。 |
| SummarySource | `summary_task_id`、`source_task_id`、`required` | 汇总任务与其来源任务的关联。 |
| SummaryExecution | `root_task_id`、`summary_task_id`、`summary_member_id`、`status` | 按需创建的 Summary Team 实例及其运行状态。 |
| MemberMessage | `message_id`、`from_member_id`、`to_member_id`、`content`、`created_at` | 可靠 inbox 消息正文；广播时正文仍只存储一次。 |
| MessageReceipt | `message_id`、`recipient_member_id`、`read_at`、`handled_at` | 每个收件成员的独立收件/确认状态；解决广播消息不能共享单一 `read_at` 的问题。 |
| OrganizationEvent | `event_id`、`event_type`、`payload`、`created_at` | 审计与外部观测使用的轻量活动记录。 |

### 4.2 Task Pool 任务管理 Schema

Task Pool 的核心是 `OrganizationTask`。为便于开发实现，建议采用以下 schema：

```python
class TaskCreator:
    creator_type: Literal["client", "member"]
    creator_id: str
    organization_id: str
    member_id: str | None


class TaskAssignment:
    assignment_type: Literal["unassigned", "claimed", "delegated"]
    member_id: str | None
    assigned_by_member_id: str | None
    assigned_at: int | None


class TaskOutputSpec:
    spec_type: str = "inline"
    spec_uri: str | None
    description: str | None
    inline_rules: list[str]


class TaskOutputContext:
    result_uri: str | None
    result_hash: str | None
    result_type: str | None
    description: str | None


class TaskAggregationConfig:
    mode: Literal["HIERARCHICAL", "SUMMARY_TEAM"]
    summary_task_id: str | None       # SUMMARY_TEAM 时由框架创建
    summary_member_id: str | None     # 由 SummaryTeamFactory 动态创建后填写
    final_output_task_id: str | None  # 最终结果所在任务


class OrganizationTask:
    task_id: str
    parent_task_id: str | None
    root_task_id: str
    created_by: TaskCreator
    status: Literal[
        "OPEN", "DELEGATED", "WAITING_SOURCES", "CLAIMED", "IN_PROGRESS",
        "COMPLETED", "FAILED",
    ]
    created_at: int
    updated_at: int
    title: str
    description: str
    task_type: str | None
    required_capabilities: list[str]
    assignment: TaskAssignment
    aggregation: TaskAggregationConfig | None  # 仅根任务必填
    output_spec: TaskOutputSpec | None
    output_context: TaskOutputContext | None
    output_abstract: str | None
    failure_code: Literal[
        "EXECUTION_FAILED", "SOURCE_FAILED", "SUMMARY_PROVISION_FAILED",
        "CANCELLED", "EXPIRED",
    ] | None                         # FAILED 时必填
    failure_reason: str | None        # FAILED 时必填，补充人类可读原因
    failed_at: int | None             # FAILED 时必填
    metadata: dict[str, Any]


class TaskReview:
    review_id: str
    task_id: str
    reviewer_member_id: str
    review_status: Literal["PENDING", "ACCEPTED", "REJECTED", "NEEDS_REVISION"]
    verdict: str | None
    required_changes: list[str]
    created_at: int
    updated_at: int


class TaskSource:
    summary_task_id: str
    source_task_id: str
    source_role: str | None
    required: bool
    created_at: int


class SummaryExecution:
    execution_id: str
    root_task_id: str
    summary_task_id: str
    summary_member_id: str | None
    status: Literal[
        "PROVISIONING", "WAITING_SOURCES", "RUNNING",
        "COMPLETED", "FAILED", "RELEASED",
    ]
    created_at: int
    released_at: int | None
```

字段使用说明：

| Schema | 作用 |
|---|---|
| `TaskCreator` | 记录任务由用户还是某个成员创建，以及创建成员的 `member_id`。 |
| `TaskAssignment` | 记录任务是否无人认领、已认领或已委派，以及当前由哪个成员负责。 |
| `TaskOutputSpec` | 创建任务时声明期望交付物，例如内联结果、文件 URI、接口说明或验收规则。 |
| `TaskOutputContext` | 完成或失败时提交实际产物的位置、类型、摘要说明等。 |
| `TaskAggregationConfig` | 根任务的汇总方式；第三方汇总时记录框架创建的 Summary Task、动态 Summary Member 与最终结果任务。 |
| `OrganizationTask` | Task Pool 中的主记录；`parent_task_id` 和 `root_task_id` 构成任务树，只有根任务携带 `aggregation`；失败时用 `failure_code` 区分具体失败类型。 |
| `TaskReview` | 父任务创建成员对完成子任务的验收记录。 |
| `TaskSource` | 汇总任务引用的来源任务；可在来源执行前绑定，只有满足完成条件的必需来源才会使汇总任务启动。 |
| `SummaryExecution` | Task Pool 持久化的动态 Summary Team 实例记录；恢复与回收均以此为准，不依赖内存对象。 |

### 4.3 任务状态机

```mermaid
stateDiagram-v2
    [*] --> OPEN: 创建未定向任务
    [*] --> DELEGATED: 创建时指定目标 Team
    [*] --> WAITING_SOURCES: 创建 Summary Task 并请求创建 Summary Team
    OPEN --> CLAIMED: 能力匹配 Team 认领
    CLAIMED --> IN_PROGRESS: 开始执行
    DELEGATED --> IN_PROGRESS: 目标 Member 开始执行
    WAITING_SOURCES --> IN_PROGRESS: 所有必需来源就绪
    CLAIMED --> DELEGATED: 当前 Team 再委派
    IN_PROGRESS --> DELEGATED: 当前 Team 再委派
    CLAIMED --> COMPLETED: 提交结果
    DELEGATED --> COMPLETED: 提交结果
    IN_PROGRESS --> COMPLETED: 提交结果
    WAITING_SOURCES --> FAILED: SOURCE_FAILED / SUMMARY_PROVISION_FAILED / CANCELLED / EXPIRED
    OPEN --> FAILED: CANCELLED / EXPIRED
    CLAIMED --> FAILED: EXECUTION_FAILED / CANCELLED / EXPIRED
    DELEGATED --> FAILED: EXECUTION_FAILED / CANCELLED / EXPIRED
    IN_PROGRESS --> FAILED: EXECUTION_FAILED / CANCELLED / EXPIRED
```

状态约束：

- 创建任务必须带至少一个 `required_capabilities`；
- 只有 `OPEN + UNASSIGNED` 的任务可被认领；
- 只有被分配的成员可以 `start`、`complete` 或 `fail`；
- 父任务完成前，其直接子任务必须全部 `COMPLETED` 且审核为 `ACCEPTED`；
- 子任务由创建它的父任务创建成员审核；
- `WAITING_SOURCES` 只用于框架创建的 Summary Task；`SummaryTeamFactory` 创建动态 Summary Member 后，任务即直接委派给该 Member；所有必需来源完成并通过审核后才能开始执行；
- `FAILED` 是唯一失败终态；`failure_code` 必须为 `EXECUTION_FAILED`、`SOURCE_FAILED`、`SUMMARY_PROVISION_FAILED`、`CANCELLED` 或 `EXPIRED` 之一。需要重试时，创建新的修复任务或显式的 retry 记录，不复用失败任务。

失败码说明：

| `failure_code` | 使用时机 |
|---|---|
| `EXECUTION_FAILED` | 被分配 Member 在执行过程中确认无法完成任务。 |
| `SOURCE_FAILED` | Summary Task 的必需来源任务已失败，且 Root Member 决定终止本次汇总。 |
| `SUMMARY_PROVISION_FAILED` | 框架无法基于预设 `SummaryTeamSpec` 创建、启动或恢复任务专属 Summary Team。 |
| `CANCELLED` | 用户、owner 或具有取消权限的系统操作主动终止任务。 |
| `EXPIRED` | 任务超过 deadline、租约或允许执行时长，由系统终止。 |

### 4.4 任务汇总机制

根任务由创建它的 Root Leader 选择汇总模式。该选择决定最终结果由谁产出，不改变普通任务的创建、认领、委派、失败和审核规则。

| 模式 | 适用情况 | 最终输出责任 |
|---|---|---|
| `HIERARCHICAL` | 父任务必须使用直接子任务结果继续加工。 | 每层父 Team 汇总自己的直接子任务；最终由 Root Leader 完成根任务。 |
| `SUMMARY_TEAM` | 多个任务结果相对正交，需要统一结构、表达和结论。 | 框架按需创建任务专属 Summary Team；该 Team 完成直接委派给它的 Summary Task，Root Leader 将结果返回给用户。 |

#### 4.4.1 预设 Summary Team 与按需创建

框架内置唯一的 `SummaryTeamSpec`，定义 Summary Team 的提示词、能力、工具集和默认模型策略；它不是用户配置，也不会在 Organization 创建时启动。Root Leader 选择 `SUMMARY_TEAM` 后，`SummaryTeamFactory` 基于该预设 Spec 创建任务专属实例，并将其注册为当前 Organization 的动态 `OrganizationMember`。

```text
Root Leader 选择 SUMMARY_TEAM
  → 创建 Summary Task（WAITING_SOURCES）
  → SummaryTeamFactory 创建 SummaryExecution
  → 使用预设 SummaryTeamSpec 创建 Summary Team 实例
  → 注册动态 Summary Member（capability: summary）
  → 将 Summary Task 直接委派给该 Member
```

动态 Summary Team 不主动认领普通开放任务，只处理创建它时直接委派的 Summary Task。默认一个根任务对应一个 Summary Task 和一个独立的 Summary Team 实例；汇总完成、失败或根任务取消后，该实例应被停止并回收。

#### 4.4.2 逐层责任汇总（HIERARCHICAL）

父 Team 创建子任务后，只追踪自己的直接子任务。所有直接子任务完成并审核通过后，父 Team 读取其结果、完成自己的父任务；该过程逐层向上，直到 Root Leader 完成根任务。

```mermaid
flowchart TD
    R[Root Leader / 根任务] --> A[父 Team 任务]
    A --> B[子 Team 任务 1]
    A --> C[子 Team 任务 2]
    B --> D[完成子任务结果]
    C --> E[完成子任务结果]
    D --> F[父 Team 汇总直接子任务]
    E --> F
    F --> G[完成父任务结果]
    G --> H[Root Leader 完成根任务]
```

#### 4.4.3 第三方 Summary Team 汇总（SUMMARY_TEAM）

Root Leader 创建根任务时选择 `SUMMARY_TEAM`。框架立即创建 `summary_task` 和 `SummaryExecution`，再由 `SummaryTeamFactory` 基于预设 `SummaryTeamSpec` 创建任务专属 Summary Team，并将任务**直接委派给动态 Summary Member**。Summary Task 初始状态为 `WAITING_SOURCES`。Root Leader 再把需要进入最终汇总的任务绑定为来源；所有必需来源就绪后，Runtime 唤醒该动态 Summary Member 执行汇总。

```mermaid
flowchart TD
    R[Root Leader 创建根任务] --> S[框架创建 Summary Task]
    S --> F[SummaryTeamFactory 创建任务专属 Summary Team]
    F --> D[直接委派给动态 Summary Member]
    D --> W[WAITING_SOURCES]
    R --> A[专业任务 A]
    R --> B[专业任务 B]
    A --> AO[结果 A 已完成并审核通过]
    B --> BO[结果 B 已完成并审核通过]
    AO --> X[绑定为 Summary 来源]
    BO --> X
    X --> Q{必需来源均已就绪?}
    Q -->|否| W
    Q -->|是| T[唤醒动态 Summary Member]
    T --> U[统一整合并输出最终结果]
    U --> V[完成 Summary Task]
    V --> C[回收 Summary Team 实例]
    C --> Z[Root Leader 返回最终结果]
```

规则：

- `summary_member_id` 只能由 `SummaryTeamFactory` 创建并写入；Root Leader 不能指定普通成员替代 Summary Member；
- Root Leader 通过 `org_attach_summary_sources` 显式指定来源任务，动态 Summary Team 不扫描整个 Task Pool；
- `required=true` 的来源必须已 `COMPLETED`，且有审核记录时必须为 `ACCEPTED`；
- 任一必需来源失败时，Runtime 发布失败通知并唤醒 Root Leader；Root Leader 可创建补充任务、替换来源或终止根任务；
- Summary Team 不负责复杂评分模型；发现内容缺失或冲突时，使用既有任务工具创建补充任务，完成后再继续汇总；
- 创建、启动或恢复动态 Summary Team 失败时，Summary Task 进入 `FAILED(SUMMARY_PROVISION_FAILED)`，并通知 Root Leader。

#### 4.4.4 汇总模式的配置约束

- 仅创建根任务的 Root Leader 可以设置 `aggregation.mode`；子任务不设置该字段；
- 根任务创建后、尚未创建任何工作子任务前，Root Leader 可以显式调整模式；之后禁止直接切换；
- `HIERARCHICAL` 的 `final_output_task_id` 为根任务 ID；`SUMMARY_TEAM` 的 `final_output_task_id` 为 Summary Task ID；
- 根任务状态对用户呈现为完成的前提：对应 `final_output_task_id` 已 `COMPLETED`。

## 5. 核心类图



```mermaid
classDiagram
    class OrganizationRuntime {
        +createOrganization(...) Organization
        +joinTeam(...) Organization
        +dissolveOrganization(...) Result
        +bindMember(...) void
        +provisionSummaryTeam(...) OrganizationMember
        +releaseSummaryTeam(...) void
        +handleEvent(event) void
        +resumeOrganization(...) void
    }

    class OrganizationTaskPool {
        +createTask(...) TaskResult
        +claimTask(...) TaskResult
        +delegateTask(...) TaskResult
        +startTask(...) TaskResult
        +completeTask(...) TaskResult
        +failTask(...) TaskResult
        +reviewTask(...) TaskResult
        +createRootTask(...) TaskResult
        +configureTaskAggregation(...) TaskResult
        +createSummaryTask(...) TaskResult
        +attachSummarySources(...) TaskResult
    }

    class OrganizationMessageService {
        +sendMessage(...) MessageResult
        +getMessage(messageId) MemberMessage
        +listUnreadMessages(memberId) MemberMessage[]
        +ackMessage(messageId, memberId) Result
    }

    class SummaryTeamFactory {
        +provision(...) SummaryExecution
        +start(...) OrganizationMember
        +recover(...) OrganizationMember
        +release(...) void
    }
    class SummaryTeamSpec {
        <<framework preset>>
        +capabilities
        +toolSet
        +prompt
        +modelPolicy
    }

    class OrganizationStore {
        <<interface>>
        +saveOrganization(...)
        +saveTask(...)
        +saveMessage(...)
        +listRecoverableTasks(...)
        +listUnreadMessages(...)
    }

    class EventBus {
        <<interface>>
        +publish(topic, event)
        +subscribe(topic, handler)
    }

    class Organization {
        +organizationId
        +ownerMemberId
        +members
    }
    class OrganizationTask {
        +taskId
        +status
        +assignment
        +parentTaskId
        +aggregation
    }
    class TaskAggregationConfig {
        +mode
        +summaryTaskId
        +summaryMemberId
        +finalOutputTaskId
    }
    class SummaryExecution {
        +executionId
        +rootTaskId
        +summaryTaskId
        +summaryMemberId
        +status
    }
    class MemberMessage {
        +messageId
        +content
        +createdAt
    }
    class MessageReceipt {
        +messageId
        +recipientMemberId
        +readAt
        +handledAt
    }

    OrganizationRuntime --> OrganizationTaskPool : coordinates
    OrganizationRuntime --> OrganizationMessageService : coordinates
    OrganizationRuntime --> SummaryTeamFactory : provisions and releases
    OrganizationRuntime ..> EventBus : subscribes
    OrganizationTaskPool --> OrganizationStore : persists
    OrganizationTaskPool ..> EventBus : publishes task events
    OrganizationMessageService --> OrganizationStore : persists
    OrganizationMessageService ..> EventBus : publishes inbox events
    OrganizationTaskPool ..> OrganizationTask : manages
    OrganizationTask --> TaskAggregationConfig : root task owns
    SummaryTeamFactory ..> SummaryExecution : manages
    SummaryTeamFactory ..> SummaryTeamSpec : creates from
    SummaryTeamFactory ..> OrganizationStore : persists execution
    OrganizationMessageService ..> MemberMessage : manages
    OrganizationMessageService ..> MessageReceipt : manages
    OrganizationRuntime ..> Organization : binds members
```

## 6. Org Level Tools

### 6.1 组织管理工具

组织管理工具只允许 Organization Member 调用。它们改变的是“谁属于组织”，而不是“谁做哪项工作”。

| 工具 | 何时使用 | 关键输入 | 返回与副作用 | 约束 |
|---|---|---|---|---|
| `org_create_organization` | 当前 Member 希望成为一个新协作组织的 owner 时。 | `organization_id`、`display_name?`、`description?`。 | 创建 Organization、注册当前 Member 为 owner，并返回完整组织信息。 | 不创建或启动 Summary Team；Summary Team 只在根任务选择第三方汇总时按需创建。 |
| `org_invite_member` | owner 希望把一个已激活的 Member 纳入组织时。 | `organization_id`、`member_id` | 注册目标 Member 的 capabilities，向目标 Member 注入组织能力并发布加入事件。 | 仅 owner 可调用；目标 Member 必须能访问相同的 Organization Store。 |
| `org_list_available_members` | 邀请前查询当前 session 中有哪些可用 Member 时。 | 可选过滤条件，如能力标签。 | 返回 Member ID、capabilities 和是否已加入其他组织。 | 只读，不改变成员关系。 |
| `org_view_organization` | 需要确认 owner、成员、能力或组织元数据时。 | `organization_id` | 返回组织详情和成员 roster。 | 只读；非成员是否可见由部署权限策略决定。 |
| `org_dissolve_organization` | 组织任务已结束且不再需要成员绑定时。 | `organization_id` | 解绑成员、取消订阅、清理组织数据，返回清理结果。 | 仅 owner 可调用；应拒绝仍有非终态任务的解散，或要求显式 `force`。 |

休眠 Team 模板发现和自动激活不属于 MVP 工具集；如需支持，应作为 `org_list_configured_teams` 与 `org_activate_and_invite_team` 的增强功能实现。

### 6.2 任务协作工具

任务工具只改变 OrganizationTask 和审核/汇总关系。正式工作分派必须使用这些工具，不能用 Member 消息替代。

| 工具 | 何时使用 | 关键输入 | 返回与副作用 | 约束 |
|---|---|---|---|---|
| `org_view_tasks` | 认领前、执行前或排障时查询任务。 | `action=list/open/assigned/get`、`task_id?`、`status?`、分页参数。 | 返回任务摘要或完整任务，包含状态、分配、依赖和输出。 | 只读；读取单任务时应校验组织归属。 |
| `org_create_task` | 创建根任务、子任务，或一开始就指定目标 Member 的任务。 | 标题、描述、`required_capabilities`、`parent_task_id?`、目标 `member_id?`；根任务增加 `aggregation_mode`。 | 保存 `OPEN` 或 `DELEGATED` 任务；根任务选择 `SUMMARY_TEAM` 时，框架创建 `summary_task`、按预设 Spec 创建任务专属 Summary Team，并直接委派该任务。 | 能力集合不能为空；仅 Root Member 可设置 `aggregation_mode`；子任务必须继承正确的 `root_task_id`。 |
| `org_configure_task_aggregation` | Root Member 在尚未开始拆分工作前需要调整根任务汇总方式时。 | `root_task_id`、`aggregation_mode`。 | 更新根任务汇总配置；切换到 `SUMMARY_TEAM` 时创建 Summary Task，并按预设 Spec 创建和委派 Summary Team。 | 仅根任务创建成员可调用；一旦已创建工作子任务或已绑定来源即拒绝修改。 |
| `org_claim_task` | 某 Member 能力满足开放任务且决定承担时。 | `task_id`。 | 原子更新 `OPEN -> CLAIMED`，设置 Member 分配信息，发布认领事件。 | 仅允许 `OPEN + UNASSIGNED`；并发竞争时只有一个 Member 成功。 |
| `org_delegate_task` | 当前承担 Member 发现任务应交给另一个 Member 时。 | `task_id`、目标 `member_id`。 | 更新为 `DELEGATED`，记录委派方并向目标 inbox 发布任务事件。 | 调用者必须是当前分配 Member；终态任务不可委派。 |
| `org_update_task(action=start)` | 当前 Member 准备开始已认领/已委派工作时。 | `task_id`。 | 更新为 `IN_PROGRESS`。 | 不发布完成/失败事件；只能由被分配 Member 调用；Summary Task 只能在来源就绪后由 Runtime 唤醒启动。 |
| `org_update_task(action=complete)` | 当前 Member 已产出最终结果时。 | `task_id`、`output_context`、`output_abstract`。 | 校验父子门禁后更新为 `COMPLETED`，保存结果并发布完成事件。 | 父任务的直接子任务必须全部完成且审核通过。 |
| `org_update_task(action=failed)` | 当前 Member 判断任务不可恢复、无法继续或交付失败时。 | `task_id`、`failure_code`、`failure_reason`、`output_context?`。 | 更新为 `FAILED`，持久化失败码、失败原因和已有产物，发布失败事件。 | `FAILED` 是终态；`failure_code` 必填；重试须创建修复任务或显式 retry，不得静默重开原任务。 |
| `org_view_child_tasks` | 父任务创建 Member 需要检查子任务推进情况时。 | `parent_task_id`、`only_mine?`。 | 返回直接子任务及其状态、分配和审核摘要。 | 只读；默认只返回当前 Member 创建的子任务。 |
| `org_view_pending_reviews` | 父任务创建 Member 收到子任务完成/失败事件后决定是否验收时。 | 分页参数。 | 返回等待当前 Member 审核的完成子任务及结果。 | 失败子任务不应伪装成待接受结果，应走失败处理策略。 |
| `org_review_task` | 父任务创建 Member 对已完成子任务做验收时。 | `task_id`、`ACCEPTED/REJECTED/NEEDS_REVISION`、`verdict?`、`required_changes?`。 | 保存审核结果并发布审核事件。 | 仅任务创建 Member 可审核；未完成任务不可审核。 |
| `org_create_summary_task` | 仅用于独立的非根任务汇总。根任务的第三方汇总由 `org_create_task(aggregation_mode=SUMMARY_TEAM)` 自动创建。 | 标题、描述、来源任务可选。 | 创建汇总任务，并由框架按预设 Spec 创建任务专属 Summary Team 后直接委派。 | 不得替代根任务的自动 Summary Task；调用者不能指定普通 Member 代替 Summary Member。 |
| `org_attach_summary_sources` | Root Member 为自动创建的 Summary Task 指定最终汇总来源时。 | `summary_task_id`、`source_task_ids`、`required?`、`source_role?`。 | 保存来源关联；所有必需来源就绪后发布 `org_summary_sources_ready`。 | 仅 Root Member 可为根任务的 Summary Task 绑定来源；来源必须属于同一根任务；执行完成且有审核记录时，审核必须为 `ACCEPTED`。 |
| `org_view_summary_sources` | 汇总 Team 执行前读取已绑定的来源任务和产物时。 | `summary_task_id`。 | 返回汇总任务、来源任务、输出和审核信息。 | 只读。 |
| `org_view_summary_execution` | Root Member 或 owner 需要查看动态 Summary Team 的创建、运行或回收状态时。 | `summary_task_id` 或 `root_task_id`。 | 返回 `SummaryExecution`、动态 `summary_member_id` 和生命周期状态。 | 只读；不暴露其他根任务的实例详情。 |

### 6.3 Member Inbox 工具

Member inbox 用来记录 Organization Member 之间的沟通。它不负责派发工作；要派发工作，请使用任务工具。

| 工具 | 用来做什么 | 什么时候调用 | 输入 | 返回/变化 |
|---|---|---|---|---|
| `org_send_member_message` | **发一条新消息。** | 要告诉其他 Member API 约定、依赖、阻塞或验收要求时。 | `content`、目标 `member_id?`。 | 创建消息，返回 `message_id`，通知目标 Member。 |
| `org_get_member_message` | **查看一条指定消息。** | 已经拿到某个 `message_id`，需要看它的完整内容时。 | `message_id`。 | 返回这条消息的正文、发送方和当前处理状态；不会把消息标记为已处理。 |
| `org_list_member_messages` | **查看收件箱列表。** | 不知道有哪些待处理消息，或者 UI 要展示 inbox 时。 | `unread_only?`、分页参数。 | 返回多条消息的列表；不改变消息状态。 |
| `org_ack_member_message` | **确认消息已处理。** | 已经根据消息完成回复、建任务，或确认无需处理时。 | `message_id`、处理结果可选。 | 将当前 Member 对这条消息标记为“已处理”；重复通知不会再次唤醒该 Member。 |

最常见的接收流程只有四步：

```text
收到消息通知（message_id）
  → get：读取这一条消息
  → 处理：回复、创建任务，或确认无需动作
  → ack：标记本 Member 已处理
```

简单区分：`send` 是发新消息，`get` 是打开一条已知消息，`list` 是浏览多条消息，`ack` 是处理完后的确认。消息事件只传递 `message_id`，正文仍从 Store 查询；广播消息由每个接收 Member 分别确认。

### 6.4 事件协议

事件使用统一包装：

```text
OrgEventMessage = {
  event_type: string,
  payload: object,
  sender_id: string
}
```

| 事件 | 必要 payload | 作用 |
|---|---|---|
| `org_task_created` | `task_id`、能力需求引用 | 发现能力匹配的 Team。 |
| `org_task_claimed` | `task_id`、认领 `member_id` | 唤醒认领 Member 继续执行。 |
| `org_task_delegated` | `task_id`、目标 `member_id` | 唤醒目标 Member 执行。 |
| `org_task_completed` | `task_id` | 触发父任务审核和后续任务匹配。 |
| `org_task_failed` | `task_id`、失败 `member_id`、`failure_code`、失败摘要 | 触发父任务创建 Member 或 owner 进行修复、重派。 |
| `org_task_review_requested` | `task_id`、父任务、审核 `member_id` | 通知审核 Member 审核。 |
| `org_task_reviewed` | `task_id`、审核结论 | 更新父任务推进条件。 |
| `org_summary_provisioned` | `root_task_id`、`summary_task_id`、`summary_member_id` | 动态 Summary Team 已创建并可接收直接委派。 |
| `org_summary_provision_failed` | `root_task_id`、`summary_task_id`、失败摘要 | Summary Team 创建或启动失败，通知 Root Member。 |
| `org_summary_sources_ready` | `summary_task_id` | 所有必需来源就绪，唤醒对应的动态 Summary Member。 |
| `org_summary_source_failed` | `summary_task_id`、`source_task_id`、失败摘要 | 通知 Root Member 补充、替换来源或终止根任务。 |
| `org_summary_completed` | `root_task_id`、`summary_task_id` | 通知 Root Member 读取并返回最终汇总结果。 |
| `org_member_message` | `message_id`、目标 `member_id` | 投递可靠 Member inbox。 |
| `org_member_joined` / `org_member_left` | `member_id` | 更新成员与观测视图。 |

## 7. 核心流程

### 7.1 创建组织并加入 Team

```mermaid
sequenceDiagram
    participant A as Owner Member
    participant RT as Organization Runtime
    participant ST as Organization Store
    participant B as Invited Member

    A->>RT: create_organization(organization_id)
    RT->>ST: 保存 Organization 与 owner membership
    RT->>A: 注入组织管理工具

    A->>RT: join_organization(member B)
    RT->>ST: 保存 Member B membership 与 capabilities
    RT->>B: 注入任务工具与 inbox 工具
    RT->>RT: 订阅 Member B task / inbox topic
```

### 7.2 任务认领、完成与失败

```mermaid
sequenceDiagram
    participant A as 发起 Member
    participant TP as Task Pool
    participant ST as Store
    participant EB as Event Bus
    participant RT as Organization Runtime
    participant B as 执行 Member

    A->>TP: create_task(required_capabilities)
    TP->>ST: 保存 OPEN 任务与审计事件
    TP->>EB: publish org_task_created
    EB->>RT: 任务事件
    RT->>B: 能力匹配则排队认领 turn

    B->>TP: claim_task(task_id)
    TP->>ST: 原子更新 OPEN -> CLAIMED
    TP->>EB: publish org_task_claimed
    EB->>RT: 认领事件
    RT->>B: 排队执行 turn

    B->>TP: start_task(task_id)
    TP->>ST: 更新 CLAIMED/DELEGATED -> IN_PROGRESS

    alt 执行成功
        B->>TP: complete_task(task_id, output)
        TP->>ST: 校验子任务、写入 COMPLETED 和审计事件
        TP->>EB: publish org_task_completed
    else 不可恢复失败
        B->>TP: fail_task(task_id, failure_code, failure_reason, output?)
        TP->>ST: 写入 FAILED、失败码、失败原因和审计事件
        TP->>EB: publish org_task_failed
        EB->>RT: 失败事件
        RT->>A: 唤醒父任务创建 Member 决定修复、重派或降级
    end
```

`failure_code` 用于区分失败类型，`failure_reason` 用于补充具体原因。两者应足以让父任务创建 Member 或 owner 判断下一步：创建修复任务、委派给其他 Member、降低交付范围，或结束根任务并向用户报告失败。

### 7.3 子任务审核与修复

```mermaid
flowchart LR
    C[子 Team 完成子任务] --> P[创建 PENDING 审核记录]
    P --> W[唤醒父任务创建 Member]
    W --> D{审核结论}
    D -->|ACCEPTED| G{所有直接子任务均已完成且 Accepted?}
    G -->|是| F[完成父任务]
    G -->|否| W
    D -->|REJECTED / NEEDS_REVISION| R[父 Team 决定创建修复任务或重新委派]
    R --> C
    C --> X[子任务失败]
    X --> R
```

“创建多少个修复任务”是业务策略，不由Task Pool隐式限制。若产品需要限制次数，应在任务 metadata 中保存 `retry_count` / `retry_limit`，并由 Task Pool 强制校验。

### 7.4 第三方 Summary Team 汇总

```mermaid
sequenceDiagram
    participant R as Root Member
    participant TP as Task Pool
    participant ST as Store
    participant EB as Event Bus
    participant RT as Organization Runtime
    participant SF as SummaryTeamFactory
    participant S as 动态 Summary Team

    R->>TP: create_task(aggregation_mode=SUMMARY_TEAM)
    TP->>ST: 保存根任务
    TP->>ST: 创建 Summary Task(WAITING_SOURCES)
    TP->>SF: provision(root_task_id, summary_task_id)
    SF->>RT: 基于预设 SummaryTeamSpec 创建实例
    RT->>ST: 注册动态 Summary Member 并委派 Summary Task
    RT->>EB: publish org_summary_provisioned

    R->>TP: attach_summary_sources(summary_task_id, source_task_ids)
    TP->>ST: 校验并保存来源关联
    alt 所有必需来源已完成且审核通过
        TP->>EB: publish org_summary_sources_ready
        EB->>RT: 汇总来源就绪
        RT->>S: 排队动态 Summary Member turn
        S->>TP: start_task(summary_task_id)
        S->>TP: complete_task(summary_task_id, final_output)
        TP->>EB: publish org_summary_completed
        EB->>RT: 汇总完成
        RT->>R: 注入最终结果上下文
        RT->>SF: release(summary_task_id)
    else 存在必需来源失败
        TP->>EB: publish org_summary_source_failed
        EB->>RT: 来源失败
        RT->>R: 唤醒以补充、替换或终止
    end
```

Root Member 不需要等待 Summary Member 自主认领：Summary Task 在根任务创建时，由框架基于预设 Spec 创建任务专属 Summary Team 并直接委派。Root Member 只负责定义来源任务；动态 Summary Member 只处理已绑定来源的最终整合，并在结束后由框架回收。

### 7.5 Member 消息投递、处理与确认

```mermaid
sequenceDiagram
    participant A as Member A
    participant MS as Message Service
    participant ST as Store
    participant EB as Event Bus
    participant RT as Team B Runtime
    participant B as Member B

    A->>MS: send_message(to_member_id, content)
    MS->>ST: 保存消息并创建 Member B 的未确认 receipt
    MS->>EB: publish org_member_message(message_id)
    EB->>RT: Member B inbox handler
    RT->>ST: 获取 message_id 对应的未读消息
    RT->>RT: 去重后排队 Member turn
    RT->>B: 注入消息正文和来源上下文
    B->>B: 回复、创建/更新任务，或记录无需动作
    B->>MS: ack_message(message_id, Member B)
    MS->>ST: 幂等更新 Member B receipt 的 read_at / handled_at
```

处理规则：

- 已确认消息不再重复投递；
- runtime 应按 `message_id` 去重，避免重复事件导致多个 Member turn；
- Member turn 失败、未执行或进程中断时不得确认消息；恢复扫描会再次投递未读消息；
- 广播消息为每个目标 Member 建立独立收件状态，避免一个 Member 的确认覆盖其他 Member；
- 正式工作分派仍使用任务接口，消息只用于契约、依赖、阻塞与协商。

## 8. 可靠性：恢复与补偿扫描

本节属于 MVP。因为事件总线可能丢失通知、重复投递，且运行时可能重启，所以必须通过持久化状态补偿，而非依赖事件历史。

```mermaid
flowchart TD
    A[Organization Runtime 启动或 Team 重新加入] --> B[恢复成员绑定和 topic 订阅]
    B --> C[扫描本 Team 已认领或执行中的任务]
    B --> D[扫描本 Member 未读 Member 消息]
    B --> E[扫描能力匹配的开放任务]
    B --> H[扫描未释放的 SummaryExecution]
    C --> F[去重后排队执行 turn]
    D --> F
    E --> G[去重后排队认领 turn]
    H --> I[按预设 Spec 恢复动态 Summary Team]
```

最小恢复要求：

- 任务状态、消息状态和审核状态必须可持久化查询；
- 重启后恢复 `CLAIMED` / `IN_PROGRESS` 任务的处理机会；
- 重启后按 `SummaryExecution` 恢复未释放的动态 Summary Team，并恢复 `WAITING_SOURCES` Summary Task 的来源就绪检查；
- 重启后重新投递未确认的 Member 消息；
- 所有事件处理必须容忍重复；任务状态转换和消息确认必须幂等。

增强阶段可增加退避重试、死信队列、任务租约、执行超时、跨多个 Organization 的恢复策略和更丰富的观测指标。
