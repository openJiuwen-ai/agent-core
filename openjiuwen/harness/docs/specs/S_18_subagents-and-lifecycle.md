# S_18 SubAgent 预设与生命周期

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/subagents/`（8 文件）、`openjiuwen/harness/subagent_lifecycle.py`、`openjiuwen/harness/manifest/harness_elements.py`（subagent 构建器） |
| 最近一次修订日期 | 2026-10-08 |
| 关联 feature | F_05_browser-task-integrity、F_07_browser-jev-policy、F_08_browser-jev-handover、F_09_browser-runtime-phase-contract、F_10_browser-runtime-simplification、F_11_browser-p0-closure、F_12_browser-jev-shared-observation、F_13_browser-local-decision-loop、F_14_browser-model-usage-summary |

## 范围 / 边界

Browser 的完整工具、Rails、运行时与宿主接入统一见
[浏览器子智能体](../../../../docs/zh/2.开发指南/API文档/openjiuwen.harness/subagents/浏览器子智能体.md)。
本 spec 保留生命周期不变量；统一说明包含本地未提交实现，不表示已发布版本全部支持。

本规约定义 harness 的**预设子代理**及其生命周期辅助：五类预置 agent 的配置构建、
创建、任务资源生命周期。`S_10` 是异步运行时（状态机 / 容量 / snapshot），本 spec 是
预设的**声明 + 创建 + 同步生命周期**。

具体覆盖：

- `subagents/browser_agent.py`：`build_browser_agent_config` / `create_browser_agent`。
- `subagents/code_agent.py`：`build_code_agent_config` / `create_code_agent`。
- `subagents/research_agent.py`：`build_research_agent_config` / `create_research_agent`。
- `subagents/verification_agent.py`：`build_verification_agent_config` /
  `create_verification_agent`。
- `subagents/mobile_gui_agent.py`：`build_mobile_gui_agent_config` /
  `create_mobile_gui_agent`。
- `subagents/explore_agent.py` / `plan_agent.py`：explore / plan 预设（`S_12` 的
  `build_explore_subagent` / `build_plan_subagent` 消费）。
- `subagent_lifecycle.py`：`prepare_subagent_task_resources` / `cleanup_subagent_task_resources`。
- `subagents/__init__.py` 导出面（`__all__` 10 符号）。

不在本规约范围内：
- 异步运行时（spawn/wait/status/snapshot）—— `S_10`。
- 子代理工具面（`SubagentSpawnTool` 等）—— `S_05`。
- manifest catalog 注册（`S_12`）与 rail 挂载（`S_04`）。

## 不变量

1. **五类预设 + 两个辅助**：预设 agent 共 5 类——browser / code / research /
   verification / mobile_gui；辅助 preset 由 manifest 的 `build_explore_subagent` /
   `build_plan_subagent` / `build_general_purpose_subagent` 提供。每类有
   `build_*_agent_config`（纯配置）+ `create_*_agent`（实例化）成对 API。
2. **`subagents/__init__.py` 的 `__all__` 是公开面**（10 个 build/create 函数）；
   辅助 preset 不在 `__all__`（走 `manifest` catal登记，`S_12`）。
3. **配置构建是纯函数**：`build_*_agent_config` 不含副作用、只读输入
   （`model` / `subagents` / `workspace` 等），产出 `SubAgentConfig`（`S_01` 配置形态）；
   `create_*_agent` 包装 `SubAgentConfig` → `DeepAgent`。
4. **任务资源生命周期辅助唯一**：`prepare_subagent_task_resources(subagent)` /
   `cleanup_subagent_task_resources(subagent)`（`subagent_lifecycle.py`）——`getattr`
   探测可选钩子（`prepare_task_resources` / `cleanup_task_resources`），awaitable 则
   await；cleanup **绝不允许遮蔽子代理结果**（异常仅 `logger.exception`）。
5. **code agent 装配约束**：`build_code_agent_config` 自带内置 plan agent 注入
   （`_inject_builtin_plan_agents`，`_has_agent` 防重复）+ 必带 rail 合并
   （`_merge_rails_with_required`）+ coding memory 目录解析（`_resolve_coding_memory_dir`）；
   这些是 code preset 的硬语义，新增 preset 不得绕过。
6. **browser agent 约束**：`build_browser_agent_config` 经
   `_resolve_runtime_settings` 解析浏览器运行时；`_browser_model_with_temperature(model, temp)`
   设置浏览器模型温度；`_coerce_browser_instance` 归一实例形态。
7. **同步创建路径**：`create_*_agent(config, ...)` 是同步（复用 `create_deep_agent` 的
   同步构造，`S_01` 不变量 3）；`enable_subagent_runtime` 时才进 `S_10` 的异步控制面。
8. **manifest 侧预设与 `subagents/` 预设同源**：`S_12` 的 `build_*_subagent` 是
   `subagents/` 预设的 catalog 注册形态；二者共享 `SubAgentSpec` 装配语义，不新造预设。
9. **browser 上下文权威边界**：runtime 负责执行真值、来源归属、明确 blocker 与统一结果传输；
   模型按原始目标和本任务观察判断业务完成。默认不推断业务字段/数量/比较槽，也不运行对应认证投影。
   外部显式字段契约仅校验声明的输出结构；模型设置的 phase 条件不拥有业务完成否决权（F_19）。
   模型可见 PageState 与 WorkingContext 必须先按结构投影后序列化，保持合法 JSON。
   原文未映射、当前仍是搜索 URL 等语义推断不改写完成状态；明确终态仍由 runtime 统一传输。PageState 的
   `page_blockers` 仅表示页面启发式信号，不能直接覆盖 runtime 的权威任务终态。
10. **browser 观察采用统一窗口**：Probe、snapshot、find、evaluate 先由 runtime 提取证据并将
    当前结果投影到既有约 12K 字符预算，再交给 `ToolResultWindowProcessor` 的配置窗口，
    并发只读结果的合并结构由 PageState 提供。WorkingContext 默认只投影 runtime 权威状态，
    不再要求模型维护第二份记忆。原生 AX 先解包装再注册 refs，不被不完整 Probe 替换；工具消息
    只携带新数据和 PageState 摘要，不重复附加旧 Cards。确认排序/筛选变化时废弃旧列表目标，
    不因每次只读观察递增 generation。
    有损投影前保存任务内可恢复原文，handle 不跨 session；临时观察文件有效期 24 小时，
    写入时惰性清理过期文件，跨任务扫描每工作区最多每小时一次；每任务上限 128 个 / 64 MiB。
    停止运行时不保证物理文件即时删除。存储失败时保留原文。
    永久 raw audit 仍需显式开启，临时 recall 不是全局文件系统能力。
11. **共享浏览器按任务占用**：同进程同 `browser_key`（未配置时按 MCP server id）的任务
    通过现有 BrowserService registry 排队，覆盖初始化至 cleanup，不只锁单次工具调用。
    不同 key 保持独立；不关闭共享 Chrome、不清除 Cookie。跨进程/多个 key 指向同一 CDP
    不在此保证范围内，需宿主显式配置统一身份。
12. **取消必须停止执行者**：ReAct stream 消费者取消或 `aclose` 时取消并等待 producer；
    TaskTool 和 callback/session 包装器关闭内层生成器。清理任务 observer 后才释放占用。
    宿主可在 `run.context.extra.execution_deadline_at` 传入 epoch 秒；TaskTool 将其传给
    Browser query 并限制调用剩余时长。未设置时保持原有任务预算。
    Browser TaskTool 持有显式 child Session，使超时可返回既有证据的 partial；取消保存已读结果
    后继续传播 CancelledError，不能继续网页动作。非 Browser 子代理仍沿用原有派发行为。
13. **观察属于任务和来源**：沿用现有观察记录，保留 query、来源 URL、generation 和原文。
    初始复用页面不自动证明新任务已完成；同实体同口径纠正旧值，不同商品不能拼接字段，
    不同日期/报价口径保留区分。resume 清除旧的模型摘要，最终使用本次有效结果。
    外部显式输出契约可使用既有 slot 投影；不会根据当前 URL 或字段解释自动撤销 completed 并触发续跑。
    父层发现明确遗漏时，可显式携带原 resume_task_id 纠正已完成的 worker 判断；不自动触发。
    定向恢复仍受原 query deadline 和既有 resume 配额约束，不创建新验证器或重置任务期限。
14. **需求不由页面按钮扩张**：原始用户目标通过既有 TaskTool run context 传递，默认不推断字段。
    导航首页不要求商品字段，搜索卡标题不证明已进入详情页，页面按钮枚举不创建比较槽。
    未执行的 DSML 工具意图不能认证完成，同 run 至多纠正一次。定向恢复保留原始约束和修复指令，
    不重置共享期限，也不因推断字段适配不完整而强迫普通信息任务重复读取。
    “地点不限”“评分若有”等局部否定/可选条件不生成硬性字段；星级与住客评分分开。
    replan_required 是执行指导，收尾时不能单独制造网页 blocker。原生读取、Card 和 evaluate
    的带来源观察采用同一完成判断，保留 typed evidence 与未结构化观察的区别，不虚构字段覆盖。
    默认不输出 `unverified_fields` 检查表。确有未完成用户目标时，可在收尾一次使用
    现有 browser_progress 文本标注 partial/next_action，不要求逐轮进度或另一个验证模型。
    支付/登录/验证码等页面情况由来源观察和模型判断传递，不靠通用正则推断生成全局阻断。
    取消、权限、截止期限和已有明确执行终态仍保持不变。

15. **Browser 决策与执行分离**：RuntimeSettings.decision 默认 llm；shadow 后台只记录建议，
    hybrid 使用 BrowserPolicyModel 将 Jev 的有界选择编译为标准工具调用。上下文处理器
    传递运行时同次 DOM 观察的任务内引用，禁止反向解析提示词或共享全局最新页面。
    权限流程完成后由工具派发入口重查节点并消费单次决策，禁止刷新旧 Jev 目标继续执行。节点检查、权限、
    预算、执行结果和完成协议仍属于现有 runtime。Jev 错误、无效/低可信决策、未覆盖操作
    和收尾请求回到同一任务的原模型；取消继续传播，不重置 deadline，不重放副作用。
    模型热更新重新绑定显式模型实例，Browser 策略保留包装并替换底层 LLM。
    Jev transport 显式区分 TypeSafe native 和 OpenRouter Decisions；使用各自模型名、端点与
    密钥环境变量，OpenRouter 的同版本日期快照视为有效响应，不经过 chat completions。
    两种 provider 统一复用 core 的 JevSystemOneClient 完成 HTTP 与严格类型解析；浏览器适配层
    负责同一总预算内的有限重试、模型校验和安全诊断，关闭底层默认重试以避免叠加。布尔值或
    字符串不得被强制转换为有效置信度；浏览器侧仍校验候选集合、概率分布和置信度阈值。

16. **Browser 模型用量独立于决策采纳**：每次 Browser invocation 的 `task_end.model_usage`
    分列 `llm`、`jev` 与 `total` 的客户端调用次数、输入/输出/总 token 和模型调用耗时。
    Jev 拒绝、HANDOFF、shadow 与随后 LLM 兜底分别计入；准入前跳过不算调用。
    内部 HTTP 重试合并为一次客户端调用，耗时含重试，token 只累计供应商返回的已知用量。
    缺失 usage、失败、取消和仍在执行的 shadow 请求显式标记；未知消耗不能被解释为零。
    流式响应只累计最终累计 usage 一次，不按 chunk 加总。`task_end.elapsed_ms` 保持 invocation
    墙钟耗时，模型耗时合计不含工具并可能含 shadow 重叠。续跑每次单独统计，不能把每段当作任务累计值。
    计量只读写独立 session 统计，不参与策略、预算、工具派发或完成判断。

## Browser 运行时减法（F_17，当前契约）

阶段计数、自然语言验收和可选条件只提供诊断，不阻断一般动作。删除阶段次数上限、
三次核验读取配额、重规划试探/拒绝终止和价格区间特判。总截止时间、迭代数、单次等待、
权限、串行派发、精确目标校验及真实未知效果保护保持有效。

cart_delta 仅是可选工具读数，不再要求动作先建立基线，不因成功点击强制进入核验协议，
普通观察不自动附加购物车 RPC。当前意图替换旧条件；journal 未知效果不会随之消失。
authoritative_browser_result 保留兼容名，但权威范围限于执行/观察；业务完成由 LLM
结合原始目标与本任务观察判断。acceptance、missing_conditions 和自动字段缺口是提示，
不能推导“任务已认证”或“页面被锁住”。工具日志证明动作未发送时记录未派发；真实未知
效果仍禁止盲重放。该行为同时适用于 llm/hybrid，不再由 strict/lean 切换执行真值。

此前 F_10/F_12/F_13/F_16 中与本节冲突的预算、自动证明和试验开关约定由本节取代。
详见 [F_17](../features/F_17_browser-runtime-reduction.md)。

## 接口契约

```python
def build_browser_agent_config(...) -> SubAgentConfig
def create_browser_agent(...) -> DeepAgent
def build_code_agent_config(...) -> SubAgentConfig
def create_code_agent(...) -> DeepAgent
def build_research_agent_config(...) -> SubAgentConfig
def create_research_agent(...) -> DeepAgent
def build_verification_agent_config(...) -> SubAgentConfig
def create_verification_agent(...) -> DeepAgent
def build_mobile_gui_agent_config(...) -> SubAgentConfig
def create_mobile_gui_agent(...) -> DeepAgent

