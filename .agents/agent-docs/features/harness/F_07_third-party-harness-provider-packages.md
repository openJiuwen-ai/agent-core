# 第三方 Harness Provider 包：entry point 发现与生成 skill

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-10 |
| 范围 | `openjiuwen/harness_providers/factory.py`、`skills.py`、`__init__.py`；skill `.claude/skills/harness-provider-integration/`（脚手架、平台 wheel 构建、运行时清单） |
| 关联 spec | S_19（不变量 9、11，接口契约，Portable skills） |
| 测试基线 | `tests/unit_tests/harness_providers` + `tests/unit_tests/agent_teams/external` 393 通过；新增 `test_provider_discovery.py` 9 项 |
| 验证基线 | 干净虚拟环境非 editable 安装 openjiuwen 0.1.19（本次改动），仓库外生成三种模式的包：单测与注册测试全过；opencode 1.17.18 ACP 包 23 项全过（含 6 项真实 e2e，经 entry point 走 manifest 工厂）；自带 opencode 的 macOS arm64 平台 wheel 经 jiuwenswarm 原生 `_verify_wheel_artifact` / `_safe_extract_wheel` 安装、追加 `sys.path` 后，在 PATH 无 opencode 的进程里跑通真实文本与工具 Turn；同一 wheel 可被 pip 安装。2026-10-12：自带 opencode 1.18.31 的 wheel 并入 jiuwenswarm 内置清单后，经 `install_external_cli_runtime` 从 HTTP 源下载、校验、安装为 `current`，`resolve_provider` 加载解压出的模块，在 PATH 无 opencode 的进程里跑通真实文本 Turn。kimi-code 2.1.1 ACP 包：ACP 模板只填厂商常量，指定模型（`MODEL_CONFIG_ID`）下 36 项全过，含 6 项契约 e2e 与 4 项厂商 e2e（工具参数、MCP `mcp__{server}__{tool}`、`.kimi-code/skills` 技能、非法模型拒绝）；自带 kimi + fd 的 macOS arm64 wheel 经 jiuwenswarm 托管安装后在 PATH 无 kimi 的进程里完成 ACP 握手与建会话 |
| Refs | #2006 |

## 背景

用户本地有自己的 agent CLI（或 SDK），没有 openjiuwen 源码，只能 `pip install openjiuwen`。要把这样的
agent 接成 harness provider，产物必须是独立 pip 包，openjiuwen 装上它就能解析；而 `resolve_provider`
此前写死五个内置名，`HarnessProviderName` 是封闭 `Literal`，`install_skills` 只认内置 provider 的技能
目录，外部包没有任何注册入口。

## 决策

1. **沿用仓库已有的插件先例**（`openjiuwen.vector_stores`）：内置封闭且优先 → 进程内
   `register_harness_provider` → entry point 组 `openjiuwen.harness_providers`。组名声明为稳定公开 API。
2. **入口值是 `HarnessProvider` 本身**（类无参实例化或实例），不发明新的插件接口；SPI 已经是
   `card` + `create(config)`。
3. **加载失败大声报错**：与向量库"失败即视为未匹配"不同，provider 名是调用方显式点名的，静默降级只会
   变成一句难懂的 unknown provider。加载、实例化、SPI 不符都抛带原因的 `ValueError`。
4. **manifest 端点映射交给 provider**：可选钩子 `manifest_config(endpoint)`，`endpoint` 是不含框架类型的
   普通字典（`model / provider / api_base / api_key`），外部包不必 import manifest 模型；无钩子时只传
   `model`。显式 `config` 永远优先（`setdefault`）。**已被 [[F_08_provider-owned-model-config]] 取代**：
   改为协议级 `ModelConfigProvider.model_config(ModelChoice)`，manifest 与团队成员共用，`manifest_config`
   不再保留。
5. **技能目录由 provider 声明**：`install_skills(..., scan_dir=...)`，不往内置 `_SCAN_DIRS` 表里注册；
   越界路径沿用既有的"逃逸项目目录"校验。
6. **对外扩展面收窄并写明**：`harness_protocol`，以及 `harness_providers` 的 `base` / `jsonsafe` /
   `inputs` / `mcp_naming` / `skills` / 工厂注册接口；改动它们按破坏外部包对待。
7. **生成 skill 自包含**：pip 包不带 md 文档，skill 自带规则、模板与契约副本；脚手架在任意目录生成
   src 布局包（含 entry point、假运行时单测、注册测试、e2e 与 `contract.py`），生成即绿，厂商相关处
   以 `SCAFFOLD(topic)` 标记、`--check` 归零。三种模式：ACP 常驻、一次性 CLI、Python SDK。
   openjiuwen 可来自 PyPI，也可来自克隆的 develop 源码（`pip install -e <clone>`）：脚手架读
   `direct_url.json` 区分两者，前者写 `openjiuwen>=<已装版本>`，后者写指向 develop 的 git 依赖（从 git URL
   装的写 pip 记录的 commit）；两种都先读源码确认 factory 支持 entry point 发现，不支持就拒绝生成。

