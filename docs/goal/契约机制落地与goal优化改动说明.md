# Goal 模块优化改动说明（日志改造 + Assessor 解析修复 + 完成契约机制）

> 供后续评审，说明本次对 `openjiuwen/harness/goal` 子系统做了什么、为什么做、怎么验证。

## 一、背景

本次围绕 goal 持续目标能力的**可观测性、评估准确性、验收标准**三方面做低侵入优化，对应文档《Goal能力实现架构调研及优化方案设计》的**方案一：完成契约机制** + 两个配套修复：

1. **日志违规**：goal 模块 5 个文件用标准 `logging.getLogger(__name__)`，不进 SDK 的 loguru 统一配置，导致 `[GoalEvaluator]` 等关键日志不可见——违反 `.claude/rules/logging.md`。
2. **Assessor 解析 bug**：assessor 输出包 ` ```json ` fence + evidence 嵌套 ` ```python ` 代码块时，`_parse_assessment_json` 的非贪婪 regex 截断 JSON → `parse failed` → 误判 `continue` → agent 做对了却反复重跑（hello.pptx 任务实测触发）。
3. **契约机制（方案一）**：assessor 只看模糊 `objective`，验收标准靠语义猜，导致「模糊完成」风险。落地结构化契约（5 字段），双注入 agent + assessor，让 assessor 按契约逐项验证。

## 二、改动总览

**13 个 tracked 文件改动（+318 -22），6 个新增文件。**

| 类别 | 文件 | 改动 |
|------|------|------|
| 日志改造 | evaluation.py / manager.py / store.py / task_completion_rail.py / tools/goal.py | 5 文件 `logging.getLogger` → SDK `LazyLogger(LogManager.get_logger("goal"))`，统一进 loguru |
| debug 日志 | task_completion_rail.py | `_invoke_transcript_assessor` 加 `logger.debug` 打印 assessor raw response（截 2000 字符） |
| Bug 修复 | evaluation.py | `_parse_assessment_json` 加第 3 层 fallback：找第一个 `{` 到最后一个 `}` 直接 `json.loads`，抗 fence + 嵌套代码块 |
| 契约机制-数据 | schema.py | 新增 `GoalContract` dataclass（5 字段 + `is_empty`/`render_block`/`to_dict`/`from_dict`）+ `GoalRecord.contract` 字段 + 序列化 + `create` 透传 |
| 契约机制-API | manager.py | `GoalManager.set()` 加 `contract` kwarg + 透传 `GoalRecord.create` |
| 契约机制-prompt | prompts/sections/goal.py | `_GOAL_TASK_TEMPLATE` 加 `<contract>` 占位 + `_format_contract` + `build_goal_task_query` 传 contract + `TRANSCRIPT_ASSESSOR_SYSTEM` 加「契约优先」规则（rule 0，中/英）+ `build_transcript_assessor_prompt` 加 contract 参数 + 注入 `<contract>` 段 |
| 契约机制-rail | task_completion_rail.py | `_invoke_transcript_assessor` 调 prompt 时传 `record.contract` |
| 契约机制-工具（新增） | goal/contract_parser.py | `draft_contract`（复用 model.invoke 辅助生成契约）+ `parse_contract_from_text`（内联字段解析）+ `DRAFT_CONTRACT_SYSTEM_PROMPT`（双语）+ `_parse_contract_json`（三层 fallback） |
| 导出 | goal/__init__.py | 导出 `GoalContract` / `draft_contract` / `parse_contract_from_text` |
| 测试 | test_goal_schema.py / test_goal_prompts.py / test_goal_manager.py / test_goal_evaluation.py | 扩展契约相关测试（共 +20 用例） |
| 测试（新增） | test_contract_parser.py | 11 用例：`_parse_contract_json` 三层 / `parse_contract_from_text` 内联解析 / `draft_contract` mock + 失败降级 |
| 示例（新增，untracked） | examples/harness/goal_debug.py / goal_388_pptx.py / goal_contract_demo.py | 端到端调试脚本 |

> `pyproject.toml` 的 +2 行是 `uv sync --extra cli` 的依赖管理副作用，非功能改动。

## 三、详细改动

### 3.1 日志改造（5 文件 → SDK LazyLogger）

**问题**：goal 模块用 `import logging; logger = logging.getLogger(__name__)`，标准 logging 不经 SDK 的 `LogManager` 配置层，`[GoalEvaluator]`/`[GoalLifecycle]` 的 INFO 日志默认不输出，调试不可见。

**改法**：5 文件统一改成
```python
from openjiuwen.core.common.logging import LazyLogger, LogManager
logger = LazyLogger(lambda: LogManager.get_logger("goal"))
```
- 模块级用 `LazyLogger`（import 零副作用，首次访问才绑定，符合 [.claude/rules/logging.md](.claude/rules/logging.md) 第 4 条）；
- namespace 统一 `"goal"`，进 loguru sink，受 `configure_log_config` 控制；
- 不动 core 框架（`__init__.py` 不加新 logger，复用 `LogManager.get_logger`）。

