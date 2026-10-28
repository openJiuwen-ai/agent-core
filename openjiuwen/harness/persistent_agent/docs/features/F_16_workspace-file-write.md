# F_16 按调用授权修改办公空间文件

| 项 | 内容 |
| --- | --- |
| 类型 / 状态 | feature；agent-core 组件实现与验证完成，JiuwenSwarm 宿主适配 / 联调待完成 |
| 日期 | 2026-10-12；引用修订：2026-10-13 |
| 负责人 | Yan Zhijun |
| 范围 | `persistent_agent/{ports.py, schema/{build_context.py,spec.py}, workspace/, tools/}`、Harness 文件写工具的条件写入衔接、Persistent Agent 定向测试；JiuwenSwarm 的工作空间授权、Session 私有目录、SysOperation 后端及 AgentServer 联调 |
| 前置 / 关联 | PA-14 文件读取、PA-15 写前意图记账；PA-18 处理写入结果未知后的查证；PA-22 处理文件来源失效 |
| 依据 | [Persistent Agent 详细架构设计](../designs/persistent-agent-architecture.md) §4.7、§5.2.4、§6.2.1、§8.3；[开发计划](../designs/persistent-agent-development-plan.md) PA-14～PA-16、M2b；[F_14](F_14_workspace-file-read.md)；[F_15](F_15_tool-intent-before-side-effect.md)；[S_04](../specs/S_04_tools-and-workspace.md) |

## 背景与目标

PA-14 和 PA-16 的职责分开：PA-14 按当前调用授权读取办公空间文件；PA-16 按当前调用授权创建和修改文件。修改已有文件时通常还需要先读取文件并核对版本，但读取权限本身不授予写权限。

开发计划对 PA-16 的要求是：复用 Harness 文件工具按授权创建和修改文件；写入前经过 PA-15 的意图记账；会话私有草稿放在 `sessions/<session_id>/` 下且其他 Session 不可访问；并发修改同一文件时能识别冲突；文件落盘不代表任务完成或群内可见；任务失败不自动回滚文件。

本方案遵循 PA-14 已确定的职责边界：agent-core 定义写入契约、校验调用身份和逻辑路径、包装 Harness 文件工具；JiuwenSwarm 解析真实业务授权、准备和映射物理目录、提供实际运行环境与文件系统后端。agent-core 不自行创建 JiuwenSwarm 的业务目录，也不把文件绝对路径交给模型。

## 决策

### 1. 写能力显式开启，读写权限分别判定

在现有 `PersistentAgentSpec.enabled_features` 中增加 `workspace_write`，不新增单独布尔字段。默认不开启。开启时必须同时满足：

- `workspace_read` 已启用，PA-14 读取工具及其授权端口可用；修改已有文件时要有受控读取和版本观察能力；
- `enable_sys_operation=True`，继续复用 Harness 文件工具；
- PA-16 的宿主写授权端口已注入；
- Persistent Agent 正式执行入口装配 PA-15 的 `PersistentToolIntentRail`。

读授权与写授权使用不同的结果类型和判定；不得把 `WorkspaceReadScope.status == "allowed"` 当成写授权。`workspace_write` 未显式开启时，不挂载 Persistent 文件写入适配，普通 DeepAgent 和个人版行为不变。

### 2. JiuwenSwarm 提供物理目录和写授权，agent-core 校验调用边界

PA-14 的 `WorkspaceAccessPort` / `WorkspaceReadScope` 保持只读语义。PA-16 增加独立的写授权契约，避免把写能力隐含进读权限：

```text
WorkspaceWriteAccessPort.resolve_write_scope(
    execution: ExecutionContext,
) -> WorkspaceWriteScope
```

`WorkspaceWriteScope` 是不可变的运行时对象，至少包含：

| 字段 | 约束 |
| --- | --- |
| `status` | `allowed`、`forbidden`、`unknown`；`unknown` 必须失败关闭 |
| 调用身份 | `agent_id`、`session_id`、可选 `principal_ref`，必须与当前 `ExecutionContext` 一致 |
| 版本绑定 | `policy_revision`，必须与当前 Execution 的授权快照一致 |
| 工作空间 | `workspace_root`、宿主明确授予写权限的 `allowed_write_roots` |
| Session 私有范围 | `private_sessions_root`、当前 Session 的 `current_session_root` |
| 来源与诊断 | `source_ref` 及可选内部 `reason`；物理路径和授权诊断不得返回给模型 |

