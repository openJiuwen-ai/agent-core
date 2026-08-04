# per-member 内容移出系统提示词前缀（team_identity attachment）

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-07-28 |
| 范围 | `prompts/sections.py`（新增 `TeamSectionName.IDENTITY` + `build_team_identity_section`；`build_team_role_section` 去 `member_name` 参数；`build_team_static_sections` 加 `include_member_specific`；删 `build_team_private_prompt_section` / `TeamSectionName.PRIVATE_PROMPT`，私有工作约定并入 identity section；`build_team_member_system_prompt` 传 `include_member_specific=True`）；`prompts/{cn,en}/attachment_notice.md`（列出 `team_identity` type）；`prompts/__init__.py`（导出）；`rails/team_policy_rail.py`（`_identity_section` 走 `_sync_dynamic_sections` 挂 attachment）；`docs/specs/S_09`（新增不变量 6a、修订 6 / 18a / 签名块）；`docs/designs/architecture_cn.md`、`AGENTS.md`、`prompts/AGENTS.md`；测试 `test_team_policy_rail.py` / `prompts/test_member_system_prompt.py`。**关联仓库 jiuwenswarm**：`agents/harness/team/rails/team_skill_storage_policy_rail.py`（成员工作区路径改走 attachment）+ `tests/agents/swarm/test_swarm_assembly.py` |
| 测试基线 | `tests/unit_tests/agent_teams/` 2175 passed / 16 skipped；jiuwenswarm `test_swarm_assembly.py -k skill_storage` 2 passed |
| Refs | #751 |

## 背景

团队成员的系统提示词按设计应该是"同一 team、同一角色的成员逐字一致"的——这是
prompt 前缀 KV cache 能跨成员命中的前提。实际扫描下来，teammate 的系统提示词里还
残留三处 per-member 内容：

1. **`team_role` section 的 `你的 member_name: <name>` 行**（`build_team_role_section`）。
   紧挨在 H1 标题之后、整段角色策略之前，位置极靠前——一个字不同，后面**整段** role
   policy、workflow、dispatch、lifecycle 的 cache 全部作废。
2. **`team_private_prompt` section**（成员私有工作约定，`spawn_teammate` 的可选 `prompt`
   参数）。它是彻头彻尾的 per-member 内容——每个成员一份，甚至长度都不同——却和
   role / workflow / lifecycle 一起进了 builder。
3. **jiuwenswarm `TeamSkillStoragePolicyRail` 的成员工作区路径**（`_format_forbidden_paths`
   渲染的 `- 成员工作区：<path>` 一行）。同 section 的另外两条（team 共享工作区、team
   skills 共享视图）是 team 级路径、成员间相同，只有这一条带成员名。

成员数量一多，这些变量把"一份团队前缀"劣化成"每成员一份前缀"，缓存命中率随成员数
线性塌陷。

对照已有分层（[[F_46]] / [[F_50]] / [[F_52]]）：churn 的团队状态（`team_members` /
`team_info`）早就走 `prompt_attachment_manager` 挂在消息尾部，正是为了不碰前缀。本次
把"恒定但 per-member"的值也收进同一条通道——判据从"会不会变"扩展成"会不会在成员之间
不同"，两者都会打断共享前缀。

## 决策

### D1：`member_name` 独立成 `team_identity` section，走 attachment
- 新增 `TeamSectionName.IDENTITY = "team_identity"`（P:10）+ `build_team_identity_section
  (member_name, language)`，`member_name` 为空返回 `None`。正文一行：`# 成员身份` +
  `你的 member_name: X`。
- `build_team_role_section` **删掉 `member_name` 参数**（不是保留传 `None`）——role section
  从此在签名层面就无法携带成员身份，编译期消除回潮路径。
- `TeamPolicyRail.__init__` 构造 `_identity_section`（内容恒定，构造一次），
  `_sync_dynamic_sections` 与 members / info 一并 `_upsert_or_clear` 进 attachment。它不需要
  mtime 探针——恒定内容没有 probe 可言。
- `attachment_notice.md`（cn/en）同步列出 `team_identity`：成员能收到的每一种 attachment type
  都必须在说明里点名，否则 LLM 会遇到一个没被介绍过的 `<prompt-attachment>`。措辞上把
  `team_identity`（恒定）与 `team_members`・`team_info`（逐轮刷新）分开，避免"始终以最新一次
  为准"的时效性说明被误读成身份也会变。

### D2：私有工作约定并入 `team_identity`，不新增第二个 attachment type
- private prompt 与 member_name 的生命周期完全一致（spawn 时固定、此后恒定、成员间不同），
  投递去向也一致（进程内 → attachment，外部 CLI → 内联），语义上同属"关于你自己的信息"。
  因此它不是并列的第二个 section，而是 `team_identity` 里的一个 `## 私有工作约定` 子节
  （原 H1 降为 H2）。`build_team_private_prompt_section` / `TeamSectionName.PRIVATE_PROMPT`
  **删除**——留着一个没有调用方的 builder 只会诱使后人再拆出去。
- `build_team_identity_section(member_name, member_prompt)` 两个入参各自可缺：`member_prompt`
  为空（leader、或没配私有 prompt 的成员）只出名字行，两者都空返回 `None`——语义与原来
  "空则不挂 section"一致。
- 由此 `build_team_static_sections` 只需一个 flag `include_member_specific`；rail 侧也只有
  一个 `_identity_section`、一次 `_upsert_or_clear`。

