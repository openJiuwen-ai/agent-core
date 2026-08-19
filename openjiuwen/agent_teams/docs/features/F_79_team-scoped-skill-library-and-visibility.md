# Team 场景 Skill 单点化：一份库 + 可见性声明

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-12 |
| 范围 | `agent_teams/paths.py`（`global_skills_dir` / `configure_global_skills_dir` / `reset_global_skills_dir` / `SKILL_VISIBILITY_FILENAME` / `member_skill_visibility_path` / `team_skill_visibility_path` / `team_workspace_dir` / `team_member_workspace_dir`）；新增 `agent_teams/skill/visibility.py`（声明文档 + 合成 + authority 排名 + provider）、`agent_teams/skill/file_lock.py`（声明文件跨进程写锁）、`agent_teams/skill/rail_spec.py`（rail 声明装配）、`agent_teams/skill/library_state.py`（库级 `skills_state.json` 开关读取）、`agent_teams/rails/team_skill_use_rail.py`（`TeamSkillUseRail`）；`agent_teams/rails/elements.py`（新增 `core.team.skill_use`）；`agent_teams/agent/agent_configurator.py`（`skills=[]` + `enable_skill_discovery=False` + 挂 team rail）；`agent_teams/workflow/backends/team_worker_backend.py`（`_apply_worker_skill_visibility`）；`agent_teams/team_workspace/manager.py`（`_seed_team_skill_visibility`、挂载点合并跳过声明文件）；`agent_teams/workspace_layout.py`（删 `ensure_member_skill_copy`、删 workspace copytree 兜底）；`agent_teams/agent/scheduling/review_feedback_evolution.py`（`_build_default_member_rail` 改用 `SkillEvolutionRail`）；`docs/specs/S_13` 修订、`S_05` / `S_12` 局部修订、`AGENTS.md` rail 表修正；测试 `tests/unit_tests/agent_teams/test_skill_visibility.py`、`test_skill_visibility_paths.py`、`test_skill_file_lock.py`、`test_skill_library_state.py`、`test_team_skill_use_rail.py`、`test_team_skill_assembly.py`。**关联仓库 jiuwenswarm**：`common/utils.py`（`configure_skill_library` / `migrate_team_skill_views`）、`common/schema/message.py`（`ReqMethod.SKILLS_VISIBILITY_GET/SET/UPDATE`）、`server/runtime/skill/skill_manager.py`（三个 RPC handler）、`server/runtime/agent_adapter/interface.py`（方法路由）、`agents/swarm/providers/skills.py`、`agents/harness/team/rails/team_skill_library_reload_rail.py`（新增）、删除 `agents/harness/team/team_skill_links.py` 与 `rails/team_shared_skill_link_refresh_rail.py` |
| 测试基线 | agent-core 全量 14852 passed / 1 failed（既有环境失败 `test_execute_javascript_code_success`，Node 25 ANSI 输出）；`tests/unit_tests/{harness,agent_teams}` 单独跑 5194 passed / 0 failed（证明单 agent 路径未受影响）。jiuwenswarm 全量 4748 passed / 4 failed（均为既有环境失败：3 个 `test_message_handler_security_review_prompt`、1 个 `test_warm_key_normalizes_project_directory`） |
| Refs | #751 |

## 背景

### 症状

Team 模式下 Skill 实体在磁盘上有过四种形态并存：全局技能库里的原件、成员
workspace 下 `skills/` 里的软链、Windows 上的 junction、受限沙箱里的整目录拷贝。
每一种都是"把库里的东西再摆一份到成员看得见的地方"，于是同一个 Skill 的 `SKILL.md`
可能有 N 份且互相漂移；`.team` 挂载点又把团队 workspace 的 `skills/` 拉进每个成员
的可见范围，成员实际看见什么取决于"哪几层视图刚好没同步失败"。

Skill 的安装、卸载、演进都要遍历这 N 份视图去补链、断链、重拷；任何一处漏掉就是
"技能面板里装好了，agent 说找不到"这一类现场问题。视图本身是分发机制，可见性只是
它的副作用——想收窄某个成员的可见范围，唯一手段是少给它链一个目录。

### 一次无痕的架构级回退（这份文档存在的一半理由）

