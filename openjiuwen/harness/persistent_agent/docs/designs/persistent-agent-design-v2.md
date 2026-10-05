# Persistent Agent 设计（v2）

| 项 | 内容 |
| --- | --- |
| 类型 | 目标设计草案；标注"已实现"的部分以代码为准，其余均为拟议 |
| 最近一次修订日期 | 2026-10-04 |
| 关联模块 | `openjiuwen.harness.persistent_agent` |
| 设计概览 | [Persistent Agent 设计概览](persistent-agent-overview.md)（目标与原则不变） |
| 旧版详细设计 | [persistent-agent-architecture.md](persistent-agent-architecture.md)（保留不改，本文替代其作为详细设计讨论稿） |
| 开发计划 | [persistent-agent-development-plan.md](persistent-agent-development-plan.md) |

**本文与旧版的核心区别**（详见附录 B）：

1. **不再按"每一步都必须能从崩溃中恢复"来设计。** 执行中正常记录日志；崩溃后由新的 Execution 读取上一次的日志，让 Agent 自己判断进度并继续。
2. **删除旧版自建的 Checkpoint，也不使用 agent-core 的 checkpointer。** 跨 Execution 需要保留的状态，由窗口管理按白名单保存为一条"会话状态记录"。
3. **Persistent Agent 与 AgentServer 分成两部分写。** AgentServer 部分只写"必须保证什么"，不写怎么实现。
4. **前提假设**：Persistent Agent 运行在 AgentServer 进程内，向服务端记账是同步调用，失败就停止，不设计"结果未知"状态。
5. **用户确认直接复用 harness 现有的 `PermissionInterruptRail` 和中断恢复机制**，由这件事的发起人在群里点击确认卡片。
6. **上下文按 Execution 的生命周期管理**：开始时加载窗口，执行中只追加，容量达到 70% 时换窗，结束时保存。不再像旧版那样每次模型调用都重新组装，以保护模型的前缀缓存。

---

## 1. 这是什么

**一句话：** Persistent Agent 给原生 `DeepAgent` 加一层"在指定 Session 里执行"的外壳。它让同一个 Agent 能同时服务多个群聊、私聊和定时任务：各会话的工作互不干扰，执行过程可追溯，中断后能接着做。

它**不是**新的 Agent 类型：推理、规划、调用工具仍然是 DeepAgent。外壳只做四件事：

1. 把一次执行固定到一个 Session（隔离）；
2. 管理这个 Session 的上下文窗口和跨执行状态（上下文）；
3. 把执行过程报告给 AgentServer（记账与日志）；
4. 关键操作前请发起人确认（用户确认）。

### 贯穿全文的例子

> "报销助手"是一个 Agent，同时出现在三个地方：
>
> - **报销群**：张三在群里说"帮我把这周的发票整理成报销单，提交到财务系统"；
> - **和李四的私聊**：李四让它查一下差旅标准；
> - **每天 9 点的定时任务**：汇总前一天未处理的报销。
>
> 三处各是一个独立的 Session，使用同一份 Agent 配置和同一个办公空间，但各自的上下文、待办和进度互不可见。
>
> 张三这件事里，"提交到财务系统"是对外写操作，执行前群里会弹出确认卡片，只有**张三本人**点击才有效。执行到一半如果服务重启，新的执行会读上一次的日志，发现"发票已整理完、报销单已生成、提交操作已登记但没有结果"，于是先去财务系统查证，再决定是否提交。

### 本期范围

| 本期做 | 本期不做 |
| --- | --- |
| 指定 Session 内执行；群消息参与判断；上下文窗口管理与换窗 | 跨 Session 共享私有状态 |
| 记账与执行日志；发起人确认；停止和取消 | 程序层面的精确崩溃恢复（改由 Agent 读日志自行判断） |
| 办公空间文件读写；PersonalContext 知识只读；群聊记录查询 | 主动学习（独立扩展，见附录 D） |
| 向 AgentServer 交出候选回复和产物 | 渠道发送、排队调度、预算账户、群消息存储、管理报表（属于 AgentServer） |

## 2. 核心概念

| 概念 | 白话 | 例子 |
| --- | --- | --- |
| **Agent** | 数字同事本人：长期身份加上能力配置 | 报销助手 |
| **Session** | 它参与的一段独立对话 | 报销群、和李四的私聊、定时任务，各算一个 |
| **Request** | Session 里的一件具体事情 | "整理发票并提交报销单" |
| **Execution** | 处理这件事的一次实际执行 | 第一次执行在等待确认时结束，第二次接着做：Request 只有一件，Execution 有两次 |
| **Window（窗口）** | 模型能看到的工作上下文，每个 Session 任意时刻只有一个当前窗口 | 太长时换窗：旧窗归档，生成新窗 |
| **会话状态记录** | 跨 Execution 需要保留的 Session 状态，按白名单保存为一条记录 | 当前窗口、等待确认时的中断状态、"本会话记住"的确认选择 |
| **执行日志** | 每次 Execution 做过什么的记录 | "整理发票 → 成功；提交操作已登记 → 无结果" |
| **办公空间** | Agent 的工作目录，多个 Session 共用 | 报销模板、整理好的报销单 |

旧版的 **Checkpoint 概念已删除**。上下文和跨执行状态由会话状态记录负责，一次执行之内的进度靠执行日志。

### 2.1 状态归属

| 归属 | 内容 | 存在哪 |
| --- | --- | --- |
| **Session 私有** | 会话状态记录（当前窗口、中断状态、本会话确认选择、DeepAgent 运行状态） | 窗口存储（本模块实现，后端由 AgentServer 提供） |
| | 历史窗口归档 | 窗口存储（本模块实现，后端由 AgentServer 提供） |
| | 执行日志 | AgentServer 的事件存储 |
| | 会话草稿 | 办公空间的 `sessions/<session_id>/` 子目录 |
| **Agent 共享** | Agent 配置（Spec，含确认规则） | AgentServer 发布 |
| | 办公空间（除各会话草稿目录） | 文件系统，按权限读写 |
| | PersonalContext 知识 | 只读，按授权读取发布版本 |
| **群所有** | 群聊记录 | AgentServer；Session 只能按授权读取 |

**Session 私有**指其他 Session 不能读取，即使是同一个 Agent 的其他 Session 也不行。

## 3. 职责边界总览

```mermaid
flowchart LR
    U["用户 / 群成员"] --> S
    subgraph S["AgentServer（同一进程）"]
        Q["接收、排队、调度"]
        G["群聊记录"]
        WS["窗口存储后端<br/>KV / 专用文件系统"]
        B["预算账户"]
        R["记账与执行日志"]
        D["发送与投递"]
        C["确认卡片（只发给发起人）"]
    end
    subgraph P["Persistent Agent"]
        F["构造与实例隔离"]
        X["执行入口 / 恢复入口"]
        CM["上下文窗口管理<br/>含 WindowStore"]
        L["DeepAgent 推理循环"]
        CF["PermissionInterruptRail"]
    end
    Q -->|"指定 Request，创建实例"| X
    C -->|"卡片点击 → 恢复入口"| X
    X --> CM
    CM -->|"加载 / 保存 / 归档"| WS
    CM -->|"最近消息 / 搜索"| G
    X --> L
    L --> CF
    X -->|"事件（同步记账 / 日志）"| R
    L -->|"调模型前申请额度"| B
    X -->|"候选回复与产物"| D
    X -->|"等待确认"| C
    L --> W[("办公空间")]
    CM --> K[("PersonalContext 知识（只读）")]
```

