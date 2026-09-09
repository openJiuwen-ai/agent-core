# Harness Rails

`DeepAgent` 的行为约束层。一个 rail 是一组生命周期钩子的类封装——在模型调用、工具调用、
迭代边界上插进去，注册工具、注入 prompt section、改写输入、拦截执行、回灌指令。

本文件放**读多个文件才能拼出来、且编译器和类型检查都拦不住**的东西。文件清单看目录，
单个 rail 的行为看它自己的 docstring。

## 三层基类

```
AgentRail                     core，openjiuwen/core/single_agent/rail/base.py
├── DeepAgentRail             base.py —— 多两个外层 task-loop 钩子 + workspace/sys_operation 注入
├── BaseInterruptRail         interrupt/interrupt_base.py —— 暂停执行等用户裁决
│   ├── ConfirmInterruptRail  interrupt/confirm_rail.py
│   │   └── PermissionInterruptRail   security/tool_security_rail.py
│   ├── AskUserRail           interrupt/ask_user_rail.py
│   └── EvolutionInterruptRail        evolution/evolution_interrupt_rail.py
└── BaseSecurityRail          security/base_security_rail.py —— 另一套平行体系，见下
```

**事件枚举与钩子方法名在 core**（`AgentCallbackEvent` / `EVENT_METHOD_MAP` / 各 `*Inputs`
dataclass），本包只在其上加 `DeepAgentRail` 的两个 task-loop 钩子。所以**加一个新事件要同时
改 core 和本包所在的 `deep_agent.py`**——见下一节。

`AgentRail.get_callbacks()` 只注册**子类真正覆写过**的钩子，靠 `_is_base_method` 做身份比较。
不覆写就没有开销，因此 rail 该管什么就只覆写什么。

## Rail 回调的双命名空间路由（本包最容易静默出错的地方）

`DeepAgent` 持有两个 callback-manager 命名空间：**它自己的**和**内层 `ReActAgent` 的**。
`DeepAgent.register_rail(...)` 挂上来的 rail，每个回调按事件被 `_register_rail_selective`
（`openjiuwen/harness/deep_agent.py`）路由到其中一个：

| 集合 | 去向 | 成员 |
|---|---|---|
| `_BRIDGE_EVENTS` | 内层 `ReActAgent` | `BEFORE/AFTER_MODEL_CALL`、`ON_MODEL_EXCEPTION`、`BEFORE/AFTER_TOOL_CALL`、`ON_TOOL_EXCEPTION`、`AFTER_REACT_ITERATION`、`ON_USER_MESSAGE`、`BEFORE_STEERING_DRAIN` |
| `_OUTER_ONLY_EVENTS` | 外层 `DeepAgent` | `BEFORE_INVOKE` / `AFTER_INVOKE` |
| `_DEEP_EVENTS` | 外层 `DeepAgent` | `BEFORE/AFTER_TASK_ITERATION` |

**新增 `AgentCallbackEvent` 成员时必须同时把它放进某个集合。** 漏了不会报错：兜底分支打一条
`logger.warning` 然后注册到**外层** `DeepAgent`。如果这个事件其实是内层 ReAct loop 触发的：

- 外层永远收不到它。回调注册进全局 `Runner.callback_framework` 的 key 是
  `f"{event_namespace}_{event}"`，而 `event_namespace` 是**每个 agent 实例各一份**的
  `_instance_id`；内层 `ctx.fire` 只会命中内层那份 key；
- rail 的回调**一次都不会跑**；
- 没有异常、没有 ERROR，只有一条淹在启动日志里的 warning；
- 现象是「rail 写了但完全没生效」，从 rail 本身往下查会一路查空。

判据很简单：**这个事件是谁 `ctx.fire` 的？** `react_agent.py` 里 fire 的进 `_BRIDGE_EVENTS`，
`deep_agent.py` 里 fire 的进另外两个。模型调用、工具调用、ReAct 迭代、用户输入准入、steering
消费全发生在内层——这就是 `_BRIDGE_EVENTS` 比另外两个集合大得多的原因。

