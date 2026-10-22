# Extension 扩展包机制详细架构设计

| 项 | 内容 |
| --- | --- |
| 文档类型 | 目标设计草案；尚未确定的内容标为补充建议或待确认项 |
| 最近一次修订日期 | 2026-10-22 |
| 责任人 | Core Extension 模块维护者；与 Events 的协作由双方维护者共同确认 |
| 所属模块 | `agent-core/openjiuwen/core/extension/` |
| 配套文档 | [设计概览](extension-overview.md)、[Core 公共规范](../../../AGENTS.md) |
| 协作模块设计 | [Events 概览](../../../events/docs/designs/events-overview.md)、[Events 详细架构](../../../events/docs/designs/events-architecture.md) |
| 模板依据 | [架构模板](../../../docs/templates/architecture-template.md)；概览采用 [Overview 模板](../../../docs/templates/overview-template.md) |

本文以企业报告包为例，说明 agent-core 如何解析扩展包、注册事件处理器，以及在失败、重载和卸载时如何处理。重点是各模块分别做什么、如何协作、依据哪些记录判断操作结果，以及何时可以释放资源。

包管理和真实业务操作通过事件执行，由业务代码或 Handler 在明确的调用点发起。Registry 是业务调用的便捷封装，各管理事件的 Core Handler 负责决定是否触发下一项包操作。EventBus 提供通用事件服务，不承担包流程编排。事件机制的具体设计以 Events 文档为准。

本文用 **Extension** 指扩展包管理模块，用“扩展单元”（manifest 中的 extension）指包内可以独立声明和加载的扩展。**Handler** 是响应事件的处理函数，**Ops** 是 Handler 使用平台能力的入口。安装目录由调用方确定，以 `install_dir: Path` 传入 `open_install_dir`；配置键、配置解析和默认路径由调用方维护。进程启动时先 `init` 一次，再设置安装目录并批量安装。前端转发和 Gateway 协议不在本文范围内。

**Core Handler** 指 Events 中 CORE 层级的默认处理器，每个事件有且只有一个。Extension 的 Package Manager、Extension Orchestrator 和 Extension Runtime 为管理事件的 Core Handler 提供能力，一个处理器可以按需使用多个模块。下文将 **Extension Orchestrator** 和 **Extension Runtime** 简称为 Orchestrator 和 Runtime。

**实现状态：** EX-01～EX-04 已落地：控制面、Registry、九类管理事件编排与 Bus 发布。本文管理事件流程与实现一致；待确认项见第 12 节。

契约见 [S_01](../specs/S_01_package-loading.md)、[S_02](../specs/S_02_registry-and-bus.md)；方案见 [F_01](../features/F_01_package-source.md)～[F_04](../features/F_04_bus-wiring.md)。工作项见 [开发计划](extension-development-plan.md)。错误码使用仓库统一 `StatusCode`（`EXTENSION_*`，201000–201015）。九类事件编排在 extension；`events/builtins` 登记转发。

| 阅读目的 | 章节入口 |
| --- | --- |
| 了解定位、核心概念和模块分工 | 第 1～3 节 |
| 理解各模块的工作方式、协作流程和完成条件 | 第 4～6 节 |
| 核对数据、故障、安全和升级处理 | 第 7～10 节 |
| 推演完整场景，查阅验证要求和待确认问题 | 第 11～12 节 |

## 1. 定位、目标与范围

### 1.1 定位与目标

Extension 负责扩展包的管理和加载：读取包声明，检查来源与依赖，确定加载顺序，再注册或撤销包中的 Handler。这部分管理职责也称为“控制面”。注册后的事件匹配和 Handler 执行由 Events 负责。

例如，企业报告包依赖公共报告包，包内的 `enterprise-report` 又依赖 `report-base`。调用方先登记平台事件和能力构建处理器，再请求安装这两个包。系统先加载公共包，再加载企业包；进入企业包后，先处理 `report-base`，再处理 `enterprise-report`。Runtime 调用各扩展单元的注册方法，将报告扩展的 USER Handler 注册到 Bash 工具事件上。

后续工具调用由 Events 选择并执行 Handler。Extension 则通过生效记录和操作报告说明哪些注册已完成、哪些失败、还有哪些资源待清理。安装成功和一次业务调用成功是不同的结果，需要分别确认。

设计需要保证：包来源与依赖选择一致，包与包内扩展的顺序分别管理，每条注册都有明确归属，失败时知道应撤销哪些注册，卸载时不会提前释放仍在使用的资源。

### 1.2 范围与外部责任

| 范围 | 本期目标及责任边界 |
| --- | --- |
| 包来源 | 接收调用方传入的本地目录或 zip。zip 先解压到临时目录。通过检查的文件复制到安装目录，调用方的源码目录和 zip 不删除 |
| 运行域 | 处理 manifest 声明为 `backend` 的 Python 扩展；其他运行域应拒绝，支持方式另行设计 |
| 管理操作 | EventBus 初始化时登记 9 类管理事件及其 Core Handler。`Registry.init` 只接收这份 EventBus，并登记 jiuwenswarm 的内置事件。`package.reload` 在依赖方仍接受新版本时热切换该包的 Handler，期间新调用使用新注册表，旧调用保持自己的快照 |
| 包管理 | 校验 manifest、宿主版本和包版本，分别确定包与包内扩展的加载顺序；不自动联网寻找依赖 |
| 事件执行 | 使用 Events 的公共注册和调用接口。一个包的 Handler 在一次没有 `await` 的同步更新中写入 Bus 注册表。业务 `trigger` 开始时读取注册表快照 |
| 业务协作 | Core 业务模块提供事件定义、默认处理、能力和会话资料。Runtime 保存生效记录，供下一次安装判断依赖 |
| 暂缓内容 | 远程仓库、自动依赖安装、多版本同时生效、前端 Runtime、沙箱、跨进程互斥，以及只热更新包内某一个扩展单元 |

可选参数 `extension_id` 用于表达只操作某个扩展单元的意图。但需要一并处理哪些依赖、部分操作的结果如何表示，仍待 Q05 确认。本节说明计划支持的范围，不表示这些能力已经实现。

### 1.3 关键需求与对应设计

