# 可演进 workspace（A 提示词 / B DB 值 / C tool 描述 三类文本演进）

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-24（F_82 + F_85 + F_84 三合一重写 + member_prompt 同 session 重发，以代码为准） |
| 范围 | `team_workspace/{assembler,workspace_store,workspace_cache,frontmatter,layout}.py`、`prompts/loader.py`（`make_template_loader`）、`tools/locales/__init__.py`（`make_translator` 加 cache 参数）、`agent/agent_configurator.py`（`_assemble_member_workspace` / `_attach_workspace_cache`）、`agent/coordination/kernel.py`（`start` A/C 写盘）、`tools/team.py`（`TeamBackend` overlay + `_spec_evolution_enabled`）、`schema/{blueprint,team}.py`（`evolution_enabled`）、`team_context.py`（身份块注入）、`rails/*`、`scheduler`、`tool_factory`；测试 `tests/unit_tests/agent_teams/team_workspace/`、`tests/unit_tests/agent_teams/test_team_context_inject.py` |
| 测试基线 | team_workspace UT 全过；ST 在独立分支验证（见「验证」），不进本分支 |
| Refs | `specs/S_24_evolvable-workspace.md` |

> 本文由原 F_82（演进 workspace 主体）、F_85（三路写盘 + 总开关 + cache fill）、
> F_84（B 类覆盖统一化）三份文档合并重写，以代码与 commit 为准。原 F_84/F_85 已删。

## 背景

成员 workspace 中模型可见的文本有三类：**A 提示词模板**（`prompts/<lang>/*.md`）、
**B DB 值**（`team_member`/`team_info` 行的 `desc`/`prompt` 列）、**C tool 描述**
（`locales/descs/<lang>/` md + `STRINGS` dict）。三个问题：

- **演进方不可见**：值散在代码常量、DB 列、md 文件里，演进方没有统一编辑面。
- **改框架默认即改行为**：代码升级会静默覆盖演进方的定制。
- **回退无门**：想回到框架默认只能改代码。

本特性把三类文本统一为 `YAML frontmatter + body` 文件落盘到 team-workspace 目录：
装配阶段写框架基线，演进方改文件 body、下次 run 生效（Runner 边界失效 + lazy 重读，
运行期不热更新）。删文件回代码默认；body hash 与基线一致（未演进）的文件不认演进，
框架默认变了自动升级。

## 数据结构 / 状态机

### 统一文件形态

```text
---
kind: prompt            # prompt | member | team | tool | tool_params
name: leader_bootstrap  # 文件标识
language: cn            # 语言
baseline_sha256: <hash> # 落盘时 body 的 sha256
evolved: false          # 落盘时恒 false；读侧按 hash 比对判断
---
<body>
```

### 三类落盘位置（`WorkspaceLayout` 常量，单一真相）

| 类 | 框架源 | workspace 目标 | frontmatter kind |
|---|---|---|---|
| A 提示词 | `prompts/<lang>/*.md`（rglob 全量） | `<team>/team-workspace/prompts/system/<name>.<lang>.md` | `prompt` |
| B member | DB 列（desc/prompt） | `<member_dir>/prompts/identity/card.md` + `member_prompt.md` | `member` |
| B team | DB 列（team_info.desc/prompt） | `<team>/team-workspace/prompts/identity/team_card.md` + `team_prompt.md` | `team` |
| C tool 级 | `locales/descs/<lang>/<domain>/<key>.md` | `<team>/team-workspace/prompts/tool/<domain>/<key>.<lang>.md` | `tool` |
| C 参数级 | `locales/<lang>.py` 的 `STRINGS` dict（完整 dict 原样） | `<team>/team-workspace/prompts/tool/tool.param.<lang>.md`（JSON dict） | `tool_params` |

workspace 根 = `paths.team_workspace_dir`（顶层单一真相）；workspace 内子路径 = `WorkspaceLayout`。

### 写盘规则（`WorkspaceAssembler._write_baseline` 原语，幂等）

1. **首次装配（文件不存在）**：写 framework 基线，frontmatter 记
   `baseline_sha256 = body_sha256(body)`、`evolved: false`。
2. **文件已存在，body hash == `baseline_sha256`**（未演进）：框架默认变了（hash 不等）→
   用新默认覆盖并更新 `baseline_sha256`；框架默认没变 → 不覆盖。