### 3.2 Assessor raw response debug 日志

`_invoke_transcript_assessor` return 前加：
```python
logger.debug("[GoalLifecycle] transcript assessor raw response (len=%d): %s", len(content), content[:2000])
```
让 assessor 原始返回可见（含是否有 ` ```json ` fence、字段顺序、嵌套代码块），调试评估链路。脚本配 `goal` logger=DEBUG 可见。

### 3.3 Assessor 解析 bug 修复（`_parse_assessment_json` 三层 fallback）

**Bug**：assessor 返回 ` ```json\n{...evidence 含 ```python...```...}\n``` ` 时，regex `r"```(?:json)?\s*\n?(.*?)\n?\s*```"` 非贪婪，从第一个 ` ```json ` 匹配到 **evidence 里 python 代码块的闭合 ` ``` `**，JSON 截断 → `json.loads` 失败 → `parse failed` → fallback `continue` → agent 做对了却反复重跑。

**修法**：加第 3 层 fallback——裸 JSON / regex fence 都失败后，找第一个 `{` 到最后一个 `}` 直接 `json.loads`。evidence 里的 `{` `}` 都在 JSON 字符串值内部，不影响外层结构解析。

```python
if not isinstance(data, dict):
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            data = None
```

三层：裸 JSON → regex fence → find/rfind `{...}`。第 3 层只在第 2 层失败时触发，向后兼容。

### 3.4 完成契约机制（方案一）

**设计**：把模糊 objective 转成结构化 `GoalContract`（5 字段：outcome/verification/constraints/boundaries/stop_when），双注入：
- **agent 侧**：`<goal_task>` 加 `<contract>` 段，agent 看到验收标准按契约自验证；
- **assessor 侧**：`build_transcript_assessor_prompt` 注入 `<contract>` 段 + `TRANSCRIPT_ASSESSOR_SYSTEM` 加 rule 0「契约优先：存在 `<contract>` 时先逐项验证 verification/constraints/boundaries/stop_when，所有满足才 complete」。

**关键设计决策**：
1. **`goal_manager.set(objective, contract=...)` 显式传**——set 只存储，不调 LLM（避免 `control_lock` 内长时间持锁）。`draft_contract` / `parse_contract_from_text` 是独立工具函数，**host 在 set 前自行调用**；
2. **空契约退化**——`is_empty()` 时不注入 `<contract>`（assessor）/ 显示「无」（agent），向后兼容（无契约行为同改造前）；
3. **旧数据兼容**——`GoalRecord.from_dict` 无 contract 字段 → None，round-trip 不破坏；`GoalContract.from_dict` 宽松不 raise（避免 SessionGoalStore.load 的 except 清空整个 record）；
4. **契约 ≠ 评测 rubric**——契约是用户/辅助模型生成的标准，粗于评测 rubric，不泄露金标准；
5. **draft_contract 失败降级**——LLM 调用/解析失败 → 空契约，不阻塞 host 的 set。

**契约来源双通道**（contract_parser.py）：
- `parse_contract_from_text(text)`——内联 `verify:`/`constraints:`/`boundaries:`/`stop when:` 解析；
- `draft_contract(objective, model, language)`——辅助 LLM 生成（复用 `_invoke_transcript_assessor` 的 `model.invoke(tools=[], temperature=0.0)` + TypeError 兜底），`_parse_contract_json` 三层 fallback 解析。

**用户审查/修改/澄清**：GoalContract 非 frozen（字段可改）+ `render_block`/`to_dict` 可读 → host 可显示给用户审查、改字段、再 set。反问澄清（检测空字段→问用户→修正）也在 host 层（agent-core 是 SDK 不做交互）。

## 四、验证结果

### 单元测试

```
93 passed（73 旧兼容 + 20 新增）
- test_goal_schema.py: 10 → 14（+4：GoalContract round-trip/render + GoalRecord 带 contract + 旧数据兼容）
- test_goal_prompts.py: 6 → 10（+4：build_goal_task_query 带 contract + assessor prompt 带 contract + 空契约退化）
- test_goal_manager.py: 20 → 21（+1：set 传 contract 持久化 + store round-trip）
- test_goal_evaluation.py: 6 → 7（+1：嵌套代码块解析）
- test_contract_parser.py: 11 新增（_parse_contract_json 三层 / parse_contract_from_text / draft_contract mock + 降级）
- test_task_completion_extensions.py: 17（未变）
```

### ruff

无新增问题。剩 2 个 pre-existing F401（`ToolCallInputs`/`build_goal_protocol_section`，非本次引入，按 karpathy 原则不动）。

### 端到端（388 PPT 战略汇报任务，契约机制方式2）

`draft_contract` 从 388 模糊目标生成完整 5 字段契约（verification 具体到「跑 python -c 确认幻灯片数」），`set(objective, contract=drafted)` 跑 goal：
- agent 16 轮 ReAct 完成（读 3 文件 + 生成 10 页 PPT + 逐页数据一致性验证 + submit complete）；
- assessor 1 次评估（29.5s）→ evidence 明确「**一、契约逐项核对结果：1.完成✅ 2.验证✅ 3.约束✅ 4.范围✅ 5.停止条件**」→ COMPLETED，attempt_count=1；
- 对比无契约跑：assessor 从「凭 agent 自证语气判」升级到「按契约 5 字段逐项核对判」。

### 端到端（web host，契约机制方式2 + interrupt 修复）

在 jiuwenswarm web host 完整流程验证契约闭环，**发现并修复一个 interrupt 场景的 goal 死循环 bug**（`tools/goal.py`）：

**Bug**：goal round 被权限中断（HITL/permission pause）时，agent 已提交 COMPLETE 报告，但**下一轮 `GoalReportSink.begin_attempt` 无条件清空 sink**，把未消费的终态报告丢掉。interrupt 路径拿不到报告 → 走 `skip assessment on interrupt` → goal 永远 ACTIVE → task loop 无限重驱动已完成的 goal。日志特征：`[GoalLifecycle] skip assessment on interrupt` 每秒数十上百条、`[GoalEvaluator]` 0 次调用、agent 反复提交 complete 却永不 finalize。

**修复**：`begin_attempt` 只清空 continue/空报告，**保留未消费的 COMPLETE/BLOCKED 终态报告**，让跨 round 边界的 interrupt with_iteration 仍能 consume 并 finalize。新增回归测试 `test_goal_interrupt_after_begin_attempt_finalizes_pending_complete`。

**验证**：修复后 web host 重跑 388，goal 评估链路首次打通——
- `[GoalLifecycle] interrupt with pending complete report; running transcript assessment`（不再 skip）
- `[GoalEvaluator] HYBRID: agent reported complete, verifying via transcript`（评估真正跑起来）
- 首次评估返回 continue（agent 产出 19 页，超 388 rubric 预期的 ~10 页）→ agent 改进 → 再次提交 complete → 交付 `战略汇报.pptx`（19 页，关键数据与源文件核对通过），目标完成。

## 五、未改的（确认）

| 不改 | 原因 |
|------|------|
| `GoalEvaluator.assess()` | 契约经 prompt 注入 assessor 模型，由模型语义判断，transcript_response 解析逻辑不变 |
| `GoalStopConfig` / `GoalStopStrategy` | 契约与停止策略正交 |
| `SessionGoalStore` | 走 GoalRecord.to_dict/from_dict，contract 自动持久化 |
| `tools/goal.py`（submit_goal_report/get_current_goal 工具本身） | 契约设置不涉及这两个工具，agent 已从 `<goal_task>` 看到契约；但 `GoalReportSink.begin_attempt` 因 interrupt 死循环做了修复（见「四、验证结果」） |
| `deep_agent.py` | 只调 manager 的 begin_attempt/ensure_active_goal_work_locked，不调 set |
| `GoalOperationError` | 契约是正常字段，不引入新错误类型 |

## 六、后续方向（未做）

1. **方式3（assessor 读产物 + 通用验收标准）**——让 assessor 独立验证产物（不只看 attempt_context）。但通用验收标准归评估云服务团队定，不在 agent-core。最小路径是**产物摘要注入**（框架用 Python 提取 PPT/数据摘要注入 assessor prompt，assessor 仍无工具）；
2. **方案二（阻塞审计规则）**——相同阻塞连续 N 次才确认 BLOCKED，防 agent 过早放弃（参考 Codex）；
3. **`suggest_clarifying_questions` 工具**（可选）——host 不想自己写澄清问题逻辑时，调它让 LLM 生成问题。反问交互本身仍在 host 层；
4. **CLI/TUI 接入 `/goal` 命令**——jiuwenswarm TUI 前端补 `/goal` builtin（agent-core 已具备 GoalManager，缺口在前端）。

## 七、文件清单（完整）

### 修改（tracked）
- `openjiuwen/harness/goal/schema.py`
- `openjiuwen/harness/goal/manager.py`
- `openjiuwen/harness/goal/store.py`
- `openjiuwen/harness/goal/evaluation.py`
- `openjiuwen/harness/goal/__init__.py`
- `openjiuwen/harness/prompts/sections/goal.py`
- `openjiuwen/harness/rails/task_completion_rail.py`
- `openjiuwen/harness/tools/goal.py`
- `tests/unit_tests/harness/goal/test_goal_schema.py`
- `tests/unit_tests/harness/goal/test_goal_prompts.py`
- `tests/unit_tests/harness/goal/test_goal_manager.py`
- `tests/unit_tests/harness/goal/test_goal_evaluation.py`

### 新增（untracked）
- `openjiuwen/harness/goal/contract_parser.py`
- `tests/unit_tests/harness/goal/test_contract_parser.py`
- `examples/harness/goal_debug.py`
- `examples/harness/goal_388_pptx.py`
- `examples/harness/goal_contract_demo.py`
