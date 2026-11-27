---
id: S_02_session-conversation-store
title: Session 共享对话存储与执行状态隔离规约
type: S
author: Yanqing-Shi
author_email: toddsyq@gmail.com
modules:
  - openjiuwen/core/context_engine
  - openjiuwen/core/session
files:
  - openjiuwen/core/context_engine/schema/conversation.py
  - openjiuwen/core/context_engine/context_engine.py
  - openjiuwen/core/context_engine/context/context.py
  - openjiuwen/core/context_engine/conversation.py
  - openjiuwen/core/context_engine/processor/base.py
  - openjiuwen/core/context_engine/processor/forked/base.py
  - openjiuwen/core/context_engine/processor/offloader/tool_result_budget_processor.py
  - openjiuwen/core/session/agent.py
  - openjiuwen/core/session/internal/agent.py
  - openjiuwen/core/session/checkpointer/base.py
  - openjiuwen/core/session/checkpointer/conversation_store.py
  - openjiuwen/core/session/checkpointer/conversation_state.py
  - openjiuwen/core/session/checkpointer/inmemory.py
  - openjiuwen/core/session/checkpointer/persistence.py
  - openjiuwen/core/single_agent/agents/react_agent.py
---

# Session 共享对话存储与执行状态隔离规约

## 1. 范围与前提

本规约定义显式绑定的对话数据和执行者 checkpoint 的边界。当前实现覆盖
`InMemoryCheckpointer` 与 `PersistenceCheckpointer`；后者复用配置好的 `BaseKVStore`。
宿主授权、租户存储作用域和 Session 操作锁由宿主提供，store 不重复取锁、不提供窗口 CAS 或运行 lease。
调用者须在同一持锁操作内完成 read、执行和最终 commit。

ContextEngine 已通过显式 Session binding 装载并保存共享窗口。绑定本身不创建或复制对话。
Swarm 主绑定注入与生命周期接线仍属后续阶段。

## 2. 数据与接口

四个类型从 `openjiuwen.core.context_engine` 导出：

| 类型 | 当前契约 |
| --- | --- |
| `ConversationKey(session_id, conversation_id)` | 两个 ID 必须显式提供且为非空字符串，保持原始字面量；无隐式 main，不含 mode/profile/card。 |
| `ConversationSnapshot` | keyword-only；schema_version=1、messages、offload_messages；保存结构化 BaseMessage 及 metadata，不包含模型、工具或系统提示配置。 |
| `SessionConversationStore` | async create/read/commit Protocol。 |
| `ConversationBinding(key, store)` | 不可重定向的运行期引用；不进入 Session env/state 或 checkpoint。 |

Snapshot 允许执行途中未完成的工具调用。消息必须为 BaseMessage 列表；内存 offload handle 必须有对应 buffer。
文件型 offload 的引用可以存储，但路径访问、可移植性和模型输入合法性不由 store 判断。
冻结 dataclass 不冻结嵌套容器；store 的读写边界提供序列化隔离，修改返回对象不改变已保存记录。
`to_state()` 校验后返回深拷贝；`to_state(copy=False)` 只校验并返回 snapshot 自身的列表，
供立即序列化或独占该 snapshot 的调用方使用。KV store 的写入与读取都走后者：序列化本身就是隔离，
校验与序列化之间没有 await，不再额外拷贝整个窗口。

| 方法 | 行为 |
| --- | --- |
| `create(key)` | 幂等建立空记录；已有内容直接读取，损坏或未知 schema 也不能清空。仅由宿主在新对话创建边界调用。可能并发调用：只靠 KV `exclusive_set` 的原子性保证后到者不覆盖已有内容；数据库后端以“不存在才插入”的单条语句实现（PostgreSQL / SQLite `ON CONFLICT DO NOTHING`，MySQL `INSERT IGNORE`）。 |
| `read(key)` | 每次读取当前记录，无本地窗口缓存；不存在抛 CONTEXT_CONVERSATION_NOT_FOUND。 |
| `commit(key, snapshot)` | 一次 KV set 替换整份消息与 offload 数据；记录不存在时拒绝，不隐式 upsert。提交前只做存在性检查，不再读取并解码已有记录：调用方是在 Session 操作锁内、从该记录成功装载的窗口。损坏记录由 `read` / `create` 报告，装载失败的窗口本身不能提交。 |