端口只解析宿主当前授权并返回当前调用的物理范围，不执行写入。JiuwenSwarm 可以由同一个授权适配器实现读、写两个端口，但两类 scope 必须分别计算。不能因为某个文件位于 Agent 工作空间、共享目录或相同 Agent 根下，就推定当前调用有权写入。

agent-core 每次写工具调用都重新解析并校验 scope，然后解析模型给出的工作空间逻辑相对路径。路径策略至少要：

1. 拒绝绝对路径、驱动器路径、UNC、空字节、`..` 越界路径及符号链接逃逸；
2. 拒绝访问其他 Session 的 `sessions/<other_session_id>/`；
3. 仅允许写入宿主在 `allowed_write_roots` 中明确授权的共享路径，或当前 Session 的 `current_session_root`；
4. 在探测目标是否存在之前处理 `forbidden` / `unknown`，避免通过错误差异泄露越权文件信息；
5. 对工具结果和错误中的物理路径做清理，仅暴露逻辑相对路径。

**目录所有权：** `sessions/<session_id>/` 的物理目录创建、生命周期和平台路径映射属于 JiuwenSwarm。agent-core 只使用写授权端口返回的当前 Session 根进行校验，不在工具调用过程中创建、迁移或修复宿主目录。模型不能传入任意物理路径来选择目录。

会话私有草稿与共享工作文件是两类路径：明确作为会话草稿的文件写入当前 Session 私有根；共享办公空间文件只有在宿主对当前主体和 Session 明确授予写权限时才可修改。agent-core 不把一个共享路径静默改写到 `sessions/<session_id>/`，也不自动把私有草稿发布到共享目录。

### 3. 复用 Harness 写工具，不新增另一套文件写入实现

PA-16 包装 Harness 已有的 `WriteFileTool`（模型工具名 `write_file`）和 `EditFileTool`（模型工具名 `edit_file`），沿用既有 SysOperation、文件格式处理和工具输出。新增一个 Persistent 文件访问模块，负责写路径授权、条件版本和结果清理；它不新增模型可见的工具名，也不复制一套文件系统实现。

PA-16 只开放上述办公空间文件创建、全文件写入和编辑能力。Shell、CLI、MCP、自定义工具及其他可能写入的能力不因 `workspace_write` 自动开放；它们仍受各自 Harness 安全边界约束，并按 PA-15 默认作为有副作用工具处理。

对于已有文件，沿用 Harness “先读再修改”的工具语义，但 PA-16 必须把授权和版本观察绑定到当前执行，不能仅依赖进程级、按物理路径索引的读状态作为 Session 授权证据。事件中的 `target_ref` 使用逻辑路径；`tool.intent.args_summary` 不得包含文件正文或凭证，可记录操作类型、逻辑路径、长度等受控摘要。

### 4. 调用顺序：授权和路径检查在前，PA-15 意图屏障在实际写入前

```text
模型请求 write_file / edit_file
  → 既有 PermissionInterruptRail 完成 allow / ask / deny 判定
  → 解析本次 WorkspaceWriteScope 并校验身份、授权根和逻辑路径
  → 取得当前文件版本 / 检查创建条件
  → PersistentToolIntentRail await tool.intent
  → CommitPort 确认本条关键事件可靠保存
  → 调用 Harness WriteFileTool / EditFileTool 写入
  → 记录 tool.completed（succeeded / failed / unknown）
```

授权拒绝、授权未知或路径越界时，不调用底层写工具；尚未进入派发的调用不应伪装成已完成的写操作。用户确认仍复用现有 PermissionInterruptRail，不新增 PA-16 专用确认 UI。只有授权和前置校验通过、且 `tool.intent` 持久化成功后，才允许产生文件副作用。

`CommitPort.record` 在这里负责确认 `tool.intent` 事件持久化，不负责写办公空间文件；文件实际由 Harness 工具经 JiuwenSwarm 注入的 SysOperation / 文件系统写入。意图记账失败时，沿用 PA-15 的 fail-closed 行为，底层文件写入次数必须为零。

### 5. 并发修改采用条件写入，冲突不覆盖、不自动重试

“写入时再看一下 mtime”不足以识别两个并发任务的竞争；PA-16 对已有文件采用版本条件写入：