### D3：外部 CLI 成员保持内联（`include_member_specific`）
- 只有 `build_team_member_system_prompt`（外部 CLI 路径）传 `True`。
- 理由：外部 CLI 成员的 prompt 是**独立进程的一次性快照**，不与兄弟成员共享前缀（各自
  CLI、各自 provider），没有 cache 可保护；反过来它在启动期还没有 attachment 通道，拿掉
  这些它就既不知道自己是谁、也拿不到私有约定。两条路径的差异**只有这一个 flag**，其余
  静态 section 仍完全一致。

### D4（jiuwenswarm）：成员工作区路径走 attachment，team 级路径留在前缀
- `_format_forbidden_paths` 只渲染 team 级两条（团队内成员间相同，留在系统提示词无害）。
- 成员工作区路径改由新的 `team_skill_storage_member_workspace` attachment
  （`PromptAttachmentKind.WORKSPACE_DELTA`）承载，正文自带上下文（"该目录不是 skill 源目录，
  不要把新 skill 创建到这里"），脱离原 section 后语义仍完整。
- rail 的 `init` 顺带取 `agent.prompt_attachment_manager`；缺席或路径为空时静默跳过，
  `ValueError`（无 session）降级为 warning，不打断模型调用——与 `RuntimePromptRail` 同款处理。

### D5：S_09 落一条硬不变量（6a）
"进 builder 的团队 section 必须对同一 team、同一角色的所有成员逐字一致"，点名当前唯一的
per-member section（`team_identity`）与唯一豁免：`hitt_human_agent` / `bridge_agent` 的
`{{self_line}}`——这两个角色在一个 team 里单例，不构成放大。新增 section 前先问"是否
per-member"，是就走 attachment。

## 拒绝的方案
- **把自身名字并进 `team_members` attachment（roster 开头加一行"你是 X"）**：省一个 section，
  但 `build_team_members_section` 在没有 peer 时返回 `None`，单成员团队的名字会整个丢掉；
  且 roster 走 mtime 探针缓存，把恒定内容塞进探针路径是把两种生命周期混在一起。
- **role section 保留 `member_name` 参数、传 `None` 关闭**：留着参数就留着回潮路径，下一个人
  照着签名传进来就悄悄破坏前缀。直接删。
- **private prompt 独立成第二个 attachment type**（本次的第一版实现）：两者生命周期、投递
  去向、语义归属完全相同，拆两个 type 就要拆两个 flag、两个 rail 字段、两条 upsert、两段
  notice 文案——全是同一件事的重复。合成一个 section + 一个 `## 子节` 后，这些成对结构一次
  性消失。
- **private prompt 留在系统提示词、只搬 identity**：private prompt 反而是 per-member 内容里
  体量最大的一块（自由文本，可长可短），留着等于前一步白做。
- **保留 `build_team_private_prompt_section` 作为薄封装**：没有调用方的 builder 就是死代码，
  且它的存在会诱使后人再把它拆成独立 section。删。
- **给外部 CLI 也走 attachment**：它启动期没有 attachment 通道（team context 靠 roster 变化时
  steer 进去），首轮就不知道自己是谁；而它本来也没有共享前缀可保护，改了纯亏。
- **jiuwenswarm 侧把整个 `team_skill_storage_policy` section 搬进 attachment**：里面 90% 是
  团队级规则、成员间完全相同，搬走等于把稳定内容从可缓存前缀挪到每轮重发的尾部，方向反了。
  只挪那一行变量。
- **jiuwenswarm 侧把成员工作区路径直接删掉不提**：LLM 需要知道具体路径才能避开它，删了规则
  就失效。

## 验证
- 单测（按 CLAUDE.local.md 只跑 targeted pytest，不跑 lint/format）：
  - `test_team_policy_rail.py`：role section 不含 `你的 member_name`；`build_team_identity_section`
    的正文 / priority / 空名返回 `None`；rail 跑完后系统提示词里既无 `# 成员身份` 也无
    `你的 member_name`，而 attachment 里 `team_identity` 含 `你的 member_name: leader1`；
    `include_member_specific` 门控（默认关、置位开）；identity section 同时含名字行与
    `## 私有工作约定` 子节、空 `member_prompt` 只掉子节、两者都空返回 `None`；外部 CLI
    prompt 同时内联名字与私有约定正文；attachment_notice cn/en 都点名三种 type。
  - `prompts/test_member_system_prompt.py`：静态集默认不含 identity，置位后含；leader
    （无私有 prompt）的 identity 只携带名字。
  - 全套件 `tests/unit_tests/agent_teams/`：**2175 passed / 16 skipped**。
  - jiuwenswarm `test_team_skill_storage_policy_rail_resolves_and_injects_paths`：断言
    team 级两条路径仍在系统提示词、成员工作区路径**不在**系统提示词、且以 attachment 形式
    出现在 `team_skill_storage_member_workspace` 下。1 passed。

## 已知遗留
- `hitt_human_agent` / `bridge_agent` 的 `{{self_line}}` 仍在系统提示词里（S_09 不变量 6a
  的显式豁免）。这两个角色一个 team 内单例，前缀放大有限；若将来出现多 human_agent /
  多 bridge 的团队，应按同一思路收进 `team_identity`。
- `team_extra`（用户自定义 `base_prompt`）目前按 team 级配置对待、留在 builder。若将来
  允许 per-member 覆盖，它就变成 per-member 内容，必须并进 `team_identity` 或另走 attachment。
- `team_info` 每轮进 attachment 的既有决策不变（用户决策保留元数据动态可变的余地，见 [[F_46]]）。
- 本次只处理 team 侧 rail；harness / jiuwenswarm 其它 rail 的系统提示词是否还有 per-member
  变量未做全量扫描。
