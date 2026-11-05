---
id: F_20261105_1405_otel-decorator-llm-telemetry
title: 装饰器 llm span 遥测纯新增（弃外保内）
type: F
author: yidaaichixia
author_email: 1046386000@qq.com
modules:
  - core.session.tracer
  - core.runner.resources_manager
  - extensions.tracer_otel
files:
  - openjiuwen/core/session/tracer/decorator.py
  - openjiuwen/core/runner/resources_manager/model_manager.py
  - openjiuwen/core/runner/resources_manager/resource_manager.py
  - openjiuwen/extensions/tracer_otel/handler.py
  - tests/unit_tests/core/session/tracer/test_decorator.py
  - tests/unit_tests/core/runner/resources_manager/test_model_manager.py
  - tests/unit_tests/extensions/tracer_otel/test_handler.py
designs: []
design_note: 方案在遥测重构评审讨论与 GitCode PR 线程（!3570/!3571 撤回后重做为纯新增线）中确认，无独立 D 文档。
---

# 装饰器 llm span 遥测纯新增（弃外保内）实施与决策归档

## 关联依据

- 设计：无独立 D 文档；架构结论为「弃外保内」——内层真实 llm span 作为计数与身份载体（本特性），外层桥接/shell span 保持原样不动，层间双计由后端规则（优先计子、无子再计桥）去重。
- spec：`extensions/tracer_otel` 模块 AGENTS（属性键独立定义、handler 副作用隔离、红线不动全局 OTel 状态）继续有效，本次未新增 spec 约束。
- 日期：2026-11-05。
- 实际实施范围：
  - C1 `core/session/tracer/decorator.py`：`decorate_model_with_trace` 装饰器（含 TTFT 单调自计时、TPOT token 基准优先、流 chunk 跟踪、usage 快照、`_llm_call_info` 请求参数对齐、`identity` 平铺合并），从已撤回的 merge 线整体 salvage。
  - C2 `extensions/tracer_otel/handler.py`：`_set_llm_response_attrs` 容器级 `usage_metadata` sibling 提取（修 unwrap 钻入 chunk 列表后 usage 丢失 + 非 dict 早退缺口）；`_set_agent_identity_attrs` 消费 `workflow_id`/`workflow_name`（写缺才写）、metadata 首写优先、`component_*` 刻意不映射。
  - C3 `OtelAgentHandler.drop_agent_span`：best-effort 关闭仍开着的 span（agent-studio !2583 RAG 检索降级路径依赖此钩子）。
  - C4 类级能力常量 `OtelAgentHandler.LLM_SPAN_DECORATOR_CAPABILITY = 1`，供 agent-studio `trace_compat.py` 门禁从失效的 `LLM_SPAN_MERGE_CAPABILITY`（merge 线已撤回）重指向。
  - C5 装饰器双 handler 半开兜底（2026-11-05 增补，用户部署实证缺陷 ②）：invoke/stream 两 handler 增加 `ended` 标记 + `finally` 补偿——`CancelledError`/`GeneratorExit` 是 BaseException、绕过 `except Exception`，此前 span 停在半开、挂载的 ambient 上下文泄漏、后续 span 认错父；补偿以 best-effort 触发 `on_*_end`（流式带已见部分结果与 usage，非流式 `outputs=None`），失败吞掉不破坏取消语义。
  - C6 `ModelMgr.get_model` 增补 `identity: dict | None = None` 透传给 `decorate_model_with_trace`（2026-11-05 增补，用户部署实证缺陷 ①）：嵌入侧（studio runtime 双写桥）拿到的是裸会话、无 agent card，控制器层 agent 身份只能经显式参数带给 llm span；缺省 `None` 保持全部存量调用方的原 wire 形态。
  - C6 补透公共入口三环（2026-11-05 复审修正，用户发现）：嵌入侧拿模型的真实入口是 `Runner.resource_mgr.get_model`（`ResourceMgr`），原 C6 只改了叶子 `ModelMgr.get_model`——入口 keyword-only 固定签名不收 identity，嵌入侧传参直接 TypeError（模型创建失败，比遥测丢失严重）。`ResourceMgr.get_model` / `_inner_get_resources_by_provider` / `_dispatch_get` 三处 keyword-only 增补 `identity=None`；`_dispatch_get` 仅 `resource_type == "model"` 且显式非 None 时转发，其余资源类型的 get 不收到该参数，共享分发契约不被单类型参数污染。

