# S_04 办公空间授权、文件读取与文件登记规约

## 元信息

| 项 | 值 |
| --- | --- |
| 类型 | spec |
| 编号 / slug | S_04 / tools-and-workspace |
| 关联模块 | [workspace/access.py](../../workspace/access.py)、[path_policy.py](../../workspace/path_policy.py)、[file_access.py](../../workspace/file_access.py)、[tools/workspace_read.py](../../tools/workspace_read.py)、[execution/share_file.py](../../execution/share_file.py)、[execution/handoff.py](../../execution/handoff.py) |
| 最近一次修订日期 | 2026-10-16 |
| 关联 feature | [F_14](../features/F_14_workspace-file-read.md)、[F_18](../features/F_18_output-handoff.md)（`share_file`）、[F_20](../features/F_20_continuation.md)（续做的登记范围）；文件修改接入见[开发计划](../designs/persistent-agent-development-plan.md) PA-16 |
| 设计依据 | [架构设计：办公空间](../designs/persistent-agent-architecture.md#47-办公空间与知识)、[身份与隔离](../designs/persistent-agent-architecture.md#9-权限隔离与资源限制) |

本规约维护 WorkspaceAccessPort、调用级读取授权、逻辑路径、文件读取结果、私有草稿隔离及 `share_file` 的文件登记。通用副作用和工具事件从本文拆出，由 S_08 唯一维护。

## 1. 范围、术语与适用条件

PA-14 只读组件已实现：复用 Harness ReadFileTool / ListDirTool，并注册受保护的 `read_file` / `list_files`。实际目录部署、业务授权、平台 SDK 与宿主端口实现归 AgentServer；真实宿主联调仍未验证，不能以本地只读组件证明 PA-16 写入或跨进程恢复已开放。

启用条件为 Spec.enabled_features 含 workspace_read、enable_sys_operation=true，且 PersistentPorts.workspace_access 存在并提供异步 resolve_read_scope。上下文及装配阶段均校验，不等到模型读取时才发现缺少依赖。未开启时不添加本读取 Rail 与 `share_file`；普通 DeepAgent 仍遵循通用行为。`share_file`（PA-08）随读取能力装配，不新增能力标记，见第 4.3 节。

公共 [工具](../../../docs/specs/S_05_tools-contract.md)、[安全](../../../docs/specs/S_08_security-engine.md)、[工作空间](../../../docs/specs/S_09_workspace.md)机制继续由 Harness 负责，本模块补充业务身份、私有 Session 路径及来源。Shell、CLI、MCP、写工具和子进程不因注册本读取 Rail 就获得相同的沙箱隔离保证。

DeepAgent 工具路径还须遵守既有 allow / ask / deny 及配置范围；宿主 scope=allowed 本身不表示审批完成或授予扩大后的能力。直接调用读取包装器不能证明已经经过完整的原生权限 Rail 链。

## 2. 数据结构与事实归属

WorkspaceReadScope 为宿主返回的不可变运行对象，不进入可序列化 Spec，不保存 Session、锁或取消状态。定义位置为 [access.py](../../workspace/access.py)。

| 字段 | 语义与约束 |
| --- | --- |
| status | allowed / forbidden / unknown；unknown 不能按允许处理 |
| agent_id、session_id | 当前执行的 Agent / Session 归属 |
| principal_ref | 可选；提供时必须与 ExecutionContext 主体一致 |
| workspace_root、allowed_roots | 实际根与宿主授予的可读根；字符串被规范化为 Path，根目录本身不是授权 |
| private_sessions_root、current_session_root | 可选私有范围根；保护其他 Session 草稿，当前 Session 范围仍由宿主授予 |
| source_ref、policy_revision | 稳定来源及当前执行策略关联，必须非空；策略修订须匹配 ExecutionContext |
| reason | 可选诊断；不能把宿主异常或实际主机路径直接暴露给模型 |

读取成功沿用 Harness 内容 / 列表数据，并附逻辑路径、source_refs、version、status、completeness、next_cursor。version 是本次 stat 元数据观察得到的不透明摘要，可能为空；不是文件内容哈希、不可变快照或防并发变更的凭证。路径和版本只说明此次读取观察，不授予下一次使用权限。

## 3. 不变量

1. **I-1 授权先行：** 每次读取 / 列举先从宿主解析范围，再验证 Agent、Session、可选主体和策略修订。查不清、端口异常或归属错配都不能放行。
2. **I-2 私有范围：** 同 Agent、同项目根或共享工作空间不授予其他 Session 草稿访问权；其他 Session 私有路径即使在共享允许根内仍拒绝。
3. **I-3 路径受控：** 模型只能提供逻辑相对路径；词法边界和规范化真实路径均须检查，符号链接不能逃逸允许根。
4. **I-4 安全结果：** 拒绝先于底层读取，不因错误差异泄露未经授权的目标是否存在；授权成功后才区分 not_found 与后端不可用。
5. **I-5 只读边界：** 读取适配只注册读取与列举，不承担写入、外部回执核对、工具完成判定或发送交接；`share_file` 只把文件登记到本次执行的登记表，不发送、不上传，读授权不代表允许公开。

## 4. 接口契约

### 4.1 `WorkspaceAccessPort.resolve_read_scope(execution) -> WorkspaceReadScope`

由读取适配调用，宿主按当前业务身份返回范围，不执行文件读取。validate_read_scope 核对形态与调用归属；端口失败、错误类型或策略不匹配转为安全 unknown 结果，不直接回传宿主诊断。该逐次文件鉴权不能被扩大为“当前窗口及全部派生知识立即撤权”；知识和 Execution 快照边界见 S_09 / S_03。

### 4.2 `read_file` / `list_files`

工具参数继续引用原 Harness ToolCard；read_file 的路径键为 file_path，list_files 为 path，列举路径缺省为 `.`。包装器在实际委托前将已授权路径替换为底层规范路径，结果中的结构化路径元数据再转换回逻辑路径；invoke 与 stream 使用同一保护路径，stream 交出同一次调用结果。

拒绝 NUL、绝对路径、驱动器 / UNC 路径及 `..` 段；统一斜杠、移除 `.` 段，空路径字符串按根目录 `.` 归一化，不将其写成“所有空路径均拒绝”。先检查词法候选的允许根与私有范围，再检查 realpath 后的相同边界；进入底层工具前检查获授权目标是否存在。

| 结果 | 含义与副作用 |
| --- | --- |
| ok / complete | 读取或列举成功；返回观察到的逻辑路径、来源和可用版本 |
| partial / partial | read_file 提供非零 offset 或任何 limit 时的有界读取；不是历史完整性保证 |
| forbidden | 范围拒绝或路径越界；不执行底层读取 |
| unknown | 授权不确定、归属 / 策略错误或端口异常；不执行底层读取 |
| not_found | 已授权目标不存在 |
| unavailable | 已授权读取后端失败或返回形态非法 |

错误返回 ToolOutput(success=false)，data 只含安全 status，error 为固定安全说明。成功 next_cursor 当前为空；list_files 没有分页协议，保持 complete，不能补写游标翻页或 version_conflict 能力。内容正文并非凭证过滤器，结构化主机路径清理不能被误写为全量正文脱敏保证。

### 4.3 `share_file`

启用 workspace_read 时随读取能力装配（ShareFileRail），未启用时模型看不到该工具。参数为 file_path（必填，逻辑相对路径）、share_mode（必填，upload / attachment）和 caption（可选，最多 4096 字符，随文件公开；空白视为没有）。不提供目标参数，投递目标由程序设为当前回复目标（S_02 第 4.4 节）。

登记时依次检查以下各层，任一不通过即返回失败、不登记：

| 层 | 检查内容 | 来源 |
| --- | --- | --- |
| 授权 | resolve_read_scope 与 validate_read_scope；授权未知、端口异常或身份不符时失败关闭 | 第 4.1 节 |
| 路径 | resolve_read_path，与 read_file 相同的词法与真实路径规则 | 第 4.2 节 |
| 规则 | 权限引擎按“读”检查 file_path（FileGuard 登记 `FileToolSpec("share_file", "file_path", "read")`），只在工具调用时进行 | Harness 安全引擎 |
| 文件 | 目标是普通文件：不跟随末级符号链接、非阻塞打开，打开后核对类型以及设备号与 inode；当前大小不超过上限。只读元数据，不读内容 | handoff.check_shared_file |
| 登记 | 逻辑路径可写入输出事件（最多 1024 字符等，见 S_02）；合并后不超过 64 项 | handoff.merge_registration |

成功结果是固定格式的 JSON：`{"share_file": "registered", "file_path", "share_mode", "caption", "note", "receipt"}`，收尾与确认恢复都只识别这一格式，并按 file_path、share_mode、caption 重新计算 receipt（内容校验值）比对，不一致即不算登记。校验值只是内容校验，不是签名：它能发现保留原校验值的内容改写，但不能证明结果来自本工具，也发现不了同时重算校验值的改写。同一规范化路径与共享方式重复登记只保留一项：说明取最新一次，位置保持首次登记时的位置；64 项上限作用于合并后的集合。失败返回 ToolOutput(success=false)，data 只含 status（forbidden、unknown、not_found、unavailable、not_a_file、too_large、invalid_arguments、too_many_files），说明不含宿主路径或宿主诊断。

收尾准备时（S_02 第 4.4 节）重新取得授权、解析路径，按文件层检查后分块读取，计算 sha256 并累计实际读取的字节数；超过 Spec.share_file_max_bytes 立即失败，不把整个文件加载到内存，也不记录文件内容。该上限默认 32 MiB，是开发占位值，正式值由服务端对齐；取值须大于 0 且不超过保护性硬上限 1 GiB，进入 Spec 指纹。规则层只在调用时检查：收尾不是工具调用，不能发起确认。

`share_file` 在 PA-15 名单中按无外部副作用处理（S_08），不记写意图，照常记工具完成事件。调用时写入的登记表只用于合并与 64 项上限，随 Execution 释放。收尾时先同步实时窗口，只按最终窗口中最后一个结束标记或执行头之后、所有 Rail 处理完成后仍为固定格式的 `share_file` 成功结果确定交出的文件：宿主 Rail 改写或阻断的结果、说明、补位和中断结果都不算。确认恢复时，在原生恢复之前把确认链的有效登记并入登记表，计入上限。停止、崩溃和其他暂停后的继续，由 Agent 决定是否重新登记。

## 5. 读取资源与生命周期

本调用构造独立读取 Rail / 工具实例；Rail init 注册读取工具，uninit 移除本调用相应能力，owner 及实例清理由 S_07 约束。每次读取重新取得宿主范围，不缓存成永久授权。文件后续变化需重新读取和核对，观察版本不阻止并发修改或读取期间的路径 / 文件竞态。

目录部署可有 inbox、work、sessions、knowledge、templates、practices，但名称不是授权机制。当前配置授予的公共文件可共享；会话草稿、私有轨迹、状态和归档的隔离分别由本规约和 S_03 负责，状态后端不能因设置私有目录就放进工具可访问范围。文件成功不表示任务结束或公开到群，结果交接见 S_02。

## 6. 当前限制与替代关系

- PA-16 的 agent-core 文件修改组件已实现，独立写授权端口及精确内容条件在 preflight 中绑定，PA-15 意图记账成功后才委托 Harness 写工具。内容哈希及条件创建冲突由 SysOperation 稳定错误码映射为 version_conflict，不解析本地化错误文本。真实授权端口、provider 和跨仓联调仍待完成；不能承诺跨进程事务、自动接管或恰好一次写入。普通 DeepAgent 不启用 workspace_write 时保留原 Write/Edit 行为，详见 [F_16](../features/F_16_workspace-file-write.md)。
- 当前路径保护不提供完整 OS 沙箱，也不证明 Shell / MCP / 子进程隔离；不因保存脚本或 SOP 自动安装 Skill 或扩大权限。
- 目标分支后续提交已将 WorkspaceAccessPort / WorkspaceReadScope 的外层懒导出映射修正为 workspace.access，并新增写授权端口和范围导出；F_14 保留的导入失败是修正前历史证据。真实宿主能力仍不能只凭 __all__ 声明证明可用。
- 原 S_04 的通用副作用、操作标识、意图与完成规则转由 [S_08](S_08_tool-effects-and-intent.md) 维护；旧租约围栏、外部查询 / 补偿与所有工具统一返回回执的协议不属于现行 PA-14 保证。

## 7. 关联规约与验证索引

S_07 负责能力装配和 owner；S_08 负责读取工具与 `share_file` 的只读分类及完成事件；S_03 负责私有状态和归档；S_09 负责知识资料读取；S_02 负责候选文件的交接格式、身份与投递保证。

| 条款 / 接口 | 检查入口 |
| --- | --- |
| 范围形态、不可变性及身份匹配 | [test_workspace_access.py](../../../../../tests/unit_tests/harness/persistent_agent/test_workspace_access.py) |
| 路径、符号链接及其他 Session 私有范围 | [test_workspace_path_policy.py](../../../../../tests/unit_tests/harness/persistent_agent/test_workspace_path_policy.py) |
| 装配开关与独立读取 Rail | [test_workspace_read_rail.py](../../../../../tests/unit_tests/harness/persistent_agent/test_workspace_read_rail.py) |
| 拒绝前不访问后端、结果与来源版本 | [test_workspace_read_tools.py](../../../../../tests/unit_tests/harness/persistent_agent/test_workspace_read_tools.py) |
| `share_file` 登记检查、登记表、装配与大小上限 | [test_share_file.py](../../../../../tests/unit_tests/harness/persistent_agent/test_share_file.py) |
| 收尾读取、摘要与整批准备 | [test_handoff.py](../../../../../tests/unit_tests/harness/persistent_agent/test_handoff.py) |

这些检查定位 agent-core 组件；外层公开导入、真实宿主范围、OS 级隔离及跨仓联调须另行验证，证据见 F_14 和唯一开发计划。
