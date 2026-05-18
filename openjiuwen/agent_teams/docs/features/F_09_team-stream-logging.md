# Team Stream Logging

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-15 |
| 范围 | `openjiuwen/agent_teams/monitor/stream_logger.py`（新增）、`openjiuwen/agent_teams/monitor/__init__.py`、`openjiuwen/core/runner/team_runner.py`、`tests/unit_tests/agent_teams/monitor/`（新增）、`tests/unit_tests/agent_teams/test_runner_team_runtime.py`、`docs/specs/S_01`、`docs/specs/S_14`、`agent_teams/CLAUDE.md` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/monitor/ tests/unit_tests/agent_teams/test_runner_team_runtime.py` 全绿 |
| Refs | #751 |

## 背景

`F_02_member-attributed-streaming` 之后，`Runner.run_agent_team_streaming` 已经能从
leader 一条流里流出 leader + 全体 inprocess teammate 的 chunk，且每个 chunk 都带
`source_member` / `role` 标签（`TeamOutputSchema`）。但这条流此前**没有任何落盘日志**：
团队多角色协作出问题时，思考过程、工具调用、文本返回、中断信号全在内存里过一遍就没了，
排查只能靠复现 + 加临时 print。

需要一个能把流式过程**可读地**落到一个独立诊断文件的设施，用于事后定位团队流程。约束：

- 必须**先聚合再写入**——token 级 chunk（`llm_output` / `llm_reasoning`）逐个写文件会把
  日志刷爆且不可读。
- 必须明确标识**成员名 + 角色**，并能正常输出**多行 markdown**（模型输出本身就是 markdown）。
- 级别要分层标记：文本 INFO、思考/工具 DEBUG、中断/失败 WARN（作为明文 `[LEVEL]` 标签写入
  文件，不需要 `team_logger` 的运行时过滤）。
- runner 调用方要能**开关**，并能指定**目标文件路径**——`tail -f` 一份独立诊断文件比夹在
  框架日志里翻方便得多。

## 数据结构 / 状态机

不新增对外数据模型——复用 `OutputSchema` / `TeamOutputSchema`（`source_member` / `role`）/
`TeamRole`。新增一个处理对象 `TeamStreamLogger`，内部状态按 **source 分桶**：

```
_runs: dict[(member, role), _Run]                # 每个 source 一个独立累积段
_llm_output_seen: set[(member, role)]            # answer 去重门控，per-source
_chunk_count: int                                # flush 时写一行收尾
_file: TextIO | None                             # __init__ 打开，flush() 关闭

_Run = dataclass(category: str, buf: list[str])  # 当前累积段的 category + token buffer

feed(chunk):
  key = (chunk.source_member, chunk.role)
  累积型 chunk -> 若 _runs[key].category != 当前 category 则先 flush _runs[key]，再追加
                 不同 key 的累积段互不打断
  离散型 chunk -> 先 flush _runs[key]（仅该 source 的段），再立即写一条记录到文件
flush():
  flush 所有 _runs 里残留的累积段；写一行 "stream end"；close 文件
