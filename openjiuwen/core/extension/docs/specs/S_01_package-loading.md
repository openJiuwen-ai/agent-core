# S_01 扩展包加载规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | `S_01 / package-loading` |
| 关联模块 | `openjiuwen/core/extension/`；消费方包括管理事件 Core Handler 与宿主启动逻辑 |
| 最近一次修订日期 | 2026-11-10 |
| 关联 feature | [F_01](../features/F_01_package-source.md)、[F_02](../features/F_02_plan-runtime.md)、[F_06](../features/F_06_package-enable.md) |
| 设计依据 | [设计概览](../designs/extension-overview.md)、[详细架构](../designs/extension-architecture.md)、[开发计划](../designs/extension-development-plan.md) |

本规约是扩展包**加载控制面**的唯一契约维护位置：声明与安装目录、依赖与排序、Plan 与 candidate、生效记录与卸载判断、错误码。对应 EX-01、EX-02。Registry、九类管理事件与 Bus 发布见 [S_02](S_02_registry-and-bus.md)。

## 1. 范围、术语与责任

### 1.1 责任范围

- **本规约定义：** `manifest.json` 字段与入口边界；安装目录布局与打开目录/zip；包级 / 包内扩展依赖与稳定排序；单包 Plan；prepare 产生 candidate 的成功/失败语义；受限登记可接受的 `handler_level`；Runtime 生效记录与卸载判断；`ActivationReport.error` 使用的 `StatusCode` 名称。
- **本规约不定义：** EventBus 的 `trigger` 语义、Bus 注册表快照、9 类管理事件的 Core Handler 时序、宿主配置解析、远程仓库与多版本同时生效。
- **宿主负责：** 确定安装目录，通过 `open_install_dir(install_dir: Path, ...)` 传入本模块；配置键和默认路径由宿主维护。宿主调用异步 `open_install_dir` 绑定安装根目录及必传的报告路径，再调用 `load_installed()`；发现、选版和排序由 Core 完成。
- **Events 负责：** 注册表存储、调用快照与 Handler 执行。扩展包加载在发布 candidate 时写入 Bus（见 S_02）。

### 1.2 术语

| 术语 | 含义 |
| --- | --- |
| 包根 | 含有 `manifest.json` 的目录 |
| 安装副本 | `<install_dir>/<package.id>/<version>/` |
| 用户来源 | 调用方传入的原始目录或 zip |
| 生效记录 | Runtime 中的 `ActivePackageRecord`，供后续依赖与卸载判断 |
| candidate | 一次 prepare 收集齐的 Handler 声明与已加载模块；发布前对业务调用不可见 |
| Plan | 单包的有序扩展列表、MCP 声明与 generation |
| MCP 声明 | 包根 `mcp.json` 中的 `mcpServers` 条目，安装期只登记、事件期建连 |

### 1.3 事实归属

| 对象 | 事实持有者 | 修改入口 |
| --- | --- | --- |
| 安装副本文件 | 安装目录 / Package Manager | `copy_into_store` / `remove_installed` / `remove_package_dir` |
| 生效记录 | Runtime | 发布成功后的 `record_active`；允许卸载后的 `drop_active` |
| 在途调用计数 | Runtime | `begin_calls` / `end_calls`（由事件层或发布路径配合调用） |
| Bus 上的 Handler | EventBus | `extension.apply` 一次同步写入（见 S_02） |

## 2. 声明与安装目录

### 2.1 manifest 不变量