3. **文件已存在，body hash != `baseline_sha256`**（已演进）：**不覆盖**（演进优先）。
4. **无 frontmatter 手写文件**：视为已演进（无基线可比对），body 总是生效，不覆盖。
5. **畸形 frontmatter**（YAML 解析失败或非 mapping 根）：文件视为**无效**——读侧回退
   framework 默认 / DB 值，写侧以新基线重建。

### 演进判定（读侧唯一依据）

`body_sha256(body) != frontmatter.baseline_sha256` → 已演进 → 读侧用文件 body 覆盖代码
默认/DB 值；hash 一致 → 未演进 → 走默认；畸形 frontmatter → 文件无效 → 走默认。
总开关 `evolution_enabled` 关时（见 D5）**不写文件、不建 cache**——读侧一律回退框架默认 /
DB 裸值，已落盘文件保留但不生效。

## 三路写盘（按值源与依赖拆，不绑角色 / 不绑成员数）

写盘从"每成员装配全量"拆成三个团队级 / 单点挂载（消除 N 倍读放大：N 成员 × 60+ 文件）：

| 类 | 值源 | 挂载点 | 说明 |
|---|---|---|---|
| A/C（系统模板 + tool 描述/参数） | 框架源（`_framework_body` / `descs/`） | `coordination.start`（团队级一次） | 不依赖 team row、不依赖成员；teammate 的 `start` 幂等重跑无害 |
| B-team（team_card / team_prompt） | build_team 的 `desc` 参数 | `build_team`（create_team 后）+ `_reattach_team` | 不查 `get_team_info`（冷启动 team row 不存在 → None 坑）；`team_prompt` 是只写不读到模型字段（build_team 无 prompt 参数） |
| B-member（card / member_prompt） | ctx 演进值（`build_context_from_db` → `get_member` overlay） | 装配期 `_assemble_member_workspace` | 值源是演进值，移 spawn_member 会降级为 spec 裸值丢演进值；每成员只 2 文件，放大可控 |

挂载点不依赖 `workspace_manager.initialize`（A/C 只依赖 team_name + 框架源）——ST 不配
`workspace` 段（manager 为 None）时 A/C 仍要写。

## 决策

### D1：一个 cache 单例，三类分 dict 字段（不分三个 cache 类）

读侧只有一个 `WorkspaceCache` 实例（team 级单例，挂 `TeamWorkspaceManager`，
`TeamBackend.workspace_cache` property 委托 manager 一行）。内部 A/B/C 各占 dict 字段。
写读脱钩：写盘在装配阶段（`WorkspaceAssembler`），读取在运行期 lazy get（miss 读一次常驻，
hit 零 IO）。

### D2：两个读侧入口工厂，可选 cache 参数，默认 None 零侵入

`make_template_loader(cache=None)` 与 `make_translator(lang, cache=None)` 各加可选参数：
`cache=None` 时与原签名完全等价；只有能拿到 cache 的装配点（rails / worker backend /
scheduler / tool factory）显式传 cache。

### D3：演进判定用 body hash 而非 mtime / 探针

cache 无 probe、无 mtime：`get*` miss 时读一次进内存，hit 纯 dict 查找、零文件 IO（连 stat
都没有）。hash 比对天然支持"框架默认变化自动升级"（新基线 hash 不同 → 覆盖），mtime 做不到。

### D4：run 边界失效，运行期不热更新

装配时写基线 + 建空 cache；演进方改文件 → 下次 run 生效（Runner finally `invalidate` 清空
dict，下次 run 第一次 `get*` 重读）。运行期不监听文件变化（不做 hot reload），保持稳态零 IO。

### D5：`evolution_enabled` 演进机制总开关

| | on | off |
|---|---|---|
| 写文件 | 写全部最新（baseline 种子化 / 框架升级 / 演进保护） | **不写** |
| cache | 建对象，存最新值 | **不建对象，None** |
| 读 | cache 命中，演进值覆盖 | 回退框架/DB（cache=None 自然回退） |

实现：`TeamBackend` 补 `_spec_evolution_enabled` 字段（`setup_team_backend` 对称补齐）守卫
写侧；`_attach_workspace_cache` 按开关建 / 不建 cache；`kernel.start` A/C 写、装配 B-member
写都判断开关。

**开关改变必走冷恢复**：`RESUME_FROM_PAUSE` 复用 agent 忽略传入 spec，同 session 改不了开关；
改开关 = 跨 session `activate` 拆旧 agent → 重新 configure → 重新建 / 不建 cache。

### D6：cache 语义——最新值缓存层，写侧 fill（消除写读二次读）

