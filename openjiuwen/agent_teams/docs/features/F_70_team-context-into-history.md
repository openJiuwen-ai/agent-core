# 团队上下文改走对话历史：数据产生时插入，只插不删，基线持久化

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-07-29 |
| 范围 | **core**：`single_agent/rail/base.py` 新增 `AgentCallbackEvent.ON_USER_MESSAGE` + `UserMessageInputs` + `AgentRail.on_user_message`（`rail/__init__.py` 同步导出）、`single_agent/agents/react_agent.py` 三处输入准入收敛为 `_admit_user_message` 并触发该事件；**harness**：`deep_agent.py` 把该事件加入 `_BRIDGE_EVENTS`（否则回调挂到外层 agent、永不触发）；新增 `schema/team.py` 的 `TeamRuntimeContext.display_name`（leader 经 `blueprint`、teammate 经 `spawn_manager` / `payload` 填充）；新增 `team_context.py`（`TeamContextTracker` + session 基线）、`prompts/messages.py`（三种消息正文 + `diff_roster`）；`prompts/sections.py`（删 `build_team_info_section` / `build_team_members_section` / `build_team_attachment_notice_section` + `TeamSectionName.INFO`/`MEMBERS`/`ATTACHMENT_NOTICE` + `include_attachment_notice` 参数，`build_team_identity_section` 改为包装 `build_identity_text`）；`prompts/__init__.py`；`inbound_render.py`（`render_team_context`）；`i18n.py`（`team_context.roster_announcement_note` cn/en）；`rails/team_policy_rail.py`（attachment 通道整体删除，改写历史落位 + `prepend_to_content`）；`external/runtime.py`（`CliRuntimeBase` 统一 member session + `send` 主钩子 + `announce_team_context` 补偿钩子 + `_send_raw` 下沉）；`external/cli_agent/{spawn,claude/runtime,codex/runtime}.py`；`spawn/external_cli_spawn.py`；`agent/coordination/handlers/member.py`（删三份重复实现）；模板 `prompts/{cn,en}/inbound_tags.md`、`teammate_policy_external.md`、`tools/locales/descs/{cn,en}/list_members.md`；删 `prompts/{cn,en}/attachment_notice.md` |
| 测试基线 | `tests/unit_tests/{agent_teams,core/single_agent,harness}/` 4650 passed / 31 skipped |
| Refs | #751 |

## 背景

[[F_46]] 把三片团队状态（`team_identity` / `team_info` / `team_members`）从系统提示词挪进
`prompt_attachment_manager`，理由是"动态状态击穿 KV cache"。[[F_68]] 又把恒定但 per-member 的
`team_identity` 也收进同一条通道。

**但那条理由本身是错的**，这次先把事实说准。attachment 的投递语义是
`PromptAttachmentManager.make_window_mutator` → `inject_messages`：每次模型调用把全部
attachment 渲染成一条 `UserMessage`，**追加到 window 末尾，且不写进会话历史**。追加在尾部
不会让前面任何 token 失效——**attachment 从来没有击穿过前缀 cache**。

真实代价是另一回事：这坨内容**每次模型调用（含同一轮里每次 tool-loop 迭代）都要重新编码、
永远拿不到缓存命中**。而三片里 identity 恒定、`team_info` 近乎恒定（`TeamDao` 至今没有
update 路径）、名册大多数时候也没变——为"可能变"付的是"每次全额重发"的价。

方向因此反过来：这些内容不该待在"用完即弃"的通道里，而应该**写进对话历史**——写一次，
之后永远落在缓存前缀里。这不是新机制，正是 F_46 自己定义的**机制 B**（内嵌 XML 的
user message，写入历史、永久留存）；本次是把当初归错类的内容归位。

## 五条原则

### 原则一：按「数据产生的时刻」插入，不是按「第一次调用」

团队信息不是一开始就存在的。leader **首次调用模型时还没有团队**——`team_info` 和名册要等它
调用 `build_team` 之后才出现。所以没有"开局把恒定信息拍在历史最前面"这回事：每片状态在它
第一次出现（以及此后每次变化）的那一次投递上插入一条消息，探针不变就什么都不做。

