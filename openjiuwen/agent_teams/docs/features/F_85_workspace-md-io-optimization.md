# Workspace md IO 优化（三路写盘 + evolution_enabled 总开关 + cache fill）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-20 |
| 范围 | `openjiuwen/agent_teams/team_workspace/{assembler,workspace_store,workspace_cache}.py`、`openjiuwen/agent_teams/agent/coordination/kernel.py`（`start` A/C 写盘）、`openjiuwen/agent_teams/agent/agent_configurator.py`（`_attach_workspace_cache` 开关判断 + `_assemble_member_workspace` 三路拆分 + `setup_team_backend` 补传开关）、`openjiuwen/agent_teams/tools/team.py`（`TeamBackend._spec_evolution_enabled` + `build_team`/`_reattach_team` B-team 写盘）；测试 `tests/unit_tests/agent_teams/team_workspace/test_{workspace_cache,workspace_assembler,workspace_store}.py` |
| 测试基线 | team_workspace UT **103 passed**；ST：evolvable 20/20、resume 17/0、switch 21/0、restart 16/16、swarmflow 18/18、plan_mode exit 0、review 34/34、disabled（二段式）exit 0（详见「验证」） |
| Refs | `specs/S_24_evolvable-workspace.md`（已按本特性同步）、`features/F_82_evolvable-workspace.md` |

## 背景

读侧 cache（`WorkspaceCache` lazy get + `invalidate`）已保证"一次运行每文件读一遍"，但**写侧存在 N 倍读放大**：`_assemble_member_workspace` 在**每个成员** spawn / 恢复时调 `write_team_workspace`，全量 A/C（~60+ 文件）各 `read_text` + 算 hash 判演进 → **N 个成员 × 60+ 文件 × (1 读盘 + 2 hash)**。

ST 实证（2026-08-19 基线日志计数）：resume（N=2）每个 edited 文件 run2 期间被"判演进读" **2 次**（leader + worker 各装配一次）；switch（N=2, COLD_RECOVER）**3 次**（全新实例 + fresh spawn）。

## 决策

### D1：三路写盘（按值源与依赖拆，不绑角色 / 不绑成员数）

写盘从"每成员装配全量"拆成三个团队级 / 单点挂载：

| 类 | 值源 | 挂载点 | 说明 |
|---|---|---|---|
| A/C（系统模板 + tool 描述/参数） | 框架源（`_framework_body` / `descs/`） | `coordination.start`（团队级一次） | 不依赖 team row、不依赖成员；teammate 的 `start` 幂等重跑无害 |
| B-team（team_card / team_prompt） | build_team 的 `desc` 参数 | `build_team`（create_team 后）+ `_reattach_team` | 不查 `get_team_info`（冷启动 team row 不存在 → None 坑）；`team_prompt` 是只写不读到模型字段（build_team 无 prompt 参数） |
| B-member（card / member_prompt） | ctx 演进值（`build_context_from_db` → `get_member` overlay） | 装配期 `_assemble_member_workspace` | 值源是演进值，移 spawn_member 会降级为 spec 裸值丢演进值；每成员只 2 文件，放大可控 |

挂载点不依赖 `workspace_manager.initialize`（A/C 只依赖 team_name + 框架源）——ST 不配 `workspace` 段（manager 为 None）时 A/C 仍要写（旧 ST 即此情况）。

### D2：`evolution_enabled` 从"读侧开关"提升为"演进机制总开关"

旧语义：写侧永远写盘，开关只管读侧（false → cache 不读文件回退）。新语义：

| | on | off |
|---|---|---|
| 写文件 | 写全部最新（baseline 种子化 / 框架升级 / 演进保护） | **不写** |
| cache | 建对象，存最新值 | **不建对象，None** |
| 读 | cache 命中，演进值覆盖 | 回退框架/DB（cache=None 自然回退） |

**开关改变必走冷恢复**：`RESUME_FROM_PAUSE` 复用 agent 忽略传入 spec（`manager.py:1052`），同 session 改不了开关；改开关 = 跨 session `activate` 拆旧 agent → 重新 configure → 重新建 / 不建 cache。

实现：`TeamBackend` 补 `_spec_evolution_enabled` 字段（`setup_team_backend` 对称补齐，此前漏传）守卫写侧；`_attach_workspace_cache` 按开关建 / 不建 cache；`kernel.start` A/C 写、装配 B-member 写都判断开关。

### D3：cache 语义从"演进值覆盖层"改为"最新值缓存层"，写侧 fill

核心诉求"每个 md 只读一次只算一次 hash"：写侧判演进读一次，读侧又判演进读一次 = 同一文件读 2 次。写侧手里**已握着最新值**，直接 fill cache，读侧命中零 IO。