```

`category` 是 chunk `type` 到日志类别的映射；级别由 `_CATEGORY_LEVEL` 表声明式给出。
记录格式 = 一行 header（`<ts> [LEVEL] member=… role=… category=…`）+ 每行 `  | ` 前缀的
原始内容块，直接 `file.write` + `file.flush` 到调用方指定的文件。

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

### 4. 聚合**按 source 独立维护**，不用单一游标

`harness/cli/ui/renderer.py` 的 `render_stream` 用单一游标 + 类型变化触发换行——那适合
**串行**渲染到 terminal。但 team 流式里 inprocess fan-out 让 leader 和 teammate 的 chunk
**按时间交错**到同一条 stream，单一游标遇到每个跨 source 的切换都会 flush，结果是每个
token chunk 落成一条独立记录（实测复现：50 个 chunk = 50 条日志，完全失去聚合意义）。

本 logger 用 `dict[(member, role), _Run]`，每个 source 独立维护一个累积段。同一 source
切换 category 或出现该 source 的离散 chunk 时 flush 该段；不同 source 的 chunk 交错**互
不影响**。这是 "good data structure eliminates the special case"：跨成员切换不再是边界
事件，只是别人的事。

### 5. `feed` / `flush` 永不向流式路径抛异常

两个方法整体 `try / except Exception`，失败时 best-effort 把一行 `[WARN] ... error: ...`
标记写回**自己**的输出文件；写不进去就静默吞。`__init__` 不在豁免范围——路径不可用直接抛，
让调用方在构造时立刻发现，符合"fail fast at construction"。诊断 logger 是旁路观察者，
自己挂掉绝不能搞挂它观察的 stream——与 S_14 不变量 2 同源。

### 6. 对齐 CLI renderer 的 chunk 处理语义，但去重按 source 维度

chunk 类型词表、`_extract_content`（`payload.content` / `payload.output`）、
`controller_output` 的 `task_failed` 提取——都对齐 `renderer.py`。**唯一差异**：`answer`
去重门控 `_llm_output_seen` 是 per-source 集合，不是全局 bool——CLI renderer 串行渲染
一份输出，全局 bool 够用；本 logger 多 source 并行，全局 bool 会让一个 source 的
`llm_output` 误屏蔽其它 source 的 `answer`。

### 7. 截断只砍工具产物，不砍模型输出

`tool_result` / `tool_args` 内容超阈值截断（读文件类工具能甩出整个文件，会淹没日志）；
`llm_output` / `llm_reasoning` / `answer` **永不截断**——诊断日志的核心价值就是模型完整输出。

### 8. 直接写文件，不走 `team_logger`

构造时 `open(file_path, "a", encoding="utf-8")`，每条记录 `file.write(...)` + `file.flush()`，
`flush()` 时 `file.close()`。**不走 `team_logger`** 的原因：

- 用户想要一份**独立可 `tail` 的诊断文件**，跟框架其它日志分开，不被日志级别全局过滤、
  不被 rotation 切掉关键流程，路径由调用方完全控制。
- 没有 formatter 之后，模型输出 / JSON 工具参数里的字面 `{}` / `%s` 不会被任何模板引擎
  误解析——零注入面，比走 logger 还安全。
- 级别仍以明文 `[INFO]` / `[DEBUG]` / `[WARN]` 标签写入每行 header，grep 友好；不需要
  运行时过滤（诊断文件本来就该全量留痕）。

### 9. 跳过裸 `OutputSchema` + 工具字段缺失时回退到 payload dump

实测线上一份运行日志后发现两类污染：

1. **裸 `OutputSchema` 漏进 stream**：`core/session/agent_team.py:_normalize_output_stream`
   把任何不是 OutputSchema 的 dict 包成 `OutputSchema(type="message", payload=data)`，
   tracer subsystem 通过 stream writer 写 `Span.model_dump()` 时就会以这种形态漏到运行
   stream 里。这些 chunk 没经过 `StreamController._tag_chunk`，全是裸 `OutputSchema`、
   不是 `TeamOutputSchema`，member/role 都是 None。它们不属于团队成员输出（是 trace 基础
   设施），但 payload 体积巨大（`traceId` / `invokeId` / `inputs` / `outputs` 全在里面），
   留在日志里直接淹没真正的团队流程。**决策**：在 `_feed` 第一步 `isinstance(chunk,
   TeamOutputSchema)` 检查未通过就 return（chunk 计数仍 +1，只是不写记录），把 logger
   严格收敛到经过 team 标签的 chunk。
2. **`tool_call` / `tool_result` 落空字段**：`harness/cli/rails/tool_tracker.py` 是
   规范发射点（payload 顶层 `tool_name` / `tool_args` / `tool_result`），但实际运行
   `ctx.inputs` 在某些路径下不带这些字段 → 原本的 `payload.get("tool_name", "")` 取空 →
   记录显示 `tool_name= tool_args=` 两个空字段。**决策**：`_tool_call_summary` /
   `_tool_result_summary` 检测到所有标准字段都空时，fallback 为整个 payload 的 capped
   字符串——保证记录始终有可读内容。
3. **`tool_update` 被归到 `other`**：第三方 rail（如 jiuwenclaw `stream_event_rail`）
   以 `type="tool_update"` 发射 tool call 进度通知，payload shape
   `{"tool_update": {"tool_name", "tool_call_id", "arguments", "status"}}`。logger 原本
   不识别这个 type → 走 `category=other` dump 整个 payload，可读性差。**决策**：把
   `tool_update` 显式列为已知类型（category=`tool_update`，DEBUG），抽出嵌套字段写一行
   `tool_name=… status=… tool_call_id=… arguments=…`。

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

### 方案 E：单一游标模型（仿 CLI renderer）

最初实现照 `render_stream` 抄了"单一游标 + 类型/source 变化时 flush"。**拒绝理由**：CLI
是串行渲染到 terminal、每秒几行的人类阅读速度，跨 source 切换 flush 没问题；team 流式
是 inprocess fan-out 后 leader/teammate token chunk 时间维度交错（实测每条 chunk 隔几十
毫秒），单一游标每跨 source 切换都 flush，结果聚合彻底失效。改用 `dict[(member, role), _Run]`
后跨 source 不再是切换、是并行存在，逻辑天然干净。

### 方案 F：用 `team_logger` 作为输出 sink

最初实现把记录通过 `team_logger.{info,debug,warning}` 落到框架日志。**拒绝理由**：

- 用户想要一份**独立可 tail** 的诊断文件，混在框架日志里得 grep 才能挑出团队流程，体验差。
- 框架日志有全局级别过滤；DEBUG 在生产里通常被关掉，思考过程/工具调用就丢了——诊断
  logger 不该被全局过滤策略影响。
- `team_logger` 的 formatter 处理 `{}` / `%s` 占位符，模型输出 / JSON 工具参数里的字面
  括号会触发奇怪 case；直接写文件无 formatter，零注入面。
- 路径由调用方完全控制（构造参数 `file_path`），跟 DI 设计一致。

## 验证

- `tests/unit_tests/agent_teams/monitor/test_stream_logger.py`（新增）：同 source 连续
  `llm_output` / `llm_reasoning` 聚合、**两 source 交错聚合各成一条**（核心回归用例）、
  同成员不同 role 分桶、`flush` 收尾、`answer` 去重在同 source 内丢弃 + **跨 source 不
  误丢**、级别路由参数化、`runtime_ready` 特判、`tool_result` 截断 vs `llm_output` 不
  截断、多行 markdown 原样保留、裸 `OutputSchema` 兜底 `<unknown>`、`feed` / `flush`
  遇坏 chunk 写文件标记不抛、header 格式契约、父目录自动创建、`flush()` 关闭文件。
- `tests/unit_tests/agent_teams/test_runner_team_runtime.py`（追加 2 个 e2e 用例）：不传
  `stream_logger` 时目标路径不被创建；传入时文件被写入且流本身不被扰动。
- 基线：上述两处全绿。`ruff check` / `ruff format` 干净；mypy 对新模块无报错
  （`team_runner.py` 的报错均为既有 mixin 模式噪声，与本次改动无关）。

## 已知遗留

- **subprocess 模式 teammate 不被记录**：subprocess teammate 的 chunk 留在子进程内、不
  fan-out 到 leader（`F_02` 已知遗留）。等跨进程 chunk 转发通路落地后，leader 路径的
  `stream_logger` 自然就能覆盖到。
- **CLI 未接入**：`cli/stream_renderer.py` 目前不构造 `TeamStreamLogger`。给 `/team start`
  之类加一个"同时落诊断日志"的开关是后续的小改动，不在本次范围。
- **日志类别词表是字符串常量**：`OutputSchema.type` 在 core 层没有规范枚举，本模块和
  `renderer.py` 各自声明一份字符串常量。若 core 层后续给 chunk type 出枚举，两处应收敛到
  同一来源。