### 原则二：插在「本次新增消息」里，且优先塞进 user message 正文最前面

上一次模型调用之后新增的那些消息里：

1. **有 user message** → XML 插到其中**最旧的一条 user message 正文的最前面**；
2. **没有 user message**（tool-loop 中途，新增的只有 assistant + tool result）→ 在 tool
   result **之后追加一条新的 user message** 专门承载。

两种落位都只动"最新那一段"，前面的历史一个 token 都不变——这正是缓存命中的判据。

### 原则三：只插入，不删除，不重写

已写进历史的团队消息永远不动。名册变化不重发全量，只追加一条增量（加入 / 退出 / 信息更新）。
`team_info` 变更同理：追加一条新的，而不是改写旧的——旧那条是"当时的事实"。

### 原则四：投递进度基线必须持久化在成员自己的 session

**rail 每一轮都会被重建**，不只是 resume 时：round 结束 → `finalize_round` →
`harness.stop()` → `NativeHarness` 进 TERMINATED，下次 `start()` 重建 → `RailSpec.build`
重新 `TeamPolicyRail(...)`（`rails/elements.py`，无任何缓存）。基线留内存 = 每轮重发。
pause/resume、stop→start 只是更严重的版本。

### 原则五：名册消息一律是公告，不是行动指令

每条名册消息都带 `<team-note kind="announcement-only">`。少了它，成员一看到"有人加入"就会
礼节性地发一轮问候，白烧一轮 LLM + 一轮邮箱投递，而且会连锁触发对方也回一轮。

## 决策

### D1：抽一个共用的 `TeamContextTracker`（`team_context.py`）

两个方法就是全部对外面：

```python
text = await tracker.pending_text(session)
if text:
    ...把 text 放到该放的位置...
    await tracker.commit(session)      # 只有真的投递出去才推进基线
```

**先投递、后推进基线**是这个两步式唯一 load-bearing 的点：反过来写会在投递失败时永久丢掉
一条公告。**不加锁**——一个成员的 rail `before_model_call`、CLI 的 `send` 与事件补偿都跑在
同一条协程上，没有并发入口。这条前提写在 tracker 的模块 docstring 里，将来真出现并发入口
时才需要重新审视。

三条状态通道，各自有自己的探针与基线字段：

| 通道 | 探针 | 首次 | 之后 |
|---|---|---|---|
| `identity` | 无（构造期已知，恒定） | `<team-context>` 身份段 | 不再发 |
| `team_info` | `get_team_updated_at()` | `<team-context>` 团队信息段 | 探针推进 → 追加一条更新段 |
| `roster` | `get_members_max_updated_at()` | `<team-event kind="roster">` 全量 | `<team-event kind="roster-change">` 增量 |

原来 `team_info` 走 `MtimeSectionCache`、`team_members` 走 rail 内手写探针的不对称，随着两者
收进同一套"探针 + 持久化基线 + diff"一并消失。`prompts/section_cache.py` 作为通用原语保留。

**探针推进但没有东西可说**（名册里只剩自己、或团队行还不存在，见 D1a）时，基线在
`pending_text` 内部直接推进并落盘——没有东西会投递失败，不推进只会让每次调用重复读一遍 DB。

### D1a：团队行不存在时不发 `team_info`（落地后修的首个 bug）

原则一说"数据产生的时刻"，但第一版把这句话交给了渲染函数去判断——而
`build_team_info_text` 只要**任一**字段非空就出正文，`team_workspace_mount` /
`team_workspace_path` 又是 rail 构造参数、恒有值。结果 leader 首轮就收到一条只有工作区路径、
没有 team_name / display_name / 团队目标的"团队信息"，`build_team` 成功后再收一条完整的：
同一件事说两遍，第一遍还是错的。