1. 从当前 Session 获准读取文件时，取得绑定到逻辑路径和 Session 的不透明观察版本；该值不能由模型自行指定或从其他 Session 借用。PA-14 当前返回的文件元数据版本用于表示读取时观察到的状态，不是条件写入令牌；PA-16 必须取得内容摘要或后端版本令牌作为写前条件，不能直接把元数据版本当成 CAS 保证。
2. 写入时把预期版本传给实际文件写入层，由底层在写入原子边界内比较当前版本并提交。
3. 版本已变化时返回稳定、机器可识别的 `version_conflict` 结果，不修改目标文件；Agent 需重新读取并基于新内容决定后续操作，不盲目覆盖。适配器不解析本地化错误文本来猜测冲突。
4. 新文件创建使用原子“仅当目标不存在时创建”；并发创建同一路径时，失败的一方得到冲突，而不是覆盖胜方内容。条件必须区分“目标不存在”和“目标是空文件”，不能仅用空内容哈希混同两种状态。
5. 写工具调用已开始但超时 / 断连导致结果无法确认时，按 PA-15 记录 `unknown`；PA-16 不自动重试、查询或回滚，交由 PA-18 的续做查证规则处理。

PA-16 的 Persistent opt-in 写工具会把本次 preflight 取得的精确内容哈希或“仅当不存在”条件传给 `SysOperation.fs().write_file`；本地文件系统在 `ReadWriteLockManager` 的文件锁内检查该值，锁管理器提供跨进程互斥。`EditFileTool` 自身另有进程内路径锁，该锁不能单独代表跨进程保证。普通个人版 / DeepAgent 默认实例不启用这条条件写入适配，继续保留原有工具路径。JiuwenSwarm 实际使用的 SysOperation / sandbox provider 必须实现等价的条件写入语义并通过并发测试。若部署后端无法提供所需的原子条件操作，则该后端不得启用受 PA-16 保护的并发写能力。

若多个 AgentServer 进程共享同一文件系统，只有进程内 `asyncio.Lock` 不构成跨进程保证；还须确认所有写入者都经过同一具备跨进程锁和条件版本比较的文件系统实现。使用其他 sandbox provider 时，不能因为 LocalFileSystem 已实现该能力就推定 provider 也具备同等语义。

### 6. 文件落盘、任务完成和群内共享分开

- 写工具成功只说明文件操作成功，不代表 Request / 任务已完成。
- 写入 `sessions/<session_id>/` 的会话草稿默认仅本 Session 可访问，不自动成为共享工作文件。
- 文件落盘不代表群成员可见。需要交给群时，按 PA-08 生成 `output.proposed` 并提供共享方式，由 AgentServer 执行上传或发送。
- 后续任务失败时，PA-16 不自动删除或回滚已经成功落盘的文件。事件日志保留意图和工具结果，失败结局由执行入口决定。

## JiuwenSwarm 适配边界

JiuwenSwarm 的职责是把宿主的身份、授权、会话和物理文件系统接到 agent-core 契约，不重写一套只服务 Persistent Agent 的文件工具。

### 1. Session 工作空间生命周期

在创建 / 恢复 Session 的宿主生命周期中准备会话目录，并把逻辑根 `sessions/<session_id>/` 映射到本次 `ExecutionContext.session_id` 对应的物理目录。已有目录布局可以保留，但适配器必须显式返回正确的 `current_session_root` 和 `private_sessions_root`；不能以 prompt 中的路径说明替代目录隔离。每次工具调用的授权解析只读取已准备好的目录，不在 `resolve_write_scope` 中临时创建目录或执行旧目录迁移。

旧版 JiuwenSwarm 的目录初始化、业务资源授权、平台身份 / SDK、历史目录迁移仍留在 JiuwenSwarm；本特性只复用其中能够提供 Session 根路径和授权结论的宿主能力。

### 2. WorkspaceWriteAccessPort 适配

JiuwenSwarm 适配器使用本次 `ExecutionContext` 查找 Agent、Session、主体及当前授权策略，返回不可变 `WorkspaceWriteScope`。伪代码仅表达职责，不表示当前仓库已有该类或接口：

```python
class JiuwenSwarmWorkspaceWriteAccess:
    async def resolve_write_scope(self, execution):
        decision = await self.authorizer.resolve_workspace_write(
            agent_id=execution.agent_id,
            session_id=execution.session_id,
            principal_ref=execution.principal_ref,
        )
        if decision.cannot_confirm:
            status = "unknown"
        else:
            status = decision.status  # allowed / forbidden

        session_root = self.session_workspace_root(execution.session_id)
        return WorkspaceWriteScope(
            status=status,
            agent_id=execution.agent_id,
            session_id=execution.session_id,
            principal_ref=execution.principal_ref,
            policy_revision=decision.policy_revision,
            workspace_root=self.agent_workspace_root,
            allowed_write_roots=decision.allowed_write_roots,
            private_sessions_root=self.agent_workspace_root / "sessions",
            current_session_root=session_root,
            source_ref=decision.source_ref,
        )
```