| AgentServer 负责 | Persistent Agent 负责 |
| --- | --- |
| 收消息，判定属于哪个 Session，排队 | 接到指定的一件事，然后执行 |
| **保证同一 Session 同时只有一个执行者** | 一个实例只绑定一个 Session 和一次 Execution |
| 每次执行提供一个新的空 Session (runtime)，不调用 `pre_run / post_run` | 自己加载和保存会话状态记录 |
| 存储群聊记录，提供最近消息和搜索接口 | 执行开始时取最近消息作为背景；执行中按需搜索 |
| 提供窗口存储后端（`BaseKVStore` 或专用的持久化文件系统），负责命名空间隔离、保留期和删除 | 实现窗口存储（`WindowStore`）：会话状态记录和归档的格式、读写与检索；决定窗口内容、何时换窗 |
| 预算账户：预占、结算 | 每次调模型前申请额度，调用后回报实际用量 |
| 保存事件和执行日志 | 产出事件；关键事件等待记账成功后再继续 |
| 向发起人展示确认卡片，鉴别点击人身份，回调恢复入口 | 关键操作前中断并给出确认内容，恢复后继续或跳过 |
| 发送回复、上传文件到群 | 交出候选回复和产物引用，不直接发送 |
| 重启后对中断的执行发起续做 | 读取上一次的执行日志，接着做 |
| 发布 Agent 配置（含确认规则） | 提供"可配置操作"查询接口，按 Spec 中的规则执行 |

**前提假设：** Persistent Agent 运行在 AgentServer 进程内，端口都是进程内的直接调用。将来如果拆成跨进程部署，需要单独升级记账协议（引入超时查询和执行者代次），见附录 C。

---

# 第一部分：Persistent Agent 侧设计

## 4. 构造与实例隔离（已实现，PA-01～03）

这部分已经在代码中实现，本节只按代码现状描述。

- **配置**：[`PersistentAgentSpec`](../../persistent_agent/schema/spec.py) 继承 `DeepAgentSpec`，是一份扁平配置，带 `fingerprint()` 用于校验版本。
- **构造**：[`create_persistent_agent(spec, context=...)`](../../persistent_agent/factory.py) 返回**原生 `DeepAgent`**，不新建 Agent 类。构造过程不调用模型，也不调用端口。
- **一次执行的上下文**：[`PersistentBuildContext`](../../persistent_agent/schema/build_context.py) 携带 [`ExecutionContext`](../../persistent_agent/schema/execution.py)（身份与权限快照）、Session 和端口集合。它只能使用一次，并为这次执行分配独立的 `tool_owner_id`，避免同名有状态工具相互覆盖。
- **隔离**：[`execution/binding.py`](../../persistent_agent/execution/binding.py) 提供内部的 `invoke_bound / stream_bound / abort_bound / close_bound`：
  - 调用前校验 Agent、Session、Execution 是否都匹配；
  - 拒绝同一实例的并发调用；
  - 停止和清理只影响自己这次执行的资源。

**结论：一个 Execution 对应一个实例。** 实例用完即清理；同一 Session 的下一次执行（包括确认恢复）由新实例加上新的空 Session，再由窗口管理放回会话状态记录。

## 5. 一次执行的主流程

### 5.1 两个入口（拟议，PA-04）

```text
execute_persistent_request(spec, context, inputs) -> AsyncIterator[AgentEvent]
resume_persistent_request(spec, context, confirm_reply) -> AsyncIterator[AgentEvent]
```

| 入口 | 用于 | 调用方 |
| --- | --- | --- |
| `execute_persistent_request` | 新的 Request、崩溃续做、停止后继续 | AgentServer 调度 |
| `resume_persistent_request` | 确认恢复（发起人点击确认卡片，或确认超时） | AgentServer 的卡片回调 |

两个入口内部组合已有的函数，不另写一套推理循环：

1. `create_persistent_agent(spec, context)`：构造实例；
2. （仅 `execute`，且为群消息）参与判断（第 6 节）；选择忽略的直接结束；
3. **加载会话状态记录**，写回 Session state（第 7.8 节）；`execute` 在窗口末尾追加本次执行信息（第 7.4 节），`resume` 不追加任何内容；
4. `stream_bound(...)`：运行 DeepAgent（`resume` 以卡片回复作为恢复输入），把原生流式输出映射成 `AgentEvent`；执行中需要时换窗（第 7.6 节）；
5. **整理窗口 → 保存会话状态记录 → 记账 `paused` / `final`**，顺序固定（第 7.7 节）；再调用 `close_bound` 清理资源。

### 5.2 正常执行的主干时序（李四的私聊请求）

```mermaid
sequenceDiagram
    participant H as AgentServer
    participant E as 执行入口
    participant M as 窗口管理
    participant A as DeepAgent
    H->>E: 指定 Session、Request、Execution，加上 Spec 和新的空 Session
    E-->>H: execution.started（只记录）
    E->>M: 加载会话状态记录
    M->>H: 经存储后端读取会话状态记录
    M->>M: 写回 Session state，窗口末尾追加执行头、临时背景、任务输入
    loop 每次模型调用
        A->>H: 申请模型额度
        A->>A: 推理，调用工具（只追加）
        E-->>H: progress / tool.completed（只记录）
    end
    E-->>H: output.proposed
    E->>M: 整理窗口，保存会话状态记录
    E-->>H: execution.final(completed)（同步记账）
    H->>H: 发送回复（不需要用户确认）
```

带确认、换窗、崩溃的完整流程见第 12 节。

## 6. 参与判断

只有**群消息**需要判断；私聊指令和已触发的定时任务直接执行。

- 三选一：**忽略 / 回应 / 主动插话**。被 @ 是强信号，但不代表必须回答。
- 判断是**一次独立的轻量模型调用**：只带职责、当前消息和少量最近群消息（来自 `GroupChatPort`），不加载窗口、不写入窗口，也不开放写工具。
- 选择忽略：产出 `execution.final(ignored)` 后结束，不生成正式任务，也没有回复。被忽略的消息不会进入窗口，下次执行时作为最近群消息的背景出现。
- 判断失败（模型异常、输出格式不对）：有限次重试后报失败，**不能记为忽略**。

本期每条群消息都作为 Request 进入 Session 的串行队列，轮到时再判断。Agent 忙于长任务时判断会滞后；把判断改为不占用 Session 的轻量路径列入后续扩展（附录 D）。

## 7. 上下文管理

### 7.1 设计原则

**为什么不复用现有的上下文 rail。** harness 现有的 `ContextAssembleRail`、`PersonalContextRail` 和 `ContextProcessorRail` 的默认压缩链，都是为"每次模型调用都刷新一遍、按阈值自动压缩"设计的：

- 往系统提示词里写动态片段（例如 `tools` 片段），内容一变，整个前缀缓存失效；
- 默认压缩链会自动改写历史，和 Persistent Agent 自己决定的换窗时机冲突；
- 卸载和压缩归档写在共享的办公空间里，同一 Agent 的其他 Session 可以用文件工具读到。更严重的是，**状态记录如果放在 Agent 工具可访问的任何位置，模型就能改写中断状态或"本会话记住"的选择，从而绕过发起人确认**。

因此 Persistent Agent **自建一套按 Execution 生命周期的窗口管理**，并且在 Persistent Agent 中**不挂载**上述三个 rail。

**保留复用的部分。** `PromptAttachmentManager` 的"只在末尾追加增量"通道（[prompt_attachment_manager.py](../../../prompts/prompt_attachment_manager.py)）符合"执行中只追加"的要求，可以作为执行中追加动态内容的实现手段。

**前缀缓存原则：**

1. 一次 Execution 内，**系统提示词和工具定义固定不变**：不增删工具，不往系统提示词里放动态内容。
2. **动态内容只追加到末尾**，不改写已有内容。
3. 每次 Execution 只需要做一次的事情（续做日志、知识版本、最近群消息），**在开头一次性注入**。
4. 撤权**在下一个 Execution 生效**。确实需要立即生效的，主动换窗，接受一次缓存失效。
5. **换窗是唯一计划内的改写点**，阈值定为上下文容量的 70%，尽量少发生。
6. 参与判断用独立调用，有自己的短前缀，不污染窗口。

### 7.2 Session、Request、Execution、Window 的关系

