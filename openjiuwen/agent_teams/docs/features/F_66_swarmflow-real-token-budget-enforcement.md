# Swarmflow token budget：真实用量 + 硬约束落地

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-07-16 |
| 范围 | `openjiuwen/agent_teams/workflow/engine/{budget.py,runtime.py,runner.py,primitives.py,errors.py,__init__.py}`、`.../engine/backends/{base.py,mock.py}`、`.../workflow/backends/{budget_rail.py,team_worker_backend.py,avatar_session_backend.py}`、`.../workflow/{runner.py,tool_swarmflow.py}`、`.../schema/blueprint.py`、`.../agent/agent_configurator.py`、`.../rails/{team_context.py,elements.py,team_tool_rail.py}`、`.../tools/{tool_factory.py,tool_async.py}`、`.../tools/locales/descs/{cn,en}/swarmflow.md` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/`：改动前 1950 passed → 改动后 **1977 passed / 0 failed**（+27 = 新增 `workflow/test_budget.py` 21 条 + `tools/test_tool_variants.py` schema 回归 6 条）；E2E `agent_team_swarmflow_budget_e2e.py` 真实 deepseek 端点 **PASSED**（`rounds=9 workers=9 spent=6148/6000, 683 tokens/round`） |
| Refs | #1047 |

## 背景

`budget` 这套原语在脚本侧一直是**可读但不生效**的摆设。三处断裂，任何一处都足以让它归零：

1. **数字是假的**。`AgentResult.tokens` 由 `_estimate_tokens(prompt, result) = len(prompt)//4 + len(payload)//4` 拍脑袋算出来——它只看得见 `agent()` 的入参和返回值，**看不见 worker 自己那一圈 ReAct 循环**（系统提示词 + 若干次模型调用 + 工具结果）。E2E 实测同一轮：真实 ~600 token，该估算 ~50，差一个数量级。
2. **没人接线**。`run_swarmflow` 压根不接 `budget_total`，只有 `run_workflow(budget_total=)` 这个测试入口有。产品路径（leader → `swarmflow` 工具）上 `budget.total` **恒为 `None`**，`remaining()` 恒为 `None`。
3. **没有约束**。全仓 grep `budget_total` 只有定义和读取，没有任何一处 `if spent >= total`。工具描述自己都写着「（注：调用前硬拦截实现推进中，当前为计数 + 脚本自检。）」

### 核心洞察一：`int` 传不了引用，这才是硬约束做不出来的根因

`Runtime.tokens_spent: int` + `Runtime.budget_total: int | None` 两个字段的形状决定了结局。约束必须发生在**真正烧 token 的地方**——worker harness 自己的循环里；而 `int` 是不可变的，**没法把它交给 backend 装的 rail 去读写**。引擎能碰到的只有 `agent()` 的首尾两个瞬间，中间那一整圈它看不见也管不着。

于是把两个字段收成一个可共享引用的对象：

```python
@dataclass
class BudgetLedger:
    total: int | None = None
    spent: int = 0
```

数据结构一换，约束点自然浮现——账本传给谁，谁就能既记账又执法。这不是加了一层封装，是**换了个能被共享的形状**。

### 核心洞察二：谁知道真实成本，谁就该记账

引擎只看得见 `agent()` 的开始和结束，所以它只能猜（那正是 `len//4` 的来历）。真正知道一次调用花了多少的，是**发起模型调用的那一层**——backend。所以把账本的写权唯一地交给 backend（`AgentBackend.bind_budget`），引擎只读。

这条「单写者」规则同时消掉了双重计数：引擎里 `rt.tokens_spent += res.tokens` 那行必须删掉，否则 rail 记一次、引擎再记一次。`AgentResult.tokens` 随之退化为**单次调用成本的如实上报**（无人累加），语义反而更干净。

## 数据结构

```
BudgetLedger (engine/budget.py)   ← 每个 leader 一个，所有 run 共享
  ├─ total: int | None            ← TeamAgentSpec.swarmflow_budget
  ├─ spent: int                   ← 真实用量，由 backend 写
  ├─ remaining() / exhausted
  │
  ├── 引擎只读：agent() / send() 入口 _check_budget → BudgetExhausted
  ├── 脚本只读：budget.total / spent() / remaining()
  └── backend 写：
        ├─ TeamWorkerBackend.run → SwarmflowBudgetRail(每次调用一个)
        ├─ AvatarSessionManager  → SwarmflowBudgetRail(每个 session 一个)
        └─ MockBackend._report   → 离线估算(无模型可问)
```

