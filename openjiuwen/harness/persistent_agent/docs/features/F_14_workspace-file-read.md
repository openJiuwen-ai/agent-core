# F_14 按调用授权读取办公空间文件

| 项 | 内容 |
| --- | --- |
| 类型 / 状态 | feature；PA-14，agent-core 只读组件已实现；JiuwenSwarm 宿主适配与跨仓联调待完成 |
| 日期 | 2026-10-09 |
| 负责人 | Yan Zhijun |
| 范围 | `persistent_agent/{ports.py, schema/{build_context.py,spec.py}, workspace/{access,path_policy,rail}.py, tools/{__init__,workspace_read}.py}`、外层懒加载导出及对应单测 |
| 测试基线 | agent-core 定向测试 24 passed、1 skipped；JiuwenSwarm 真实授权后端尚未联调 |
| 关联 | [架构设计](../designs/persistent-agent-architecture.md) §2.1、§8、§9、§14；[开发计划](../designs/persistent-agent-development-plan.md) PA-14、M2b；[S_04 工具副作用与办公空间规约](../specs/S_04_tools-and-workspace.md)；Harness [工作空间规约](../../../docs/specs/S_09_workspace.md) 与文件工具 |

## 背景

PA-14 只交付办公空间的受控读取能力：Agent 可以列举和读取当前调用被授权的文件，并把来源和读取时观察到的内容版本交给后续上下文或模型使用。它是只读能力，可以独立于 PA-15 的意图记账和 PA-16 的文件修改交付。

Harness 已有 `Workspace`、`ReadFileTool` 和 `ListDirTool`，并由通用权限引擎和安全 Rail 负责公共工具安全。Persistent Agent 需要补充的是调用级授权边界：同一个 Agent 的不同 Session 不能因此互相读取私有草稿；宿主无法确认授权时，不能把“不确定”降级为允许读取；模型提供的路径也不能直接成为主机文件系统路径。

本特性不把旧版 JiuwenSwarm Runtime 整体迁入 agent-core。agent-core 只定义调用级授权端口、路径约束和受保护的 Harness 工具适配；实际目录部署、业务资源授权、旧目录迁移和平台 SDK 仍由 JiuwenSwarm 负责。

## 决策

### 1. 能力显式开启，默认行为不变

`PersistentAgentSpec.enabled_features` 增加 `workspace_read`。未声明该能力时，现有 Persistent 构造路径不挂载办公空间读取 Rail；普通 DeepAgent 和个人版入口保持原行为。

启用 `workspace_read` 时，构造上下文必须同时满足：

- `PersistentPorts.workspace_access` 提供 `resolve_read_scope(execution)`；
- Spec 开启 `enable_sys_operation`，以复用既有 Harness 文件工具；
- `PersistentBuildContext` 和 Spec 装配阶段都拒绝缺少依赖的配置，不等到模型调用后才失败。

本特性只注册受保护的 `read_file` 和 `list_files` 适配器，不注册写入、编辑、Shell、CLI、MCP 或其他未纳入本特性边界的能力。

### 2. 宿主提供不可变的调用级授权范围

agent-core 新增运行时端口：

```text
WorkspaceAccessPort.resolve_read_scope(
    execution: ExecutionContext,
) -> Awaitable[WorkspaceReadScope]
```

端口只解析当前调用的授权，不执行文件读取。`WorkspaceReadScope` 是不可变运行对象，不进入可序列化 Spec，不保存 Session、锁或取消状态。

| 内容 | 约束 |
| --- | --- |
| `status` | `allowed`、`forbidden`、`unknown` 三态；`unknown` 不得按允许处理 |
| 调用身份 | `agent_id`、`session_id`，可选 `principal_ref` |
| 授权根 | `workspace_root`、`allowed_roots` |
| Session 私有范围 | `private_sessions_root` 与当前 `current_session_root` |
| 来源与版本 | `source_ref`、`policy_revision`，用于结果来源和授权绑定 |
| 诊断 | 可选 `reason`，不能直接回传给模型或暴露主机细节 |