1. 一个包根只有一个 `manifest.json`，`package_type` 必须为 `extension`，`schemaVersion` 必须为 `1`。
2. `package.id` 匹配 `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`，且不含 `..`。`package.version` 为 `MAJOR.MINOR.PATCH`。
3. `extensions[].module` 相对包根；解析后绝对路径必须落在包根内且文件存在。`runtime` 仅接受 `backend`。
4. `extensions[].requires` 只指向同一包内其它扩展 id，不得自依赖，不得引用外包扩展。
5. `requires.host` 可选；若存在，为空白分隔的版本条件，表示宿主版本要求，不参与包拓扑排序。
6. 通过检查的包落在安装副本路径；来源已是该路径时就地使用。
7. 解压 zip 使用新建临时目录；关闭打开结果时只删除该临时目录。ZIP 可将包根置于根目录，或经一层版本目录、两层包 ID/版本目录包装；包装链必须唯一，最多两层，`.DS_Store`、`._*` 和 `__MACOSX` 不参与包装层判定。其它多候选、夹带普通同级文件或更深包装均拒绝。任何安装失败清理与卸载都不删除用户来源。
8. 同一 ID 可以有多个磁盘版本，但只能属于一个 domain。启动选最高合法版本；仍存活进程中的包使用实际生效版本。选中版本失败不自动降级。显式安装批次中重复 ID 仍整批拒绝。
9. 可选的 `mcp.json` 与 `manifest.json` 同级。顶层只允许 `mcpServers`，其下每个 server 名匹配 `^[a-z][a-z0-9_-]{0,63}$`；条目允许 `client_type`、`server_path`、`params`、`auth_headers`、`auth_query_params`、`include_image_content`，未知字段或非法值使安装失败。`client_type` 默认 `sse`；`stdio` 必须提供非空 `params.command`，非 `stdio` 必须提供 `server_path`。文件缺失表示本包不使用 MCP。
10. `enable` 是可选布尔字段，省略时按 `true` 处理。它表示安装副本是否参与当前进程的加载与 Handler 发布；`false` 的包保留在安装目录中，但不导入包模块、不登记 Handler 或 MCP 声明。非布尔值使声明非法。

安装来源中省略 `enable` 时，安装副本会补写 `"enable": true`；启动扫描到的旧安装副本也会在加载前补写该字段。该补写只作用于受管安装副本，不修改用户提供的来源目录或 zip。

`package.domain` 为可选字符串；设置 `domain` 或 `allowed_domains` 后必须填写非空字符串。匹配保留大小写和首尾字符，不自动修正。`allowed_domains` 由宿主指定；SDK 不固定产品域名。显式安装归属不匹配报 `EXTENSION_DOMAIN_MISMATCH`，启动只过滤合法的其它域包。

### 2.2 打开与复制

| 输入 | 行为 | 失败码 |
| --- | --- | --- |
| 目录 | 就地读 manifest；包树内不得含符号链接 | 声明非法：`EXTENSION_MANIFEST_INVALID`；符号链接：`EXTENSION_SOURCE_INVALID` |
| zip | 解压后从根、单层版本目录或两层包 ID/版本目录定位唯一包根，再读 manifest | 非法 zip、成员逃逸、符号链接成员、超限：`EXTENSION_SOURCE_INVALID`；包根不明确：`EXTENSION_MANIFEST_INVALID` |
| 其它路径 | 拒绝 | `EXTENSION_SOURCE_INVALID` |
| 复制到已存在的同版本目录 | 拒绝覆盖 | `EXTENSION_INSTALL_COPY_FAILED` |
| 复制/路径危险 | 拒绝，保留用户来源 | `EXTENSION_INSTALL_COPY_FAILED` |
| 删除时无该版本副本 | 拒绝 | `EXTENSION_PACKAGE_NOT_INSTALLED` |

本模块不读取配置文件；`install_dir` 由调用方传入。

### 2.3 zip 防护上限

实现使用下列上限（与 jiuwenswarm 同类防护对齐）。超出任一上限 → `EXTENSION_SOURCE_INVALID`，临时目录清理，用户 zip 不删。

| 上限 | 取值 |
| --- | --- |
| 成员文件数 | 10_000 |
| 单文件解压后大小 | 32 MiB |
| 全部解压后总大小 | 512 MiB |
| 成员路径深度（路径段数） | 32 |

另拒绝：成员名含 `..`、绝对路径、反斜杠路径段、Unix 符号链接属性。

### 2.4 公开入口（控制面）

