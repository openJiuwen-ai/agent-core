# 可演进 workspace（A/B/C 三类文本演进机制）

`openjiuwen.agent_teams.team_workspace` 子系统的设计规约：A 提示词 / B DB 值 / C tool 描述三类文本统一 `frontmatter + body` 落盘、装配写基线、改文件 + 重启生效。本文描述"系统当前是什么样"。

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/team_workspace/{assembler,workspace_store,workspace_cache,frontmatter,layout}.py`、`openjiuwen/agent_teams/prompts/loader.py`、`openjiuwen/agent_teams/tools/locales/__init__.py`、`openjiuwen/agent_teams/tools/tool_factory.py`、`openjiuwen/agent_teams/agent/agent_configurator.py`（`_assemble_member_workspace`）、`openjiuwen/agent_teams/agent/team_agent.py`（`share_workspace_cache_with` + `invalidate_workspace_cache`）、`openjiuwen/agent_teams/runtime/manager.py`（`finalize` pause 路径调 invalidate）、`openjiuwen/agent_teams/schema/blueprint.py` + `schema/team.py`（`evolution_enabled`） |
| 最近一次修订日期 | 2026-08-24 |
| 关联 feature | `features/F_82_evolvable-workspace.md`、`features/F_85_workspace-md-io-optimization.md` |

## 范围 / 边界

**这个规约管：**

- A/B/C 三类文本的写盘（`WorkspaceAssembler` / `WorkspaceStore`）与读侧演进覆盖（`WorkspaceCache` + 两个读侧工厂）。
- `frontmatter.py` 原语（`body_sha256` / `read_frontmatter` / `write_frontmatter` / `atomic_write`）。
- 路径单一真相：workspace 根（顶层 `paths.team_workspace_dir`）+ workspace 内子路径（`WorkspaceLayout`）。
- 装配点 `_assemble_member_workspace` 的写盘 + attach + lazy get 生命周期与 S7 缓存共享。B-member 另在 `spawn_member` 写 db 前经 `write_member_identity` 取演进值写 db 快照 + prime cache（见不变量 10/11），`_assemble_member_workspace` 仍是 spawn 期装配（幂等重跑）。

**这个规约不管：**

- 成员—团队拓扑、拉平、junction、引用计数（binder / ref_store）。
- message/task 正文的 session 文件外置——归 `S_23_session-file-data-store`。
- tool 的 `type`/`required`/`enum` 等 schema 结构——归 `S_12_schema-data-models`。
- 共享 workspace 的锁/版本/同步（`TeamWorkspaceManager` / `WorkspaceFileLock` 等）——归 `S_13_team-workspace`。

## 不变量

1. **统一形态**：三类文本都是 `YAML frontmatter + body` 文件，frontmatter 必含 `baseline_sha256`；A 类另含 `kind: prompt` + `name` + `language`，B 类 `kind: member|team`，C 类 `kind: tool|tool_params`。
2. **演进判定唯一依据**：`body_sha256(body) != frontmatter.baseline_sha256` → 已演进；hash 一致 → 未演进；无 frontmatter → 视为已演进；**畸形 frontmatter（YAML 解析失败或非 mapping 根）→ 文件无效**——读侧回退默认（不认 body），写侧可重建基线。
3. **已演进文件永不覆盖**：写盘对已演进文件（规则 2 判定）一律跳过；未演进文件随框架默认变化（hash 不等）自动用新默认覆盖并更新基线；无效文件（畸形 frontmatter）可重建基线。
4. **读侧单例**：整个 team 一个 `WorkspaceCache` 实例，挂 `TeamWorkspaceManager`（`attach_workspace_cache`），`TeamBackend.workspace_cache` property 委托 manager。**消费约定**：有 `TeamBackend` 对象的一律经 `backend.workspace_cache` 取（rail 工厂 / tool factory / scheduler / tiny agent / external CLI / handler）；仅两个声明例外——`ExternalTeamClient`（自建 manager、无 backend 对象）与 `TeamWorkerBackend`（仅 build_context、无 backend 对象）走 manager / extras 直取，代码内注释标明。
5. **lazy get + 写侧 fill 双填，稳态零文件 IO**：cache 不主动 build/扫描——`get*` 是 dict 查找，miss 时读一次文件填进 dict 后返回，hit 零 IO（无 probe、无 mtime、无 stat）；**写侧 assembler 把最新 body 直接 `fill_*` 进 cache**（每个写分支手里已握最终值：框架默认或演进值），读侧命中零 IO——**同一文件在一次 run 内最多读一次、只算一次 hash**；运行期不热更新（改文件 → 下次 run 生效）。
6. **工厂零侵入**：`make_template_loader(ws_cache=None)` 与 `make_translator(lang, ws_cache=None)` 默认 `None` 时与原行为完全等价；`ws_cache` 非 None 时演进值优先、framework/DB 回退（参数名 `ws_cache`——遵守 G.VAR.03，不与 `functools.cache` 冲突）。
7. **Runner finally 失效**：cache 失效点在 `RuntimeManager.finalize` 的 pause 路径（`agent.invalidate_workspace_cache()`），每 run 边界执行一次，清空 dict（不读文件）；下次 run 的第一次 `get*` 重新 lazy miss 读演进值。stop 路径对象 GC，无需失效。teammate 经 `share_workspace_cache_with` 共享 leader 的 manager 引用（同一 cache 实例），不 build 自己的。
8. **路径单一真相**：`"team-workspace"` 字面量只在顶层 `paths.py: team_workspace_dir` 一处；`prompts/system` / `prompts/tool` / `prompts/identity` / `tool.param.*` / `MEMBER_IDENTITY_REL` 只在 `WorkspaceLayout` 一处。全仓 `grep "prompts/system"` 命中收敛到 `layout.py`。
9. **`evolution_enabled` 是演进机制总开关**：`on` 写全部最新文件 + 建 cache（读侧演进值覆盖框架默认 / DB）；`off` **不写文件、不建 cache**（`manager.workspace_cache` 为 `None`，读侧自然回退框架默认 / DB 裸值）、已落盘文件**保留但不生效**。**开关改变必须换 session 冷恢复**——同 session `RESUME_FROM_PAUSE` 复用 agent 忽略传入 spec，改开关必走 `activate` 拆旧 agent → 重新 configure → 重新按开关建 / 不建 cache。
10. **B 类双写**：DB 列存"入队演进快照"（fallback），文件存演进值；`display_name` 不演进。B-member 的 db 快照由 `spawn_member` 写 db 前经 `write_member_identity` 取演进值写入（复用成员=演进值，首次成员=基线值）；后续演进只更 md 不回写 db。读侧 overlay 永远读 md 最新演进值，md 没演进退 db（=入队演进快照，正确）。B 类 team 级仅在 ctx 带 DB 值（`team_info` 行存在）时写。
11. **写盘三路拆分 + 幂等**：按值源与依赖分三个挂载点（`on` 时各写一次，全幂等）：
    - **A/C（系统模板 + tool 描述/参数，值源框架源）→ `coordination.start`** 团队级一次（kernel），teammate 的 `start` 重复调用幂等无害。
    - **B-team（team_card/team_prompt，值源 build_team 的 desc 参数）→ `build_team` 的 create_team 之后 + `_reattach_team`**；`team_prompt` 是只写不读到模型字段（build_team 无 prompt 参数）。
    - **B-member（card/member_prompt，值源演进 md 经 `write_member_identity` 返回）→ `spawn_member` 写 db 前（非 leader 先 `prepare_member_workspace` 建 root + 取演进值 prime cache + 写 db 快照）+ 装配期 `_assemble_member_workspace`（spawn 期幂等重跑）**。`write_member_identity` 只读/保护演进 md + prime cache + 返回演进值，**不建目录不碰 link**（root 由调用前的 `prepare_member_workspace` 保证：非 leader 在 spawn_member、leader 在 setup_agent）。db 存入队演进快照，后续演进只更 md 不回写 db；读侧 overlay 永远读 md 最新，md 没演进退 db（=演进快照）。统一覆盖所有成员（预定义/动态/HUMAN_AGENT/external_cli 全走 spawn_member）。binder reuse-first：恢复遇已有 in-team 真实目录原样复用、不补 link、不补 ref（补 link 是 legacy 迁移语义，历史会话状态不可控且收益不抵风险，详见 F_82 D8.1）。
    - **`off` 时三处都不写**（`TeamBackend._spec_evolution_enabled` 守卫）。cache 对象只在 `_attach_workspace_cache` 判断 `on` 时创建（`off` 不建，`manager.workspace_cache = None`）。in-process 队友经 `share_workspace_cache_with` 共享 leader 的 manager 引用（同一 cache 实例），复用分支（manager 已有 cache）命中即返回（S7 read-once）。

## 接口契约

### `frontmatter.py`（原语）

```python
def body_sha256(body: str) -> str: ...
    # body 的 sha256 十六进制摘要（frontmatter 基线比对用）