# subagent_lifecycle.py
async def prepare_subagent_task_resources(subagent: Any) -> None
async def cleanup_subagent_task_resources(subagent: Any) -> None
```

错误 / 返回语义：

- 预设备缺失必填输入（如 code agent 的 workspace）→ 抛 `ValueError` 族（构建期）。
- 同名子代理重复注入 → `_has_agent` 去重（不重复添加内置 plan agent）。
- `cleanup_subagent_task_resources` 内部异常 → `logger.exception` 后继续（不遮蔽结果）。

## 数据结构

### 预设一览

| preset | 构建 | 创建 | 关键装配 |
|---|---|---|---|
| browser | `build_browser_agent_config` | `create_browser_agent` | 浏览器 runtime settings、模型温度 |
| code | `build_code_agent_config` | `create_code_agent` | 内置 plan agent 注入、必带 rails、coding memory 目录 |
| research | `build_research_agent_config` | `create_research_agent` | 研究工具组 |
| verification | `build_verification_agent_config` | `create_verification_agent` | 验证 rail / 工具 |
| mobile_gui | `build_mobile_gui_agent_config` | `create_mobile_gui_agent` | GUI 操作工具组 |
| explore / plan / general_purpose | manifest `build_*_subagent` | （经 catalog） | 探索 / 计划 / 通用 |

### 生命周期钩子

| 钩子 | 时机 | 失败语义 |
|---|---|---|
| `prepare_task_resources` | 子代理 invoke 前 | 失败向上（invoke 中断） |
| `cleanup_task_resources` | 子代理结束后 | 仅记日志，不遮蔽结果 |

## 与其它 spec 的关系

- 异步执行进 `S_10` 控制面；spawn 类型名（`"browser_agent"` / `"verification_agent"`）
  与 `S_16` 的 sticky 白名单一致。
- 预设经 `manifest` catalog 注册 —— `S_12`；`SubAgentConfig` / `DeepAgentSpec.subagents`
  字段 —— `S_01`（`SubAgentSpec` 解析成 `SubAgentConfig`，装配见 `S_13`）。
- `SubagentRail` 挂载 / `create_subagent` 装配 —— `S_04` / `S_02`。
- `create_*_agent` 复用 `create_deep_agent` 构造流 —— `S_01`。

## Jev 分段交接补充（F_08，2026-09-23）

同一任务的评估总预算、回退作用域、可执行状态指纹和执行记录保存在 session phase state；
focused resume / 模型重建不重置预算或 deadline。软回退只在意图或可执行状态实质变化后
重新准入，时间、capture_id、target_id 更新本身不构成恢复条件。FINISH 请求验证当前阶段，
由 LLM 收尾或补齐缺项；新阶段版本可重新准入。任何失败（含认证、计费、配置与协议错误）只回退当前可执行状态段，状态变化后重新询问 Jev（F_15）。
browser_page_action 是参数封闭的运行时辅助工具，提供显式 URL 导航、返回、有界滚动和
固定文本读取、查找、快照、标签页查看/选择、精确目标 hover、短等待（F_13）；
与 Batch 一样先经过权限钩子，再检查实际参数、任务、页面与 DOM document，且验证底层
capability。工具成功回执与执行后观察分开记录；不能自动重放结果不明的操作。

## 轻量意图与共享执行事实（F_09 / F_10，2026-09-24）

`browser_phase` 是可选兼容入口，设置当前 objective 即可；allowed_operations、已观察的
字段绑定和条件均为可选。不按“然后/再”等措辞强制阶段协议。多个搜索词/业务字段或多来源任务缺少明确
局部意图时交回 LLM；不重新启用逐轮模型 WorkingMemory。临时节点/URL 条件随意图替换，
既有用户要求、持久证据/购物车要求、未知业务执行记录不能被阶段更新删除。

普通观察 lifecycle 统一更新条件与执行事实，llm / hybrid 使用相同规则。Jev publisher
只投影，不认证。管理调用不消耗动作预算、不使页面失效；核对读取保留有界恢复通道，
管理调用本身不重置 deadline、任务总预算或 replan 计数。真实观察进展重置连续失败的
replan 计数，重复捕获不算进展。显式 verify 只在需要新读数时使用。
相同意图设置幂等；可选填写条件满足后仍可提交搜索，文档变化使临时节点绑定过期而非认证成功。
自动业务核对仅读取关联的待核对条件，每轮总计最多 5 秒，并记录专门的耗时事件。

journal 是派发/逐步骤结果的事实来源；部分 Batch 保留成功、未派发、未知、尚未开始。
已知未找到 Ref 不记为未知写入。已知局部 UI 修改与账号/业务或未知脚本影响分开处理；
后两类结果不明时保留相关修改限制，读取可用。普通页面变化不认证未知业务成功。
policy receipts 与 recent_actions 为有限投影，终态及父代理交接带共享执行摘要和待核对项。

购物车读取/确定性比较归有界 cart_verification 适配器；动作前必须有适用基线和对象身份。
普通搜索填写不关闭基线采集，可能的购物车写入/未知脚本则关闭。空购物车需要正向完整性
证明；按目标 SKU 或整车保留范围核对，已要求整车保留不能降级。没有基线、身份、完整
读数时返回 partial。是否使用 Jev 不改变业务完成标准。详见 F_10 的边界与验证记录。

## P0 输入、验收与交接闭环（F_11，2026-09-24）

阶段使用分类 schema；绑定投影与验证共用新鲜唯一节点，错误明确缺字段、缺身份或候选不唯一。
临时条件替换保留有界审计；显式输出契约的提取保留排序口径和商品/店铺评分的来源区分。
弱 URL 条件不能认证业务目标，工具参数不能自证排序 variant；默认任务不再推断业务字段。
Jev 回退将当前意图、缺项、执行事实和允许的恢复方式送入本次 LLM 输入；修正后沿既有指纹重新准入。

业务反馈（例如 Added）属于已观察局部事实，不能认证 SKU/数量差。购物车基线保持原始身份，
已观察能力、原生工具与 Batch 的恢复使用同一适配器。未知修改仍先核对、后续做未完成部分。
正常终止和模型失败均携带执行回执、来源观察、worker 判断与剩余期限；已知运行时阻断和模型报告
分别传递，阻断列表不是页面障碍的完备枚举，父代理不得把未观察的未来步骤表述为实际阻断。
浏览器模型使用独立的 client 配置，
外层等待总计最多 60 秒，首帧后的流空闲最多 15 秒，保留更短 client 配置，且受任务
剩余时间和交接保留时间约束；不修改父模型配置。超时关闭流，不重放部分动作。

动作额度耗尽后保留三次有界核对读取，禁止新写入；阶段 verify 和购物车读取纳入未知影响
恢复通道的计数；普通 probe 不占用显式核验额度。计数绑定各个未解决效果，metadata set 不能重置额度。固定 inspect_cart 只提供 reader 线索，不能认证
完整购物车；reader 重绑保留原始基线和预期增量。显式输出契约的评分使用现有实体槽，不跨商品复用。


### September 24 shared observation and Jev coverage (F_12)

Control capability facts belong to the shared Runtime regardless of model mode.
Exact AX targets can be enriched with bounded fixed DOM reads before journal preparation.
Cross-read sort/card proofs require the same query, source, page, generation and
interaction revision; potentially mutating actions invalidate that association.
Ordinary reads do not consume the three explicit unknown-effect verification attempts,
which are keyed to each outstanding effect rather than the most recent unrelated action.
Resolving one effect cannot reset another effect's consumed attempts.
Jev may select registered fixed probes and source-grounded first-result navigation,
with the same task, permission, argument and late page/node guards as other actions.
Separately quoted sort labels do not make an otherwise unique search literal ambiguous.
No alternate executor, model-generated proof or new browser lock is introduced.


### September 25 local decision loop (F_13)

Observed labels have one normalization. Action-result and automatic observation
share an interaction revision; independent later changes invalidate it. Navigation
proof binds a selected result to the actual landing page, including popup batches.
Browser tools execute serially through the existing scheduler. Recovery trials
are consecutive, reset by observed progress, and remain bounded by task budgets.
Model total/idle and tool timeouts are independent of the task deadline.

Jev selects operation and its scoped target in a single multi-head request. Closed
fixed reads and known local UI remain available during unknown-effect recovery;
unverified business effects never certify completion or authorize replay.
Bindings, receipts, milestones and observations reuse the existing phase, journal
and PageState. No additional workflow manager or alternate executor is introduced.

### Browser client accounting (F_14)

The existing task-end log reports per-client calls, returned token usage and
client wait duration, separately from task wall-clock duration. Accounting is
invocation-local and never participates in routing, budgets or completion.
Pending, cancelled and unreported usage remain explicit; a late completion from
an older invocation cannot affect the current one. Because Session updates merge
dicts, each saved meter replaces its dedicated state key synchronously so completed
call handles are removed. Tests use the real Session as well as isolated fakes.
### September 29 Jev decision context (F_15)

A target that failed twice (Jev, LLM or no observable progress) is retired for the parent
query across subagent runs, url, intent and re-render changes, and re-admitted when it later
succeeds. Jev's request includes the LLM's last six browser steps with per-step outcomes and
navigation destinations (host and path only), covered controls with the element covering them
(information only, never targets), structured target options with observed control state, and
flags for truncated page text and unread result cards. Search terms and quoted values come from
the user's unwrapped request (either host envelope prefix), limited to those the current intent
names. FINISH is not offered while task requirements are missing. Any Jev failure hands back
only the current observed state. Payload and pick logs are opt-in because they carry task text.

### September 29 lean guard trial (F_16)

`OPENJIUWEN_BROWSER_GUARDS=lean` enables a trial guard set, and strict is the default. In lean
mode a cart action is an ordinary click with no cart baseline requirement. A Playwright timeout
whose call log never reaches "performing <action>" is recorded as not dispatched rather than as
an uncertain write. An action that was performed and then timed out stays uncertain in both
modes. Task typing recognises buying and booking wording in both modes.


## Browser 执行回执与局部连续性（F_18，2026-10-08）

目标 preflight 通过只表示可尝试，不能写 executed=true。执行调用中未获 ACK 为 unknown；
完整 Playwright Call log（先移除 ANSI 样式）证明未发送时，compact/primitive 共用 journal
归类为未派发。复合输入已有动作的执行事实不能被后续等待错误抹掉；外层不再重放未知点击。

本地 offload recall 不推进页面版本。同页、同版本、同范围卡片读取可在短观察窗口复用，
导航、潜在写入、等待或新观察变化使缓存失效。任意脚本保持潜在写入边界。进展指纹只比较
内容与可执行状态，不把调用历史、观察元数据和卡片重新有效算成进展；新读取记 new_observation。

Jev 候选避免重交页面已经观察到的同一搜索，默认把回到已访问标签页留给 LLM 恢复；新标签仍
可选。同对象已 ACK 的业务动作由共享 journal 排除自动重放，不新增业务成功证明。新对象、
已执行的新输入/选择、显式新局部目标可表达后续工作；固定读取和其他局部动作继续开放。
LLM/Jev 共享 local_context，复杂目标复用可选 objective/绑定；普通查询无需额外阶段调用。

详见 [F_18](../features/F_18_browser-receipts-and-local-continuity.md)。

## Browser 默认观察与结果交接（F_19，当前契约）

默认任务不再从自然语言生成字段、数量、比较槽，不维护对应 acceptance/coverage 认证路径。
已保存的 inferred 状态在加载/续跑时退出该路径，保留原始观察、journal、局部目标与期限。
外部明确提供 requirements_source=explicit 的输出契约继续按既有字段形态检查，不能扩张为业务认证。
当前搜索 URL、未映射字段和推断缺失不自动改写完成状态，也不自动创建修复调用。
父层可显式用原 resume_task_id 纠正遗漏，受原期限和一次续跑限制；不带续跑请求的重复调用返回已有结果。

统一终态携带 worker 判断、执行回执、来源观察和 summary；字段诊断不替代原文。阻断列表不是完备枚举，
为空不能证明没有页面障碍。父层与 resume 保留同一结果；模型渲染只展示一次结果，避免三份重复投影。
常用等待使用已有目标/文本/URL 条件；缺目标仍拒绝，旧目标校验、串行派发、权限和真实未知效果保护保留。
本节取代 F_17 中继续默认维护 inferred 诊断和语义反证的部分，详见 [F_19](../features/F_19_browser-observation-first-results.md)。
