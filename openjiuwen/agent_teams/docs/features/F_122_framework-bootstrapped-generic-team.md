# 调度模式：会话启动即由框架建队（通用团队，任务驱动组员）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-15 |
| 状态 | 已实现（core 侧本仓；swarm 接线 D5 在 jiuwenswarm 的 `feature/framework-bootstrapped-team` 分支落地，PR 须 bump `openjiuwen` rev 到含本特性的 core 提交） |
| 适用范围 | **仅 `dispatch_mode == "scheduled"`**。autonomous 模式不开放本特性 |
| 范围 | core：`schema/blueprint.py`（`bootstrap_mode` + 团队身份字段 + 校验）、`agent/coordination/kernel.py`（`start()` 的 leader 分支补"无行即建"）、`tools/tool_factory.py` + `tools/tool_permissions.py`（framework 建队时减掉 `build_team`）、`team_context.py`（`TeamContextTracker` 新增 policy 频道）、`inbound_render.py`（进程内 `<team-policy>` 渲染）、`rails/team_policy_rail.py` + `prompts/sections.py`（leader 前缀换 prebuilt 起手段；准则文本在构造期渲染后交给 tracker）、`prompts/{cn,en}/leader_prebuilt.md`（新增）、`prompts/{cn,en}/inbound_tags.md`（补 `<team-policy>` 说明）、`core/context_engine/processor/compressor/util.py`（`team_policy` 重注入兼容历史消息形态）、`i18n.py`（通用团队身份默认文案）、`agent/agent_configurator.py` + `rails/elements.py` + `rails/team_tool_rail.py`（`bootstrap_mode` 接线到 TEAM_TOOL / TEAM_POLICY 两个 RailSpec）；swarm（另一仓库落地）：`agents/harness/team/config_loader.py`、`agents/harness/team/team_manager.py`、`server/runtime/agent_adapter/team_helpers.py`、`common/schema/chat_send.py`、web 前端 `features/teamDispatch/`；文档：`docs/specs/{S_01,S_08,S_09,S_22}` |
| 测试基线 | `pytest tests/unit_tests/agent_teams tests/unit_tests/core/context_engine --ignore=…/cli --ignore=…/observability --deselect …/test_member_activity.py::test_observe_member_status_emits_after_the_debounce` → **4154 passed, 36 skipped, 3 xfailed** |
| Refs | （待用户给 issue 号） |
| 关系 | 建立在 F_62（调度模式运行时）、F_70（团队状态经对话历史投递）、F_76（leader 协同准则渐进式披露）、F_118（任务内联成员 + 退场策略）之上。本特性把 F_76 的"准则是一条对话内容，不是系统提示词"坚持到底：当 leader 不再调 `build_team` 时，准则改由 F_70 的 `TeamContextTracker` 以 `<team-policy>` 块在首次执行时插到用户输入前面——与 teammate 收到自身身份 / 团队信息 / 名册的方式完全一致 |

## 背景

调度模式（`dispatch_mode="scheduled"`）下，任务驱动的能力——`create_task(assignee|member)` 落库、
`TeamScheduler` 自动开工 / 验票 / 退场、`create_task(member=...)` 内联造人——**全部以"团队已经物化"
为前提**：`TeamScheduler` 在 `kernel.setup` 构造后休眠，只在 `kernel.notify_team_built()`
（`build_team` 成功回调）或 `kernel.start()` 读到已有团队行时激活（S_22 不变量 2）。

而"团队物化"这件事今天只有一个入口：leader 自己调用 `build_team` 工具。于是每个调度模式会话都
要先花一轮 LLM 交互做一件**没有决策含量**的事——给团队起个名、写个描述、把自己注册进去——
然后才能开始规划任务。F_76 把 leader 的全部协同准则塞进 `build_team` 的返回值，这又把
"必须先调一次 `build_team`"焊死成了协议：不调它，leader 连准则都拿不到。

用户诉求：

1. **会话一开始团队就在**：由框架在会话启动时直接建队，不由模型触发。
2. **团队设定通用**：不规定领域，团队名 / 描述 / leader 身份用一套通用默认值（可配置覆盖）。
3. **会话中靠创建任务驱动成员产生**：leader 直接 `create_task(member={...})` 带出承担者
   （F_118），需要常驻角色才 `spawn_teammate`。
4. **准则不进系统提示词**：原 `build_team` 返回的那份准则，改为在首次执行时作为 context 插到
   user 输入前面，与 teammate 收团队状态的方式一致。

本质上这是调度模式的一个**启动变体**：团队的"谁来建、何时建"从 leader 的运行期选择变成 spec
的静态配置，准则的"怎么到达 leader"从工具返回值变成对话上下文。它不改变任何任务驱动机制本身。

## 现状勘查（设计输入）

