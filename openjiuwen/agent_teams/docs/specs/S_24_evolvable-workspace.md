# 可演进 workspace（A/B/C 三类文本演进机制）

`openjiuwen.agent_teams.team_workspace` 子系统的设计规约：A 提示词 / B DB 值 / C tool 描述三类文本统一 `frontmatter + body` 落盘、装配写基线、改文件 + 重启生效。本文描述"系统当前是什么样"。

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/team_workspace/{assembler,workspace_store,workspace_cache,frontmatter,layout}.py`、`openjiuwen/agent_teams/prompts/loader.py`、`openjiuwen/agent_teams/tools/locales/__init__.py`、`openjiuwen/agent_teams/tools/tool_factory.py`、`openjiuwen/agent_teams/agent/agent_configurator.py`（`_assemble_member_workspace`）、`openjiuwen/agent_teams/agent/team_agent.py`（`share_workspace_cache_with` + `invalidate_workspace_cache`）、`openjiuwen/agent_teams/runtime/manager.py`（`finalize` pause 路径调 invalidate）、`openjiuwen/agent_teams/schema/blueprint.py` + `schema/team.py`（`evolution_enabled`） |
| 最近一次修订日期 | 2026-08-19 |
| 关联 feature | `features/F_82_evolvable-workspace.md` |

## 范围 / 边界

**这个规约管：**

- A/B/C 三类文本的写盘（`WorkspaceAssembler` / `WorkspaceStore`）与读侧演进覆盖（`WorkspaceCache` + 两个读侧工厂）。
- `frontmatter.py` 原语（`body_sha256` / `read_frontmatter` / `write_frontmatter` / `atomic_write`）。
- 路径单一真相：workspace 根（顶层 `paths.team_workspace_dir`）+ workspace 内子路径（`WorkspaceLayout`）。
- 装配点 `_assemble_member_workspace` 的写盘 + attach + lazy get 生命周期与 S7 缓存共享。

**这个规约不管：**

- 成员—团队拓扑、拉平、junction、引用计数、迁移（binder / ref_store / migrator）。
- message/task 正文的 session 文件外置——归 `S_23_session-file-data-store`。
- tool 的 `type`/`required`/`enum` 等 schema 结构——归 `S_12_schema-data-models`。
- 共享 workspace 的锁/版本/同步（`TeamWorkspaceManager` / `WorkspaceFileLock` 等）——归 `S_13_team-workspace`。

## 不变量

1. **统一形态**：三类文本都是 `YAML frontmatter + body` 文件，frontmatter 必含 `baseline_sha256`；A 类另含 `kind: prompt` + `name` + `language`，B 类 `kind: member|team`，C 类 `kind: tool|tool_params`。
2. **演进判定唯一依据**：`body_sha256(body) != frontmatter.baseline_sha256` → 已演进；hash 一致 → 未演进；无 frontmatter → 视为已演进；**畸形 frontmatter（YAML 解析失败或非 mapping 根）→ 文件无效**——读侧回退默认（不认 body），写侧可重建基线。
3. **已演进文件永不覆盖**：写盘对已演进文件（规则 2 判定）一律跳过；未演进文件随框架默认变化（hash 不等）自动用新默认覆盖并更新基线；无效文件（畸形 frontmatter）可重建基线。
4. **读侧单例**：整个 team 一个 `WorkspaceCache` 实例，挂 `TeamWorkspaceManager`（`attach_workspace_cache`），`TeamBackend.workspace_cache` property 委托 manager。**消费约定**：有 `TeamBackend` 对象的一律经 `backend.workspace_cache` 取（rail 工厂 / tool factory / scheduler / tiny agent / external CLI / handler）；仅两个声明例外——`ExternalTeamClient`（自建 manager、无 backend 对象）与 `TeamWorkerBackend`（仅 build_context、无 backend 对象）走 manager / extras 直取，代码内注释标明。
5. **lazy get + 稳态零文件 IO**：cache 不主动 build/扫描——`get*` 是 dict 查找，miss 时读一次文件填进 dict 后返回，hit 零 IO（无 probe、无 mtime、无 stat）；运行期不热更新（改文件 → 下次 run 生效）。
6. **工厂零侵入**：`make_template_loader(cache=None)` 与 `make_translator(lang, cache=None)` 默认 `None` 时与原行为完全等价；`cache` 非 None 时演进值优先、framework/DB 回退。
7. **Runner finally 失效**：cache 失效点在 `RuntimeManager.finalize` 的 pause 路径（`agent.invalidate_workspace_cache()`），每 run 边界执行一次，清空 dict（不读文件）；下次 run 的第一次 `get*` 重新 lazy miss 读演进值。stop 路径对象 GC，无需失效。teammate 经 `share_workspace_cache_with` 共享 leader 的 manager 引用（同一 cache 实例），不 build 自己的。
8. **路径单一真相**：`"team-workspace"` 字面量只在顶层 `paths.py: team_workspace_dir` 一处；`prompts/system` / `prompts/tool` / `prompts/identity` / `tool.param.*` / `MEMBER_IDENTITY_REL` 只在 `WorkspaceLayout` 一处。全仓 `grep "prompts/system"` 命中收敛到 `layout.py`。
9. **`evolution_enabled` 只管读侧**：关时文件照常写、cache build 不读文件、所有值为 None、调用方走默认。
10. **B 类双写**：DB 列存裸值（fallback），文件存演进值；`display_name` 不演进。B 类 team 级仅在 ctx 带 DB 值（`team_info` 行存在）时写。
11. **写盘幂等 + configure 只建空对象**：`_assemble_member_workspace` 在每次 spawn / session 恢复执行，目录、基线、缓存 attach 全部幂等；**只创建空 `WorkspaceCache` 对象并 attach，不 build、不扫描文件**（值在第一次 `get*` 时 lazy 读）。文件读取全部 lazy 化。in-process 队友经 `share_workspace_cache_with` 共享 leader 的 manager 引用（同一 cache 实例），复用分支（manager 已有 cache）命中即返回（S7 read-once）。

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
    def __init__(self, store: WorkspaceStore, team_name: str, *,
                 language: str = "cn", evolution_enabled: bool = True) -> None: ...

    def get_template(self, name: str) -> PromptTemplate | None: ...   # A，lazy miss 读 system/<name>.<lang>.md
    def get_member_field(self, member_name: str, field: Literal["desc", "prompt"]) -> str | None: ...  # B member
    def get_team_field(self, field: Literal["desc", "prompt"]) -> str | None: ...  # B team
    def get_tool_md(self, desc_key: str) -> str | None: ...            # C tool 级，lazy miss rglob
    def get_tool_param(self, desc_key: str, param: str) -> str | None: ...  # C 参数级，lazy miss 读 tool.param.<lang>.md

    def invalidate(self) -> None: ...
        # 清空 dict（不读文件）；Runner finally pause 路径每 run 调一次
```