存储异常为 CONTEXT_EXECUTION_ERROR，保留 cause；损坏编码、非法消息或 schema 为 CONTEXT_MESSAGE_INVALID。
存储异常后不自动重试；写入可能成功而确认失败时，由宿主保持操作边界并判断结果。

## 3. Provider 与持久化边界

`checkpointer.conversation_store()` 同步返回同一 provider 持有的 store。
内存 checkpointer 使用独立 InMemoryKVStore；持久化 checkpointer 使用已有 KV 实例、连接及配置。
记录复用 pickle codec，以 `codec:base64(payload)` 文本封装一次写入；当前 schema 只接受三个指定字段。

底层 key 为 `conversation/<encoded session_id>/<encoded conversation_id>`，各 ID 分别百分号编码。
原始冒号、斜杠和百分号不会混淆分量。key 中不含字面冒号，避免与任意 `<session_id>:` checkpoint 清理前缀相交。
执行者或整个 checkpoint Session 的 `release` 都不删除共享对话；对话生命周期接口留待宿主后续接入。

新增的 Checkpointer 方法不是 abstractmethod；未支持的实现（包括 Noop 和 Redis 扩展）调用时明确报
CHECKPOINTER_CONFIG_ERROR。第三方 checkpoint 若接收 binding，须实现同样的保存/恢复投影；本阶段未为其自动接入。

## 4. Session 绑定与 checkpoint 投影

`Session` 与 `create_agent_session` 新增 keyword-only `conversation_binding`，绑定默认 context。
`bind_conversation(binding, *, context_id="default_context_id")` 可在 pre_run 前显式绑定其他 context。
相同 key、相同 store 对象的重复绑定幂等；更换 key/store 或 pre_run 后增加绑定明确失败。
`get_conversation_binding(context_id)` 返回绑定或 None。context ID 中的点按 ContextEngine 既有规则替换成下划线，
该规范化不改变 ConversationKey。子运行的 Session ID 不必等于产品 ConversationKey 的 session_id。

两种已支持 checkpointer 的 AgentStorage 在保存和恢复时，都从 `global_state.context` 排除已绑定的 context ID。
投影不原地改写存量 checkpoint 或 live state；其他 context、global state 和 agent state 保持原有恢复行为。
无绑定时沿用完整旧快照。绑定但共享记录缺失时，不从旧 context 自动建档、回填或迁移。

`conversation_snapshot` 只作为装载边界的纯校验器保留；复制式交接的 `export_conversation` / `replace_conversation` 已移除，
对话不再通过复制或 receipt 在执行者之间转移。
旧 Session 历史处理不在本契约范围。

## 5. ContextEngine 读写与缓存

- `create_context` 对已绑定 context 每次都读取 store，包括缓存命中；忽略 checkpoint context 与 history_messages，
  不回填、不自动 create。共享记录缺失/读取失败直接失败，不沿用旧缓存。曾绑定的缓存不能通过未绑定 Session 降级加载。
- 引擎缓存使用 `(executor session_id, normalized context_id)` 二元组，分量中的下划线不会发生字符串拼接碰撞。
  绑定后仍使用当前引擎的 processors、模型预算、workspace、系统提示和工具；共享数据不赋予工具权限。
- 恢复/切换边界要求可继续执行的完整工具序列，复用纯快照校验器；未闭合调用明确失败，不被当成空闲窗口。
  装载对读到的记录只拷贝一次（`conversation_snapshot` 内），窗口不持有 store 返回的对象；
  写回不拷贝，由 store 序列化。
  同一次已装载的运行中，内存工作副本可以先持有调用、再追加对应结果；写回只发生在下述两个时点，
  所以除已登记的审批中断外，记录中的工具调用都有对应结果。
- 带待审批中断恢复时（`ReActAgent`）：已绑定窗口在共享记录装载成功之后才清除中断状态，装载失败时该审批仍可回答；
  未绑定窗口保持原有顺序，先清除再装载。
- 共享装载后清除本地 usage 报告/测量缓存，并使旧 provider usage 失效，按当前 counter/model 重算。
  保留该执行者上一次实际 LLM 请求窗口，用于检测其他执行者压缩/修改共享前缀后的 KV 失效范围。
  缓存实例的最后访问时间同样保留在本执行者内存，避免每次刷新使 idle TTL 重新起算；该时间不写入共享 record。