**一句话：Session、Request、Execution 是"做事"的层级；Window 是"上下文"的层级。两者按 Session 对齐，在时间上交错，互不嵌套。**

| 关系 | 数量 | 说明 |
| --- | --- | --- |
| Session : Request | 1 : N | 同一 Session 的 Request 串行处理 |
| Request : Execution | 1 : N | 确认恢复、崩溃续做、停止后继续，都会产生新的 Execution |
| Session : 当前窗口 | 1 : 1 | 窗口按 Session 组织，和 Request 无关 |
| Session : 窗口（含归档） | 1 : N | 每换一次窗，链上就多一个 |
| Window : Request | N : M | 一个窗口可以覆盖多件事；一件长的事也可以跨越多个窗口 |
| Execution : Window | 1 : N | 一次执行中可以换多次窗，同一时刻只用一个 |
| 同一 Session 的活动 Execution | 最多 1 | 由 AgentServer 保证，所以窗口存储不需要并发控制 |



**时间线（报销群）：**

| 时刻 | 发生的事 | Request / Execution | 窗口操作 | 当前窗口 |
| --- | --- | --- | --- | --- |
| 1 | 张三："整理发票并提交" | Q1 / E1 开始 | 加载当前窗口（新 Session 则创建 W1），末尾追加本次执行信息 | W1 |
| 2 | 整理发票，内容越来越多 | Q1 / E1 | 达到 70%，换窗：**W1 先归档**，再生成 W2 | W2 |
| 3 | 准备提交，触发确认 | Q1 / E1 暂停 | **保存会话状态记录**（W2 + 中断状态），E1 结束；Session 阻塞 | W2 |
| 4 | 张三点击"同意" | Q1 / E2（确认恢复） | 放回 W2 和中断状态，**不追加任何内容**，原生恢复 | W2 |
| 5 | 提交完成 | Q1 / E2 完成 | 整理并保存 | W2 |
| 6 | 王五在群里闲聊 | Q2 / E3，判断为忽略 | **不写入窗口** | W2 |
| 7 | 张三："再查一下审批进度" | Q3 / E4 开始 | 加载 W2，把王五的闲聊作为最近群消息背景追加 | W2 |
| 8 | 执行中换窗，然后进程崩溃 | Q3 / E4 | W2 已归档（带 E4 的 `execution_id`），生成 W3；**崩溃导致 W3 没有保存** | 仍是 W2 |
| 9 | 服务端重启后续做 | Q3 / E5 | 加载 W2，追加 E4 的执行日志、按 E4 查到的归档引用和原任务输入 | W2 |

**规则：**

1. **窗口只由 Execution 读写**；Request 和 Session 不直接操作窗口。
2. **加载的永远是 Session 的当前窗口**，不存在"加载某个 Request 的窗口"。
3. **执行中只追加**；换窗是唯一的改写点。
4. **换窗时旧窗先归档；Execution 结束时保存会话状态记录。** 崩溃时会话状态记录保持不变。
5. **被忽略的群消息不进入窗口。**

### 7.3 窗口生命周期

```mermaid
flowchart LR
    A["Execution 开始"] --> B["加载会话状态记录<br/>写回 Session state"]
    B --> C["末尾追加本次执行信息<br/>（确认恢复时不追加）"]
    C --> D["DeepAgent 运行<br/>执行中只追加"]
    D -->|"容量达到 70%"| E["换窗<br/>先归档旧窗，再生成新窗"]
    E --> D
    D --> F["Execution 结束<br/>整理窗口 → 保存记录 → 记账"]
```

### 7.4 Execution 开始：窗口的组成

顺序原则是：**越稳定的放越前面，越临时的放越后面**。

```text
┌ 系统提示词 + 工具定义       ← 由 Spec 固定，所有 Execution 相同
├ 已保存的窗口（历史段落）     ← 上一次 Execution 保存的内容，可缓存的前缀
├ 本次执行头                   ← request_id、execution_id、执行类型
├ 临时背景块（标记为临时）     ← 最近群消息、知识导航、续做日志等
└ 本次任务输入                 ← 放在最后，离模型生成最近
   ↓ 执行中只追加：模型回复、工具调用和结果……
```

| 执行类型 | 本次执行头 | 临时背景块 | 任务输入 |
| --- | --- | --- | --- |
| **新的 Request** | 类型为"新任务" | 最近群消息（含被忽略的消息）、知识导航 | 用户的新请求 |
| **确认恢复** | **不追加**（执行信息记在结束标记里） | **不追加** | 卡片回复**作为中断恢复的输入**，不作为消息 |
| **崩溃续做** | 类型为"续做"，注明上一次的 `execution_id` | 上一次的执行日志，以及按其 `execution_id` 查到的归档引用 | **重新注入原任务输入**（它在没保存的那一段里） |
| **停止后继续** | 类型为"停止后继续" | 停止期间新增的群消息 | 继续的指示 |
| **群消息参与判断** | 不加载窗口，见第 6 节 | — | — |

**确认恢复为什么不追加任何内容：** 等待确认时，窗口末尾是模型发起、还在等结果的工具调用。按模型协议，工具结果必须紧跟在工具调用后面，中间插入任何消息都会被模型 API 拒绝。

**其他规则：**

- 同一 Session 的第一个 Execution 没有会话状态记录，就创建一个空窗口。
- **去重**：触发本次执行的消息已经是任务输入，组装最近群消息时要排除它（实现阶段处理）。
- **增量获取群消息**：每次 Execution 结束时，在执行结束标记里记下本次看到的最后一条群消息 ID；下次执行用它作为游标，只取之后的新消息，再往前多取几条作为衔接。
- **知识版本**：PersonalContext 的授权范围和知识版本在 Execution 开始时解析一次，知识导航放进临时背景块，执行中不再刷新（前缀缓存原则 3、4）。

### 7.5 执行中：只追加

- 模型回复、工具调用和结果，按 DeepAgent 原有方式追加到窗口末尾；
- 执行中需要新增的动态内容，通过附件通道以增量形式追加；
- 查询群聊记录和历史归档的结果，作为工具结果进入窗口（第 7.9 节）；
- 不改写、不删除已有内容；系统提示词和工具定义保持不变。

### 7.6 换窗

**触发：** 两次模型调用之间，当前窗口达到上下文容量的 **70%**。预留的 30% 用于生成旧窗摘要。Agent 主动请求换窗列入后续扩展（附录 D）。

**安全边界：** 必须等工具调用和工具结果都成对完整之后才能换，不能在中间切断。

**步骤（顺序固定）：**

1. **先归档旧窗**，同步写入窗口存储。归档带上 `session_id`、窗口序号、产生它的 `execution_id`、覆盖的 `request_id`；归档失败就不换窗，如果此时已经放不下，暂停并说明原因。
2. 调用一次模型生成旧窗摘要（申请额度）。失败可重试，原文已在归档里，不会丢。
3. 组装新窗，替换 ContextEngine 中的上下文；记录 `window.switched`。

**新窗的内容：**

| 内容 | 来源 |
| --- | --- |
| 旧窗摘要 | 第 2 步生成 |
| 任务状态：目标和待办 | DeepAgent 的 `task_plan` |
| 未完成的写操作 | 本次执行的写操作意图记录 |
| 归档引用 | 指向刚归档的旧窗 |
| **当前的执行头和任务输入** | **重新注入**，因为原来那份已随旧窗换出 |
| 最近几轮原文 | 保证工具调用和结果完整成对 |

如果新窗的最小必要内容仍然放不下，暂停并说明原因，不静默截断。

**旧窗持久化**，不能只作为当前 Execution 的临时文件，原因有三：

1. 窗口跨 Request：几天后问到的事情，答案可能在早已换出的旧窗里；
2. 新窗摘要引用了归档：归档如果是临时的，摘要里的结论就无法回查原文；
3. 崩溃续做需要它：那次执行期间的工作，只有归档和执行日志里还有记录。

**归档什么：**

