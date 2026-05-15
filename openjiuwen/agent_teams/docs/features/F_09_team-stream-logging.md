# Team Stream Logging

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-14 |
| 范围 | `openjiuwen/agent_teams/monitor/stream_logger.py`（新增）、`openjiuwen/agent_teams/monitor/__init__.py`、`openjiuwen/core/runner/team_runner.py`、`tests/unit_tests/agent_teams/monitor/`（新增）、`tests/unit_tests/agent_teams/test_runner_team_runtime.py`、`docs/specs/S_01`、`docs/specs/S_14`、`agent_teams/CLAUDE.md` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/monitor/ tests/unit_tests/agent_teams/test_runner_team_runtime.py` 74 passed |
| Refs | #751 |

## 背景

`F_02_member-attributed-streaming` 之后，`Runner.run_agent_team_streaming` 已经能从
leader 一条流里流出 leader + 全体 inprocess teammate 的 chunk，且每个 chunk 都带
`source_member` / `role` 标签（`TeamOutputSchema`）。但这条流此前**没有任何落盘日志**：
团队多角色协作出问题时，思考过程、工具调用、文本返回、中断信号全在内存里过一遍就没了，
排查只能靠复现 + 加临时 print。

需要一个能把流式过程**可读地**记到 `team_logger` 的设施，用于事后定位团队流程。约束：

- 必须**先聚合再打印**——token 级 chunk（`llm_output` / `llm_reasoning`）逐个打日志会把日志
  刷爆且不可读。
- 必须明确标识**成员名 + 角色**，并能正常输出**多行 markdown**（模型输出本身就是 markdown）。
- 级别要分层：文本 INFO、思考/工具 DEBUG、中断/失败 WARN。
- runner 调用方要能**开关**。

## 数据结构 / 状态机

不新增数据模型——复用 `OutputSchema` / `TeamOutputSchema`（`source_member` / `role`）/
`TeamRole`。新增一个处理对象 `TeamStreamLogger`，内部是一个小状态机：

```
当前累积段 = (_member, _role, _cat, _buf)        # 仅 llm_output / llm_reasoning 入 _buf
_has_llm_output: bool                            # answer 去重门控，贯穿整个 run
_chunk_count: int                                # flush 时打一行收尾

feed(chunk):
  累积型 chunk -> 若 (member,role,category) 变化则先 flush 旧段，再追加进 _buf
  离散型 chunk -> 先 flush 待定累积段，再立即单独落一条
flush():
  flush 末尾未结的累积段