修法是把闸门放回 tracker：`_team_info_block` 拿到 `get_team_info()` 为 `None` 就直接返回，
只推进探针（团队行缺失时探针读 0，创建后自然变），不产出任何块。渲染函数保持通用——它不知道
"为什么" info 是空的，那是 tracker 的职责。

名册侧同一类问题不存在但值得记一笔：`list_members` 按设计排除调用者本人，所以 `build_team`
只写了 leader 自己那行时名册为空、不产出消息，要等 `spawn_teammate` 才有内容——这是正确行为，
不是漏发。

漏网的原因也记下来：`test_leader_says_nothing_before_the_team_exists` 当时没给 rail 配工作区
路径，而生产里一直有，于是那条断言在"三个字段都空"的场景下空跑通过。补的回归用例
`test_workspace_paths_alone_do_not_announce_a_team` 显式配上路径，撤掉修复即挂。

### D1b：身份带两个名字，且与团队信息合并成一个 `<team-context>`

同一批落地后暴露的另外两处：

- **身份只有 `member_name`**。但 peer 的名册每行都是
  `member_name=X display_name=Y`——成员认不出哪一行是自己，也没法用团队其他人称呼它的方式
  称呼自己。`build_identity_text` 因此补 `display_name`。公开 `desc` **仍然不进**自己的身份
  （S_09 不变量 18a：它只属于别人的名册）。

  **`display_name` 必须读自己那行 member 行，不能用构造期的值**：leader 的构造期值来自
  `LeaderSpec.display_name`（spec 默认，如 `Team Leader`），而真正写进 DB 的是
  `build_team(leader_display_name=...)` 当场传的那个（如「队长」）——拿构造期值等于告诉
  leader 一个团队里没人用的名字。所以 identity 通道**等自己那行存在**才发：teammate 的行在
  spawn 时就有，门槛为零；leader 则在建队后的那次调用上拿到身份，正好与同一刻出现的团队
  信息合并成一个 `<team-context>`。构造期的 `TeamRuntimeContext.display_name` 退化为无
  backend 时（纯静态单测）的兜底。
- **两个相邻的 `<team-context>`**。身份和团队信息都是"关于团队的既成事实"，同一次投递里
  各包一个标签等于把同一类东西说两遍。改为同一批渲染出的正文合并进一个 `<team-context>`；
  它们在**不同**调用上产生时（leader 的身份在首轮、团队信息在建队那轮）自然还是两条消息，
  这无法也不该合并。

### D1c：落位不再靠下标——新增 `ON_USER_MESSAGE` 钩子，在消息进入对话之前处理它

按下标定位本轮起点这条路走不通：**上下文压缩会重写 / 丢弃消息，保存下来的下标随后指向别的
东西**，最坏情况是把团队上下文插到压缩摘要前面。而且要插的本来就只有"原始输入"（新一轮
query / follow-up / steer 进来的消息），不是历史里任意一条 user message。

所以在 core 侧新增一个事件：`AgentCallbackEvent.ON_USER_MESSAGE`
（`UserMessageInputs{message, source}`，`source` ∈ `query` / `steering` / `resume`）。
`ReActAgent` 把三处 `add_messages(UserMessage(...))` 收敛成一个
`_admit_user_message(...)`：先构造消息 → `ctx.fire(ON_USER_MESSAGE)` 让 rail 就地改
`message.content` → 再写进对话。**这是 rail 能把一条输入当作"输入"来处理的唯一时刻**；写进去
之后它就是普通历史，会被压缩搬动，按位置找它不再安全。

`TeamPolicyRail` 因此变成两条通道，都不碰任何既有消息：

- **`on_user_message`（主通道）**：待发内容拼到这条输入正文最前面。零下标、免疫压缩。
- **`before_model_call`（兜底）**：只**追加**，不定位。state 也可能在一轮的 tool loop 中途
  出现——leader 的 `build_team` 正是在中途建队、写自己的 member 行和名册，而下一条输入可能
  很久才来；这时把待发内容作为一条独立消息追加到尾部。尾部是唯一不需要下标、也不会被
  "压缩重写了它前面的历史"影响的位置。

