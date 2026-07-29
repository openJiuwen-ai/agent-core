# Models Pool and Allocation

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/models/` |
| 最近一次修订日期 | 2026-07-17 |
| 关联 feature | F_16_by-model-name-allocator-list-serialisation.md、F_67_intelli-router-allocation-strategy.md |

## 范围 / 边界

本规约只管 **`agent_teams/models/` 这一层的多模型部署原语**：池条目的字段
契约、池刷新时的 `model_id` 继承策略、四种分配策略的语义、Allocator 协议、
两种 router 便利配置（单端点 `ModelRouterConfig` / 客户端可靠路由
`IntelliRouterConfig`）如何展开成统一的池视图、空池兜底如何回到 per-agent
模型配置。

**不管**的事情：

- `TeamSpec.model_pool` / `TeamAgentSpec.model_pool` / `model_pool_strategy`
  / `model_router` / `model_intelli_router` 字段在 schema 层的声明位置——那是
  `schema/blueprint.py` 与 `schema/team.py` 的事，本规约只规定从这些字段
  构造出来的运行时形态。
- `TeamModelConfig` / `ModelClientConfig` / `ModelRequestConfig` 的内部字段——
  那是 `core/foundation/llm` 的事，本规约只规定 `to_team_model_config()`
  的物化路径。
- `IntelliRouterModelClient` / `intelli_router.ReliableRouter` 的路由策略、
  重试与健康检查语义——那是 `core/foundation/llm` 与第三方包的事。本规约只
  规定 `IntelliRouterConfig` 如何展开成池视图、以及 `metadata.client` 上那组
  `intelli_router_*` 键的生成规则。
- 把 allocator 接进 `TeamAgent._setup_agent` 的具体集成、leader/teammate
  spawn 时怎么调用 `allocate()`——那是 `agent/` 子系统的事，本规约只规定
  allocator 暴露给调用方的协议方法。
- session checkpoint 中 `model_allocator_state` 的存放位置——那是
  `runtime/metadata.py` 的事，本规约只规定 allocator 的 `state_dict` /
  `load_state_dict` round-trip 契约。
- 池中条目的 `metadata.client` / `metadata.request` 字段语义——那是
  foundation 层 client/request 配置的事，本规约只规定它们在物化时与显式
  字段的优先级。

## 不变量

下列断言在任意时刻必须为真。违反任何一条都属于设计退化，不是"暂时绕一下"。

1. **`model_id` 是进程局部、永不持久化的运行时身份**。每次池从 spec 重建
   时，未匹配到旧条目签名的新条目都要拿到一个全新的 uuid。任何把
   `model_id` 写进 DB 或跨 session 携带的代码都是错的——它的作用只是给
   foundation 层的 client 缓存做去重 key，跨进程没意义。

2. **持久化身份是 `(model_name, group_index)`**。DB 里只能存这两个字段，
   不能存条目的快照副本。运行时凭证、URL、request 旋钮都从 in-session
   池里 live 读取，凭证轮换不需要重 spawn 成员。

3. **`inherit_pool_ids` 只对 bit-exact 旧条目继承 `model_id`**。bit-exact
   的判定基于 `_entry_signature`（除 `model_id` 外所有字段 JSON
   canonical）；任何字段差异（含 api_key 轮换）都强制新 uuid，避免基础
   设施层缓存到旧凭证的 client 在轮换后继续服务。重复签名按池序一一配对，
   多余新条目用各自的 auto-uuid。

4. **`Allocator.allocate()` 返回 `None` ⇔ "无可用条目"**。`None` 是
   显式的回退信号，调用方据此走 `TeamAgentSpec.agents` per-agent 模型
   配置兜底。`None` 不是错误，不抛异常。

5. **`build_model_allocator` 在空池上必须返回 `None`**，不构造任何
   allocator——这是 per-agent 兜底链路的入口条件。pool 非空时，未识别的
   `model_pool_strategy` 抛 `ValueError`，不静默回退。

6. **Router 策略下 `model_name` 必须全池唯一**。`RouterAllocator` 构造
   时校验，重复直接 `ValueError`；`ModelRouterConfig._validate_model_names`
   在 spec 层提前拦下用户便利路径上的重复/空白名。`intelli_router` 继承
   同一条约束（`IntelliRouterAllocator` 是 `RouterAllocator` 的子类）。

7. **`model_pool` / `model_router` / `model_intelli_router` 在
   `TeamAgentSpec` 上三者互斥**。配置超过一个直接 `ValueError`（报错列出
   实际配了哪几个）。strategy `"router"` / `"intelli_router"` 也允许由用户
   手动配 pool + 设 strategy 触发——但条目约束相同，由对应 allocator 在
   构造时强制。

13. **`intelli_router` 策略下每条 entry 携带的 deployment 列表必须完全
    相同且是全量**，且 `api_provider` 恒为 `"intelli_router"`。

    这条**不是**"顺手复制一份"，而是 router 共享的**前提**，破坏它会静默
    超配额：`IntelliRouterModelClient` 按 client-config 缓存
    `ReliableRouter`，其 cache key 由 **deployment 列表 + 路由旋钮**算出，
    **不含 `model_name` / `client_id`**。各 entry 的 deployments 逐字相同
    → 命中同一个 router → failover 状态、健康检查、per-deployment 的
    tpm/rpm 配额**全团队共享**。

    反过来，"每条 entry 只带自己那个模型的 deployment"这个看似自然的优化
    会让 key 发散 → 每个成员各建一个 router → **各自独立计 rpm/tpm**：
    4 人团队实际花掉声明配额的 4 倍，且谁都不知道别人已经发现某个
    deployment 挂了。单测
    `test_intelli_router_all_entries_share_one_router_cache_key` 钉死此点
    （mutation 验证过：按 model_name 过滤 deployments 立即变红）。

    成员之间**该有的差异在 request config 上**——pin 的 `model_name` 在
    `ModelRequestConfig` 里，不在 client config 里。因此每个成员各有一个
    **薄** `IntelliRouterModelClient` 包装（`client_id` 逐个不同，符合预期），
    重的东西只有一份。

    entry 自身的 `api_key` / `api_base_url` 恒为空串（凭证是 per-deployment
    的；该 provider 不在 foundation 层的顶层凭证校验集合内）。
    `IntelliRouterAllocator` 构造时校验 provider 与 deployments 非空，
    针对用户手写 pool 的路径。

14. **多端点可靠性只能由一层拥有**。`round_robin` / `by_model_name` /
    `router` 把成员摊到多个端点上（allocator 负责可用性）；`intelli_router`
    把多端点整个下沉给客户端 router（client 负责可用性）。两者是**替代**
    关系，不是叠加——池里放多条 intelli_router entry 再套 `round_robin`
    是两层都做负载均衡，属设计退化。

15. **`IntelliRouterDeployment.api_base` 不含 `/v1`**。这是全仓唯一与
    `ModelClientConfig.api_base`（指向 OpenAI 兼容 API 根、通常以 `/v1`
    结尾）**约定相反**的字段：intelli_router 的 provider adapter 自行拼接
    `f"{api_base}/v1/chat/completions"`。传入带 `/v1` 的值得到
    `/v1/v1/...` → 404，且上游错误处理在未 `read()` 流式响应时读 body，
    抛出的是 `ResponseNotRead` 而非 404——真实错误被吞掉。框架**不**自动
    剥离该后缀（静默改用户输入更难查），由调用方转换。

8. **池条目的显式字段优先于 `metadata.client` / `metadata.request`**。
   `to_team_model_config()` 物化时，`api_key` / `api_base_url` /
   `api_provider` / `model_name` / `client_id`（来自 `model_id`）覆盖
   metadata 中同名键。metadata 是补充，不是覆盖通道。

9. **`Allocation` 是一次分配的不可变结果**。`@dataclass(frozen=True,
   slots=True)`；`to_team_model_config()` / `to_db_ref()` 的两条出口
   不允许调用方绕过去直接读 `entry`。所有 allocator 必须返回这个类型。

10. **`resolve_member_model` 是纯位置查找，不动 allocator 计数器**。
    分组缩水时 fallback 到 index 0（确定性回退）；分组不存在或池为空
    返回 `None`。它是从 DB ref 复活 live 配置的唯一入口，不参与 spawn
    时的初次分配。

11. **`state_dict` / `load_state_dict` 必须 JSON round-trip 安全**。
    snapshot 必须可经 `json.dumps` / `json.loads` 还原。`load_state_dict`
    必须容忍：缺键（从 0 开始）、未知键（忽略）、`pool_digest` 不匹配
    （计数器全部归零或保持默认）。`pool_digest` 只覆盖结构维度
    `(model_name, api_base_url)` per-entry——凭证或 metadata 变更不刷
    digest，rotation 计数照常继承。

12. **新分配策略只需实现 `ModelAllocator` 协议**。不要为新策略改基础
    数据类型（`Allocation` / `ModelPoolEntry`）的字段，不要在
    `build_model_allocator` 之外建第二条派发路径。

## 接口契约

### `ModelPoolEntry` （pydantic BaseModel）

```python
class ModelPoolEntry(BaseModel):
    model_name: str
    api_key: str
    api_base_url: str
    api_provider: str
    description: Optional[str] = None
    model_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict = Field(default_factory=dict)

    def to_team_model_config(self) -> TeamModelConfig: ...
