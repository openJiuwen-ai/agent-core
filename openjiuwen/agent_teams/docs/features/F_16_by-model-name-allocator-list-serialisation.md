# ByModelNameAllocator List Serialisation

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-16 |
| 范围 | `openjiuwen/agent_teams/models/allocator.py`, `tests/unit_tests/agent_teams/models/test_allocator.py`, `openjiuwen/agent_teams/docs/specs/S_11_models-pool-and-allocation.md` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/models/test_allocator.py` 81 passed |
| Refs | #751 |

## 背景

用户配置多模型（同时含 `glm-5` 与 `glm-5.1`）启动 team，session
checkpoint 持久化时崩溃：

```
TypeError: 'NoneType' object does not support item assignment
  File "openjiuwen/core/session/utils.py", line 218, in expand_nested_structure
    current[current_key] = expand_nested_structure(value)
```

诊断日志逐层冒泡显示真因：

```
[split_nested_path] input=glm-5.1, output=['glm-5', '1']
[root_to_path] nested_path=glm-5.1, current_type=int, reason=current is not dict
[DEBUG] root_to_path returned None: key=glm-5.1, result_keys=['glm-5']
[DEBUG TypeError] key=inner_indexes ...
[DEBUG TypeError] key=model_allocator_state ...
[DEBUG TypeError] key=jiuwen_team_sess_... ...
```

调用栈：team session state → `model_allocator_state` →
`inner_indexes` → 处理 key `"glm-5.1"`。

`ByModelNameAllocator.state_dict()` 旧实现把 `_inner_indexes: dict[str, int]`
直接以 dict 形式序列化（key 是 `model_name`）。session 持久化层
`expand_nested_structure` 在处理 dict 时把含 `.` 的 key 当 nested-path
编码拆分；`"glm-5.1"` 拆成 `["glm-5", "1"]`，先创建 `result["glm-5"] = {}`
（如果是先遍历到 `glm-5.1`）或撞上已存在的 scalar `result["glm-5"] = 0`
（如果先遍历到 `glm-5`），第二层取到 None 后崩。

## 决策

`state_dict()` 改为 list 序列化，每条 record 携带 `model_name` 与 `index`
两个字面字段；`load_state_dict()` 优先读新 `counters` list，
向后兼容读旧 `inner_indexes` dict。

```python
# new
{
    "counters": [
        {"model_name": "glm-5", "index": 0},
        {"model_name": "glm-5.1", "index": 1},
    ],
    "pool_digest": "...",
}

# legacy (still loadable)
{
    "inner_indexes": {"glm-5": 0, "glm-5.1": 1},
    "pool_digest": "...",
}
```

要点：

- 持久化层从此**永不**把 model_name 放在 dict key 位置——彻底脱离
  "key 是 path 还是字面" 这个误解空间。
- `pool_digest` 字段不变；digest 不匹配仍然全部归零（既有不变量保持）。
- legacy 路径只在 load 端保留——新 session 一律按 list 写。
- 运行时内部状态 `self._inner_indexes: dict[str, int]` **不动**——它
  在进程内不经过 session 持久化层，模型名作 key 没有问题；仅出口
  端（`state_dict`）做格式翻译。

## 拒绝的方案

1. **只修底层 `expand_nested_structure` 不修上层**：底层 fix 已落
   ([commit `0629245fd`](#))，确实根除 crash 和数据损坏。但上层
   仍把 model_name 当 dict key 用，**别的子系统**未来再这么做也是
   一颗潜在地雷。Allocator 自己用 list 序列化是把"模型名是 opaque
   value"的语义在数据形态上显式化——比让每个 caller 都记得避坑可靠。
2. **强行限制 model_name 不含 `.` / `[`**：违反用户预期——`glm-5.1`、
   `claude-3.5-sonnet`、`gemini-1.5-pro` 都是真实在用的模型名。
3. **不做 legacy 兼容，让旧 session pool_digest 不匹配兜底归零**：
   会让所有已存 session 的轮转计数器静默清零。代价为零的
   `load` 端 fallback 完全可以保留旧数据。

## 验证

- `pytest tests/unit_tests/agent_teams/models/test_allocator.py` 81 passed
- 新增 regression 用例：
  - `test_by_model_name_state_dict_round_trips_dotted_model_names`：
    pool 含 `glm-5` / `glm-5.1` / `claude-3.5-sonnet`，state_dict
    序列化为 list，load_state_dict 后轮转计数无误。
  - `test_by_model_name_load_state_dict_accepts_legacy_dict_format`：
    旧 `inner_indexes` dict 格式仍能正确恢复 counters。
- 既有 `test_by_model_name_state_dict_resumes_per_group_rotation` /
  `test_persist_leader_config_includes_allocator_state` 等 round-trip
  用例同步迁移到新 `counters` list 断言。
- `test_by_model_name_load_state_dict_tolerates_malformed_input` 同步
  覆盖新格式的非法输入容忍（`counters` 非 list、`index` 非数字）。

## 已知遗留

- `RoundRobinModelAllocator` / `RouterAllocator` 的 `state_dict` 本来
  就不含 dict-of-model-name，未受此问题影响，不改。
- 仓库其它地方如果再出现"把用户输入字符串当 dict key 塞进 session
  state"的代码，靠下层 `expand_nested_structure` 的字面递归 fix
  兜底；未来如发现新的同类 caller，按本 feature 同款方式改 list 序列化。