`5126b037b`（2026-04-18，`fix(harness): improve workspace cwd fallback and skill
rail config`）已经把结论写对过一次：commit body 里那行
"Fix SkillUseRail to use base skills_dir with enabled_skills filter" ——**单一基目录
 + 名单过滤**，正是本次重新落地的形状。

三天后 `767511257`（2026-04-21）把它删掉，换成 `enable_skill_discovery` 布尔开关 +
目录视图。那次提交的 message 只有一行：`fix(agent-teams): support Windows junction
for team skill discovery`。看标题谁都以为只是给 Windows 补个 junction 分支，没人会
知道底下顺手把"单目录 + 过滤"的模型换回了"多目录视图"。此后的每一个团队 Skill bug
都在这条被换回来的路上修（下面三个哈希在 **jiuwenswarm 仓**）：整目录 copytree
（`d96bb3114`，2026-04-24）、软链视图（`7720f45d6`，2026-06-04）、HarmonyOS 沙箱
拷贝兜底（`965f40263`，2026-07-25）。

**只写 what 的 commit message 会让一次架构级回退无痕消失。** 这也是本模块把
"每次特性更新必须归档 feature 文档"写成硬约束的原因（见 `AGENTS.md`「设计文档归档
与双向同步」）。这份文档的第二个作用就是：下次有人想把可见性重新做回目录视图时，
先来这里读一遍为什么它被拆掉过两次。

### 上一版实现为什么被回退

本次改动的上一版直接改了 harness 的 `SkillUseRail` 与 `harness/factory.py`，让
单一基目录 + 过滤成为**全局**语义。结果波及两仓约 100 个文件，改动散布到
`harness/rails/skills/`、`harness/rails/evolution/`、`harness/factory.py`、
`harness/workspace/`、`rsi/`、`symphony/`、`agent_evolving/`——单 agent、RSI、
symphony 全被卷进一次本来只属于 team 的问题。那一版已被整体回退。

**本次的核心约束由此确立：改动只准落在 team 作用域内。** 单 agent 走到的代码一行
不动。

## 数据结构

### 磁盘布局

```
<全局技能库>/                                   # paths.global_skills_dir()，唯一实体存放点
├── <skill_name>/SKILL.md
└── skills_state.json                          # 库级全局开关（marketplace / 安装流写）

<team_home>/
├── team-workspace/
│   ├── artifacts/… trajectories/ team-memory/
│   └── skills-visibility.json                 # 团队可见性声明（不是技能本体）
└── workspaces/<member>_workspace/
    ├── .team/<team_name> → …/team-workspace   # 产物协同挂载点，保留
    └── skills-visibility.json                 # 成员可见性声明
```

`paths.global_skills_dir()` 默认 `{openjiuwen_home}/workspace/skills`；宿主自带技能库
时用 `configure_global_skills_dir()` 进程级改指（jiuwenswarm 在启动时经
`common/utils.py::configure_skill_library` 指向 `~/.jiuwenswarm/agent/workspace/skills`）。

### 声明文档

`agent_teams/skill/visibility.py::SkillVisibility`：

```json
{
  "version": 1,
  "scope": "member",
  "id": "reviewer",
  "bootstrapped_from": "config:agents.skills",
  "authority": 0,
  "allow": [],
  "deny": []
}
```

读写规则：读不加锁（写方一律 `os.replace` 落盘，读方要么看到旧文档要么看到新文档）；
写走 `file_lock.cross_process_file_lock` + 同目录临时文件 + 原子 rename。文件缺失、
不可读、JSON 损坏一律降级为"无限制"文档——丢了声明不能等于剥夺该成员的全部技能。

## 决策

### D1：Skill 实体单点化，可见性下沉为 metadata

Team 场景下 Skill 实体只存在于 `paths.global_skills_dir()` 一处。成员 workspace 与
team workspace 下不再有 `skills/` 目录，各自只有一份 `skills-visibility.json`。

代码层体现：`workspace_layout.py` 删掉 `ensure_member_skill_copy`；
`team_workspace/manager.py::initialize` 原来那行 `os.makedirs(.../"skills")` 换成
`_seed_team_skill_visibility()`；claw 侧删掉 `team_skill_links.py` 与
`team_shared_skill_link_refresh_rail.py` 整套链接同步。

### D2：实现方式是 team 自有 rail（继承），不是改动单 agent rail