`_seen_message_count` / `_placement_target` / `_round_start_index` 全部删除。

**落地时踩的坑：新事件必须挂进 `DeepAgent` 的路由表**。DeepAgent 有两个 callback-manager
命名空间（自己的、内层 ReActAgent 的），`_register_rail_selective` 按事件把 rail 的每个回调
分派到其中之一。`ON_USER_MESSAGE` 由**内层** agent 触发，却没进 `_BRIDGE_EVENTS`，于是落进
"Unknown rail event → 注册到外层 DeepAgent + 打一条 warning" 的兜底——回调挂在一个永远不会
触发它的 manager 上，`on_user_message` **完全不执行**，团队上下文全靠 `before_model_call`
兜底追加成一条独立消息。功能静默失效，日志里只有一行 warning。

补的回归**针对这一类而不是这一个**：断言
`_BRIDGE_EVENTS | _OUTER_ONLY_EVENTS | _DEEP_EVENTS` **覆盖全部** `AgentCallbackEvent`。那条
兜底分支正是让失败变静默的原因，有了完整性断言，以后新增事件不给路由决策就直接挂测试。

这个坑能溜过去还有测试方法的原因：当时的单测直接调 `rail.on_user_message(ctx)`，验证了
"rail 会正确改写消息"，没验证"rail 会被调用"——**绕过注册测组件，等于没测接线**。

<details>
<summary>被替换掉的三版中间方案（记下来以免后人重走）</summary>

1. **"历史里最后一条 user message"**：一轮攒了广播 + steering 两条输入时会落进 steering 那条。
2. **"最后一条 assistant 之后"**：修好了上一条，但仍然按下标定位，压缩一来就失效。
3. **消息 id 锚点**（`metadata[CONTEXT_MESSAGE_ID_KEY]`）：不再依赖下标，但锚点被压缩抹掉后
   只能退化成"取空段"——等于为一条本来可以不存在的路径养一套失效处理。钩子从根上消掉了
   "事后再去找那条消息"这件事。

</details>

### D2：基线写在成员自己的 child AgentSession

事实链（都已核实）：

- rail 的 `ctx.session` 是 `TeamHarness._make_child_session` 用
  `team_session.create_agent_session(card=native.card, share_stream_writer=False)` 建的
  **该成员自己的 child AgentSession**；
- agent session 的 state 在 checkpoint 里**按 `agent_id` 分桶**
  （`core/session/checkpointer/persistence.py` 的 `AgentStorage._get_entity_id =
  session.agent_id()`，`agent_id` = card.id = `f"{team_name}_{member_name}"`），`pre_run` 恢复；
- **成员的对话历史存在同一个桶里**（`context_engine.save_contexts` →
  `session.update_state({"context": ...})`）。

所以基线与历史**由同一次 `AgentStorage.save` 一起落盘**，天然不会出现"历史里有消息、基线
没跟上"的漂移。持久化结构是单个 key `team_prompt_context`：
`identity_emitted` / `team_info_mtime` / `roster_mtime` / `roster`。`roster` 必须存——增量
diff 要有 old 才算得出。

### D3：进程内成员——两个写入点，rail 不持有任何位置状态

- attachment 通道全部代码删除（`attachment_manager` 字段、`_ATTACHMENT_SOURCE`、
  `_upsert_or_clear`、`_identity_section`、`_info_cache`、`_members_cached_mtime` 手写探针）。
- 写入点见 D1c：`on_user_message` 拼进正在被消费的输入，`before_model_call` 把仍然待发的内容
  追加成一条独立消息。rail 里**没有任何字段记录"上次看到哪"**。
- `content` 是 `str | list[str | dict]`，两种形态都要能接前缀，所以 `prepend_to_content`
  是模块级公开函数（可单测）：`list` 且首元素不是 str 时插一个新块，不硬塞进别人的结构里。

### D4：外部 CLI 成员——同一个 tracker，两个入口，一条投递路径