```

物化规则：

- `metadata.client`（dict）合并进 `ModelClientConfig` 的 kwargs；
  `metadata.request`（dict）合并进 `ModelRequestConfig` 的 kwargs。
- 显式字段（`api_key` → `api_key`、`api_base_url` → `api_base`、
  `api_provider` → `client_provider`、`model_id` → `client_id`、
  `model_name` → `model`）总是后写入，覆盖 metadata 中同名键。
- metadata 顶层除 `client` / `request` 外的键不被物化消费——保留给
  allocator 策略（权重、亲和提示等）。

### `ModelRouterConfig` （pydantic BaseModel）

```python
class ModelRouterConfig(BaseModel):
    api_base_url: str
    api_key: str
    api_provider: str
    model_names: list[str] = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)

    def to_pool_entries(self) -> list[ModelPoolEntry]: ...
```

字段约束（`@model_validator(mode="after")`）：

- `model_names` 非空（`min_length=1`）。
- 元素全部为非空、非纯空白字符串。
- 元素全局唯一。

`to_pool_entries()` 给每个 name 复制一份 `(api_key, api_base_url,
api_provider, deepcopy(metadata))`，展开成 `list[ModelPoolEntry]`；
`TeamAgentSpec.build()` 调用它并把 `model_pool_strategy` 设成
`"router"`，下游所有路径（`resolve_member_model` / `inherit_pool_ids`
/ `update_model_pool`）一律走 pool 视图，没有 router 专用分支。

### `IntelliRouterDeployment` / `IntelliRouterConfig` （pydantic BaseModel）

```python
class IntelliRouterDeployment(BaseModel):
    model_name: str
    api_key: str
    api_base: str          # provider ROOT —— 不含 /v1，见不变量 15
    id: str | None = None
    provider: str = "openai"
    tpm: int | None = None
    rpm: int | None = None
    tags: list[str] = []
    timeout: float | None = None
    verify_ssl: bool | None = None

    def to_deployment_dict(self) -> dict: ...