## 决策归属与关系

- merge 线（!3570/!3571 原内容：ambient shell、rail 合并、respect_existing 等 span 合并机制）整体撤回，本地备份于 `backup/2026-11-05-*` 四个 ref；decorator 模块 diff 与合并机制无关，整体取用。
- 本文档为新决策记录：计数与身份全部落在装饰器新建的内层真实 llm span 上，外层 span（!2523/!2524 已合入的桥面数据）保持不动，后端负责去重。

## 上下文与跨边界影响

- 产生方：decorator 在模型调用内层触发 tracer 事件；`extensions/tracer_otel` handler 消费。
- 消费方：后端按 span name 区分桥/内层（当前即可区分），去重规则需在 studio 侧激活前/同时上线，否则层间双计窗口。
- 跨仓依赖：agent-studio S1 门禁 `trace_compat.py` 当前指向已不存在的 `LLM_SPAN_MERGE_CAPABILITY`（死开关），需重指向 `LLM_SPAN_DECORATOR_CAPABILITY`；!2583（RAG retrieval span）依赖本特性 C3 `drop_agent_span`。发布顺序：core 先、studio 后；merge 顺序无依赖（探针双向退化安全）。

## 实际实现与开发决策

- **usage 走容器级 sibling 而非嵌入消息**：流式 chunk 列表本身无法携带 usage，装饰器在容器层放 `usage_metadata` sibling；handler 先读 sibling、再回退到 unwrap 后对象的嵌入 usage（rail 路径不变）。sibling 是装饰器最新快照，优先于消息内（可能滞后）的 usage。
- **TTFT 单调自计时**：不依赖模型层时间戳（模型层缺失时仍可得），usage 内已有 `ttft_seconds` 时不覆盖。
- **TPOT token 基准优先**（`output_tokens - 1` 为分母），无 token 数时回退 chunk 基准，均无则不写。
- **`_llm_call_info` 与 rail 基线消费对齐**：develop 基线 `on_llm_start` 已消费 `request_params`（temperature/top_p/top_k/max_tokens/stop_sequences/reasoning_level）与 `message_count`，装饰器按同键提供，handler 端零新增消费逻辑。
- **身份键写缺才写 + metadata 首写优先**：起止两次转发同键冲突时保留起点值；基线 rail 路径不携带这些键，写缺才写在既有路径上惰性，无行为变化。
- **`component_*` 刻意不映射**：`openjiuwen.workflow.component_id` 是后端的 shell 判别键，内层 llm span 不得认领，否则去重规则失效。
- **develop 基线已含 `ttft_seconds` 优先读取**（#1833 引擎链路落地），C2 因此比计划少一块；0.1.17_hotfix 基线无该块，hotfix 复刻时 C2 需按该基线行文适配。
- **C5 兜底走 `on_*_end` 而非 `on_*_error`**（2026-11-05）：弃流/取消是调用方选择而非模型故障，语义上按「带部分结果的收尾」关闭；补偿内只 await 不 yield，若仍失败（真实取消链路可能再抛）吞掉——遥测不得改变取消语义。
- **C5 弃流补偿的时序是 best-effort**（2026-11-05 实测）：经完整 proxy 链 `aclose()` 时内层 handler 的 finally 由 asyncio asyncgen finalizer 钩子调度（GC 时机），生产可用但测试不可依赖；单测直接驱动 handler（`_make_trace_stream_wrap_handler`），代理链的同步关闭不属于本契约。
- **C6 `identity` 是显式参数而非复用 session**：裸会话无 agent card，rail `_build_common_info` 路径无从取身份；显式传递不改动 session 结构，且 `None` 缺省使存量调用方 wire 完全不变。
- **跨层透传的验收必须打到公共入口**（2026-11-05 修正的教训）：studio 探针 `_decorator_accepts_identity` 查 `decorate_model_with_trace` 签名——双子带上该参数后门开，但 ResourceMgr 入口断链，"新 core + 新 studio"组合反而 TypeError。签名探测只证明叶子改了、不证明链路通；参数透传类改动的用例必须从嵌入侧真实入口（`Runner.resource_mgr.get_model`）打到叶子，本次 ResourceMgr 用例即按此补。
- **C7 嵌入桥 llm span 父级走显式 refs，显式 > 逻辑 > 环境三级**（2026-11-05 部署实证 ② 修复）：嵌入双写桥的 llm span 逻辑父不在 agent handler 的 span manager 表里，环境回退又被链路上下文污染破坏（部署实证：llm span 与 node span 并列、误挂工作流死根；`create_agent_session` 实证会把 current span 换成 recording=False 的死根）。studio 在 `AgentCoreModelLayer.invoke/stream` 同步入口（桥线程启动前、node executor 上下文内）捕获当前 span 的 `(trace_id, span_id, trace_flags)`，随 kwargs 走与 agent_id 同一条 `get_model(identity=)` 通道（C6）；core `on_llm_start` 据显式 refs 构造 `NonRecordingSpan` 父上下文，refs 缺失/非法（非 hex / 全零）时逐位回退既有解析，行为不变。flags 缺省按已采样：显式 refs 只来自活着的 recording span，宁可多采不丢数据。
- **C8 会话重绑清空工作流映射（未修，独立 bugfix 线）**（2026-11-05 根因钉死）：裸会话 lazy `session.tracer()` → `Tracer.init()` 对全局注册表 handler 扇出 `set_session_id`；WF handler 见到与缓存不同的会话 id 即 `_cleanup_stale_spans` 清空 `_layer_root_spans/_component_spans` 并强关活 span。嵌入侧每次模型调用都建裸会话（会话 id 常与外层会话不同），于是工作流执行中途映射被清、后续组件解析落环境回退（部署实证 ③ 的结构性成因：end 节点误挂；伴随 token 异 Context ValueError 与双端 end 噪声，同族）。修复需要区分「真会话切换」与「嵌入裸会话瞬态翻转」（候选：handler 按 Tracer 实例化而非全局共享 / cleanup 按存活语义收敛），涉及跨会话隔离设计，不并入本线。