外部 CLI 成员没有 rail、拿不到对端的内部上下文，只能把 XML 拼进发给它的 user message。
把原 `send` 的方法体下沉为 `_send_raw`，上面架两个入口：

- **主钩子 `send`**（搭车）：`pending_text` 非空就拼到正文最前面，`_send_raw` 成功后
  `commit`。一个钩子覆盖新起 turn / steer / follow_up 三条分支。
- **补偿钩子 `announce_team_context`**（独立公告）：成员变更事件触发，有内容就把它作为
  **一条独立的 user message** 送出去。

`_send_raw` 是唯一真正投递的地方，两个入口都是"投递成功再 commit"，**没有第二条投递路径、
也没有 `skip_check` 之类的开关**——谁先发就谁推进基线，另一条拿到的是空，不会重复。

`CliRuntimeBase` 同时统一开 member AgentSession（codex 原有的 `_ensure_member_session` 上提，
codex 只保留自己那段更严格的 thread-id 恢复 `_restore_thread_id`），`member_agent_id` 对所有
后端可传，基线因此对每种 CLI 后端都能持久化。

`handlers/member.py` 里那套"重建 section + 组 attachment + 全量 steer"（含 F_68 已点名的三份
重复实现 `_build_team_info_section` / `_build_team_members_section` / `_prompt_attachment`）
删干净，`TEAM_CONTEXT_EVENTS` 与 `REFRESH_TEAM_CONTEXT` 两条事件线保留，内容收敛成一句
`runtime.announce_team_context()`。

### D5：删掉 attachment 说明段，标签说明并进 `inbound_tags`

团队侧不再有任何 attachment，`team_attachment_notice` 就是死代码，连同
`build_team_attachment_notice_section` / `TeamSectionName.ATTACHMENT_NOTICE` /
`include_attachment_notice` 参数 / `prompts/{cn,en}/attachment_notice.md` 一并删除。

`inbound_tags.md`（cn/en）补 `<team-context>` 与 `<team-event kind="roster|roster-change">`
的说明——成员能收到的每种标签都必须在说明里点名（F_68 D1 的规矩）。措辞与原
attachment_notice **正好相反**：从"始终以最新一次为准、不是对话历史"改成"写进对话历史、
按时间顺序累积"，并明说名册是"全量 + 增量累积"而不会再重发全量。

harness 通用的 `build_prompt_attachments_section`（P:75）**保留**——memory / skill /
agent_mode 等 rail 仍在用 attachment 通道，那段说明不归团队管。
`build_team_identity_section` 也**保留**：外部 CLI 的系统提示词仍靠
`include_member_specific=True` 内联它（启动期还没有对话可写）。它现在只是
`build_identity_text` 的 `PromptSection` 包装，正文只有一份。

同步刷掉两处过期表述：`teammate_policy_external.md` 的"共享工作空间路径由 `team_info`
attachment 给出"，`tools/locales/descs/*/list_members.md` 的"名册在
`<prompt-attachment type="team_members">` 里实时提供"。

### D6：成员私有工作区并进 identity，jiuwenswarm 那条 attachment 撤掉

`TeamRuntimeContext` 的 workspace root（`agent_configurator` 里的 `workspace_root_path`，
即 `ensure_team_member_workspace_link(team, member)`）作为一行进 identity 正文：

```
你的私有工作区: `/…/workspaces/<member>_workspace`（存放你自己的产物、记忆与技能视图；
团队共享文件走团队共享工作空间，不要放这里，也不要把新 skill 创建到这里）
```

判据和 `member_name` / `display_name` 完全一致——**per-member、spawn 时固定、此后恒定**，
所以它属于同一段正文，而不是另开一条通道。括号里的用途说明一并带上：只给路径，模型分不清
它和团队共享工作空间的分工。