**lazy get 语义**：`get*` 是 dict 查找，miss 时读单个文件填 dict 后返回，hit 零 IO。无 `build`/`rebuild`/`is_built`——cache 创建即空，按需填充。`invalidate` 是唯一清空路径（Runner finally 调），下次 `get*` 重新 miss。同一个 run 内同一文件最多读一次（read-once）。

### `WorkspaceStore`（B 类读写）

```python
class WorkspaceStore:
    def __init__(self) -> None: ...  # 路径直取 agent_teams.paths 函数

    def write_member_prompt(self, team_name: str, member_name: str, text: str | None) -> None: ...
    def write_card(self, team_name: str, member_name: str, desc: str | None) -> None: ...
    def write_team_prompt(self, team_name: str, text: str | None) -> None: ...
    def write_team_card(self, team_name: str, desc: str | None) -> None: ...

    def read_card(self, team_name: str, member_name: str) -> str | None: ...
    def read_member_prompt(self, team_name: str, member_name: str) -> str | None: ...
    def read_team_card(self, team_name: str) -> str | None: ...
    def read_team_prompt(self, team_name: str) -> str | None: ...

    def team_workspace_root(self, team_name: str) -> Path: ...  # = paths.team_workspace_dir(team_name)
```

写盘侧全部经 `_is_evolved` / `_may_write` 实现"已演进不覆盖"不变量：`_is_evolved(meta, body)` = 无 `baseline_sha256`（手写文件）或 body hash 与基线不一致 → True；`_may_write(path)` = 目标不存在、未演进、或畸形 frontmatter（无效文件可重建）。B 类 member 读写统一经 `agent_teams.paths.team_member_workspace_dir`（`workspaces/<member>_workspace`，链接透明到 real dir）——所有成员（leader / predefined / dynamic）同一入口，无 `mode` 分发、无 real-dir probe（211）。

