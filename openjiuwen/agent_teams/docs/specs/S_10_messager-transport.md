# S_10 Messager 传输层与注册表

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/messager/base.py`、`openjiuwen/agent_teams/messager/messager.py`、`openjiuwen/agent_teams/messager/inprocess.py`、`openjiuwen/agent_teams/messager/pyzmq_backend.py`、`openjiuwen/agent_teams/schema/blueprint.py`（`TransportSpec` / `register_transport` / `_TRANSPORT_REGISTRY`） |
| 最近一次修订 commit | 18823271 |
| 关联 feature | — |

## 范围 / 边界

本规约定义 agent_teams 子系统的**消息传输层**——leader 与 teammate / human-agent
之间所有 pub/sub 与 P2P 通信背后的统一抽象。它管：

- `Messager` 抽象类的方法契约：每个后端必须实现的最小方法集。
- `MessagerHandler` 协议：订阅与 P2P 接收回调的形态。
- `MessagerTransportConfig` / `MessagerPeerConfig`：JSON 安全的装配蓝图字段语义。
- `create_messager(config)` 工厂：从 config 到具体后端实例的**唯一入口**。
- `TransportSpec` 注册表机制：`register_transport(name, cls)` + `TransportSpec.build()`
  这条 spec → 实例的桥；内置类型 `inprocess` / `pyzmq` 的注册时机。
- 内置后端两端的语义差异：进程内直接 handler 调用 vs 跨进程 ZeroMQ 序列化。
- 与 `spawn_mode` 的耦合规则。

不在本规约范围内（落到其它 spec）：

- `EventMessage` / `TeamTopic` 的字段定义与事件分类——见 `schema/events.py` 与
  S_03（coordination protocol）。
- 调用方如何分类 / 派发收到的 EventMessage——见 S_03 与各 manager 的
  `register_handler` 调用栈。
- `StorageSpec` 注册表（虽然机制对称）——见 S_01 与 task DB 相关 spec。
- `spawn_mode` 决策本身——见 S_05。
- HITT 通过 P2P 发送 inbox 消息的高层语义——见 S_07。

## 不变量

抽象与入口：

1. `Messager` 是 `agent_teams` 工具层与具体传输实现之间的**唯一抽象**。task DB、
   coordination loop、spawn manager、stream controller 的所有事件流转都通过
   `Messager` 的 7 个抽象方法完成；任何旁路（直接 `new socket` / 自建
   `asyncio.Queue` / 直接 `import` `_Bus`）都是错的。
2. `create_messager(config)` 是从 `MessagerTransportConfig` 到具体 `Messager`
   实例的**唯一工厂**。`TeamAgent` 装配链不允许直接 `import InProcessMessager`
   或 `PyZmqMessager` 构造实例。
3. `TransportSpec.build()` 是从可序列化 `TransportSpec` 到具体 Config 模型的
   **唯一桥**，并且必须经由 `_TRANSPORT_REGISTRY` 派发；下游再用产出的
   `MessagerTransportConfig` 喂给 `create_messager`。这两步是分离的：
   `TransportSpec.build()` 只产 config，不产 messager 实例。
4. `register_transport(name, cls)` 是注册表的**唯一写入口**。注册的 `cls` 必须是
   `pydantic.BaseModel` 子类，且必须能接受 `model_validate({"backend": name, ...params})`
   构造——`TransportSpec.build()` 强制注入 `backend=self.type`。

序列化与运行时：

5. `MessagerTransportConfig` 与 `MessagerPeerConfig` 是 `pydantic.BaseModel`，
   `model_dump()` 必须保持 JSON 安全——只放 string / number / list / dict /
   None；不放 socket、context、`asyncio` 对象、回调函数。
6. `Messager` 实例是**运行时资源**，不进 spec、不进 `model_dump()`、不跨进程。
   跨进程的只能是 `MessagerTransportConfig`。
7. 注册表是**进程级单例字典**（`_TRANSPORT_REGISTRY`），通过
   `_ensure_builtin_infra_registered()` lazily 填充内置项。后续注册靠调用方
   显式 `register_transport(...)`，没有自动发现机制。

后端语义：

8. **`inprocess` 后端通过共享一个 `_Bus` 单例传递引用**：`EventMessage` 不经过
   任何 `serialize()`，直接以 Python 对象传给订阅者的 handler。这是性能与简单性
   的取舍，也是单测 / `spawn_mode='inprocess'` 唯一可行的传输。
9. **`pyzmq` 后端必经过完整序列化往返**：`publish` 走
   `EventMessage.serialize()` + `pub.send_multipart`；`send` 走
   `model_dump() + json.dumps()`；订阅 / P2P 接收侧通过
   `EventMessage.deserialize()` / `EventMessage.model_validate(json)` 还原。
   两者都经过 `_recipient_id` / topic frame 这层 wire 协议——不允许在
   pyzmq 路径上"塞一个 raw Python 对象偷懒"。
10. **`sender_id` 自动盖章**：两个内置后端在 `publish` 时都会检查 `message`
    是否暴露 `sender_id`，缺省值时用本节点 `node_id` 补齐——目的是让订阅侧
    `_filter_self` 在两种后端下行为一致（leader 不消费自己发出的事件，否则
    `TeamCleanedEvent` 这种会让 leader 把自己拆掉）。
11. **`pyzmq` 后端 `start()` 是幂等且 lazy**：`publish` / `subscribe` /
    `send` / `register_direct_message_handler` 在 `_running=False` 时会先
    `await self.start()`。但**不允许**调用方依赖这一点跳过显式 lifecycle 管理；
    `inprocess` 后端的 `start` / `stop` 是 no-op，行为对齐只是好意，不是契约。

P2P 与 pub/sub 的两条独立通道：

12. **每个 `Messager` 实例同时承载 pub/sub 与 P2P 两条通道**，但寻址语义不同：
    pub/sub 用 `topic_id`（订阅 fan-out），P2P 用 `agent_id`（点对点单播）。
    本节点的 P2P 身份是 `MessagerTransportConfig.node_id`。
13. **每个节点最多注册一个 direct-message handler**：`register_direct_message_handler`
    覆盖式写入；二次调用会替换旧 handler；`unregister_direct_message_handler`
    无参（按节点身份反查）。这避免了 P2P 路由的多 handler 歧义。
14. **`pyzmq` P2P 寻址必须查 `_peer_book`**：发送侧必须在
    `bootstrap_peers` 或 `known_peers` 里能查到目标 `agent_id` 的 `addrs`，
    否则抛 `RuntimeError("Unknown zmq route for recipient '...'")`。
    `inprocess` 后端不需要 peer book，靠共享 `_Bus._p2p` 直接 dispatch。

错误语义：

15. **未注册的 transport type 在 `TransportSpec.build()` 立即抛 `ValueError`**，
    错误消息列出当前已注册类型；不允许默认回退到 `inprocess` 或静默失败。
16. **`create_messager` 收到未知 `backend` 字符串时抛 `ValueError`**，与上一条
    保持双层防御——注册表与工厂都拦得住。
17. **`pyzmq` 后端缺 `pyzmq` 依赖时**，`PyZmqMessager.start()` 的
    `_ensure_zmq()` 抛 `RuntimeError("PyZmqMessagerTransport requires
    optional dependency 'pyzmq'.")`。延迟到 `start()` 是为了允许导入模块本身
    （`pyzmq` 是可选依赖）。
18. **pubsub 地址缺失时**，`_PubSubLayer.start()` 在
    `_require_publish_addr` / `_require_subscribe_addr` 抛 `RuntimeError`；
    `direct_addr` 为空时 P2P ROUTER 不 bind，节点只能作为发送方、不接收 P2P。
19. **handler 异常被吞**：pub/sub 的 `_Bus.publish` 与 `_PubSubLayer._recv_loop`、
    P2P 的 `_handle_request` 都用 `team_logger.error` / `contextlib.suppress` 截住
    handler 抛出的异常，单个订阅者失败不影响其它订阅者也不污染 publish 路径。
    业务侧需要错误可观测性时，**自己**在 handler 内用 `team_logger` 记录或外抛
    给上层重试机制；不要指望传输层把异常返还给 publisher。

与 spawn 的耦合：

20. **`spawn_mode='inprocess'` 强制 inprocess transport**：`TeamAgentSpec` 的
    `_default_transport_for_spawn_mode` validator 在 `transport=None` 且
    `spawn_mode='inprocess'` 时自动注入 `TransportSpec(type='inprocess')`。
    用户**显式**配 `spawn_mode='inprocess'` + `transport=TransportSpec(type='pyzmq')`
    时不会被覆盖，但会跑出"同进程内绕一圈 socket"的退化语义——属于"你想这样
    就这样"，框架不挡。
21. **`spawn_mode='process'` 默认保留 `transport=None`**：强迫调用方显式声明
    跨进程后端（典型 `pyzmq`），避免静默用 inprocess 在子进程里跑——子进程的
    `_Bus` 是另一个进程的副本，永远收不到 leader 发的消息。

## 接口契约

### `Messager` 抽象类（`messager/messager.py`）

7 个 `abstractmethod`，所有后端必须实现：

```python
class Messager(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def publish(self, topic_id: str, message: EventMessage) -> None: ...

    @abstractmethod
    async def subscribe(self, topic_id: str, handler: MessagerHandler) -> None: ...

    @abstractmethod
    async def unsubscribe(self, topic_id: str) -> None: ...

    @abstractmethod
    async def send(self, agent_id: str, message: EventMessage) -> None: ...

    @abstractmethod
    async def register_direct_message_handler(self, handler: MessagerHandler) -> None: ...

    @abstractmethod
    async def unregister_direct_message_handler(self) -> None: ...
```

语义：

- `start` / `stop`：lifecycle 钩子。`inprocess` 是 no-op；`pyzmq` 在
  `start` 中创建 ZeroMQ context、bind ROUTER、起 recv 协程。两者都允许重复调用
  （`_running` 守卫）。
- `publish(topic_id, message)`：fan-out 给该 topic 当前所有订阅者。`topic_id`
  约定经由 `TeamTopic.build(session_id, team_name)` 构造，传输层不做格式校验。
- `subscribe(topic_id, handler)` / `unsubscribe(topic_id)`：本地节点对该
  topic 注册 / 注销 handler。同一 topic 二次 subscribe 会**覆盖**前一个
  handler（两个内置后端实现都是 dict 写入，没有 multi-handler 列表）。
- `send(agent_id, message)`：点对点单播。`pyzmq` 走 DEALER → ROUTER 并等
  ACK；`inprocess` 直接调被叫节点的 P2P handler。无 ACK 协议暴露给上层——上层
  靠 `request_timeout` 即可。
- `register_direct_message_handler(handler)`：把本节点 `node_id` 与一个
  接收回调关联。后续别的节点 `send(self.node_id, msg)` 时会触发 handler。

### `MessagerHandler` 协议（`messager/messager.py`）

```python
MessagerHandler = Callable[[EventMessage], Awaitable[None]]
```

- 永远是 `async`。同步回调不允许。
- 入参是已经反序列化好的 `EventMessage`（pyzmq 路径会先 `model_validate` /
  `deserialize`，inprocess 路径直接传引用）。
- 返回值忽略；handler 抛出的异常被传输层截住（见不变量 19）。

### `MessagerPeerConfig`（`messager/base.py`）

```python
class MessagerPeerConfig(BaseModel):
    agent_id: str
    peer_id: Optional[str] = None
    addrs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
```

- `agent_id`：peer 的逻辑身份；P2P 寻址用。
- `peer_id`：可选，当前为预留字段（pyzmq P2P 走 `agent_id` → `addrs[0]`）。
- `addrs`：传输层物理地址列表；`pyzmq` 取 `addrs[0]` 作为 DEALER 连接目标。
- `metadata`：后端自由扩展。inprocess 全部忽略；pyzmq 仅在 `MessagerTransportConfig.metadata`
  级别用 `pubsub_bind` 决定是否启 XPUB/XSUB proxy。

### `MessagerTransportConfig`（`messager/base.py`）

```python
class MessagerTransportConfig(BaseModel):
    backend: str = "inprocess"
    team_name: str = "default"
    node_id: Optional[str] = None
    direct_addr: Optional[str] = None
    pubsub_publish_addr: Optional[str] = None
    pubsub_subscribe_addr: Optional[str] = None
    listen_addrs: list[str] = Field(default_factory=list)
    bootstrap_peers: list[MessagerPeerConfig] = Field(default_factory=list)
    known_peers: list[MessagerPeerConfig] = Field(default_factory=list)
    request_timeout: float = 10.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def broadcast_topic(self) -> str: ...
```

字段语义：

- `backend`：注册表 key，`inprocess` / `pyzmq` 或调用方注册的自定义类型。
  与 `TransportSpec.type` 一一对应——`TransportSpec.build()` 强制覆写此字段。
- `team_name`：本节点所属团队名；用于 `broadcast_topic()` 拼 `team:{name}:broadcast`。
- `node_id`：本节点逻辑身份。pyzmq 用作 DEALER `IDENTITY` 与 P2P 接收侧
  `_recipient_id` 自查；inprocess 用作 `_Bus` 订阅 / P2P 注册的 key。
  **缺省 `None` 时退化为空串 `""`，自动 publish 时只能 `node_id` 派生
  `sender_id`，所以生产环境必须显式赋值**。
- `direct_addr`：本节点 P2P ROUTER bind 地址。pyzmq 必填；inprocess 忽略。
- `pubsub_publish_addr` / `pubsub_subscribe_addr`：pubsub 通道地址对。
  pyzmq 必填，否则 `_PubSubLayer.start` 抛 `RuntimeError`。
- `listen_addrs`：预留字段，当前后端未消费。
- `bootstrap_peers` / `known_peers`：pyzmq P2P peer book 初始化集合，二者
  合并填入 `_P2PLayer._peer_book`；后续运行时调用 `register_peer` 增量补。
  inprocess 全部忽略——它的 P2P 寻址是 `_Bus._p2p` 全局字典。
- `request_timeout`：pyzmq P2P `dealer.recv()` 等 ACK 超时秒数；
  inprocess 忽略。
- `metadata`：后端扩展位。pyzmq 当前只识别 `metadata["pubsub_bind"]: bool`
  ——`True` 时启 XPUB/XSUB broker proxy。
- `broadcast_topic()`：返回 `f"team:{team_name}:broadcast"`，作为团队级
  广播 topic 的便利构造器。

### `create_messager` 工厂（`messager/base.py`）

```python
def create_messager(config: MessagerTransportConfig) -> Messager
```

- 入参必须是已构造的 `MessagerTransportConfig`（典型来自 `TransportSpec.build()`）。
- 当前实现是 `if/elif` 硬派发到内置后端。**这与 `TransportSpec.build()` 的注册表
  派发是分离的两层**：注册表负责 type → Config 类，工厂负责 config → 实例。
  注册自定义后端的调用方需要负责自己 import / 实例化具体 `Messager` 类——
  `create_messager` 只识别 `inprocess` / `pyzmq` 这两个内置 backend 字符串，
  其它一律抛 `ValueError(f"Unsupported messager backend: {backend}")`。
- 不接收除 `config` 外的位置或关键字参数；新参数走 `MessagerTransportConfig`
  字段或 `metadata` 扩展位。

### `TransportSpec` 注册表（`schema/blueprint.py`）

```python
def register_transport(name: str, cls: type[BaseModel]) -> None: ...

class TransportSpec(BaseModel):
    type: str
    params: dict[str, Any] = {}

    def build(self) -> BaseModel:
        # 1. _ensure_builtin_infra_registered() lazily 注册 inprocess / pyzmq
        # 2. _TRANSPORT_REGISTRY[self.type] 取 Config 类，未知 type 抛 ValueError
        # 3. config_cls.model_validate({"backend": self.type, **self.params})
```

约束：

- `register_transport(name, cls)`：`cls` 必须是 `pydantic.BaseModel` 子类，且必须
  能消费 `{"backend": name, ...}` 字典。内置 `inprocess` / `pyzmq` 共用同一个
  `MessagerTransportConfig`——backend 字段决定语义，不需要为新 backend 拆 Config 类
  （除非新 backend 引入了 `MessagerTransportConfig` 装不下的字段）。
- 注册时机：`_ensure_builtin_infra_registered` 是**幂等的 lazy 初始化**，第一次
  `TransportSpec.build()` 时填内置项；调用方注册的自定义类型可以在任何时刻
  `register_transport`，但必须在 `build()` 被调用前完成。
- 注册表无去重：重复 `register_transport(name, cls)` 静默覆盖前者——这是有意的
  调试便利，但不要依赖它做"运行时换后端"。

### `PyZmqMessager` 额外公共方法

```python
@property
def local_peer(self) -> MessagerPeerConfig: ...

def register_peer(self, peer: MessagerPeerConfig) -> None: ...
```

- `local_peer`：返回本节点的 peer 描述（`agent_id=node_id`、`addrs=[direct_addr]`），
  方便调用方把 leader 的地址广播给即将拉起的 teammate 进程。
- `register_peer(peer)`：增量把 peer 写入 `_P2PLayer._peer_book`，配合
  pyzmq P2P 寻址不变量（不变量 14）。

`InProcessMessager` 不提供这两个方法——它没有 peer book 的概念。

## 数据结构

### 注册表（`schema/blueprint.py`）

```python
_TRANSPORT_REGISTRY: dict[str, type[BaseModel]] = {}
```

- 进程级 mutable dict。键：`name`（与 `TransportSpec.type` 对应）；
  值：Config `BaseModel` 子类。
- lazy 初始化时填入：
  - `"inprocess" → MessagerTransportConfig`
  - `"pyzmq"     → MessagerTransportConfig`
- 调用方 `register_transport("custom", MyTransportConfig)` 后，
  `_TRANSPORT_REGISTRY["custom"] = MyTransportConfig`。

### `_Bus`（`messager/inprocess.py`）

```python
class _Bus:
    _topic_subs: dict[str, dict[str, MessagerHandler]]   # topic → agent_id → handler
    _p2p: dict[str, MessagerHandler]                      # agent_id → handler
```

- 进程级 singleton（模块级 `_bus`）；`_get_bus()` lazy 构造。
- 测试夹具通过 `cleanup_inprocess_bus()` 重置：清空两张字典并把 `_bus` 设回 `None`，
  保证 test isolation。
- 双层 dict 设计的目的：每个 topic 内部用 `agent_id` 做 key 而非纯 list，
  让同一个节点对同一 topic 的二次 subscribe 自然覆盖、unsubscribe 单次 pop
  即可——避免 list 扫描。

### `_P2PLayer`（`messager/pyzmq_backend.py`）

```python
class _P2PLayer:
    _ctx: zmq.asyncio.Context
    _router: zmq.asyncio.Socket           # ROUTER bound to direct_addr
    _router_task: asyncio.Task            # _recv_loop 协程
    _running: bool
    _peer_book: dict[str, MessagerPeerConfig]   # agent_id → peer
    _handlers: dict[str, MessagerHandler]       # agent_id → P2P handler
```

生命周期：`start(ctx)` bind ROUTER + spawn `_recv_loop`；
`_recv_loop` 反复 `recv_multipart` 解析 `_recipient_id` 派给本地 handler；
每个 `send` 临时建一个 DEALER socket，发完即关——无连接池。
`stop()` 取消 recv 协程、`router.close(linger=0)`。

### `_PubSubLayer`（`messager/pyzmq_backend.py`）

```python
class _PubSubLayer:
    _pub: zmq.asyncio.Socket              # PUB connected to publish_addr
    _sub: zmq.asyncio.Socket              # SUB connected to subscribe_addr
    _xpub: zmq.asyncio.Socket             # 可选 broker proxy
    _xsub: zmq.asyncio.Socket
    _proxy_task: asyncio.Task             # XPUB/XSUB proxy 协程
    _sub_task: asyncio.Task               # _recv_loop 协程
    _running: bool
    _subscriptions: dict[str, SubscriptionHandle]   # subscription_id → handle
    _handlers: dict[str, MessagerHandler]           # topic → handler
    _seen_ids: set[str]                              # 预留：dedup 暂未启用
```

- 每个节点本地 PUB/SUB 都连到约定地址。`metadata["pubsub_bind"]=True` 的节点
  额外起 XPUB/XSUB proxy 当 broker——多节点架构下选一个节点扮演 broker 即可。
- `_handlers` 是 `topic → handler` 的扁平字典，不是 `topic → list[handler]`：
  与 `inprocess` 路径行为对齐，二次 subscribe 覆盖前者。
- `SubscriptionHandle` 当前主要供 `find_handle_by_topic` 反查与
  `unsubscribe(handle)` 内部使用；`PyZmqMessager.unsubscribe(topic_id)` 这层
  对外接口屏蔽了 handle，仅暴露 topic 字符串。

### `EventMessage` 序列化路径（与本 spec 间接相关）

- `EventMessage.serialize()`：pubsub 路径用，输出 `bytes`。
- `EventMessage.deserialize(bytes)`：pubsub 接收侧用。
- `model_dump()` + `json.dumps`：P2P 路径用。inbound 侧 `model_validate(json)` 还原。
- `sender_id`：参与节点身份与 self-filter 不变量；publish 时缺省自动盖章为
  `node_id`（不变量 10）。
- `_recipient_id`：仅 pyzmq P2P 路径在 `model_dump()` 后注入的额外键，作为
  ROUTER 接收端二级路由用；不进入 `EventMessage` schema、不出现在 inprocess
  路径上。

## 与其它 spec 的关系

- **S_01（公开 API 与 Spec 流）** 定义了 `TransportSpec` / `StorageSpec` 的
  `pydantic.BaseModel` 形态与不变量"`Spec.build()` 是 spec → 实例的唯一桥"。
  本 spec 把 transport 这一支 build 链路展开到具体的注册表 + Config + 工厂三段
  实现。
- **S_03（coordination 协议）** 定义了 leader/teammate 之间所有 `EventMessage`
  类型与 `TeamTopic` 路由约定。本 spec 是它的传输底座——所有 coordination 事件
  都通过 `Messager` 的 `publish` / `send` 流转。
- **S_05（spawn 与 stream）** 直接消费本 spec 的不变量 20 / 21：
  `spawn_mode='inprocess'` 强制 inprocess transport；`spawn_mode='process'` 强制
  调用方显式配 pyzmq 或自定义跨进程后端。spawn payload 携带的是序列化后的
  `MessagerTransportConfig`，不是 `Messager` 实例。
- **S_07（交互视角与 HITT）** 通过 `register_direct_message_handler` 接入 P2P 通道——
  HumanAgentInbox 把人类成员的回复以 `EventMessage` 经由 P2P 送给 leader。本 spec
  确保每节点单 handler 的语义（不变量 13），avoiding inbox 与 coordination 互相覆盖。
- **`StorageSpec` 注册表**（`register_storage` / `_STORAGE_REGISTRY`，结构与本 spec 的
  transport 注册表对称）属于 task DB / message DB 持久化层，与本 spec 共享同一个
  `_ensure_builtin_infra_registered` lazy 初始化时机，但语义独立——见任务存储
  相关 spec。