| 内容类型 | 例子 | 归档方式 |
| --- | --- | --- |
| 可以从源头重新获取的 | 读文件、读网页、查知识库、查群聊记录的结果；临时背景块 | **只存来源引用**（路径、URL、消息 ID、版本），需要时重新读取，读取时会重新检查权限 |
| 无法重新获取的 | 用户消息、模型回复、写操作回执、外部接口返回的一次性结果 | **持久化原文** |

归档存放在窗口存储里，属于 Session 私有，**不放在共享的办公空间，也不放在 Agent 工具可访问的任何位置**（第 7.8 节）。

### 7.7 Execution 结束：整理并保存

**顺序固定：整理窗口 → 保存会话状态记录 → 记账 `paused` / `final`。**

| 顺序 | 在中间崩溃的后果 |
| --- | --- |
| 先记账，再保存 | 服务端认为已完成，但窗口里没有这次的工作内容，下一件事加载时这段上下文永久丢失 ❌ |
| **先保存，再记账** | 窗口已有完整内容和结束标记，只是没收到记账；触发崩溃续做时，Agent 看到"已经产出最终结果"，直接整理交出 ✅ |

**只保存整理过的当前窗口，覆盖保存；不为每个 Execution 另存窗口快照。** 需要审计时用执行日志回看。

| 内容 | 处理 | 理由 |
| --- | --- | --- |
| 已保存的历史段落 | **原样保留** | 保持前缀不变 |
| 本次执行头 | 保留，压缩成一行 | 标记"这是哪件事的第几次执行" |
| **临时背景块** | **删除** | 群消息和知识导航下次会重新注入最新版本；续做日志用完即失效 |
| 用户输入、确认结果 | 保留 | 对话事实 |
| 模型回复、工具调用和结果 | 保留；可以重新获取的大结果换成来源引用 | 工作过程，后续 Request 可能用到 |
| **执行结束标记** | **新增** | 简短记录：结局（完成 / 暂停等待确认 / 取消 / 失败 / 被停止）、交出的产物、本次看到的最后一条群消息 ID |

**等待确认时的特殊规则：** 末尾那个等待结果的工具调用**一律不动**，结束标记记在会话状态记录的元信息里，不追加到窗口中。临时背景块在工具调用之前，可以照常删除。

被删掉的临时块位于本次执行段落的开头，已保存的历史段落不受影响，下次执行的前缀命中不受影响。

| 结局 | 是否保存 |
| --- | --- |
| 完成、取消、失败 | 保存 |
| 暂停等待确认 | 保存，并包含中断状态 |
| 被停止 | 保存（取决于停止路径能否拿到上下文，见附录 C） |
| 参与判断后忽略 | 没有写入窗口，无需保存 |
| 崩溃 | 无法保存，靠执行日志续做 |

### 7.8 会话状态记录（替代 checkpointer）

**Persistent Agent 不使用 agent-core 的 checkpointer。** 原因：

- 现有中断恢复只读写 Session state，并不直接依赖 checkpointer（[handler.py:279](../../../../core/single_agent/interrupt/handler.py:279)）；checkpointer 只是把 Session state 存到外部再读回来的工具；
- checkpointer 保存的是整个 Session state，如果再由窗口管理另存一份当前窗口，就会出现两份上下文，加载时不知道以哪份为准；
- 默认的内存实现重启即丢失，而且数据格式完全跟随 core 内部实现。

**做法：** AgentServer 每次执行都提供**新的空 Session**，并且**不调用** `pre_run / post_run`，因此 checkpointer 不参与。窗口管理在执行开始时把会话状态记录写回 Session state，结束时按白名单取出、整理后保存。

| Session state 字段 | 作用 | 什么时候存 |
| --- | --- | --- |
| `context` | 当前窗口（ContextEngine 从这里加载上下文） | 每次执行结束 |
| `__react_agent_interruption__` | 中断状态：被拦截的工具调用、迭代轮次、原始请求 | 等待确认时 |
| `INTERRUPT_AUTO_CONFIRM_KEY` | "本会话记住"的确认选择 | 每次执行结束 |
| DeepAgent 运行状态（可选） | task_plan 等 | 每次执行结束 |

**白名单以外的字段一律不保存。** 所有字段**写在同一条记录里，一次写入**，不存在"窗口存了、中断状态没存上"的情况。

**窗口存储（`WindowStore`）的分工：** 存储逻辑由本模块实现，存储后端由 AgentServer 注入。

| 职责 | 归属 | 内容 |
| --- | --- | --- |
| 数据格式与逻辑 | **Persistent Agent**（内部组件 `WindowStore`） | 会话状态记录的结构与序列化；归档的组织方式；按 `execution_id` 查询归档；归档原文检索 |
| 存储基础设施 | **AgentServer**（注入存储后端） | 数据库或 Redis、多节点共享、备份、命名空间隔离、按保留期清理、删除 Agent 或 Session 时清空数据 |

这样划分的原因：记录里存的是 ContextEngine 上下文和 ReAct 中断状态等 agent-core 内部格式，检索也要理解消息结构，这些应由本模块掌握；而存储基础设施本来就由服务端运维。本模块可以用内存实现的后端完成全部单元测试。

**存储后端（二选一，由服务端决定）：**

| 后端 | 适用场景 | 要求 |
| --- | --- | --- |
| [`BaseKVStore`](../../../../core/foundation/store/base_kv_store.py)（默认） | 服务端已有 Redis 或数据库 | 服务端注入实例 |
| 专用的 SysOperation 文件系统 | 服务端希望统一使用远程文件存储 | **单独创建一个实例**，不注册给 Agent 的工具使用；指向持久化的存储根目录，不使用会被回收的沙箱容器 |

**无论哪种后端，都必须满足三条：**

1. **Agent 的工具访问不到**：不在办公空间里，也不在 Agent 工具所用的 SysOperation 里。否则模型可以改写中断状态或"本会话记住"的选择，绕过发起人确认。
2. **持久化**：跨 Request、跨重启都在。沙箱是计算环境，容器会按配置回收，不能用作窗口存储。
3. **会话状态记录一次写入**：KV 的 `set`、文件的 `write_file` 写整条记录。

**键的设计（以 KV 为例，由本模块定义）：**

| 键 | 内容 |
| --- | --- |
| `pa:{agent_id}:{session_id}:state` | 会话状态记录 |
| `pa:{agent_id}:{session_id}:archive:{序号}` | 一份归档 |
| `pa:{agent_id}:{session_id}:archive_index` | 归档索引：序号、`execution_id`、覆盖的 `request_id`、时间 |

服务端清理某个 Session 的全部数据只需按前缀删除，按保留期清理归档只需读取索引按时间删除，不需要理解其中存储的内容。

**`WindowStore` 的操作与失败语义：**

| 操作 | 失败时 |
| --- | --- |
| `load_state(session_id)` / `save_state(session_id, record)` | 加载失败：不开始执行；保存失败：本次视为未正常结束，按崩溃续做处理 |
| `archive(session_id, execution_id, old_window)` / `list_archives(session_id, execution_id)` | 归档失败：不换窗，必要时暂停并说明原因 |
| `search(session_id, query)` | 区分"确定没有"和"没查完或失败"。本期先用关键词匹配（读取索引后逐份扫描），可参考现有压缩归档检索的实现；数据量大时再考虑向量检索 |

### 7.9 群聊记录与历史检索

群聊记录**存在 AgentServer**，Persistent Agent 通过 `GroupChatPort` 读取：

| 操作 | 用途 | 调用时机 |
| --- | --- | --- |
| `recent_messages` | 最近群消息，作为背景 | Execution 开始时调用一次（以及参与判断时），放入临时背景块 |
| `search_messages` | 搜索历史群消息 | 执行中由模型按需调用，包装为工具 `search_group_messages` |

每条消息包含：`message_id`、发送者（稳定身份 ID 加显示名，并标明是人还是 Agent）、服务端接收时间、正文、@ 了谁、回复的是哪条、附件引用、状态（正常 / 已编辑 / 已撤回，已撤回的不返回正文）、是否属于排队中的请求。