`BudgetLedger` 放 `engine/` —— 它是「计数器 + 天花板」，零业务耦合，与 `engine/admission.py` 同性质（铁律 1 约束的是不耦合业务，不是只能 stdlib）。

**leader 级而非 run 级**：工具描述早就写明 `spent()`「跨主循环 + 所有工作流共享，非按工作流独立」，与 `ConcurrencyGovernor` 的 L3 同作用域。并发 run 抽同一个池，不是各拿一个天花板。

## 两级执法：rail 管圈内，引擎管圈间

单靠引擎那个入口 gate 是不够的——**一个 worker 自己就能在返回前把整个预算烧光**。两级缺一不可：

| 层 | 位置 | 时机 | 手段 |
|---|---|---|---|
| **rail**（主力） | `SwarmflowBudgetRail`，挂在每个 worker / avatar harness 上 | `after_model_call`：读 `usage_metadata` 记账，超了就停；`before_model_call`：付不起就不发起 | `ctx.request_force_finish` —— **就地终止 harness 的当前 round** |
| **引擎**（兜底） | `_check_budget`，紧挨 `_check_abort` | `agent()` / `AgentSession.send()` **入口** | `raise BudgetExhausted` |

- rail 用 **force-finish 而非抛异常**：超预算是「钱花完了」不是「坏了」，已做的工作照常返回。
- `before_model_call` 那一路专治**并发**：账本是共享的，兄弟 worker 把预算烧干时，本 worker 下一次调用直接被挡。
- 引擎的 gate **只在入口**，不做 pre-journal 检查（与 `_check_abort` 不同）：钱已经花了的调用必须落 journal，否则 resume 会重跑并**再付一次**。

### `BudgetExhausted` 为什么是 `BaseException`

与 `WorkflowAborted` 同款理由：脚本能用 `except Exception` 吞掉、然后继续 spawn agent 的天花板，不叫天花板。它要能穿透 `parallel()` / `pipeline()` 分支体的 `except Exception`。

但语义与 abort 相反——abort 是**可恢复**的暂停（resume 重跑），exhausted 是**终态失败**（重跑只会撞同一个 gate）。所以 `SwarmflowTool.run_background` 单独捕获它并转成 `BackendError`（普通 `Exception`），让 async-tool runtime 注入一条 leader 能读到的失败；直接放 `BaseException` 上去会把 task 静默杀掉。

### 允许小幅越界，且必须允许

一次调用的用量**只有返回后才入账**，所以最后那一次调用可以把 `spent` 顶过 `total`。`remaining()` 因此 clamp 到 0（不返回负数）。这是设计，不是 bug——要想不越界，就得在调用前预知成本，那不可能。E2E 的断言也据此写成 `spent < total * 2`（越界有界）而非 `spent <= total`。

## 配置入口：为什么不是工具参数

`swarmflow_budget` 落在 `TeamAgentSpec`，走 `agent_configurator → inject_team_handles → TeamToolRail → create_team_tools → SwarmflowTool → run_swarmflow`——与 `swarmflow_concurrency` 完全平行的链路。

**没有做成 `swarmflow()` 的工具入参**：花钱的上限是部署方的决定，不该由 leader 每次调用现编。工具描述里 `budget.total` 本来就写的是「用户本轮 token 目标」。

## 拒绝的方案

| 方案 | 为什么不 |
|---|---|
| 引擎继续累加 `AgentResult.tokens`，rail 只查不写 | 双重计数。要么 rail 返回 0（隐式约定，必烂），要么引擎不加。选后者：单写者，无特例 |
| rail 只记自己的 in-flight 量，靠 `ledger.spent + self._call_tokens` 判断 | 并发 worker 互相看不见对方的在途消耗，天花板被击穿的幅度随并发数放大。账本实时写才能互见 |
| 账本构造后传两遍（backend 一份、`run_workflow` 一份） | 同一个对象传两处，参数不一致就是静默错账。改成 `run_workflow` 单点 `bind_budget` 注入 |
| `AgentBackend` 用类属性存账本 | 可变类属性跨实例共享，经典 bug。走 `__init__` + `bind_budget` |
| 保留 `_estimate_tokens` 做「无 usage 时的兜底」 | 天花板的含义会随 provider 是否上报 usage 而变。不上报就记 0，宁可不管也不要管错 |
| E2E 直连 `run_swarmflow` 传 budget | 违反 E2E 套件 README 铁律（`tests/system_tests/agent_swarm/swarmflow/README.md`）（必须从 team 入口驱动 leader），且绕过整条配置链——链断了测不出来 |