1. **`build_team` 做的事可以程序化调用。** `TeamBackend.build_team(display_name, desc,
   leader_display_name, leader_desc, overrides)` 是普通 async 方法，仓内已有三处绕过工具直接调它：
   `group_chat/handler.py::deliver_group_message`（团队行不存在就以空描述建队）、swarm 的
   `summary_org/launcher.py`（`activate → backend.build_team → pause`）与 `expert_org/launcher.py`。
   它落团队行、注册 leader 行（`BUSY`）、注册预定义成员（`UNSTARTED`）、写 `team_card.md` /
   `team_prompt.md`、触发 `on_team_built`。`on_team_built` 在 `TeamAgent._mark_team_built` 里持久化
   checkpoint 的 `db_state=created` 并调 `kernel.notify_team_built()` 激活调度器。**这条链一行不改即可复用。**
2. **`kernel.start()` 已经有"团队行存在 → `recover_team`"的 leader 分支**，缺的只是它的对称分支
   "团队行不存在且配置要求框架建队 → `build_team`"。`start()` 的执行顺序是：`db.initialize` →
   `bind_session` → `harness.start` + `stream_controller.start` → **leader 查团队行** → workspace
   初始化 → workspace 基线种子 → memory toolkit。`TeamAgent.invoke / stream` 先 `coordination.start(session)`
   再 `enqueue_user_input(inputs)`（`team_agent.py:867/877`、`925/935`），所以在 `start()` 内建队**天然
   早于首条用户输入进入 leader**。
3. **runtime dispatch 表不需要改。** 首次运行：团队行不在 DB、session 无 bucket → `CREATE`
   （`require_spec=True`），`start()` 里建行。同 session 再跑：行在 + bucket 在 → `COLD_RECOVER`，
   `start()` 读到行走 `recover_team`。新 session 接手：`NEW_TEAM_IN_SESSION`，同样走 `recover_team`。
   三条路径对 `start()` 来说只是"行在 / 行不在"两个分支，与今天一致。
4. **F_76 把准则放进 ToolResult 的理由在本模式下变了形，但结论的后半段仍成立。** F_76 的论据是：
   `enable_hitt` / `enable_task_verification` 的生效值由 leader 在 `build_team` 调用时才确定，所以准则
   只能在那之后投递。本模式下 leader 不调 `build_team`，`overrides=None`，生效值 = spec 天花板
   （`build_team` 的既有公式 `spec_ceiling and (arg if arg is not None else True)` 在 `arg=None` 时退化
   为 `spec_ceiling`）；`dispatch_mode` 本就是静态 spec 配置（S_22 不变量 3）。于是**准则文本在 rail
   构造期就能渲染成一份静态文本**。但 F_76 的另一半——准则是一条对话内容而不是系统提示词前缀——
   由用户明确要求保留：它仍以对话上下文到达 leader，只是载体从 ToolResult 换成 F_70 的团队状态通道。
5. **F_70 的 `TeamContextTracker` 正是"按基线、投递一次、随输入插到最前"的现成机制。** 它按频道
   （自身身份 / 团队信息 / 名册）比对持久化在成员 child `AgentSession` state（key
   `team_prompt_context`）里的基线，只渲染尚未告知的部分；`TeamPolicyRail.on_user_message` 把渲染结果
   `parts.insert(0, text)` 插到本轮输入最前面再 `commit` 基线。它有**两条投递道**：
   `attachment_manager` 存在时走**当前态附件通道**（section `team.context`，每个边界重注册，历史里没有
   快照——首次或压缩后——就注册 `snapshot_text()` 全量），否则走**历史消息通道**（文本直接进历史）。
   准则作为第四个频道加进去，两条道自动覆盖。
6. **F_76 的已知遗留在本模式下顺带消失。** F_76 记录了 `NEW_TEAM_IN_SESSION` 的 leader 拿不到准则
   （准则在历史里，历史随 session 走）。tracker 基线存在 leader 自己的 child session state，换 session
   即空基线，准则会随首条输入重新投递。
7. **`<team-policy>` 标签已存在。** `inbound_render.render_team_policy(body, tools, note)` 把外部 CLI
   成员的系统提示词包成 `<team-policy tools="...">`（F_114）。进程内 leader 复用同一元素名，去掉
   MCP 命名空间属性与 `tool-namespace` note（它调的就是裸名工具）。
8. **压缩器已有 `team_policy` 重注入点。** `build_team_policy_reinjected_messages` 在历史里找
   `build_team` 的 tool_call 并原样重注入其结果、不截断；找不到返回 `[]`。本模式历史里没有
   `build_team` 调用，需要让它也认得 `<team-policy>` 形态的历史消息（仅历史消息通道需要；附件通道
   压缩后自动重注册全量快照）。
9. **调度模式 leader 的派活提示词已经覆盖"内联造人"。** `dispatch_scheduled_leader.md` 明确写了
   "常驻成员先 `spawn_teammate` 再 `create_task`；只为一个任务存在的专家用 `member` 内联并指定
   `lifetime`"。本特性只需在起手段里把"团队当前零成员、按任务带人"说清，不重写派活规则。
