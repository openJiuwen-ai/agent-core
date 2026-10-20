# S_13 资源包加载（Plugin / AgentTemplate）

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/resources/`、`openjiuwen/harness/extension_binder.py`、`openjiuwen/harness/schema/extension_spec.py`、`openjiuwen/harness/schema/deep_agent_spec.py`（Spec 部分） |
| 最近一次修订日期 | 2026-10-21 |
| 关联 feature | F_22_agent-template-card-and-async-loader.md |

## 范围 / 边界

本规约定义 harness 的扩展包机制：Plugin / AgentTemplate 的 manifest 加载、解析、
绑定、回滚。`resources/` 5 文件 + `extension_binder.py` 是"平台包 → 运行中的 DeepAgent"
唯一通道。

具体覆盖：

- `resources/template_card.py`：`AgentTemplateCard` 与 `parse_agent_template_card` /
  `load_agent_template_card` / `read_agent_template_card`（只读 `manifest.json` 的展示信息）。

- `resources/extension_loader.py`：`find_plugin_manifest` / `load_plugin_package` /
  `find_agent_template_manifest` / `load_agent_template_package` / `aload_agent_template_package` /
  `normalize_package_mcps`（JSON + legacy YAML 双格式，路径 absolutize）。
- `resources/extension_resolver.py`：`resolve_plugin_parts` / `resolve_agent_template_parts`
  → `ExtensionParts`；`ResourceRef` / `ResourceKind` / `LoadRecord`；
  `render_agent_template_system_prompt` / `render_prompt_content`（模板人设 / 单段正文的纯文本渲染）。
- `extension_binder.py`：`apply_extension_hot` / `unapply_extension_hot`（绑定 / 撤销 +
  失败回滚）。
- `schema/extension_spec.py`：`PluginSpec` / `AgentTemplateSpec` / `McpServerSpec` /
  `McpDirSpec` / `SkillSpec` / `PromptSectionSpec` / `RubricSpec` / `MemorySpec`。
- `schema/deep_agent_spec.py`：`DeepAgentSpec` 携带快照（`AgentTemplateSpec.model_dump(json)`）。

不在本规约范围内：
- manifest 的声明式描述符（catalog）—— `S_12`（本 spec 消费它）。
- rails / tools / subagents 语义 —— `S_04` / `S_05` / `S_18`。
- `DeepAgent.load_agent_template_spec` 的冷恢复路径 —— `S_02`（`load_agent_template` /
  `load_plugin` / `load_harness_config` 入口在此锚定）。

## 不变量

1. **两格式 manifest**：新式 JSON（`schema_version` 等）与 legacy YAML
   （`_normalize_legacy_plugin_yaml` 先归一）都收敛到 `PluginSpec` / `AgentTemplateSpec`；
   `_CANONICAL_PLUGIN_YAML_FIELDS` 是 legacy 白名单。路径一律 absolutize
   （`_resolve_legacy_path` / `_absolutize_*`）。
2. **加载唯一入口**：`load_plugin_package(manifest_path)` / `load_agent_template_package(...)`
   是文件 → Spec 的唯一入口（异步对应 `aload_agent_template_package`）；`find_plugin_manifest` /
   `find_agent_template_manifest` 是 manifest 定位唯一入口。子模板（subagent）经
   `_load_agent_subtemplate` 递归加载。
2a. **格式规则只有一份（同步实现），异步入口在工作线程里跑它**：`aload_agent_template_package` /
   `read_agent_template_card` 分别是 `asyncio.to_thread(load_agent_template_package, ...)` /
   `asyncio.to_thread(load_agent_template_card, ...)`，同一个包在同步与异步入口下的产出或异常相同，
   且不在事件循环线程上做文件 IO。复合包的加载器（如 agent_teams 的 AgentGroup）直接调用
   `load_agent_template_package` 复用成员模板规则。
2b. **卡片与运行时 spec 分开读**：`AgentTemplateCard` 只来自 `manifest.json` 本身
   （`read_agent_template_card`），不加载 persona / tools / skills。卡片只校验
   `package_type == "agent_template"`，其余字段宽松（缺省空串 / 空元组）；展示字段
   （`display_name` / `display_description` / `tags`）按 manifest 原样保存（字符串或 `{语言: 文本}`），
   渲染时用 `localized_text` 取语言（`cn` 兼读 `zh`，再回退 `en` / 任意文本）。`runtime` 非空即
   `"external"`，`runtime_provider` 取 `runtime.provider_name`，`runtime_config` 原样取 `runtime.config`（宿主据此把专家的配置交给 provider，不必为读一个字段完整加载包）。展示字段不进入 `AgentTemplateSpec`。
3. **解析唯一入口**：`resolve_plugin_parts(spec, ctx)` / `resolve_agent_template_parts(spec, ctx)`
   产出 `ExtensionParts`（`tools` / `mcps` / `rails` / `prompt_sections` / `skills` /
   `subagents`）；prompt section 文本按 `language` 选择（`_select_content`）、参数经
   `_render_params` 渲染；skill 解析为 `ResolvedSkill`（`directory` / `mode` /
   `enabled_skills`）。
4. **绑定 / 撤销唯一入口**：`apply_extension_hot(agent, parts) -> list[ResourceRef]` 上
   DeepAgent 绑定全部 parts 并记账 `ResourceRef`；**失败时回滚本批已绑 ref**
   （`unapply_extension_hot` 撤销）。`ResourceKind`：tool / mcp / rail / prompt_section /
   skill / subagent。`LoadRecord`（`load_id` / `source_uri` / `refs`）是一次加载的账本。
5. **身份去重**：绑定前按 `ExtensionParts` 的 `_tool_identity`（card.name）等检查
   same-name / same-identity → 已存在则抛（整批失败）；prompt section 替换走
   `ResolvedPromptSection.replace_existing` 快照。
6. **模板快照**：`DeepAgentSpec` 以 `AgentTemplateSpec.model_dump(mode="json")` 普通 dict
   保存模板（模板 spec 模型见 `S_01` 配置形态，序列化见本 spec），**不保存运行时对象**；`DeepAgent.load_agent_template_spec()`
   反序列化并按 `_prepared` 保证**只应用一次**。基础 rails 先初始化、模板后热挂载——模板 Skill
   复用宿主 `SkillUseRail` 的前提，且仍位于任何模型调用之前。
7. **包 manifest 能力**：`normalize_package_mcps` / `_load_mcps_from_dir` 支持
   `mcp_dir` 目录展开；`_merge_sidecar_prompt_sections` 支持 sidecar section 文件。
8. **`resources/__init__.py` 导出面**：`AgentTemplateSpec` / `ExtensionParts` /
   `LoadRecord` / `McpDirSpec` / `McpServerSpec` / `MemorySpec` / `PluginSpec` /
   `PromptSectionSpec` / `ResolvedPromptSection` / `ResolvedSkill` / `ResourceKind` /
   `ResourceRef` / `RubricSpec` / `SkillSpec` / `find_agent_template_manifest` /
   `find_plugin_manifest` / `load_agent_template_package` / `load_plugin_package` /
   `normalize_package_mcps` / `resolve_agent_template_parts` / `resolve_plugin_parts`，以及
   `AgentRuntimeSpec` / `RuntimeSpec` / `AgentTemplateCard` / `LocalizedText` / `localized_text` /
   `parse_agent_template_card` / `load_agent_template_card` / `read_agent_template_card` /
   `aload_agent_template_package` / `render_agent_template_system_prompt` / `render_prompt_content`。

## 接口契约

```python
def find_plugin_manifest(path: str | Path) -> Path
def find_agent_template_manifest(path: str | Path) -> Path
def load_plugin_package(manifest_path: str | Path) -> PluginSpec
def load_agent_template_package(manifest_path: str | Path) -> AgentTemplateSpec
async def aload_agent_template_package(manifest_path: str | Path) -> AgentTemplateSpec
def normalize_package_mcps(mcps: Any, package_dir: str | Path) -> list[dict[str, Any]]