写侧判演进读一次，读侧又判演进读一次 = 同一文件读 2 次。写侧手里已握着最新值，直接 fill cache，
读侧命中零 IO。

- `WorkspaceCache` 加 `fill_template` / `fill_member_field` / `fill_team_field` /
  `fill_tool_md` / `fill_tool_param` / `mark_tools_loaded`——把值直接写进 dict
  （`None` 是合法 prime：标记"无文件值"，读侧不重试 miss）。
- `_read_evolved` → `_read_body`：miss 时读文件返回 body（不管演进与否），`None` 仅缺失 / 畸形；
  演进判断保留在**日志**（`evolved — workspace value wins` / `un-evolved — workspace value
  served`）供 ST 计数。
- `WorkspaceStore` 4 个 B 类写方法**返回最终 body**（演进值 / 新 text / None）→ assembler fill
  直接用，不再读一次。
- `WorkspaceAssembler` 构造加 `cache` 参数，每个写分支 fill（`cache=None` 时跳过）。

### D7：无 frontmatter 手写文件视为已演进；畸形 frontmatter 视为无效

无基线可比对 → body 总是生效、永不覆盖（演进方的编辑永远不丢）。畸形 frontmatter 相反——
文件无效，读侧不认 body（回退默认），写侧可重建基线：无效文件不配做演进保护。

### D8：B 类双写（DB 裸值 + 文件演进值）与统一覆盖载体

B 类 member 级由 `_assemble_member_workspace` 在成员 spawn / 恢复时写；team 级仅当 ctx 带
DB 值（`team_info` 行存在）才写。读侧 `TeamBackend` overlay（`get_member` / `list_members` /
`get_team_info`）用 cache 演进值覆盖 DB 裸值；`display_name` 不演进（回退 DB 列）。

**统一覆盖原则（防霰弹式）**：B 类演进覆盖只发生在 `TeamBackend` 的三个 overlay 方法里。任何
下游代码需要"给模型看的 desc/prompt"时，必须经 `TeamBackend` 方法获取（或从该方法的返回值
取值），禁止直接访问 `workspace_cache.get_member_field` / `get_team_field`，禁止直读 ctx /
DB 裸值。`get_member_field` / `get_team_field` 的调用方白名单只落在 `tools/team.py` 的
`_overlay_member` 与 `get_team_info` 两处。

#### 身份块 prompt 注入（已实施）

`team_context.py:_identity_body` 在已取 overlay member 后用
`member.prompt or self._member_prompt` 注入 `build_identity_text`，构造快照降为 fallback
（与 `display_name` 同一模式：构造参数 + backend 实时值优先）。`TeamContextTracker.member_prompt`
构造参数保留：`team_backend=None` 的单测场景（无 backend 时构造值原样渲染）依赖它；有 backend 时
渲染层以 overlay 值为准。

身份块的常量字段（member_name / display_name / member_workspace_path）spawn 后永不变，
由 `_IDENTITY_EMITTED` 基线门控**只投递一次**。私有工作约定（`member_prompt.md`）是身份块里
唯一可手编演进的字段，它的 mtime 探针驱动身份块**重发只含 prompt 子节的增量**
（`build_identity_prompt_delta`，不含常量字段），与 team_info / roster 两条 mtime 通道并列：

- 首次（`not identity_emitted`）：渲染完整身份块 + `identity_emitted=True` + 记录 prompt mtime。
- 已 emitted：`backend.get_member_updated_at(name, "prompt")` 探 md mtime；移动 →
  `member.prompt` overlay 值渲染 prompt-only 增量块 + 更新 mtime；不移动 → return None（one-shot 保持）。
- backend 无单成员探针（`getattr` 取不到 `get_member_updated_at`）→ return None（one-shot 保持，
  覆盖演进机制 off / 旧 fake backend）。

演进值在"下个 run"或"同 session resume"生效的完整路径：
1. 演进方改文件（body hash != baseline_sha256，frontmatter `updated_at` 移动）。
2. 新 run / 新 session → `RuntimeManager.finalize`（pause 路径）`invalidate_workspace_cache()`
   清空 dict。
3. 成员 spawn / 装配：ctx 携带 DB 裸值（写盘源不变）；tracker 构造 `member_prompt` 仍为 ctx 值
   （fallback 语义，不删）。
4. pending_text → `_identity_body`：首次走完整块；已 emitted 走 mtime 探针，
   `backend.get_member()` → `_overlay_member` → `cache.get_member_field("prompt")` lazy miss →
   读最新 `member_prompt.md` → `member.prompt = 演进值` → 渲染 prompt-only 增量块进对话历史。