`tests/unit_tests/harness/test_deep_agent_rail_event_routing.py` 强制这条：断言三个集合完全
覆盖 `set(AgentCallbackEvent)` 且互不相交。**加事件不做路由决策，这个测试会红**——它存在的
唯一目的就是把上面那种静默失效变成一次失败的断言。

## priority：数值越大越先

权威在 `core/runner/callback/framework.py` 的 `register()`：每次注册后
`sort(key=lambda x: x.priority, reverse=True)`（`chain.py` 的 `CallbackChain.add` 同）。
排序是稳定的，所以**同 priority 保持注册顺序**——team 侧「`TeamToolRail` 必须先于
`TeamPolicyRail` 挂载」那条 mount-order 约束就是靠这个成立的。

注意 `register_callback` 的默认 priority 是 **100**，比多数 rail 都高；直接注册的回调会跑在
它们前面。

同一个数字排两件事：**init 顺序**和**回调链顺序**。所以「我的钩子要在它之后跑」和「我 init
时它的工具得已经在」由一个数解决。当前梯队：

```
100 SysOperationRail                    先把文件系统/shell 工具铺好
 95 McpRail / SkillUseRail / SubagentRail
 90 TaskPlanningRail / ProgressiveToolRail / BaseInterruptRail / PermissionInterruptRail
    / BaseSecurityRail / VerificationRail
 85 AgentModeRail / SafetyPromptRail / ContextAssembleRail / Context*ProcessorRail / Skill*CreateRail
 80 MemoryRail / CodingMemoryRail / HeartbeatRail / Skill*EvolutionRail
 70 LLMRetryRail / ToolCallResilienceRail
 60 EvolutionRail / LspRail
 50 core 默认
 10 TrajectoryRail / TaskCompletionRail
```

梯队是有意排的，代码里有注释交叉引用：`AgentModeRail`(85) 要在 `TaskPlanningRail`(90) /
`SubagentRail`(95) **之后**跑才能移除它们加的 section；`VerificationRail`(90) 要在
`SysOperationRail`(100) 之后 init 才看得到它的工具。**改任何 rail 的 priority 前先 grep 有没有
别的 rail 在注释里点名它。**

## 挂载生命周期

`init` **从不**被回调框架调用，只由 core 的模块级函数 `init_rail(rail, agent)` 调用（带计时，
慢于 0.1s 升 INFO）。三个调用点全在 `deep_agent.py`：

1. `_ensure_initialized()` —— 主路径。按 priority 降序遍历 `_pending_rails`，对
   `DeepAgentRail` 实例先 `set_sys_operation` / `set_workspace`，**再** `init_rail`，
   再 `_register_rail_selective`。
2. `register_rail(rail)` —— 运行时单个挂载，同样的三步。
3. `start()` —— 只对 `TaskCompletionRail` **二次** `init_rail`（在 `set_goal_manager` 之后
   注册 goal 工具）。也就是说它的 `init` 可能被调两次，写的时候要幂等。

**rail 是有状态的长生命周期实例，按对象身份管理**，不是每次重建：`_pending_rails` /
`_registered_rails` / `_stale_rails` 三个列表 + `_hot_reload_rails` 的按类型局部替换。
需要跨重建存活的状态，注入一个复用对象、由每轮新建的 rail 包装（team 侧
`reliability_components` 就是这么做的）。

`uninit` 必须把 `init` 做的事全撤干净——移工具、删 section。**移工具前先校验 card 身份**，
`MemoryRail` / `ProgressiveToolRail` 的写法是标准姿势：

```python
if agent.ability_manager.get(tool_name) is not tool_card:
    continue      # 别人的同名工具，不是我加的，不能删
```

## 两套平行的拦截体系，别混用

