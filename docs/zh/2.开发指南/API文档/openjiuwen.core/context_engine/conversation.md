# 共享对话数据与存储

`openjiuwen.core.context_engine` 导出 `ConversationKey`、`ConversationSnapshot`、
`ConversationBinding` 和 `SessionConversationStore`。

ContextEngine 根据显式 binding 加载和保存共享窗口。调用者须在宿主 Session 操作锁内完成整个执行和最终保存。
Swarm 宿主注入与共享生命周期尚未接线。

## 数据与存储接口

```python
ConversationKey(session_id: str, conversation_id: str)
ConversationSnapshot(*, schema_version: int = 1,
                     messages: list[BaseMessage] = ...,
                     offload_messages: dict[str, list[BaseMessage]] = ...)
ConversationBinding(key: ConversationKey, store: SessionConversationStore)

async def create(key: ConversationKey) -> ConversationSnapshot: ...
async def read(key: ConversationKey) -> ConversationSnapshot: ...
async def commit(key: ConversationKey, snapshot: ConversationSnapshot) -> None: ...
```

两个 ID 都须显式提供；主对话可由宿主选择 `"main"`，子对话应使用独立 ID。
租户隔离来自宿主配置的存储作用域。Snapshot 保存结构化消息、metadata 和 offload buffer，
允许尚未完成的工具调用；不保存模型、工具或系统提示配置。存储层可保留文件引用，但 ContextEngine 的绑定执行
只接受 provider 持有的 offload buffer，无法解析的私有文件引用明确失败。

`create` 仅用于宿主创建新对话：已有记录原样读取，不清空。
`read` 和 `commit` 对缺失记录抛出 `CONTEXT_CONVERSATION_NOT_FOUND`，不会自动导入旧 checkpoint。
`read` 和 `create` 遇到损坏或未知 schema 抛出 `CONTEXT_MESSAGE_INVALID`，provider 失败抛出 `CONTEXT_EXECUTION_ERROR` 并保留 cause。
`commit` 整份替换记录，提交前只确认记录存在，不读取并解码已有内容；调用方应在 Session 操作锁内提交自己成功装载的窗口。
提交失败不自动重试；确认丢失时不能假定写入未发生。

## Provider 与 Session 绑定

`InMemoryCheckpointer.conversation_store()` 和 `PersistenceCheckpointer.conversation_store()`
返回各自持有的 store。持久化实现复用已有 KV provider 和 pickle codec，支持原有 SQLite/shelve 配置。
其他 checkpointer 默认抛出 `CHECKPOINTER_CONFIG_ERROR`，不会因此增加新的抽象方法实现要求。

```python
from openjiuwen.core.context_engine import ConversationBinding, ConversationKey
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointerProvider


async def create_new_session(card):
    checkpointer = await PersistenceCheckpointerProvider().create({
        "db_type": "sqlite", "db_path": "checkpoint.db",
    })
    store = checkpointer.conversation_store()
    key = ConversationKey("product-session", "main")
    await store.create(key)  # 宿主在新 Session 创建边界调用，并提供操作锁。
    return create_agent_session(
        session_id="executor-session", card=card, checkpointer=checkpointer,
        conversation_binding=ConversationBinding(key, store),
    )
```

`Session.bind_conversation(binding, *, context_id="default_context_id")` 须在 `pre_run` 前调用。
构造参数绑定默认 context，也可显式绑定多个 context。context ID 中的点按引擎现有规则规范化为下划线；
ConversationKey 保持原样。相同 key 和 store 的重复绑定幂等，重定向绑定会失败。
`Session.get_conversation_binding(context_id)` 返回绑定或 None。

## ContextEngine 数据通道

在宿主持有 Session 操作锁期间：

```python
from openjiuwen.core.foundation.llm import UserMessage


async def append_input(engine, session):
    context = await engine.create_context(session=session)
    await context.add_messages(UserMessage(content="new input"))
    await engine.save_contexts(session)
    await session.commit()
```

`create_context` 在缓存命中时也重新读取共享记录，忽略旧 checkpoint context 和 `history_messages`，不会自动建空记录。
当前引擎的 processors、模型预算、系统提示和工具保持独立；usage 按当前模型重新计算。

写回共享记录的时点固定为：每次模型请求之前（`get_context_window`，含窗口处理和压缩结果）、主动压缩正常结束，
以及运行结束时的最终保存。`add_messages` 只修改内存工作副本，不按每条消息写回；在运行之外追加消息的宿主须像上例那样显式保存。
进程在两次模型请求之间异常退出时，上一次请求之后的回复和工具结果不在记录中。只读窗口、无变化压缩和重复 flush 不重写未变化记录。
复用既有本地 message revision 追踪消息及 offload 的修改、清空和恢复；成功保存的位置不是持久化版本或 CAS。活跃压缩期间按原有语义入队的输入，由持有既有 processor 锁的
压缩操作收尾保存；提交阶段的新追加等待该锁。`add_messages` 与同步 `set_messages/pop_messages/offload_messages` 都只暂存修改，
须由下一次模型请求或 `await engine.save_contexts(session)` / `await context.save_conversation()` 完成保存，不启动后台写入。

已绑定的 context 不再随执行者 checkpoint 保存或恢复；部分共享保存也保留其他局部 context。
共享 offload 使用 `in_memory` 占位表示，原文 buffer 随 provider 持久化，回收执行者后仍可恢复。
加载边界要求工具调用完整闭合；运行中工作副本可以先持有调用、再追加结果，除已登记的审批中断外，写回的记录中调用都有结果。

读取、提交或 processor 操作失败/取消后，需要成功刷新才能继续构建模型请求或保存；确认丢失不自动重写旧窗口或重放工具。
输入保存失败会阻止模型调用。未绑定路径继续使用原有 checkpoint 和 offload 行为。

`checkpointer.release` 仅清理执行者 checkpoint，不删除共享记录。共享记录的删除、归档、分叉和回退
尚未接入宿主生命周期。旧 Session 历史迁移需另行处理。