10. **`build_team` 不只是建队，还是 leader 工具集里的"幂等接管"入口。** `rejects_rebuild()` /
    `_reattach_team` 是为 `NEW_TEAM_IN_SESSION` 的 leader 准备的（F_76 §2b）。本模式下接管由 `start()`
    的 `recover_team` 分支完成，准则由 tracker 重发，两条都不再需要。因此 `build_team` 可以从 leader
    工具集**整个拿掉**，而不是留一个"调了也无事发生"的工具（prompts/AGENTS.md 编辑规则 2：能力关掉时
    讲它的文案必须跟着消失，工具同理）。
11. **swarm 侧已有"会话级调度模式"的完整通道**：`chat.send` 的 `team_dispatch_mode` →
    `team_helpers._resolve_session_team_dispatch_mode`（params > 会话 metadata > 团队配置）→
    `TeamManager.get_swarm_enriched_team_spec(dispatch_mode=)` → `spec.dispatch_mode`，并经
    `persist_session_team_dispatch_mode` 持久化；前端 `features/teamDispatch/featureConfig.ts` 有实验开关。
    本特性的会话级开关可以逐字复用这条通道的形状。

## 核心判断

值得做，且改动面很小：**不新增事件、handler、工具、状态、启动路径、投递通道**。新代码是"spec 多一个
开关 + 两个身份字段、`kernel.start()` 多一个对称分支、工具工厂多一条减法、leader 前缀换一个短模板、
tracker 多一个频道、压缩器多认一种消息形态"。所有任务驱动机制（调度器、内联成员、退场、验票）原样复用。

关键设计约束（与既有规约对齐）：

- **只在调度模式开放**。autonomous 模式下团队物化后 leader 仍要广播启动成员、成员自主认领，
  "框架建队"对它没有任务驱动的收益。spec 校验直接拒绝 `bootstrap_mode="framework"` +
  `dispatch_mode="autonomous"`。
- **`bootstrap_mode` 与 `team_mode` / `dispatch_mode` / `teammate_mode` 正交**。它只回答"团队行由谁、
  在什么时刻写入、准则经哪条通道到达"，不碰名册能否生长（`team_mode`）、任务如何到达成员
  （`dispatch_mode`）、成员是否先交计划（`teammate_mode`）。通用团队默认 `team_mode="default"`；若宿主
  挂了 AgentGroup 预定义成员则自然是 `hybrid`，本特性不干预。
- **模式变量在 spec 构建期全部落定**，没有任何运行期选择留给 leader。这是准则能在 rail 构造期渲染成
  静态文本的前提。
- **系统提示词前缀不因本特性变厚**。leader 的系统提示词仍只有两个 team section（起手段 +
  `team_extra`），准则是对话内容——与 F_76 / F_70 "系统提示词只装任何时刻都成立的内容"一致。

## 数据结构 / 状态机

### 1. `TeamAgentSpec` 新字段

```python
class TeamAgentSpec(BaseModel):
    ...
    bootstrap_mode: Literal["leader", "framework"] = "leader"
    """谁物化团队行、准则怎么到达 leader。

    "leader"（默认，今天的行为）：leader 调 build_team 工具建队，协同准则随该工具返回值披露（F_76）。
    "framework"：leader kernel start() 发现团队行不存在时由框架直接 build_team；leader 工具集
    不含 build_team；协同准则由 TeamContextTracker 以 <team-policy> 块在首次输入前投递。
    仅允许与 dispatch_mode="scheduled" 组合。
    """

    team_display_name: str | None = None
    """framework 建队时写入团队行的 display_name；None 取 i18n 通用默认（按 spec.language）。"""

    team_desc: str | None = None
    """framework 建队时写入团队行的 desc（会进 team_card.md 与成员看到的 team_info）；None 取通用默认。"""
```

leader 身份复用既有 `LeaderSpec.display_name` / `LeaderSpec.desc`（已有默认值 `"Team Leader"` /
`t("blueprint.default_desc")`），不新增字段。

校验（`TeamAgentSpec` model validator，与 `_validate_hitt_predefined` 同层）：

- `bootstrap_mode == "framework" and dispatch_mode != "scheduled"` → `ValueError`，错误信息点名
  "framework bootstrap requires scheduled dispatch"。
- `bootstrap_mode == "leader"` 时 `team_display_name` / `team_desc` 允许为 None 且被忽略（leader 自己填）；
  非 None 时不报错，但不生效——文档写明，避免"配了不生效"的困惑。

i18n 新增（`i18n.py`，cn / en 成对）：

| key | cn | en |
|---|---|---|
| `blueprint.generic_team_display_name` | 通用协作团队 | General Collaboration Team |
| `blueprint.generic_team_desc` | 不限定领域的通用任务协作团队：由 Leader 按任务动态组建承担者，任务完成后成员按退场策略关闭或休眠。 | A domain-agnostic collaboration team: the leader assembles assignees per task; members retire or go dormant once their tasks close. |