**排队中的请求只能作为背景，不能成为当前任务的指令。** 搜索结果要区分三种情况：找到了、确定没有、没查完或查询失败（不能当作"没有"）。

**两个检索工具要分开：**

| | 群聊记录 | 历史窗口归档 |
| --- | --- | --- |
| 内容 | 群里所有公开消息 | Agent 自己的工作过程 |
| 归属 | 群 | Session 私有 |
| 工具 | `search_group_messages` | `search_session_history` |

合并成一个工具，可能会把 Agent 的私有工作过程当成群公开内容引用出去。私聊 Session 不需要 `GroupChatPort`，私聊对话本身已经在窗口和归档里。

## 8. 办公空间与知识

- **办公空间**：复用 harness 的 Workspace 和文件工具，读写都受权限引擎约束。文件写入属于写操作，按第 9 节记账。会话私有的草稿放在 `sessions/<session_id>/` 下，其他 Session 不能访问。
- **PersonalContext**：只读。通过 `KnowledgePort` 在 Execution 开始时解析授权和版本，导航放进临时背景块（第 7.4 节），正文由模型按需读取。不挂载 `PersonalContextRail`。
- 文件写好了，**不代表任务完成**，也不代表群里能看到。需要群里看到时，交出 `output.proposed` 并写明共享方式，由 AgentServer 上传或发送。

## 9. 事件、记账与执行日志

### 9.1 三级处理

| 级别 | 事件 | 处理方式 |
| --- | --- | --- |
| **用户确认** | 权限引擎判定为 `ask` 的操作；Agent 主动提问 | 复用中断机制，见第 10 节 |
| **同步记账** | `tool.intent`（仅限写操作）、`execution.paused`、`execution.final` | 记账成功才继续；失败就停止当前执行 |
| **只记录** | 其他所有事件 | 发出即可，不阻塞执行；同时构成执行日志 |

**为什么写操作必须先记账：** 防止"写操作已经执行，但没留下任何记录就崩溃了"。执行前登记了意图，续做时就一定能发现这一项没有结果，从而先查证、再决定，而不是盲目重做。

**哪些算写操作：** 以工具声明的副作用属性为准（只读或有副作用）。没有声明的一律按写操作处理。

### 9.2 事件类型（在已实现的 13 种基础上精简）

| 事件 | 用途 | 级别 |
| --- | --- | --- |
| `participation.decided` | 参与判断结果 | 只记录 |
| `execution.started` | 正式开始，服务端据此生成任务 | 只记录 |
| `progress.updated` | 进度，以及模型在工具调用之间输出的**中间结论**（每条约 500 字以内） | 只记录 |
| `reply.delta` | 流式回复片段 | 只记录 |
| `model.completed` | 模型调用及实际用量 | 只记录 |
| `tool.intent` | 写操作意图：`operation_id`、工具名、目标、参数哈希，以及**可读的参数摘要**（敏感参数脱敏） | **同步记账** |
| `tool.completed` | **所有工具**的调用结果：工具名、可读的参数摘要、状态（成功 / 失败 / 未知）、**简短结果摘要**（每条约 500 字以内）、结果引用 | 只记录 |
| `window.switched` | 换窗：旧窗归档引用、新窗序号 | 只记录 |
| `output.proposed` | 候选回复或文件交接 | 只记录（随 final 一起由服务端处理） |
| `execution.paused` | 暂停：等待确认（附带被拦截的工具名和参数，供生成卡片）、被停止或出错 | **同步记账** |
| `execution.final` | 最终结果：completed / ignored / cancelled / failed | **同步记账** |

与已实现的代码相比：删除 `checkpoint.proposed`；`window.activated` 改为 `window.switched`；`control.waiting` 与 `execution.suspended` 合并为 `execution.paused`。见附录 E。

崩溃续做查找归档**不依赖 `window.switched` 事件**，而是直接按 `execution_id` 查询窗口存储（第 7.6 节）。

### 9.3 执行日志

**事件流本身就是执行日志**，由 AgentServer 保存，不在本地另写日志文件。

| 方面 | 做法 |
| --- | --- |
| 保存 | 所有事件交给 AgentServer，按 `execution_id` 归档 |
| 用途 | 崩溃后，那次执行的窗口内容已经丢失，执行日志是 Agent 了解"上次做了什么、得到了什么"的**唯一来源** |
| 读取权限 | 只能读取**同一 Session、同一 Request** 下的执行日志 |
| 为什么不写本地文件 | 本地文件放在 Agent 工具可访问的位置，模型可以改写（与附录 A 第 17 条冲突）；换机器或沙箱回收后可能丢失；和事件流是两份记录，可能不一致 |

**Agent 怎么看到日志：** 不需要模型自己去查。崩溃续做的 Execution 开始时，由 Persistent Agent（程序）调用 `read_execution_log(上一次的 execution_id)`，把事件整理成一段简短的执行记录，作为临时背景块追加到窗口末尾。

**整理规则：**

1. 丢弃对判断进度无用的事件：`reply.delta`、`model.completed`；
2. 按 `operation_id` 或工具调用 ID，把意图和结果合并成一行；
3. 保留中间结论（`progress.updated`）；
4. 列出按 `execution_id` 查到的本次执行期间的归档；
5. 太长时，**写操作一条不省略**，只读操作可以合并或者只保留最近的若干条。

**整理后的样子：**

```text
上一次执行 E4（请求 Q3，因系统中断未完成）
1. [成功] search_group_messages(关键词="审批进度") → 找到 3 条消息
2. [成功] read_file(reports/0930报销单.xlsx) → 已读取，共 12 行
3. [结论] 审批停在财务复核环节
4. [已登记，无结果] submit_reminder(单号=BX-0930) operation_id=op_12
本次执行期间的归档：archive#7（可用 search_session_history 检索）
```

需要比摘要更完整的结果时，模型可以重新执行只读操作（续做规则允许），或者到当次执行的归档里检索。

## 10. 用户确认与控制

### 10.1 直接复用 `PermissionInterruptRail`

确认能力**完全复用** harness 现有实现，Persistent Agent 不写确认相关的 rail 代码：

- [`PermissionInterruptRail`](../../../rails/security/tool_security_rail.py) 是 [`ConfirmInterruptRail`](../../../rails/interrupt/confirm_rail.py) 的子类，由权限引擎按工具、路径、shell 命令和风险等级判定 `allow / ask / deny`（[permission_engine](../../../security/permission_engine/core.py)）；
- DeepAgent 在配置中 `permissions.enabled` 为真时**自动创建并挂载**这个 rail（[deep_agent.py:774](../../../deep_agent.py:774)）；
- 判定为 `ask` 时，rail 抛出中断，ReAct 把上下文和中断状态写进 Session state，返回确认请求；
- 恢复时，ReAct 读出中断状态，带着回复执行被拦截的工具调用，再从下一轮迭代继续（[handler.py:310](../../../../core/single_agent/interrupt/handler.py:310)）。

**Persistent Agent 只负责一件事：** 在两次执行之间，把白名单里的 Session state 字段保存好、再放回去（第 7.8 节）。

### 10.2 确认卡片与恢复