def read_frontmatter(text: str) -> tuple[dict, str]:
    # 解析 "---\n...\n---\n<body>"；无 frontmatter → ({}, text)（手写 body）
    # 畸形（YAML 解析失败 / 非 mapping 根）→ 抛 ValueError（文件无效）
    # 返回 (meta dict, body str)

def write_frontmatter(meta: dict, body: str) -> str:
    # meta 序列化为 YAML frontmatter + body

def atomic_write(path: Path, text: str) -> None:
    # tmp + replace 原子落盘
```

### `WorkspaceCache`（读侧单例）

```python
class WorkspaceCache:
    # 对象只在 evolution_enabled=True 时创建——off 时 _attach_workspace_cache 不建
    # （manager.workspace_cache = None），读侧自然回退。对象不带开关字段。
    def __init__(self, store: WorkspaceStore, team_name: str, *, language: str = "cn") -> None: ...

    # 读侧（lazy get）
    def get_template(self, name: str) -> PromptTemplate | None: ...   # A，lazy miss 读 system/<name>.<lang>.md
    def get_member_field(self, member_name: str, field: Literal["desc", "prompt"]) -> str | None: ...  # B member
    def get_team_field(self, field: Literal["desc", "prompt"]) -> str | None: ...  # B team
    def get_tool_md(self, desc_key: str) -> str | None: ...            # C tool 级，lazy miss 扫描 tool/
    def get_tool_param(self, desc_key: str, param: str) -> str | None: ...  # C 参数级，lazy miss 读 tool.param.<lang>.md

    # B 类 mtime 探针（lazy get，同 FileContent 的 updated_at，0=缺文件/演进 off）
    # 读侧判据，不读 body：roster/team_info/identity 三条 mtime 通道的驱动信号
    def get_member_updated_at(self, member_name: str, field: Literal["desc", "prompt"]) -> int: ...  # B member 单成员单字段
    def get_team_updated_at(self, field: Literal["desc", "prompt"]) -> int: ...  # B team 单字段

    # 写侧 fill（组装器已持有最终 body——框架默认或演进值——直接入 dict，读侧零 IO）
    def fill_template(self, name: str, body: str | None) -> None: ...
    def fill_member_field(self, member_name: str, field: Literal["desc", "prompt"], body: str | None) -> None: ...
    def fill_team_field(self, field: Literal["desc", "prompt"], body: str | None) -> None: ...
    def fill_tool_md(self, desc_key: str, body: str | None) -> None: ...
    def fill_tool_param(self, desc_key: str, param: str, text: str | None) -> None: ...
    def mark_tools_loaded(self) -> None: ...  # 写侧已填全部 C 条目，标记扫描完成

    def invalidate(self) -> None: ...
        # 清空 dict（不读文件）；Runner finally pause 路径每 run 调一次