### 2. `TeamContextTracker` 基线新增一个字段

```python
_POLICY_HASH = "policy_hash"   # 已投递的准则文本 sha256；缺失 = 从未投递
```

落在既有 `team_prompt_context` state 里，与 `identity_emitted` / `team_info_mtime` / `roster` 同生命周期
（leader 自己的 child `AgentSession`）。用内容哈希而不是布尔：团队工作空间演进（S_24）改了
`leader_policy.md` 等模板后，构造期渲染出的准则文本变化 → 哈希变化 → 下一次输入前**重发一次**，
与 `team_info` 频道"文件变了就重发"的语义对齐。

### 3. 团队行 / 成员行 / 状态机

**零改动。** framework 建队落的团队行与 leader 调 `build_team` 落的完全同形
（`dispatch_mode="scheduled"`、`enable_task_verification = spec` 值）；leader 行 `BUSY`；预定义成员
（若有）`UNSTARTED`。`MemberStatus` / `TaskStatus` / checkpoint `db_state` 的取值与流转不变。

### 4. `bootstrap_mode` 的传播

与 `dispatch_mode` 同路：`TeamAgentSpec.bootstrap_mode` → `AgentConfigurator` 烘焙进 `TEAM_TOOL` /
`TEAM_POLICY` 两个 `RailSpec.params` → `elements.py` 的 `ConstructionInput` → `TeamToolRail`
（减工具）/ `TeamPolicyRail`（选起手段模板 + 决定是否给 tracker 准则文本）。teammate 不消费它。

## 决策

### D1：建队点放在 `CoordinationKernel.start()` 的 leader 分支

```python
team_row_present = False
if host.role == TeamRole.LEADER and infra.team_backend:
    backend = infra.team_backend
    existing = await backend.db.team.get_team(backend.team_name)
    if existing is not None:
        team_row_present = True
        await host.recover_team()
    elif blueprint.spec.bootstrap_mode == "framework":
        await backend.build_team(
            display_name=blueprint.spec.team_display_name or t("blueprint.generic_team_display_name"),
            desc=blueprint.spec.team_desc or t("blueprint.generic_team_desc"),
            leader_display_name=blueprint.spec.leader.display_name,
            leader_desc=blueprint.spec.leader.desc,
            overrides=None,            # 生效值 = spec 天花板
        )
        team_row_present = True
```

理由：

- 这是"团队行存在 → 恢复"的**对称分支**，两种情况在同一处决策，读代码的人一眼看到"start 之后团队行
  必然存在"这条新不变量。
- `build_team` 内部触发 `on_team_built` → `_mark_team_built`：持久化 `db_state=created`（此时
  `team_session` 已 `bind_session`，写得进去）→ `notify_team_built()` 激活调度器。**激活时机与今天
  "先于任何 spawn / create_task"的承诺一致**，且早于首条用户输入入队（勘查 2）。
- 不碰 `runtime/dispatch.py` 的 7 路真值表（勘查 3）。

`overrides=None` 是刻意的：framework 建队没有 leader 在场做选择，HITT / Bridge / 验证闸一律取 spec
天花板。宿主要改这些，改 spec。

### D2：framework 建队时 leader 工具集减掉 `build_team`

`tool_factory.create_team_tools` 新增 `bootstrap_mode` 参数，与 plan_mode / persistent 两条减法同处：

```python
if role == "leader" and bootstrap_mode == "framework":
    allowed = allowed - {"build_team"}
```

不留"调了无事发生"的工具：工具在、描述在，模型就会去调它（prompts/AGENTS.md 编辑规则 2 的工具版）。
`clean_team` 不受影响（temporary 团队仍需 leader 收尾）。`rejects_rebuild` / `_reattach_team` 代码保留
不动——它们服务于 `bootstrap_mode="leader"` 的 `NEW_TEAM_IN_SESSION`，本模式物理不可达。

### D3：准则经 `TeamContextTracker` 的 policy 频道，在首次执行时插到输入最前

**系统提示词不变厚。** `TeamPolicyRail._build_leader_sections` 按 `bootstrap_mode` 只换起手段：

| bootstrap_mode | 系统提示词里的 team section | 准则投递载体 |
|---|---|---|
| `leader`（今天） | `team_bootstrap`（P:11）+ `team_extra`（P:17） | `build_team` ToolResult（F_76） |
| `framework`（新） | **`team_prebuilt`（P:11，新模板）** + `team_extra`（P:17） | **`TeamContextTracker` policy 频道 → `<team-policy>` 块，随首条输入插到最前** |