**这是本次的核心约束。** `openjiuwen/harness/rails/skills/skill_use_rail.py` 的
`SkillUseRail` **一字未改**，`harness/factory.py`、`harness/rails/evolution/`、
`harness/workspace/`、`rsi/`、`symphony/`、`agent_evolving/` 同样未改。

新行为由两件东西拼出来，两件都在 `agent_teams/` 下：

1. `rails/team_skill_use_rail.py::TeamSkillUseRail(SkillUseRail)` —— 只覆写两个
   方法：
   - `_filter_skills`：先 `_apply_visibility()` 按声明重算 `enabled_skills` /
     `disabled_skills`，再调 `super()._filter_skills()`。过滤语义整条继承父类。
   - `_build_skills_snapshot_signature`：父类签名只跟踪 Skill 目录与 `SKILL.md`
     的 mtime，授权变化在库里"什么都没动"，成员会一直用旧提示词；所以把合成后的
     allow / deny 本身追加进签名。
   另外覆写 `get_skills_for_session`：session 基线是持久化状态，不复查的话被撤销的
   Skill 到会话结束前仍可调用。
2. `agent/agent_configurator.py` 把 `skills=[]` + `enable_skill_discovery=False`
   写进成员的 `build_spec`，使 DeepAgent 工厂的通用 `SkillUseRail` 自动挂载条件不
   成立；`agent_spec.skills` 不丢，它作为 seed allow-list 进 team rail 的 params。

判据很直白：**这段代码单 agent 模式会走到吗？会走到就别动。** 单 agent 依旧
`workspace/skills` + `enable_skill_discovery`，行为逐字不变。

### D3：`core.team.skill_use` 走 manifest 声明式装配

rail 经 `rails/elements.py` 的 `TEAM_SKILL_USE = "core.team.skill_use"` +
`TeamSkillUseInput` 声明，和其余 team rail 同一条 provider 路径（F_32）。
`skill/rail_spec.py` 提供两个入口，供 `AgentConfigurator` 与 `TeamWorkerBackend`
共用同一份判定，不各写一遍：

- `build_team_skill_rail_spec(...)`：成员没有稳定身份、或蓝图已自带 Skill rail 时
  返回 `None`；否则产出带完整 params 的 `RailSpec`。
- `complete_declared_team_skill_rails(...)`：宿主蓝图（jiuwenswarm）能声明曝光偏好
  但铸不出成员名——成员名是 spawn 时才有的。它把身份 params 补进已声明的裸 rail。

params 全部可序列化（声明路径以字符串传递），成员在另一个进程按 seed 重建时自己
重建 provider，不依赖跨不了边界的 live handle。

**成员声明的播种只有一个写者**：`create_team_skill_use_rail`。宿主（claw）的
`swarm.member_skill_toolkit` 曾经也拿 `config.agents.<role>.skills` 播种同一个
文件——两个写者在同一次成员装配里抢同一把文件锁，且各自的跳过规则不同（toolkit
在选择为空时跳过写入，rail 无条件写），那条"省一次文件锁"的优化因此从来没有生效。
现在 toolkit 只读路径用于日志，seed allow 只经 rail 的 `bootstrap_allow` 进入。

**团队声明的播种同样只有一个写者**：`team_workspace/manager.py::initialize` 里的
`_seed_team_skill_visibility()`。它跟着 team workspace 的初始化跑，每个有 workspace
的团队启动时必到一次；没有 workspace 的团队本来也没地方放这份文件，而**缺文件读回来
就是"团队不施加约束"**，所以不需要别处补写。

曾经还有两处写同一份文件（claw 侧 `agents/swarm/assembly.py` 的
`_bootstrap_team_skill_visibility`、`agents/harness/team/team_manager.py` 的
`ensure_team_skill_visibility_initialized`），三处语义碰巧一致——都是 allow 为空 +
`AUTHORITY_SEED` + 永不覆盖已存文件——所以没出过事。这种"碰巧"不是设计：任何一处
将来改成播非空 allow，结果就变成按调用顺序决定，而调用顺序（装配先还是 workspace
初始化先）从来不是稳定契约。两处已删除，`tests/unit_tests/agentserver/test_team_shared_skills.py`
（claw 仓）钉住"平台侧不得再出现第二个团队播种者"。**嵌入方不得再加写者**：要预置
授权就改 workspace 初始化那一处，或走 `skills.visibility.set/update` 的显式授权路径
（`AUTHORITY_EXPLICIT`，任何 seed 都动不了它）。

