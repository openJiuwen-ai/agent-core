# Models Pool and Allocation

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/agent_teams/models/` |
| 最近一次修订 commit | 18823271 |
| 关联 feature | — |

## 范围 / 边界

本规约只管 **`agent_teams/models/` 这一层的多模型部署原语**：池条目的字段
契约、池刷新时的 `model_id` 继承策略、三种分配策略的语义、Allocator 协议、
单端点 router 便利配置如何展开成统一的池视图、空池兜底如何回到 per-agent
模型配置。

**不管**的事情：

- `TeamSpec.model_pool` / `TeamAgentSpec.model_pool` / `model_pool_strategy`
  / `model_router` 字段在 schema 层的声明位置——那是 `schema/blueprint.py`
  与 `schema/team.py` 的事，本规约只规定从这些字段构造出来的运行时形态。
- `TeamModelConfig` / `ModelClientConfig` / `ModelRequestConfig` 的内部字段——
  那是 `core/foundation/llm` 的事，本规约只规定 `to_team_model_config()`
  的物化路径。
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
   在 spec 层提前拦下用户便利路径上的重复/空白名。

7. **`model_pool` 与 `model_router` 在 `TeamAgentSpec` 上互斥**。同时
   配置直接 `ValueError`。strategy `"router"` 也允许由用户手动配 pool
   + 把 strategy 设成 `"router"` 触发——但条目唯一性约束相同，由
   `RouterAllocator` 在构造时强制。

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

### 三种内置策略

| 类 | `allocate(None)` | `allocate(name)` | 持久化字段 | 备注 |
|---|---|---|---|---|
| `RoundRobinModelAllocator` | 推进 `_index`，返回下一条 | 同上（忽略 name） | `index` + `pool_digest` | 全 pool 线性轮转，name-agnostic |
| `ByModelNameAllocator` | `None` | 取 group → 推进 group 内 `_inner_indexes[name]` → 返回 | `inner_indexes`（dict）+ `pool_digest` | 缺 name 或 name 未在池中 → `None`，调用方走 per-agent 兜底 |
| `RouterAllocator` | 返回 `pool[0]` 作为团队默认模型 | name 唯一映射 → 命中即返回；未命中 → `None` | 仅 `pool_digest` | 构造时校验池非空 + name 唯一；`load_state_dict` no-op |

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

### `model_router` 与 `model_pool` 的关系

`TeamAgentSpec` 暴露两条用户输入：

- `model_pool: list[ModelPoolEntry]`：完整声明，每条独立凭证。
- `model_router: Optional[ModelRouterConfig]`：单端点便利输入，一份
  凭证 + name 列表。

`@model_validator` 拒绝同时配置；`build()` 时把 `model_router`
通过 `to_pool_entries()` 展开成 `model_pool` 并把
`model_pool_strategy` 设成 `"router"`。下游全部走 pool 视图，没有
router 专用分支。

用户也可手动 `model_pool=[...] + model_pool_strategy="router"`，但
`RouterAllocator` 构造时强制每条 name 唯一——重复直接 `ValueError`，
不允许"先看看运行起来什么样"。

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
