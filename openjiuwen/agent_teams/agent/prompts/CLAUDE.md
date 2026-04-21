# Agent Team Prompts

Markdown 模板是团队 Agent 的行为契约。Python 侧只做装配（`policy.py` / `team_rail.py`），所有文案都在此目录下的 `.md` 文件里 —— 改提示词不需要动 Python。

## Directory Layout

| 路径 | 作用 |
|---|---|
| `__init__.py` | `load_template(name, lang)` / `load_shared_template(name)` 加载器，`@cache` 缓存，默认语言 `"cn"` |
| `system_prompt.md` | 语言无关的装配模板，仅含 `{{placeholder}}` 占位符，由 `load_shared_template` 加载 |
| `cn/` · `en/` | 语言相关的角色/工作流/生命周期模板，由 `load_template` 加载 |

两种加载路径决定了文件落位：**语言无关模板放在根目录**（目前只有 `system_prompt.md`），**语言相关模板必须 cn/en 成对存在**。新增语言只需增加对应子目录。

## Template Catalogue（每种语言下必须齐备）

| 模板文件 | 触发条件 | 装配位置 | 主要内容 |
|---|---|---|---|
| `leader_policy.md` | `role == LEADER` | `role_policy` | Leader 的核心理念、职责、决策原则、响应节奏、任务状态流转 |
| `teammate_policy.md` | `role == TEAMMATE` | `role_policy` | Teammate 的自主规划/领取/协作规范、通信协议、代码/文件协作约定 |
| `leader_workflow.md` | Leader 且 **非** 预定义团队 | `workflow_section` | 常规 Leader 工作流：建队 → 建任务 → spawn 成员 → 广播启动 → 等通知 |
| `leader_workflow_predefined.md` | Leader 且 `predefined_team=True` | `workflow_section` | 预定义团队工作流：禁止 `spawn_member`，成员已预先注册 |
| `lifecycle_persistent.md` | Leader 且 `lifecycle="persistent"` | `lifecycle_section` | 长期团队收尾语义（完成任务后待命，不解散） |
| `lifecycle_temporary.md` | Leader 且 `lifecycle="temporary"`（默认） | `lifecycle_section` | 临时团队收尾语义（shutdown → clean_team） |

Teammate 不消费 workflow / lifecycle 模板；`policy.py` 与 `team_rail.py` 都在 `role != LEADER` 时直接返回空 section。

## system_prompt.md 占位符（装配契约）

`system_prompt.md` 的占位符由 `policy._build_team_policy` 填入，是模板与 Python 之间唯一的耦合点。改占位符必须同步改 `policy.py`：

| 占位符 | 填充源 | 语义 |
|---|---|---|
| `{{member_name_section}}` | `member_name` 非空时渲染 | 当前成员的内部标识（member_name） |
| `{{role_policy}}` | `leader_policy.md` / `teammate_policy.md` | 角色核心策略 |
| `{{workflow_section}}` | Leader 才注入，非 Leader 为空串 | 工作流程（预定义团队 / 常规） |
| `{{lifecycle_section}}` | Leader 才注入 | 生命周期收尾 |
| `{{persona_label}}` · `{{persona}}` | i18n label + 传入 persona | 角色人设 |
| `{{team_info_section}}` | `_format_team_info` 结果 | 团队名/显示名/目标描述 |
| `{{team_members_section}}` | `_format_team_members` 结果 | 成员花名册（排除自身） |
| `{{base_prompt_section}}` | 用户自定义追加内容 | 末尾附加的扩展指令 |

> 注意：`team_rail.py` 使用独立的 Section 分层（`build_team_policy_section` / `build_team_workflow_section` / `build_team_lifecycle_section`）直接读取同一批 `.md` 文件，并不通过 `system_prompt.md` 装配。改模板正文不影响；改占位符只影响 `policy.py` 那条装配路径。

## 编辑规则（Hard Constraints）

1. **cn / en 双语对齐** — 任何语义变更必须同步修改两种语言文件。结构、小节顺序、字段名保持一致，只翻译文本。
2. **不要在 `.md` 里写 Python 花括号** — 模板只认 `{{placeholder}}` 双花括号（`PromptTemplate.format`）。单花括号会被当作字面量，双花括号才是占位符。目前只有 `system_prompt.md` 需要占位符。
3. **`@cache` 基于 `(name, language)`** — 运行中的进程不会感知文件改动。开发时如需热更新，重启进程或清 `_load.cache_clear()`。
4. **空分节省略而不是空字符串** — 新增可选章节时，参考 `workflow_section` / `lifecycle_section` 的 None 处理方式（`policy.py:114-122`, `team_rail.py:201-211`）。**不要在 `.md` 里写占位文字**。
5. **策略分层不要重复写** — `leader_policy.md` 谈"角色身份/决策原则"，`leader_workflow.md` 谈"操作步骤"，`tools/locales/descs/*.md` 谈"工具使用语义"。三层内容互不重叠（参见 `agent_teams/tools/CLAUDE.md` 的 Prompt Layering 章节）。
6. **排版风格** — 顶层用 `##`（因为外层 Rail 已经提供 `#` 级标题），列表/代码块保持紧凑。避免使用 emoji 装饰。

## Runtime Assembly 路径

有两个装配入口，两者都会消费本目录下的模板：

- **`policy.build_system_prompt`**（老路径） — 使用 `system_prompt.md` 作为壳模板，一次性拼成完整 system prompt。
- **`team_rail.build_team_*_section`**（Rail 路径） — 每个模板独立产出一个 `PromptSection`，由 Rail 按优先级合并。这是当前的主力路径。

两条路径读的是同一批 `.md`，因此**对模板正文的修改会同时生效**。只有占位符/章节拆分这类结构性变更，需要关注落到哪条路径上。