### D4：合成规则与"空 allow"语义

`visibility.py::compose_skill_visibility`：

```
enabled  = member.allow ∪ team.allow
disabled = member.deny  ∪ team.deny ∪ global_disabled
```

`global_disabled` 来自库根的 `skills_state.json`，经
`team_skill_use_rail.global_disabled_skills()` 读取，实现落在
`harness/skills/library_state.py::collect_disabled_skills`。

这里前后换过两次落位，值得记下来。最初 team 侧直接 import 了 `harness/factory.py`
的私有函数 `_collect_disabled_skills_from_state`：省了一个解析器，代价是 team 包
吊在另一层的私有符号上——factory 那边改名，团队成员的 Skill 过滤会静默失效且没有
任何测试变红。为解开这个依赖，team 包一度自持了一份拷贝，于是同一个文件格式有了
两个解析器：加字段要同步改两处，改漏一处就让单 agent 与团队对同一个库得出不同的
可见集。

两个方案都有明显缺陷，真正的成因是这段逻辑**放错了层**：它读的是 Skill 库的状态，
既不属于「构造 agent」（factory 的职责），也不属于团队。现在它独立成
`harness/skills/library_state.py` 这个公开模块，factory 与 team rail 都从这里取，
库格式变更只需改一处。`tests/unit_tests/harness/test_skill_library_state.py` 里的
`test_skill_library_state_has_a_single_parser` 用 AST 剥掉 docstring 后检查两个
消费方都没有再自己拼状态文件路径——散文里提文件名不算违规，在代码里拼路径才算。

**空 `enabled` 原样返回，绝不在这里展开成全量 Skill 名集合。** 这个短路语义是从父类
`SkillUseRail._filter_skills` 继承来的——`if self.enabled_skills and skill.name not
in self.enabled_skills` 里的前半段就是"空 allow 不过滤"。在合成层把它展开成全集，
表面等价，实际会把视图冻结在展开那一刻：之后新装的 Skill 因为不在这份快照里而全部
不可见。**后人改这里之前先读这一段。** deny 恒优先于 allow。

### D5：authority 排名，让正确性不依赖调用顺序

`AUTHORITY_SEED = 0 < AUTHORITY_MIGRATION = 10 < AUTHORITY_EXPLICIT = 100`。
`bootstrap_skill_visibility` 只在自己的 rank **严格高于**已存文档时才覆盖 allow，
且任何情况下都保留已存的 deny（丢掉一条撤销只可能放宽权限）。

存在的理由是一个具体的竞态：启动时既有"从存量目录视图迁移出来的 allow"
（`migration:symlinks`，描述这个 workspace **实际**看得见什么），也有"config 播种的
allow"（`config:agents.skills`，只是个默认值）。两者谁先落盘取决于启动路径顺序——
迁移在装配前还是装配后跑、哪个 team 先激活。若是 first-writer-wins，迁移调用点挪个
位置就会静默改变某个成员的可见范围。排名让结果只由"谁的信息更权威"决定：迁移值
无论先到后到都赢过默认播种，而 `set_skill_visibility` / `update_skill_visibility`
写下的显式授权（`AUTHORITY_EXPLICIT`）任何 seed 都动不了。

旧文档没有 `authority` 字段：`_parse_authority` 按 `bootstrapped_from` 归类——有播种
标记的算 SEED，没有的说明是显式授权写的，算 EXPLICIT。升级不会把既有授权变成可被
覆盖的。

### D6：保留的拷贝例外，及判据

**判据：把 Skill 送进唯一库、或跨越进程 / 机器边界投递，是例外；一切"库内再分发"
取消。** 按此保留三处：

| 保留项 | 位置 | 为什么不是库内分发 |
|---|---|---|
| `.team` 挂载点三级降级（symlink → junction → copytree） | `team_workspace/manager.py::_mount_directory` | 它服务的是团队**产物**协同，不服务 Skill。受限运行时下产物必须仍能到达 agent，所以 copytree 兜底留着——但它下面已经没有 `skills/` 可拷 |
| Skill 安装入库 | 安装 / marketplace 流 | 这是把 Skill **送进**唯一库，不是从库里复制出去 |
| 内置 Skill 播种 | 首次启动 | 同上，方向是进库 |