## 与设计的偏差

- 原 merge 线方案（层合并、桥面回滚）经评审撤回，改为本纯新增线；方向级变化已在 PR 讨论确认（2026-11-05）。除此之外无偏差。

## 实际执行与证据

| 执行日期 / 验证范围 | 实现版本与环境 | 命令 / 复现步骤 / 记录链接 | 实际结果 | 未覆盖项 / 失败原因 |
| --- | --- | --- | --- | --- |
| 2026-11-05 tracer_otel + decorator 单测 | feature/otel-decorator-llm-telemetry（C1–C4）本地；.testenv（openjiuwen 可编辑安装指向本工作区，Python 3.12） | `/Users/lily/ai-app/jiuwen/.testenv/bin/python -m pytest tests/unit_tests/extensions/tracer_otel/ tests/unit_tests/core/session/tracer/test_decorator.py -q` | 通过：172 passed（含新增 5 项 sibling/workflow 身份/首写优先/能力常量单测，3.68s） | 端到端 collector 链路（跨仓联调范围） |
| 2026-11-05 C5/C6 增补单测 | 同上（C1–C6） | 同命令 + `tests/unit_tests/core/runner/resources_manager/`，`PYTHONPATH` 指向本工作区 | 通过：179 passed（新增 3 项：弃流补偿 fires end with partial、取消补偿 fires end with outputs=None、get_model identity 透传）；stash 源码修复后 3 项必红、恢复后全绿 | 端到端 collector 链路（跨仓联调范围） |
| 2026-11-05 C6 补透 ResourceMgr 入口 | feature/otel-decorator-llm-telemetry（wt-twin-dev 工作区） | `PYTHONPATH=wt-twin-dev .testenv pytest tests/unit_tests/core/runner/resources_manager/ tests/unit_tests/core/session/tracer/ tests/unit_tests/extensions/tracer_otel/ -q` | 通过：225 passed；ResourceMgr 两用例修复前必红（TypeError 与生产缺陷同型：入口/dispatch 拒收 identity），修复后全绿 | 端到端 collector 链路（跨仓联调范围） |
| 2026-11-05 C7 显式父级 | 同上（wt-twin-dev 工作区 + agent-studio 主 checkout b7d31bfb7 起改） | core：`pytest tests/unit_tests/extensions/tracer_otel/ -q` 169 passed（新增 4 项：refs 生效/缺省逐位回退/非法忽略/显式优先于逻辑父，前两项与末项修复前必红）；studio：wrapper+integration 123 passed（refs 透传 5 项、layer 捕获/注入/默认名 8 项，refs 转发 2 项与 layer 文件修复前必红）；端到端 `topo_fix.py`（真链 layer→wrapper→identity→core）证 ② llm 父 == node span 且同 trace、① gen_ai.agent.id/name 带上、本链 ③ 未复现（llm 及时关窗） | 对照：旧 core 同链 node-end 亦误挂 node-llm（C8 既有行为，非本次回归；core 全量单测失败集与基线 diff 一致，22 项均为 a2a/tensordict 缺依赖环境噪声） | 端到端 collector 链路（跨仓联调范围） |