### `WorkspaceAssembler`（写侧）

```python
class WorkspaceAssembler:
    def __init__(self, store: WorkspaceStore | None = None) -> None: ...

    def write_team_workspace(self, *, team_name: str, language: str,
                             team_desc: str | None, team_prompt: str | None) -> None:
        # A 类全量（rglob prompts/<lang>/）+ C 类全量 + B 类 team 级；幂等

    def write_member_identity(self, *, team_name: str, member_name: str,
                              member_desc: str | None, member_prompt: str | None) -> None:
        # B 类 member 级（card.md + member_prompt.md）；统一写链接入口
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

def make_translator(lang: str = "cn", cache: WorkspaceCache | None = None) -> Translator:
    # key="_desc"（tool 级）：cache._tool_md_values → descs/<lang>/ md → STRINGS._desc
    # key="<param>"（参数级）：cache._tool_params → STRINGS.<key>.<param>
```

`cache=None` 时闭包与旧 `make_translator(lang)` / `load_template` 完全等价——11 个 `make_translator` 调用点与 N 个 `load_template` 调用点零改动。装配点显式传 cache：`create_team_tools`（`tool_factory.py`）内部从 `agent_team.workspace_cache` 取（不另设参数——backend 已委托 manager，见不变量 4）做 `make_translator(lang, cache=...)` + `make_template_loader(cache)`；rails / scheduler / tiny agent / external CLI 同理从 `backend.workspace_cache` 取，仅 worker backend / external client 两个无 backend 场景从 manager 直取（代码注释标明例外）。

## 装配生命周期

`AgentConfigurator` 的 evolvable 装配分两步（`setup_agent` 内，每次 spawn / session 恢复执行，幂等）：

**① `_attach_workspace_cache`（`TeamHarness.build` **之前**）**——cache 创建 / 复用 + attach：

1. 若 manager 已带 cache（in-process 队友共享，S7 read-once）→ `backend.attach_workspace_manager(manager)` 后返回。
2. 否则 `WorkspaceCache(store, team_name, language, evolution_enabled)` **创建空对象 + attach**（不 build、不扫描）→ `manager.attach_workspace_cache(cache)` → `team_backend.attach_workspace_manager(manager)`。

**② `_assemble_member_workspace`（`TeamHarness.build` 之后）**——纯写盘：

3. `WorkspaceAssembler.write_team_workspace`：A/C 全量基线 + B team 级（ctx 带 DB 值才写）。
4. `write_member_identity`：当前成员 B 类基线（统一写 `workspaces/<member>_workspace` 链接入口）。

> **时序约束**：cache attach 必须在 `TeamHarness.build`（rails mint）之前——rail 工厂构造期把 `backend.workspace_cache` 绑进 A 类 loader 闭包，若 mint 时 cache 未 attach（首次 build / COLD_RECOVER 新实例），loader 退化为 framework 只读，团队的演进提示词值永远不会到达模型。

**文件读取全部 lazy 化**：cache 对象创建时为空，`get*` 第一次 miss 时读单个文件填 dict 后返回，后续 hit 零 IO；无需 roster 名单——dynamic 成员谁查询谁读，configure 是 sync 的限制消失。失效点在 `RuntimeManager.finalize` 的 pause 路径（`agent.invalidate_workspace_cache()` 清空 dict，不读文件），stop 路径对象 GC。`evolution_enabled` 从 `TeamAgentSpec`（默认 true）经 ctx 传入。

## 生命周期 / 维护

- **写**：装配期（`write_team_workspace` / `write_member_identity`），任何装配路径（spawn / recover / worker）幂等，不绑 `build_team`。
- **读**：run 内第一次 `get*` lazy 读文件填 dict，后续命中零 IO；演进方改文件 → 下次 run 生效（Runner finally 失效 → 下次 run 第一次 get 重读）。
- **回退**：删文件 → 回代码默认 / DB 裸值。
- **升级**：框架默认变化 → 未演进文件自动覆盖并更新基线；已演进文件保持演进值。
- **清理**：B 类 member 文件随成员目录生命周期；team-workspace 目录随 team 根目录。
