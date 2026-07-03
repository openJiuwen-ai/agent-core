# 成员 desc/prompt 二分与 spec 归一

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-07-03 |
| 范围 | `schema/team.py`、`schema/blueprint.py`、`prompts/sections.py`、`prompts/__init__.py`、`prompts/policy.py`、`prompts/bridge_remote_brief.py`、`rails/team_policy_rail.py`、`rails/elements.py`、`agent/agent_configurator.py`、`agent/payload.py`、`agent/spawn_manager.py`、`agent/team_agent.py`、`agent/member_factory.py`、`agent/bridge_outbound_wrap.py`、`agent/coordination/handlers/message.py`、`spawn/{external_cli,inprocess}_spawn.py`、`interaction/bridge_protocol.py`、`tools/team.py`、`tools/tool_member.py`、`tools/locales/{cn,en}.py`、`external/cli_agent/adapters.py`、`i18n.py` |
| 测试基线 | `tests/unit_tests/agent_teams/` 1691 passed, 16 skipped |
| Refs | #751 |

## 背景

每个团队成员本应只有两个描述字段，语义正交：

- **desc（公开）**：成员对外描述，供成员之间相互认知。
- **prompt（私有）**：成员私有信息，回拼进自己的系统提示词。

但重构前存在三处结构性偏差：

1. **命名漂移**。DB 列（`team_member.desc` / `team_member.prompt`）和工具 input schema
   本来就是 `desc` / `prompt`，但 spec 层（`LeaderSpec.persona` / `TeamMemberSpec.persona`
   + `prompt_hint`）、runtime context（`TeamRuntimeContext.persona`）、prompt section
   （`team_persona`）、i18n（`blueprint.default_persona`）、bridge 子系统
   （`sender_persona` / `bridge_persona` / `MemberSummary.persona` / `build_bridge_persona`）
   一路用 `persona` 这个中间概念，全库 130+ 处，同一个东西三四个叫法。

2. **prompt 私有语义被实现成首启消息**。DB `team_member.prompt` 列在整个 runtime 里唯一
   的读取点是 `team_agent.py::_on_teammate_created`，被当作 `initial_message` 经 spawn
   payload 的 `query` 字段 `harness.send` 出去——一次性启动喊话，而不是回拼进系统提示词。
   这直接违反 `tools/locales/descs/*/spawn_teammate.md` 对 `prompt` 的定义（「仅注入该成员
   自己的 system prompt」「不要写成『开始工作』这类空泛启动语句」）。

3. **desc 双投放**。`build_team_persona_section` 把成员**自己的 desc** 也拼进了它自己的
   系统提示词（`team_persona` section），与「desc 只进他人名单」的定义相冲突，破坏了两个
   字段的正交性。

4. **leader / teammate spec 割裂**。`LeaderSpec`（只有 `persona`，无私有 prompt）与
   `TeamMemberSpec`（`persona` + `prompt_hint`）是两个独立类，字段名和结构都不一致。

## 决策

1. **两字段正交二分，各有唯一去向。** `desc`（公开）→ 只拼进他人的成员名单
   （`team_members` prompt section）与 `list_members`；`prompt`（私有）→ 只拼进自己的
   系统提示词（`team_private_prompt` static section）。两条投放边互不交叉。

2. **删除 persona section。** 严格按 #1，成员自己的系统提示词身份由 `prompt` 承担，`desc`
   不再出现在自己的 prompt 里。`build_team_persona_section` / `TeamSectionName.PERSONA` /
   `persona_heading` 整体移除；新增 `build_team_private_prompt_section`（section 名
   `team_private_prompt`，priority 16，介于 persona 原位 15 与 extra 17 之间）。

3. **提取 `MemberSpecBase` 归一 leader / teammate。** 基类持
   `member_name / display_name / desc / prompt / model_name`，`LeaderSpec` 与
   `TeamMemberSpec`（+ `role_type`）+ `BridgeMemberSpec` 都继承它。公开/私有二分定义一次。
   leader 因此也获得私有 `prompt`。