```

`category` 是 chunk `type` 到日志类别的映射；级别由 `_CATEGORY_LEVEL` 表声明式给出。
日志记录格式 = 一行 header（`[team.stream] member=… role=… category=…`）+ 每行 `  | `
前缀的原始内容块。

## 决策

### 1. 归属 `agent_teams/monitor/`，不放 `core/runner/`

流式诊断日志是团队运行态可观测性，归属 monitor 子系统。`core/runner/team_runner.py`
只是它的**调用方**，不是它的归属地——按"领域所有权"放置，不按"谁触发"放置。模块进
`monitor/` 后也自然纳入 S_14 的契约范围。

### 2. 依赖注入：调用方构造、runner 只 feed/flush

runner 新增 `stream_logger: TeamStreamLogger | None = None` 入参（实例方法 + classmethod
facade 两处）。传对象 = 开，传 `None` = 关——开关就是对象本身的存在性，不需要额外的 bool
flag。runner **不 import、不构造** `TeamStreamLogger`，只在 `TYPE_CHECKING` 下引用类型，
循环里 `feed`、`finally` 第一行 `flush`。构造责任完全在调用方（CLI / SDK 用户）。

### 3. 真实类型导入，不鸭子类型

`stream_logger.py` 直接 `import` `OutputSchema` / `TeamOutputSchema` / `TeamRole`，用
`isinstance(chunk, TeamOutputSchema)` 取标签、带类型注解访问 `chunk.type` / `chunk.payload`。
不用 `getattr(chunk, "source_member", None)` 这种为了省一个 import 的鸭子类型——类型契约
显式、可被 mypy 检查。裸 `OutputSchema`（无标签）走 `<unknown>` 兜底。

### 4. 聚合边界比 CLI renderer 多 member/role 维度

`harness/cli/ui/renderer.py` 的 `render_stream` 只在 chunk `type` 变化时换行。本 logger
的累积段边界额外纳入 `member` / `role`：inprocess fan-out 进来的 teammate chunk 可能插在
leader 两个 `llm_output` 之间，不按成员切就会把 teammate 的话粘进 leader 的文本块。

### 5. `feed` / `flush` 永不向流式路径抛异常

两个方法整体 `try / except Exception` -> `team_logger.exception` 留痕后返回。诊断日志
是旁路观察者，自己挂掉绝不能搞挂它观察的 stream——与 S_14 不变量 2 同源。

### 6. 对齐 CLI renderer 的 chunk 处理语义

chunk 类型词表、`_extract_content`（`payload.content` / `payload.output`）、`answer` 在
见过 `llm_output` 后丢弃的去重、`controller_output` 的 `task_failed` 提取——都对齐
`renderer.py`，保证 CLI 看到的和日志记到的是同一套语义。

### 7. 截断只砍工具产物，不砍模型输出

`tool_result` / `tool_args` 内容超阈值截断（读文件类工具能甩出整个文件，会淹没日志）；
`llm_output` / `llm_reasoning` / `answer` **永不截断**——诊断日志的核心价值就是模型完整输出。

### 8. 防花括号注入

模型输出 / JSON 工具参数里有字面 `{}`。`_emit` 用纯 Python f-string 拼好整块文本，再以
**单个位置参数**传给固定模板 `team_logger.<level>("{}", block)`——内容永远是参数、不是
模板，不会被 logger 的 formatter 误解析。

## 拒绝的方案

### 方案 A：放 `core/runner/team_stream_logger.py` + 鸭子类型

最初计划把模块放在 runner 旁边、用 `getattr` 鸭子类型避免 `import agent_teams`。**拒绝
理由**：`core/` 不该吸收 team-specific 的领域代码；鸭子类型只是为了绕开一个本来就该有的
import，让类型契约变隐式、躲过 mypy。归属 `monitor/` 后这些 import 全是子系统内的正常依赖，
没有循环导入问题。

### 方案 B：runner 加 `log_stream: bool` 开关、自己构造 logger

让 runner 自己 `if log_stream: TeamStreamLogger()`。**拒绝理由**：runner 要为此 import
`agent_teams.monitor`，反向耦合；且把"构造哪个观察者"的决策权从调用方夺走。依赖注入让
runner 对具体实现一无所知，未来换别的处理对象也不用动 runner。

### 方案 C：wrapper async generator（仿 CLI 的 `_wrap_stream`）

把日志做成包装 `run_agent_team_streaming` 输出的 async generator。**拒绝理由**：runner 自身
就是 generator + try/finally，包一层 generator 要么在 runner 外（CLI 参考实现就是这样，但
那是 CLI 的活）、要么和既有 finally 缠在一起。`feed` / `flush` 是同步方法（打日志本就同步），
塞进既有 try/finally 是 4 个插入点的最小 diff，且 logger 能脱离 event loop 单测。

### 方案 D：member=True / base=True 路径也接

`run_agent_team_streaming` 的 spawned-teammate（`member=True`）与 `BaseTeam`（`base=True`）
路径不接 `stream_logger`。**拒绝理由**：inprocess teammate 的 chunk 已经 fan-out 到 leader
流，leader 路径一处接日志就覆盖全员；member 路径单独接会重复记。subprocess teammate 的
跨进程 chunk 转发本就是 `F_02` 的已知遗留，等那条通路落地再说。

## 验证

- `tests/unit_tests/agent_teams/monitor/test_stream_logger.py`（新增，17 个用例）：连续
  `llm_output` / `llm_reasoning` 聚合、member/role 变化打断累积、`flush` 收尾、`answer`
  去重两条路径、级别路由参数化、`runtime_ready` 特判、`tool_result` 截断 vs `llm_output`
  不截断、多行 markdown 原样保留、裸 `OutputSchema` 兜底 `<unknown>`、`feed` / `flush`
  遇坏 chunk 不抛、header 格式契约。
- `tests/unit_tests/agent_teams/test_runner_team_runtime.py`（追加 2 个用例）：不传
  `stream_logger` 时 `team_logger` 零调用；传入时 ≥1 条记录且流本身不被扰动。
- 基线：上述两处合计 **74 passed**。`ruff check` / `ruff format` 干净；mypy 对新模块无
  报错（`team_runner.py` 的报错均为既有 mixin 模式噪声，与本次改动无关）。

## 已知遗留

- **subprocess 模式 teammate 不被记录**：subprocess teammate 的 chunk 留在子进程内、不
  fan-out 到 leader（`F_02` 已知遗留）。等跨进程 chunk 转发通路落地后，leader 路径的
  `stream_logger` 自然就能覆盖到。
- **CLI 未接入**：`cli/stream_renderer.py` 目前不构造 `TeamStreamLogger`。给 `/team start`
  之类加一个"同时落诊断日志"的开关是后续的小改动，不在本次范围。
- **日志类别词表是字符串常量**：`OutputSchema.type` 在 core 层没有规范枚举，本模块和
  `renderer.py` 各自声明一份字符串常量。若 core 层后续给 chunk type 出枚举，两处应收敛到
  同一来源。