**准则文本在 rail 构造期渲染。** `TeamPolicyRail.__init__` 在 `role == LEADER and bootstrap_mode ==
"framework"` 时调用既有的 `build_leader_policy_disclosure(lifecycle, teammate_mode, team_mode,
dispatch_mode, language, hitt_enabled=spec.enable_hitt, loader=<rail 绑定的 workspace loader>)`，
得到的文本包成 `<team-policy>` 后作为 `policy_text=` 传给 `TeamContextTracker`。内容函数一字不改，
只是消费者从 `BuildTeamTool.render_for_llm` 换成 tracker；其它角色 / `leader` 模式传 `None`，频道关闭。

- `hitt_enabled` 用 spec 天花板：本模式没有 `build_team` 调用，天花板就是生效值（勘查 4）。
- `enable_task_verification` 同理，调度版 dispatch 段里"reviewer 系统是否生效取决于 build_team 的
  `enable_task_verification` 参数"这句在 prebuilt 下不成立——`dispatch_scheduled_leader.md` 这句要改成
  模式中立的表述（"取决于团队配置的验证开关"），cn/en 同步。这是本特性唯一要动的既有模板句子。
- loader 用 rail 已绑定的团队工作空间 loader（S_24 可演进工作空间），演进后的 `leader_policy.md` 等
  照常生效；哈希变化触发重发（数据结构 §2）。

**`<team-policy>` 渲染。** `inbound_render` 新增 `render_team_policy_inline(body)`（或给
`render_team_policy` 加 `tools=None` 分支）：`<team-policy>\n{body}\n</team-policy>`，不带 `tools`
属性、不带 `tool-namespace` note，正文**不转义**（与外部 CLI 版同理：它是团队自己的 markdown）。
`inbound_tags.md`（cn/en）补一句：`<team-policy>` 是团队协同准则，随首条输入到达一次、长期有效。

**tracker 频道。** `TeamContextTracker` 新增：

```python
def __init__(self, *, ..., policy_text: str | None = None): ...

def _policy_block(self, baseline, updated) -> str | None:
    if self._policy_text is None:
        return None
    digest = sha256(self._policy_text)
    if baseline.get(_POLICY_HASH) == digest:
        return None
    updated[_POLICY_HASH] = digest
    return self._policy_text          # 已是 <team-policy> 块
```

`_compose_blocks` 的顺序：`<team-context>`（身份 + 团队信息）→ **`<team-policy>`** → 名册
`<team-event kind="roster">`。leader 先读"我是谁、团队是什么"，再读"规则是什么"，最后看"名册上有谁"
（只有自己），然后才是用户的目标。`snapshot_text()` 同样带上 policy（它是全量当前态）。

**两条投递道零改动自动覆盖**（勘查 5）：

- 附件通道（swarm 的 DeepAgent 有 `prompt_attachment_manager`）：`team.context` section 是当前态，历史
  无快照时（首次、压缩后）注册 `snapshot_text()` 全量——准则在里面；之后每个边界重注册同一内容，哈希
  不变不重复宣告。**压缩自愈，无需重注入。**
- 历史消息通道：`on_user_message` 把 `pending_text` 插到 `parts[0]`，`commit` 基线。准则成为一条普通
  历史消息——与 F_76 的 ToolResult 面临同一个问题：full compaction 会把它摘要掉。

**压缩重注入扩展**（仅历史消息通道需要）：`build_team_policy_reinjected_messages` 在现有"找
`build_team` tool_call"之后加第二种匹配——从后往前找第一条正文以 `<team-policy` 开头的
`UserMessage`（去掉前置的 `<team-clock>` 等同输入兄弟块后判断，或直接子串匹配 `<team-policy`
且不在 `messages_to_keep` 里），原样整条重注入，不截断。注册名、marker、`_policy_already_kept`
去重逻辑复用。两种形态互斥（有 `build_team` 调用的团队不会有 `<team-policy>` 消息，反之亦然），
不会双重注入。

**投递时机。** 团队行已由 D1 在 `start()` 建好，所以首条输入到来时 identity / team_info / roster /
policy 四个频道**同一次**渲染、同一次 commit。leader 首轮的输入形如：

```
<team-context>…身份 + 团队信息…</team-context>

<team-policy>…完整协同准则（role / workflow / dispatch / task_state / lifecycle / HITT / inbound tags）…</team-policy>

<team-event kind="roster">…名册：仅 leader…</team-event>

<team-clock now="…"/>
<用户的目标>
```

**恢复路径。**

| 路径 | tracker 基线 | 准则 |
|---|---|---|
| `COLD_RECOVER`（同 session） | 在（child session state 随 session 恢复） | 历史里有，不重发；若被压缩掉，附件通道重注册 / 历史通道由重注入兜底 |
| `NEW_TEAM_IN_SESSION`（新 session 接手） | 空 | 随首条输入重发——F_76 的已知遗留在本模式下消失 |
| 工作空间演进改了准则模板 | 哈希不同 | 下一次输入前重发一次 |

