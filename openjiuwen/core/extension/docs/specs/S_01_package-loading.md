# S_01 扩展包加载规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | `S_01 / package-loading` |
| 关联模块 | `openjiuwen/core/extension/`；消费方包括管理事件 Core Handler（未实现）与宿主启动逻辑 |
| 最近一次修订日期 | 2026-10-21 |
| 关联 feature | [F_01 声明与定序](../features/F_01_package-source.md)、[F_02 Plan 与 Runtime](../features/F_02_plan-runtime.md) |
| 设计依据 | [设计概览](../designs/extension-overview.md)、[详细架构](../designs/extension-architecture.md)、[开发计划](../designs/extension-development-plan.md) |

本规约是扩展包**加载控制面**的唯一契约维护位置：声明与安装目录、依赖与排序、Plan 与 candidate、生效记录与卸载判断、错误码。对应已完成工作项 EX-01、EX-02。管理事件的触发顺序、Registry.init，以及把 Handler 写入 EventBus 注册表，见架构目标设计与开发计划 EX-03、EX-04；9 类管理事件的 Core Handler 在 Events 目录实现。Event 机制接线后的正式契约另建或回写，并回引本文控制面条款。

## 1. 范围、术语与责任

### 1.1 责任范围

- **本规约定义：** `manifest.yaml` 字段与入口边界；安装目录布局与打开目录/zip；包级 / 包内扩展依赖与稳定排序；单包 Plan；prepare 产生 candidate 的成功/失败语义；受限登记可接受的 `handler_level`；Runtime 生效记录与卸载判断；`ActivationReport.error` 使用的 `StatusCode` 名称。
- **本规约不定义：** EventBus 的 `trigger` 语义、Bus 注册表快照、9 类管理事件的 Core Handler 时序、宿主 `config.yaml` 解析、远程仓库与多版本同时生效。
- **宿主负责：** 解析 `extensions.install_dir`（默认 `~/.jiuwenswarm/extensions`），把 `Path` 交给本模块；在进程启动流程中于适当时机发起批量安装（经管理事件，管理事件侧未实现）。
- **Events 负责：** 注册表存储、调用快照与 Handler 执行。扩展包加载在发布 candidate 时写入 Bus（发布未实现）。

### 1.2 术语

| 术语 | 含义 |
| --- | --- |
| 包根 | 含有 `manifest.yaml` 的目录 |
| 安装副本 | `<install_dir>/<package.id>/<version>/` |
| 用户来源 | 调用方传入的原始目录或 zip |
| 生效记录 | Runtime 中的 `ActivePackageRecord`，供后续依赖与卸载判断 |
| candidate | 一次 prepare 收集齐的 Handler 声明与已加载模块；发布前对业务调用不可见（发布未实现） |
| Plan | 单包的有序扩展列表与 generation |

### 1.3 事实归属

| 对象 | 事实持有者 | 修改入口 |
| --- | --- | --- |
| 安装副本文件 | 安装目录 / Package Manager | `copy_into_store` / `remove_installed` |
| 生效记录 | Runtime | 发布成功后的 `record_active`；允许卸载后的 `drop_active` |
| 在途调用计数 | Runtime | `begin_calls` / `end_calls`（由事件层在 `trigger` 进出时调用；事件层接线未实现） |
| Bus 上的 Handler | EventBus | `extension.apply` 一次同步写入（未实现） |

## 2. 声明与安装目录

### 2.1 manifest 不变量

1. 一个包根只有一个 `manifest.yaml`，`schemaVersion` 必须为 `1`。
2. `package.id` 匹配 `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`，且不含 `..`。`package.version` 为 `MAJOR.MINOR.PATCH`。
3. `extensions[].module` 相对包根；解析后绝对路径必须落在包根内且文件存在。`runtime` 仅接受 `backend`。
4. `extensions[].requires` 只指向同一包内其它扩展 id，不得自依赖，不得引用外包扩展。
5. `requires.host` 可选；若存在，为空白分隔的版本条件，表示宿主版本要求，不参与包拓扑排序。
6. 通过检查的包落在安装副本路径；来源已是该路径时就地使用。
7. 解压 zip 使用新建临时目录；关闭打开结果时只删除该临时目录。任何安装失败清理与卸载都不删除用户来源。
8. 同一 `package.id` 在安装目录中只保留一个版本目录。启动列出若见同 id 多版本，按同批重复 id 处理，不自动挑选。

### 2.2 打开与复制

| 输入 | 行为 | 失败码 |
| --- | --- | --- |
| 目录 | 就地读 manifest；包树内不得含符号链接 | 声明非法：`EXTENSION_MANIFEST_INVALID`；符号链接：`EXTENSION_SOURCE_INVALID` |
| zip | 解压到临时目录后再读 | 非法 zip、成员逃逸、符号链接成员、超限：`EXTENSION_SOURCE_INVALID` |
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
| `PackageManager.copy_into_store` / `remove_installed` / `list_installed` | `package_manager` | 安装副本读写 |
| `resolve_install_set` | `selection` | 一批安装的接受、拒绝与包序 |
| `evaluate_reload` | `selection` | 重载版本是否仍被依赖方接受 |
| `order_key` / `topological_order` | `ordering` | 稳定排序与拓扑 |
| `Orchestrator.build_plan` | `orchestrator` | 单包 Plan |
| `Runtime.prepare` / `abort` / `record_active` / `drop_active` | `runtime` | candidate 与生效记录 |
| `Runtime.begin_calls` / `end_calls` / `uninstall_decision` / `check_reload` | `runtime` | 在途计数与生命周期判断 |
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

