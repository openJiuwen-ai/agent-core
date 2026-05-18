# spawn_member — Public / Private Visibility Contract in Tool Description

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-16 |
| 范围 | `openjiuwen/agent_teams/tools/locales/descs/cn/spawn_member.md`、`openjiuwen/agent_teams/tools/locales/descs/en/spawn_member.md`、`openjiuwen/agent_teams/tools/locales/cn.py`、`openjiuwen/agent_teams/tools/locales/en.py` |
| 测试基线 | 仅文案改动，未触发逻辑路径变更；`make check` 通过（仅 lint 改动文件） |
| Refs | `#751` |

## 背景

`spawn_member` 工具的输入字段在信息暴露面上是**异质**的——但当前工具描述把它们当作
对称参数列出，没有告知 leader 哪些字段会被全员看到、哪些只属于该成员自己：

| 字段 | 实际暴露面 |
|---|---|
| `member_name` / `display_name` / `desc` | 进 `TeamMember` 表 → 由 `build_team_members_section`（`prompts/sections.py:620`）拼成 `# 成员关系` 注入所有 peer 的 system prompt；也由 `list_members` 工具返回（`tools/team_tools.py:538-567`，`map_result` 至少返回 `member_name` / `display_name` / `status`，`data` 返回完整 `model_dump()`） |
| `prompt` | 仅作为该成员启动时的人设/约定写入该成员自己的 system prompt，不出现在 `# 成员关系` section、不出现在 `list_members` 输出 |
| `model_name` | 内部 allocator hint，不进入任何 LLM 上下文 |

实际后果：leader 在 `desc` 里写"对该成员的内部考量""跨成员策略对照"等本应私密的内容
时，这些信息会随 `# 成员关系` section 进入每个 peer 的 system prompt——LLM 在不知情
的情况下泄漏。`prompt` 才是装这些私密叮嘱的正确位置，但旧文档没说清，leader 没有
信号去做这件事。

## 决策

1. **在 `spawn_member._desc`（`descs/<lang>/spawn_member.md`）的参数表里新增 "可见性 / Visibility" 列**。
   每个参数标 `公开` / `私有` / `内部`：
   - `member_name` / `display_name` / `desc` → `公开`
   - `prompt` → `私有`
   - `role_type` / `model_name` → `内部`（`role_type` 决定 framework 装配方式，不进入任何成员的 prompt 文本；F_18 默认对 teammate 也隐藏）
2. **在 `_desc` 增加独立的「信息可见性边界」小节**，明确：
   - 公开字段会注入到 peer 的 system prompt 与 `list_members`；
   - 私有字段只注入该成员自己；
   - 列出 leader 不应写到 `desc` / `display_name` 里的典型私密内容（内部考量、隐藏约束、机密策略），引导到 `prompt`。
3. **在 `locales/<lang>.py` 各参数描述前缀 `[公开]` / `[私有]` / `[内部]`** 并同时把 visibility 描述写进短文。这一层是 LLM 在工具入参 JSON schema 中直接看到的 description，必须自带可见性信号——而不是依赖它去读完整的 `_desc` Markdown。
4. **`spawn_member.prompt` 短描述从"成员启动时收到的首条指令"改写为"成员的长期工作约定"** —— 与 `_desc` 中"desc/prompt 都是长期内容"的契约对齐。旧描述误导 LLM 写空泛启动语句，新描述明示这是长期人设约束 + 私密叮嘱的载体。

## 拒绝的方案

- **在 `SpawnMemberTool.invoke` 里加运行时校验,拒绝看起来私密的 `desc`**：拒绝。
  "私密"是语义判断,不是字符串判断;启发式（关键字黑名单 / 长度阈值）只会产生
  误判,不会增加真实保护。这是 LLM 行为约束类问题,正确做法是把契约写在
  `prompt` 描述里让模型遵守,不是在工具入口塞规则引擎。
- **拆字段：为"私密人设"新增独立参数**：拒绝。`prompt` 字段就是为这个语义而生
  （已注入该成员自己的 system prompt），新增字段等于绕开既有契约重新发明轮子，
  既没解决问题（leader 仍会不知道该用哪个字段），也违反 `tools/CLAUDE.md` 的
  "不要拆只差一个参数的工具"原则的同源思路。
- **把 visibility 标记下沉成结构化字段**（如 `input_params.x-visibility=public`）：拒绝。
  当前 LLM 只读 `description` 字符串渲染的 JSON schema；额外字段不会进入 prompt，
  对模型零效果。如果未来引入支持自定义 schema 注解的渲染层再补。
- **改成员关系 section,屏蔽 `desc` 字段不再外露**：拒绝。`desc` 的设计意图就是
  "peer 用来判断该向谁派任务的角色介绍",屏蔽它会让 `# 成员关系` section 失去
  作用。问题是"leader 写错了内容",不是"channel 不该存在"。

## 验证

- `ruff check` on `cn.py` / `en.py`：All checks passed.
- Runtime check：
  ```python
  from openjiuwen.agent_teams.tools.locales import make_translator
  for lang in ('cn', 'en'):
      t = make_translator(lang)
      t('spawn_member')               # _desc renders
      t('spawn_member', 'desc')       # 带 [公开] / [PUBLIC] 前缀
      t('spawn_member', 'prompt')     # 带 [私有] / [PRIVATE] 前缀
      t('spawn_member', 'display_name')
  ```
  全部通过，前缀正确，与 Markdown `_desc` 的「信息可见性边界」一节语义一致。
- 测试影响：`grep` 全仓未见任何测试硬编码这些参数描述字符串，文案变更不破坏现
  有断言。

## 已知遗留

- 其它会被 peer 看到的字段（如 `build_team` 的 `display_name` / `team_desc`、
  `create_task` 的 `title` / `content`）也未在描述里标 visibility。本次只动
  `spawn_member` 是因为它是泄漏面最严重的入口（一次性写入长期生效，且 leader
  天然会在这里思考"对成员的定位"）。其它入口的对称改造留到下次触发点出现。
- `list_members` 工具的 `data` 段返回完整 `TeamMember.model_dump()`，包含
  `prompt` 字段。当前 `map_result` 只渲染 `member_name` / `display_name` /
  `status`，因此 LLM 看不到 `prompt`；但调用方若直接读 `ToolOutput.data` 即可
  拿到。短期内不构成 LLM-side 暴露，但严格意义上 `prompt` 也应在 backend 层从
  `list_members` 的返回里裁掉——留作 follow-up。
