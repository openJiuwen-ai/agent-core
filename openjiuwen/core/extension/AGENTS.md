# Extension 开发与文档维护规范

本目录维护 Core 的扩展包管理与加载机制及其设计、规约、特性记录和开发计划。
通用开发与文档要求采用 [Core 公共规范](../AGENTS.md)，本文仅补充模块职责和维护入口；
当前行为通过实现与测试核实，已确认契约由主责规约维护。两者不一致时，按
[Core 同步流程](../AGENTS.md#特性设计与契约同步)处理，并区分目标设计与已实现能力。

## 适用范围与规则入口

- 本文适用于 `openjiuwen/core/extension/` 及其全部子目录。先遵循
  [仓库根规则](../../../AGENTS.md)和 [Core 公共规范](../AGENTS.md)，再执行本模块补充要求。
- 本模块负责扩展包的来源与声明校验、依赖与加载顺序、操作计划、扩展加载与注册归属，
  以及安装、回滚、卸载和重载的资源生命周期。注册后的事件匹配与 Handler 执行由 Events 负责；
  业务模块负责真实业务入口的事件触发、默认处理和实际资源访问的授权检查。
- 目标依赖方向为 `core/extension → core/events`，模块分工见
  [详细架构第 3 节](docs/designs/extension-architecture.md#3-架构总览与模块分工)。
  复用 Events 的公共事件模型、EventHandlerOps 和生命周期接口，不在本模块复制通用事件机制，
  也不让 Events 反向依赖包管理实现。`openjiuwen/extensions/` 中的可选集成不属于本文适用范围。
- 当前方案从本模块的设计概览和详细架构进入；已确认契约在主责 spec 中唯一维护，
  概览与架构引用该位置。待确认事项保留相应标识。
- 修改前阅读已有实现、公开导出、调用方、相关测试和模块设计，核对本次变化的实际影响。
  包加载控制面、Registry、九类管理事件编排与 Bus 注册表发布均已实现（EX-01～EX-04）。

## 文档分类与目录用途

分类与内容分工采用 [Core 文档分工](../AGENTS.md#内容边界与文档分工)。本模块入口如下：

| 目录 / 文件 | 用途 | 创建或更新时机 |
| --- | --- | --- |
| [AGENTS.md](AGENTS.md) | 模块规则、职责边界与阅读入口 | 模块职责或维护要求变化时 |
| [docs/designs/](docs/designs/) | 概览、详细架构、开发计划、备选方案和待确认问题 | 整体方案、范围或开发安排变化时 |
| [docs/specs/](docs/specs/) 中的 `S_NN_*.md` | 包声明、管理事件、注册归属、生命周期及相关接口、状态和不变量 | 新契约形成或已有契约变化时 |
| [docs/features/](docs/features/) 中的 `F_NN_*.md` | 具体特性或重构的方案、决策、实施影响和验证记录 | 实现前建档，开发中补充，落地后保留历史 |

### 主要设计文件

正式 spec 已建立，见上表。特性方案见 `docs/features/`。工作项与进度见开发计划。

| 文件 | 主要内容 |
| --- | --- |
| [设计概览](docs/designs/extension-overview.md) | 整体定位、核心对象、包管理流程、职责边界及能力开放条件 |
| [详细架构](docs/designs/extension-architecture.md) | Registry、Package Manager、Orchestrator 与 Runtime 分工，管理事件、两级依赖与顺序、注册归属、生命周期 |
| [开发计划](docs/designs/extension-development-plan.md) | EX-01～EX-04 工作项、依赖顺序、负责人与进度 |
| [包加载规约](docs/specs/S_01_package-loading.md) | 控制面契约：manifest、安装目录、依赖排序、Plan/candidate、生效记录、错误码 |
| [Registry 与 Bus 规约](docs/specs/S_02_registry-and-bus.md) | Registry、九类管理事件、candidate 发布语义 |
| [F_01 声明与定序](docs/features/F_01_package-source.md) | EX-01：来源、安装目录、依赖选择与排序 |
| [F_02 Plan 与 Runtime](docs/features/F_02_plan-runtime.md) | EX-02：单包 Plan、candidate、生效记录与卸载判断 |
| [F_03 Registry](docs/features/F_03_registry.md) | EX-03：Registry 与九类事件编排 |
| [F_04 Bus 接线](docs/features/F_04_bus-wiring.md) | EX-04：candidate 发布与 Runtime–Bus 接线 |

九类包管理事件的 feature/spec 维护在本模块 docs；`events/builtins` 仅保留登记与转发。Events 公共机制见
[Events 设计概览](../events/docs/designs/events-overview.md)与
[Events 详细架构](../events/docs/designs/events-architecture.md)。

## 命名与编号

文档命名、编号与日期采用 [Core 命名与编号规则](../AGENTS.md#命名与编号)。

需要开发计划时，计划条目使用 `EX-NN`，与 feature 文件分别编号；在计划与 feature 中明确关联，
不默认编号相同，也不与架构中的 `EX-A` 规则编号混用。

## 模板与必要内容

模板与填写要求采用 [Core 模板与必要内容要求](../AGENTS.md#模板与必要内容)，模板在
[公共模板目录](../docs/templates/)统一维护。

扩展包相关文档须按涉及范围说明以下内容，完整契约在主责 spec 中维护，本文不重复定义：

- 包来源、manifest、入口路径与运行域的校验边界，包之间和包内扩展之间的依赖及排序分别由谁维护。
  不将包加载顺序等同于 Events 的 Handler 执行顺序。
- 管理事件的调用点、输入输出、完成依据与失败责任，Core Handler 和模块能力的分工，
  以及父子操作的可信关联、授权与准入。独立操作与内部辅助步骤的边界参见
  [架构第 4.1 节](docs/designs/extension-architecture.md#41-registry统一管理入口)；
  业务代码和 Handler 显式推进流程，EventBus 不自动编排下一项包操作。
- Plan 的可信来源与目标范围、每条 `handler_id` 的包和扩展归属、回滚范围、依赖失败的跳过规则，
  以及 Report 与实际生效、执行和残留记录的对应关系。安装成功与真实业务执行成功分别验证。
- 卸载、重载、取消、超时和停止时的调用退出与资源释放条件，以及文件所有权和清理边界。
  注销不代表旧调用已经结束，注册回滚不能撤销任意外部副作用；保留未完成清理的记录。
- 进程内状态与可持久保留的数据边界、重新加载与恢复执行的区别，以及受限登记和权限检查的归属。
  受限接口可以登记 USER 和 BUILTIN，不能登记 CORE。它不是恶意 Python 代码隔离。
  一个包的 Handler 一次写入 EventBus 注册表。重载是热切换。崩溃后不能从旧调用中间恢复执行。

未确定的行为标为待确认，设计问题从
[架构第 12 节](docs/designs/extension-architecture.md#12-设计选择与待确认事项)进入，
不额外承诺实现没有提供的保证。

## 开发与文档同步

设计先于实现、契约同步及验证记录采用
[Core 同步流程](../AGENTS.md#特性设计与契约同步)。公共契约变化同步其主责文档，模块专属契约在
`docs/specs/` 维护，具体方案和实施记录在 `docs/features/` 维护。

涉及 Events 公共模型、登记与注销、Ops 或使用结束确认时，核对双方设计、实现和验证归属，
共享契约由主责模块维护，本模块只引用并补充包管理要求。
实现中需要调整已确认架构时，说明修改内容、原因、影响及验证方式，按已有授权和适用规则更新设计；
仅修正过时事实或引用时按公共同步规则处理。不将目标设计或参考示例直接写成当前实现事实。

## 特性认领与进度维护

计划建立、特性认领、进度更新及验收依据采用 [Core 进度维护要求](../AGENTS.md#开发计划与进度维护)。
需要计划时，统一在上述 Extension 开发计划入口维护，概览、架构及 feature 引用该入口。
与 Events 及业务模块协作的依赖、负责人和验证证据在计划中明确，不以本模块注册成功代替协作验收。

## 模块规则索引

所属及关联模块的阅读入口采用 [Core 目录索引](../AGENTS.md#core-目录与开发入口)。
跨模块修改时读取目标目录的适用规则；Events 的局部规则见 [Events AGENTS.md](../events/AGENTS.md)，
不将其他模块的局部要求扩大应用，也不在本文复制公共索引。

## 引用与交付检查

提交及公共交付检查采用 [Core 交付要求](../AGENTS.md#引用与交付检查)，模块重点检查如下：

- 检查文档相对路径、章节锚点及引用方，保持本模块及 Events 协作设计入口有效。
- 核对概览、架构、spec、feature 和开发计划中的名称、职责、范围、依赖、状态及证据是否一致，
  保留目标设计、待确认事项、已实现行为与实际验收结果的区别。
- 实现变化按 [Core 测试要求](../AGENTS.md#测试与验证)验证，重点覆盖受影响的来源校验、两级依赖与顺序、
  事件调用与结果核对、注册归属、失败回滚、依赖保护、卸载判断及资源清理；跨模块协作验证入口见
  [架构第 11 节](docs/designs/extension-architecture.md#11-完整场景与验证要求)。只记录实际执行的验证及其限制。
- 纯文档修改检查内容、链接和差异，无需运行业务测试。分别运行 `git diff --check` 和
  `git diff --cached --check`，并单独检查这两条命令不覆盖的未跟踪新文件；`CLAUDE.md` 只保留 `@AGENTS.md` 引用。
