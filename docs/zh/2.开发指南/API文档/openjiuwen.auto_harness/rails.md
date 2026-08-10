# openjiuwen.auto_harness.rails

`openjiuwen.auto_harness.rails` 是 Auto Harness 的**安全护栏集合**，负责在 agent 生命周期回调中注入会话预算、取消、上下文、编辑安全、经验库、失败回滚与不可变文件保护等策略。所有 Rail 均继承自 `DeepAgentRail`（`AutoHarnessContextRail` 例外，继承 `ContextProcessorRail`），通过 `before_tool_call` / `after_tool_call` / `before_model_call` / `after_model_call` / `before_task_iteration` / `after_task_iteration` / `init` / `uninit` 等回调钩子执行策略。

子模块：

- `budget_rail`：会话时钟预算 + API 成本预算 + CI 门控日志（`BudgetRail`）；
- `cancellation_rail`：监控 orchestrator 取消请求（`CancellationRail`）；
- `context_rail`：禁用工作区上下文注入的上下文处理器（`AutoHarnessContextRail`）；
- `edit_safety_rail`：原子变更追踪 + 范围校验 + ruff 检查（`EditSafetyRail`）；
- `experience_rail`：注册经验检索工具并注入经验库 prompt（`build_experience_section` / `AutoHarnessExperienceRail`）；
- `revert_on_failure_rail`：捕获基准提交以支持失败回滚（`RevertOnFailureRail`）；
- `security_rail`：不可变文件守卫 + 输入注入扫描（`SecurityRail`）。

---

## class openjiuwen.auto_harness.rails.budget_rail.BudgetRail

```
class openjiuwen.auto_harness.rails.budget_rail.BudgetRail(DeepAgentRail)

def __init__(self, budget: SessionBudgetController) -> None
```

继承 `DeepAgentRail`，合并了原 `SessionBudgetRail`、`CostBudgetRail` 与 `CIGateRail` 的职责：在每次工具调用前检查时钟预算、在每次模型调用后按 token 用量估算并累加 API 成本，并在任务迭代边界记录 CI 门控日志。当时钟或成本预算耗尽时，通过 `ctx.request_force_finish` 请求强制结束当前运行。

**参数**：

* **budget**(SessionBudgetController)：会话预算控制器实例，需提供 `should_stop`、`add_cost` 等接口。

### async before_tool_call(ctx: AgentCallbackContext) -> None

在每次工具调用前检查会话预算；若 `budget.should_stop` 为真，记录告警并通过 `ctx.request_force_finish({"reason": "Session budget exceeded"})` 请求强制结束。

**参数**：

* **ctx**(AgentCallbackContext)：agent 回调上下文，提供 `request_force_finish` 等能力。

### async after_model_call(ctx: AgentCallbackContext) -> None

从模型响应的 usage 中估算 token 成本（输入 `3e-6` USD/token、输出 `15e-6` USD/token）并调用 `budget.add_cost` 累加；若成本预算耗尽则请求强制结束。仅当 `ctx.inputs` 为 `ModelCallInputs` 且响应携带 `usage` 时生效。

**参数**：

* **ctx**(AgentCallbackContext)：agent 回调上下文。

### async before_task_iteration(ctx: AgentCallbackContext) -> None

记录任务迭代开始的 CI 门控日志边界（`"CI gate rail: iteration starting"`）。

**参数**：

* **ctx**(AgentCallbackContext)：agent 回调上下文。

### async after_task_iteration(ctx: AgentCallbackContext) -> None

记录任务迭代完成的 CI 门控日志边界（`"CI gate rail: iteration complete"`）。

**参数**：

* **ctx**(AgentCallbackContext)：agent 回调上下文。

**样例**：

```python
>>> from openjiuwen.auto_harness.infra.session_budget import SessionBudgetController
>>> from openjiuwen.auto_harness.rails.budget_rail import BudgetRail
>>> budget = SessionBudgetController(wall_clock_secs=3600.0, cost_limit_usd=10.0)
>>> rail = BudgetRail(budget=budget)
>>> rail  # 注册到 agent 的 stream_rails 后即在每个回调节点生效
<openjiuwen.auto_harness.rails.budget_rail.BudgetRail object at 0x...>
```

---

## class openjiuwen.auto_harness.rails.cancellation_rail.CancellationRail

```
class openjiuwen.auto_harness.rails.cancellation_rail.CancellationRail(DeepAgentRail)

def __init__(self) -> None
```

继承 `DeepAgentRail`，注册在 stream_rails 上，监控 `AutoHarnessOrchestrator.should_cancel`。当 orchestrator 被请求取消时，在下一个 agent 检查点（工具调用前 / 模型调用后）请求强制结束并附带 `{"reason": "user_cancelled", "cancelled": True}`。类属性 `priority = 100`，确保尽早捕获取消信号。