`validate_read_scope` 将返回的范围与当前 `ExecutionContext` 的 Agent、Session、主体和授权策略版本核对。路径位于某个根目录下，或使用了相同 Agent 身份，并不能单独授予访问权；授权根必须由宿主根据当前调用给出。

### 3. 路径先做逻辑约束，再做真实路径约束

模型只传递工作空间内的逻辑相对路径。解析规则如下：

1. 拒绝空字节、绝对路径、Windows 驱动器路径、UNC 路径以及包含 `..` 的路径；
2. 统一路径分隔符，并将 `.` 归一化为工作空间根；
3. 先检查词法路径是否位于允许根或当前 Session 根；
4. 再使用规范化真实路径检查一次，拒绝通过符号链接逃逸到允许范围之外；
5. `sessions/<other_session_id>/` 等其他 Session 私有目录即使在共享工作空间根下，也必须拒绝；
6. 生成逻辑相对路径和 `source_ref`，不把后端绝对路径交给模型。

授权状态为 `forbidden` 或 `unknown` 时，在探测目标是否存在前直接返回安全错误。只有已获授权的路径才允许区分 `not_found`；这样既不读取越权文件，也不通过错误差异泄露越权路径是否存在。

### 4. 复用 Harness 文件工具并包装读取结果

`PersistentWorkspaceReadRail` 在工具初始化阶段包装现有的 `ReadFileTool` 与 `ListDirTool`，不复制文件读取实现。每次工具调用重新执行以下流程：

```text
读取当前 Execution 的授权范围
  → 校验范围身份与 policy_revision
  → 解析并校验逻辑路径
  → 对已授权路径检查目标并调用 Harness 文件工具
  → 清理后端路径并补充来源、版本和完整性信息
```

失败结果只使用稳定的安全状态：

| 状态 | 含义 |
| --- | --- |
| `forbidden` | 当前调用明确无权访问，或路径越界 |
| `unknown` | 宿主授权、身份绑定或读取条件无法确认 |
| `not_found` | 已授权范围内的目标不存在 |
| `unavailable` | 已授权，但后端读取失败或返回不符合契约 |

成功结果在原有 `ToolOutput` 数据上补充：

- 逻辑相对路径；
- `source_refs`，由宿主来源引用和逻辑路径组成；
- `version`，由读取时观察到的文件元数据生成的不透明版本值；
- `status`、`completeness` 和 `next_cursor`；
- `content` 或列表数据。

`ReadFileTool` 显式使用 `offset` / `limit` 时标记为 `partial`；当前 `ListDirTool` 没有分页契约，因此列表结果保持 `complete`，不虚构游标。结构化字段和工具生成的文本中若包含后端绝对路径，适配器会替换为逻辑相对路径。

这里的 `version` 是读取时观察到的元数据版本，不是内容快照、条件更新令牌或持久化事实。文件可能在授权、读取和后续使用之间发生变化；来源失效和下一次 Execution 的重新读取由 PA-22 处理。

### 5. JiuwenSwarm 适配边界

JiuwenSwarm 侧实现 `WorkspaceAccessPort`，并把宿主事实转换成 `WorkspaceReadScope`。适配器至少需要：

1. 从 `ExecutionContext` 定位当前 Agent、Session 和主体；
2. 使用服务端当前授权结果生成 `allowed_roots`，并单独标出 `sessions/<session_id>/` 的私有范围；
3. 无法确认主体、Session、资源授权或策略版本时返回 `unknown`，不返回一个放宽范围的 `allowed`；
4. 保留宿主的目录初始化、`sessions/feishu/<chat_id>` 等平台映射、旧工作目录迁移、模板写入和 Feishu / 资源 SDK；
5. 构造 Persistent Agent 时注入 `PersistentPorts.workspace_access`，并显式开启 `workspace_read`；
6. 不把平台绝对路径、授权诊断或 SDK 对象放进 Spec、模型输入或工具错误文本。