```

**lazy get + fill 语义**：`get*` 是 dict 查找，miss 时读单个文件填 dict 后返回，hit 零 IO。**fill 模型**：cache 存**最新值**（不是"演进覆盖层"）——写侧把每个文件最终的 body（框架默认或演进值）`fill_*` 进 dict，读侧命中即服务，无演进判断（`None` 仅表示文件缺失）。无 `build`/`rebuild`/`is_built`——cache 创建即空，写侧 fill + 读侧 lazy 两条路径填充。`invalidate` 是唯一清空路径（Runner finally 调），下次 `get*` 重新 miss。**同一文件在一次 run 内最多读一次、只算一次 hash**（写侧已判演进并 fill，读侧不重读）。

### `WorkspaceStore`（B 类读写）

```python
class WorkspaceStore:
    def __init__(self) -> None: ...  # 路径直取 agent_teams.paths 函数

    # 写方法返回「文件最终 FileContent」：演进值（保护）、新写入 baseline、或 None（text 为空）。
    # 一次读/写即得 body + updated_at —— assembler 直接 fill cache 的 overlay 与 mtime 两个通道。
    def write_member_prompt(self, team_name: str, member_name: str, text: str | None, *, now: int | None = None) -> FileContent | None: ...
    def write_card(self, team_name: str, member_name: str, desc: str | None, *, now: int | None = None) -> FileContent | None: ...
    def write_team_prompt(self, team_name: str, text: str | None, *, now: int | None = None) -> FileContent | None: ...
    def write_team_card(self, team_name: str, desc: str | None, *, now: int | None = None) -> FileContent | None: ...

    def read_card(self, team_name: str, member_name: str) -> str | None: ...
    def read_member_prompt(self, team_name: str, member_name: str) -> str | None: ...
    def read_team_card(self, team_name: str) -> str | None: ...
    def read_team_prompt(self, team_name: str) -> str | None: ...

    # roster mtime overlay 探针：读 B 类文件 frontmatter 的 updated_at（缺则补写）。
    def read_member_updated_at(self, team_name: str, member_name: str, field: Literal["card", "prompt"]) -> int: ...
    def read_team_updated_at(self, team_name: str, field: Literal["card", "prompt"]) -> int: ...

    def team_workspace_root(self, team_name: str) -> Path: ...  # = paths.team_workspace_dir(team_name)