适配器必须满足：

1. 用 `ExecutionContext.agent_id`、`session_id`、`principal_ref` 与 JiuwenSwarm 记录交叉核对；缺失或不一致时返回 `unknown` / 拒绝，而不是扩大授权根；
2. `allowed_write_roots` 只列宿主明确允许当前调用写入的范围，不把整个 Agent 根、配置目录、MCP 目录或凭证目录默认作为可写范围；
3. Session 目录由宿主生命周期准备，目录映射与 `ExecutionContext.session_id` 一致；其他 Session 的根不出现在当前写授权范围内；
4. 把实际 SysOperation / Sandbox workspace 根限制在获授权目录，确保模型工具路径校验之外还有宿主文件系统边界；
5. 注入 PA-15 所需 `CommitPort`，且关键 `tool.intent` 的 `record` 返回前确已持久化；
6. 不把绝对路径、平台授权诊断或 SDK 对象放入 Spec、模型输入、事件摘要或工具错误。

当前 JiuwenClaw 检查结果：源码中尚未发现 `PersistentAgentSpec`、`PersistentPorts` 或 `WorkspaceAccessPort` 的运行时接入。现有 `gateway_adapter/workspace_file_adapter.py` 处理附件落盘和目录 / 文件选择，不是 Agent 工具调用时的写授权适配点。PA-16 应接入后续 Persistent Agent 构造与 Execution 适配层；不应把文件写入权限塞进 Gateway 上传接口或复用 prompt 路径提示。

### 3. 接入顺序

```text
JiuwenSwarm 准备 Agent / Session 工作空间与授权数据
  → 创建本次 ExecutionContext
  → 注入读授权端口、写授权端口、CommitPort 和其他 PersistentPorts
  → 显式开启 workspace_read、workspace_write
  → Persistent Agent 装配读写 Rail 与 Harness 文件工具
  → 每次写工具调用重新校验写授权和逻辑路径
  → PA-15 意图落盘成功后，经宿主 SysOperation 条件写入
```

`PersistentAgentSpec` / Agent 实例可按配置复用；包含 Agent、Session、主体和授权版本的 scope 必须按 Execution / 工具调用解析，不能缓存为跨 Session 的可变授权状态。生产开放前，先验证真实 `CommitPort.record` 的关键事件持久化行为，再启用 `workspace_write`。

## 拒绝的方案及原因

| 方案 | 不采用的原因 |
| --- | --- |
| 把 PA-14 的读取授权直接当作写授权 | 读权限与写权限可能不同；违反最小权限原则，也无法表达对共享文件的写入边界 |
| 让 agent-core 创建、迁移 JiuwenSwarm 的 Session 物理目录 | 目录生命周期、业务映射和部署位置属于宿主；会把平台职责带入 SDK，并可能与既有目录布局冲突 |
| 由 JiuwenSwarm 绕过 Harness 文件工具另写一套 Agent 写工具 | 会分叉参数校验、权限 Rail、工具结果和安全行为；PA-16 要求复用 Harness 文件工具 |
| 只在工具调用前检查一次 mtime，再直接覆盖 | 检查与写入之间仍有竞争窗口，无法可靠识别并发修改 |
| 只依赖 `EditFileTool` 的进程内锁或 `WriteFileTool` 的读状态 | 进程级状态不是宿主授权，也不保证跨 Execution / 跨进程原子性；Persistent 写入必须把本次条件交给底层原子写接口 |
| 意图记账失败后继续写，或把 `unknown` 自动重试 | 会造成未记账副作用或重复写入；遵守 PA-15 的失败关闭和 PA-18 的未知结果查证边界 |
| 文件写成功就报告任务完成 / 群内可见 | 文件操作与 Request 结局、群消息投递是不同生命周期；共享交接由 PA-08 / AgentServer 负责 |
| 任务失败自动删除或恢复旧文件 | 计划明确不自动回滚；删除 / 补偿本身是新的副作用，不能隐式执行 |

## 对契约与状态的影响