# template_card
def parse_agent_template_card(manifest: Mapping[str, Any]) -> AgentTemplateCard
def load_agent_template_card(manifest_path: str | Path) -> AgentTemplateCard
async def read_agent_template_card(manifest_path: str | Path) -> AgentTemplateCard
def localized_text(value: LocalizedText, language: str) -> str

def render_prompt_content(content: dict[str, str], *, language: str, params: dict[str, Any]) -> str

def resolve_plugin_parts(spec: PluginSpec, ctx: BuildContext) -> ExtensionParts
def resolve_agent_template_parts(spec: AgentTemplateSpec, ctx: BuildContext) -> ExtensionParts

class ExtensionParts:
    tools: list[Tool | ToolCard]
    mcps: list[McpServerConfig]
    rails: list[AgentRail]
    prompt_sections: list[ResolvedPromptSection]
    skills: list[ResolvedSkill]
    subagents: list[SubAgentConfig]

class ResourceRef(BaseModel):
    kind: ResourceKind
    identity: str
    extra: dict[str, Any]

class LoadRecord(BaseModel):
    load_id: str
    source_uri: str | None = None
    refs: list[ResourceRef]

async def apply_extension_hot(agent: DeepAgent, parts: ExtensionParts) -> list[ResourceRef]
async def unapply_extension_hot(agent: DeepAgent, record: LoadRecord) -> list[str]
```

错误 / 返回语义：

- 加载失败（manifest 缺失 / 格式非法 / 路径越界）→ 抛（loader 层），不产生半 Spec；同步与异步
  入口抛出同类型、同文案的异常。
- 卡片读取失败（读不到 / 非 JSON / 非对象 / `package_type` 不对）→ `ValueError`。
- 解析失败（factory_ref 不可解析 / skill 目录缺失）→ 抛 `ValueError` 族。
- `apply_extension_hot` 绑定中途失败 → 已绑 ref 全部撤销后 re-raise（本调用原子性）。
- `unapply_extension_hot` 返回解除绑定的标签列表（供宿主日志 / UI）。
- `DeepAgent.load_agent_template_spec` 重复调用（`_prepared`）→ 无操作。

## 数据结构

### 加载管线

| 阶段 | 组件 | 产物 |
|---|---|---|
| 定位 | `find_plugin_manifest` / `find_agent_template_manifest` | manifest 路径 |
| 展示（可选） | `load_agent_template_card` / `read_agent_template_card` | `AgentTemplateCard` |
| 读取 | `load_plugin_package` / `load_agent_template_package` / `aload_agent_template_package`（+legacy 归一 / 子模板递归；异步入口在工作线程里跑同步实现） | `PluginSpec` / `AgentTemplateSpec` |
| 解析 | `resolve_plugin_parts` / `resolve_agent_template_parts` | `ExtensionParts` |
| 绑定 | `apply_extension_hot` | `list[ResourceRef]` + `LoadRecord` |
| 撤销 | `unapply_extension_hot` | 标签列表 |

### ResourceKind

| kind | 绑定目标 |
|---|---|
| `tool` | `ability_manager`（card 身份） |
| `mcp` | MCP server config |
| `rail` | rail 挂载（`register_rail`） |
| `prompt_section` | `SystemPromptBuilder` section 替换 |
| `skill` | `SkillUseRail` 挂载 |
| `subagent` | `SubAgentConfig` 注册 |



### 扩展 Spec 模型（`schema/extension_spec.py` / `schema/deep_agent_spec.py`）

| 模型 | 语义 |
|---|---|
| `_ExtensionSpecModel` | pydantic 基类 |
| `McpServerSpec` / `McpDirSpec` / `SkillSpec` / `PromptSectionSpec` / `RubricSpec` / `MemorySpec` | 各资源 Spec |
| `AgentTemplateSpec` / `PluginSpec` | 模板 / 插件 manifest |
| `DeepAgentSpec`（含 `SubAgentSpec`） | 装配规约；`SubAgentSpec` 含 `factory_name` / `factory_kwargs`，由 `resources/extension_resolver` 解析成 `SubAgentConfig` |

`DeepAgentSpec` 以 `AgentTemplateSpec.model_dump(mode="json")` 普通 dict 保存模板快照。

## 与其它 spec 的关系

- 解析出的 rails / tools / subagents 语义 —— `S_04` / `S_05` / `S_18`。
- manifest 描述符解析用 `factory_ref` / `resolve_factory` —— `S_12`。
- `DeepAgentSpec` 快照模型字段 —— 本 spec（`schema/deep_agent_spec.py`）；冷恢复绑定入口 —— `S_02`。
- prompt section 语言选择用 `resolve_language` / section 装配 —— `S_06`。
- 与 `agent_teams` 的 `F_81`（AgentTemplate 快照加载）同一思想，harness 侧独立实现。
- `agent_teams/experts/`（AgentGroup 包、模板 → Team 成员组装）消费本 spec 的卡片、加载生成器与
  `render_prompt_content` —— agent_teams `S_08` / `F_125`。