8. **动态加载单位是 provider 包，厂商 CLI 打在包内**：内置 claudecode / codex 随 openjiuwen 发布，
   jiuwenswarm 冻结版只下载它们的厂商 SDK（SDK wheel 自带 CLI）；第三方 provider 不在 openjiuwen 里，
   所以沿用同一托管运行时流程（清单 → 下载 → sha256 → 安全解压到私有 `site-packages` → 追加
   `sys.path`），下载单位上移到 provider 包。是否自带 CLI 由打包的用户选择：不自带时 wheel 是纯
   Python、一个包覆盖所有平台；自带时照 `claude_agent_sdk/_bundled/claude`、`codex_cli_bin/bin/codex`
   打进平台 wheel（`_bundled/runtime.json` + `_bundled/bin/`）。运行时选用顺序固定为 `cli_path` →
   PATH 上的 CLI → 自带 CLI，自带的只作最后兜底；用到它时才把其 `bin/` 前置到子进程 PATH 供辅助可执行
   （kimi 的 `fd`）使用；兜底的 bundle 损坏时启动失败。
9. **清单与 jiuwenswarm 同构**：`runtime-manifest.json` 沿用 `external_cli_runtime.json` 的
   `schema_version`、平台键与产物字段，顶层 `providers` 段另带 `entry_point / required_modules /
   executables`，对应其安装后的完整性校验；每个平台的产物是完整闭包（解压式安装不解析依赖）。
   jiuwenswarm 的托管运行时按同一条目结构读 `agents`（内置 provider 的厂商 SDK）与 `providers`（第三方
   provider 包）两段，claude / codex 的模块与可执行文件检查也写在清单里；第三方条目随发版并入内置清单，
   不支持终端用户运行时导入（任意 URL 加 sha256 等于执行任意代码）。
10. **构建脚本校验二进制格式**：读 Mach-O / ELF / PE 头与 CPU 类型，与目标平台不符就拒绝；平台标签
    `py3-none-<tag>`、`Root-Is-Purelib: false`、bundled 可执行强制 0755、重算 `RECORD`。

11. **ACP 模板两种选模型方式并存**：opencode 只接受启动参数、拒绝会话内 `set_config_option`；kimi-code
    正相反。模板用 `MODEL_ARGS`（启动参数）与 `MODEL_CONFIG_ID`（会话内切换，建会话 / 加载会话后调用
    并核对回报的 `currentValue`，不在可选列表里以 `request_rejected` 启动失败）由取证结果二选一，
    都没有时拒绝配置的模型。工具 STARTED 只在拿到参数或调用结束时发出（kimi 先发不带参数的
    `in_progress`）。

## 拒绝的方案

- **在 openjiuwen 内维护一张外部 provider 白名单或配置文件**：每接一个厂商都要发 openjiuwen 版本，
  与"用户本地自助接入"矛盾。
- **新增独立插件接口（如 `HarnessPlugin`）**：`HarnessProvider` SPI 已足够，多一层只多一份适配代码。
- **entry point 名与内置同名时覆盖内置**：会让安装一个包静默改变内置行为；内置封闭。
- **让外部包往 `_SCAN_DIRS` 写入**：模块级可变全局 + 导入时副作用，顺序依赖且难以测试。
- **生成的包复用仓库测试基建（`tests.test_logger`、`_contract.py`）**：用户机器上没有仓库；改为包内副本。
- **为每个 ACP 厂商复制一份 ACP 客户端代码长期并存**：第二个 ACP 厂商出现时抽共享基础包，skill 中写明。
- **openjiuwen 自己扫描插件目录、直接从 `.whl` 文件导入**：依赖、C 扩展、版本冲突都无人负责；安装
  （下载、校验、解压、闭包）是宿主的职责，openjiuwen 只负责从 `sys.path` 上发现入口。
- **只允许用户在宿主机另装 CLI**：桌面版用户无法保证装了 CLI，故保留可选的自带兜底。
- **自带 CLI 优先于 PATH**（与 claude-agent-sdk 的查找顺序一致）：用户已装并登录、按自己习惯配置的
  CLI 应当被尊重，自带的版本只在找不到时顶上。

## 实测事实（影响模板设计）

- opencode 1.17.18 ACP：`tool_call` 以 `pending` + 空 `rawInput` 出现，参数在 `in_progress` 更新里；
  `title` 先是工具名后变成路径；`session/set_config_option` 对其列出的模型也回 `-32602`，因此模型走
  启动参数；`session/cancel` 后 `stopReason: cancelled` 且会话可续；`session/load` 可用；MCP 工具在模型
  侧名为 `{server}_{tool}`。
- 运行中进程 `pip install` wheel 后，下一次 `resolve_provider` 即可解析；editable 安装与升级已导入的包
  需要重启进程。

## 已知遗留

- 只在 macOS arm64 上实测了自带 CLI 的平台 wheel；其它平台只有格式检查，需在对应平台跑一次冒烟。
- 第三方 provider 作为团队外部成员、jiuwenswarm 设置页上传安装已由 agent_teams
  [[F_117_third-party-harness-provider-members]] 补齐；内置清单 `providers` 段的条目目前仍只有安装接口，
  没有设置页入口。
- 生成包的 openjiuwen 依赖下限取生成时的已安装版本；本改动发布后需把下限改为首个包含 entry point
  发现的正式版本。
- kimi-code 额度错误与认证错误同为 `-32000` 且无结构化字段，只能归 `auth_required`；等厂商给出结构化
  区分再细分为 `quota_exceeded`。