| 环节 | 做法 |
| --- | --- |
| **卡片内容** | 服务端根据 `execution.paused` 中被拦截的工具名和参数生成卡片；rail 默认的提示语不直接展示给用户 |
| **卡片选项** | **同意 / 同意并本会话记住 / 拒绝（可附反馈）**，对应 `ConfirmPayload`。不提供"永久允许"：全局规则由服务端通过 Spec 发布，不能被一次点击改写，因此服务端也不提供 `persist_allow_rule` 回调 |
| **谁能点** | 只有这件事的发起人；服务端鉴别点击人身份，其他人的点击无效 |
| **恢复** | 服务端回调 `resume_persistent_request`，传入卡片回复；新实例放回会话状态记录，以回复作为恢复输入调用 DeepAgent。**不往窗口追加任何消息** |
| **同意** | 执行被拦截的工具调用；写操作照常先记账 `tool.intent` |
| **拒绝** | 跳过该工具，rail 把发起人的反馈作为工具结果交给模型 |
| **本会话记住** | 选择被写入 `INTERRUPT_AUTO_CONFIRM_KEY`，随会话状态记录保存，本 Session 后续同类操作自动放行 |
| **超时** | 服务端以"拒绝，原因：确认超时"调用恢复入口，Agent 照常收尾 |
| **记入日志** | 确认请求和发起人的决定都是事件，续做时 Agent 能看到 |

**等待确认期间，Session 阻塞**：同一 Session 的其他 Request 继续排队，直到确认完成或超时。这样恢复时窗口末尾一定还是那个等待结果的工具调用。允许等待期间处理其他请求列入后续扩展（附录 D）。

续做规则中"查不清就问发起人"，复用同一套机制里的 `AskUserRail`，走相同的卡片和恢复路径。

### 10.3 确认规则的配置接口

| 接口 | 用途 | 实现 |
| --- | --- | --- |
| **查询** `describe_confirmable_operations(spec)` | 列出这个 Agent 的工具及默认确认级别，供 Agent 负责人在服务端界面查看和设置 | 根据 Spec 中的工具列表和权限引擎的默认风险等级生成 |
| **配置** `PersistentAgentSpec.permissions` | 服务端发布 Agent 配置时写入确认规则 | 新增字段，复用 `PermissionsSection` 格式，构造时传入 DeepAgent 配置，纳入配置指纹 |

规则随 Spec 下发，每次执行按发布时的版本生效。注意：`DeepAgentSpec` 目前没有 `permissions` 字段，而 `PersistentAgentSpec` 禁止额外字段，所以需要新增。

### 10.4 停止、取消与补充消息

同进程运行，AgentServer **直接调用**执行侧函数，不需要轮询控制端口：

| 控制 | 做法 | 结果 |
| --- | --- | --- |
| 取消这件事 | `abort_bound` | 保存会话状态记录，`execution.final(cancelled)`；已发生的外部效果不自动撤销 |
| 停止 Agent | 对所有活动执行调用 `abort_bound` | 保存会话状态记录，`execution.paused(stopped)`；重启后**不自动续做**，需要明确继续或结束 |

**补充消息一律作为新的 Request。** 张三在 Q1 执行过程中说"只算本月的"，这条消息按普通 Request 排队；等 Q1 结束后，Agent 结合当前窗口和历史归档，自己判断它是对 Q1 的补充还是一件新事情。不使用 DeepAgent 的 `follow_up / steer`。代价是补充信息无法作用于正在执行的任务，必要时 Agent 会修改上一件事的结果。

## 11. 三种"继续执行"

| | 确认恢复 | 崩溃续做 | 停止后继续 |
| --- | --- | --- | --- |
| **性质** | 计划内的暂停 | 计划外的中断 | 人为叫停 |
| **触发** | 发起人点击卡片，或确认超时 | 服务端重启后自动发起 | 必须有人明确要求继续 |
| **现场** | **完整保存**：窗口和中断状态在同一条记录里 | **没有保存**：会话状态记录停在本次执行开始前 | 窗口保存到停止那一刻，但没有中断点 |
| **从哪里继续** | 精确地从被拦截的工具调用继续 | Agent 读执行日志，自己判断进度 | Agent 根据窗口内容判断下一步 |
| **入口** | `resume_persistent_request` | `execute_persistent_request`，`recovery_ref` 指向崩溃的那次执行 | `execute_persistent_request`，执行头标明"停止后继续" |
| **是否读执行日志** | 不需要 | **需要** | 一般不需要 |
| **复用的机制** | harness 中断恢复 | 执行日志 + 续做规则 | 已保存的窗口 |

"轮与轮之间"（上一件事正常结束，下一件事开始）不属于以上任何一种，只是正常加载窗口。

### 11.1 崩溃续做规则

**触发（简单方案）：** 服务端重启后，对所有"已派发、但既没有 `final` 也没有 `paused`"的 Request **自动续做**，不按停机时长区分，也不额外询问用户。被停止的任务不在此列。

**续做规则（写入提示词）：**

1. 日志里**已成功**的步骤不再重做；只读步骤如果不确定，可以重做。
2. 写操作**已登记意图但没有结果**的：先查证（读文件状态、查外部系统）。查不清就用 `AskUserRail` 问发起人，**不能直接重做**。
3. 日志或窗口显示**已经产出最终结果**的：直接整理结果交出，不重新生成。

**代价：** 进度是"尽力记录"，不是"已确认"。最坏情况是部分只读步骤被重做，多花一些 token；写操作有意图记账兜底，不会被悄悄重复执行。

**已知风险：** 崩溃检测依赖服务端重启后的扫描。如果进程卡死而不是退出，服务端不会触发续做；一旦人工另起执行，可能出现两个执行者。这属于跨进程部署要解决的问题（附录 C）。

## 12. 关键流程

### 12.1 接收消息并进入执行

1. 群里来了一条消息，AgentServer 存入群聊记录，判断它属于哪个 Session（群 + Agent），去重后创建 Request 并排队。
2. 轮到这个 Session（没有活动 Execution，也没有等待确认的任务）时，服务端创建 Execution，提供新的空 Session，调用 `execute_persistent_request`。
3. Persistent Agent 构造实例，用一次独立调用做参与判断。
4. 选择忽略：记账 `final(ignored)`，结束，不写入窗口。
5. 选择回应：记录 `execution.started`；加载会话状态记录；在窗口末尾追加执行头、临时背景（最近群消息，已排除触发消息；知识导航）和任务输入；开始运行。
6. 结束时：整理窗口 → 保存会话状态记录 → 记账 `final`；服务端处理 `output.proposed`，发送回复。

私聊和定时任务跳过第 3、4 步。

### 12.2 执行中需要用户确认（确认恢复）

```mermaid
sequenceDiagram
    participant H as AgentServer
    participant E as Persistent Agent
    participant A as DeepAgent
    participant U as 张三（发起人）
    A->>A: 发起"提交报销单"工具调用
    A->>A: PermissionInterruptRail 判定 ask，抛出中断
    A-->>E: 确认请求（Session state 中已有 context 和中断状态）
    E->>E: 整理窗口（末尾的工具调用不动）
    E->>H: 保存会话状态记录（窗口 + 中断状态，一次写入）
    E-->>H: execution.paused（等待确认，同步记账）
    Note over E: E1 结束；Session 阻塞
    H->>U: 群里弹出确认卡片
    U->>H: 点击"同意"
    H->>E: resume_persistent_request(卡片回复)
    E->>H: 加载会话状态记录
    E->>E: 写回 Session state，不追加任何内容
    E->>A: 以卡片回复作为恢复输入调用
    A->>A: 原生恢复：rail 读到"同意"
    E-->>H: tool.intent（同步记账成功后才执行）
    A->>A: 执行提交，从下一轮迭代继续
    E->>H: 整理窗口，保存会话状态记录
    E-->>H: execution.final(completed)
```

### 12.3 运行中触发换窗

1. 某次调模型前，发现窗口达到容量的 70%。
2. 等当前的工具调用和工具结果都完整成对。
3. **先归档旧窗**，带上本次的 `execution_id`，同步写入窗口存储；失败就不换窗。
4. 调一次模型生成旧窗摘要（申请额度）。
5. 组装新窗（摘要、任务状态、未完成写操作、归档引用、重新注入的执行头和任务输入、最近几轮原文），替换 ContextEngine 中的上下文。
6. 记录 `window.switched`，继续执行。新窗只存在于内存，到本次执行结束时才随会话状态记录保存。

### 12.4 崩溃续做