**与 F_76 的关系。** `leader` 模式一字不动。`build_leader_policy_disclosure` 的"排除三项"在本模式下
仍成立：`team_identity` 走 tracker 的身份频道（本来就是）、`team_extra` 留前缀、`team_bootstrap` 换成
`team_prebuilt` 留前缀。"一份内容一个消费者"：framework 模式下准则只有 tracker 一个消费者。

**`leader_prebuilt.md`**（cn/en，纯静态，无占位符）只说三件事，不重复准则正文：

1. 身份：你是 TeamLeader；团队已由系统建好并把你注册为 Leader，**没有 `build_team` 这一步**，直接
   从理解目标开始。
2. 现状：团队是通用团队，**当前没有任何成员**；团队信息、协同准则（`<team-policy>`）、名册会随
   第一条输入一起出现在对话里，按它们行事。
3. 组员方式指路：成员随任务产生——只服务一个任务的承担者用 `create_task(member={..., lifetime})`
   内联带出；需要跨任务复用的常驻角色才 `spawn_teammate`。细则以 `<team-policy>` 里的《任务下发》
   为准（即 `dispatch_scheduled_leader`，此处不重复）。

`swarmflow` 分流文案：`leader_swarmflow.md` 讲的是"build_team 路径 vs swarmflow 路径"，本模式下
"build_team 路径"这个词已不存在。v1 决定：`team_prebuilt` **不留** `{{collaboration_mechanism}}` 槽，
swarmflow 工具若仍挂载，由其工具描述自己说明用途。如评审认为需要，再加一份
`leader_swarmflow_prebuilt.md`。

### D4：通用团队身份是 spec 默认值，不是硬编码

`team_display_name` / `team_desc` 为 None 时取 i18n 通用文案（按 `spec.language`）；宿主（swarm）可从
`config.yaml` 覆盖。leader 身份沿用 `LeaderSpec.display_name` / `desc`——swarm 的
`modes.team.<name>.leader.display_name` / `persona` 已经映射到这两个字段，不需要新配置。

### D5：swarm 侧接线（复制 `team_dispatch_mode` 通道的形状）

| 层 | 改动 |
|---|---|
| 团队配置 | `config_loader.py:607` 附近：`spec_dict["bootstrap_mode"] = team_raw.get("bootstrap_mode", "leader")`；`team_display_name` / `team_desc` 同样从 `modes.team.<name>` 读 |
| 会话级参数 | `chat_send.py`：`team_bootstrap_mode: NotRequired[Literal["leader", "framework"]]`，语义同 `team_dispatch_mode`（只在会话首条请求生效，随会话持久化） |
| 解析与持久化 | `team_helpers.py`：`_resolve_session_team_bootstrap_mode(params, session_id)`（params > 会话 metadata > 团队配置）+ `persist_session_team_bootstrap_mode`，metadata key `session_team_bootstrap_mode`；`TeamManager.get_swarm_enriched_team_spec(bootstrap_mode=)` 写入 `spec.bootstrap_mode` |
| 组合约束 | 会话级 `framework` + 最终 `dispatch_mode != "scheduled"` → **在 swarm 边界报错给客户端**（`chat.send` 参数校验），不要等到 core 的 spec validator 抛 `ValueError` 变成 500 |
| 前端 | `features/teamDispatch/`：调度模式开关下新增子选项"启动即建队"（仅 `scheduled` 时可选），随首条 `chat.send` 下发 `team_bootstrap_mode`，从会话 metadata 恢复 |

`tool_display.py` 等对 `build_team` 工具名的 UI 映射不需要改：本模式下该工具不会被调用，映射只是不命中。

### D6：不变量（写进 S_22 / S_08 / S_09）

1. `bootstrap_mode="framework"` ⇒ `dispatch_mode="scheduled"`（spec 校验保证）。
2. `bootstrap_mode="framework"` 的 leader `kernel.start()` 返回时团队行必然存在（新建或已存在），
   调度器必然已激活。S_22 不变量 2 的激活点从两个变为三个：`notify_team_built`（`build_team` 工具 /
   framework 建队共用同一回调）与 `start()` 读到已有行。
3. `bootstrap_mode="framework"` 的 leader 工具集不含 `build_team`；其系统提示词只含 `team_prebuilt` +
   `team_extra`；协同准则由 `TeamContextTracker` 的 policy 频道以 `<team-policy>` 块投递，基线
   `policy_hash`；`build_leader_policy_disclosure` 在该模式下只有 tracker 一个消费者。
4. framework 建队的生效能力 = spec 天花板（`enable_hitt` / `enable_bridge` / `enable_task_verification`）。
5. 准则在两种 bootstrap 模式下都是**对话内容而非系统提示词**（S_09）；压缩后由 `team_policy` 重注入
   （历史消息通道）或附件通道的全量快照重注册恢复。

## 拒绝的方案

