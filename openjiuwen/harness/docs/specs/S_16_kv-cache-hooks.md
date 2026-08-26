# S_16 KV Cache 亲和钩子

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/kv_cache/`（2 文件） |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 DeepAgent 子代理生命周期使用的 **KV Cache 亲和钩子**（`kv_cache/`）。
KVC 的亲和判定、sticky 白名单与子会话键解析都收敛在这一个模块，供子代理运行时
（`S_10`）与预设子代理装配（`S_18`）消费。

具体覆盖：

- `kv_cache/kv_cache_hooks.py`：`affinity_enabled` / `is_sticky_subagent_type` /
  `resolve_sub_session_id` / `get_model`（DeepAgent 子代理生命周期的 KVC 策略钩子）。

不在本规约范围内：
- KVC 内核（`openjiuwen/core/foundation/kv_cache/`）—— core 规约。
- Skill 库开关状态（`skills/`）—— `S_07`。
- `kv_cache_affinity_config` 配置字段（`DeepAgentConfig`）—— `S_01`。

## 不变量

1. **`kv_cache_hooks.py` 是 DeepAgent ↔ KVC 的唯一桥**：`affinity_enabled(deep_agent)`
   从 `deep_config.kv_cache_affinity_config.enable_kv_cache_affinity` 判定亲和是否开启
   （不检查 model/绑定状态）；`is_sticky_subagent_type(subagent_type)` 只在
   `{"browser_agent", "verification_agent"}` 返回 True——sticky 类型是 KVC 亲和的
   子代理白名单。
2. **子会话 id 解析唯一**：`resolve_sub_session_id(task_id, parent_session_id, metadata)`
   ——`metadata["sub_session_id"]` 优先，否则 `f"{parent_session_id}_sub_{task_id}"`。
   该 id 是子代理在 KVC 侧的会话键（`S_10` 子代理生命周期配套）。

## 接口契约

```python
# kv_cache/kv_cache_hooks.py
def affinity_enabled(deep_agent: Any) -> bool
def is_sticky_subagent_type(subagent_type: str) -> bool
def resolve_sub_session_id(*, task_id: str, parent_session_id: str,
                           metadata: dict) -> str
def get_model(deep_agent: Any) -> Any | None
```

错误 / 返回语义：

- `affinity_enabled` 无 `kv_cache_affinity_config` → `False`（不抛）。
- `resolve_sub_session_id` 空 task_id → `f"{parent_session_id}_sub_unknown"`。

## 数据结构

### KVC 亲和判定链

`affinity_enabled`（全局开关）∧ `is_sticky_subagent_type`（子代理白名单）→
`resolve_sub_session_id`（会话键）→ core `dispatch_session_kv_cache_signal` /
`evict_session_kv_cache`（core 动作）。

## 与其它 spec 的关系

- KVC 钩子在子代理生命周期被调（`S_10`）；`kv_cache_affinity_config` 配置字段 ——
  `S_01`。
- sticky 白名单（`browser_agent` / `verification_agent`）与 `S_18` 的 spawn 类型名一致。
- core 侧 `dispatch_session_kv_cache_signal` / `evict_session_kv_cache` 属 core 规约，
  本 spec 只锚定 harness 侧的钩子契约。
