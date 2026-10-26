# F_22 CLI 挂载 JiuwenMemoryRail 与启用开关

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-15 |
| 范围 | `cli/agent/factory.py`（装配层）；文档同步修订 `S_04` / `S_15` / `rails/AGENTS.md` |
| 测试基线 | `tests/unit_tests/harness/test_cli_jiuwen_memory_rail.py`（13 用例）、`tests/cli/integration/test_agent_factory.py`（新增 2 用例）；真实 agent-memory server 冒烟 + 真实模型 E2E |
| Refs | （待补 issue 号） |

## 背景

`JiuwenMemoryRail`（commit `39a7ce26b` 引入，设计文档在 agent-memory 仓
`jiuwen_memory_adapter/jiuwenswarm/jiuwen_memory_rail_design.md` §6.1）此前只从
`openjiuwen.harness.rails` 导出、无任何装配点——设计意图是"宿主自备 provider 注入"。
CLI 服务（`openjiuwen` REPL，经 `create_backend` → `LocalBackend.start()` →
`create_agent` 装配）作为宿主需要一条启动挂载路径，且记忆服务属可选依赖，
必须有开关控制是否挂载。

rail 的设计基线即配 agent-memory 适配包的 `AgentMemoryMemoryProvider`
（`jiuwen_memory_adapter/jiuwenswarm/agent_memory_provider.py`，经 PYTHONPATH
顶层引入）——rail 内的 jiuwenswarm 专有逻辑（project 路由 `_jiuwenswarm_project_dir`、
envelope 剥离）与该 provider 的 AgentMemory 引擎配套。CLI 作为 jiuwenswarm 宿主前端
本就依赖该适配包环境，挂载它即闭合"搜索 / conclude / md 读写"完整工具面。仓内
`openjiuwen/core/memory/external/jiuwen_memory_provider.py` 的 `JiuwenMemoryProvider`
（mem2，仅 `mem2_search`/`mem2_add` 两工具）工具面不完整，不作为 CLI 装配 provider。

## 数据结构与状态

CLI 装配层新增私有构造器 `_build_jiuwen_memory_rail(cfg)`，环境变量面：

| 变量 | 作用 | 缺省 |
|---|---|---|
| `JIUWEN_MEMORY_ENABLED` | 总开关；`1/true/yes/on`（大小写、首尾空格不敏感）才挂载 | off |
| `JIUWEN_MEMORY_BASE_URL` | agent-memory 服务地址；非空 → HTTP 客户端，空 → in-process SDK（`assemble_runtime()`） | 空（SDK 模式） |
| `JIUWEN_MEMORY_API_KEY` | Bearer 凭据（HTTP 模式可选；服务端 `--auth-mode required` 时必需） | 空 |
| `JIUWEN_MEMORY_TENANT_ID` | 租户/组织轴，映射 rail `scope_id`（scope `agent` 维） | `default` |
| `JIUWEN_MEMORY_USER_ID` | 用户记忆轴，映射 rail `user_id`（跨会话稳定） | `jiuwenswarm-user` |

挂载点：`create_agent` 的 rails 列表（`MemoryRail` 之后追加），经
`create_deep_agent(rails=...)` 进既有排队注册流。构造失败（import / provider 构造
异常）→ debug 日志 + 返回 `None` 不挂载，与 `_build_memory_rail` 同款兜底。

## 决策

1. **开关放 CLI 装配层，不进 `create_deep_agent` 参数 / `DeepAgentConfig`**：
   rail 需要 host 构造的 provider 实例，`create_deep_agent` 无法凭空构造；与
   `MemoryRail`（`EMBEDDING_*` env）同一模式，遵循 `S_01`"新增配置项走 config
   拆分而非堆参数"。
2. **provider 用 agent-memory 适配包的 `AgentMemoryMemoryProvider`（4 工具
   `agent_memory_search`/`agent_memory_conclude`/`agent_memory_read_md_file`/
   `agent_memory_edit_md_file`，HTTP / in-process SDK 双模式）**：rail 的 jiuwenswarm
   专有逻辑（project 路由 `_jiuwenswarm_project_dir`、envelope 剥离）与该 provider
   的 AgentMemory 引擎配套，CLI 作为 jiuwenswarm 宿主前端本就依赖该适配包环境。
   仓内 `JiuwenMemoryProvider`（mem2，仅 search/add 2 工具）工具面不完整，不绑定。