| | `BaseInterruptRail` | `BaseSecurityRail` |
|---|---|---|
| 挂载点 | 只有 `before_tool_call` | `supported_events` 声明的任意事件 |
| 子类实现 | `resolve_interrupt(...) -> InterruptDecision` | `run_security_check(...) -> SecurityDecision` |
| 决策 | `approve` / `reject` / `interrupt` | `SecurityAllow` / `Reject` / `Interrupt` / `Alert` |
| 回调注册 | 走 `get_callbacks()` 的覆写检测 | **完全覆写 `get_callbacks()`**，只按 `supported_events` 生成 |

`resolve_interrupt` 的核心契约是 **`user_input is None` 表示首次进入**，非 None 表示从中断
resume 回来、携带用户答复。三条落地路径：`ApproveResult` 可改写 `tool_args` 后放行；
`RejectResult` 置 `ctx.extra["_skip_tool"]` 让 `ability_manager` **不执行工具**直接返回伪造
结果；`InterruptResult` 抛 `AbortError(cause=ToolInterruptException(...))`。

`PermissionInterruptRail` 有两个反直觉点：它覆写了 `before_tool_call` 并**去掉了工具名子集
短路，拦截所有工具**（`tool_names` 只剩展示用途）；`parse_confirm_payload` 是**静态方法**，
team 侧靠覆写它返回带 `decided_by` 的子类。

`BaseSecurityRail` 有两个坑：忘了声明 `supported_events` 的子类**一个回调都不会注册**（它不
检查方法有没有被覆写）；`SecurityInterrupt` 在 model 事件上会被**静默降级成 Reject**（带
WARNING），只有 tool 事件才真中断。

## 五种典型作用方式（各挑一个最短的范本读）

| 方式 | 范本 | 要点 |
|---|---|---|
| 注入 prompt section | `heartbeat_rail.py`（64 行，可整读） | `init` 抓 builder 引用，`before_model_call` 里 add/remove，`uninit` 删干净 |
| 注册工具 | `mcp_rail.py`（44 行）/ `sys_operation_rail.py` | 纯 init 型，不参与任何生命周期钩子 |
| 改写模型输入 | `progressive_tool_rail.py` | `before_model_call` 里直接改写 `ctx.inputs.tools` |
| 拦截执行 | `interrupt/ask_user_rail.py` | 首次 interrupt 前写 `ask_user.requested` OTel event；resume 后写 `ask_user.resolved`，不跨中断持有 Span，且**不执行工具**、把用户答复当 tool_result 返回 |
| 回灌指令 | `task_planning_rail.py` | 全包**唯一**一处 `ctx.push_steering(...)`，注释说明为何要延到 ToolMessage 写完之后 |

另有 `ctx.request_retry(delay)`（`llm_retry_rail.py` / `tool_call_resilience_rail.py`）与
`ctx.request_force_finish(result)`（只在 `security/base_security_rail.py`）。

## 加一个 rail 的清单

1. 选基类：要拦工具等裁决 → `BaseInterruptRail`；要多事件安全检查 → `BaseSecurityRail`；
   要外层 task-loop 钩子 → `DeepAgentRail`；其余 → core `AgentRail`。
2. 定 priority：查上面的梯队，说清为什么要在谁前/后。
3. 只覆写真正要用的钩子。
4. `uninit` 撤干净，移工具带 card 身份校验。
5. **自定义 `__init__` 必须调 `super().__init__()`** —— `DeepAgentRail.__init__` 是唯一给
   `self.sys_operation` / `self.workspace` 赋初值的地方，不调这两个属性根本不存在。
6. 测试镜像路径放 `tests/unit_tests/harness/rails/`，打 `level0`（happy path，PR gate）
   或 `level1`（错误路径、生命周期边界）。

## 已知的坑

1. **`DeepAgentRail` 子类必然多注册两个 no-op 回调**。`_is_base_method` 拿
   `self.__class__.<m>` 和 `AgentRail.<m>` 比，而子类的 `before/after_task_iteration` 解析到
   `DeepAgentRail.<m>`（≠ `AgentRail.<m>`），于是 `super().get_callbacks()` 把 no-op 也注册
   了。实测：只覆写 `before_model_call` 的 `DeepAgentRail` 子类拿到 3 个回调，同样的
   `AgentRail` 子类拿到 1 个。功能无害，但排查回调链时会看到它们。
