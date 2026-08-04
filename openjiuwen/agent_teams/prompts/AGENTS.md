# Agent Team Prompts

Markdown 模板是团队 Agent 的行为契约。Python 侧只做装配（`sections.py` 按 section 构造 `PromptSection`，各 builder 直接 `load_template` 读 `cn/`/`en/` 下的 `.md`），所有文案都在此目录下的 `.md` 文件里 —— 改提示词不需要动 Python。

## Directory Layout

| 路径 | 作用 |
|---|---|
| `__init__.py` | 公开导出：loader、sections、messages、section_cache |
| `loader.py` | `load_template(name, lang)` 加载器，`@cache` 缓存，默认语言 `"cn"` |
| `sections.py` | `TeamSectionName` + `build_team_*_section` 构造 `PromptSection`（系统提示词的唯一装配路径，由 `TeamPolicyRail` / `build_team_member_system_prompt` 消费）；`build_team_role_section` 直接 `load_template` 读 `leader_policy` / `teammate_policy` |
| `messages.py` | 团队状态的**消息**正文：`build_identity_text` / `build_team_info_text` / `build_roster_snapshot_text` / `build_roster_delta_text` + `diff_roster` + `format_member_line` + `labels_for`。纯渲染，投递时机与基线归 `agent_teams/team_context.py`。见 [[F_70]] |
| `section_cache.py` | `MtimeSectionCache`：通用 mtime 缓存原语（团队侧当前无使用者）|
| `cn/` · `en/` | 语言相关的角色 / 工作流 / 生命周期模板，由 `load_template` 加载 |

所有模板都是语言相关的，**必须 cn/en 成对存在**。新增语言只需增加对应子目录。

## Template Catalogue（每种语言下必须齐备）

| 模板文件 | 触发条件 | 装配位置 | 主要内容 |
|---|---|---|---|
| （无模板，Python 装配） | `member_name` 或 `member_prompt` 非空 | `messages.build_identity_text` → `build_team_identity_section` | **唯一的 per-member 内容**：自身 `member_name` 一行 + `## 私有工作约定` 子节（`member_prompt` 为空则只出名字）。正文只有一份（`build_identity_text`），section 只是它给外部 CLI 用的包装（P:10，`include_member_specific=True` 内联进静态 prompt）；**进程内成员经对话历史收到它**（`<team-context>`），留在系统提示词会让每个成员各占一份前缀 KV cache。见 [[F_68]] / [[F_70]] |
| `leader_policy.md` | `role == LEADER` | `build_team_role_section` | Leader 的核心理念、协作机制选择（按任务协同性质：结构可确定性编排 → swarmflow；涌现式自主协同 → build_team）、职责、成果交接（通道由内容形态决定：短内容直接进消息正文，成型产物落盘、消息只传路径）、决策原则（禁止自执行 / 背景不清先建调研成员 / 无人认领时指派或 spawn / 整合总结交独立汇总成员）、响应节奏、任务状态流转 |
| `teammate_policy.md` | `role == TEAMMATE` | `build_team_role_section` | Teammate 的自主规划/领取/协作规范、通信协议、代码/文件协作约定 |
| `leader_workflow.md` | Leader 且 `team_mode="default"` | `build_team_workflow_section` | 常规 Leader 工作流：建队 → 建任务 → spawn 成员 → 广播启动 → 等通知 |
| `leader_workflow_predefined.md` | Leader 且 `team_mode="predefined"` | `build_team_workflow_section` | 预定义团队工作流：禁止 `spawn_teammate` 等 spawn 工具，成员已预先注册 |
| `leader_workflow_hybrid.md` | Leader 且 `team_mode="hybrid"` | `build_team_workflow_section` | 混合团队工作流：预注册基础成员 + 允许动态 `spawn_teammate` 扩员 |
| `dispatch_autonomous_leader.md` · `dispatch_autonomous_teammate.md` | `dispatch_mode="autonomous"`（默认），按角色挑版 | `build_team_dispatch_section` | 任务经公共看板自主认领，也可在 `create_task` 时预指派给已存在的非 leader 成员；Leader 用 `send_message(to="*")` 广播启动；Teammate 用 `claim_task` 领取 / 启动指派给自己的任务 / 完成。`human_agent` 无 `claim_task`、需 Leader 显式指派的说明也在 leader 版里 |
| `dispatch_scheduled_leader.md` · `dispatch_scheduled_teammate.md` | `dispatch_mode="scheduled"`，按角色挑版 | `build_team_dispatch_section` | 任务由 Leader 直接指派：`create_task` 必带 assignee（已存在且非 leader）、成员先于任务存在、**不广播启动**（调度框架自动通知并拉起）；Teammate 不自主认领，用 `member_complete_task` 完成 |
| `lifecycle_persistent.md` | Leader 且 `lifecycle="persistent"` | `build_team_lifecycle_section` | 长期团队收尾语义（完成任务后待命，不解散） |
| `lifecycle_temporary.md` | Leader 且 `lifecycle="temporary"`（默认） | `build_team_lifecycle_section` | 临时团队收尾语义（shutdown → clean_team） |
| `inbound_tags.md` | 常驻（每个成员，含外部 CLI） | `build_team_inbound_tags_section` | 入站消息与团队状态的 XML 标签体系（`<team-inbound>` / `<team-note>` / `<team-event>` / `<team-context>`、**`<team-note>` 嵌在被修饰块内部**见 [[F_72]]、`for="controller"`、名册的 `kind="roster"` 全量 + `kind="roster-change"` 增量**累积**语义，见 [[F_70]]）+ **`from="user"` 的角色定义**（团队外部真人 / 不在名册 / 无 agent 进程 / 不可派任务）。user 的身份是角色中立事实故放这里一处覆盖全角色；**怎么答复 user 由各自角色契约管**（teammate 必须 `send_message(to="user")`，leader 纯文本直达），此处不写。进程内成员与外部 CLI（`read_inbox`）都渲染这套 XML |
| `hitt_leader.md` / `hitt_teammate.md` / `hitt_teammate_anonymous.md` / `hitt_human_agent.md` | `hitt_enabled` 且角色命中 | `build_team_hitt_section`（→ system prompt builder，静态） | HITT **静态协作契约**（规则），按角色分四版（`_hitt_template_name` 挑版），roster-agnostic 不列名字——人类成员在名册消息里标 `[human]`；仅 `hitt_human_agent` 用 `{{self_line}}` 注入自身名字行。gate 用 `hitt_enabled`（capability flag），HITT 一开启即 present、无需先 spawn 人类。见 [[F_52]] |
| `bridge_agent.md` | `role == BRIDGE_AGENT`（bridge avatar 本人） | `build_team_bridge_section` | Bridge avatar **自契约**（调度语义）。bridge 成员在别人眼里就是普通 teammate（名册消息里不加标记，无 peer 向说明段），只有 avatar 本人拿这段；`{{self_line}}` 注入自身名字行。见 [[F_52]] |

