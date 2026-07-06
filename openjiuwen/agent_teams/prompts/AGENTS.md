# Agent Team Prompts

Markdown 模板是团队 Agent 的行为契约。Python 侧只做装配（`sections.py` 按 section 构造 `PromptSection`，各 builder 直接 `load_template` 读 `cn/`/`en/` 下的 `.md`），所有文案都在此目录下的 `.md` 文件里 —— 改提示词不需要动 Python。

## Directory Layout

| 路径 | 作用 |
|---|---|
| `__init__.py` | 公开导出：loader、policy、sections、section_cache |
| `loader.py` | `load_template(name, lang)` 加载器，`@cache` 缓存，默认语言 `"cn"` |
| `sections.py` | `TeamSectionName` + `build_team_*_section` 构造 `PromptSection`（唯一装配路径，由 `TeamPolicyRail` / `build_team_member_system_prompt` 消费）；`build_team_role_section` 直接 `load_template` 读 `leader_policy` / `teammate_policy` |
| `section_cache.py` | `MtimeSectionCache`：dynamic section 的 mtime 缓存原语 |
| `cn/` · `en/` | 语言相关的角色 / 工作流 / 生命周期模板，由 `load_template` 加载 |

所有模板都是语言相关的，**必须 cn/en 成对存在**。新增语言只需增加对应子目录。

## Template Catalogue（每种语言下必须齐备）

| 模板文件 | 触发条件 | 装配位置 | 主要内容 |
|---|---|---|---|
| `leader_policy.md` | `role == LEADER` | `build_team_role_section` | Leader 的核心理念、协作机制选择（按任务协同性质：结构可确定性编排 → swarmflow；涌现式自主协同 → build_team）、职责、决策原则、响应节奏、任务状态流转 |
| `teammate_policy.md` | `role == TEAMMATE` | `build_team_role_section` | Teammate 的自主规划/领取/协作规范、通信协议、代码/文件协作约定 |
| `leader_workflow.md` | Leader 且 `team_mode="default"` | `build_team_workflow_section` | 常规 Leader 工作流：建队 → 建任务 → spawn 成员 → 广播启动 → 等通知 |
| `leader_workflow_predefined.md` | Leader 且 `team_mode="predefined"` | `build_team_workflow_section` | 预定义团队工作流：禁止 `spawn_teammate` 等 spawn 工具，成员已预先注册 |
| `leader_workflow_hybrid.md` | Leader 且 `team_mode="hybrid"` | `build_team_workflow_section` | 混合团队工作流：预注册基础成员 + 允许动态 `spawn_teammate` 扩员 |
| `lifecycle_persistent.md` | Leader 且 `lifecycle="persistent"` | `build_team_lifecycle_section` | 长期团队收尾语义（完成任务后待命，不解散） |
| `lifecycle_temporary.md` | Leader 且 `lifecycle="temporary"`（默认） | `build_team_lifecycle_section` | 临时团队收尾语义（shutdown → clean_team） |
| `attachment_notice.md` | 进程内成员（有 attachment 通道） | `build_team_attachment_notice_section` | 团队动态状态说明：成员名册 / 团队信息 / 人类成员名单以 `<prompt-attachment>`（type=`team_members`/`team_info`/`team_hitt_roster`）挂在消息尾部逐轮刷新；HITT 协作规则在系统提示词里、稳定不变 |
| `inbound_tags.md` | 常驻（每个成员，含外部 CLI） | `build_team_inbound_tags_section` | 入站消息 XML 标签体系（`<team-inbound>` / `<team-note>` / `<team-event>`、`for="controller"`）。进程内成员与外部 CLI（`read_inbox`）都渲染这套 XML |
| `hitt_leader.md` / `hitt_teammate.md` / `hitt_teammate_anonymous.md` / `hitt_human_agent.md` | 存在 human_agent 成员，且角色命中 | `build_team_hitt_contract_section`（→ system prompt builder）；名册另由 `build_team_hitt_roster_section` 出 `team_hitt_roster`（→ attachment） | HITT **静态协作契约**，按角色分四版（`_hitt_template_name` 挑版），roster-agnostic 不列名字；只 `{{peers}}` 注入自身名字。人类成员名册由 `_format_human_agent_roster` 生成单独的 `team_hitt_roster` 段。组合入口 `build_team_hitt_section`（契约+名册拼一段）仅供外部 CLI 成员 / 测试。见 [[F_50]] |
| `bridge_leader.md` / `bridge_teammate.md` / `bridge_agent.md` | 存在 bridge_agent 成员，且角色命中 | `build_team_bridge_section` | Bridge 协作规则，按角色分三版；`{{roster}}` 注入桥接成员名册，`{{peers}}` 注入自身名字 |

Teammate 不消费 workflow / lifecycle 模板；`sections.py` 在 `role != LEADER` 时对这两个 section 直接返回 None。HITT / Bridge 模板仅在存在对应成员时按角色挑选（见 `_hitt_template_name` / `_bridge_template_name`）。

## 编辑规则（Hard Constraints）

1. **cn / en 双语对齐** — 任何语义变更必须同步修改两种语言文件。结构、小节顺序、字段名保持一致，只翻译文本。
2. **动态值走 `{{name}}` 占位符** — Bridge 模板用 `{{roster}}`（桥接成员名册）+ `{{peers}}`（自身名字）；HITT 契约模板只用 `{{peers}}`（人类成员名册已拆成独立的 `team_hitt_roster` 段，不再内联进契约）。占位符用 `PromptTemplate` 默认的 `{{ }}` 定界符，由 builder 调 `load_template(...).format({...})` 渲染；名册 / 自身名字这类动态行由 Python 侧的 `_format_*_roster` / `_self_member_line` 生成后注入。纯静态模板（policy / workflow / lifecycle / attachment_notice / inbound_tags / HITT anonymous 等）不含占位符，`load_template` 原样返回。
3. **`@cache` 基于 `(name, language)`** — 运行中的进程不会感知文件改动。开发时如需热更新，重启进程或清 `_load.cache_clear()`。
4. **空分节省略而不是空字符串** — 新增可选章节时，参考 `build_team_workflow_section` / `build_team_lifecycle_section` 的 None 处理方式（`sections.py` 在 `role != LEADER` 时直接返回 None）。**不要在 `.md` 里写占位文字**。
5. **策略分层不要重复写** — `leader_policy.md` 谈"角色身份/决策原则"，`leader_workflow.md` 谈"操作步骤"，`tools/locales/descs/*.md` 谈"工具使用语义"。三层内容互不重叠（参见 `agent_teams/tools/AGENTS.md` 的 Prompt Layering 章节）。
6. **排版风格** — 顶层用 `##`（因为外层 Rail 已经提供 `#` 级标题），列表/代码块保持紧凑。避免使用 emoji 装饰。

## Runtime Assembly 路径

唯一装配入口是 `sections.build_team_*_section`：每个模板独立产出一个 `PromptSection`，由 `agent_teams/rails/team_policy_rail.py` 的 `TeamPolicyRail` 按优先级合并进 `SystemPromptBuilder`（外部 CLI 成员则经 `build_team_member_system_prompt` 渲染成独立字符串）。各 builder 直接 `load_template` 读对应 `.md`（如 `build_team_role_section` 读 `leader_policy` / `teammate_policy`）。

（早期还有一条 `policy.build_system_prompt` + `system_prompt.md` 壳模板的老装配路径，仅测试在用，已随 desc/prompt 归一一并移除。）