- 写回时点固定：每次模型请求之前（`get_context_window`，含本次窗口处理和压缩的结果）、正常结束的主动压缩，
  以及运行结束时的最终保存（`save_contexts` / `save_conversation`）；均在既有 processor 锁内提交已变化的共享数据。
  `add_messages` 只修改内存工作副本，不按每条消息写回。进程在两次模型请求之间异常退出时，
  上一次请求之后的 assistant 回复和工具结果不在记录中，已执行的工具可能被模型再次调用，这是已接受的代价。
  复用本地 message revision 记录成功保存的位置：只读窗口、无变化压缩和重复 flush 不全量重写。
  offload buffer 的修改、清空和恢复也计入本地变更；提交中发生的新修改不能误记为已保存。
  该位置不进入 checkpoint/store，不是窗口 CAS、版本协议或第二份消息权威。
  活跃压缩期间按原有语义入队的追加由当前持锁压缩者一起保存；进入存储提交后关闭该旁路，后续追加等待现有锁，
  并由下一个写回时点保存。
  不新增窗口锁，不重复取得宿主 Session 操作锁，不启动脱离操作生命周期的后台写入任务。
- `add_messages` 与同步 `set_messages/pop_messages/offload_messages` 的修改都暂存内存，由下一次模型请求或显式
  `await engine.save_contexts(session)` / `await context.save_conversation()` 保存。运行之外修改窗口的宿主操作
  （独立 rewind、审批批次清理等）须持锁并在返回前显式保存。
  `clear_messages` 空闲时直接保存，processor 已持锁时由该操作收尾保存。
- `save_contexts` 保存共享 context 到 store，Session 中只投影未绑定 context；保存部分共享窗口时保留尚未缓存的局部 context。
  缓存必须已通过当前 Session 装载相同 key/store 后才能保存，避免旧 Session 引用回写。
- 宿主决定式压缩（`compact_context`、自动桥接与 precompute，见 `compaction.py`）：草稿是推测性的内存副本，
  不读写共享记录；被拒绝、过期或 precompute 的候选不改变记录。只有安装后的结果由该操作收尾保存一次。
  草稿保留绑定本身：processor 据此把 offload 内容留在记录内（`in_memory`），不向 workspace 写文件，
  安装后的窗口因而能通过“只接受 provider 自有 offload”的写回校验。
  这类操作全程持有 processor 锁，收尾以 `save_contexts(..., processor_lock_held=True)` /
  `save_conversation(processor_lock_held=True)` 在已持有的锁内直接提交，不重复取锁（该锁不可重入）。
  收尾的保存与 `session.commit()` 位于 `install_scope()` 内：宿主 checkpointer 在这次 commit 里再次调用
  `save_contexts` 时同样直接提交，不等待自己的调用方；该范围随流程传给其内部启动的任务，范围之外的调用方仍需等锁。
  宿主把该能力接到在线 Session 时须持有宿主 Session 操作锁。

## 6. Offload、异常与兼容边界

已绑定窗口的官方和 forked offload processors 经各自公共 offload 入口使用 `in_memory` 表示；原文 buffer 与占位消息一起持久化到共享 provider，
不依赖执行者私有内存或工作目录文件。Budget/Window processor 的模型可见引用只描述实际存储类型，内存引用不附带虚构文件路径。
未绑定窗口保持既有文件/内存 offload 行为。共享装载或提交中的文件型/未知 offload 引用明确拒绝；不隐式迁移私有文件。

绑定窗口在读取、提交或 processor 操作失败/取消后，进入需刷新状态。后续追加、模型窗口和保存明确失败，直到显式
`create_context` / `load_conversation` 成功读取真实记录。取消继续传播，普通第三方 store 异常转换为框架错误并保留 cause。
确认丢失后不从旧内存重写或重放工具。初始输入保存失败不能进入模型调用。

宿主必须覆盖整个执行和最终保存的操作边界。当前 core 数据通道不证明主 Agent/实际 Leader 的宿主 key 注入、
Swarm 主绑定注入、旧历史、删除/归档/分叉/回退已经接通。

## 7. 依据与验证入口

- 跨仓设计：`jiuwenswarm:D-20261112-0530-session-conversation-window`，本规约落实 I-01/I-02。
- 行为测试：`tests/unit_tests/core/session/checkpointer/test_conversation_store.py`。
- 数据通道与真实 ReAct 请求测试：`tests/unit_tests/core/context_engine/test_shared_conversation.py`。
- 兼容回归：同目录 checkpoint 测试及 `tests/unit_tests/core/context_engine/test_conversation.py`。