| 符号 | 模块 | 作用 |
| --- | --- | --- |
| `load_manifest` / `parse_manifest_mapping` | `manifest` | 读并校验声明 |
| `PackageManager.open_source` | `package_manager` | 打开目录或 zip |
| `PackageManager.copy_into_store` / `remove_installed` / `remove_package_dir` / `list_installed` / `set_enabled` | `package_manager` | 安装副本读写、整包目录收尾与启用状态更新 |
| `PackageManager.managed_versions` | `package_manager` | 逐版本校验并枚举可确认的受管副本；单个损坏目录不阻断其它版本 |
| `build_startup_plan` | `selection` | 全目录选版、检查跨域依赖并稳定排序 |
| `resolve_install_set` | `selection` | 一批安装的接受、拒绝与包序 |
| `evaluate_reload` | `selection` | 重载版本是否仍被依赖方接受 |
| `order_key` / `topological_order` | `ordering` | 稳定排序与拓扑 |
| `Orchestrator.build_plan` | `orchestrator` | 单包 Plan |
| `Runtime.prepare` / `abort` / `record_active` / `drop_active` | `runtime` | candidate 与生效记录 |
| `Runtime.begin_calls` / `end_calls` / `uninstall_decision` / `check_reload` | `runtime` | 在途计数与生命周期判断 |
| `Registry.set_enabled` | `registry` | 保留安装副本地启用或禁用一个包 |
| `ExtensionFailure` / `OperationError` / `operation_error` | `errors` | 同步失败与 Report 错误 |

## 3. 依赖、排序与选择

### 3.1 不变量

1. 包序：先满足 `requires.packages` 的拓扑序；无边时按 `SHA-256(UTF-8 package.id)` 十六进制升序，再比 id。不用 `hash()`。
2. 包内扩展序：同样算法，键为 `extension.id`；uninstall Plan 使用反序。
3. 同批重复 `package.id` → 整批失败，`EXTENSION_DUPLICATE_PACKAGE_ID`。
4. id 已在生效记录中 → 该包 `EXTENSION_PACKAGE_EXISTS`，不替换；同批其它无关包可继续。
5. 宿主版本不满足 → `EXTENSION_HOST_VERSION_TOO_LOW`。
6. 依赖缺失、版本不匹配、或依赖方已拒绝 → `EXTENSION_DEPENDENCY_NOT_MET`。
7. 环上节点 → `EXTENSION_DEPENDENCY_CYCLE`；仅被环挡住的节点 → 依赖未满足。
8. 重载时，若某依赖方的版本条件不接受新版本 → `EXTENSION_DEPENDENT_INCOMPATIBLE`，生效记录不变。
9. 启动计划中，磁盘存在精确 `(package.id, version)` 时保留真实 manifest。报告只为尚未复制完成、但已登记的目标补临时节点；相同版本的 domain 或依赖与磁盘不一致时拒绝，不以报告覆盖磁盘事实。

### 3.2 版本条件

- 版本：`MAJOR.MINOR.PATCH`。
- 说明符：空白分隔子句；每子句为可选运算符 `>= <= != == > <` 加版本；缺省运算符视为 `==`。
- 空说明符匹配任意版本。
- 非法说明符在读 manifest 时为 `EXTENSION_MANIFEST_INVALID`；在宿主比较路径上可为 `EXTENSION_HOST_VERSION_TOO_LOW`。

### 3.3 生效记录字段

`ActivePackageRecord` 包含 `package_id`、`version`、`root`、`generation`、`requires_packages` 和 `domain`。Package Manager 不持有生效记录。

## 4. Plan、candidate 与生命周期判断

### 4.1 Plan

| 字段 | 含义 |
| --- | --- |
| `plan_id` | 本次计划标识 |
| `operation` | `install` / `reload` / `uninstall` |
| `package` | 包名、版本、包根绝对路径 |
| `previous_generation` | 重载/卸载时的原 generation；纯新装可为 `None` |
| `target_generation` | 安装/重载成功后应达到的 generation；卸载等于 previous |
| `extensions` | 已按依赖排好序的入口列表 |
| `mcp_servers` | 本次安装/重载解析出的包内 MCP 声明；卸载为空 |