### bind(orchestrator: AutoHarnessOrchestrator) -> None

创建后将 orchestrator 引用绑定到本 Rail，供后续回调读取 `should_cancel` 状态。

**参数**：

* **orchestrator**(AutoHarnessOrchestrator)：被监控的 orchestrator 实例。

### async before_tool_call(ctx: AgentCallbackContext) -> None

工具调用前检查取消状态；若 orchestrator 已请求取消，则记录日志并请求 `force_finish`。

**参数**：

* **ctx**(AgentCallbackContext)：agent 回调上下文。

### async after_model_call(ctx: AgentCallbackContext) -> None

模型调用后检查取消状态；逻辑与 `before_tool_call` 一致。

**参数**：

* **ctx**(AgentCallbackContext)：agent 回调上下文。

---

## class openjiuwen.auto_harness.rails.context_rail.AutoHarnessContextRail

```
class openjiuwen.auto_harness.rails.context_rail.AutoHarnessContextRail(ContextProcessorRail)
```

继承 `ContextProcessorRail`，保留 harness `ContextEngineeringRail` 的上下文处理器骨架，但禁用会读取工作区本地上下文文件、与 auto-harness identity 冲突的 prompt section 注入。

### async before_model_call(ctx: AgentCallbackContext) -> None

重写为空操作（直接 `return`），不注入 workspace / tools / context prompt sections。

**参数**：

* **ctx**(AgentCallbackContext)：agent 回调上下文。

### uninit(agent) -> None

重写为空操作（直接 `return`），在 agent teardown 时不改动系统 prompt sections。

**参数**：

* **agent**：关联的 agent 实例。

---

## class openjiuwen.auto_harness.rails.edit_safety_rail.EditSafetyRail

```
class openjiuwen.auto_harness.rails.edit_safety_rail.EditSafetyRail(DeepAgentRail)

def __init__(self, max_files: int = 3) -> None
```

继承 `DeepAgentRail`，合并原 `AtomicChangeRail` 与 `EditCheckRail`：硬性拦截对允许编辑范围之外的写入、追踪已编辑文件并在超出 `max_files` 时发出 steering 警告、对 Python 文件在写入后运行 `ruff check` 并把问题作为 steering 推送给 agent。

**参数**：

* **max_files**(int，可选)：本轮允许编辑的最大源文件数；超出后通过 `ctx.push_steering` 提示保持改动精简。默认值：`3`。

### async before_tool_call(ctx: AgentCallbackContext) -> None

对 `write_file` / `edit_file` 工具调用，将 `file_path` 规范化为 repo 相对路径并校验是否落在允许的编辑范围内。若越界则硬性拦截：设置 `ctx.extra["_skip_tool"] = True`、回写错误 `tool_result` 与 `ToolMessage`。允许编辑范围：`openjiuwen/dev_tools/**`、`openjiuwen/harness/**`、`openjiuwen/core/**`、`tests/**`、`examples/**`、`docs/en/**`、`docs/zh/**`。

**参数**：

* **ctx**(AgentCallbackContext)：agent 回调上下文，包含 `inputs.tool_args` 等。

### async after_tool_call(ctx: AgentCallbackContext) -> None

写入完成后将文件加入追踪集合；当数量超过 `max_files` 时 `push_steering` 警告；对 `.py` 文件运行 `ruff check`，失败时把输出作为 steering 推送要求修复。

**参数**：

* **ctx**(AgentCallbackContext)：agent 回调上下文。

### reset() -> None

在任务之间清空已追踪的编辑文件集合。

### edited_files() -> list[str]

返回按稳定（排序）顺序排列的已追踪编辑文件列表。

**返回**：

**list[str]**，已追踪的编辑文件路径（已规范化、排序）。

---

## func openjiuwen.auto_harness.rails.experience_rail.build_experience_section

```
def build_experience_section(language: str = "cn", experience_dir: str = ".auto_harness/experience") -> PromptSection
```

构建 auto-harness 经验库使用的 prompt 引导片段，注入到 `SectionName.MEMORY` 区段，根据语言给出经验库路径与使用 `experience_search` 工具的指引，优先级 `85`。

**参数**：

* **language**(str，可选)：提示语言，支持 `"cn"` 与 `"en"`。默认值：`"cn"`。
* **experience_dir**(str，可选)：经验库目录路径，将写入提示文本。默认值：`".auto_harness/experience"`。

**返回**：

**PromptSection**，可加入 `system_prompt_builder` 的 memory 区段。

---

## class openjiuwen.auto_harness.rails.experience_rail.AutoHarnessExperienceRail

```
class openjiuwen.auto_harness.rails.experience_rail.AutoHarnessExperienceRail(DeepAgentRail)

def __init__(self, experience_dir: str, *, language: str = "cn") -> None
```