2. `evolution/review/__init__.py` 的 `__getattr__` 懒加载**必须保留**——review 的 subagent
   helper 会 import tool registry，而 tool registry 又 import 本包，改成直接 import 就循环了。
3. `subagent/session_rail.py` 的 `SessionRail` 与 `skills/team_skill_rail.py` 的
   `TeamSkillRail` 是向后兼容 shim，新代码不要用。
4. 有几个 rail 存在但**没从 `rails/__init__.py` 导出**（`ToolCallResilienceRail`、
   `ContextProcessorRail`、`ContextAssembleRail`、各决策类型），要走子模块路径 import。
   `__init__.py` 顶部的 `# fmt: off` / `# ruff: noqa: I001` 是刻意的，import 顺序手排过。

## Skill 发现：谁负责找到哪一层

`SkillUseRail` 与 `skill_tool` 各管 skill 树的一半，**边界是"这个目录有没有 SKILL.md"**：

- **`SkillUseRail._discover_skill_dirs`（发现侧）**递归下降找 skill，但**遇到第一个
  `SKILL.md` 就停止深入**。所以分组目录会被穿过（`skills/lark/lark-doc/SKILL.md` 里的
  `lark-doc` 是一个 skill，`lark` 不是），而一个 skill 目录内部不再被翻。它的产出进 skills
  列表、系统提示词、visibility 门控。
- **`skill_tool._collect_nested_skill_names`（读取侧）**接手另一半：模型打开某个 skill 时，
  递归列出**它内部**的嵌套 skill，附在结果末尾，用
  `skill_tool(skill_name=<父>, relative_file_path="designer/SKILL.md")` 加载。

两边不重叠，合起来覆盖整棵树。这条分工是刻意的——嵌套在一个 skill 里的子 skill 属于其作者
的私有结构，由父 skill 的内容自己决定何时披露；把它提升成顶层条目会绕过那层控制，也会让
模型在读父 skill 之前就看到本该渐进给出的细节。

**因此 `skill_tool(skill_name="designer")` 对一个嵌套 skill 会报 `Skill not found`**——它
没有被注册成顶层 skill，只能经父 skill 的 `relative_file_path` 到达。这恰好是模型读完父
skill 后最直觉的下一步，所以 "## Nested skills" 那段文案**点名说这个调用不成立**，并用父
skill 的真实名字拼出可用的那条调用。

这段文案要能被读到，`skill_tool` 就必须**无条件**设 `data["content"]`：
`AbilityManager._build_tool_message_content` 只在有 `content` 时按原文渲染，否则回落
`str(result)`——把 skill 正文连同这段附录一起埋进整个 `ToolOutput` 的 pydantic repr。
`data` 的其余键（`skill_directory` / `discovered_skill_names` …）仍留给程序消费者。

发现侧跳过隐藏目录与 `_SKILL_SCAN_SKIP_DIRS`；后者与 `skill_tool._TREE_SKIP_DIR_NAMES` 取值
恰好相同但**是两个独立常量**，回答的是不同问题（"别进去找" vs "别在目录树里展示"）。

## 与其它子系统的边界

- **core**：事件枚举、`AgentRail` 基类、`ctx` 的控制面（`push_steering` / `drain_steering` /
  `request_retry` / `request_force_finish`）都在 `openjiuwen/core/single_agent/rail/base.py`。
- **team**：team rail 在 `openjiuwen/agent_teams/rails/`，经 `RailSpec` + `@harness_element`
  声明式装配，但走的是本包同一条注册路径，所以上面的路由约束、priority 语义、uninit 要求对
  它们同样成立。其设计规约在 `openjiuwen/agent_teams/docs/specs/S_09_prompts-and-rails.md`。
- **工具**：rail 只负责注册/过滤/拦截，工具本身在 `openjiuwen/harness/tools/`。