generation：进程内首次成功从 `1` 起；之后每次成功替换加 `1`；重启后重新从 `1`。卸载 Plan 不递增。

包内扩展成环 → `EXTENSION_DEPENDENCY_CYCLE`。uninstall 且无生效记录按安装副本与报告状态决定是否清理；reload 对
已安装且 `enable: false` 的包返回成功的空操作，保持禁用状态。其它无生效记录的 reload →
`EXTENSION_PACKAGE_NOT_INSTALLED`。install 且已传入生效记录 → `EXTENSION_PACKAGE_EXISTS`。

### 4.2 prepare 与登记

1. 一次 Plan 内所有扩展都成功加载并 `register` 后，才形成完整 candidate；否则清理本次模块并返回失败。
2. 每次 prepare 使用新的模块根名并执行入口文件当前内容；包根内相对导入必须可用。
3. 受限登记仅接受 `user`、`builtin`；其它值 → `EXTENSION_HANDLER_LEVEL_FORBIDDEN`。受限入口不提供 `PackageMetadata` 参数。
4. handler 不可调用或 `match_pattern` 非映射 → `EXTENSION_LOAD_FAILED`。
5. `abort` 只移除 candidate 模块，不删除安装副本或用户来源。

将 candidate 一次同步写入 Bus 注册表，由 `extension.apply` 完成（细则见 S_02）。写入时 Runtime 调用 `register_event_handler(..., package_metadata=...)`，传入 Plan 上已核实的 `PackageMetadata`（含 `package_id`、`package_version`、`root`）；业务 `trigger` 进入该 Handler 时，Ops 使用该登记信息。写入前业务 `trigger` 不得看见该 candidate。

`ExtensionPlan.package` 即 Events 的 `PackageMetadata`（含包根）。`ActivePackageRecord.root` 与之对应，供依赖与卸载判断。

### 4.3 卸载判断

| 条件 | StatusCode | 副作用 |
| --- | --- | --- |
| 未生效 | `EXTENSION_PACKAGE_NOT_INSTALLED` | 无 |
| 被其它生效包依赖 | `EXTENSION_PACKAGE_IN_USE` | 不删记录、不删目录 |
| `inflight_count > 0` | `EXTENSION_PACKAGE_BUSY` | 立即失败，不等待 |
| 通过 | — | 允许 `drop_active`；上层删除全部安装副本及受管 `<install_dir>/<package.id>` 目录 |

`begin_calls` / `end_calls` 成对维护在途数；无匹配的 `end_calls` → `EXTENSION_INFLIGHT_MISMATCH`。

### 4.4 报告状态

`ActivationReport.status` 只有 `SUCCESS` 与 `FAILED`。依赖未满足等业务失败写在 `error` 中。调用方未拿到 Report 时，用 `package.query` 核对生效记录；查询不补做安装。

### 4.5 错误码

错误码与消息模板定义在 `openjiuwen.core.common.exception.codes.StatusCode`，号段以代码枚举为准（本特性新增 `201021–201023`）。`OperationError.code` 使用**枚举成员名**。