```

写盘侧全部经 `_evolved_content(path)` 实现"已演进不覆盖"不变量：`_evolved_content` 一次 `parse_file_content` 读文件判演进，**演进返回 `FileContent`（写方法保留该值、跳过写、连同其 `updated_at` 一并回填 cache）、未演进 / 缺失 / 畸形返回 `None`（可写）**。新写入 baseline 时用 `_content_from_parts(meta, body, ts)` 就地构造 `FileContent`（无需回读文件）。演进分支打 `"[workspace] %s evolved — write skipped (evolution wins)"` 日志（与 assembler A/C 侧一致，供 ST 计数断言）。B 类 member 读写统一经 `agent_teams.paths.team_member_workspace_dir`（`workspaces/<member>_workspace`，链接透明到 real dir）——所有成员（leader / predefined / dynamic）同一入口，无 `mode` 分发、无 real-dir probe（211）。

`FileContent`（`file_content.py`）是单文件读出的值对象：`kind / name / language / baseline_sha256 / updated_at / body / evolved`（`evolved` 忠实存 frontmatter 原值，写时恒 `False`，读侧不信，用 `is_evolved()` 派生：空 `baseline_sha256`→手写→演进；否则 body hash 偏离 baseline→演进）。`parse_file_content(path)` 读一次文件得全部字段；`updated_at` 缺失时回补（meta-only，保 body + baseline，不影响 `is_evolved`）；手写文件（无 frontmatter）不回补、不触碰，`updated_at=0`。

### `WorkspaceAssembler`（写侧）

```python
class WorkspaceAssembler:
    def __init__(self, store: WorkspaceStore | None = None,
                 cache: WorkspaceCache | None = None) -> None:
        # cache 非 None 时每个写分支 fill（off/单 agent 时为 None，fill 跳过）

    def write_system_and_tool_prompts(self, *, team_name: str, language: str = "cn") -> None:
        # A 类全量（rglob prompts/<lang>/）+ C 类全量（tool md + tool.param JSON）；值源框架源，不依赖 DB
        # 挂载点：coordination.start（团队级一次）

    def write_team_identity(self, *, team_name: str,
                            team_desc: str | None, team_prompt: str | None) -> None:
        # B 类 team 级（team_card.md + team_prompt.md）；值源 build_team 的 desc 参数
        # 挂载点：build_team（create_team 后）+ _reattach_team

    def write_member_identity(self, *, team_name: str, member_name: str,
                              member_desc: str | None, member_prompt: str | None
                              ) -> tuple[str | None, str | None]:
        # B 类 member 级（card.md + member_prompt.md）；值源演进 md；挂载点：spawn_member 写 db 前 + 装配期
        # 只写/保护演进 md → prime cache → 返回演进值(供 db 写入)；不建 workspace 目录
        # root(link/真实目录)由调用前的 prepare_member_workspace 保证(spawn_member 对非 leader、setup_agent 对 leader)