4. **私有 prompt 走 static section，KV-cache 前缀稳定。** `prompt` 在成员 spawn 时定死
   （rail init 构造一次，不逐轮从 DB 刷新），与逐轮刷新的 dynamic attachment
   （`team_members` / `team_info` / `team_hitt`）分层。**leader 尤其特殊**：其系统提示词
   在 build 时预先传入、固定，build_team 后只生成新的团队 `desc`（给成员相互识别，走
   dynamic attachment），**不生成新的 leader prompt**，保证 leader 系统提示词前缀 KV-cache
   稳定。leader 的私有 prompt 除了运行时经 blueprint 注入 rail，还在 `build_team` spawn leader
   自身时经 `TeamBackend(leader_prompt=ctx.prompt)` 持久化到 leader 的 DB 成员行（公开 `desc`
   由 LLM 的 build_team 工具参数填、私有 `prompt` 走 spec），保证 cold-recovery 从 DB 重建
   leader context 时私有 prompt 不丢失。

5. **首启消息整体退场。** DB `prompt` 不再被当作 `initial_message`。所有成员起来后仅订阅，
   真实任务一律由 leader 通过 `send_message` / 任务指派下发（与 F_33「空 query 不 send，靠
   mailbox poll」一致）。`_on_teammate_created` 恒传 `initial_message=None`。

6. **彻底肃清 `persona` 一词，含 bridge 协议与工具 schema。** 同时修正 F_07 的旧假设
   「desc doubles as connect briefing」——那是 desc/prompt 未分化时的权宜。归一后：
   bridge / external CLI 的远程 agent 是该成员**自己的执行大脑**，远程据以扮演角色的
   briefing 就是该成员**自己的系统提示词** = `prompt`（私有），而非 `desc`（写给他人看的
   花名册）。因此 bridge / external CLI 成员对称 `spawn_teammate`，同时持 `desc`（公开，进
   他人花名册 / `build_team_overview`）与 `prompt`（私有，远程 briefing）：
   - 远程 briefing 走 prompt：`build_bridge_persona` → `build_bridge_brief(prompt=)`、
     `bridge_protocol.connect(bridge_persona=)` → `connect(prompt=)`。
   - 团队花名册走 desc：`MemberSummary.persona` → `MemberSummary.desc`、`build_team_overview`
     渲染各成员 `desc`。
   - `spawn_bridge_agent` / `spawn_external_cli_agent` 暴露 `desc` + `prompt` 两参数（不
     合并），工具 input schema 增补 `prompt`；远程 briefing 依赖 `prompt`，故非空校验落在
     `prompt`。
   - 其余术语改名：`sender_persona` → `sender_desc`、`_lookup_persona` → `_lookup_desc`。

## 拒绝的方案

- **保留 persona section（desc 也进自己）**：让 desc 变成「他人名单 + 自己 prompt」双投放，
  破坏正交性，也让「desc 是写给别人的」这一定义失焦。拒绝。
- **只改字段名、不删 persona section**：命名统一了但语义边仍错（desc 进自己）。拒绝。
- **bridge 子系统留 persona 术语**：与「彻底肃清」目标冲突，且 `persona` 在 bridge 里指的
  也是成员对外描述（= desc），没有独立语义值得保留。拒绝。

## 已知遗留

- （已清理）`prompts/policy.py` 的 `build_system_prompt` + `_build_team_policy` legacy 装配路径
  确认仅测试在用，已随本次一并删除（连同 `system_prompt.md` 壳模板与 `_format_team_*` helper）；
  `policy.py` 精简为纯 `role_policy` 加载器，测试迁移到 `role_policy` 与主力路径
  `build_team_member_system_prompt`。唯一装配路径现在只有 `sections.py` + `TeamPolicyRail`。