**jiuwenswarm 的 `TeamSkillStoragePolicyRail` 不再管这件事**（撤销 [[F_68]] D4）：删掉
`MEMBER_WORKSPACE_SECTION_NAME` / `_build_member_workspace_section` /
`_sync_member_workspace_attachment` / `attachment_manager` / `ATTACHMENT_SOURCE`，构造参数去掉
`member_workspace_root` 与 `language`（后者只服务于那条 attachment），provider 的
`TeamSkillStoragePolicyInput` 同步瘦身。它的静态 section 只留团队级路径——"成员 skill 目录
不作为新 skill 的创建目标"这条规则本来就是 roster-agnostic 的，留在系统提示词里即可；具体是
哪个目录，成员从自己的 identity 里已经知道。

这样"成员工作区"这件事只有一处产出，不再一个仓库讲一半。

## 拒绝的方案

- **给 `ContextMessageBuffer` 加 `add_front` / 用 `set_messages` 重排历史**（把恒定信息拍在
  历史最前面）：先是被原则一否掉——leader 首次调用时根本没有团队信息可拍。而且
  `set_messages(with_history=True)` 会把 `_history_messages_size` 归零
  （`core/context_engine/context/message_buffer.py`），破坏 `snapshot_rail` 与
  `native_harness` 的 snapshot / rollback 语义；`add_front` 则是为一个不该存在的需求往核心层
  加原语。改用原则二之后，核心层一行都不用动。
- **开局一次性把全量团队状态拍进去**：同上，数据那时还不存在。
- **基线留 rail 内存，靠"没有基线就发全量快照"自愈**：rail 每轮都重建，等于每轮重发；
  pause/resume 后恢复出来的上下文里本来就有那份快照，再发一遍就是信息重复。
- **给 tracker 加 `asyncio.Lock` 防两个入口撞车**：一个成员的所有入口都在同一条协程上，
  没有并发。为不存在的竞态加锁是提前优化。
- **`announce_team_context` 复用公开的 `send` 投递**：`send` 自己的搭车检查会把同一段内容
  再拼一遍，逼得只能"先 commit 再投递"，于是投递失败就永久丢公告。下沉 `_send_raw` 之后
  两个入口都能保持"投递成功再 commit"。
- **给 `send` 加 `skip_team_context` 开关**：同一件事的两种写法，开关就是设计没收敛的信号。
- **外部 CLI 继续走事件触发的独立全量快照**：全量快照每次变更重发一遍，正是本次要消除的
  浪费；而且它重复实现了 rail 侧的 section 构造。
- **外部 CLI 只搭车、不做事件补偿**：一个长时间没有入站消息的 CLI 成员会一直看不到名册变化。
  补偿钩子挂在既有的成员事件线上，不引入轮询。
- **用轮询探测外部 CLI 的团队上下文**：协同路径本来就是事件驱动的，加轮询是把整体设计破掉。
- **把名册增量也塞进 `<team-context>`**：`<team-context>` 是"关于团队的既成事实"，名册变动
  是"发生了什么"，本就是 `<team-event>` 的语义；混用会让"累积"这件事说不清楚。

## 验证

- `test_team_policy_rail.py` 重写动态段落（51 passed）：有 user message 时插正文最前且不新增
  消息；只有 tool result 时末尾新增一条 user message；leader 建队前什么都不插、建队那次才
  插；探针不变的后续调用零产出且只付探针成本；名册变化只发增量且旧消息逐字不变；
  **同一 session 上重建 rail 一条都不插**（本次核心回归），清掉基线才重新发；恢复出来的旧
  历史不被改写；公告 note 在快照与增量上都在，含 cn 的 load-bearing 措辞"不要"。
  另加 `prepend_to_content` 的 str / list / 结构化首块 / 不改原列表四个用例，以及落地后修复的
  回归：`test_workspace_paths_alone_do_not_announce_a_team`（D1a，配上工作区路径后建队
  前不发团队信息、建队后只发一条完整的；撤掉修复即挂）、
  `test_identity_and_team_info_share_one_block` + `test_identity_carries_both_names`（D1b）、
  `test_only_the_first_input_carries_it`（D1c，广播 + steering 两条输入时上下文落在先被消费
  的那条上）、`test_state_appearing_mid_tool_loop_is_appended`（D1c 兜底，leader 建队后追加
  一条独立消息且既有历史逐字不变）。