3. **开关必须显式真值**：记忆服务是重依赖（HTTP 连外部服务或起 in-process
   AgentMemory 引擎），不能凭 env 存在性隐式开启；真值解析沿用
   `USE_RL_ONLINE_RAIL` 的既有惯例
   （`strip().lower() in ("1", "true", "yes", "on")`）。
4. **开关语义 = 不构造不挂载**，与 rail 内置的 `provider.is_available()` 降级
   （"挂了但空转"，一条 warning）是两层独立机制，叠加使用。
5. **`user_id` 同时传 rail 与 provider 构造器**：rail 在 `before_invoke` 才把
   `user_id` 下发 `provider.initialize`，而 provider 的 `system_prompt_block`
   在 `init` 时就读 `user_id`——两边同源避免提示词与实际绑定轴不一致。

## 拒绝的方案

- **开关进 `CLIConfig` / settings.json 三层配置**：现有特性 rail（MemoryRail /
  Vision / Audio）全部 env 直读，`CLIConfig` 只承载模型面配置；保持一致不扩字段。
  需要持久化配置时再评估。
- **`JIUWEN_MEMORY_BASE_URL` 存在性当开关**：base_url 缺省空（走 in-process
  SDK），presence-based 在 SDK 模式下恒为假、无法表达“开”；且连接模式（HTTP/SDK）
  与“挂不挂”是正交两件事，不应让连接参数兼任开关。
- **挂仓内 `JiuwenMemoryProvider`（mem2，2 工具）**：mem2 仅 `mem2_search`/`mem2_add`，
  缺 `conclude`/`read_md_file`/`edit_md_file` 完整面；且 rail 的 project 路由
  （`_jiuwenswarm_project_dir`）、jiuwenswarm envelope 剥离是为 AgentMemory 引擎
  设计，mem2 provider 不消费 project 坐标。完整工具面 + 路由配套只在
  `AgentMemoryMemoryProvider` 上成立，故 CLI 选它。
- **`enable_jiuwen_memory_rail` 装配参数**：违反 `S_01` 装配约定（扩参数列表是
  hack），且无法解决 provider 从哪来的问题。

## 验证

- **单测**（13 用例）：缺省 off、真值 5 变体、假值 5 变体、env 透传
  （base_url/api_key/tenant/user_id 落到 provider 与 rail）、缺省回退
  （含 `session_id=""` 跨会话共享契约）。
- **装配级**（2 用例）：patch `init_model` / `create_deep_agent` 后断言真实
  `create_agent` 的 rails 列表含 / 不含 `JiuwenMemoryRail`。
- **真实 server 冒烟**（agent-memory offline profile + dev auth）：
  `init` 注册 `jiuwen_memory_agent_memory_search`/`_conclude`/`_read_md_file`/
  `_edit_md_file` 四工具 + 注入 `external_memory` section；`before_invoke` 后
  provider `_is_initialized=True`（healthz 连通）；`uninit` 工具撤净。
- **E2E**（真实 LLM + 真实 server，走 `create_backend` → `LocalBackend` →
  `run_streaming` 与 CLI REPL 同链路）：turn 1 `after_invoke` 的 `sync_turn` 落库
  提炼事实（"用户姓名：王小明。最喜欢的编程语言是 Python。"，infer=true 的
  EXTRACT 真实生效）；新会话 turn 2 `before_model_call` prefetch 命中并注入
  提示词，回答正确（"Python。"）。

既有 rail 生命周期测试（`test_jiuwen_memory_rail.py` /
`test_external_memory_rail.py`，37 用例）无回归。

## 已知遗留

- 开关目前仅 env 形态；settings.json / CLI 参数形态未做（见拒绝方案第一条）。
- project 路由依赖宿主在 agent 实例上绑定 `_jiuwenswarm_project_dir`
  （jiuwenswarm 每轮 refresh）；非 jiuwenswarm 宿主未绑定时 project 为 None，
  记忆落在 kernel 默认目录，不按 project 隔离。
- `session_end_extract` 维持 rail 缺省 `False`（infer=true 已同步抽取，会话末
  再跑批量 EXTRACT 属重复 LLM 开销，见设计文档 §6.1.2 差异 1）。
- `JiuwenMemoryRail` 引入提交（`39a7ce26b`）未归档 feature 文档；其导出与
  priority(75) 的 spec 漂移已在本特性提交中一并修复（`S_04` / `rails/AGENTS.md`）。