| 方案 | 为什么不选 |
|---|---|
| **A. 在 `TeamRuntimeManager.activate` 的 `CREATE` 分支建队**（仿 summary_org launcher） | launcher 是团队**之外**的 operator 在编排一个 persistent 团队；本特性是团队**自己的**生命周期。`start()` 已经持有"行在 → 恢复"的分支，把"行不在 → 建"放到另一层会让同一个问题在两处决策。另外 `activate` 时 session 尚未 `bind_session`，`on_team_built` 的 checkpoint 写入会被 `_persist_team_db_state` 的 `team_session is None` 静默跳过，`db_state` 落不下 `created`。 |
| **B. 保留 `build_team` 工具但让它在本模式下幂等返回准则** | 模型看到工具就会调（F_76 §2b 的 cold-recover 经验："永远不会带来新信息，只会白烧一轮"）。准则已有通道，这个工具没有任何剩余职责。 |
| **C. 准则回到系统提示词**（本文档首稿方案） | 用户明确否决。理由也站得住：系统提示词应只装"任何时刻都成立"的内容（F_76 / F_70），准则随团队工作空间演进会变，放前缀就要重算整条 KV cache；而且两种 bootstrap 模式的 leader 前缀会分叉成"薄 / 厚"两形，断言 leader 提示词的测试要分两套。走 tracker 频道后，两种模式的系统提示词同形（只换起手段），准则统一是对话内容，压缩兜底也统一走 `team_policy` 重注入。 |
| **D. 准则作为一条独立的 `UserMessage` 在 `start()` 里 `add_messages` 进历史** | 绕开了 tracker 的基线：换 session 时不知道该不该重发，压缩后不知道有没有丢，与身份 / 团队信息走两套记账。tracker 频道把"投过没有"统一记在 `team_prompt_context` 里，附件通道的当前态语义也只有经 tracker 才拿得到。 |
| **E. 不做新开关，用 `predefined_members` 预置一个"通用成员"模拟"启动即有团队"** | 这只是把 `build_team` 从"建空团队"变成"建有一人的团队"，leader 仍要调它；而且"通用成员"违背任务驱动造人的诉求——成员应随任务出现、随任务退场（F_118）。 |
| **F. 在 autonomous 模式也开放 framework 建队** | autonomous 的任务流转依赖 leader 广播启动 + 成员自主认领，"框架建队"没有任务驱动收益。范围外，明确拒绝（spec 校验）。 |
| **G. 让 framework 建队时把 `enable_task_verification` 等交给宿主运行期传参** | 这会重新引入"运行期落定的模式变量"，准则就不能在构造期渲染、哈希基线也失去意义。宿主要调，改 spec；spec 本就是会话级构建的（swarm 每会话 `get_swarm_enriched_team_spec`）。 |
| **H. 把 `leader_bootstrap.md` 改成带槽模板，framework 模式填"团队已建好"** | 两种模式的起手段几乎没有共同句子，强行共模板会出现"能力关掉但文案还在"的洞；按 prompts/AGENTS.md 编辑规则 2 应换模板。 |

## 时序（首轮）

```
Runner.run_agent_team_streaming(spec: bootstrap_mode=framework, dispatch_mode=scheduled)
  └─ manager.activate → dispatch: team_in_db=False, team_in_session=False → CREATE
       └─ TeamAgentSpec.build() → TeamAgent(leader)
            kernel.setup(role=LEADER): dispatch_mode=scheduled → TeamScheduler 构造（休眠）
            TeamToolRail: allowed -= {build_team}
            TeamPolicyRail: 前缀 = team_prebuilt + team_extra；
                            policy_text = <team-policy>{build_leader_policy_disclosure(...)}</team-policy>
                            → TeamContextTracker(policy_text=...)
  └─ TeamAgent.stream(query)
       └─ coordination.start(session)
            db.initialize → bind_session → harness.start
            leader: get_team → None → spec.bootstrap_mode==framework
              → backend.build_team(通用身份, overrides=None)
                 → 团队行 / leader 行 / 预定义成员行 / team_card.md
                 → on_team_built → _mark_team_built → db_state=created → scheduler.activate()
            workspace.initialize → 基线种子 → memory toolkit
       └─ enqueue_user_input(query)
            TeamPolicyRail.on_user_message:
              tracker.pending_text → <team-context>(身份+团队信息) + <team-policy> + roster（仅 leader）
              parts.insert(0, …) → commit（identity_emitted / team_info_mtime / policy_hash / roster）
            leader 首轮：create_task(member=...) → 内联成员落 UNSTARTED 行 + 任务落 PENDING(assignee)
            TeamScheduler 开工扫描 → leader 身份邮箱交接 → auto_start_member 拉起成员
```

## 验证

core 新增 / 改动用例（均通过）：

- `tests/unit_tests/agent_teams/test_dispatch_choice.py::test_spec_bootstrap_mode_validation`：默认值、
  `framework` + `scheduled` 合法且 JSON round-trip、`framework` + `autonomous` 拒绝。