### 3.2 版本条件

- 版本：`MAJOR.MINOR.PATCH`。
- 说明符：空白分隔子句；每子句为可选运算符 `>= <= != == > <` 加版本；缺省运算符视为 `==`。
- 空说明符匹配任意版本。
- 非法说明符在读 manifest 时为 `EXTENSION_MANIFEST_INVALID`；在宿主比较路径上可为 `EXTENSION_HOST_VERSION_TOO_LOW`。

### 3.3 生效记录字段

`ActivePackageRecord` 至少包含：`package_id`、`version`、`root`、`generation`、`requires_packages`。Package Manager 不持有生效记录。

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

generation：进程内首次成功从 `1` 起；之后每次成功替换加 `1`；重启后重新从 `1`。卸载 Plan 不递增。

包内扩展成环 → `EXTENSION_DEPENDENCY_CYCLE`。reload/uninstall 且无生效记录 → `EXTENSION_PACKAGE_NOT_INSTALLED`。install 且已传入生效记录 → `EXTENSION_PACKAGE_EXISTS`。

### 4.2 prepare 与登记

1. 一次 Plan 内所有扩展都成功加载并 `register` 后，才形成完整 candidate；否则清理本次模块并返回失败。
2. 每次 prepare 使用新的模块根名并执行入口文件当前内容；包根内相对导入必须可用。
3. 受限登记仅接受 `user`、`builtin`；其它值 → `EXTENSION_HANDLER_LEVEL_FORBIDDEN`。受限入口不提供 `PackageMetadata` 参数。
4. handler 不可调用或 `match_pattern` 非映射 → `EXTENSION_LOAD_FAILED`。
5. `abort` 只移除 candidate 模块，不删除安装副本或用户来源。

将 candidate 一次同步写入 Bus 注册表，由 `extension.apply` 完成（未实现）。写入时 Runtime 调用 `register_event_handler(..., package_metadata=...)`，传入 Plan 上已核实的 `PackageMetadata`（含 `package_id`、`package_version`、`root`）；业务 `trigger` 进入该 Handler 时，Ops 使用该登记信息。写入前业务 `trigger` 不得看见该 candidate。

`ExtensionPlan.package` 即 Events 的 `PackageMetadata`（含包根）。`ActivePackageRecord.root` 与之对应，供依赖与卸载判断。

### 4.3 卸载判断

| 条件 | StatusCode | 副作用 |
| --- | --- | --- |
| 未生效 | `EXTENSION_PACKAGE_NOT_INSTALLED` | 无 |
| 被其它生效包依赖 | `EXTENSION_PACKAGE_IN_USE` | 不删记录、不删目录 |
| `inflight_count > 0` | `EXTENSION_PACKAGE_BUSY` | 立即失败，不等待 |
| 通过 | — | 允许 `drop_active`；删安装副本由上层调用 Package Manager |

`begin_calls` / `end_calls` 成对维护在途数；无匹配的 `end_calls` → `EXTENSION_INFLIGHT_MISMATCH`。

### 4.4 报告状态

`ActivationReport.status` 只有 `SUCCESS` 与 `FAILED`。依赖未满足等业务失败写在 `error` 中。调用方未拿到 Report 时，用查询类事件核对生效记录（查询事件未实现）；查询不补做安装。

### 4.5 错误码

错误码与消息模板定义在 `openjiuwen.core.common.exception.codes.StatusCode`，号段 `201000–201015`。`OperationError.code` 使用**枚举成员名**。

| StatusCode | 适用情况 |
| --- | --- |
| `EXTENSION_PACKAGE_EXISTS` | 同 id 已生效，install 拒绝覆盖 |
| `EXTENSION_DUPLICATE_PACKAGE_ID` | 同批重复 id |
| `EXTENSION_HOST_VERSION_TOO_LOW` | 宿主版本不满足 |
| `EXTENSION_DEPENDENCY_NOT_MET` | 包依赖不满足 |
| `EXTENSION_DEPENDENCY_CYCLE` | 包或包内扩展成环 |
| `EXTENSION_DEPENDENT_INCOMPATIBLE` | 重载版本不被依赖方接受 |
| `EXTENSION_PACKAGE_IN_USE` | 卸载时仍被依赖 |
| `EXTENSION_PACKAGE_BUSY` | 卸载时有在途调用 |
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

- 安装副本的所有权在 Package Manager；用户来源永不由本模块删除。
- 生效记录在发布成功后写入（发布未实现）；卸载判断通过后删除。
- prepare 失败或 `abort` 只清理本次 `sys.modules` 条目。
- 旧 prepare 产生的函数对象在模块被 abort 后仍可执行其创建时的字节码；是否继续持有引用由调用方与事件层快照决定。

## 6. 适用限制与兼容性

- 本规约适用于同一进程内的包加载控制面。
- 不定义跨进程互斥、远程拉包、多版本同时生效或前端 Runtime。
- 公共名称、错误码或不变量变更时，必须同步本规约与 `tests/unit_tests/core/extension/`。

## 7. 验证索引

| 主题 | 测试文件 |
| --- | --- |
| 声明、安装目录、zip 防护、依赖定序 | `tests/unit_tests/core/extension/test_package_source.py` |
| Plan、prepare、生效记录、卸载判断 | `tests/unit_tests/core/extension/test_plan_runtime.py` |

管理事件与 Bus 发布的验证在对应工作项（EX-03、EX-04）实现时建立，并引用本节控制面条款。