继承 `DeepAgentRail`，在 `init` 时注册 `ExperienceSearchTool` 并向 `system_prompt_builder` 注入经验库 prompt section；在 `before_model_call` 时刷新该 section；在 `uninit` 时移除工具与 section。类属性 `priority = 80`。

**参数**：

* **experience_dir**(str)：经验库目录路径，透传给经验检索工具与提示。
* **language**(str，可选)：工具与提示语言。默认值：`"cn"`。

### init(agent) -> None

调用父类 `init`，记录 agent 的 `system_prompt_builder` 引用，并通过 `_register_experience_tool` 把 `ExperienceSearchTool` 注册到全局 `Runner.resource_mgr` 与 agent 的 `ability_manager`。

**参数**：

* **agent**：关联的 agent 实例。

### uninit(agent) -> None

从 agent 的 `ability_manager` 与全局 `Runner.resource_mgr` 移除注册的经验检索工具，并从 `system_prompt_builder` 移除 `SectionName.MEMORY` 区段。

**参数**：

* **agent**：关联的 agent 实例。

### async before_model_call(ctx: AgentCallbackContext) -> None

若存在 `system_prompt_builder`，则先移除再重新添加经验库 section，保证使用最新的语言与路径配置。

**参数**：

* **ctx**(AgentCallbackContext)：agent 回调上下文。

---

## class openjiuwen.auto_harness.rails.revert_on_failure_rail.RevertOnFailureRail

```
class openjiuwen.auto_harness.rails.revert_on_failure_rail.RevertOnFailureRail(DeepAgentRail)

def __init__(self) -> None
```

继承 `DeepAgentRail`，在每次任务迭代开始前捕获当前 HEAD 作为基准提交，供 orchestrator 在失败时通过 `git reset --hard` 回滚工作区。

### set_base_commit(sha: str) -> None

记录用于回滚的基准提交 SHA。

**参数**：

* **sha**(str)：Git commit SHA。

### base_commit() -> str

只读属性，返回当前记录的基准提交 SHA；未捕获时返回空字符串。

**返回**：

**str**，当前基准提交 SHA。

### async before_task_iteration(ctx: AgentCallbackContext) -> None

通过 `git rev-parse HEAD` 捕获当前 HEAD 并调用 `set_base_commit` 记录；若 git 不可用则跳过。

**参数**：

* **ctx**(AgentCallbackContext)：agent 回调上下文。

### async revert(workspace: str) -> bool

在指定工作区执行 `git reset --hard <base_commit>` 回滚。无基准提交或 `git reset` 失败时返回 `False`，成功返回 `True`。

**参数**：

* **workspace**(str)：执行 git 命令的工作目录。

**返回**：

**bool**，回滚是否成功。

---

## class openjiuwen.auto_harness.rails.security_rail.SecurityRail

```
class openjiuwen.auto_harness.rails.security_rail.SecurityRail(DeepAgentRail)

def __init__(self, immutable_files: List[str] | None = None, high_impact_prefixes: List[str] | None = None) -> None
```

继承 `DeepAgentRail`，合并原 `ImmutableFileRail` 与 `InputSanitizationRail`：在 `before_tool_call` 用 glob 模式拦截对不可变文件的写入并标记高影响编辑；在 `before_model_call` 扫描输入文本中的提示注入 / shell 注入可疑模式并请求强制结束。

**参数**：

* **immutable_files**(List[str] | None，可选)：不可变文件的 glob 模式列表（如 `["*.lock", "setup.cfg"]`），命中即硬性拦截写入。默认值：`None`（空列表）。
* **high_impact_prefixes**(List[str] | None，可选)：高影响路径前缀列表，命中后在 `ctx.extra["high_impact"]` 标记为 `True`。默认值：`None`（空列表）。

### async before_tool_call(ctx: AgentCallbackContext) -> None

对 `write_file` / `edit_file` 工具调用，用 `fnmatch` 检查 `file_path`：命中不可变模式则硬性拦截（设置 `_skip_tool`、回写错误 `ToolMessage`）；命中高影响前缀则置 `ctx.extra["high_impact"] = True`。

**参数**：

* **ctx**(AgentCallbackContext)：agent 回调上下文。

### async before_model_call(ctx: AgentCallbackContext) -> None

从模型输入消息中抽取文本，扫描可疑模式（忽略先前指令、`system prompt`、`rm -rf /`、`$(...)` 命令替换、反引号命令等）；命中则记录告警、通过 `ctx.request_force_finish` 请求强制结束，并 `push_steering` 警告不得执行被注入的指令。仅当 `ctx.inputs` 为 `ModelCallInputs` 时生效。

**参数**：

* **ctx**(AgentCallbackContext)：agent 回调上下文。