- `tests/unit_tests/agent_teams/test_coordination_lifecycle.py`（5 例）：leader + framework + 无行 →
  `start()` 内 `backend.build_team` 被调一次（i18n 通用身份 / spec 覆盖的身份、leader 的 display_name /
  desc、无 `overrides`），`recover_team` 不调；有行 → 只 `recover_team`；`bootstrap_mode="leader"` / 非
  leader 角色 → 两者都不调。
- `tests/unit_tests/agent_teams/tools/test_tool_variants.py`（2 例）：framework 下 leader 集与默认集只差
  `build_team`；teammate 集不变。
- `tests/unit_tests/agent_teams/test_team_context_policy.py`（新文件，6 例）：policy 频道投递一次并记
  `policy_hash`；文本变化重发；未 commit 则再次提供；`policy_text=None` 不出块；`snapshot_text()` 带块；
  `render_team_policy_inline` 无 `tools` / 命名空间 note 且正文不转义。
- `tests/unit_tests/agent_teams/test_team_policy_rail.py::TestTeamPolicyRailFrameworkBootstrap`（5 例）：
  framework 下 leader 前缀 = `team_prebuilt` + `team_extra` 且不含准则正文；首条输入里 `<team-context>` →
  `<team-policy>` → 名册的顺序、准则正文与 `build_leader_policy_disclosure(dispatch_mode="scheduled")`
  逐字一致、第二条输入不重发；附件通道下准则在注册的快照里而不在输入里；`leader` 模式不出块；
  teammate 不出块。
- `tests/unit_tests/core/context_engine/test_team_policy_reinjection.py`（新文件，6 例）：首条输入里的
  `<team-policy>` 块整块原样恢复（不带输入其余部分）；仍在 keep 里不重注入；`build_team` 结果优先；
  已重注入的拷贝不再作为来源；未闭合块跳过；无准则返回 `[]`。
- `tests/unit_tests/agent_teams/prompts/test_member_system_prompt.py::test_an_in_process_member_reads_the_policy_unwrapped`：
  断言由"不含 `<team-policy`"收紧为"不以 `<team-policy` 开头且不含 `<team-policy tools=`"——
  `inbound_tags.md` 现在会提到这个标签。

`tests/unit_tests/agent_teams/agent/test_member_activity.py::test_observe_member_status_emits_after_the_debounce`
在未改动的基线分支上同样失败（2s 去抖计时用例），与本特性无关，基线里 deselect。

swarm 侧（D5）的用例在 jiuwenswarm `tests/unit_tests/agentserver/test_team_dispatch_mode.py`（8 例：解析优先级 / 未知值 / 持久化 / spec 接线 / 组合拒绝）。

## 已知遗留 / 待确认点

1. **swarmflow 分流文案**在本模式下暂不渲染（D3）；若实际使用中 leader 在"该不该走 swarmflow"上表现
   退化，再补 `leader_swarmflow_prebuilt.md`。
2. **准则体量**：`build_leader_policy_disclosure` 渲染结果数 KB，经附件通道时每个边界重注册同一内容
   （哈希不变不宣告，但 section 本身在当前态里）。这与 `leader` 模式下准则占一条 ToolResult 的体量
   相同，不是新增成本；只是提醒实现时不要把 policy 频道做成"每轮重发"。
3. **temporary 生命周期的收尾**：framework 建队的 temporary 团队仍由 leader `clean_team` 解散，与今天一致；
   但"会话一开始就建队"更贴近 persistent 语义，swarm 默认配置是 `persistent`，建议 framework 模式文档
   推荐 persistent，不做强制。
4. **Organization（expert_org / summary_org）** 自带 `build_team` 调用，不走本开关；若将来想让 Owner
   Team 也"启动即建"，需单独评估 launcher 与 `start()` 建队的先后（两处都建会撞主键，`build_team` 对
   已存在行走 `_reattach_team` 幂等，但 `on_team_built` 会触发两次）。
5. **swarm 接线（D5）已在 jiuwenswarm 侧落地**（`team_bootstrap_mode` 会话参数、`session_team_bootstrap_mode`
   持久化、`modes.team.<name>.bootstrap_mode` 配置、"+"菜单里调度模式下的「启动即建队」子开关）；framework +
   非 scheduled 的组合在 `TeamManager.get_swarm_enriched_team_spec` 边界以 `ValueError` 拒绝。
6. **policy 频道反哺 `leader` 模式**：同一频道若也在 `bootstrap_mode="leader"` 下启用（准则文本改为
   运行期从 `build_team` 结果取），可以填平 F_76 记录的 `NEW_TEAM_IN_SESSION` 遗留。本特性不做，
   作为 follow-up 记录。
7. `TeamSpec`（spawn 载荷）**未镜像** `bootstrap_mode`：teammate 不消费它，只在 `TeamAgentSpec` 上保留。
