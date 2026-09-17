# S_16 KV Cache 子代理生命周期

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/kv_cache/kv_cache_subagent_lifecycle.py` |
| 最近一次修订日期 | 2026-08-31 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 DeepAgent 子Agent 生命周期与 Session 级 KVC 接口之间的适配规则。

`kv_cache_subagent_lifecycle.py` 负责：

- 判断 DeepAgent 是否开启 KVC affinity；
- 识别 sticky 与 one-shot 子代理；
- 解析 child Session 的 cache identity 和 parent lineage；
- 创建共享父 Session `KVCacheRuntime` 的 child Session；
- 将子代理的开始、成功结束、失败和取消映射为
  `Session.prepare_kvc()`、`Session.suspend_kvc()`、`Session.release_kvc()`。

该模块不负责：

- 保存 binding、action tail 或 inference admission；这些属于 `core/kv_cache/kv_cache_runtime.py`；
- 查找 Model 或调用 Provider；这些由 Session、Runtime 和 Model KVC 边界完成；
- 改变 KVC 关闭时的子代理调用、历史和清理路径；
- 定义 `DeepAgentConfig.kv_cache_affinity_config` 字段；该配置属于 `S_01`。

## 不变量

1. **KVC 是可选性能优化。** `affinity_enabled()` 返回 `False` 时，调用方必须保留原始
   `subagent.invoke(inputs)` 路径，不得为了 KVC 创建或传入 child Session。
2. **本模块是 Harness 子代理到 Session KVC 接口的唯一生命周期桥。** 其中不保存
   Model、binding、action tail，也不直接调用 `prefetch_kvc()`、`offload_kvc()` 或
   `evict_kvc()` Provider 接口。
3. **sticky 类型固定为 `browser_agent` 与 `verification_agent`。** sticky 子代理开始前
   调用 `prepare_kvc()`；成功结束后调用 `suspend_kvc()`。one-shot、失败或取消的子代理
   调用 `release_kvc()`。
4. **child Session 继承父 Session 的 Application 级 Runtime。** child Session 使用独立
   cache identity，但通过 `parent_session_id` 与父 cache root 建立 lineage。
5. **Team member 的 child identity 必须避免碰撞。** 当运行时父 Session id 与
   provider-facing parent cache id 不同时，`scope_sub_session_id()` 使用 parent cache id
   的稳定摘要扩展 child Session id。

## 接口契约

```python
# openjiuwen/harness/kv_cache/kv_cache_subagent_lifecycle.py
def affinity_enabled(deep_agent: Any) -> bool
def is_sticky_subagent_type(subagent_type: str) -> bool
def resolve_subagent_parent_cache_id(parent_session: Any) -> str
def scope_sub_session_id(
    sub_session_id: str,
    *,
    runtime_parent_session_id: str,
    parent_cache_id: str,
) -> str
def resolve_sub_session_id(
    *,
    task_id: str,
    parent_session_id: str,
    metadata: dict,
) -> str
def create_subagent_session(
    parent_session: Session,
    *,
    sub_session_id: str,
    parent_cache_id: str,
    card: Any = None,
) -> Session
async def prepare_subagent(session: Session, *, subagent_type: str) -> None
async def finish_subagent(
    session: Session,
    *,
    subagent_type: str,
    succeeded: bool,
) -> None
async def evict_subagent(session: Session) -> None
```

关键返回语义：

- `affinity_enabled()` 缺少 KVC 配置时返回 `False`；
- `resolve_sub_session_id()` 优先使用 `metadata["sub_session_id"]`，否则生成
  `{parent_session_id}_sub_{task_id}`；空 `task_id` 使用 `unknown`；
- `resolve_subagent_parent_cache_id()` 解析 lineage 失败时降级为运行时 Session id；
- `create_subagent_session()` 只在 KVC ON 路径调用。

## 生命周期映射

| Harness 事实 | 子代理类型 | Session KVC 动作 |
|---|---|---|
| 即将开始推理 | sticky | `prepare_kvc()` |
| 推理成功，可继续复用 | sticky | `suspend_kvc()` |
| 推理成功 | one-shot | `release_kvc()` |
| 推理失败或取消 | 任意类型 | `release_kvc()` |
| 显式驱逐子代理 | 任意类型 | `release_kvc()` |

完整调用关系：

```text
TaskTool / SessionSpawnExecutor / SubagentSessionManager
    → kv_cache_subagent_lifecycle
    → child Session.prepare_kvc / suspend_kvc / release_kvc
    → KVCacheRuntime
    → Model KVC action
```

## 与其它 spec 的关系

- 子代理运行、恢复、取消和关闭语义由 `S_10` 定义；本规约只映射其中的 KVC 动作。
- `kv_cache_affinity_config` 配置字段属于 `S_01`。
- sticky 类型名与 `S_18` 的预设子代理类型保持一致。
- Skill 库状态属于 `S_07`，不由 KVC 生命周期模块管理。