关键性质：cache 在本 run 第一次 `get*` 时已失效/为空 → 必读到最新文件值；常量字段 one-shot、
prompt 子节 mtime 驱动重发，两条通道并列不串扰；写盘仍用 ctx 裸值 → `baseline_sha256` 语义完整。

#### 写盘源例外（显式声明）

`WorkspaceAssembler` 的 ctx 输入**禁止** overlay：`baseline_sha256` 必须记录 DB 裸值，否则
"未演进 → 框架升级自动覆盖"与"已演进 → 不覆盖"的判定全部失真。此例外写进 S_24 不变量。

### D9：C 类参数级全工具一个 JSON dict 文件

完整 `STRINGS` dict 原样落盘 `tool.param.<lang>.md`（点分键保持 `"<desc_key>.<param>"` 平铺
形态，不做嵌套聚合），读侧按第一个点拆分还原。scheduled 变体（`create_task_scheduled` 等）
代码里复用 base key，落盘忠实映射——只有 base 条目，演进方改 base 同时影响变体。

### D10：路径单一真相

workspace 内部相对路径（`prompts/system` / `prompts/tool` / `prompts/identity` /
`tool.param` 名 / `MEMBER_IDENTITY_REL`）收敛到无状态 `WorkspaceLayout` 一个模块，写侧
（assembler/store）与读侧（cache/loader）共用同一套规则；workspace 根字面量收敛到顶层
`paths.team_workspace_dir` 一处。新增可演进文本类只需在 layout 加一行常量 + 一对方法。

### D11：写侧演进保护日志统一

fill 优化后读侧命中 cache 不再打 `evolved — workspace value wins` 日志，resume/switch ST 的
`<file>.md evolved` 计数断言改由**写侧判演进日志**支撑。统一 A/C 与 B 类写侧演进分支的日志
needle：`"[workspace] %s evolved — write skipped (evolution wins)"`（assembler A/C-tool +
store 4 个 B 方法 + `_write_tool_params` 全补齐）。

## 演进不生效的例外场景（正常，非缺陷）

以下场景演进值不到模型是设计预期，不是 bug：

1. **同 session identity 常量字段不重发**：身份块的常量字段（member_name /
   display_name / member_workspace_path）由 `_IDENTITY_EMITTED` 基线门控只投递一次——
   这些字段 spawn 后永不变，重发是噪声。**member_prompt 是唯一例外**：它是身份块里唯一可手编
   演进的字段，同 session resume 时经 `get_member_updated_at("prompt")` mtime 探针重发
   prompt-only 增量子节（不含常量字段，见 D8）。

2. **external CLI 三方 agent 无 workspace**：external CLI 成员（`external_cli_spawn.py`）是
   轻量成员——cwd = team-workspace，无私有 workspace、无 member identity md 文件、不参与
   workspace cache / `get_member` overlay。其静态 prompt 装配
   （`member_prompt=ctx.prompt`）直传 ctx DB 裸值，**不经 overlay**，演进值不到。这是 cli 三方
   成员的固有边界，不是缺口。详见
   `analysis/evolvable-team/design-v5/2026-08-22-predefined-member-no-identity-md.md`。
   （原 F_84 设计文档曾把"external CLI 装配点的一次 get_member 是 spawn 路径的额外 DB 读"写为
   预期成本——**该描述是错的**，cli 没有 workspace，不存在该 get_member 路径；此处纠正。）

3. **swarmflow worker 是 ephemeral `wf-*` 成员**：不写 DB roster 行、不落盘 B 类 member 级
   identity，system_prompt 走 C 类 inline（`_t("swarmflow_worker", key=...)`），不经 assembler、
   不读 workspace member identity 文件。演进走 C 类 tool 描述通道，不走 B 类 member 通道。

## 拒绝的方案

### R1：A/B/C 三个独立 cache 类

演进判定、build 时机、attach 生命周期完全相同，拆三个类只是重复三份机械代码。一个实例三类 dict
字段、一个 build 入口、一次扫描，读侧消费者按类查询即可。

### R2：破坏式改造读侧签名（改全部调用点）

`make_translator` 调用点全部显式传 cache 会放大改动面、牵动 UT 与 external CLI 路径。可选
参数 + 默认 `None` 走原逻辑，改动收敛到装配点。

### R3：mtime / 探针做演进判定

mtime 无法表达"框架默认变化 → 自动升级"（基线也变了）；探针在稳态读路径引入 IO。body hash
一次读入内存后纯 dict 查找，判定在 build 期完成。

