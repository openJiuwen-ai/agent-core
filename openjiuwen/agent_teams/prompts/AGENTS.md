# Agent Team Prompts

Markdown 模板是团队 Agent 的行为契约。Python 侧只做装配（`sections.py` 按 section 构造 `PromptSection`，`policy.py` 只加载 role policy 文本），所有文案都在此目录下的 `.md` 文件里 —— 改提示词不需要动 Python。

## Directory Layout

| 路径 | 作用 |
|---|---|
| `__init__.py` | 公开导出：loader、policy、sections、section_cache |
| `loader.py` | `load_template(name, lang)` / `load_shared_template(name)` 加载器，`@cache` 缓存，默认语言 `"cn"` |
| `policy.py` | `role_policy`：按角色加载 `leader_policy` / `teammate_policy` markdown（sections 的 role slice 消费它） |
| `sections.py` | `TeamSectionName` + `build_team_*_section` 构造 `PromptSection`（唯一装配路径，由 `TeamPolicyRail` / `build_team_member_system_prompt` 消费） |
| `section_cache.py` | `MtimeSectionCache`：dynamic section 的 mtime 缓存原语 |
| `cn/` · `en/` | 语言相关的角色 / 工作流 / 生命周期模板，由 `load_template` 加载 |

所有模板都是语言相关的，**必须 cn/en 成对存在**。新增语言只需增加对应子目录。

## Template Catalogue（每种语言下必须齐备）

| 模板文件 | 触发条件 | 装配位置 | 主要内容 |
|---|---|---|---|
| `leader_policy.md` | `role == LEADER` | `role_policy` | Leader 的核心理念、协作机制选择（按任务协同性质：结构可确定性编排 → swarmflow；涌现式自主协同 → build_team）、职责、决策原则、响应节奏、任务状态流转 |
| `teammate_policy.md` | `role == TEAMMATE` | `role_policy` | Teammate 的自主规划/领取/协作规范、通信协议、代码/文件协作约定 |
| `leader_workflow.md` | Leader 且 `team_mode="default"` | `build_team_workflow_section` | 常规 Leader 工作流：建队 → 建任务 → spawn 成员 → 广播启动 → 等通知 |
| `leader_workflow_predefined.md` | Leader 且 `team_mode="predefined"` | `build_team_workflow_section` | 预定义团队工作流：禁止 `spawn_teammate` 等 spawn 工具，成员已预先注册 |
| `leader_workflow_hybrid.md` | Leader 且 `team_mode="hybrid"` | `build_team_workflow_section` | 混合团队工作流：预注册基础成员 + 允许动态 `spawn_teammate` 扩员 |
| `lifecycle_persistent.md` | Leader 且 `lifecycle="persistent"` | `build_team_lifecycle_section` | 长期团队收尾语义（完成任务后待命，不解散） |
| `lifecycle_temporary.md` | Leader 且 `lifecycle="temporary"`（默认） | `build_team_lifecycle_section` | 临时团队收尾语义（shutdown → clean_team） |

Teammate 不消费 workflow / lifecycle 模板；`sections.py` 在 `role != LEADER` 时对这两个 section 直接返回 None。

## 编辑规则（Hard Constraints）

1. **cn / en 双语对齐** — 任何语义变更必须同步修改两种语言文件。结构、小节顺序、字段名保持一致，只翻译文本。
2. **`.md` 模板是纯文本** — `cn/` `en/` 下的模板不含占位符，`load_template` 原样返回内容。（占位符壳模板 `system_prompt.md` 已随 legacy `build_system_prompt` 一并移除。）
3. **`@cache` 基于 `(name, language)`** — 运行中的进程不会感知文件改动。开发时如需热更新，重启进程或清 `_load.cache_clear()`。
4. **空分节省略而不是空字符串** — 新增可选章节时，参考 `build_team_workflow_section` / `build_team_lifecycle_section` 的 None 处理方式（`sections.py` 在 `role != LEADER` 时直接返回 None）。**不要在 `.md` 里写占位文字**。
5. **策略分层不要重复写** — `leader_policy.md` 谈"角色身份/决策原则"，`leader_workflow.md` 谈"操作步骤"，`tools/locales/descs/*.md` 谈"工具使用语义"。三层内容互不重叠（参见 `agent_teams/tools/AGENTS.md` 的 Prompt Layering 章节）。
6. **排版风格** — 顶层用 `##`（因为外层 Rail 已经提供 `#` 级标题），列表/代码块保持紧凑。避免使用 emoji 装饰。

## Runtime Assembly 路径

唯一装配入口是 `sections.build_team_*_section`：每个模板独立产出一个 `PromptSection`，由 `agent_teams/rails/team_policy_rail.py` 的 `TeamPolicyRail` 按优先级合并进 `SystemPromptBuilder`（外部 CLI 成员则经 `build_team_member_system_prompt` 渲染成独立字符串）。`policy.role_policy` 只负责加载 `leader_policy` / `teammate_policy` markdown，供 role section 消费。

（早期还有一条 `policy.build_system_prompt` + `system_prompt.md` 壳模板的老装配路径，仅测试在用，已随 desc/prompt 归一一并移除。）