class IntelliRouterConfig(BaseModel):
    deployments: list[IntelliRouterDeployment] = Field(min_length=1)
    model_names: list[str] | None = None
    strategy: str = "simple-shuffle"
    num_retries: int = 3
    timeout: float = 30.0
    strategy_kwargs: dict = {}
    enable_health_check: bool = False
    health_check_interval: float = 300.0
    enable_observability: bool = False
    web_dashboard_port: int = 0
    verify_ssl: bool = True
    metadata: dict = {}

    def resolved_model_names(self) -> list[str]: ...
    def to_pool_entries(self) -> list[ModelPoolEntry]: ...
```

`to_deployment_dict()`：必填 `(model_name, api_key, api_base, provider,
tags)` 恒输出；`id` / `tpm` / `rpm` / `timeout` / `verify_ssl` **为 None 时
省略该键**，让客户端套用自己的兜底——尤其 `verify_ssl`，客户端只在键缺失时
才回退到 router 级取值（`dep_cfg.get("verify_ssl", config.verify_ssl)`）。

`resolved_model_names()`：`model_names` 显式给出则原样返回（保序）；否则
推导为 `["*"] + distinct(deployment model_names, 保序)`。**首元素是团队
默认**（`allocate(None)` 取 `pool[0]`）。

`model_names` 字段约束（`@model_validator(mode="after")`，仅在显式给出时生效）：

- 非空、元素非空白、元素唯一（同 `ModelRouterConfig`）。
- **每个名字必须是 `"*"` 或某条 deployment 的 `model_name`**——否则展开出的
  entry 分配给成员后，router 在请求期才发现路由不到；能在 spec 层拦下的
  错误不留到运行期。

`to_pool_entries()`：每个逻辑 name 一条 entry，`api_provider` 写死
`"intelli_router"`、`api_key` / `api_base_url` 为空串、`metadata.client`
合并进这组生成键（**生成键覆盖用户在 metadata.client 里的同名键**）：

| 键 | 来源 |
|---|---|
| `intelli_router_deployments` | `[dep.to_deployment_dict() for dep in deployments]`（**全量，每条 entry 都一样**） |
| `intelli_router_strategy` / `_num_retries` / `_timeout` / `_strategy_kwargs` | 同名字段 |
| `intelli_router_enable_health_check` / `_health_check_interval` | 同名字段 |
| `intelli_router_enable_observability` / `_web_dashboard_port` | 同名字段 |
| `verify_ssl` | router 级 `verify_ssl`（非 `intelli_router_` 前缀——它是 `ModelClientConfig` 的一等字段） |

`TeamAgentSpec.build()` 调用它并把 `model_pool_strategy` 设成
`"intelli_router"`；与 `model_router` 同理，下游一律走 pool 视图，
**没有 intelli_router 专用分支**。

### `inherit_pool_ids`

```python
def inherit_pool_ids(
    current_pool: list[ModelPoolEntry],
    new_pool: list[ModelPoolEntry],
) -> list[ModelPoolEntry]: ...
```

- 按 `_entry_signature(entry)` 对齐：`json.dumps(entry.model_dump(
  exclude={"model_id"}), sort_keys=True, default=str)`。
- 同签名多条按池序一一配对；旧条目用尽则后续新条目保持 auto-uuid。
- 顺序无关：reorder-only 的两个池能完全对齐。
- 任意值差异（含 api_key、api_base_url、metadata 任一键、description）
  破坏匹配，强制新 uuid。
- 调用方显式传入的 `model_id` 在没有签名匹配时会被保留（不覆盖未匹配
  新条目）。

### `Allocation` （`@dataclass(frozen=True, slots=True)`）

```python
@dataclass(frozen=True, slots=True)
class Allocation:
    entry: ModelPoolEntry
    group_index: int

    def to_team_model_config(self) -> TeamModelConfig: ...
    def to_db_ref(self) -> dict:
        # {"model_name": entry.model_name, "model_index": group_index}
        ...