```

### `WorkspaceLayout`（路径单一真相，无状态静态方法）

```python
PROMPTS_SYSTEM = "prompts/system"      # A 类（lang 后缀）
PROMPTS_TOOL = "prompts/tool"          # C 类（md + param）
PROMPTS_IDENTITY = "prompts/identity"  # B 类 team（无 lang）
TEAM_CARD_FILE / TEAM_PROMPT_FILE / MEMBER_CARD_FILE / MEMBER_PROMPT_FILE
TOOL_PARAM_FILE_FMT = "tool.param.{lang}.md"   # C 参数级（tool/ 根平铺）
MEMBER_IDENTITY_REL = "prompts/identity"       # B 类 member（相对成员真实目录）

class WorkspaceLayout:
    # workspace 根内路径（收 workspace_root: Path 参数，不自行拼根）
    system_dir / system_file / iter_system_files
    tool_dir / tool_md_file / tool_param_file / iter_tool_md_files / iter_tool_param_file
    team_identity_dir / team_card_file / team_prompt_file
    # 成员目录内路径（收 member_dir: Path 参数）
    member_card_file / member_prompt_file
    # framework 源根（相对 agent_teams 包根，内部持锚点常量）
    framework_prompts_dir / framework_prompt_file / iter_framework_prompt_files
    framework_descs_dir / iter_framework_desc_files
```

### 读侧工厂

```python
def make_template_loader(cache: WorkspaceCache | None = None) -> TemplateLoader:
    # 返回与 load_template 同签名闭包：cache 演进值优先，framework md 回退

def make_translator(lang: str = "cn", ws_cache: WorkspaceCache | None = None) -> Translator:
    # key="_desc"（tool 级）：ws_cache._tool_md_values → descs/<lang>/ md → STRINGS._desc
    # key="<param>"（参数级）：ws_cache._tool_params → STRINGS.<key>.<param>
```

`ws_cache=None` 时闭包与旧 `make_translator(lang)` / `load_template` 完全等价——11 个 `make_translator` 调用点与 N 个 `load_template` 调用点零改动。装配点显式传 ws_cache：`create_team_tools`（`tool_factory.py`）内部从 `agent_team.workspace_cache` 取（不另设参数——backend 已委托 manager，见不变量 4）做 `make_translator(lang, ws_cache=...)` + `make_template_loader(ws_cache)`；rails / scheduler / tiny agent / external CLI 同理从 `backend.workspace_cache` 取，仅 worker backend / external client 两个无 backend 场景从 manager 直取（代码注释标明例外）。

### `TeamBackend` overlay + mtime 探针（B 类到模型的三条通道）

B 类演进值到模型只经 `TeamBackend`（`tools/team.py`）三个 overlay 方法：`get_member` /
`list_members` / `get_team_info` 用 `cache.get_member_field` / `get_team_field` 覆盖 DB 裸值
（`_overlay_member` / `get_team_info` 内，`display_name` 不演进回退 DB 列）。下游一律经 backend
方法取，禁止直访 `workspace_cache.get_member_field`（D8 统一覆盖原则）。

三条 mtime 通道驱动 team-context 重发（`team_context.py:TeamContextTracker`，baseline 持久化在
成员 child AgentSession）：

| 通道 | backend 探针 | 探什么 | 重发判据 |
|---|---|---|---|
| team_info | `get_team_updated_at()` | `max(DB team updated_at, team_card/prompt md updated_at)` | mtime 移动 → 重发团队信息块 |
| roster | `get_members_max_updated_at()` | 全队 MAX（DB + 各成员 card/prompt md + team card/prompt md） | mtime 移动 → 重发 roster delta |
| identity（prompt 子节） | `get_member_updated_at(name, "prompt")` | 该成员 `member_prompt.md` 的 md updated_at（**纯 md，不碰 DB**） | mtime 移动 → 重发 prompt-only 增量子节 |

`TeamBackend.get_member_updated_at(member_name, field)` 纯转发
`cache.get_member_updated_at`（cache=None → 0），不查 DB——演进信号只看 md 文件版本，
DB 列 `TeamMember.updated_at` 在 prompt 演进时不动。

**身份块 prompt 重发**：身份块常量字段（member_name / display_name / member_workspace_path）
spawn 后永不变，`_IDENTITY_EMITTED` 基线门控只投递一次。`member_prompt` 是唯一可手编演进的
身份字段，已 emitted 后 `_identity_body` 探其 md mtime，移动 →
`build_identity_prompt_delta` 渲染**只含 `## 私有工作约定` 子节的增量块**（不含常量字段）
投递进对话历史；不移动 → return None（one-shot 保持）。backend 无单成员探针
（`getattr` 取不到 `get_member_updated_at`，演进 off / 旧 fake backend）→ return None。
首次投递时记录 prompt mtime 作后续比较基线。