### 可见行为、副作用与证据有效范围

- 单测经 InMemorySpanExporter 独立读取最终 span 属性：sibling usage 提取（含 TTFT/TPOT 属性与 finish_reason 跳过语义）、sibling 优先于嵌入 usage、身份 workflow 键写入与 component_* 排除、写缺才写/首写优先、能力常量存在性。
- 未执行：真实 collector/后端链路的端到端采集验证，属于 agent-studio 激活与后端去重规则上线后的联调范围。

## 验证结论

- 已证明范围：sibling usage 提取（含 TTFT/TPOT 属性、chunk 列表不中断读取、finish_reason 跳过）、sibling 优先于嵌入 usage、workflow 身份键写入与 component_* 排除、写缺才写/首写优先、能力常量存在——均为 InMemorySpanExporter 独立读取的 span 属性断言。
- 未覆盖或未通过的承诺：端到端联调（跨仓）；hotfix 基线复刻尚未执行。
- 可支持的交付判断：core 侧单测全绿即可推送 develop twin；hotfix twin 复刻 + 定向测试后再推。

### 覆盖与资料同步

- 已盘点/已理解：merge 线全部提交与备份 ref；develop/0.1.17_hotfix 两条基线的 handler 消费行为。
- 已验证：见上表。
- 资料同步：本文档；`sync_documents.py` 索引刷新。模块 AGENTS 无需更新（未新增属性键、未改 wire format）。

## 已知遗留

| 遗留项 | 未处理原因 / 性质 | 影响与现有处置 | 后续入口 / 条件及所需验证 |
| --- | --- | --- | --- |
| hotfix 基线复刻（C2 行文适配、常量同样落地） | 未实现；develop twin 先行 | !3571 重开需该复刻 | 复刻后跑同套定向单测 |
| agent-studio S1 门禁重指向新常量 | 属 studio 侧 !2582 重做范围 | 门禁现状指向死开关，激活前不生效 | core 合入后随 !2582 重做落地 |
| 后端去重规则（优先计子、无子再计桥） | 跨系统，非本仓范围 | 不先行则层间双计窗口 | 需在 studio 激活前/同时上线 |
| !2583 RAG 依赖 C3 | 待 core 合入 | PR 线程已注明依赖 | core 合入后 !2583 跟进 |