- `PersistentAgentSpec.enabled_features` 增加 `workspace_write`；默认关闭，启用时依赖 `workspace_read`、PA-15 和 Harness 文件工具。
- 新增不可变 `WorkspaceWriteScope`、`WorkspaceWriteAccessPort` 和 `PersistentPorts.workspace_write_access`；现有 PA-14 读端口保持只读契约。
- 在现有 Persistent 文件访问模块中增加写路径授权、工具适配和构造校验；普通 DeepAgent 不改变行为。
- 继续使用 `write_file`、`edit_file` 工具名和既有 `tool.intent` / `tool.completed` 事件，不增事件类型，不修改 `ToolCard` 副作用字段。
- 为覆盖并发冲突，Persistent opt-in 的 Harness `WriteFileTool` / `EditFileTool` 把本次调用的预期版本交给 SysOperation 条件写接口；新建文件使用条件不存在语义。默认工具实例不启用该 opt-in，个人版行为保持不变。部署使用的 SysOperation provider 需实现并验证相同语义。
- PA-16 不实现写操作结果未知后的查询 / 自动恢复（PA-18）、资料失效传播（PA-22）、群内文件投递（PA-08）或其他工具类型的授权。

## 验证方案

### agent-core 组件测试

| 验证项 | 必须覆盖 |
| --- | --- |
| 能力和构造 | 默认不开放写工具；开启 `workspace_write` 时缺少读 / 写授权端口、`enable_sys_operation` 或 PA-15 执行绑定必须在执行前失败 |
| scope 绑定与 fail-closed | Agent、Session、principal、policy_revision 不匹配；`unknown` / `forbidden`；授权接口异常；均不得调用底层写工具 |
| 路径隔离 | 当前获授权共享根和当前 Session 根可写；兄弟 Session 目录、绝对 / 遍历路径、符号链接逃逸、未授权共享根拒绝 |
| 意图顺序 | 底层写调用前 `tool.intent` 必须已被 CommitPort 确认；记账失败时写入调用次数为零；意图摘要不含正文和绝对路径 |
| 版本和冲突 | 同版本并发修改只允许一个写入成功；另一个返回 `version_conflict` 且不覆盖；旧版本写入不成功；同路径创建竞争不会覆盖已创建内容 |
| 结果与失败 | 成功、明确失败、结果未知正确映射到 `tool.completed`；未知不自动重试；后续 Request 失败不自动回滚已成功写入的文件 |
| Harness 回归 | 普通 DeepAgent 的现有 Write / Edit 工具行为不变；PA-14 读工具与 PA-15 Rail 回归通过 |

当前 agent-core 验证已覆盖授权 / 路径 / 条件写入 / 并发冲突 / 意图顺序，以及普通写工具的兼容性回归。PA-16 与底层条件写入定向结果为 80 passed、3 skipped；另有 1 个既有 Windows 锁清理时序测试单独排除后可通过。Harness Grep 的 5 个失败仍是 Windows 环境既有 shell allowlist 问题，与 PA-16 无关。JiuwenSwarm 真实端口、provider 和 AgentServer 联调尚未完成。

### JiuwenSwarm / AgentServer 联调

- 使用真实 Agent / Session / principal 记录解析写权限，验证 `sessions/<session_id>/` 的宿主物理映射和兄弟 Session 隔离；
- 使用实际 AgentServer 配置的 SysOperation / Sandbox provider，验证创建、编辑、版本冲突、权限拒绝及多 Execution 并发；若部署为多进程共享文件系统，也要验证跨进程冲突；
- 验证 `tool.intent` 已可靠持久化后才发生文件副作用，并验证真实记账失败时文件保持原样；
- 验证文件写入成功但 Request 失败时文件仍保留，且不产生自动群消息；只有走 PA-08 / AgentServer 输出交接才对外共享；
- 和 PA-22 联调文件变更 / 撤权后的来源失效处理。PA-22 当前暂缓，因此这部分完成前 M2b 的完整文件失效验收仍未通过。

agent-core 测试替身只能证明调用顺序和模块逻辑，不证明 JiuwenSwarm 授权、真实 SysOperation 条件写入或关键事件的跨进程持久化。PA-16 需分别记录组件测试与宿主端到端证据；不能用 mock 结果替代真实联调。

## 已知待验证项

- 当前 JiuwenSwarm checkout 尚无 Persistent Agent 的端口注入实现；实际 AgentServer 构造 / Execution 接入点需在宿主适配开发时确认。
- agent-core 已补足 Persistent opt-in 写工具的条件哈希 / 条件创建，并将冲突映射为稳定的 `version_conflict`；实际 JiuwenSwarm 选用的 Sandbox provider 是否具备等价语义，以及多进程下是否仍成立，须联调验证。
- `WorkspaceWriteScope`、`workspace_write` 装配校验和写结果的稳定冲突状态已在 agent-core 与组件测试中对齐；在 JiuwenSwarm 的真实授权端口和条件写 provider 完成前，不在宿主生产环境开放该特性。
- PA-18 未完成前，PA-16 不处理崩溃后结果未知操作的查证和续做；PA-22 未完成前，不宣称文件来源失效链已完整验收。