## 装配生命周期

`AgentConfigurator` 的 evolvable 装配分两步（每次 spawn / session 恢复执行，幂等）+ 三路写盘挂载点：

**① `_attach_workspace_cache`（`TeamHarness.build` **之前**）**——cache 创建 / 复用 + attach：

1. 计算 `evolution_enabled = spec.evolution_enabled`；**`off` → 不建 cache**（`manager.workspace_cache` 保持 `None`，直接返回——读侧回退框架默认 / DB）。
2. `on` 且 manager 已带 cache（in-process 队友共享，S7 read-once）→ `backend.attach_workspace_manager(manager)` 后返回。
3. 否则 `WorkspaceCache(store, team_name, language)` **创建空对象 + attach**（不 build、不扫描）→ `manager.attach_workspace_cache(cache)` → `team_backend.attach_workspace_manager(manager)`。

**② 三路写盘（`on` 时，assembler 构造传 `cache` → 每个写分支 `fill_*`）**：

- **A/C**：`coordination.start` 的 `workspace_manager.initialize` 之后，`WorkspaceAssembler(cache).write_system_and_tool_prompts(team_name, language)`——团队级一次（值源框架源，不依赖 team row）；teammate 的 `start` 幂等重跑无害。**冷启动时序**：leader `start` 写 A/C 基线 → build_team（第一轮工具调用）→ teammate 装配读到已就绪基线。
- **B-team**：`build_team` 的 `create_team` 成功之后 + `_reattach_team`，`WorkspaceAssembler(cache).write_team_identity(team_name, team_desc=..., team_prompt=...)`；`off` 时 `TeamBackend._spec_evolution_enabled` 守卫跳过。值来自 build_team 的 `desc` 参数（不查 `get_team_info`——避免冷启动 None 坑）。
- **B-member**：`spawn_member` 写 db 前（非 leader 先 `prepare_member_workspace` 建 root link/真实目录 + 取演进值 + prime cache + 写 db 演进快照）+ `_assemble_member_workspace`（spawn 期幂等重跑），`WorkspaceAssembler(cache).write_member_identity(...)`——只写/保护演进 md 返回最终 body → `cache.fill_member_field` → 返回 body 供 db 写入；root 由调用前的 `prepare_member_workspace` 保证（非 leader 在 spawn_member、leader 在 setup_agent），write_member_identity 不建目录不碰 link。`off` 时跳过（不写不 fill，db 用 spec 裸值）。

> **时序约束**：cache attach 必须在 `TeamHarness.build`（rails mint）之前——rail 工厂构造期把 `backend.workspace_cache` 绑进 A 类 loader 闭包，若 mint 时 cache 未 attach（首次 build / COLD_RECOVER 新实例），loader 退化为 framework 只读，团队的演进提示词值永远不会到达模型。

**fill + lazy 双填**：cache 对象创建时为空；写侧 fill 后读侧命中零 IO；未 fill 的条目（如动态成员）由 `get*` lazy miss 读一次填 dict。无需 roster 名单——dynamic 成员谁查询谁读。失效点在 `RuntimeManager.finalize` 的 pause 路径（`agent.invalidate_workspace_cache()` 清空 dict，不读文件），stop 路径对象 GC。`evolution_enabled` 从 `TeamAgentSpec`（默认 true）经 `setup_team_backend` 传 TeamBackend（`_spec_evolution_enabled` 守卫写侧）+ `_attach_workspace_cache` 判断建 / 不建 cache。

## 生命周期 / 维护

- **写**：三路挂载（`on` 时）——A/C 在 `coordination.start`、B-team 在 `build_team`/`_reattach_team`、B-member 在 `spawn_member` 写 db 前 + 装配期；全部幂等。`off` 时三处都不写。
- **读**：写侧 fill + 读侧 lazy miss，run 内每个文件最多读一次；演进方改文件 → 下次 run 生效（Runner finally 失效 → 下次 run 第一次 get 重读）。
- **回退**：删文件 → 回代码默认 / DB 裸值；`off` → 全量回退（cache=None）。
- **升级**：框架默认变化 → 未演进文件自动覆盖并更新基线；已演进文件保持演进值。
- **清理**：B 类 member 文件随成员目录生命周期；team-workspace 目录随 team 根目录。