```mermaid
sequenceDiagram
    participant H as AgentServer
    participant E as 新 Execution
    participant M as 窗口管理
    participant A as DeepAgent
    H->>H: 重启后扫描：已派发、无 final 也无 paused 的 Request
    H->>E: 同一 Request，新 execution_id，recovery_ref = 崩溃的那次执行
    E->>H: 读取那次执行的日志
    E->>M: 加载会话状态记录（崩溃前最后一次保存的版本）
    M->>H: 按崩溃那次的 execution_id 查询归档
    M->>M: 末尾追加：续做执行头、执行日志、归档引用、原任务输入
    E->>A: 运行
    A->>A: 按续做规则判断进度；"已登记无结果"的写操作先查证
    A-->>H: 正常执行，产出新事件
```

### 12.5 停止后继续

1. 停止时，服务端对活动执行调用 `abort_bound`；Persistent Agent 尽量保存会话状态记录，记账 `paused(stopped)`。
2. 重启后不自动续做。有人明确要求继续时，服务端调用 `execute_persistent_request`，执行头标明"停止后继续"。
3. Agent 根据窗口内容判断下一步；如果停止时没能保存窗口，就退化为崩溃续做，读取执行日志。

---

# 第二部分：对 AgentServer 的要求

本部分只写 Persistent Agent 依赖的保证，实现由 AgentServer（jiuwenswarm 仓）自行设计。

## 13. AgentServer 必须保证的事

1. **单一执行者**：同一 Session 同时只有一个活动 Execution。这是会话状态记录不需要并发控制的前提。
2. **排队**：同一 Session 的 Request（包括补充消息）串行执行；**有任务等待确认时，Session 阻塞**，直到确认完成或超时。
3. **Session 的使用方式**：每次执行提供一个新的空 Session，**不调用 `pre_run / post_run`**，不让 checkpointer 参与。
4. **固定配置**：一件事从开始到结束使用同一版本的 Spec（含确认规则），包括确认恢复和续做。
5. **记账**：同步保存 `tool.intent`、`execution.paused`、`execution.final`，保存成功才返回；同时保存所有其他事件，按 `execution_id` 可查询。
6. **群聊记录**：存储群消息，提供最近消息和搜索接口，并按授权过滤（Agent 入群时间、撤回、成员权限）。
7. **窗口存储后端**：提供 `BaseKVStore` 实例，或者一个专用的、持久化的 SysOperation 文件系统，并满足第 7.8 节的三条要求；保证 Session 之间的命名空间隔离；按 Agent 配置的保留期清理归档；Agent 或 Session 删除时清空对应数据。服务端不需要理解其中存储的内容。
8. **确认卡片**：根据 `execution.paused` 生成卡片，只发给发起人、只接受发起人点击；点击后调用 `resume_persistent_request`；超时后以"拒绝"调用。
9. **续做**：重启后对"已派发、无 `final` 也无 `paused`"的 Request 自动发起续做，传入 `recovery_ref`；被停止的任务不自动续做。
10. **发送**：处理 `output.proposed`，负责发送回复和上传文件。发送失败只重试发送，不重新执行任务。
11. **预算**：管理模型额度账户，额度不足时拒绝准入。

## 14. 端口契约

| 端口 | 方向 | 操作 | 失败语义 |
| --- | --- | --- | --- |
| **窗口存储后端** | Server → Agent（注入） | `BaseKVStore`（`set / get / delete / get_by_prefix / delete_by_prefix` 等），或专用 SysOperation 文件系统（`read_file / write_file / list_files`） | 读写失败按第 7.8 节 `WindowStore` 的失败语义处理 |
| **群聊记录** `GroupChatPort` | Agent → Server | `recent_messages(group, cursor, limit)`、`search_messages(group, query, 时间范围, 发送者, cursor, limit)` | 区分"确定没有"和"没查完或失败" |
| **记账** | Agent → Server | `record(event)`：同步，成功才返回 | 抛异常：Agent 停止当前执行 |
| **执行日志** | Agent → Server | `read_execution_log(execution_id)`：返回该次执行的全部事件；只允许读取同一 Session、同一 Request 下的执行 | 读不到：续做时提示 Agent "上一次记录缺失"，由 Agent 向发起人确认 |
| **预算** | Agent → Server | `admit(model_call_id)`、`report(model_call_id, usage)` | 拒绝准入：不调用模型，产出 `paused` |
| **知识** `KnowledgePort` | Agent → Server | 解析授权和知识版本、读取正文（PA-12） | 无权限与不存在要能区分 |
| **执行与恢复** | Server → Agent | `execute_persistent_request`、`resume_persistent_request`、`abort_bound` | — |
| **确认规则查询** | Server → Agent | `describe_confirmable_operations(spec)` | — |

`WindowStore` 是本模块的内部组件，不是对外端口（第 7.8 节）。由于有单一执行者保证，它**不需要旧版的版本号、条件更新和 epoch**。

---

## 附录 A：规则清单

1. 一个实例只服务一个 Session 的一次 Execution。
2. 写操作执行前必须记账成功。
3. `execution.final` 记账成功，这件事才算完成；流式输出结束不等于完成。
4. 文件写好不等于任务完成，也不等于群里可见。
5. 需要确认的操作由这件事的发起人点击卡片确认；对外发送不需要确认。
6. 等待确认期间 Session 阻塞；超时视为拒绝。
7. 确认恢复的执行不往窗口追加任何内容。
8. 续做时，写操作结果不明就先查证，查不清就问发起人，不盲目重做。
9. 排队中的消息和补充消息都是新的 Request，不能成为当前任务的指令。
10. Session 私有状态（会话状态记录、归档、执行日志、会话草稿）互不可见。
11. 停止不等于取消；重启后不自动续做被停止的任务。
12. 窗口只由 Execution 读写；执行中只追加，换窗是唯一的改写点。
13. 换窗时先归档旧窗；Execution 结束时按"整理 → 保存 → 记账"的顺序收尾。
14. 一次 Execution 内系统提示词和工具定义不变；撤权在下一个 Execution 生效。
15. 窗口装满且换窗后最小必要内容仍放不下时，暂停并说明原因，不静默截断。
16. 不使用 checkpointer；跨执行状态只保存白名单字段，一次写入。
17. 会话状态记录和归档不能放在 Agent 工具可访问的位置（办公空间、Agent 工具所用的 SysOperation）。

## 附录 B：新旧设计对照

| 旧版章节 | 新版去向 |
| --- | --- |
| §1 概述与需求 | 简化为 §1 |
| §2 架构总览与责任边界 | 改写为 §3，分为两部分 |
| §3 模块结构与现有基础 | §4 按代码现状重写；删除拟议目录 |
| §4 核心对象、不变量、配置生效、来源重放 | 对象简化为 §2，新增状态归属 §2.1；不变量集中到附录 A；来源去重与重放移交 AgentServer |
| §5 调用 API 与宿主端口 | 改为同进程同步调用，执行与恢复两个入口（§5.1），端口见 §14 |
| §6 状态机、停止与交互 | 确认复用 `PermissionInterruptRail` 和中断恢复（§10）；结束方式简化为 paused / final；三种继续执行见 §11 |
| §7 参与决策、发送检查、群预算 | 参与判断保留（§6，改为独立调用）；发送检查与预算账户移交 AgentServer |
| §8 事件确认与背压 | 改为三级处理（§9.1）；删除背压、事件水位、epoch |
| §9 每次模型调用前的上下文装配 | **改为按 Execution 生命周期管理**（§7.3～7.7）：开始时一次性组装，执行中只追加，以保护前缀缓存 |
| §10 ContextStore 与逻辑数据模型 | 简化为本模块内部组件 `WindowStore`（§7.8）：会话状态记录 + 归档，无版本号、无条件更新；后端由服务端注入（§14） |
| §11 活动窗口与安全换窗 | 保留"旧窗先归档、再生成新窗"（§7.6）；删除指针条件更新和 epoch 围栏；新增"可重新获取的内容只存引用"和 70% 阈值 |
| §12 工具、办公空间、知识、子代理 | §7.9（群聊与历史检索）、§8；子代理沿用 DeepAgent 现有实现，写操作同样遵守 §9 |
| §13 主动学习 | 移至附录 D |
| §14 Checkpoint、副作用、恢复 | 删除 Checkpoint；副作用改为"意图记账 + 续做查证"（§9、§11.1） |
| §15 端到端场景 | 由贯穿全文的报销例子、§7.2 时间线和 §12 关键流程替代 |
| §16～17 安全、错误分类 | 安全沿用 harness 权限引擎；错误码在实现时按现有体系补充 |
| §18 跨仓集成 | 改写为第二部分 |
| §19 41 个验证场景 | 实现各 PA 时按特性补充测试，不在设计文档中预先展开 |
| §20 决策与待定 | 见附录 C |