## 顺带修掉的阻塞性 bug：`async_tasks_list` 的 null schema

E2E 一跑就 400：

```
Invalid schema for function 'async_tasks_list':
schema must be a JSON Schema of 'type: "object"', got 'type: null'.
```

`ToolCard.input_params` 默认 `{}`，而 `AsyncTasksListTool` 不带参数、也就没人给它赋值。空 dict 没有 `type` 字段 → 严格的 OpenAI 兼容端点（deepseek）**整个请求拒掉**，一个工具的 schema 缺陷把同一次调用里其余 11 个工具全带走，leader 一个工具都调不了。

与 budget 无关，但**卡死了目标里指定的模型配置**，故一并修：不带参数在 JSON Schema 里是 `{"type": "object", "properties": {}}`，不是 null。回归断言加进 `test_tool_variants.py` 的笛卡尔冒烟（该文件已在扫每个工具的 description，schema 是同一层的不变量）。

12 个 leader 工具里只此一个中招——不是系统性问题。

## 验证

**单测** `tests/unit_tests/agent_teams/workflow/test_budget.py`（21 passed）：账本计数/clamp/无界、引擎 gate（30/call × 100 上限 → 第 4 次跑完、第 5 次拒绝）、脚本按 `remaining()` 优雅收尾、backend 绑定、MockBackend 记账、rail 读 `usage_metadata`（含 `total_tokens` 缺失时回落 input+output 求和、无 usage 记 0）、force-finish 两路、无界不停、共享账本互见、spec 校验、handle 链直达工具。

**E2E** `agent_team_swarmflow_budget_e2e.py`（真实 deepseek 端点，从 leader 工具入口驱动，非直连引擎）：`budget_guard.py` 循环上界 30 轮、天花板 6000 token。实跑结果：

```
round=1 cost=563 … round=8 cost=604 spent=5116 remaining=884
round=9 cost=1032 spent=6148 remaining=0
[swarmflow] token budget exhausted (6148/6000); finishing agent round   ← rail 就地掐停第 9 个 worker
budget spent after 9 rounds (remaining=0 < worst_round=1032) — stopping ← 脚本据实测收尾
[budget] verified: rounds=9 workers=9 spent=6148/6000 (683 tokens/round)
[budget] E2E PASSED
```

两级执法都真实触发了：rail 掐停了跨线的 worker（日志里那条 warning），脚本随后按 `remaining()=0` 收尾。每轮 683 token 是真数（同一轮按长度估算只会给 ~50——这正是断言 `per_round > 200` 守的东西）。`spent=6148 > 6000` 是设计内的越界。断言：真实用量、非 30 轮上界终止、`spent >= total*0.5`、`spent < total*2`、engine 报的 worker 数 = 脚本轮数。

**回归**：全量 `tests/unit_tests/agent_teams/` **1977 passed / 0 failed**（改动前 1950，新增 27 条即本特性的单测）。

## 已知遗留

- **`preprocess_swarmflow`（MockBackend 预演）不接账本**，预演永远无界。预演不打真实模型，天花板对它无意义。
- **`AgentResult.tokens` 目前只有 MockBackend 自己读**（喂它自己的账本）。真实 backend 如实上报单次成本，但暂无消费方——留给后续 journal / 每次调用成本归因用。
- **`spent` 不落 journal**：resume 命中缓存的调用不重新计费（本就没花钱），但账本从 0 起算，跨 resume 的累计花费无法还原。真要做需要把 `spent` 写进 journal 元信息。
- **`swarmflow_budget` 只有 leader 级**，无法给单个 run 指定更小的上限。需要时再加 run 级 override，别提前设计。