### R4：运行期热更新（监听文件变化）

热更新破坏"读侧零 IO + 单例 cache"的核心不变量，还要处理并发读改写一致性。演进语义明确是
"改文件 + 重启生效"（spawn-only），不需要热更新。

### R5：`agent_teams.paths` 函数承载 A/B/C 落盘路径

`agent_teams.paths` 的 `team_workspace_dir` / `team_member_workspace_dir` 管 team/member 根目录
在哪；`WorkspaceLayout` 管 workspace 根内相对路径怎么拼。两者都是纯路径规则但职责正交——混在
一起会让"改一个路径要搜两个模块"。

### R6：A/C + B-team + B-member 全部移到 `coordination.start`

冷启动时序坑：leader `start` 早于 `build_team`（build_team 是第一轮工具调用），start 时 team row
不存在 → B-team 值取不到（`get_team_info` 返回 None）。且不加守卫时 teammate 的 `start` 各写一次
= 又 N 倍。

### R7：按角色（leader 守卫）收敛写盘

"谁是 leader"与写盘无语义关系——代码要有业务含义，写盘与角色无关，靠挂载点 + 幂等保证，不靠
运行时角色标志。

### R8：cache 设"演进值覆盖层"、`_read_evolved` 只认演进

写侧已判演进并 fill，读侧重复判演进 = 二次读。改为最新值缓存层后，读侧对已 fill 文件零 IO。

### R9：run 结束把 cache 设 None

误判。同 session 开关不变（`invalidate` 服务重读），开关改变必冷恢复（重新 configure 重建 /
不建），两者各司其职，不存在"残留 cache 对象误以为 on"的场景。

### R10：在下游各缺口处直接调 `workspace_cache` 打补丁

在 tracker / external_cli_spawn 各加一行 cache 判断——正是霰弹式修改的复发形态。每个消费点感知
cache 的存在、各自维护"演进优先/DB 回退"逻辑，新增缺口时再来第三处补丁。覆盖只经 TeamBackend
统一载体。

## 验证

- **UT**：`tests/unit_tests/agent_teams/team_workspace/`（cache fill / lazy / invalidate / store
  返回 body / assembler 三方法）全过；`tests/unit_tests/agent_teams/test_team_context_inject.py`
  （身份块注入：演进注入 / 基线 fallback / 无 backend fallback / None member 抑制）。
- **ST**（真实模型，全在独立分支验证，ST 文件不进 commit）：

| ST | 结果 | 说明 |
|---|---|---|
| `agent_team_evolvable_st.py` | 20/20 | 三路全过 |
| `agent_team_evolvable_session_resume_st.py` | 同 session resume 重读演进值（fill 后读侧命中 cache） |
| `agent_team_evolvable_session_switch_st.py` | roster / team_info 通道实证 |
| `agent_team_evolvable_restart_st.py` | 16/16，进程重启恢复 + 整目录删除重建 |
| `agent_team_evolvable_swarmflow_st.py` | 18/18，模式专属文件演进 |
| `agent_team_evolvable_plan_mode_st.py` | exit 0，plan_mode 专属文件演进 |
| `agent_team_evolvable_review_st.py` | review 流程 |
| `agent_team_evolvable_disabled_st.py` | exit 0，二段式：开→关 + 关→开 |

## 已知遗留

- **三段式完整生命周期 ST**（关→开→演进→关）待补：off 不写 → on 写全部 → 演进 → 再关回退
  框架/DB。三段必须换 session 冷恢复（D5 开关真相）。
- **external CLI 演进值共享**：cli 三方 agent 无 workspace，演进值不生效（例外场景 2，正常）。
  原 F_84 #2（external_cli_spawn 经 get_member 取 overlay）设计存疑且不适用，未实施。
- **`build_team_info_text` 补 team prompt 字段**（原 F_84 #3）：`get_team_info` 的 overlay 已覆盖
  `team.prompt` 字段，但渲染函数漏字段，演进值到不了模型的团队信息块。未实施，待定。
- **member_prompt 同 session resume 重发**：已实施（D8 身份块 prompt 注入 + mtime 探针重发
  prompt-only 增量子节）。switch session 走 fresh tracker 空基线路径，首次即带演进 prompt。
  resume ST 验证中。
- **独立进程 `ExternalTeamClient` 自建 cache**：机制已核实（`WorkspaceCache` 能脱离 manager 自建），
  代码未改；前提 `OPENJIUWEN_HOME` 与 leader 一致。