对应地，`workspace_layout.ensure_team_member_workspace_link` 的 copytree 兜底被删除
（不向后兼容）：它在无法建符号链接时整份复制成员 workspace，连带复制 Skill 目录，
复制出来的副本随即与原件漂移。现在改为让成员继续在自己的独立 workspace 里跑。

### D7：演进 rail 不再落在成员私有目录

`agent/scheduling/review_feedback_evolution.py::_build_default_member_rail` 原来建
`MemberSkillEvolutionRail`，两个目录（成员 `skills/` + 全局），copy-on-write 到成员
私有目录。现在改为直接建 `SkillEvolutionRail(global_rail.evolution_store.base_dir)`。
`MemberSkillEvolutionRail` 与 `harness/rails/evolution/member_skill_workspace.py`
在 harness 里原样保留，只是 team 不再使用它们。

### D8：RPC 授权面（jiuwenswarm）

`skills.visibility.get` / `.set` / `.update` 三个方法（`ReqMethod.SKILLS_VISIBILITY_*`），
handler 在 `server/runtime/skill/skill_manager.py`。`get` 返回原始 allow / deny 与
合成后实际生效的启用 / 禁用集合；`set` 全量替换；`update` 增量增删，读-改-写在同一次
加锁内完成。三者写入的文档都是 `AUTHORITY_EXPLICIT`。

### D9：存量迁移

`jiuwenswarm/common/utils.py::migrate_team_skill_views` 在启动时扫
`get_agent_teams_home()` 下每个 team：读旧 `skills/` 视图目录暴露的名字，按
`AUTHORITY_MIGRATION` 播种进声明文件，再清理视图。清理只删三种形态——POSIX 软链、
Windows junction / reparse point、与库逐字节一致的沙箱副本；其它真实目录是用户手放
的，保留并记日志。播种前先与库里实际存在的 Skill 名求交集：视图目录里可能有用户
手放的目录，那种名字永远解析不到 Skill，却足以把"空 allow = 继承全库"变成一份永久
收窄的名单；交集为空时按空视图处理，播成不受限文档。视图覆盖全库时记为空 allow
（继承全库），不冻结成快照。
metadata 先写、后删目录：中途失败留下的是正确文档，下次重跑只补删除。幂等。

## 拒绝的方案