Teammate 不消费 workflow / lifecycle 模板；`sections.py` 在 `role != LEADER` 时对这两个 section 直接返回 None。

**`dispatch_mode` 与 `team_mode` 正交**——`team_mode` 决定名册能否生长（能否 spawn），`dispatch_mode` 决定任务如何到达执行它的成员。两者各自独立成 section，模板数是 `3 + 2` 而非 `3 × 2`；再加一维仍是加法。因此 **workflow 模板不许写"怎么让成员开工"**（那是 dispatch 的职责），**dispatch 模板不许写"能不能 spawn"**（那是 workflow 的职责）。同理，`leader_policy.md` / `teammate_policy.md` 全模式共用，**不许出现任何模式专属的动作指令或例外**（如"预定义模式下无法 spawn"）——`tests/unit_tests/agent_teams/test_predefined_team.py` 会断言 default 模式的提示词里不含 `预定义团队模式` 字样。`dispatch_autonomous_*` 只在 LEADER / TEAMMATE 上渲染：`HUMAN_AGENT` 的"等指派"契约已在 `hitt_human_agent.md` 里，`BRIDGE_AGENT` 不承接看板任务，两者 `build_team_dispatch_section` 返回 None。HITT 模板在 `hitt_enabled` 时按角色挑版（见 `_hitt_template_name`）；Bridge 只有 `bridge_agent`（avatar 自契约），仅 `role == BRIDGE_AGENT` 时出现。

## 编辑规则（Hard Constraints）

1. **cn / en 双语对齐** — 任何语义变更必须同步修改两种语言文件。结构、小节顺序、字段名保持一致，只翻译文本。
2. **动态值走 `{{name}}` 占位符** — 只有两个模板含占位符：`bridge_agent` 与 `hitt_human_agent`，都只用 `{{self_line}}`（"你的 member_name 是 X"这一自身名字行）。占位符用 `PromptTemplate` 默认的 `{{ }}` 定界符，由 builder 调 `load_template(...).format({"self_line": ...})` 渲染；`self_line` 由 `_self_member_line` 生成，builder **仅在 HUMAN_AGENT / BRIDGE_AGENT 时才算**（这两个角色单例，不构成前缀 cache 放大）。其余模板（policy / workflow / lifecycle / inbound_tags / `hitt_leader`・`hitt_teammate`・`hitt_teammate_anonymous`）纯静态，`load_template` 原样返回。**不要往这些静态模板里加 per-member 变量**——普通 teammate 的自身名字走 `<team-context>` 消息，见 S_09 不变量 6a / 13。
3. **`@cache` 基于 `(name, language)`** — 运行中的进程不会感知文件改动。开发时如需热更新，重启进程或清 `_load.cache_clear()`。
4. **空分节省略而不是空字符串** — 新增可选章节时，参考 `build_team_workflow_section` / `build_team_lifecycle_section` 的 None 处理方式（`sections.py` 在 `role != LEADER` 时直接返回 None）。**不要在 `.md` 里写占位文字**。
5. **策略分层不要重复写** — `leader_policy.md` 谈"角色身份/决策原则"，`leader_workflow.md` 谈"操作步骤"，`tools/locales/descs/*.md` 谈"工具使用语义"。三层内容互不重叠（参见 `agent_teams/tools/AGENTS.md` 的 Prompt Layering 章节）。
6. **排版风格** — 顶层用 `##`（因为外层 Rail 已经提供 `#` 级标题），列表/代码块保持紧凑。避免使用 emoji 装饰。

## Runtime Assembly 路径

唯一装配入口是 `sections.build_team_*_section`：每个模板独立产出一个 `PromptSection`，由 `agent_teams/rails/team_policy_rail.py` 的 `TeamPolicyRail` 按优先级合并进 `SystemPromptBuilder`（外部 CLI 成员则经 `build_team_member_system_prompt` 渲染成独立字符串）。各 builder 直接 `load_template` 读对应 `.md`（如 `build_team_role_section` 读 `leader_policy` / `teammate_policy`）。

**团队状态（自身身份 / 团队信息 / 成员名册）不走这条路**：它既不是 section 也不是 attachment，正文由 `messages.py` 渲染、由 `agent_teams/team_context.py` 的 `TeamContextTracker` 在数据第一次出现或发生变化的那次调用写进成员的对话历史（只插不删）。见 [[F_70]] 与 S_09 不变量 13。

（早期还有一条 `policy.build_system_prompt` + `system_prompt.md` 壳模板的老装配路径，仅测试在用，已随 desc/prompt 归一一并移除。）