- `WorkspaceCache` 加 `fill_template` / `fill_member_field` / `fill_team_field` / `fill_tool_md` / `fill_tool_param` / `mark_tools_loaded`——把值直接写进 dict（`None` 是合法 prime：标记"无文件值"，读侧不重试 miss）。
- `_read_evolved` → `_read_body`：miss 时读文件返回 body（不管演进与否），`None` 仅缺失 / 畸形；演进判断保留在**日志**（`evolved — workspace value wins` / `un-evolved — workspace value served`）供 ST 计数。
- `WorkspaceStore` 4 个 B 类写方法**返回最终 body**（演进值 / 新 text / None）→ assembler fill 直接用，不再读一次。
- `WorkspaceAssembler` 构造加 `cache` 参数，每个写分支 fill（`cache=None` 时跳过）。

### D4：写侧演进保护日志统一

fill 优化后读侧命中 cache 不再打 `evolved — workspace value wins` 日志，resume/switch ST 的 `<file>.md evolved` 计数断言改由**写侧判演进日志**支撑。统一 A/C 与 B 类写侧演进分支的日志 needle：`"[workspace] %s evolved — write skipped (evolution wins)"`（assembler A/C-tool + store 4 个 B 方法 + `_write_tool_params` 全补齐）。

## 拒绝的方案

### R1：A/C + B-team + B-member 全部移到 `coordination.start`

冷启动时序坑：leader `start` 早于 `build_team`（build_team 是第一轮工具调用），start 时 team row 不存在 → B-team 值取不到（`get_team_info` 返回 None）。且不加守卫时 teammate 的 `start` 各写一次 = 又 N 倍。

### R2：按角色（leader 守卫）收敛写盘

"谁是 leader"与写盘无语义关系——代码要有业务含义，写盘与角色无关，靠挂载点 + 幂等保证，不靠运行时角色标志（用户纠偏）。

### R3：cache 设"演进值覆盖层"、`_read_evolved` 只认演进

写侧已判演进并 fill，读侧重复判演进 = 二次读。改为最新值缓存层后，读侧对已 fill 文件零 IO。

### R4：run 结束把 cache 设 None

误判。同 session 开关不变（`invalidate` 服务重读），开关改变必冷恢复（重新 configure 重建 / 不建），两者各司其职，不存在"残留 cache 对象误以为 on"的场景。

## 验证

- **UT**：`tests/unit_tests/agent_teams/team_workspace/` **103 passed**（cache fill / lazy / invalidate / store 返回 body / assembler 三方法）。
- **ST**（真实模型，全在 `evolvable-block-a819` 分支验证，ST 文件不进 commit）：

| ST | 结果 | 说明 |
|---|---|---|
| `agent_team_evolvable_st.py` | 20/20 | 三路全过 |
| `agent_team_evolvable_session_resume_st.py` | 17/0 | 同 session resume 重读演进值（fill 后读侧命中 cache） |
| `agent_team_evolvable_session_switch_st.py` | 21/0 | roster / team_info 通道实证 |
| `agent_team_evolvable_restart_st.py` | 16/16 | 进程重启恢复 + 整目录删除重建 |
| `agent_team_evolvable_swarmflow_st.py` | 18/18 | 模式专属文件演进 |
| `agent_team_evolvable_plan_mode_st.py` | exit 0 | plan_mode 专属文件演进 |
| `agent_team_evolvable_review_st.py` | 34/34 | review 流程 |
| `agent_team_evolvable_disabled_st.py` | exit 0 | 二段式：开→关 + 关→开 |

- **IO 日志证据**：disabled ST（fill 后）读侧 `evolved / un-evolved` needle 计数 **0**——on 运行读侧全 dict hit 零文件 IO。

## 已知遗留

- **三段式完整生命周期 ST**（关→开→演进→关）待补：off 不写 → on 写全部 → 演进 → 再关回退框架/DB。三段必须换 session 冷恢复（§D2 开关真相）。方案见 `analysis/evolvable-team/design-v5/2026-08-20-md-io-handoff.md` §4.5。
- **member_prompt 演进模型侧通道**（handoff §5.2/§5.5）：代码确认 switch + resume 都通（teammate 重新 spawn → overlay → 身份块），身份块 one-shot 重发有风险，ST 未补（用户裁决：测不过就记录不修复）。
- **独立进程 `ExternalTeamClient` 自建 cache**（handoff §6）：机制已核实（`WorkspaceCache` 能脱离 manager 自建），代码未改；前提 `OPENJIUWEN_HOME` 与 leader 一致。
- **in-process 外部 CLI 演进值共享 ST**：代码链已确认注入 leader cache（`external_cli_spawn.py:283`），ST 未覆盖。