## 附录 C：待确认事项与实现验证点

**待确认：**

| # | 事项 | 当前建议 | 备选 |
| --- | --- | --- | --- |
| 1 | 跨进程部署 | 本期不支持 | 将来引入超时查询和执行者代次，同时解决进程卡死的检测问题 |
| 2 | 归档保留期 | 由 AgentServer 按 Agent 配置决定，本模块只提供清理接口 | 固定天数 |
| 3 | 确认超时时长 | 由 AgentServer 按 Agent 配置决定 | 固定时长 |
| 4 | 系统提示词中的 `tools` 片段随工具列表变化 | Persistent Agent 内固定工具列表即可规避 | 作为 harness 层面的问题单独跟进，影响所有 DeepAgent 场景 |

**实现阶段需要验证：**

| # | 验证点 | 为什么 |
| --- | --- | --- |
| 1 | `ToolInterruptionState` 序列化后能正确还原，包括其中的工具调用消息和子代理中断 | 确认恢复依赖它 |
| 2 | 卡片回复能转换成 DeepAgent 识别的恢复输入（`InteractiveInput`，按工具调用 ID 对应），task loop 能识别出这是一次恢复 | 确认恢复依赖它 |
| 3 | 中断时上下文末尾的结构（工具调用消息是否在上下文里、有没有占位的工具结果） | 整理规则不能碰到它们 |
| 4 | 调用方传入 Session 时，正常结束的执行是否会把上下文写回 Session state；不会的话由窗口管理主动调用 `save_contexts` 或直接从 ContextEngine 读取 | 结束时保存依赖它 |
| 5 | 停止（`abort_bound`）路径能否拿到当时的上下文并保存 | 决定停止后继续能否不读日志 |
| 6 | 换窗阈值与新窗保留的原文轮数 | 按模型上下文长度和实测缓存效果确定 |

## 附录 D：后续扩展点

- **参与判断不占用 Session**：本期每条群消息都排进 Session 队列等判断；后续改为服务端在排队前调用的轻量判断路径，只有选择回应时才创建 Request。
- **等待确认期间不阻塞 Session**：本期阻塞；后续如需在等待期间处理其他请求，窗口要支持"挂起的工具调用"。
- **崩溃续做的细化**：例如停机时间较长时，先用卡片问发起人是否继续。
- **Agent 主动换窗**：在任务阶段切换时由 Agent 主动请求换窗，本期只做容量达到 70% 时自动换窗。
- **主动学习**：独立作业，单独调度、单独预算，不占用业务 Session（原 PA-18～20）。
- **更强的恢复**：如果"Agent 读日志自行判断"在实际运行中不够可靠，再引入程序层面的进度存档。
- **执行中撤权立即生效**：本期撤权在下一个 Execution 生效；有强需求的资料可以单独做调用级检查，并主动换窗。

## 附录 E：对已实现代码与开发计划的影响

| 已实现部分 | 影响 |
| --- | --- |
| `PersistentAgentSpec` | 新增 `permissions` 字段，构造时传入 DeepAgent 配置（§10.3） |
| `ExecutionContext` | `epoch`、`authority_handle` 在同进程前提下可以简化；`recovery_ref` 继续用于续做 |
| `PersistentPorts`（[ports.py](../../persistent_agent/ports.py)） | `ExecutionAuthority` 可移除（单一执行者由 AgentServer 保证）；`ControlPort` 改为直接调用；`CommitPort` 简化为同步 `record`，并增加执行日志读取；**`ContextStore` 和 `HistoryPort` 不再是对外端口，改为内部组件 `WindowStore`，依赖服务端注入的存储后端**；**`GroupMaterialPort` 替换为 `GroupChatPort`** |
| `schema/context.py` | `WindowHead`、`HeadAdvance`、`WindowSwitch` 等带版本和条件更新的结构不再需要；`MaterialQuery / MaterialSnapshot` 改为群消息、会话状态记录和归档的专用结构 |
| `AgentEvent` | 按 §9.2 合并或删除部分事件类型 |
| 开发计划 | PA-05 改为 Execution 级装配；PA-08 改为复用 `PermissionInterruptRail` 加会话状态记录；PA-09 / PA-10 改为会话状态记录、换窗与归档；PA-11 拆为群聊检索与历史归档检索；PA-14 简化为意图记账；PA-16 / PA-17 由"执行日志 + 续做规则"替代；PA-21 的撤权失效改为 Execution 级生效 |

以上调整等本文确认后，再按 harness 文档规约同步修订 specs 和开发计划。

## 附录 F：方案变更记录

| 日期 | 变更 | 原因 |
| --- | --- | --- |
| 2026-10-04 | v2 初稿：删除 Checkpoint、引入执行日志续做、发起人确认、两部分职责划分 | 降低设计复杂度，明确与 AgentServer 的边界 |
| 2026-10-04 | 上下文管理由"复用 context_engine 压缩链"改为"按 Execution 生命周期自建窗口管理" | 现有 rail 每次模型调用都刷新、往系统提示词写动态内容，会破坏前缀缓存；默认压缩链与 Persistent Agent 的换窗时机冲突；压缩归档写在共享办公空间，存在跨 Session 读取风险 |
| 2026-10-04 | 群聊记录由 AgentServer 存储，Persistent Agent 通过 `GroupChatPort` 读取 | 群消息属于群而非 Session；服务端是群消息的唯一来源，也掌握授权信息 |
| 2026-10-04 | 不使用 checkpointer，改为窗口管理按白名单保存会话状态记录；确认直接复用 `PermissionInterruptRail` 和原生中断恢复 | 中断恢复只依赖 Session state 中的少数字段；避免当前窗口有两份来源、避免两次独立写入导致不一致，也避免依赖 checkpointer 的默认内存实现 |
| 2026-10-04 | 流程推演后的修正：确认恢复不追加任何内容；等待确认时 Session 阻塞，超时视为拒绝；换窗阈值 70%，先归档再摘要；归档带 `execution_id`；结束顺序"整理 → 保存 → 记账"；补充消息作为新 Request；崩溃后自动续做 | 推演"接收消息、确认、换窗、崩溃续做"四个流程时发现工具调用配对被破坏、窗口来源不唯一、保存顺序未定义等问题 |
| 2026-10-04 | 窗口存储改为本模块实现的内部组件，存储后端（`BaseKVStore` 或专用持久化文件系统）由 AgentServer 注入；状态记录不得放在 Agent 工具可访问的位置 | 记录格式和检索逻辑依赖 agent-core 内部格式，应由本模块掌握；存储基础设施由服务端运维；若复用 Agent 工作环境的 SysOperation，模型可篡改确认状态，且沙箱容器会被回收 |
| 2026-10-04 | 执行日志确定为服务端保存的事件流，续做时由程序取回、整理后注入窗口；`tool.completed` 改为所有工具都发，并补充可读参数摘要和结果摘要；`progress.updated` 记录中间结论 | 崩溃后窗口内容丢失，日志是唯一的进度来源，原事件只有引用和哈希，信息不足；本地日志文件可被模型篡改，也可能随沙箱回收丢失 |