| StatusCode | 适用情况 |
| --- | --- |
| `EXTENSION_PACKAGE_EXISTS` | 同 id 已生效，install 拒绝覆盖 |
| `EXTENSION_DUPLICATE_PACKAGE_ID` | 同批重复 id |
| `EXTENSION_HOST_VERSION_TOO_LOW` | 宿主版本不满足 |
| `EXTENSION_DEPENDENCY_NOT_MET` | 包依赖不满足 |
| `EXTENSION_DEPENDENCY_CYCLE` | 包或包内扩展成环 |
| `EXTENSION_DEPENDENT_INCOMPATIBLE` | 重载版本不被依赖方接受 |
| `EXTENSION_PACKAGE_IN_USE` | 卸载时仍被依赖 |
| `EXTENSION_PACKAGE_BUSY` | 有在途调用、管理占用、重复执行者或启动队列仍在执行 |
| `EXTENSION_DOMAIN_MISMATCH` | 请求交给了其它域的实例 |
| `EXTENSION_REPORT_FAILED` | 报告损坏、I/O/锁超时或操作身份失效 |
| `EXTENSION_DEPENDENCY_TIMEOUT` | 等待跨进程依赖超过配置上限 |
| `EXTENSION_PACKAGE_NOT_INSTALLED` | 未生效/未安装却要 reload、uninstall 或删除副本 |
| `EXTENSION_MANIFEST_INVALID` | 声明非法 |
| `EXTENSION_SOURCE_INVALID` | 来源非法 |
| `EXTENSION_LOAD_FAILED` | 加载或 register 失败 |
| `EXTENSION_HANDLER_LEVEL_FORBIDDEN` | 扩展试图登记 core |
| `EXTENSION_INSTALL_COPY_FAILED` | 安装目录复制或危险路径 |
| `EXTENSION_INFLIGHT_MISMATCH` | 在途计数不配对（无匹配的 `end_calls`） |
| `EXTENSION_HOST_VERSION_MISSING` | 包声明了 `requires.host` 但调用方未提供宿主版本 |

同步失败使用 `ExtensionFailure`；写入 Report 使用 `OperationError`。

## 5. 生命周期与资源责任

- 安装副本和直接父级 `<install_dir>/<package.id>` 目录的所有权在 Package Manager；用户来源永不由本模块删除。成功卸载删除该包的所有受管版本和整个包 ID 目录；目录删除失败则卸载失败并保留可重试事实。
- 生效记录在发布成功后写入；卸载判断通过并完成注册、资源和受管理副本清理后删除。
  清理失败保留记录供重试，具体编排见 [S_02 第 4.1 节](S_02_registry-and-bus.md#41-store-卸载清理)。
- prepare 失败或 `abort` 只清理本次 `sys.modules` 条目。
- MCP 声明随安装/重载登记；reload 时声明变化与卸载会移除对应连接。
- 旧 prepare 产生的函数对象在模块被 abort 后仍可执行其创建时的字节码；是否继续持有引用由调用方与事件层快照决定。

## 6. 适用限制与兼容性

- 每个 Registry 只把自己的包发布到自己的 Bus。共享报告用于同主机、同一安装空间的依赖和管理保护。
- 共享文件系统必须支持进程锁和原子替换；不提供远程拉包、同一 ID 多版本同时生效或前端 Runtime。
- 报告格式、运行锁和重启判断见 [F_05](../features/F_05_domain-install-report.md)。
- 受管版本按目录独立校验；合法的其它域版本可参与全局判断，无法验证身份的旧目录只跳过。损坏目录只有在报告或 Runtime 已知其版本时才允许进入清理流程。
- 公共名称、错误码或不变量变更时，必须同步本规约与 `tests/unit_tests/core/extension/`。
- 启用、禁用仅控制本 Registry 及其绑定 Bus 中的加载结果。已开始的业务调用不回滚，调用方应按自身停止与资源释放约束处理。

## 7. 验证索引

| 主题 | 测试文件 |
| --- | --- |
| 按域启动、共享报告、锁、重启和真实子进程 | `tests/unit_tests/core/extension/test_install_report.py` |
| 声明、安装目录、zip 防护、依赖定序 | `tests/unit_tests/core/extension/test_package_source.py` |
| Plan、prepare、生效记录、卸载判断 | `tests/unit_tests/core/extension/test_plan_runtime.py` |
| `mcp.json` 解析与声明表 | `tests/unit_tests/core/extension/test_mcp_declarations.py` |
| 安装/重载/卸载的 MCP 声明生命周期 | `tests/unit_tests/core/extension/test_mcp_package_lifecycle.py` |

管理事件与 Bus 发布的验证在对应工作项（EX-03、EX-04）实现时建立，并引用本节控制面条款。