- 新增 `tests/unit_tests/core/single_agent/rail/test_on_user_message.py`（6 passed）：事件→方法
  映射、只有 override 的 rail 才注册回调、rail 就地改写 `message.content`、三种 `source`
  取值。**注意这一组全部绕过注册**——它只证明 rail 拿到消息会做对的事，不证明 rail 会被调用，
  D1c 那个路由 bug 就是从这个缝里溜过去的。
- 新增 `tests/unit_tests/harness/test_deep_agent_rail_event_routing.py`（3 passed）：
  `_BRIDGE_EVENTS | _OUTER_ONLY_EVENTS | _DEEP_EVENTS` 覆盖全部 `AgentCallbackEvent`、三个集合
  互不相交、内层触发的事件（含 `ON_USER_MESSAGE`）确实在 bridge 集合里。撤掉 D1c 的路由修复
  即挂两条。
- **端到端尚未复验**：以上都是单测。"团队上下文落在本轮第一条 user message 内部"这条要跑真实
  团队看上下文才算数——D1c 的路由 bug 正是"组件全绿、接线断掉"的形态。
- 新增 `prompts/test_team_messages.py`（24 passed）：三种正文的 cn/en、空值返回 `None`、
  workspace mount 与 path-only 两种形态、`diff_roster` 的 joined / left / changed / 空 diff /
  只改 status 不算变更、`mark_humans` 门控、`render_team_context` 转义。
- 新增 `external/test_cli_runtime_team_context.py`（12 passed）：`start` 开 member session、
  无 team_session 时静默降级、`send` 拼在正文最前、第二次 send 不重复、steer 分支同样生效、
  `announce_team_context` 独立成条且无更新时静默、**补偿后紧接 send 不再拼一遍**、
  名册变化发增量、公告 note、**投递失败基线不推进且下次仍会重发**、
  **同一 session 换新 runtime 不重发**。
- `test_team_agent_coordination.py` 的外部成员段落改为断言 runtime 收到的消息（6 passed）。
- `tests/unit_tests/agent_teams/` + `core/single_agent` + `harness`：**4650 passed / 31 skipped**。
- `tests/unit_tests/harness/prompts` + `test_prompt_attachment_manager.py`：73 passed
  （通用 attachment 通道未受影响）。

## 已知遗留

- **外部 CLI 仍比进程内成员慢半拍**：成员变更有事件补偿钩子、能即时公告，但一轮 CLI turn
  **进行中**发生的变更要等这轮结束才送达——CLI SDK 这层感知不到每轮工具调用闭环，做不到
  rail 那种"tool result 后补一条"。`team_info` 变更目前没有对应事件，只能搭车；等
  `TeamDao` 真有 update 路径时再补一条事件即可。
- **成员自己的 cwd / project_root 不在本次范围**：由 harness 的 workspace section
  （`harness/prompts/sections/workspace.py`，P:70）渲染，本来就在**系统提示词**里、不在
  attachment 里，不存在"每轮重发"的浪费。它确实是 per-member 内容、违反 S_09 不变量 6a
  （每个成员各占一份前缀），但要修得动通用 workspace section、影响所有 DeepAgent。
- ~~**jiuwenswarm 的 `team_skill_storage_member_workspace` attachment**（[[F_68]] D4）~~
  已在本次一并收掉，见 D6。
- **共享 card 会让 teammate 共用一个 `agent_id`**：若用户在 `spec.agents["teammate"]` 上设了
  `card`，所有 teammate 塌到同一个 `agent_id`、共用一份 checkpoint。这是既有问题（对话历史
  本身就会串），本次基线只是同样受影响，不在此修。
- **上下文压缩可能压掉团队消息**：session 基线仍在，所以**不会**自动重发。这是有意的——
  重发与"只插不删"打架；真需要时应由压缩路径显式清基线，本次不做。