| 要求及依据 | 对应设计 | 限制 |
| --- | --- | --- |
| 从明确的本地路径加载包，由 `package.install` 完成安装 | 第 4.1～4.2、5.1 节 | 同 ID 冲突策略待确认，不约定产品来源优先级 |
| 管理请求和独立可复用操作经过事件入口，内部步骤由所属 Core Handler 执行 | 第 2.2、4.1、5 节 | 按业务边界设置 9 类目标事件，不按函数、模块或状态更新拆分 |
| manifest 声明包和扩展入口，工厂创建实例，`register` 注册 Handler | 第 2.1、4.2～4.4 节 | 公共模型及最终导出仍待确认 |
| 通过 Plan / Report 管理执行、回滚、重载和卸载 | 第 3～8 节 | 回滚范围、报告格式和卸载等待机制仍需细化 |
| [Events 架构第 3.2 节](../../../events/docs/designs/events-architecture.md#32-代码归属与依赖方向)：公共模型归属及依赖方向 | 第 3.2、4.5 节 | Events 不导入包管理或调用方实现 |
| [Events 生命周期约定](../../../events/docs/designs/events-architecture.md#53-handler-装卸与运行中的调用)：卸载时确认相关使用结束 | 第 3.4、4.5、5.2 节 | 需要协作取得使用结束确认后，才能释放包资源 |

### 1.4 核心设计取向

Extension 管理包，Events 执行事件。两者通过公共接口协作，同一公共模型只在所属模块维护。

| 设计主题 | 采用的方案与原因 |
| --- | --- |
| 管理入口 | Registry 负责初始化时的事件装配，并提供安装、重载和卸载的便捷方法；最终类名和公开导出待定 |
| 操作执行方式 | Registry 和上层 Core Handler 通过事件发起独立操作；事件内部可以调用相关模块的辅助能力 |
| 事件触发责任 | 业务代码和 Handler 发起包管理及业务事件；EventBus 不根据 Plan、包依赖或结果安排下一项包操作 |
| 能力模型 | 使用 Events 的 EventHandlerOps，避免在 Extension 中重复定义能力基础层 |
| 两类加载依赖 | `requires.packages` 表示包之间的依赖，`extensions[].requires` 表示包内扩展之间的依赖 |
| 同 ID 多来源 | 同一批出现重复 `package.id` 时整批失败。已经生效的同 ID 再安装，返回 `EXTENSION_PACKAGE_EXISTS`，不替换 |
| 事件调用范围 | 沿用 Events 的进程内调用方式。`trigger` 开始时固定 Bus 注册表快照 |
| 无依赖时的顺序 | 对 id 的 UTF-8 字节做 SHA-256，按十六进制字符串升序。这个顺序在进程重启后保持不变 |
| 后续新增操作 | 只有出现独立调用场景、完成结果或不同处理责任时才评估新增事件；解压和复制文件不单独设事件 |

本方案首先要求注册和清理过程可追踪。目前没有性能实测数据，不承诺启动耗时、吞吐量或卸载等待时长。无依赖边时的排序使用 SHA-256(UTF-8 id) 的十六进制字符串，进程重启后顺序不变；不使用 Python 内置 `hash()`。

## 2. 核心概念与共同规则

### 2.1 核心概念、关系与生命周期

一个包可以包含多个扩展单元，每个扩展单元可以注册多个 Handler。Plan 描述本次要做的工作，Runtime 执行后维护注册和资源记录，Report 再将处理结果交给调用方。

| 对象 | 表示什么，由谁管理 | 使用范围与生命周期 |
| --- | --- | --- |
| 候选来源 | 调用方提供的本地包路径，由 Package Manager 解析 | 用于本次包选择，不直接决定 Handler 执行顺序 |
| Package / PackageRef | 包的 ID、版本和选定包根；PackageRef 是对选定包的引用 | Package Manager 管理；同一 Registry 对同 ID 只选择一个生效版本 |
| 扩展单元 / ExtensionRef | 包内的加载单元及其引用，入口返回提供 `register` 方法的实例 | Runtime 持有实例；身份由包 ID 和包内扩展 ID 共同确定 |
| Plan | 单个包的本次操作、目标扩展、步骤和计划标识 | Orchestrator 创建，Runtime 保存可信操作关联并核对执行；不是可跨重启恢复的任务 |
| Report | 单包 Plan 及上层管理请求的处理结果 | Runtime 提供执行事实，管理 Core Handler 汇总逐包结果，Registry 交回；不替代生效记录 |
| 注册批次 | 本次 Plan 已成功注册的 `handler_id` 及注册顺序 | Runtime 记录；失败时逆序注销，成功后并入生效记录 |
| 生效记录 | 包和扩展实例与 `handler_id`、资源、待清理项的对应关系 | Runtime 在当前 Registry 实例中维护 |
| 包关联信息 | 当前 Handler 所属包的可信来源信息 | Runtime 核实后通过 Events 公共登记约定关联到 Handler |
| EventHandlerOps（Ops） | Handler 访问平台能力、包信息和会话资料的入口 | 公共模型归 Events；Extension 直接使用既有接口 |

同一回调函数注册多次，也会产生不同的注册记录。卸载必须使用实际返回的 `handler_id`，不能只按函数名删除。包身份使用 Events 的 `PackageMetadata`：`package_id` 来自 `manifest.package.id`，`package_version` 来自 manifest，`root` 为解析后的绝对包根；发布到 Bus 时由 Runtime 填入，扩展作者不得改写。

Extension 维护包、实例、计划和注册归属记录，不维护 Events 的内部执行状态。双方的接口协作与责任见第 4.5 节。

### 2.2 共同规则索引

下列 EX-A 规则是本草案需要满足的要求。补充建议确认后，再写入对应的正式规约，作为长期维护的契约。

| 规则 | 适用范围与负责模块 | 不满足时如何处理 | 详细说明 |
| --- | --- | --- | --- |
| EX-A01 先明确包来源，再校验和排序 | 本次安装集合；Package Manager | 来源冲突或版本不满足时拒绝相应包，不自动改选其他来源 | 第 4.2 节 |
| EX-A02 分别处理包与包内扩展的依赖 | Package Manager 排列包，Orchestrator 排列包内扩展 | 拒绝缺失、循环或超出允许范围的依赖；依赖失败时跳过依赖方 | 第 4.2～4.3 节 |
| EX-A03 每条成功注册都有可信归属 | Runtime 的受限登记入口 | 拒绝来源不明或层级不合法的注册，不留下没有归属的记录 | 第 4.4 节 |
| EX-A04 失败时丢掉尚未发布的 candidate | Runtime；范围是当前包的本次计划 | 不把半套 Handler 留在 Bus 上。已经生效的其他包保持不变 | 第 5.2.1 节 |
| EX-A05 初始化只装配事件与处理器 | Registry 接收已初始化的 EventBus，并登记 jiuwenswarm 内置回调 | EventBus 初始化时已登记 core 内置事件（含 9 类管理事件）。装配失败时不受理包操作。`init` 不读取用户 manifest，不安装扩展。宿主回调返回后，`init` 触发一次 `ops.build`。启动批量安装发生在 `init` 之后 | 第 4.1 节 |
| EX-A06 有在途调用时拒绝卸载 | Runtime | 返回失败，不注销，不删除安装目录，也不等待调用结束 | 第 5.2.2 节 |
| EX-A07 重载是热切换 | `package.reload` 的 Core Handler | 依赖方仍接受新版本才继续。准备失败时当前注册表不变。新调用读新快照，旧调用保持自己的快照 | 第 5.2.3 节 |
| EX-A08 报告必须反映实际注册和清理情况 | Runtime 提供记录，管理事件的 Core Handler 核对，Registry 交回 | 提前返回、取消或未收到结果时先核对，不凭空报告 SUCCESS | 第 6.3、8 节 |
| EX-A09 独立操作经过事件，内部步骤归所属 Core Handler | Registry 及各管理事件的 Core Handler | 不绕过已定义的独立事件；解析检查、加载登记、注销等待和资源清理按第 4.1.3 节归并，不能按方法随意新增事件 | 第 4.1、5 节 |
| EX-A10 包管理事件由业务代码或 Handler 显式发起 | 业务入口、Registry 便捷方法和管理事件的 Core Handler | 不把安装、解析、回滚或下一步骤的业务编排写入 EventBus；通用事件调用约定沿用 Events | 第 3.2、4.1.2、4.5 节 |

## 3. 架构总览与模块分工

### 3.1 系统与外部的关系

调用方通过 Registry 的便捷入口或受控事件入口提交包管理请求，两种入口都触发同一组管理事件。Registry 不直接执行包解析、安装或卸载。Core 业务模块通过可信登记入口提供事件、BUILTIN 和能力构建处理器。用户扩展包提供 manifest、入口工厂及注册实现。它的 `register` 方法只获得受限入口，可以登记 USER 和 BUILTIN，不能登记 CORE，也不能提交管理操作。

管理操作从可信入口发起。包代码与调用方运行在同一进程，加载前需要校验声明和路径，但这些校验不能隔离恶意 Python 代码。若要执行不可信代码，需要单独设计隔离机制。工具或外部系统的实际执行结果，仍由所属业务模块核对。

### 3.2 内部组成与依赖

本图在 [overview 第 2.1 节](extension-overview.md#21-总体关系)的基础上，展开管理事件分组、模块能力及关键记录，并标明与 Events 公共接口的协作关系。Registry 与下方事件处理区域都属于 `core/extension`，为突出入口和执行职责而分开展示，不限定类或服务的具体拆分。

```mermaid
%%{init: {"theme": "base", "htmlLabels": false, "themeVariables": {"fontFamily": "Microsoft YaHei, PingFang SC, Arial, sans-serif", "fontSize": "14px", "lineColor": "#64748b", "primaryTextColor": "#1e293b", "edgeLabelBackground": "#ffffff", "clusterBkg": "#ffffff", "clusterBorder": "#cbd5e1"}, "themeCSS": ".node text, .nodeLabel, .cluster-label text, .edgeLabel text { font-size: 14px !important; }", "flowchart": {"curve": "linear", "nodeSpacing": 36, "rankSpacing": 52, "padding": 36, "wrappingWidth": 240, "subGraphTitleMargin": {"top": 20, "bottom": 24}, "htmlLabels": false}}}%%
flowchart TB
    Caller["业务代码<br/>提供包路径、操作目标和会话"]:::entry

    Registry["Registry（core/extension）<br/><br/>初始化时登记事件和处理器<br/>接收请求，触发对应事件<br/>等待并返回处理结果"]:::entry

    subgraph Extension["core/extension"]
        direction TB

        subgraph Handlers["管理事件的 Core Handler"]
            direction TB
            subgraph Management["安装、卸载与重载"]
                direction LR
                IC["package.install 事件的<br/>Core Handler<br/><br/>按依赖顺序安装包<br/>汇总各包的安装结果"]:::handler
                LC["package.uninstall<br/>package.reload<br/>事件的 Core Handler<br/><br/>忙则拒绝卸载；重载热切换 Handler"]:::handler
            end
            subgraph Operations["各步骤的操作事件"]
                direction LR
                PC["package.parse<br/>package.resolve / package.query<br/>事件的 Core Handler<br/><br/>读取声明，核对依赖与状态"]:::handler
                OC["extension.plan 事件的<br/>Core Handler<br/><br/>检查包内扩展的依赖<br/>生成本次操作的执行计划"]:::handler
                RC["extension.apply<br/>extension.rollback<br/>事件的 Core Handler<br/><br/>执行计划，补偿失败安装"]:::handler
            end
        end

        subgraph Modules["模块提供的能力"]
            direction LR
            PM["Package Manager<br/><br/>读取包路径和 manifest<br/>检查版本、包依赖并排序<br/>查询状态，清理自有产物<br/>保存包信息和依赖结果"]:::module
            Orch["Extension Orchestrator<br/><br/>检查包内扩展的依赖<br/>确定本次处理的扩展<br/>生成有序执行计划（Plan）<br/>记录目标扩展和执行步骤"]:::module
            RT["Extension Runtime<br/><br/>按计划加载并创建实例<br/>注册 Handler，记录归属<br/>回滚注册，等待并释放资源<br/>保存计划、生效与清理记录"]:::module
        end
        Handlers -.->|按需使用| Modules
    end

    Events["EventBus（core/events）<br/><br/>接收事件，调度 Handler<br/>返回处理结果或异常"]:::events

    Caller -->|提交管理请求| Registry
    Registry -->|调用 trigger 触发事件| Events
    Events -->|调度对应 Core Handler| Handlers

    classDef entry fill:#eff6ff,stroke:#2563eb,color:#1e3a5f,stroke-width:1.5px,font-size:14px
    classDef events fill:#f5f3ff,stroke:#8b5cf6,color:#4c1d95,stroke-width:1.5px,font-size:14px
    classDef handler fill:#fff7ed,stroke:#ea580c,color:#7c2d12,stroke-width:1.3px,font-size:14px
    classDef module fill:#f0fdfa,stroke:#0d9488,color:#134e4a,stroke-width:1.5px,font-size:14px
    style Extension fill:#f8fafc,stroke:#94a3b8,stroke-width:1.5px
    style Handlers fill:#fffbf5,stroke:#fdba74,stroke-width:1px
    style Management fill:#ffffff,stroke:#fed7aa,stroke-width:1px
    style Operations fill:#ffffff,stroke:#fed7aa,stroke-width:1px
    style Modules fill:#ffffff,stroke:#99d5cf,stroke-width:1px
```

蓝色表示调用与管理入口，紫色表示 Events 提供的事件服务，橙色表示事件的 Core Handler，青绿色表示 Extension 模块能力。实线表示调用或事件调度，虚线表示 Core Handler 按需使用模块能力。图中按职责合并展示部分事件，每个事件仍有各自的 Core Handler；完整事件列表见第 4.1.1 节。

一个 Core Handler 可以使用多个模块，同一模块也可以支持多个事件的 Core Handler。模块卡片说明它提供哪些能力、生成或保存哪些记录；计划、生效记录和清理记录均保存在当前进程中，不代表持久存储。解析、依赖解析、计划、执行、回滚和查询分别设有事件，加载与清理的具体步骤由执行或回滚 Handler 在内部完成。事件划分见第 4.1 节，协作顺序见第 5 节。

EventBus 只提供事件调用服务，不根据 Plan、包依赖或处理结果安排下一项包操作。业务代码和 Handler 决定何时发起包管理事件；Registry 在管理方法被调用时显式转发。Extension 与 Events 的协作边界见第 4.5 节。

包管理事件、Core Handler 及包生命周期逻辑由 Extension 维护；事件公共模型和通用执行机制由 Events 维护，业务事件和能力实现由所属业务模块维护。源码依赖保持 `core/extension → core/events`；图中的回调调度表示运行时调用，不表示反向源码依赖。

### 3.3 各模块负责什么

| 参与方 | 职责与协作方式 | 交出什么、负责到哪里 |
| --- | --- | --- |
| Registry | 装配管理事件及其 Core Handler；业务代码调用便捷方法时，显式触发对应事件 | 等待并交回管理结果 |
| 管理事件的 Core Handler | 执行对应事件的默认逻辑，按需使用一个或多个模块的能力；涉及另一项独立操作时触发对应事件 | 核对操作结果，决定是否继续后续步骤 |
| Package Manager | 提供来源与声明解析、包依赖和排序、包信息查询与自有产物清理能力 | 支持解析、依赖解析和查询事件；也供执行、回滚 Handler 在满足条件后清理自有产物 |
| Extension Orchestrator | 提供包内扩展依赖分析、操作范围与计划生成能力 | 支持 `extension.plan`；根据解析后的包信息和必要的 Runtime 记录返回单包 Plan |
| Extension Runtime | 提供可信计划关联、实例、受限登记、计划执行、回滚、注销、等待和资源释放能力 | 支持执行与回滚事件，为计划事件保存可信关联，向依赖解析、计划及查询事件提供当前运行事实 |
| EventBus / 执行器 | 提供公共注册、事件调用及 Handler 调度服务 | 交回处理结果或异常，不编排包管理步骤；内部机制见 Events 文档 |
| 调用方与 Core 业务模块 | 提供路径、可信回调、会话、业务入口、资源权限和停止信号 | 从受控事件入口发起操作，核对管理与业务结果 |

上表区分事件处理职责与模块能力职责，不限定一个 Core Handler 只能使用一个模块。安装、卸载和重载事件的 Core Handler 可以通过子事件协调多个模块；具体协作顺序见第 5 节。Core Handler 内部可以复用所需模块的纯计算或底层辅助函数，但进入另一项具有独立语义的包管理操作时，必须触发对应事件。

### 3.4 已有基础与需要补齐的部分

包加载控制面、Registry 与 Bus 发布均已实现，契约见 [S_01](../specs/S_01_package-loading.md)、[S_02](../specs/S_02_registry-and-bus.md)，进度见 [开发计划](extension-development-plan.md)。

[Events 架构](../../../events/docs/designs/events-architecture.md)提供公共接口的设计依据。9 类包管理事件及其 Core Handler 在 Events 目录实现，并在 EventBus 初始化时登记；Extension 提供这些 Handler 调用的包管理与 Runtime 能力。

Core 业务模块需要提供所属业务事件的默认行为和能力处理器。Extension 与 Events 共同完成可信包信息关联、注册追踪和在途计数配合后，才能跑通完整的加载与卸载流程。

## 4. 关键模块设计

### 4.1 Registry：统一管理入口

业务代码调用 `Registry.install`、`uninstall` 或 `reload` 时，对应方法体内显式调用受控 `trigger`，发起 `package.install`、`package.uninstall` 或 `package.reload`，并等待结果。Registry 不观察包状态、不自主扫描，也不驱动下一事件。业务代码直接调用受控 `trigger` 时，遵守相同授权及准入规则；解析、查询等请求也由业务或 Handler 明确发起。

`init` 是 jiuwenswarm 显式调用的方法。EventBus 初始化时已经登记 core 内置事件，包括 9 类管理事件及其 Core Handler。`init` 只接收这份 EventBus，并执行 jiuwenswarm 的事件和处理器登记回调。进程启动时只调用一次。它不读取用户 manifest，不加载扩展代码，不执行包安装。宿主回调返回后，它触发一次 `ops.build`，调用方不再单独触发。

`init` 返回后，jiuwenswarm 把安装目录路径交给 `open_install_dir`。Extension 列出该目录中的包目录，作为一批路径触发 `package.install`。`host_version` 在同一次调用里传入，用来和 `requires.host` 比较。这一步不安装。这样进程重启后会重新装上安装目录里的包。

必需事件或平台处理器登记失败时，Registry 不能标为就绪。目前建议拒绝重复 `init`，失败实例清理后由调用方重建。事件基础设施的准备方式遵循 Events 公共约定，Extension 不重复定义。

#### 4.1.1 包管理事件与 Core Handler 职责

事件围绕可独立请求、判断完成并处理失败的操作设置。跨模块、包含多个辅助函数或发生一次状态变化，本身都不是新增事件的理由。本期采用下列 9 类目标事件；事件标识用于说明架构，最终公开名称、类型和错误码由规约维护，不表示已有可调用 API。

本目录只包含 Extension 管理事件。工具等真实业务事件由所属业务模块维护，Events 自身的基础事件也不计入这 9 类事件。

| 事件 | 独立设置的理由与适用范围 | Core Handler 的功能与完成依据 | 使用的模块能力 |
| --- | --- | --- | --- |
| `package.install` | 安装是调用方直接请求的管理操作，承担批量包之间的推进与失败隔离 | 接收本地路径集合，触发解析、依赖解析，再逐包生成和执行计划；根据实际生效记录汇总成功、失败和依赖跳过 | Package Manager、Orchestrator、Runtime；分别通过相应子事件协作 |
| `package.uninstall` | 卸载具有独立的依赖保护、资源退出和完成条件 | 接收目标包与允许的操作范围，触发依赖解析、计划和执行；注册撤销、相关使用结束及应清理产物处理完毕后才报告完成 | Package Manager、Orchestrator、Runtime；分别通过相应子事件协作 |
| `package.reload` | 卸载与安装需要保持同一根操作关联、准入范围和整体结果 | 触发卸载，完整成功后触发安装；任一步失败即停止并汇总实际状态，不自动恢复旧版 | Package Manager、Orchestrator、Runtime，通过安装和卸载子事件使用其能力 |
| `package.parse` | 路径与声明解析可以在不导入代码的情况下独立检查和复用 | 接收一个本地目录，确定包根，读取 manifest 并检查基本格式、必填字段与入口边界，返回候选包；不检查整批包的依赖是否可满足 | Package Manager：来源、路径与声明解析 |
| `package.resolve` | 包之间的依赖判断和顺序选择具有独立结果，安装与卸载均需使用 | 接收操作类型、候选包或待卸载目标及当前生效集合；安装时检查版本、冲突和包依赖并给出包序，卸载时检查反向依赖和允许范围；返回合法选择、顺序及拒绝原因，不导入或注销代码 | Package Manager：版本、包依赖和包级排序；Runtime：当前生效与注册事实 |
| `extension.plan` | 单包的目标扩展和步骤应在产生加载或清理影响前确定，供安装与卸载复用 | 接收经依赖解析确认的包、操作类型和目标范围，检查包内依赖，生成并保存可信关联的单包 Plan；卸载时核对实例和注册归属，计划生成不代表执行成功 | Orchestrator：包内依赖、范围与计划；Runtime：可信 Plan 关联、当前实例和注册记录 |
| `extension.apply` | 执行计划具有独立的授权检查、副作用和结果核对要求 | 只接受来源及目标已核实的 Plan；安装时依序加载、创建实例和注册，卸载时依序注销、等待和释放；保存执行事实并生成 Report，安装失败时显式触发回滚 | Runtime：计划、实例、注册和资源；Package Manager：清理已确认所有权的安装产物 |
| `extension.rollback` | 尚未写入 Bus 的安装失败后，需要独立丢掉 candidate | 接收本次失败 Plan 的标识，卸载这次加载的模块，不修改已经生效的注册表，也不删除调用方源码 | Runtime 清理 candidate；Package Manager 只在自己新建且尚未发布的安装目录副本上清理 |
| `package.query` | 管理调用超时、取消或返回失败后，需要独立读取事实来判断下一步 | 按包、操作或 Plan 标识读取当前包信息、生效状态、执行进展与待清理项；返回查询时可确认的事实，不补跑或修复操作，也不将进行中状态当作最终结果 | Package Manager：包来源和依赖信息；Runtime：生效、执行与清理记录 |

表中的模块依赖包含 Core Handler 内部使用的能力，以及通过子事件取得的能力。例如安装 Handler 需要三个模块，但应通过解析、依赖解析、计划和执行事件使用它们，不直接越过这些独立事件的入口。仅接收另一个模块产出的数据，不等于必须再次调用该模块。Plan 由 Orchestrator 生成，执行时由 Runtime 核对和使用，不重新生成计划。各事件的处理步骤和模块调用时序见[第 5 节](#5-模块协作与处理流程)，依赖关系汇总见[第 5.5 节](#55-事件之间的依赖与调用约束)。

#### 4.1.2 父子事件、准入和完成条件

| 发起事件 | Core Handler 明确触发的后续事件 | 推进条件 |
| --- | --- | --- |
| `package.install` | 每个路径触发 `package.parse`，集合触发 `package.resolve`，再逐包触发 `extension.plan`、`extension.apply` | 子事件结果有效；被依赖包安装失败时跳过依赖方，独立包可继续 |
| `package.uninstall` | `package.resolve` → `extension.plan` → `extension.apply` | 操作范围合法；前一个包清理未完成时不宣称卸载完成 |
| `package.reload` | `package.uninstall` → `package.install` | 只有完整卸载成功才进入安装 |
| `extension.apply` | 安装失败时触发 `extension.rollback` | 依据已保存的执行记录补偿，保留原始错误与清理残留；卸载失败保留进展，不触发安装回滚来恢复旧实例 |
| `extension.rollback` | 默认不再拆分子事件 | 根据同一失败 Plan 的可信记录完成清理，不重新进入安装流程 |

以上箭头表示 Handler 代码中的业务顺序，不表示 EventBus 自动推进。业务代码也可以通过受控入口请求解析、依赖解析、计划、执行、回滚或查询，但不能绕过各事件的授权及前置条件；尤其不能提交自行拼装的 Plan，或借回滚入口清理无关包。

各事件输入至少需要明确操作目标、允许范围以及可信的父操作或 Plan 关联。输入中的包名、路径和标识只用于定位，仍要与 Package Manager、Runtime 维护的记录核对。事件结果需要区分已完成、未执行、失败及仍有残留，并提供对应目标、结果和事实依据；具体字段由规约维护。

父 Core Handler 检查子事件结果的类型、目标和完成依据，再决定下一步。事件被提前返回、拒绝或取消时，不能据此认定默认工作已经完成；必要时触发 `package.query` 核对。事件化不额外提供事务或外部副作用回滚。

目前建议在单个 Registry 内协调根管理操作。合法子事件复用同一可信操作关联和准入范围，不能再次等待父操作持有的同一把锁，也不能只因事件名不同就当作无关请求。需要避免同一操作递归调用自身。管理事件的可介入范围、根操作协调和递归限制由 Q06 确认。

解析、依赖解析、计划和查询均不把 candidate 写入 Bus。查询只返回 Runtime 已经记下的事实，不补做安装。只读请求如何与根操作并发，仍见 Q06。

停止时拒绝外部新的管理请求。已经开始的业务调用继续使用自己的快照。取消或超时之后，用 `package.query` 看生效记录，不把同一次安装原样再放一遍。

#### 4.1.3 事件与内部步骤的界限

| 不单列事件的工作 | 在哪里完成 | 合并理由 |
| --- | --- | --- |
| 字段、格式与路径检查 | `package.parse` 的 Core Handler | 与一次候选包解析共同决定返回结果，单项检查没有独立交付结果 |
| 版本检查、包依赖检查与包级排序 | `package.resolve` 的 Core Handler | 必须基于同一组选定包和当前生效事实共同判断；单独排序结果不足以证明依赖可满足 |
| 包内依赖检查与扩展排序 | `extension.plan` 的 Core Handler | 共同决定同一份 Plan，不在排序后重新选择目标 |
| 导入入口、调用工厂、执行 `register`、一次写入 Bus | 安装或重载 Plan 的 `extension.apply` Core Handler | `register` 先进入 candidate；全部成功后一次写入，不把半套 Handler 暴露给新的快照 |
| 收集 candidate、一次写入 Bus 注册表、丢掉未发布的模块 | 安装或重载 Plan 的 `extension.apply`，失败时由 `extension.rollback` 处理 | 写入注册表之前失败，不留下半套 Handler。卸载发现在途调用时直接失败，不等待 |
| 自有安装产物的所有权核对与清理 | 上述卸载或回滚 Core Handler 内，使用 Package Manager 能力 | 文件清理必须依附于注册撤销和使用结束条件；本期不提供任意路径删除或独立包文件回收业务 |
| 更新进展、保存 `handler_id`、生成报告 | 所属操作的 Core Handler | 属于执行事实的记录步骤，不因一次内存更新新增事件 |

这些内部步骤仍在受控事件的 Core Handler 内执行，可以复用多个模块的辅助函数，不构成绕过事件。将来只有出现独立调用场景、完成条件或不同处理责任时，才重新评估是否新增事件；不提前保留占位事件。

### 4.2 Package Manager：来源、声明与包级定序

#### 4.2.1 来源选择和路径校验

Package Manager 的来源与声明解析能力由 `package.parse` 的 Core Handler 使用；版本、包依赖与包级排序能力由 `package.resolve` 的 Core Handler 使用。调用方和安装 Handler 通过这两个独立事件取得结果，不在事件入口前先执行同一项操作。

Package Manager 接受调用方传入的本地目录或 zip。zip 解压到新建的临时目录，读完 manifest 并完成复制后只删除这个临时目录。目录则直接读取。通过检查的包复制到 `<install_dir>/<package.id>/<version>/`，其中 `install_dir` 是调用方传入的安装目录。源路径已经是这个安装目录时，就地使用，不再复制。调用方原来的目录和 zip 一律不删除。

同一个 `package.id` 在安装目录里只保留一个版本目录。同一批候选里这个 id 出现两次，`package.resolve` 让整批失败，错误码是 `EXTENSION_DUPLICATE_PACKAGE_ID`。id 已经在 Runtime 的生效记录里时，返回 `EXTENSION_PACKAGE_EXISTS`，不替换。要换这个包，先卸载，或直接改安装目录里的文件再 `reload`。

`requires.host` 是可选字段，表示这个包需要的最低 jiuwenswarm 版本，写法与包依赖的版本条件相同。宿主版本不满足时，这个包失败，错误码是 `EXTENSION_HOST_VERSION_TOO_LOW`，不影响同一批里没有依赖关系的其他包。

`extensions[].module` 必须是相对于包根的入口路径。加载前，将它解析为实际绝对路径，处理 `..`、符号链接和 Windows 目录联接（junction），确认最终入口仍位于选定包根内。不支持的运行域、文件缺失或入口越界，都应在导入代码前拒绝。

一次操作加载的是安装副本里的文件。校验通过后先复制到 `<install_dir>/<package.id>/<version>/`，再从这份副本加载。调用方随后修改自己的源码目录，不会改到这次已经复制的内容。已经是安装副本的目录则就地加载。

#### 4.2.2 manifest 的职责和最小示例

每个包根只有一个 `manifest.yaml`，声明包身份、版本、包依赖和扩展入口。具体 Handler 由扩展代码注册，不列在 manifest 中。下例仅说明对象关系，不是完整 Schema；版本范围的解析语法仍需规约化。

```yaml
schemaVersion: 1
package:
  id: org.example.enterprise-report
  version: 1.0.0
  displayName: Enterprise Report
  description: 企业报告扩展
requires:
  packages:
    - id: org.example.report-common
      version: ">=1.0.0 <2.0.0"
extensions:
  - id: report-base
    runtime: backend
    module: extensions/report-base/extension.py
  - id: enterprise-report
    runtime: backend
    module: extensions/enterprise-report/extension.py
    requires:
      - id: report-base
```

核心字段包括 `schemaVersion`、`package.id`、`package.version`，以及每个扩展单元的 id、runtime 和 module。展示名与描述只用于说明包的用途。

两类依赖分别处理：`requires.packages` 表示包之间的依赖，由 `package.resolve` 分析；`extensions[].requires` 只表示当前包内扩展之间的依赖，由 `extension.plan` 分析。包内扩展 ID 不能用于引用另一包的扩展单元。

遇到不支持的 `schemaVersion`、无效或重复的扩展 ID、缺失入口或无效依赖声明时，应拒绝加载。未知可选字段如何处理、版本范围如何解析，由正式 manifest 规约确定，不默认兼容所有未知内容。

#### 4.2.3 包级定序和依赖结果

`package.resolve` 的 Core Handler 在同一次依赖解析中完成版本与包依赖检查、冲突判断和包级排序。输入包括候选包或卸载目标、操作类型及 Runtime 提供的当前生效事实；输出是合法包集合、处理顺序及拒绝原因。检查与排序不再各设事件，以免基于不同选择结果继续执行。

Package Manager 根据本次选定的包和 Runtime 里已经生效的依赖检查加载条件。依赖另一个待安装包时，必须先处理被依赖包，再处理依赖方。没有包依赖时，按 SHA-256(UTF-8 `package.id`) 的十六进制字符串升序排列。包内扩展使用同一算法，键是 `extension.id`。依赖先后优先于这个排序。Python 内置的 `hash()` 不参与排序。

依赖缺失、存在循环或版本不满足时，不加载受影响的计划，并记录原因。目前建议让不受影响的包继续安装。例如，A 注册失败，依赖 A 的 B 就不进入 Runtime；独立的 C 可以继续，其他已成功包也不因此回滚。

同一批里 `package.id` 重复时整批失败。id 已经生效时，该包返回 `EXTENSION_PACKAGE_EXISTS`，不替换。磁盘上有一份目录，不等于它已经在 Runtime 的生效记录里。安装 Handler 按 `package.resolve` 的包序逐包触发 `extension.plan`，不把多个包的扩展混在一起排，也不联网下载依赖。卸载时同一事件检查反向依赖和在途调用。

### 4.3 Orchestrator：单包计划与结果协调

Extension Orchestrator 提供包内扩展依赖分析、排序和计划生成能力。`extension.plan` 的 Core Handler 根据依赖解析确认的包和操作范围生成单包 Plan，卸载时同时核对 Runtime 的实例与注册记录。安装或卸载 Handler 取得计划后，再独立触发 `extension.apply`，由 Runtime 核对并执行；计划生成不等于已经加载、注册或清理。

Orchestrator 每次接收一个选定包和本次操作，检查包内扩展单元的依赖，确定执行顺序。有依赖时先处理被依赖的扩展；没有依赖时按 `hash(extension.id)` 升序排列。依赖缺失或循环必须在 Runtime 加载代码前拒绝。

计划 Handler 将生成的 Plan 与当前操作、已核实包来源和目标范围交给 Runtime 保存为可信计划记录。执行 Handler 以该记录核对输入中的计划标识或引用，不能仅因输入包含合法格式的 Plan 就接受执行。保存关联是计划事件的内部步骤，不另设计划登记事件；最终公共模型和传递方式由 Q08 确认。

跨包的来源选择和失败影响由包级流程处理，Orchestrator 不重新选择包来源。它生成的 Plan 固定本次操作、PackageRef、目标 ExtensionRef 和有序步骤。Runtime 只能执行计划中的目标，不能在加载过程中临时扫描目录、增加扩展。

Plan ID 用于关联报告和诊断记录，本期不提供持久任务队列，也不保证进程重启后重复提交同一计划不会再次执行。

安装时按依赖顺序执行。卸载时建议按相反顺序处理扩展单元，再逆序注销各自的 `handler_id`，先停止依赖方。如果其他生效包仍依赖目标包，应拒绝整包卸载；只卸载某个扩展单元时，也要检查本包内是否还有扩展依赖它。

是否允许一次操作同时处理所有受影响的依赖方，见 Q05。在确认之前，不能悄悄卸载调用方未指定的扩展或包。

`extension.apply` 返回 Report 后，上层要分清注册已一次生效、失败且 candidate 已丢掉，以及因为依赖问题没有执行。状态只有 SUCCESS 和 FAILED。依赖没有满足时使用 FAILED，并在 `error.message` 里写明原因，不使用 SKIPPED。`trigger` 仍可能抛出异常；调用方不能据此认定包没有生效，需要时触发 `package.query`。

### 4.4 Runtime：受限登记、归属和资源

Runtime 提供计划执行和失败补偿能力，分别由 `extension.apply` 与 `extension.rollback` 的 Core Handler 使用。执行前根据可信计划记录核对 Plan 来源、操作关联、目标、授权和当前状态，不接受仅凭调用方输入拼装的计划。条件已经变化时停止并报告，由上层决定是否重新解析依赖和生成计划。

安装 Plan 由 `extension.apply` 的 Core Handler 按顺序处理每个扩展：核实入口，加载模块，调用 `create_extension` 创建实例，再调用实例 `register` 完成登记。这些是同一次执行的内部步骤，不再拆分加载与注册事件。Handler 可以定义在独立的 handler.py 中，由入口模块导入。

实例 `register` 只能取得 Runtime 提供的受限入口，可以登记 USER 和 BUILTIN，不能登记 CORE，也不能传入或改写 `PackageMetadata`，也不能替换事件定义。prepare 阶段这些 Handler 只进入 candidate，不写入 EventBus，因此业务 `trigger` 还看不见它们。该包的扩展全部成功后，commit 在一次没有 `await` 的同步更新里，由 Runtime 调用 EventBus 的 `register_event_handler(..., package_metadata=...)` 写入整套 Handler；`package_metadata` 取自 Plan 上已核实的 `PackageMetadata`（含包根）。业务 `trigger` 进入该 Handler 时，Ops 使用登记时保存的 `PackageMetadata`。写入前开始的调用继续使用旧快照；写入后开始的调用看到这个包的整套新 Handler。

任一加载或登记步骤失败时，执行 Handler 停止当前安装 Plan，保存已创建实例、已注册标识和资源事实，再显式触发 `extension.rollback`。回滚 Handler 仅处理这份失败 Plan 的实际记录：逆序注销、等待相关使用结束、关闭实例并释放资源。即使 `register` 未正常返回，也依据已经捕获的标识清理；回滚失败保留原始错误及残留。

卸载 Plan 同样由 `extension.apply` 的 Core Handler 执行。它根据现有归属记录依次注销、确认相关使用结束、关闭实例并释放资源；等待失败时不得继续释放。存在已确认所有权的安装产物时，最后使用 Package Manager 核对并清理。正常卸载失败不触发安装回滚来重建旧实例，而是保留卸载进展和未完成项。

注销、等待、关闭和记录更新可以复用 Runtime 的辅助函数，文件核对与清理使用 Package Manager 的辅助能力；这些步骤受当前执行或回滚事件统一约束。安装文件清理与注册撤销分别记录，不能因目录还在就再次注册，也不能因注册已移除就认为资源已经释放。

整个安装 Plan 成功后，Runtime 记下生效记录并产出 Report。同一个已生效 id 再次 `install` 时，返回 `EXTENSION_PACKAGE_EXISTS`，不再加载一遍。

每次 prepare 给这次加载单独建一个包，包根就是这份安装副本。入口文件放进这个包里执行。包根之内可以互相导入：同目录用 `from .handler import ...`，子目录用 `from .helpers import ...`，上一级用 `from ..common import ...`。点号对应的就是磁盘上的那一层目录。超出包根的路径不会被装进来。新的 prepare 读的是文件现在的内容。早先创建出来的函数对象仍执行它被创建时的那份代码。扩展在 `register` 里自己启动的后台任务，Runtime 不负责停止。

### 4.5 与 Events 的协作边界

Extension 使用 Events 的公共接口完成事件装配、操作调用和 Handler 注册。事件匹配、处理器执行、会话适配及 Ops 构建由 [Events 详细架构](../../../events/docs/designs/events-architecture.md)统一说明，本文只维护扩展机制需要提供和取得的内容。

| 协作事项 | Extension 负责什么 | 从 Events 取得什么 |
| --- | --- | --- |
| 初始化装配 | EventBus 初始化登记 core 的管理事件。Registry.init 只登记 jiuwenswarm 的内置事件 | 已经初始化好的 EventBus |
| 管理操作 | 业务代码或 Handler 显式触发对应事件，核对结果后决定是否继续 | 事件处理结果或异常 |
| 扩展注册 | Runtime 限制登记范围；发布时向 Bus 传入已核实的 `PackageMetadata`，保存每条 `handler_id` | `register_event_handler`（含可选 `package_metadata`）及注销入口 |
| 平台能力 | 提供可信包来源；具体能力由所属业务模块提供 | Ops；其中包上下文来自该 Handler 登记时的 `PackageMetadata` |
| 卸载协作 | Runtime 指定等待范围，确认相关调用与能力使用结束后再释放资源 | 按 Events 生命周期约定取得的使用结束确认 |

第 4.1.1 节的独立操作均经过对应事件。业务代码或 Handler 显式发起解析、计划执行、查询和回滚等请求；注销、等待、释放和产物清理由执行或回滚 Handler 在内部按条件推进。EventBus 不读取 Plan 或包依赖来安排下一项包操作。

管理事件的名称、输入输出和结果以本模块规约为准。通用事件接口、Handler 协议和 `trigger` 开始时的注册表快照沿用 Events。卸载不等待使用结束：有在途调用就返回 `EXTENSION_PACKAGE_BUSY`。

## 5. 模块协作与处理流程

本节按事件说明处理逻辑和模块交接。第 5.1、5.2 节展开安装、回滚、卸载和重载，第 5.4 节展开解析、依赖解析、计划、执行和查询，第 5.5 节汇总事件之间的依赖。第 4 节维护模块能力及事件边界，本节说明这些能力在一次请求中如何被使用。

以下时序图使用同一约定：实线箭头表示调用，虚线箭头表示返回；`trigger(...)` 是业务代码或 Handler 显式发起的事件调用，其他调用表示当前事件内部使用模块能力。EventBus 只展示公共调度、注册和注销接口，不展开其内部执行机制。图中的模块名称是逻辑职责，不是已实现的类名或方法签名。事件输入、结果和关联记录均为目标设计，完整字段及接口仍由后续规约确定。

### 5.1 启动与安装主流程

初始化显式登记第 4.1.1 节的管理事件及其 Core Handler。业务代码调用 `Registry.install` 后触发 `package.install`。该事件接收本地包路径集合和允许的安装范围，负责把各子事件的结果组织为一次安装结果，不直接调用其他独立事件的内部实现。

**Core Handler 的处理逻辑：**

1. 核对调用方权限、Registry 就绪状态和根操作准入，确定本次路径集合及允许范围。来自重载的合法子调用复用父操作关联，不重新等待父操作持有的准入。
2. 逐路径触发 `package.parse`，保留候选包及解析失败原因。候选不足以证明整批可安装，继续触发一次 `package.resolve`，取得合法包集合、依赖关系和包序。
3. 按包序处理目标。每个包开始前核对其依赖是否实际可用；本批被依赖包失败或仍有残留时，跳过依赖方。独立包可以继续。
4. 对可执行包依次触发 `extension.plan` 和 `extension.apply`。计划不合法时不执行；执行失败时核对其补偿结果，不再为当前包推进安装。
5. 按实际记录汇总每个包的成功、失败、依赖跳过和清理残留。只有满足第 6.3 节的完成条件，才能将该包计为安装成功。

Package Manager 的能力通过解析和依赖解析事件使用，Orchestrator 的能力通过计划事件使用，Runtime 的能力通过依赖解析、计划和执行事件使用。以下时序图只展示正常执行路径；子事件内部怎样调用模块见第 5.4 节，安装失败的补偿见第 5.2.1 节。

```mermaid
sequenceDiagram
    participant H as 业务代码 / Registry
    participant B as EventBus
    participant I as package.install 事件的 Core Handler
    participant P as 包准备事件的 Core Handler
    participant O as extension.plan 事件的 Core Handler
    participant T as extension.apply 事件的 Core Handler
    H->>B: trigger(package.install, 本地路径集合)
    B->>I: 调度安装 Handler
    loop 每个本地路径
        I->>B: trigger(package.parse)
        B->>P: 调度解析 Handler
        P->>P: Package Manager 解析路径与声明
        P-->>B: 候选包或解析失败原因
        B-->>I: 解析结果
    end
    I->>B: trigger(package.resolve, 候选集合与操作)
    B->>P: 调度依赖解析 Handler
    P->>P: 核对版本与依赖 读取当前生效记录
    P-->>B: 合法包序与拒绝原因
    B-->>I: 依赖解析结果
    loop 每个可安装包，先被依赖方后依赖方
        I->>B: trigger(extension.plan)
        B->>O: 调度计划 Handler
        O->>O: Orchestrator 确定包内目标和步骤
        O->>O: Runtime 保存 Plan 与操作及目标的可信关联
        O-->>B: 单包 Plan
        B-->>I: 计划结果
        I->>B: trigger(extension.apply, Plan)
        B->>T: 调度执行 Handler
        loop Plan 中的每个扩展
            T->>T: 加载入口并 register，声明只进入 candidate
        end
        T->>B: 一次写入该包的整套 Handler，中间没有 await
        T->>T: Runtime 记下生效记录
        T-->>B: 根据执行事实生成 Report
        B-->>I: 执行结果
    end
    I-->>B: 逐包结果与依赖跳过原因
    B-->>H: 安装结果
```

P 合并展示 `package.parse` 与 `package.resolve` 的 Core Handler，两个事件各有独立处理器。T 内部加载、创建实例和登记属于 `extension.apply` 的执行步骤；公共注册接口是 Events 提供的基础能力，不为每条注册新增包管理事件。模块依赖与执行边界见第 4.1 节。

业务代码先确认初始化就绪，再提交安装。只有解析、依赖解析和计划结果有效，才允许导入代码；整个单包 Plan 的注册和生效记录完整后才能报告该包成功。依赖包失败时跳过其依赖方，独立包可继续。最后是否开放业务入口，由调用方根据所需包的实际结果决定，不能把批量请求返回等同于全部安装成功。

### 5.2 关键协作机制

#### 5.2.1 失败登记回滚与可见性

建议以“单个包的本次安装 Plan”作为回滚范围。例如，report-base 已注册 H1，enterprise-report 又注册 H2 后抛错。`extension.apply` 的 Core Handler 保存执行进展并触发 `extension.rollback`，回滚 Handler 使用 Runtime 逆序注销 H2、H1，确认相关使用结束后释放本批实例和资源；存在自有安装产物时，再使用 Package Manager 核对和清理。其他已成功包保留，依赖该失败包的后续包跳过。

**输入与前置条件：** `extension.rollback` 接收失败安装 Plan 的标识及操作关联。Runtime 中必须存在对应的可信计划和实际执行记录；调用方填写的注册列表不能作为删除依据。执行 Handler 可以在失败处理中发起，也可以由获准的业务代码在查询后请求继续清理。

**Core Handler 的处理逻辑：**

1. 使用 Runtime 核对失败 Plan、授权范围、原 Plan 是否已停止推进安装，以及已经完成的清理。父执行事件可以仍在等待本次回滚，不要求它先返回；但不能与仍在创建实例或注册的安装步骤并发清理。拒绝正常生效包、卸载 Plan 或与失败安装无关的目标。
2. 根据实际捕获的 `handler_id` 逆序注销本批注册；即使实例 `register` 抛错，也不能漏掉抛错前已成功登记的项。
3. 对注册已安全撤销的目标，使用 Runtime 确认相关调用和能力使用结束，再关闭实例、释放本批资源。某项失败时记录残留，其他可安全清理项可以继续；仍在用的共享资源必须保留。
4. 只有对应资源满足释放条件后，才使用 Package Manager 核对并清理本次拥有的安装产物。保存每步结果，返回清理结果及未完成项，保留原安装错误。

下面展示目标可完整清理的路径。注销和等待的失败分支统一按第 8 节保留记录，不能越过失败条件释放资源。

```mermaid
sequenceDiagram
    participant H as 执行 Handler / 获准的业务代码
    participant B as EventBus
    participant C as extension.rollback 事件的 Core Handler
    participant R as Extension Runtime
    participant P as Package Manager
    H->>B: trigger(extension.rollback, 失败 Plan)
    B->>C: 调度回滚 Handler
    C->>R: 核对失败记录、范围与已清理进展
    R-->>C: 本批注册、实例与残留资源
    C->>R: 按记录逆序清理本批注册
    loop 仍需撤销的注册
        R->>B: 公共注销接口(handler_id)
        B-->>R: 注销结果
        R->>R: 保存清理进展
    end
    C->>R: 确认使用结束，再关闭实例、释放资源
    Note over C,R: 等待未结束时保留资源 不清理对应文件
    R-->>C: 可清理产物及未完成项
    C->>P: 核对所有权与释放条件，清理自有产物
    P-->>C: 文件清理结果及残留
    C->>R: 保存最终清理进展与原始错误
    C-->>B: 补偿结果与残留
    B-->>H: 回滚结果
```

回滚的目标是还没有写入 Bus 的失败 Plan。它丢掉 candidate 和这次加载的模块，不修改已经生效的注册表，也不删除用户来源。已经发布成功的包用卸载处理，不用回滚代替。

**回滚与同时生效。** prepare 失败时，Handler 还没有进入 Bus 注册表，回滚只丢掉 candidate 和这次加载的模块。commit 把一个包的全部 Handler 一次写进注册表，中间没有 `await`。业务 `trigger` 在开始时读取注册表快照，所以不会看见半个包。写入注册表之前已经开始的调用继续使用自己的快照。回滚不能撤销扩展代码已经造成的外部影响。

#### 5.2.2 卸载与资源释放

`Registry.uninstall` 显式触发 `package.uninstall`。卸载 Handler 先通过 `package.resolve` 检查反向依赖，再看这个包有没有在途调用。有在途调用就返回 `EXTENSION_PACKAGE_BUSY`，注册表和安装目录都不变，也不等待。没有反向依赖、也没有在途调用时，才触发 `extension.plan` 和 `extension.apply`，从 Bus 注册表移除该包的 Handler，并删除安装目录里的对应版本。不另设等待事件。

**输入与处理逻辑：**

1. 接收目标包标识及允许的卸载范围，核对权限、准入和 Runtime 的当前记录。目标以进程内生效及残留记录定位，不要求重新导入或重新解析磁盘上的旧代码。
2. 触发卸载模式的 `package.resolve`，检查反向依赖和在途调用。仍被其他已生效包依赖，或当前有调用，就拒绝，不进入删除。
3. 两项都通过后，触发 `extension.plan` 和 `extension.apply`，从注册表移除 Handler，并删除安装目录中的该版本。
4. 只有反向依赖和在途调用两项都通过，才报告卸载成功。任一不通过时，Bus 注册表和安装目录保持原样。

该 Handler 通过依赖解析、计划和执行事件使用三个模块。图中将多包循环简化为一个目标包；解析和计划事件的模块交接见第 5.4.2、5.4.3 节，执行内部步骤见第 5.4.4 节。

```mermaid
sequenceDiagram
    participant H as 业务代码 / Registry
    participant B as EventBus
    participant U as package.uninstall 事件的 Core Handler
    participant C as 准备或计划事件的 Core Handler
    participant T as extension.apply 事件的 Core Handler
    H->>B: trigger(package.uninstall)
    B->>U: 调度卸载 Handler
    U->>B: trigger(package.resolve, 卸载目标与范围)
    B->>C: 核对反向依赖和在途调用
    alt 仍被依赖或仍有调用
        C-->>B: PACKAGE_IN_USE 或 PACKAGE_BUSY
        B-->>U: 拒绝，注册表和安装目录不变
    else 可以卸载
        U->>B: trigger(extension.plan, 单包卸载操作)
        B->>C: 生成清理顺序
        C-->>B: 卸载 Plan
        B-->>U: 计划结果
        U->>B: trigger(extension.apply, Plan)
        B->>T: 调度卸载计划执行 Handler
        T->>B: 从注册表移除该包的 Handler
        T->>T: Package Manager 删除安装目录中的该版本
        T-->>B: SUCCESS
        B-->>U: 卸载 Plan 的 Report
    end
    U-->>B: 卸载结果
    B-->>H: 卸载结果
```

卸载在发现在途调用时立即失败，不等待这些调用结束。`extension.rollback` 只处理还没有写入 Bus 的 candidate。已经开始的业务调用继续使用它的注册表快照；卸载不会为了等它结束而停住。

仍被其他已生效包依赖时返回 `EXTENSION_PACKAGE_IN_USE`，不删除文件。调用方的源码目录和上传的 zip 不是安装目录里的副本，任何卸载和回滚都不删除它们。

#### 5.2.3 重载与停止

`Registry.reload` 触发 `package.reload`。它不先卸载。Core Handler 按 `package.id` 找到安装目录，重新读取 manifest，并用 Runtime 的生效记录检查两件事：这个包自己的依赖是否仍然满足；已经依赖它的包是否仍然接受新版本。两项都通过才触发 `extension.plan` 和 `extension.apply`。

`extension.apply` 用新的模块名加载安装目录里的当前文件，把 Handler 收进 candidate。全部成功后，在一次没有 `await` 的同步更新里替换该包在 Bus 注册表上的 Handler。此后开始的 `trigger` 使用新快照和新模块。更新开始前已经取得快照的调用继续执行旧模块里的代码。这些调用结束后再从 `sys.modules` 去掉旧模块，`reload` 的返回不等这一步。准备或校验失败时，Bus 上的当前 Handler 保持不变。

**输入与 Core Handler 的处理逻辑：**

1. 接收 `package_id`。目录不存在或包尚未生效时返回 FAILED，其他包不变。
2. 读取安装目录里的 manifest。依赖方不接受新版本时返回 `EXTENSION_DEPENDENT_INCOMPATIBLE`，不修改注册表。
3. 触发 `extension.plan`，再触发 `extension.apply`。apply 只发布这个包的新 Handler。
4. 发布成功后 generation 加 1，Runtime 更新生效记录。Report 为 SUCCESS。旧调用结束后的模块清理不改变这个结果。

```mermaid
sequenceDiagram
    participant H as 业务代码 / Registry
    participant B as EventBus
    participant C as package.reload 事件的 Core Handler
    participant P as extension.plan 事件的 Core Handler
    participant T as extension.apply 事件的 Core Handler
    H->>B: trigger(package.reload, package_id)
    B->>C: 调度重载 Handler
    C->>C: 读取安装目录，核对依赖方是否接受新版本
    alt 依赖不满足或目录无效
        C-->>B: FAILED，注册表不变
    else 可以重载
        C->>B: trigger(extension.plan)
        B->>P: Orchestrator 生成新一代 Plan
        P-->>B: Plan
        B-->>C: 计划结果
        C->>B: trigger(extension.apply, Plan)
        B->>T: 用新模块名加载，收集 candidate
        T->>B: 一次写入该包的整套 Handler
        T-->>B: SUCCESS
        B-->>C: 执行结果
        Note over B: 新的 trigger 读取新快照；旧调用保持自己的快照
    end
    B-->>H: 重载结果
```

依赖它的包只要仍满足版本条件，重载就不被拒绝。版本条件不再满足时，重载失败，已生效的 Handler 继续留在注册表里。重载会重新登记 USER 或 BUILTIN Handler，同层顺序按这次写入注册表的顺序确定。

停止由业务代码发起。调用方先关闭外部新请求入口，再显式触发正常包的卸载事件，或针对失败安装触发回滚事件；已接收操作仍可按授权完成必要子事件和内部清理。EventBus 不监听停止状态自动发起包操作。确认相关使用结束后，业务代码再释放平台资源；停止不等于删除源码。

### 5.3 个人扩展登记与使用

扩展作者在 manifest 声明入口，提供 `create_extension` 工厂及实例 `register` 方法。`extension.apply` 的 Core Handler 使用 Runtime 加载并创建实例，在同一次计划执行中调用 `register`；扩展只取得受限的 USER / BUILTIN 登记能力，不能直接提交自制 Plan 或借包管理事件扩大权限。

Handler 签名、平台能力访问和通用事件接口遵循 [Events 接入约定](../../../events/docs/designs/events-architecture.md)。Extension 负责将登记和后续使用关联到正确的包、扩展单元与注册批次，不展开 Events 内部机制。

这些是目标接入方式，目前没有可直接运行的公共 import。集成验证要覆盖解析、依赖解析、计划、执行及真实业务调用，确认扩展参与、注册归属和操作结果一致，不能只验证 `register` 被调用。

### 5.4 准备、执行与查询事件的处理链路

下面展开另外五种事件。它们既可供上层管理 Handler 复用，也可由获准的业务代码独立请求；独立请求仍需满足同样的前置条件。图中的“调用方”指本次事件的实际发起者，可能是业务代码，也可能是父 Handler。

#### 5.4.1 package.parse：解析单个包

**输入与结果：** 输入一个本地目录及允许的访问范围；输出明确的包根、经过基本检查的 manifest 和候选包信息，或具体解析失败原因。候选包只证明来源和声明可解析，不证明包依赖可满足。

**Core Handler 的处理逻辑：**

1. 核对来源访问权限，使用 Package Manager 将输入目录解析为实际包根，处理路径规范化、符号链接和目录联接。
2. 使用 Package Manager 读取 `manifest.yaml`，检查版本格式、必填字段、扩展 ID、运行域和入口声明；核对实际入口未越出包根。这里只检查声明的格式与边界，不判断整批包之间的版本兼容性。
3. 将包根与本次解析出的声明一起返回。任何必要检查失败，都返回对应失败原因；不导入代码、不创建实例、不注册 Handler，也不自动触发依赖解析。

```mermaid
sequenceDiagram
    participant H as 调用方
    participant B as EventBus
    participant C as package.parse 事件的 Core Handler
    participant P as Package Manager
    H->>B: trigger(package.parse, 本地目录)
    B->>C: 调度解析 Handler
    C->>C: 核对来源访问权限
    C->>P: 确定实际包根，读取 manifest
    P->>P: 检查格式、声明 核对入口边界
    P-->>C: 候选包，或解析失败原因
    C-->>B: 包根、声明和解析结果
    B-->>H: 交回结果，由调用方决定下一步
```

解析失败由发起者决定是否继续处理其他路径。解析成功后，安装 Handler 才将候选集合交给 `package.resolve`；这里不自动选择其他目录或下载缺失依赖。具体路径与内容一致性限制见第 4.2.1 节。

#### 5.4.2 package.resolve：确认包依赖与处理顺序

**输入与结果：** 输入操作类型、安装候选集合或卸载目标及允许范围。Core Handler 自行从 Runtime 读取当前生效与残留事实，不把调用方提交的“已安装列表”作为事实依据。输出合法目标、包级顺序、依赖关系及拒绝原因，不执行安装或卸载。

**Core Handler 的处理逻辑：**

1. 核对目标与授权范围。安装使用已解析的包根和声明；卸载使用 Runtime 中的目标记录，不依赖旧 manifest 文件仍然可读。
2. 使用 Runtime 读取当前生效版本、依赖关系及与本次操作冲突的执行和清理记录，再将必要事实交给 Package Manager。
3. 安装模式下，Package Manager 检查同 ID 冲突、版本与包依赖、缺失和循环，确定可执行包集合及先被依赖方后依赖方的顺序。被拒绝目标及受影响的依赖方分别保留原因。
4. 卸载模式下，Package Manager 检查反向依赖和允许范围，先排列依赖方，再排列被依赖方；存在范围外依赖方时拒绝对应卸载，不自动加入未授权目标。
5. 交回选择结果。该结果只是在读取到的事实下成立；实际执行前条件已变化时，后续 Handler 应停止，由父流程重新请求依赖解析和计划。

```mermaid
sequenceDiagram
    participant H as 调用方
    participant B as EventBus
    participant C as package.resolve 事件的 Core Handler
    participant R as Extension Runtime
    participant P as Package Manager
    H->>B: trigger(package.resolve, 操作、目标与范围)
    B->>C: 调度依赖解析 Handler
    C->>R: 读取生效版本、依赖和冲突记录
    R-->>C: 当前可确认的运行事实
    C->>P: 核对目标来源、范围与运行事实
    alt 安装
        P->>P: 检查版本与包依赖 生成安装包序
    else 卸载
        P->>P: 检查反向依赖，生成卸载包序
    end
    P-->>C: 合法目标、依赖关系、顺序和拒绝原因
    C-->>B: 依赖解析结果
    B-->>H: 交回结果，不自动生成 Plan
```

Package Manager 负责声明和包级依赖判断，Runtime 负责当前运行事实，两者的记录不能互相代替。排序规则及冲突策略见第 4.2.3 节；尚未确定的版本或冲突处理不能解释为默认允许替换。

#### 5.4.3 extension.plan：生成可信单包计划

**输入与结果：** 输入已通过依赖解析的一个包、操作类型、目标范围及可信操作关联；输出 Runtime 可核对的单包 Plan 标识或引用。计划生成不加载代码，也不改变注册关系。

**Core Handler 的处理逻辑：**

1. 核对包来源、依赖解析结果与本次范围的关联，使用 Runtime 确认当前生效、实例和注册事实。准备结果已过期或目标不再满足条件时拒绝生成可执行计划。
2. 将选定包、允许范围和必要运行事实交给 Orchestrator。安装按包内依赖确定扩展及顺序；卸载依据实际实例和注册归属确定清理目标，建议按相反依赖顺序处理。
3. Orchestrator 返回单包 Plan 后，将它与根操作、已核实包根和目标范围一起交给 Runtime 保存可信关联。关联保存失败就不返回可执行计划；不把调用方自行构造的 Plan 登记为可信计划。
4. 将计划标识或引用返回给发起者。由发起者决定是否触发 `extension.apply`，计划 Handler 本身不自动执行计划。

```mermaid
sequenceDiagram
    participant H as 调用方
    participant B as EventBus
    participant C as extension.plan 事件的 Core Handler
    participant R as Extension Runtime
    participant O as Extension Orchestrator
    H->>B: trigger(extension.plan, 单包操作与范围)
    B->>C: 调度计划 Handler
    C->>C: 核对来源、依赖结果与授权范围
    C->>R: 核对当前生效、实例及注册事实
    R-->>C: 与目标关联的运行事实
    C->>O: 确定包内依赖、目标扩展与步骤
    O-->>C: 单包 Plan
    C->>R: 保存 Plan 与操作、来源、范围的可信关联
    R-->>C: 可核对的计划标识或引用
    C-->>B: 计划结果
    B-->>H: 交回 Plan，此时尚未执行
```

包级来源与包序由前一阶段确定，Orchestrator 不重新选择版本或扩大目标。计划失败只拒绝本次计划，不触发安装回滚，因为尚未加载或注册；计划及关联记录的保存限制见第 4.3、7.1 节。

#### 5.4.4 extension.apply：执行安装或卸载计划

**输入与结果：** 输入可信 Plan 的标识或引用及操作关联；Runtime 核对原计划、授权、目标和当前进展后执行，输出基于事实的 Report。该事件承担计划的副作用，不能仅凭 Plan 格式合法就执行，也不重新生成计划。

**Core Handler 的处理逻辑：**

1. 核对 Plan 的包、操作和当前生效记录。id 已经生效时，不把这个包再装一遍。
2. 安装或重载时，按 Plan 加载入口并调用受限 `register`。声明先进入 candidate，不写入 Bus。该包全部成功后，一次把整套 Handler 写入注册表，并更新 Runtime 的生效记录。
3. 写入之前失败时，触发 `extension.rollback`，丢掉 candidate。已经生效的注册表不变。
4. 卸载时，先确认没有反向依赖、也没有在途调用。否则失败返回，不改注册表，不删安装目录。通过后移除该包的 Handler，并删除安装副本。
5. Report 只有 SUCCESS 或 FAILED。依赖问题写在失败原因里。

下图展开安装与卸载的模块调用。失败安装的回滚内部链路见第 5.2.1 节；“扩展实例”是 Runtime 管理的包内实例，受限登记入口不能让它修改事件定义或冒用其他包身份。

```mermaid
sequenceDiagram
    participant H as 调用方
    participant B as EventBus
    participant C as extension.apply 事件的 Core Handler
    participant R as Extension Runtime
    participant E as 扩展实例
    participant P as Package Manager
    H->>B: trigger(extension.apply, 可信 Plan)
    B->>C: 调度执行 Handler
    C->>R: 核对 Plan、授权与当前进展
    R-->>C: 允许执行的目标和步骤
    alt 安装 Plan
        C->>R: 按计划加载入口并创建实例
        R->>E: 调用 register，提供受限登记入口
        loop 每条 Handler 登记
            E->>R: 经受限入口提交 USER 或 BUILTIN，先留在 candidate
        end
        R->>B: 全部成功后一次写入 Bus 注册表
        R->>R: 保存整包归属和生效记录
        E-->>R: register 结果
        R-->>C: 实例、注册进展与执行结果
        opt 加载或登记失败
            C->>R: 保存失败事实，停止当前 Plan
            C->>B: trigger(extension.rollback, 失败 Plan)
            Note over B,C: 调度回滚 Handler，内部链路见第 5.2.1 节
            B-->>C: 回滚结果与残留
        end
    else 卸载 Plan
        C->>R: 按记录逆序撤销注册
        R->>B: 公共注销接口撤销目标 handler_id
        B-->>R: 注销结果
        R->>R: 保存进展，确认相关使用结束
        R->>E: 关闭实例并释放资源
        E-->>R: 关闭结果
        R-->>C: 已释放目标、可清理产物与残留
        opt 对应资源已安全释放且存在自有产物
            C->>P: 核对所有权与释放条件，清理产物
            P-->>C: 清理结果及残留
        end
    end
    C->>R: 保存实际结果，生成 Report
    R-->>C: 完成依据与待处理项
    C-->>B: Plan 执行结果
    B-->>H: 交回 Report
```

图中安装实例步骤按每个目标扩展重复，卸载注销步骤按目标记录重复。发生异常或等待未结束时，不再进入依赖其成功的后续步骤；图中的关闭和清理箭头只在相应前置条件成立时执行。Events 注册返回与 Runtime 捕获归属之间不插入异步等待。如何核对使用结束和资源关闭见第 4.4、5.2.2 节，不在本节展开 Events 的生命周期实现。

#### 5.4.5 package.query：读取包与操作事实

**输入与结果：** 输入包、操作或 Plan 标识，以及允许查询的范围；输出查询时可确认的包来源、生效状态、执行进展和残留信息。查询可独立发起，不要求先成功完成某个管理事件。

**Core Handler 的处理逻辑：**

1. 核对查询权限及目标范围，将输入标识与模块记录匹配。标识只用于定位，不证明调用方可以读取其他包或操作。
2. 使用 Package Manager 读取已保存的包来源、声明和依赖信息，使用 Runtime 读取可信 Plan、生效、执行及清理事实。按查询目标选择必要记录，不为查询导入代码或执行注册。
3. 将两类信息关联返回，区分进行中、已经完成、失败残留、记录不存在或证据不足。某项无法读取时明确说明，不能用空记录推断未执行，也不承诺跨模块读取是全局一致快照。
4. 由发起者根据结果决定是否请求失败安装回滚、卸载续清理或新的管理操作。查询 Handler 不补跑步骤、不修改操作进度，也不自动触发后续事件。

```mermaid
sequenceDiagram
    participant H as 调用方
    participant B as EventBus
    participant C as package.query 事件的 Core Handler
    participant P as Package Manager
    participant R as Extension Runtime
    H->>B: trigger(package.query, 目标标识与范围)
    B->>C: 调度查询 Handler
    C->>C: 核对查询权限与目标
    C->>P: 读取已记录的来源、声明和依赖
    P-->>C: 包信息及可确认范围
    C->>R: 读取 Plan、生效、执行与清理事实
    R-->>C: 当前进展、完成依据和残留
    C->>C: 关联结果 标明进行中或证据不足
    C-->>B: 查询结果，不修改操作状态
    B-->>H: 由调用方决定是否请求后续处理
```

例如，安装的 Core Handler 已经把包写入注册表，外层处理结果时却抛错。`package.query` 应能读到 Runtime 里的生效记录，调用方据此避免把同一个 id 再装一遍。

### 5.5 事件之间的依赖与调用约束

事件之间有两种关系：**调用依赖**表示某个 Core Handler 显式触发并等待另一个事件；**结果依赖**表示执行前必须取得并核对已有结果或运行事实。结果依赖不要求由 EventBus 连续执行一串事件，也不意味着每次查询都要重新解析和规划。

| 事件及处理说明 | 本 Core Handler 触发的子事件 | 执行前依赖的结果或事实 | 向后续交付什么 |
| --- | --- | --- | --- |
| [`package.install`](#51-启动与安装主流程) | 逐路径 `package.parse`，再 `package.resolve`，逐包 `extension.plan`、`extension.apply` | Registry 已 init；每个包开始时，其依赖已经实际可用 | 逐包 SUCCESS 或 FAILED |
| [`package.uninstall`](#522-卸载与资源释放) | 先 `package.resolve`；允许时再 `extension.plan`、`extension.apply` | Runtime 的生效记录。仍被依赖或仍有在途调用时不进入 apply | 成功则移除注册并删除安装副本；失败则保持原样 |
| [`package.reload`](#523-重载与停止) | 依赖方接受新版本后，`extension.plan`、`extension.apply` | 安装目录中的当前 manifest，以及 Runtime 里依赖方的版本条件 | 该包的新 Handler 一次进入后续快照；失败时注册表不变 |
| [`package.parse`](#541-packageparse解析单个包) | 无 | 可访问的本地来源、支持的声明格式和合法入口 | 候选包，供依赖解析使用 |
| [`package.resolve`](#542-packageresolve确认包依赖与处理顺序) | 无 | 安装候选包或卸载目标；Runtime 当前生效、依赖及冲突记录 | 合法目标、包序与拒绝原因，供单包计划使用 |
| [`extension.plan`](#543-extensionplan生成可信单包计划) | 无 | 已确认的包与范围、有效依赖结果；Runtime 当前实例和注册事实 | 保存可信关联的单包 Plan，供执行核对 |
| [`extension.apply`](#544-extensionapply执行安装或卸载计划) | 仅安装失败时 `extension.rollback` | 可信 Plan、当前权限、可执行状态及已保存进展 | 执行 Report、生效或清理事实；失败时保留补偿结果 |
| [`extension.rollback`](#521-失败登记回滚与可见性) | 无 | 失败安装 Plan、实际执行与清理记录，原 Plan 不再推进安装；清理对象与权限匹配 | 补偿结果与残留，供管理汇总、查询和后续清理 |
| [`package.query`](#545-packagequery读取包与操作事实) | 无 | 查询授权及可定位的模块记录；不要求前序管理调用成功 | 当前可确认的事实，由发起者决定下一步 |

正常安装的结果交接顺序为“解析 → 包依赖确认 → 单包计划 → 计划执行”，但解析事件不触发依赖解析，计划事件也不触发执行；这些推进都由安装 Handler 决定。卸载复用后三个事件，依据已生效记录确定目标，不重新加载旧代码。重载复用完整卸载与安装链路，不跳过其中任何必要阶段。

失败安装时，执行 Handler 在停止当前 Plan 后触发回滚，并等待补偿结果；回滚不反向触发执行或安装，避免形成恢复循环。查询是独立读入口，只提供决策依据。合法父子调用共享受控操作关联并保持或收紧范围；外部直接请求下层事件时也必须满足同等前置条件，不能因绕过上层便捷方法而免于校验。准入、递归和取消规则见第 4.1.2、6.2、8 节。

## 6. 状态、事件与完成条件

### 6.1 状态分类与转换

下面的状态用于解释一个操作走到了哪一步，不表示最终 API 已经定义了这些枚举值。

| 状态类别 | 何时变化、由谁确认 | 对后续流程的影响 |
| --- | --- | --- |
| Registry 未就绪 / 就绪 / 停止中 | Registry 根据初始化结果和调用方的准入控制确认 | 未就绪时拒绝管理操作；停止中拒绝外部新操作 |
| 包已解析 | 解析事件确认包根、声明格式与入口条件 | 可以进入依赖解析，不代表依赖已满足或可以导入代码 |
| 包依赖已确认 | 依赖解析事件返回合法包集合、包序及拒绝原因 | 可以为选定包生成单包 Plan；条件变化时需重新核对 |
| 计划已生成 | 计划事件确认包内目标与有序步骤 | 可以请求执行，仍需核对当前授权和记录，此时尚未注册 |
| 计划登记中 / 已生效 | Runtime 逐条保存标识，整个计划完成后确认生效记录 | 全部要求满足后，才能出具安装成功报告 |
| 回滚或卸载清理中 / 清理完成 | 分别确认注册撤销、相关使用结束和资源清理结果 | 清理结束前保留待处理记录，不发起与之冲突的重装 |
| 操作失败且有残留 | Runtime 保存原始错误和未完成清理项 | 限制会产生冲突的后续操作，继续核对并清理 |

文件是否存在、Handler 是否仍注册、资源是否仍在使用，需要分别记录。例如，注销后新调用已无法选中 Handler，但旧调用还在使用它的包资源。这时不能只用一个“已卸载”布尔值概括全部情况。

### 6.2 事件传递、确认与流量控制

包管理和业务操作都是显式发起的进程内调用。父 Handler 决定是否触发、等待及处理子操作，EventBus 不根据包依赖或结果生成下一项包操作，也不自动安排重试或回滚。通用事件调用方式直接遵循 Events 公共接口。

没有收到管理结果时，通过 `package.query` 查询 Runtime 的实际记录，再决定是否重试。对同一路径再次触发 `package.install`，并不天然保证结果不变或不会重复注册。子事件的成功、短路、失败和未完成需要区分，不能以 `trigger` 正常返回替代核心步骤的完成依据。

目前建议在单个 Registry 对应的运行实例内协调独立的根管理操作，并限制等待数量；该约束也覆盖直接触发事件的入口。父操作派生的合法子事件继承同一受控操作关联，不重新争用父操作持有的串行准入。业务并发和取消仍遵循 Events，超时由调用方与能力实现处理，具体协调方式与容量限额由 Q06 及实现方案确定。

### 6.3 完成与结果交接

单包安装要报告 SUCCESS，必须同时满足：

- Plan 内所有目标均正常完成注册。
- Runtime 已记录全部注册的所属包、扩展单元和计划。
- 生效记录完整，本计划没有尚未报告的失败。

Report 概括 Plan 中的实际注册、资源变化与清理结果。解析、依赖解析、计划、执行和回滚事件分别提供自己的完成依据；加载、登记和等待等内部步骤的结果由执行或回滚 Handler 记录并汇总。上层核对目标与事实，必要时触发 `package.query`，不能把未执行或无效返回当作已完成。

卸载要报告 SUCCESS，则必须确认依赖检查通过、目标注册已撤销、相关使用已结束，且本次应清理的资源和安装产物都已处理完成。调用方拥有的源码目录不一定属于安装产物，因此“清理完成”不等于删除源码，见第 7.2 节。

批量操作要逐包给出 SUCCESS 或 FAILED。依赖没有满足时用 FAILED，并写明原因。父 Core Handler 根据这些结果决定是否触发下一步。并发上的根操作准入仍见 Q06。

如果默认安装已完成，而外层 Handler 处理结果时失败，调用可能报错，但包已经生效。调用方应先触发 `package.query` 核对，不直接重新注册。重试卸载或回滚时，也根据保存的进展继续清理，不能绕过事件直接重放内部步骤。

## 7. 数据管理与保存规则

### 7.1 数据由谁维护，以什么记录为准

表中的管理者表示数据归属。对应事件的 Core Handler 可以使用多个模块的能力完成一项操作；读取记录、更新进展和捕获 `handler_id` 属于该事件内部步骤。独立状态查询通过 `package.query`，自有文件清理则在卸载执行或失败回滚中完成，边界见第 4.1.3 节。

| 数据 | 提供或维护方 | 使用和保存范围 |
| --- | --- | --- |
| 本地包路径 | 调用方提供，Package Manager 解析和校验 | 用于本次管理操作；Extension 不定义产品配置文件 |
| manifest 和选定包根 | 包提供声明，Package Manager 校验实际选定内容 | 同次计划保持固定；是否复制或采用不可变包内容仍待确认 |
| 包依赖和选择结果 | Package Manager 根据选定声明与已生效版本核对 | 用于本次安装及后续卸载依赖检查 |
| Plan | Orchestrator 生成，Runtime 保存其与操作、来源及目标的可信关联 | 内存中的单包操作计划，不是可恢复队列 |
| 事件与 Handler 注册记录 | Events | 由 Events 公共接口管理，Extension 保存返回的注册标识与包归属 |
| 实例、注册归属、资源与待清理项 | Runtime | 当前实例的内存记录，清理完成前不能丢弃 |
| Report 与诊断日志 | Runtime 提供执行事实，管理 Core Handler 汇总，调用方决定留存方式 | 用于结果交接和排查，不替代注册及资源使用记录 |

### 7.2 文件所有权与非事务交接

本期“装包”首先表示解析并准备本地包，是否复制到托管目录尚未确定。因此，必须区分调用方提供的源码目录与 Package Manager 自己创建、拥有的安装产物。包文件清理只能处理已确认所有权的产物，不能直接删除任意输入目录。

磁盘文件准备、Events 注册、Runtime 记录更新和 Report 返回，是分别完成的步骤。目前没有保证它们全部同时成功的跨存储事务。Runtime 每次注册成功就保存标识，失败时根据本批记录撤销注册，补偿已发生的部分步骤。

这些记录目前只在内存中，进程崩溃后无法靠它们恢复完整清理过程。要支持崩溃恢复，需要另行设计可核对的包内容和操作记录；普通日志不能当作事务日志使用。

卸载计划在 `extension.apply` 的 Core Handler 内依次完成注销、使用结束确认、资源释放和自有产物清理；失败安装的同类清理由 `extension.rollback` 完成。文件删除前使用 Package Manager 核对所有权和释放条件，不设置独立文件删除事件。文件清理与注册撤销分别记录，文件仍在不表示 Handler 仍生效。

## 8. 故障处理与继续执行

### 8.1 故障范围与责任

下表中的模块名称表示故障处理所需的能力和记录由谁提供，处理逻辑由对应事件的 Core Handler 执行，必要时协调多个模块。EventBus 仅返回结果或传播异常；决定查询、回滚、重试或清理的是接收结果的业务代码或 Handler，并由其显式触发对应事件。不存在 EventBus 自动执行的异常补偿路径。

| 故障 | 谁处理、怎样处理 | 后续需要注意什么 |
| --- | --- | --- |
| manifest 无效、入口越界、运行域不支持或版本不兼容 | Package Manager / Orchestrator 在加载前拒绝相关目标 | 不受影响的包可继续，依赖失败目标的包跳过 |
| 模块导入、工厂或 `register` 失败 | Runtime 停止当前计划，逆序注销本批已完成的注册 | 无法撤销导入产生的外部影响 |
| 回滚时注销失败 | Runtime 保留原始错误和残留标识，继续尝试其他可清理项 | 不能报告回滚完成，也不能直接重复安装 |
| 安装事件的 Core Handler 已完成，外层事件却失败 | Registry / 调用方核对生效记录 | `trigger` 报错不等于包未安装 |
| 卸载时仍有调用 | Runtime 返回 `EXTENSION_PACKAGE_BUSY` | 不注销，不删除安装目录，也不进入重载的反面流程 |
| 文件清理失败 | Package Manager 记录残留产物，Runtime 保留其关联 | 注册可能已经撤销，后续只需补文件清理 |
| 取消请求与操作完成同时发生 | Runtime 核对已经执行的步骤，清理未完成批次 | 取消不等于从未执行；清理被打断时保留相关记录 |
| 进程退出或崩溃 | 调用方重启后根据包路径重新登记，并按自身策略或人工核对残留 | 本期不能自动续接 Plan 或补偿外部操作 |

### 8.2 外部副作用与重试条件

导入 Python 模块、执行 `create_extension` 或 `register` 时，都可能运行任意代码。Runtime 只能撤销自己记录的注册、清理自己管理的资源，无法自动撤销文件写入、网络请求或扩展自行创建的后台任务。这些已经产生的外部影响也称为“副作用”。

扩展作者应让注册阶段只声明注册关系。确实需要外部操作时，由所属能力提供操作回执和避免重复执行的依据。

管理请求超时或失败后，先触发 `package.query` 核对 Runtime 的生效记录。还没写入 Bus 的失败 candidate 交给 `extension.rollback`。有在途调用的卸载直接失败，不留下“卸了一半”的注册表。

### 8.3 可以重建什么

在同一进程中，先用 `package.query` 看生效记录。已经生效的 id 不再执行同一份安装 Plan。失败且尚未发布的 candidate 走 rollback。允许卸载时才走卸载。直接再调用扩展的 `register`，不能代替这次核对。

进程重启后，完成事件装配，再由 `package.install` 及其子事件重新解析、校验和登记扩展。这只能重建注册关系，不能恢复旧 Plan 的执行位置、Handler 的局部变量、运行中的工具操作或旧 Ops。

缺少持久操作记录、必要隔离和可查询的外部回执时，不能保证自动接管旧操作，也不能保证外部动作恰好执行一次。

## 9. 权限、隔离与资源限制

### 9.1 身份与权限检查

Registry 和直接 `trigger` 的受控入口使用相同授权检查，核对谁发起操作、允许处理哪些包。子事件保留原授权范围；各操作 Core Handler 在实际导入、注册、释放或删除前再次核对目标及资源条件。上层事件获准执行，不代表可以跳过下层事件或扩大权限。

包信息回答的是“当前执行的代码来自哪个包”，不能回答“原请求获准做什么”。用户填写的包名、Session metadata 和 USER 层级都不是授权依据。

个人扩展只能使用受限的 USER 登记入口，事件定义和平台处理器由可信代码登记。必需的授权检查应由受控业务入口或实际资源访问入口执行，不能只依赖扩展 Handler 拦截。

发起子操作时，只能保持或收紧原请求的权限。平台 Core Handler 的包关联信息不能代替调用方授权，也不能使子操作获得额外权限。通用身份和授权约定见 [Events 第 9.1 节](../../../events/docs/designs/events-architecture.md#91-身份与授权检查)。

撤销包权限与卸载包是两件事。即使旧 Handler 仍在执行，后续资源访问也应按当前权限拒绝。已经发出的外部请求能否撤回，由相应业务能力决定。

### 9.2 共享与隔离

建议每个 Registry 实例管理一个 EventBus 和一套生效记录，不通过全局可变的“当前包”或“当前会话”传递身份。注册归属应绑定到当前加载任务，或由该任务专用的受限入口保存，避免把一个计划的 `handler_id` 记到另一个计划名下。

不同租户需要不同扩展集合时，应使用独立实例，或由调用方提供经过确认的隔离方案。使用受限能力入口，并不自动隔离 Python 模块全局变量、文件系统、连接池或扩展自己保存的状态。共享资源在并发使用时是否安全，由资源提供方负责。

受限登记入口用于约束正常集成方式，不是隔离恶意代码的沙箱。

### 9.3 容量与使用条件

Package Manager 和 Runtime 应在导入前限制声明大小、包内扩展数量和入口规模，并限制单个扩展能够注册的 Handler 数。调用方负责管理操作的等待数量、业务并发和停止等待时间。具体数值需要根据负载验证，目前没有经过测量的默认保证。

加载耗时、注册数量、资源占用和卸载等待成本需要在集成环境中测量。平台共享资源由能力提供模块管理，Extension 只管理其负责的包实例和资源；本模块不新增模型额度或工具并发预算管理器。

## 10. 运行保障与版本维护

### 10.1 保证范围与成立条件

正确加载依赖依赖检查和 SHA-256 排序。一个包的 Handler 在一次同步注册表更新中一起可见。卸载在仍有调用时直接失败，不删除安装目录。重载用新的模块名加载当前文件，使新 `trigger` 执行新代码，旧快照继续执行旧模块。

本期保证：无依赖时的 SHA-256 顺序在重启后不变；重载热切换该包的 Handler；整包 Handler 同时进入后续快照。本期不保证崩溃后自动恢复执行位置，也不保证扩展在 `register` 里自行创建的后台任务会被 Runtime 关掉。调用方的源码目录不会被删除。

### 10.2 问题发现与定位

建议记录包管理和业务事件的触发入口或发起 Handler，并用根操作标识、父子调用标识和 Plan ID 关联包及扩展 ID、`handler_id`、失败步骤及待清理项。包管理诊断重点区分操作是否执行、注册是否生效和资源是否仍在使用；事件内部执行日志由 Events 维护。

日志使用 Core 既有入口，不写凭据、完整工具输入输出或未经筛选的 manifest 内容。包根等路径按调用方日志策略处理。日志写入失败不意味着注册未发生，不能因此重复注册。指标、告警阈值和审计记录留存方式由后续方案确定。

### 10.3 保留、删除与升级

`schemaVersion` 表示声明格式版本，`package.version` 和依赖范围表示包版本是否兼容，二者不能互相替代。版本号是 `MAJOR.MINOR.PATCH`。版本条件按空白分开，每一段都要满足，例如 `>=1.0.0 <2.0.0`。未在 manifest 规约里定义的字段，解析时拒绝。

重载读取的是安装目录里的当前文件，并用新的模块名执行这份源码。已经在跑的调用仍使用它开始时加载的模块对象，因此新旧代码可以同时存在到旧调用结束。需要退回旧行为时，把安装目录里的文件改回后再次 `reload`，或卸载后安装旧版本。停止 Registry 不删除调用方的源码、业务数据或外部工具结果。

## 11. 完整场景与验证要求

### 11.1 代表性场景

**正常加载并参与 Bash 调用。**业务代码触发 `package.install`；安装 Handler 依次通过包解析、依赖解析、单包计划和计划执行事件推进。执行 Handler 在内部加载、创建实例并注册，保存生效记录后交回 Report。后续业务入口触发 Bash 工具事件，调用方核对扩展参与和实际结果。

**包版本不满足依赖。**调用方传入公共包 1.0.0，企业包却要求至少 1.2.0。Package Manager 检查后不加载企业包，也不自动扫描其他目录寻找替代版本。其他不受这项依赖影响的包可以继续处理。

**包内注册中途失败。** `extension.apply` 已经把部分 Handler 收进 candidate，随后 `register` 抛错。执行 Handler 触发 `extension.rollback`，丢掉整个 candidate，Bus 注册表不变。依赖该包的后续包得到 FAILED。没有依赖关系的包继续。

**重载与根操作协调。** `package.reload` 触发 `extension.plan` 和 `extension.apply`，不先触发卸载。依赖方仍接受新版本时，一次替换该包的 Handler。准备失败时注册表不变。

**重载一个仍被依赖的包。** 企业包要求公共包 `>=1.0.0 <2.0.0`。公共包重载到 1.4.0 时继续。重载到 2.0.0 时返回 `EXTENSION_DEPENDENT_INCOMPATIBLE`，公共包当前 Handler 保持不变。

**事件报错，但包可能已经生效。**安装事件的 Core Handler 已执行完计划，外层可信 Handler 在处理返回结果时抛错。Registry 不能据此判断“尚未安装”。调用方触发 `package.query` 核对生效记录，再决定是否只需重新取得报告，避免再次注册。

**解析事件被提前返回。**`package.parse` 返回但没有满足契约的候选包结果。安装 Handler 核对目标与完成依据后停止对应包，不触发依赖解析、计划或执行。不能把任意正常返回都视为解析完成，替代结果的允许范围由 Q06 细化。

**未显式触发就没有下一项包操作。**初始化、注册变化、某个业务事件返回或异常，都不会让 EventBus 自行安排安装、解析、重试或清理。下一项包操作必须有明确的业务代码或 Handler 调用点。

**失败后的回滚有明确发起者。**扩展注册是 `extension.apply` 的内部步骤。该步骤失败后，由执行 Handler 保存事实并显式触发 `extension.rollback`；即使原执行调用已经结束，受控业务入口也可在查询后请求继续回滚。EventBus 不因异常自行补发回滚事件。

**卸载与旧业务调用同时发生。**调用 A 已开始使用企业包。注销后，A 仍可能继续使用包资源。Runtime 需要确认 A 及相关能力使用均已结束后才清理资源；等待超时则保留资源并报告未完成，不能先删除包目录。

**重载一个仍被依赖的包。**企业包仍依赖公共包时，直接重载公共包会在卸载检查阶段被拒绝。调用方需要先明确处理范围，停止受影响的依赖方，再按确认的依赖顺序重新安装。公共包的新安装失败时，不自动恢复旧实例。

**停止期间仍有子调用。**调用方关闭外部新入口后，已接收的 Handler 仍可能需要发起必要子操作。停止流程要保留受控的执行和清理范围；Runtime 确认相关调用和能力使用都结束后才释放资源，无法确认时保留记录和资源。

### 11.2 架构要求与验证归属

| 主题 | 需要证明什么 | 设计位置 | 后续验证归属 |
| --- | --- | --- | --- |
| 来源与声明 | 路径解析、同 ID 冲突和依赖检查有明确结果；拒绝无效版本、入口和运行域，入口路径不超出选定包根 | 第 4.2 节 | Extension 的 manifest 与来源特性方案 |
| 统一事件入口与粒度 | 9 类目标事件均有独立入口与完成依据；内部检查、加载登记、等待释放和文件清理归所属事件，不出现绕行或无理由的额外事件 | 第 4.1、5 节 | Extension 事件契约与全过程集成验证 |
| 包流程编排 | 包管理及真实业务事件有明确业务或 Handler 触发点；EventBus 不编排下一项包步骤 | 第 2.2、4.5、6、11.1 节 | Extension 与 Events 协作验证 |
| 父子操作协作 | 子操作保持可信操作关联，不扩大权限、不重复争用根准入；未执行、取消或无效结果不能误推进 | 第 4.1.2、6、9 节 | Extension 管理流程与故障验证 |
| 两级定序 | 包与包内扩展分别检查依赖；被依赖方先加载；并列及 hash 排序规则明确 | 第 4.2～4.3 节 | Package Manager / Orchestrator 特性方案 |
| 注册归属 | 标识不会丢失，包信息可信，个人扩展只能注册 USER，同一函数的多条注册可分别处理 | 第 4.4 节 | Runtime 与 Events 协作特性方案 |
| 回滚与批量失败 | 逆序撤销本计划注册，保留清理失败项，依赖方跳过，独立包继续 | 第 5.2.1、8 节 | Runtime 与管理流程特性方案 |
| 注册可见性 | 一个包的 Handler 在一次同步更新后一起出现在后续快照里；更新前的快照仍是旧的整套 | 第 5.2.1 节 | 调用方与 Extension 集成验证 |
| 包关联与能力接入 | Handler 使用的包关联信息与 Runtime 核实的来源一致；平台能力通过既有公共接口接入，不因子操作丢失或扩大授权 | 第 4.4～4.5、9 节 | Runtime、Events 与能力提供方集成验证 |
| 卸载与重载 | 仍被依赖的目标不能直接卸载，相关使用结束后才清理，卸载失败不继续安装 | 第 5.2.2～5.2.3 节 | Runtime、Events 与调用方生命周期特性方案 |
| 失败与重试 | Core Handler 成功后外层失败、取消、超时和文件残留，不导致重复注册或误删资源 | 第 6～8 节 | 管理流程的故障注入验证 |
| Python 加载 | 相对导入、模块重名、缓存、新版本代码和资源退出符合约定 | 第 4.4、10.3 节 | Runtime 特性方案 |
| Core 业务协作 | 实际工具入口接入事件，扩展参与情况和业务结果可核对，资源权限符合约定 | 第 5.3、11.1 节 | Extension、Events 与 Core 工具模块集成验证 |

这些是需要完成的验证，当前还没有执行证据。具体步骤、环境、模块版本、故障注入方式和实际结果写入后续特性方案，开发安排由统一计划维护。文档结构和链接检查不能代替执行测试或集成验收。

## 12. 设计选择与待确认事项

### 12.1 重要设计选择及理由

| 设计问题 | 当前方案及理由 | 代价与限制 | 状态 |
| --- | --- | --- | --- |
| 管理逻辑放在哪里 | 在 `core/extension` 维护，通过 Events 公共接口完成注册和执行协作 | Events 不反向依赖包管理或业务实现 | 目标设计 |
| 操作如何执行 | 管理请求和独立可复用操作经过 9 类事件；内部步骤由所属 Core Handler 使用模块能力完成 | 每类事件要能说明独立意义、结果、模块依赖和失败责任 | 本方案的目标事件划分；公开标识及契约待规约化 |
| Core Handler 如何使用模块 | 处理器按需使用 Package Manager、Extension Orchestrator 和 Extension Runtime 的能力，一个处理器可以使用多个模块；独立操作仍经过对应事件 | 模块分工与事件处理职责需要分别明确，不按一个处理器对应一个模块划分 | 与 overview 第 2.1 节一致 |
| 谁编排包流程 | 业务代码及 Handler 显式触发包管理或业务事件，Registry 提供便捷方法；EventBus 不决定下一项包操作 | 包流程与通用事件服务的责任需要分开维护 | 基于 Events 的设计边界 |
| 谁确定加载顺序 | Package Manager 排列包，Orchestrator 排列当前包内扩展 | 不跨包混合排列扩展单元 | 目标设计 |
| 怎样让扩展生效 | 加载入口、调用工厂，用受限入口收集 USER 或 BUILTIN，再一次写入 Bus 注册表 | 导入产生的外部影响不能靠丢掉 candidate 撤销 | 目标设计 |
| 失败时如何处理 | 尚未写入 Bus 的 candidate 整包丢掉 | 已经生效的其他包保持不变 | 目标设计 |
| 怎样重载 | 依赖方仍接受新版本时，用新模块名加载并一次替换该包的 Handler | 旧快照继续执行旧模块，直到那些调用结束 | 目标设计 |
| 管理操作怎样并发 | 建议在单个 Registry 内按顺序协调 | 不解决跨进程竞争，也需避免嵌套管理调用产生冲突 | 补充建议，Q06 确认 |
| 何时删除安装目录 | 卸载时没有在途调用，才删除安装目录里的该版本。调用方的源码目录和 zip 不删除 | 有在途调用时卸载失败，目录保留 | 目标设计 |

本表记录当前方案和理由，不表示尚未讨论的方案已经被评审或否决。正式契约确认后写入 spec，具体实现取舍和验证记录写入 feature。

### 12.2 未验证的前提与待确认问题

#### 12.2.1 尚未验证的重要前提

这些是设计成立所需、但目前还缺少实现或验证证据的条件。

| 前提 | 需要补充的验证 | 不成立时会怎样 | 确认责任 |
| --- | --- | --- | --- |
| Events 能提供所需的注册、调用和使用结束确认接口 | 包来源关联、注册与注销、卸载等待的集成验证 | 无法安全卸载，也无法准确判断还有哪些残留 | Events 与 Runtime 维护者 |
| Core 调用入口能把一次 `trigger` 的注册表快照交给执行器 | 快照在第一次 await 前固定，并且能接受一个包的整套 Handler 一次写入 | 后续调用可能看见半个包 | Extension 与 Events 维护者 |
| Python 扩展按新模块名加载 | 同一次进程里，旧调用仍执行旧模块，新 prepare 执行当前文件 | 新旧代码无法区分 | Runtime 维护者 |
| 从校验到加载期间，包内容保持一致 | 托管副本、不可变包根或等效机制及版本检查 | 校验的声明可能与真正执行的代码不一致 | Package Manager、Runtime 与调用方维护者 |

#### 12.2.2 尚未确定的设计问题

下列问题需要进一步选择方案并形成契约。确认之前，正文中的建议不能作为已实现能力使用。

| 编号与问题 | 还需要明确什么 | 责任与维护位置 | 需要达到的结果 |
| --- | --- | --- | --- |
| Q05 定向操作与依赖保护 | 只指定 `extension_id` 时要一并处理哪些依赖方，以及部分卸载的范围 | Orchestrator / Package Manager；生命周期 spec | 不留下仍依赖已卸载目标的扩展 |
| Q06 管理事件与并发 | 根操作准入、停止清理及递归限制 | Extension、Events、调用方；控制事件 / 生命周期 spec | 独立操作仍走 9 类事件；根操作不等待自身 |
| Q08 最终公开接口 | EventBus 落地后，Registry 便捷方法与 Plan 如何交给 `extension.apply` | Extension 与 Events；接口 spec | 执行只接受可核对的计划；Ops 与注册表沿用 Events |

问题解决后，应将结果写回相关正文、图表、概览和对应规约，不能只在本表标为完成。负责人认领、排期、PR 和验证进展由 [开发计划](extension-development-plan.md) 统一维护；本表只说明还缺哪些设计结论及其影响。

### 12.3 相关文档

| 文档 | 阅读用途 |
| --- | --- |
| [Extension 设计概览](extension-overview.md) | 了解整体定位、主要流程和能力开放条件 |
| [Events 详细架构](../../../events/docs/designs/events-architecture.md) | 查阅公共事件接口、扩展接入与生命周期约定；事件机制的详细设计在该文档维护 |
| [Core 公共规范](../../../AGENTS.md) | 查阅模板使用、规约与特性方案分工，以及交付检查要求 |

Extension 的规约在 `docs/specs/`，特性说明在 `docs/features/`。Registry 与 EventBus 的对接还没有实现，见特性文档里的实现范围。
