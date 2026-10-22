# AgentTemplate 卡片与异步加载器

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-20 |
| 范围 | `resources/template_card.py`（新）、`resources/extension_loader.py`（新增 `aload_agent_template_package`）、`resources/extension_resolver.py`（公开 `render_prompt_content`）、`resources/__init__.py` |
| 关联 spec | S_13 |
| 关联设计 | swarm-design-docs SDD-0032（Team 专家目录与按模板拉起专家成员）；agent_teams `F_125_team-expert-catalog-and-spawn.md` 是它在 Team 侧的消费者 |
| 测试基线 | `tests/unit_tests/harness/resources/` 新增 `test_template_card.py` 7；`tests/unit_tests/agent_teams` + `tests/unit_tests/harness` 全量 10493 passed / 125 skipped / 3 xfailed |
| Refs | 待定（提交前补） |

## 背景

AgentTemplate 包（`manifest.json` + persona / tools / skills ...）的解析在 harness，但只解析了
运行时需要的字段：`display_name`、`display_description`、`category`、`tags`、`version`、`avatar`
这些展示字段被丢掉，宿主（jiuwenswarm）只好在列表页、成员显示名、单 Agent 版本比较等多处自己
读 manifest。Team 要给 leader 一组「查看 / 按模板拉起专家」的工具（agent_teams F_125），列一次
清单不能为了几个展示字段把每个包完整加载一遍（persona、技能目录、工具路径都会被校验，某个路径
有问题整条就消失）。

另一个问题是加载器只有同步版本。swarm 默认 `spawn_mode=inprocess`，leader 和所有队友共用一个
事件循环；工具在协程里同步读盘会把所有成员一起卡住。

## 决策

1. **卡片与运行时 spec 分开读**（Card / Config 分离）。新增 `AgentTemplateCard`：
   `parse_agent_template_card(manifest)` 是纯函数，`read_agent_template_card(path)` 只读
   `manifest.json` 本身，不加载 persona / tools / skills。展示字段按 manifest 原样
   保存（字符串或 `{语言: 文本}`），渲染时再按语言取（`display_label` / `display_text` /
   `tag_labels`，`cn` 同时读 `zh` 键，缺失回退英文、再回退任意文本、最后回退 `name`）。卡片另带
   `runtime`（`native` / `external`）、`runtime_provider`、`runtime_config`（`runtime.config` 原样）、
   技能目录名、MCP connector 名（去空白、去重）与原始 `avatar` / `category`，宿主列表页与单 Agent 选专家
   需要的字段都能从卡片拿到——jiuwenswarm 选外部运行时专家时只读卡片决定 provider 与配置，包只在
   `create_harness` 内部完整解析一次。
2. **卡片宽松，完整加载严格**。卡片只要求 `package_type == "agent_template"`；`name` /
   `description` 缺省为空串。宿主管理页现在列出的就是"manifest 可读且类型对"的包，卡片沿用这个
   口径，包能否运行由完整加载器判定。
3. **加载器保持同步，异步入口在工作线程里跑同一份代码**。`load_agent_template_package` /
   `load_plugin_package` 签名、实现与产出都不变；新增 `aload_agent_template_package` 与
   `read_agent_template_card`，分别是 `asyncio.to_thread(load_agent_template_package, ...)` 与
   `asyncio.to_thread(load_agent_template_card, ...)`。格式规则只有一份（同步实现），两个入口的产出
   与异常天然一致。卡片同步入口是 `load_agent_template_card`，供 jiuwenswarm 同步的 Team 组装与
   专家团目录读成员显示名。
4. **公开单段渲染 `render_prompt_content(content, *, language, params)`**：按语言选文 + 替换
   `{{ name }}` 占位符，与 `render_agent_template_system_prompt` 用同一套私有实现。Team 的人设
   渲染（逐段 `strip()`、去空段）和 jiuwenswarm 设计画布的单段渲染都调用它，不再各自抄一份。

## 拒绝的方案

- **把加载器写成 sans-IO 生成器，由 `run_sync`（`pathlib`）/ `run_async`（`aiofiles`）两个驱动器执行**
  （本特性最初的实现）。格式规则同样只有一份，但加载器整个改写成 `yield from` 风格，评审和调试成本高，
  漏走驱动器的同步 IO 也没有机制拦截。实测还更慢：aiofiles 的每个操作本身就是一次线程池往返，读 20 个
  小文件约 2.5 ms，而 `to_thread` 包住整个同步加载器只往返一次，约 0.3 ms（直接同步读约 0.26 ms）。
- **显式的"先收集文件快照、再纯构建"两段**。要读哪些文件取决于 manifest 内容（persona 目录、model
  文件、MCP 文件 / 目录、子模板 ...），收集段要先知道这些就得重复一遍格式规则；路径逃逸检查也依赖真实
  文件系统的 `resolve`。
- **加载器直接改成 `async def`、删掉同步版**。`create_harness`、`DeepAgent.load_agent_template` 等
  同步调用方都要改，牵连面过大。
- **把展示字段塞进 `AgentTemplateSpec`**。它是运行时 spec（`extra="forbid"`），加入展示信息破坏
  Card / Config 分离，所有消费方都要带着用不上的字段。
- **卡片把展示字段规范化成固定的 `{zh, en}`**。宿主列表页现在返回 manifest 原值，规范化会改变它的
  输出；按原样保存、渲染时再取语言，两边都满足。
- **卡片只提供异步读取**（设计文档的写法）。jiuwenswarm 的 Team 组装与专家团目录是同步代码，也要读成员
  显示名，所以同步的 `load_agent_template_card` 是主实现，异步的 `read_agent_template_card` 包它。

## 验证

- `test_template_card.py`：i18n 显示名、缺省回退到 `name`、tags 按语言渲染并丢弃非文本项、`runtime`
  判定、卡片宽松只校验 `package_type`、只读 manifest（persona / tools 不存在也能读出卡片）、读不到 /
  非 JSON / 非对象统一报 `ValueError`。

## 已知遗留

- 插件包（`PluginSpec`）同样不带版本号，jiuwenswarm 仍自己读插件 manifest 的 `version`；是否给插件
  也加卡片另行决定（SDD-0032 §10）。
- `load_plugin_package` 没有异步入口；目前没有异步调用方。
