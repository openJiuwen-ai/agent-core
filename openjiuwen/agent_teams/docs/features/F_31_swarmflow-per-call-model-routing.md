# Swarmflow Per-Call Model 路由

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-03 |
| 范围 | `workflow/backends/team_worker_backend.py`、`workflow/runner.py`、`agent/team_agent.py`、`tests/unit_tests/agent_teams/workflow/test_worker_backend.py` |
| 测试基线 | `make test TESTFLAGS="tests/unit_tests/agent_teams/workflow/"` → 17 passed |
| Refs | #751 |

## 背景

swarmflow 引擎原语 `agent(prompt, *, model=...)`（`workflow/engine/primitives.py`）从
上游 `dw/wf` 移植时就声明了 `model: str | None` 参数：脚本可以为单个 `agent()` 调用
指定模型，让昂贵步骤走大模型、轻步骤走小模型。引擎层一直忠实地把这个 hint 收进
`opts` 并纳入 resume 内容签名（`journal.call_signature` 把 `model` 算进 SHA-256），所以
改一个 step 的 model 会让该 step 缓存失效、其余命中——这部分本来就对。

但**真实执行路径从没消费它**。`TeamWorkerBackend._execute_worker` 一律
`create_deep_agent(model=self._model, ...)`，`self._model` 是构造时传入的 leader model
（`run_swarmflow_background` 取 `self.harness.model`）。`opts["model"]` 拿到了却被丢弃。
结果：原语接口暴露了能力，所有 worker 却都跑 leader 同一个模型——一个
declared-but-not-wired 的 gap。F_27 的「已知遗留」也记了这一条。

本特性把这条链路接通：让 `agent(model="X")` 真正把 worker 切到名为 `X` 的模型。

## 数据结构 / 状态机

模型来源是 team 的 model pool（`TeamSpec.model_pool` + `models/allocator.py`）。字符串
模型名 → `Model` 实例的解析路径已存在：

```
agent(model="X")
  → opts["model"] = "X"
  → TeamWorkerBackend.run(prompt, opts, schema)
      → model = self._resolve_model("X")          # 命中则 pool 的 Model，否则 leader model
      → _execute_worker(..., model=model)
          → create_deep_agent(model=model, ...)

resolve_member_model(team_spec, model_name="X", model_index=None)
  → TeamModelConfig | None
  → .build() → Model
```

`_resolve_model` 的回退语义：`model_name` 为空 / 无 resolver / resolver 返回 `None`
（pool 未配或名字缺失）→ 一律返回 `self._model`（leader model）。与
`resolve_member_model` 的「找不到返回 None、caller 回退 per-agent model」语义一致。

## 决策

1. **注入 `(name) -> Model | None` 回调，而非让 backend 持有 `team_spec`。**
   `TeamWorkerBackend.__init__` 新增 `model_resolver: Callable[[str], Any] | None`。
   backend 只问「给名字、还 Model」，pool / allocator 的查找细节留在 team 层
   （`run_swarmflow_background` 构造 `_resolve_worker_model` 闭包）。这与
   `agent_configurator` 复用 `models/` allocator 回调的既有模式一致，也避免 engine
   对接层耦合 `TeamSpec` 结构。

2. **用 `resolve_member_model` 纯位置查找，不走 allocator 轮转。** swarmflow worker
   用完即弃、不持久化身份，引入 allocator 就要把其轮转计数器持久化进 session——
   而 swarmflow 是 fire-and-forget background task，没有这条持久化接线。纯查找
   （同名组取首个端点、无状态、可重入）对当前需求足够。「按 model_name 分散端点
   负载」是后续优化，不提前做。

3. **在 `run` 里解析一次 model，threading 进两个 `_execute_worker` 调用。**
   `_execute_worker` 新增 `model` 关键字参数（它本就是测试 override 点），schema
   路径与 free-text 路径共享同一个已解析 model。

4. **回退而非报错。** 名字解析失败不抛异常——静默回退 leader model。脚本作者写错
   模型名或在没配 pool 的 team 上跑带 model hint 的脚本，行为退化为「全用 leader
   model」，与接通前一致，不会让整个工作流挂掉。

## 拒绝的方案

- **backend 直接持有 `team_spec` 调 `resolve_member_model`。** 让 `workflow/backends`
  这层耦合 `TeamSpec.model_pool` 结构 + allocator 模块。回调注入更窄、更可测
  （测试传一个 lambda 即可，不用造 `TeamSpec`）。

- **走 allocator（`build_model_allocator` + `allocate(model_name)`）做端点轮转。**
  需要把 allocator 的轮转计数器持久化进 session 才能跨 resume 一致，而 swarmflow
  background task 没接这条持久化路径。为「负载分散」这个当前不存在的需求引入有状态
  组件，是提前优化。

- **加 `TeamAgentSpec.worker` per-worker model spec 字段。** 那是「静态声明某个
  worker 角色固定用某模型」，与本特性的「per-call 动态 hint」是不同维度。F_27 已把
  spec 字段列为「留待需要时加」，本次不碰。

## 验证

```bash
source .venv/bin/activate && export PYTHONPATH=.:$PYTHONPATH
make test TESTFLAGS="tests/unit_tests/agent_teams/workflow/"
```

新增 `test_per_call_model_hint_routes_through_resolver`：脚本里三个 `agent()` 调用
（`model="fast"` 命中 resolver、`model="unknown"` 未命中、无 hint），断言 worker 实际
收到的 model 依次为 `fast-model` / leader / leader——一次覆盖命中、未命中回退、无 hint
回退三条路径。既有 17 个 workflow 用例全绿。

## 已知遗留

- **per-worker model spec 字段未加**（`TeamAgentSpec.worker`）：静态声明某 worker 固定
  模型仍未支持，继承自 F_27。
- **不做端点负载分散**：同名 model 在 pool 里有多个端点时，per-call 路由永远取首个
  （index 0），不轮转。需要分散时再引入有状态 allocator + session 持久化。
- **真实 LLM 端到端未自动验证**：与 F_27 同，单测覆盖到「链路接通」（resolver 回调
  spy），真实 leader + 多模型 worker 跑通需手动 / 系统测试。