#### 5.1 现有 JiuwenSwarm 组件的对应关系

旧版目录中的组件可以作为宿主适配的实现来源，但不直接搬进 agent-core：

| JiuwenSwarm 现有位置 | PA-14 中的用法 | 不应迁入 agent-core 的内容 |
| --- | --- | --- |
| `server/runtime/persistent_agents/agent_workspace.py` 的 `ensure_conversation_session_workspace` 与 `ConversationSessionWorkspace` | 在会话创建 / 恢复阶段准备当前 Session 的物理目录，得到当前 Session 根目录 | 目录初始化、`scratch` / `outputs` 的产品约定、旧 `work` 目录迁移和 manifest 兼容逻辑 |
| `server/runtime/persistent_agents/resource_scope.py` 的 `PersistentResourceScope.workspace_dir` | 作为 Agent 共享办公空间的物理根目录来源 | `resource_dir`、MCP 目录、Lark CLI 配置、runtime 和凭据目录；它们不能因为位于同一 Agent 根下就自动成为可读范围 |
| `models.py` 的 `PersistentAgentRecord.workspace_dir` | 取得 Agent 工作空间路径；`PersistentConversationRecord.runtime_session_id` 用来和 `ExecutionContext.session_id` 对齐 | 不能仅用 `owner_user_id` 或 `channel_chat_id` 代替当前调用主体和 Session 的授权判断 |
| `service.py` 的 `_ensure_im_session_workspace` | 作为宿主侧“目录已准备好”的生命周期接入点 | 不在每次工具调用的 `resolve_read_scope` 中创建目录或迁移文件 |
| 迁移后的 `agent_manager.py` / 执行适配器 | 每次新 Execution 构造端口对象并注入 `PersistentBuildContext` | 不在进程级 Agent Manager 上缓存一个跨 Session 的可变授权范围 |

旧版 `render_conversation_workspace_instructions` 只是给模型的路径提示，不是权限检查。当前 PA-14 的工具必须在调用时重新通过端口解析授权；即使提示词被修改，也不能扩大可读范围。

#### 5.2 JiuwenSwarm 侧接入骨架

下面是迁移到新 Persistent Agent 构造链后的接入骨架，表示职责和调用顺序，不是当前 JiuwenSwarm 已存在的可直接运行代码：

```python
from pathlib import Path

from openjiuwen.harness.persistent_agent import (
    PersistentAgentSpec,
    PersistentBuildContext,
    PersistentPorts,
    WorkspaceReadScope,
    create_persistent_agent,
)


class JiuwenSwarmWorkspaceAccess:
    def __init__(self, *, agent, conversation, session_workspace, authorizer):
        self._agent = agent
        self._conversation = conversation
        self._session_workspace = session_workspace
        self._authorizer = authorizer
        self._workspace_root = Path(agent.workspace_dir).resolve()
        self._session_root = Path(session_workspace.root).resolve()

    async def resolve_read_scope(self, execution):
        # 真实实现必须查询当前主体 / Session / 资源授权。
        decision = await self._authorizer.resolve_workspace_read(
            agent_id=execution.agent_id,
            session_id=execution.session_id,
            principal_ref=execution.principal_ref,
        )
        if decision.status not in {"allowed", "forbidden"}:
            status = "unknown"
        else:
            status = decision.status

        # 只列出宿主确认可共享的根；不要直接把 workspace_root
        # 当作 allowed_roots，否则列举共享根时可能暴露 sibling Session 目录。
        allowed_roots = tuple(
            Path(root).resolve()
            for root in decision.shared_roots
        )
        if status == "allowed":
            allowed_roots += (self._session_root,)

        return WorkspaceReadScope(
            status=status,
            agent_id=execution.agent_id,
            session_id=execution.session_id,
            workspace_root=self._workspace_root,
            allowed_roots=allowed_roots,
            private_sessions_root=self._workspace_root / "sessions",
            current_session_root=self._session_root,
            source_ref=f"workspace://agent/{execution.agent_id}",
            policy_revision=execution.policy_revision,
            principal_ref=execution.principal_ref,
            reason=getattr(decision, "reason", None),
        )


# 会话生命周期中先执行 ensure_conversation_session_workspace(...)；
# 这里的 session_workspace 只引用已经准备好的目录。
workspace_access = JiuwenSwarmWorkspaceAccess(
    agent=agent_record,
    conversation=conversation_record,
    session_workspace=session_workspace,
    authorizer=workspace_authorizer,
)

ports = PersistentPorts(
    commit=commit_port,
    budget=budget_port,
    window_backend=window_backend,
    workspace_access=workspace_access,
)
spec = PersistentAgentSpec(
    card=agent_card,
    # 其他模型、Prompt、Workspace、Skill 等字段沿用发布配置。
    enable_sys_operation=True,
    enabled_features=frozenset({"construction", "workspace_read"}),
)
context = PersistentBuildContext(
    execution=execution_context,
    session=agent_session,
    ports=ports,
    # 其他 BuildContext 字段按现有宿主装配。
)
deep_agent = create_persistent_agent(spec, context=context)
```