| 方案 | 出处 | 当初解决什么 | 为什么现在不要 |
|---|---|---|---|
| **逐 skill 软链视图**：成员 `skills/` 下为每个可见 Skill 建一条软链 | claw `7720f45d6`（2026-06-04，`manage runtime skills with shared links`） | 让成员不必各存一份 Skill，同时又能按成员收窄可见范围 | 把"可见性"和"分发"绑死在一个机制上：改可见性要动文件系统，而不是改一行 metadata。链接会断（库里 Skill 卸载 / 改名）、要有专门的 refresh rail 去补，且 Windows / 沙箱各有一套兜底。可见性现在是 JSON 里的两个数组，改它不碰磁盘 |
| **team 级整目录 copytree**：把库整目录拷进 team workspace | claw `d96bb3114`（2026-04-24，`optimize skills copying logic in team mode`） | 规避逐文件拷贝的性能与遗漏 | 拷出来的那份从落地那一刻就开始漂移：库里装新 Skill、演进改了 `SKILL.md`，团队看到的仍是旧的。而且 team workspace 归产物协同，塞进一份 Skill 库让 `.team` 挂载点顺带把它扩散给所有成员，可见性完全失控 |
| **HarmonyOS 沙箱 copytree 兜底**：建软链失败时退化为拷贝 | claw `965f40263`（2026-07-25，鸿蒙 PC 软链创建失败导致 team skill_tool 找不到指定 skill） | 修一个真实现场：受限运行时不允许建软链，team 的 skill 工具找不到 Skill | 它是在给「视图必须落到成员目录下」这个前提打补丁。前提取消后，受限运行时不需要任何兜底——成员本来就直接读库路径，没有要建的链接。`.team` 挂载点的 copytree 降级另有理由（产物协同）并被保留 |
| **per-member `skills_state.json`**：成员 workspace 各存一份技能状态文件 | claw `cc854c2e0` / `f386b8935`（2026-04-17，member-specific skills selection + `MemberSkillToolkitRail`） | 第一次把"每个成员看见哪些技能"表达成数据而不是目录，方向是对的 | 文件名与语义都撞库根那份 `skills_state.json`（库级全局开关，marketplace 写），两份同名文件不同含义必然有人读错；而且它只有"启用"一维，表达不了"团队 deny 成员撤不掉"。现在拆成 `skills-visibility.json`（allow / deny，成员与团队各一份，合成时 deny 优先），库根那份保持它原来的唯一含义 |
| **成员级 copy-on-write**：演进时把 Skill 从库复制进成员私有 `skills/` 再改 | ojw `F_73_reviewer-feedback-skill-evolution-boundaries.md` | 让成员演进不污染全局 Skill | 它需要成员拥有一个 Skill 目录，而这正是本次要拆掉的东西。写隔离是个真问题，但不能靠"再复制一份实体"解决（复制出来的副本立刻与库漂移，且没人负责合回去）。本次的取舍是先取消副本、演进直接写唯一库，写隔离另案处理（见「已知遗留」） |
| **直接改 harness 的 `SkillUseRail` 与 `factory`** | 本次改动的上一版，已回退 | 想让"单一基目录 + 名单过滤"成为全局语义，看起来最省事 | 波及两仓约 100 个文件，改动散布 `harness/rails/skills/`、`harness/rails/evolution/`、`harness/factory.py`、`harness/workspace/`、`rsi/`、`symphony/`、`agent_evolving/`。一个只属于 team 的问题把单 agent、RSI、symphony 全部拖下水，评审面与回归面都不可控。改为 team 作用域实现：`TeamSkillUseRail` 继承 + configurator 关闭通用 rail，harness 一字未改 |

## 验证

- agent-core 全量 14852 passed / 1 failed，唯一失败是既有环境问题
  （`test_execute_javascript_code_success`，Node 25 的 ANSI 输出），零新增失败。
- agent-core `tests/unit_tests/{harness,agent_teams}` 单独跑 5194 passed / 0 failed
  —— 这条是 D2 的直接证据：单 agent 的 `SkillUseRail` 路径行为未变。
- jiuwenswarm 全量 4748 passed / 4 failed，四个失败全是既有本机环境问题（3 个
  `test_message_handler_security_review_prompt`、1 个
  `test_warm_key_normalizes_project_directory`），零新增失败。
- 新增单测覆盖：声明文档读写 / 损坏降级 / authority 排名（`test_skill_visibility.py`）、
  路径布局（`test_skill_visibility_paths.py`）、跨进程锁（`test_skill_file_lock.py`）、
  rail 过滤与签名（`test_team_skill_use_rail.py`）、装配声明（`test_team_skill_assembly.py`）；
  claw 侧覆盖 RPC、迁移与库装配。

## 已知遗留

明确不在本次范围，各自另案：

1. **成员演进写隔离**。演进现在直接写唯一库，两个成员同时演进同一个 Skill 谁后写谁赢。
2. **`evolutions.json` 的 scope**。演进记录写在库里 Skill 目录下，没有"这条经验属于哪个
   成员 / 哪个团队"的维度。
3. **库内实体的跨进程锁**。本次只给 `skills-visibility.json` 加了写锁
   （`skill/file_lock.py`）；Skill 实体本身的安装 / 卸载 / 演进之间没有跨进程互斥。
4. **外部 CLI agent 成员（claude / codex）Skill 能力归零**。它们原先靠成员 workspace 下的
   `skills/` 目录被自己的 CLI 扫到，目录取消后这条通路没了。**已裁定接受**——恢复它需要
   给外部 CLI 单独设计一条按可见性生成视图的投递机制，那是另一个特性。
5. **`_get_mirror_skills_dirs` 清理**（claw 侧，`server/runtime/skill/skill_manager.py`）。它与
   team 视图无关——开发模式下把技能镜像回源码仓的 `resources/agent/skills`，安装包模式恒返回
   `[]`。本次没碰它，但"库只有一份实体"之后这个镜像目录的定位需要重新评估，另案。