```

- `entry` 是池条目本身的引用，不是副本——allocator 不能私藏拷贝。
- `group_index` 是该 entry 在其同名分组内的位置（0-based）。Router 策略
  下每名一条，恒为 0；round-robin 下取该 entry 在分组内的位置。
- `to_db_ref()` 是写入 DB 的唯一形态：键名 `model_name` /
  `model_index`（注意 DB 字段叫 `model_index`，allocator 内部叫
  `group_index`，名称不一致是历史决策，不要随手改）。

### `ModelAllocator` 协议（`@runtime_checkable Protocol`）

```python
class ModelAllocator(Protocol):
    def allocate(self, model_name: Optional[str] = None) -> Optional[Allocation]: ...
    def state_dict(self) -> dict: ...
    def load_state_dict(self, state: dict) -> None: ...
```

- `allocate(None)`：实现自行决定语义。round-robin 推进计数器；by-name
  返回 `None`；router 返回首条作为默认。
- `allocate(name)`：name-aware 实现按 name 路由；name-agnostic 实现忽略
  hint 但必须接受参数。
- `state_dict()`：JSON-friendly snapshot；必须可 round-trip。
- `load_state_dict(state)`：必须容忍缺键、未知键、`pool_digest` 不
  匹配；不抛异常。

### 四种内置策略

| 类 | `allocate(None)` | `allocate(name)` | 持久化字段 | 备注 |
|---|---|---|---|---|
| `RoundRobinModelAllocator` | 推进 `_index`，返回下一条 | 同上（忽略 name） | `index` + `pool_digest` | 全 pool 线性轮转，name-agnostic |
| `ByModelNameAllocator` | `None` | 取 group → 推进 group 内 `_inner_indexes[name]` → 返回 | `counters`（list of `{model_name, index}`）+ `pool_digest`；`load_state_dict` 兼容读旧 `inner_indexes`（dict）格式 | 缺 name 或 name 未在池中 → `None`，调用方走 per-agent 兜底。`counters` 用 list 而非 `dict[model_name, int]`，是因为 model_name 可能含 `.` / `[`（如 `"glm-5.1"`），而 session 持久化层把这类字符当 nested-path 解读 |
| `RouterAllocator` | 返回 `pool[0]` 作为团队默认模型 | name 唯一映射 → 命中即返回；未命中 → `None` | 仅 `pool_digest` | 构造时校验池非空 + name 唯一；`load_state_dict` no-op |
| `IntelliRouterAllocator`（`RouterAllocator` 子类） | 同父类：返回 `pool[0]`——`to_pool_entries` 把 `"*"`（统一路由）排首位，故 leader 默认取到可用性最高的一档 | 同父类 | 同父类（仅 `pool_digest`） | **分配语义与父类逐字相同**，差异仅在 entry 的含义（一整个 deployment 列表 vs 一个远端端点）——那是池怎么建的属性，不是怎么分配的属性。子类只增加构造期校验：每条 entry 的 `api_provider == "intelli_router"` 且 `metadata.client.intelli_router_deployments` 非空 |

`IntelliRouterAllocator` 用继承而非复制实现：intelli_router pool **就是**
一种 router pool，只不过那个"单端点"在客户端。为让两个策略"看起来对称"
而复制一遍 `_by_name` 查找 + 首条兜底 + digest-only state，是用重复代码换
视觉整齐。

`pool_digest` 由 `_pool_digest` 计算：每条 entry 取
`(model_name, api_base_url)`，按池序拼接 sha1。凭证 / metadata 变更
不刷 digest——它只反映结构性变化（增删 / reorder）。digest 不匹配时：

- `RoundRobinModelAllocator`：`_index` 归零。
- `ByModelNameAllocator`：所有 `_inner_indexes` 归零。
- `RouterAllocator`：no-op（无计数器）。

### `build_model_allocator`

```python
def build_model_allocator(
    spec: TeamAgentSpec,
    team_spec: TeamSpec,
) -> Optional[ModelAllocator]: ...
```

- 空池 → `None`（per-agent 兜底入口）。
- `team_spec.model_pool_strategy` 派发：
  `"round_robin"` → `RoundRobinModelAllocator`
  `"by_model_name"` → `ByModelNameAllocator`
  `"router"` → `RouterAllocator`
  `"intelli_router"` → `IntelliRouterAllocator`
  其它 → `ValueError`（不静默回退）。
- `spec` 形参当前未使用，保留给未来需要 per-agent metadata 的策略。

### `resolve_member_model`

```python
def resolve_member_model(
    team_spec: TeamSpec,
    *,
    model_name: Optional[str],
    model_index: Optional[int],
) -> Optional[TeamModelConfig]: ...
```

解析顺序：

1. 池为空或 `model_name` 为 falsy → `None`。
2. 同 `model_name` 分组不存在 → `None`。
3. 分组存在且 `model_index` 是合法范围内整数 → 返回该位置 entry 的
   `to_team_model_config()`。
4. 分组存在但 index 越界（分组缩水）→ fallback 到 index 0。

不动 allocator，不写 DB，不刷新计数器——纯 lookup。

## 数据结构

### 字段构成（`ModelPoolEntry`）

| 字段 | 类型 | 默认 | 生命周期 |
|---|---|---|---|
| `model_name` | `str` | — | 持久化身份的一半；DB 里 `tasks` / `members` 表只存它（与 `model_index`） |
| `api_key` | `str` | — | live 凭证；不进 DB；池刷新时 in-place 更新 |
| `api_base_url` | `str` | — | live 端点；不进 DB |
| `api_provider` | `str` | — | live provider；不进 DB |
| `description` | `Optional[str]` | `None` | 用户自描述；不参与 allocator 决策 |
| `model_id` | `str (uuid4)` | auto | 进程局部 client 身份；surfaced 为 `ModelClientConfig.client_id`；不进 DB；池刷新时仅 bit-exact 继承 |
| `metadata` | `dict` | `{}` | 物化时合并进 `client` / `request`；其余顶层键保留给 allocator 策略 |

### 持久化身份 vs 运行时身份

- **持久化身份**：`(model_name, group_index)`。group_index 是同 name
  分组内的位置；分配时由 allocator 计算并通过 `Allocation.to_db_ref()`
  落库。DB 字段名为 `model_name` / `model_index`。
- **运行时身份**：`model_id`（uuid4）。surfaced 为
  `ModelClientConfig.client_id`，foundation 层 client 缓存的 dedupe
  key。每次池从 spec 重建（`update_model_pool` / `inherit_pool_ids`
  入口），bit-exact 旧条目继承旧 `model_id`，否则重新生成。

两套身份正交：DB 复活成员时按 `(model_name, model_index)` 在 live 池
上查到 entry，自然拿到该 entry 当前的 `model_id`——凭证轮换后旧
client 不会被再次命中（签名变了 → uuid 变了 → cache miss）。

### `model_pool` / `model_router` / `model_intelli_router` 的关系

`TeamAgentSpec` 暴露三条互斥的用户输入：

- `model_pool: list[ModelPoolEntry]`：完整声明，每条独立凭证。
- `model_router: Optional[ModelRouterConfig]`：单端点便利输入，一份
  凭证 + name 列表。**可靠性归 allocator**（把成员摊到多个 name 上）。
- `model_intelli_router: Optional[IntelliRouterConfig]`：客户端可靠路由
  输入，一组 deployment + 路由旋钮。**可靠性归 client**（router 在请求期
  跨 deployment 重试 / failover / 限流感知）。

`@model_validator` 拒绝配置超过一个（报错列出实际配了哪几个）；`build()`
时把对应 router 通过 `to_pool_entries()` 展开成 `model_pool` 并把
`model_pool_strategy` 设成 `"router"` / `"intelli_router"`。下游全部走
pool 视图，没有 router 专用分支。

用户也可手动 `model_pool=[...] + model_pool_strategy="router"`（或
`"intelli_router"`），但对应 allocator 构造时强制约束——`router` 强制 name
唯一，`intelli_router` 额外强制 provider 与 deployments 非空——重复/错配
直接 `ValueError`，不允许"先看看运行起来什么样"。

**选哪一个**：`model_router` 与 `model_intelli_router` 不是"新旧"关系，而是
可靠性归属不同的两层（见不变量 14）。上游本来就是单端点网关（OpenRouter /
LiteLLM proxy）→ `model_router`；要在多个独立端点/凭证之间做请求级容错 →
`model_intelli_router`。**不要叠加**。

### 空池兜底

- 用户没配 `model_pool` 也没配 `model_router` → `team_spec.model_pool`
  为空。
- `build_model_allocator` 返回 `None`。
- `TeamAgent._setup_agent` 走 `TeamAgentSpec.agents` per-agent 路径：
  leader 用 `LeaderSpec.model` / teammate 用 `TeamMemberSpec.model`。
- 这条兜底路径与 legacy 完全等价——allocator 是叠加能力，不是替代。

### Allocator 状态生命周期

```
spec → build_model_allocator → allocator instance（in-memory）
                                    │
                       allocate() ←─┤
                                    │
                       state_dict()─┤── session checkpoint["teams"][name]["model_allocator_state"]
                                    │
            load_state_dict(saved) ←┘   ← 恢复时
```

- 计数器只活在 in-memory；checkpoint 持久化只存 `state_dict()` 输出。
- 恢复时：先按 `team_spec.model_pool` 重建 allocator（fresh 计数器
  + 当前 `pool_digest`），再 `load_state_dict(saved_state)`。digest
  不匹配 → 计数器归零；匹配 → rotation 接续。
- `RouterAllocator` 没有计数器，`load_state_dict` 是 no-op。

## 与其它 spec 的关系

- **S_01 public-api-and-spec-flow**：`TeamAgentSpec` / `TeamAgentSpec.build()`
  是池条目与 router 的入口，`model_pool` / `model_router` /
  `model_pool_strategy` 字段在那里对外。本规约接住 build() 之后的运行时
  形态。
- **S_02 team-agent-architecture**：`TeamAgent._setup_agent` 在 leader
  / teammate spawn 时调用 `allocate()` 并把结果写进 spawn 调用，本规约
  规定 allocator 暴露给 spawn 链路的协议契约（`Allocation` 出口与
  `None` 兜底语义）。
- **S_04 session-and-recovery**：session checkpoint 中
  `state["teams"][team_name]["model_allocator_state"]` 持久化的就是
  `state_dict()` 输出；recovery 时按本规约约定的 round-trip 协议恢复。
  `(model_name, model_index)` 也由 session 层在 member 表中持久化、由
  `resolve_member_model` 在复活时反查。
- **S_05 member-spawn-and-stream**：teammate spawn 时按 `model_name`
  hint 调 `allocate(name)`；返回 `None` 时 spawn 走 per-agent 模型
  兜底。本规约规定 `None` 的语义是"无可用条目"而不是错误。
- **S_06 runtime-pool-dispatch**：`update_model_pool` 走 `inherit_pool_ids`
  做池刷新；本规约规定 bit-exact 继承的精确语义与 `pool_digest` 的
  re-evaluation 时机。