调用顺序固定为：

```text
JiuwenSwarm 会话 / 目录准备
  → 为本次 Request 创建 ExecutionContext
  → 创建本次调用的 WorkspaceAccessPort 适配器
  → 注入 PersistentPorts 并开启 workspace_read
  → create_persistent_agent
  → PersistentWorkspaceReadRail
  → 每次 read_file / list_files 调用重新解析授权并委托 Harness 文件工具
```

其中 `execution.policy_revision` 必须是本次授权快照的版本；适配器不能随意用当前时间或 Agent 的 `updated_at` 冒充策略版本。若 JiuwenSwarm 只能确认“查询失败 / 授权状态过期”，就返回 `unknown`，让 agent-core 阻止读取。

当前旧版 `agent_manager.py` 里传给 `JiuWenSwarm` 的 `workspace_dir` 和 `mcp_resource_root` 只能配置运行环境，不能替代上述 `WorkspaceAccessPort`。迁移时应在新的 Persistent 构造 / 执行适配层增加端口注入，而不是把路径继续塞进模型提示词后认为已经完成授权。

agent-core 不负责查群成员、解析 Feishu 身份、初始化宿主目录、上传文件或把读取结果发送到群里。办公空间文件需要作为群文件交接时，走 PA-08 的候选输出和宿主投递流程；读取历史窗口归档走 PA-12，不与本特性的文件列表 / 文件读取工具合并。

## 拒绝的方案及原因

| 方案 | 不采用的原因 |
| --- | --- |
| 在 agent-core 中仅按 `workspace_root` 和路径前缀授权 | 无法表达当前主体、Session 私有目录和宿主实时授权；同一根目录不代表不同 Session 互相可读 |
| 把 `agent_id` 或相同项目根当成访问凭证 | Agent 身份是共享配置身份，不是 Session 私有资源授权 |
| 在构造时解析一次权限，后续复用静态范围 | 授权可能在两个工具调用之间变化；每次读取前必须重新取得当前范围 |
| 复制旧版 `agent_workspace.py` 或整套 Persistent Runtime | 会把宿主目录、平台 SDK、生命周期和渠道逻辑带入 agent-core，违反当前职责边界 |
| 重写一套 `ReadFileTool` / `ListDirTool` | 会分叉 Harness 既有文件工具和安全检查；本特性只增加 Persistent 的调用级授权适配 |
| 用“目标不存在”表示 `forbidden` 或 `unknown` | 会泄露越权资源是否存在，也会把授权不确定错误降级成可继续执行 |
| 本阶段同时开放写入并用读取结果证明可恢复 | 写入必须先经过 PA-15 的同步意图记账，文件修改属于 PA-16；读取不产生 `operation_id` 或写操作事件 |

## 对契约与状态的影响

- `PersistentPorts` 增加可选的 `workspace_access`；只有 `workspace_read` 开启时才要求该端口。
- `PersistentAgentSpec.enabled_features` 增加 `workspace_read`；Spec 仍只保存可序列化配置，端口和授权范围只存在于调用上下文。
- 外层 Persistent 包懒加载导出 `WorkspaceAccessPort` 和 `WorkspaceReadScope`，不新增 Agent 类或执行循环。
- 新增 `workspace/` 与 `tools/` 内部适配器；默认不改普通 DeepAgent 的工具集合和行为。
- 读取结果带来源和观察版本，但不写 Session 状态、WindowStore、执行日志，也不改变 `CommitPort`、`tool.intent` 或 `tool.completed` 契约。
- Harness 通用权限引擎、文件工具和安全 Rail 仍是底层安全边界；本特性不能绕过它们，也不以自身的路径检查替代子进程 / MCP / Shell 内部隔离。

## 验证

本次 agent-core 定向验证命令：

```text
uv run pytest -q tests/unit_tests/harness/persistent_agent/test_workspace_access.py tests/unit_tests/harness/persistent_agent/test_workspace_path_policy.py tests/unit_tests/harness/persistent_agent/test_workspace_read_rail.py tests/unit_tests/harness/persistent_agent/test_workspace_read_tools.py --tb=short
```

实际结果：**24 passed、1 skipped**，另有 2 个既有 Pydantic 弃用警告。

| 验证项 | 覆盖内容 |
| --- | --- |
| 授权范围与构造 | 不可变范围、端口形状、能力开启时的依赖检查、未知状态不触碰文件系统、调用身份不匹配拒绝 |
| 路径策略 | 共享根和当前 Session 根、兄弟 Session 拒绝、相对路径 / 绝对路径 / 遍历路径、符号链接逃逸、元数据版本 |
| Rail 装配 | 只注册受保护的读取 / 列举工具；未开启能力时不挂载；缺少 `enable_sys_operation` 时拒绝 |
| 工具适配 | 授权前不调用后端、`forbidden` / `unknown` / `not_found` / `unavailable` 区分、来源和版本元数据、partial 结果、后端绝对路径清理 |

本次验证使用 agent-core 测试替身作为宿主授权端口；尚未证明 JiuwenSwarm 的实际目录映射、真实权限查询和跨仓 AgentServer 联调。

本次只更新工作区文件，未提交、未推送。

## 已知遗留

- JiuwenSwarm 尚未提供生产 `WorkspaceAccessPort` 适配；真实授权来源、目录部署和跨仓联调属于宿主侧工作。
- 当前版本只观察文件元数据版本，不提供读取内容的不可变快照、条件读取或跨进程并发协调；PA-22 的来源失效处理和 PA-16 的并发写冲突另行实现。
- `ListDirTool` 当前没有分页接口，大目录的有界列举和游标语义待后续确定；本实现不虚构 `next_cursor`。
- 当前列表适配器校验被请求列举的目录，但不替宿主过滤共享父目录下的私有子目录条目；JiuwenSwarm 应优先把共享 `allowed_roots` 限定为明确的公共目录和当前 Session 根，不要把包含 `sessions` 的整个 Agent 根作为可列举根。
- 本特性不实现 PA-12 历史归档检索、PA-15 意图记账、PA-16 文件修改、PA-18 结果未知查证或 PA-22 来源失效传播。
- 运行时仍依赖 Harness 文件工具和宿主提供的文件系统边界；本特性不声称能够替代子进程、Shell、CLI、MCP 的隔离，也不把普通文件存在 / 读取成功解释为任务完成或群内可见。
