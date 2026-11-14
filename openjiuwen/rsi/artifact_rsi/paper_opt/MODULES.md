# `paper_opt` 模块说明

路径根：`agent-core/openjiuwen/rsi/artifact_rsi/paper_opt`

本文按代码结构说明每个模块的职责、入口、关键 schema、与上下游的连接，以及主要配置旋钮。

---

## 0. 总体架构：三层编排

系统由内到外有三层编排：

```
PaperArtifactProviderImpl.run
  └─ PaperTreeOrchestrator._run_loop          # 外层：最多 max_iterations 个树节点
       └─ 每节点 ManagerRuntime.arun           # 中层：decide → validate → route → persist
            └─ SubagentRegistry → 六模块       # 内层：各 module agent
       └─ score_paper → adopt / reject / prune

对照路径（无决策）：
PipelineRunner.run(topic)                      # 固定顺序一次过
```


|          | `PipelineRunner`   | `ManagerRuntime`                | `PaperTreeOrchestrator`                              |
| -------- | ------------------ | ------------------------------- | ---------------------------------------------------- |
| 控制       | 固定顺序               | LLM 每轮 `ManagerDecision`        | 外层树轮次 + 比分采纳                                         |
| 校验/预算    | 无                  | `transitions` + 多类 retry        | `max_iterations`                                     |
| 持久化/HITL | 无                  | `PersistedManagerState` + pause | `tree.json` / `state.json`；Provider 级 resume **不支持** |
| 返回       | `list[str]` PDF 路径 | `TerminalReport`                | `EngineResult` + 树                                   |


典型 Manager 科学闭环：

```
topic_survey
  → experiment_design(create)
  → code_implementation
  → experiment_execution
  → reflection
  → (design update | reporting)
  → DONE
```

补充：

- `paper_preprocess` 仅在**改稿 / 树节点播种**时使用，不进入 Manager 状态机。
- `paper_scoring` **不在** Manager 循环内，由树外层 `judge` 调用，用于节点比分采纳。

---



## 1. 顶层：`__init__.py` / `provider.py` / `configs/`



### 1.1 `__init__.py`

对外仅导出契约：

```python
from openjiuwen.rsi.artifact_rsi.paper_opt.provider import PaperArtifactProvider
__all__ = ["PaperArtifactProvider"]
```



### 1.2 `provider.py` — `PaperArtifactProvider` Protocol

论文产物优化的**接口契约**。真正实现在 `auto_research/tree_provider/provider.py` 的 `PaperArtifactProviderImpl`。

职责划分：

- Provider：论文优化、持久任务快照、artifact 定位
- AgentServer：路由、请求组装、事件队列、Web 传输、URL 投影与权限校验


| 方法                                                            | 语义                            |
| ------------------------------------------------------------- | ----------------------------- |
| `artifact_type: Literal["paper"]`                             | 静态类型约束为 paper                 |
| `validate_input(artifact_path)`                               | 校验可选论文路径/布局；**不**启动优化、不建快照    |
| `run(request, on_event)`                                      | 启动新任务；先持久化再发事件；先 running，末终端态 |
| `pause` / `terminate`                                         | 暂停并落检查点 / 永久终止                |
| `resume`                                                      | 约定返回 `SCENARIO_NOT_SUPPORTED` |
| `read_state` / `read_report` / `get_tree` / `locate_artifact` | 只读投影，无副作用                     |




### 1.3 `configs/pipeline.default.yaml`

各模块**自行读取**对应配置段构造 runtime（无共享 factory）。

要点：

- `api_key` **不是**配置字段，只从环境变量读取，避免密钥进仓库。
- `openjiuwen.base_url` / `model` / `provider` 默认 `null`，让 env（`API_BASE` / `MODEL_NAME` / `MODEL_PROVIDER`）生效；yaml 写成非 null 会抢占 env。


| 配置段              | 作用                                                  |
| ---------------- | --------------------------------------------------- |
| `openjiuwen`     | 模型客户端：`base_url` / `model` / `provider` / `timeout` |
| `pipeline`       | 线性 runner：`topic` + `modules.*` 开关                  |
| `manager`        | Manager 预算与模块开关（含 `reflection`）                     |
| `logging`        | run 级日志、脱敏、滚动文件                                     |
| `topic_survey` 等 | 各研究模块旋钮                                             |
| `paper_scoring`  | 独立打分模块；不进 pipeline/manager                          |


注意：`pipeline.modules.idea_generation: true` 是遗留键，仓库中**没有**对应模块实现。

---



## 2. `auto_research/common/` — 公共基础设施

无业务研究逻辑，为 pipeline / manager / 各 module 提供路径约定、指标契约、日志、检查点与环境加载。

### 2.1 `workspace.py`

`experiments/<run_id>/` 布局与路径安全。

关键 API：

- 根路径：`set_project_root` / `project_root`
- 目录：`workspace_dir`、`design_dir`、`generated_code_dir`、`module_attempt_dir`、`results_dir`、`smoke_test_dir`
- 产物：`paper_*`、`reflection_*`、`manager_*`、`agent_trace_path`
- 安全：`resolve_project_reference`、`to_project_relative`

几乎所有模块都会用到；树编排器可把 `project_root` 切到 per-task `run_dir`。

### 2.2 `python_runtime.py`

发现真实 Python 3（避开 Windows App Execution Alias）。

- `PythonRuntime`、`discover_python_runtime`、`ensure_on_path`
- 旋钮：`PYTHON_BIN_DIR`；解析后写 `PYTHON_EXE`



### 2.3 `metrics.py`

指标解析、压缩、契约校验、失败分类。供 execution / reflection / manager handoff / reporting 共用。

关键能力：`resolve_metric`、`validate_metrics_contract`、`run_sanity`、`failure_class_from_metrics`、`compact_metrics`、`overlay_paired_metrics`、`infer_proposed_name` 等。

### 2.4 `logging.py`

run 关联日志、pipeline 滚动文件、SDK 日志目录、trace 脱敏。

- `configure_run_logging`、`log_context`、`sanitize_for_trace`
- yaml `logging:`：`enabled` / `level` / `console` / `content_mode` / `max_field_chars` / `backup_count` / `max_bytes`
- 默认 `console: false`，避免超大 LLM dump 卡死终端



### 2.5 `ids.py`

`new_run_id(prefix="run")` → `"{prefix}-{uuid4.hex[:8]}"`。

### 2.6 `error_tree.py`

`python_error_tree` / `exception_banner`：从子进程输出抽 traceback，供 manager / code / execution 摘要失败原因。

### 2.7 `env.py`

`load_project_dotenv`：加载仓库 `.env`（不覆盖已有 env）；兼容 `project_root` 被 redirect 的情况。

### 2.8 `checkpointing.py`

引导 OpenJiuwen checkpointer（主要为 Experiment Design 持久会话）。

旋钮（`experiment_design.checkpointer`）：`type` / `db_type` / `db_path` / `db_enable_wal`。

---



## 3. `auto_research/config/`



### `settings.py`

`load_config(path) -> dict`：先 `load_project_dotenv()`，再 `yaml.safe_load`。

无结构化 pydantic schema；各模块自行解读对应配置节。

---



## 4. `auto_research/pipeline/` — 流水线宿主



### 4.1 `runner.py` — `PipelineRunner`

无研究逻辑的**线性 wiring**，适合一次性跑通：

```
TopicSurvey
  → ExperimentDesign
  → CodeImplementation
  → (status != ready 则返回 [])
  → ExperimentExecution
  → Reflection（失败不阻塞）
  → Reporting
  → [pdf] 或 failed 字符串
```

不经 Manager 决策、预算、HITL、树比分。

### 4.2 `manager.py` — `ManagerRuntime`

可信宿主循环：**decide → validate → route → persist**。

入口：`arun` / `run`；辅助 `build_initial_state`、`apply_report_effects`、`_enabled_modules`。

循环骨架：

1. `sync_host_requirements(state)`
2. 可选 `on_stage("manager")`
3. `_decide_with_repair()`：`ManagerAgent.adecide` + `validate_decision`（最多 `max_decision_retries+1`）
4. `apply_state_changes`
5. 按 `signal`：
  - `DONE` → `can_complete` → `TerminalReport(complete)` 或反馈重试
  - `BLOCKED` → `TerminalReport(blocked)`
  - `EXECUTE` → 落 `pending_contract` → `_execute_contract`（`SubagentRegistry`）→ `apply_report_effects` / 落盘
6. 超 `max_rounds` → `incomplete`

特殊逻辑：`_is_no_literature_create_task` 匹配「无需文献综述 / no literature review」等 marker 时，从 enabled 去掉 `topic_survey`（仅限 `create_new_paper` 且无初始 research paths）。

主要旋钮（`manager:`）：

- `max_iterations`：ManagerAgent 内循环
- `max_rounds`：宿主轮次上限
- `max_survey_calls` / `max_code_retries` / `max_design_revisions` / `max_execution_retries` / `max_decision_retries` / `max_reporting_retries`
- `max_history_chars` / `excerpt_chars`
- `modules.*`：模块开关



### 4.3 `transitions.py`

宿主侧决策 / 合约 / DONE 合法性校验。非法决策抛 `DecisionValidationError`（可重试的控制格式错误）。

关键 API：

- `validate_decision` / `validate_contract` / `list_legal_actions`
- `can_complete` / `sync_host_requirements`
- `apply_state_changes` / `sanitize_execute_state_changes`
- `code_head_from_state`、各类 remaining_*_retries

合法 `(module, mode)`：


| module                 | mode                                    |
| ---------------------- | --------------------------------------- |
| `topic_survey`         | `run`                                   |
| `experiment_design`    | `create` / `update` / `revise_research` |
| `code_implementation`  | `run`                                   |
| `experiment_execution` | `run`                                   |
| `reflection`           | `run`                                   |
| `reporting`            | `run`                                   |


DONE 条件（概要）：全部 requirements completed、无 unresolved_issues、有成功 completed execution；若启用 reporting 还需成功 reporting。

### 4.4 `subagents.py` — `SubagentRegistry`

allowlist 适配器：把既有模块变成 Manager 子代理。

```python
class SubagentAdapter(Protocol):
    module: ModuleId
    async def ainvoke(contract, state, *, round_index, attempt) -> SubagentReport: ...
```


| Adapter                      | 要点                                             |
| ---------------------------- | ---------------------------------------------- |
| `TopicSurveyAdapter`         | → `SurveyHandoff`                              |
| `ExperimentDesignAdapter`    | 支持 `create` / `update` / `revise_research`     |
| `CodeImplementationAdapter`  | 普通实现；或 `restore_code_commit` / promotion retry |
| `ExperimentExecutionAdapter` | 要求非空 `target_variants`；写 `execution_history`   |
| `ReflectionAdapter`          | 需要 plan + execution                            |
| `ReportingAdapter`           | `compiled` → succeeded；关注 `is_final_attempt`   |




### 4.5 `hitl.py` — 人机协同


| API                        | 行为                                       |
| -------------------------- | ---------------------------------------- |
| `persist_pause`            | 丢弃 in-flight → save → pause notes        |
| `discard_in_flight_round`  | 清 pending；可能 `design_session_epoch += 1` |
| `apply_resume_steering`    | 消费 operator followup；决定是否继续循环            |
| `inject_operator_followup` | 注入操作员跟进指令                                |
| `run_asyncio`              | Ctrl+C → `RunPaused`                     |
| `add_hitl_args`            | `--followup` / `--followup-file`         |


注意：Manager 级可 pause/resume；**树 Provider 的** `resume` **固定不支持**。

### 4.6 `stage_activity.py`

`tail_activity`：轮询 `agent_trace.jsonl`，把最新 tool 事件变成短中文 `note`，经 `on_stage(module, note)` 推前端进度。

---



## 5. `auto_research/tree_provider/` — 论文尝试树 Provider

把六模块 pipeline 适配成「论文尝试树」：每个树节点跑一次完整 ManagerRuntime，再打分决定采纳 / 剪枝。

### 5.1 `provider.py` — `PaperArtifactProviderImpl`

薄封装 `PaperTreeOrchestrator` + `TaskStorage`。

- `artifact_type = "paper"`，`supports_pause = True`
- 进程内 `task_id → run_dir/orchestrator` 注册表（**重启丢失**）
- `resume` → `SCENARIO_NOT_SUPPORTED`
- 只读方法通过 storage + projection 投影公开 schema



### 5.2 `schemas.py`

公开线格式 + `PaperTaskState`（pydantic）。

含：`RsiTreeNode`、`PaperNodeExtra`、`NodeOutcome`、`EngineEvent` / `OnEvent`、`friendly_failure_reason` 等。

区分：

- `best_node_id`：公开最优节点
- `frontier_node_id`：内部父指针 / 前沿



### 5.3 `storage.py` — `TaskStorage`

原子落盘：

- `state.json`
- `tree.json`（按 `node_id` upsert）
- `artifacts.json`



### 5.4 `orchestrator.py` — `PaperTreeOrchestrator`

外层树循环：

1. `build_node_seed`
2. 写入 placeholder 节点
3. `ManagerRuntime.arun`
4. `score_paper`
5. 比分严格大于 parent 才采纳；否则 reject / prune

旋钮来源：

- 默认配置：`configs/pipeline.default.yaml`
- 请求侧：`max_iterations`、`optimization_instruction`、`artifact_path`、`model`、`web_proxy`
- 可覆盖：`topic_survey.web_proxy` / `search_scope`、模型 overlay、`manager.modules.reflection`



### 5.5 `seed.py`

`NodeSeed` / `build_node_seed`：

- 有父论文 → `task_mode="modify_paper"` + `paper_preprocess`
- `retry_reason` 写入 constraints
- `build_node_run_id` 去掉冒号（Windows 路径安全）



### 5.6 `judge.py`

薄封装 `PaperScorer`：`score_paper` → `composite_score`。异常由 orchestrator 映射为 `scoring_error` 剪枝。

### 5.7 `projection.py`

纯投影，无 I/O：

- `project_engine_state`
- `project_engine_report`
- `project_tree_response`

---



## 6. `auto_research/extensions/` — OpenJiuwen 扩展点

设计原则：通过公共扩展点新增 Tool / Rail；**禁止** monkeypatch OpenJiuwen。

注意：

- `registry.register_all` 目前仍是占位（`NotImplementedError`）
- 实际由各 `modules/*/agent.py` **显式组装** rails/tools
- 仓库中没有真正的 `extensions/agents/` 实现目录



### 6.1 Rails（`extensions/rails/`）


| 文件                              | 类                           | 作用                                                                           |
| ------------------------------- | --------------------------- | ---------------------------------------------------------------------------- |
| `observability_rail.py`         | `ObservabilityRail`         | 写 `agent_trace.jsonl`；`with_observability`                                   |
| `topic_survey_tools.py`         | `TopicSurveyToolsRail`      | `free_search` / `fetch_webpage` / `download_survey_source`（可选 `paid_search`） |
| `experiment_design_tools.py`    | `ExperimentDesignToolsRail` | 只读 FS（与 reflection 工具集同类）                                                    |
| `reflection_tools_rail.py`      | `ReflectionToolsRail`       | 只读 FS；禁 write / shell                                                        |
| `manager_capability_rail.py`    | `ManagerCapabilityRail`     | 仅允许 `submit_manager_decision`                                                |
| `guarded_sys_operation_rail.py` | `GuardedSysOperationRail`   | Windows 上给 PowerShell 补 deny_patterns                                        |
| `exploration_budget_rail.py`    | `ExplorationBudgetRail`     | 连续无写 `output/` 时注入警告（默认阈值=30）                                                |
| `openjiuwen_reference_rail.py`  | `OpenJiuwenReferenceRail`   | 独立 sandbox 只读参考 docs / 源码                                                    |
| `design_reference_rail.py`      | `DesignReferenceRail`       | 只读本 run 的 `design/`                                                          |




### 6.2 Tools（`extensions/tools/`）

统一模式：`reset` → agent invoke → host `require_submission`；schema 内联展开 `$ref`。


| 工具                           | 提交 Schema                                        |
| ---------------------------- | ------------------------------------------------ |
| `SubmitTopicSurveyTool`      | `TopicSurveyDraft`                               |
| `SubmitExperimentDesignTool` | `ExperimentDesignDraft`                          |
| `SubmitReflectionTool`       | `ReflectionJudgment`                             |
| `SubmitManagerDecisionTool`  | `ManagerDecision`                                |
| `DownloadSurveySourceTool`   | 内联 JSON：`url` / `filename` / `fallback_evidence` |


---



## 7. `auto_research/modules/` — 研究子模块



### 7.1 `paper_preprocess/`（不在 Manager 循环内）

**角色**：改稿 / 树节点播种的**确定性预处理**：校验本地 LaTeX → 抽证据 → 生成 `initial_prompt` + `ResearchContext`。

**入口**：

- `PaperPreprocessAgent.run`
- `preprocess_paper`
- `validate_latex_paper`
- `extract_paper_evidence`

**关键 schema**：`PaperPreprocessInput/Output`、`LatexPaperDocument`、`PaperEvidence`、`ResearchContext`、`ResearchClaim`。

**连接**：

- 上游：本地论文目录 / 父节点 PDF/TeX
- 下游：`tree_provider/seed.py`；reporting 用 `ResearchContext` 对齐先验论文

**配置**：无独立 yaml 段。

**支撑文件**：`latex_validation.py`、`extractor.py`。

---



### 7.2 `topic_survey/`

**角色**：文献 / 网页调研；产出 `ResearchBrief.resource_paths`。

**入口**：`TopicSurveyAgent.survey` / `asurvey` / `run`；`write_survey_artifacts`。

**关键 schema**：

- 输入：`TopicSurveyInput`（`topic` / `max_papers` / `max_web_pages` / `initial_context`）
- 草稿：`TopicSurveyDraft`、`SurveySource`、`CitationMetadata`
- 输出：`TopicSurveyOutput`；**交给下游的是** `ResearchBrief`

**连接**：

- → `experiment_design.create`
- 工具：`TopicSurveyToolsRail` + `SubmitTopicSurveyTool` + `DownloadSurveySourceTool`
- host 侧 `citations.py` 拥有 citation 资格判定

**旋钮（**`topic_survey:`**）**：

- `max_papers` / `max_web_pages` / `max_iterations` / `timeout_seconds`
- `web_proxy` / `search_scope`
- `include_paid_search` / `free_search.duckduckgo|bing`

**支撑**：`artifacts.py`、`citations.py`、`prompts/system.md`。

---



### 7.3 `experiment_design/`

**角色**：实验设计「活文档」：`create` / `update` / `revise_research` → `ExperimentPlan` + design.md + code_agent_instruction.md。

**入口**：`ExperimentDesignAgent.acreate` / `aupdate` / `arevise_research`；`run` ≡ create。

**关键 schema**：

- `ResearchBrief`
- `ExperimentDesignInput` / `EvaluationFeedback` / `ExperimentDesignDraft`
- `ExperimentPlan`（`status`: draft/continue/accepted/stopped；`recovery_mode`: live/checkpoint/artifacts）
- `CodeAgentInstruction`、`MetricDefinition`

**连接**：

- ← survey
- → code_implementation
- 执行后 host 合成 `EvaluationFeedback` 再 `update`
- 补充调研 → `revise_research`（清 `pending_research_revision`）

**旋钮**：

- `max_iterations`、`skills`
- `context.engine.*` + processors（`MessageSummaryOffloader` / `DialogueCompressor` / `CurrentRoundCompressor` / `RoundLevelCompressor`）
- `checkpointer.*`（sqlite 持久会话）

**支撑**：`artifacts.py`（SCHEMA_VERSION=6 living summary）、`prompts/system.md`、`prompts/openjiuwen_conventions.md`（也被 code 引用）。

---



### 7.4 `code_implementation/`

**角色**：实现 `ExperimentPlan`；在 `agent_workspace/output/` 写代码，smoke 通过后 promote 到 `generated_code/` 并 host git commit。

**入口**：

- `CodeImplementationAgent.run` / `arun` / `promote_output`
- checkpoint：`seed_output_from_head` / `host_commit` / `restore_commit` 等

**关键 schema**：

- `CodeImplementationInput`
- `ImplementedVariant`
- `CodeImplementationManifest`（`status` ready/failed；`readiness` smoke_ready/failed/promotion_failed；`code_commit`）
- `CodeImplementationOutput`

**连接**：

- ← plan
- → experiment_execution
- 失败修复循环；可 `restore_code_commit`
- Rails：`GuardedSysOperationRail`、`OpenJiuwenReferenceRail`、`DesignReferenceRail`、`ExplorationBudgetRail`

**旋钮**：

- `max_iterations`（含 LSP fix-loop）
- `smoke_test_timeout_seconds`
- `max_smoke_test_retries`
- 密钥只读 env（`API_KEY` 等）

**支撑**：`checkpoint.py`、`grounding.py`、`reference_index.py`、`prompts/system_prompt.md`。

---



### 7.5 `experiment_execution/`

**角色**：**确定性**真实跑实验（**无 LLM**）；硬门禁 `implementation.status == "ready"`。

**入口**：`ExperimentExecutionAgent.run`。

**关键 schema**：

- `ExperimentExecutionInput`（`target_variants`；Manager 要求非空）
- `VariantResult`、`ExperimentResult`
- `ExperimentExecutionOutput`

**连接**：

- → reflection / reporting / design update
- 失败且非瞬态 → 回 code 修复
- 写 logs/results，并 mirror 到 artifact 目录

**旋钮**：

- `timeout_seconds`
- `max_transient_retries`：仅 `launch_error` / `timeout`
- **不重试** `nonzero_exit` / `missing_metrics` / `invalid_metrics`（确定性代码 bug）

**说明**：目录下有 `prompts/system.md`，但 agent **未挂 DeepAgent**；依赖 `common.metrics` 与 `checkpoint.current_commit`。

---



### 7.6 `reflection/`

**角色**：执行完成后的**科学语义裁判**（机械 sanity 由 host 先滤）；建议可被 Manager 否决。

**入口**：

- `ReflectionAgent.run` / `arun`
- `judgment_cites_primary`（强制引用 `plan.primary_metric`）
- `render_reflection_markdown`

**关键 schema**：

- `ReflectionInput`
- `ReflectionJudgment`（`validity` / `hypothesis_verdict` / `objective_progress` / `recommendation`…）
- `EvidenceItem`
- `Recommendation`：`iterate_design` | `repair_code` | `rerun_execution` | `gather_more_evidence` | `accept_and_report`
- `Reflection` / `ReflectionOutput`

**连接**：

- → `ReflectionHandoff`
- reporting 通常要求最新 execution 有 fresh reflection
- process-failed / invalid_run 应跳过 reflection
- 线性 `PipelineRunner` 中 reflection 失败不阻塞 reporting

**旋钮**：`reflection.max_iterations`（默认 60）。

---



### 7.7 `reporting/`（含 skills）

**角色**：终稿模块：host 预构建 bib / 图 / 证据 → skills DeepAgent → **host 独立校验编译结果**。

**入口**：`ReportingAgent.run` / `arun`。

**关键 schema**：

- `ReportingInput`：`survey` / `plan` / `result` / `reflection` / `previous_context` / `repair_instruction` / `is_final_attempt`…
- `ReportingOutput`：`status` compiled|failed、`paper_pdf_path`、`lint_issues`
- `MethodFigureSpec`

固定章节（`sections.py`）：

`abstract` / `introduction` / `related_work` / `method` / `experiments` / `discussion` / `conclusion`

**旋钮（**`reporting:`**）**：

- `max_iterations`（120）、`timeout`（900）
- `latex_preflight` / `latex_preflight_timeout` / `latex_bin_dir`
- `bibliography_timeout`
- `method_figure.enabled` / `drawio_bin` / `drawio_skill_dir`

**支撑模块**：`bibliography`、`figures`、`lint`、`latex` / `latex_runtime`、`sections`、`evidence`、`reflow`。

#### Skills（推荐顺序：plan → write → figure → review → latex）


| Skill       | 目的                               | Scripts / 资产                                                                                                                                        |
| ----------- | -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ts-plan`   | 标题 / 3 条贡献 / 关键词                 | 无                                                                                                                                                   |
| `ts-write`  | 写 `sections/*.tex`               | 无                                                                                                                                                   |
| `ts-figure` | 方法图 + 结果图（可关）                    | `extract_headings`、`check_method_figure_spec`、`attempt_drawio`、`render_method_figure`、`insert_method_figure`、`check_figure_script`、`promote_figure` |
| `ts-review` | lint → 对抗审稿 → 引文检查               | `lint_check.py`、`check_citations.py`                                                                                                                |
| `ts-latex`  | 组装编译；无 toolchain 可停、host 可接受 tex | `compile.py`、`preflight.py`；资产 `neurips_2025.sty`                                                                                                   |


完成判定由 host `_verify_and_build_output` 执行，不信任 agent 自报。

---



### 7.8 `manager/`（决策 agent，非宿主）

**角色**：低权限路由 LLM：每轮只调用 `submit_manager_decision`，发出 `EXECUTE` / `DONE` / `BLOCKED`。真正执行在 `pipeline/manager.py`。

**入口**：

- `ManagerAgent.adecide`
- `render_manager_query`
- `artifacts.py`：state / events / rounds / terminal / pause 落盘

**关键 schema**：


| Schema                            | 含义                                                                                                                                            |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `ManagerDecision`                 | `signal`、`rationale`、`state_changes`、`contract?`、`blocked_reason?`                                                                            |
| `SubtaskContract`                 | `module` / `mode` / `goal` / `acceptance_criteria` / `related_report_ids` / `repair_instruction` / `target_variants` / `restore_code_commit`… |
| `PersistedManagerState`           | `original_task`、`task_state`、`reports`、`rounds`、`terminal`、`latest_*`、`execution_history`…                                                    |
| `TaskState`                       | `phase`、requirements/artifacts/facts、`counters`/`limits`、`enabled_modules`、`pending_research_revision`…                                       |
| Handoffs                          | `SurveyHandoff` / `DesignHandoff` / `CodeHandoff` / `ExecutionHandoff` / `ReflectionHandoff` / `ReportHandoff`                                |
| `ManagerSnapshot` + `RoutingHint` | 含 `list_legal_actions` 结果与剩余预算                                                                                                                |
| `TerminalReport`                  | `complete` | `blocked` | `failed` | `cancelled` | `incomplete`                                                                                |


**旋钮**：`manager.max_iterations`（agent 内）、`BudgetLimits` via `limits_from_config`、`manager.modules.`*。

---



### 7.9 `paper_scoring/`（独立，不进 Manager）

**角色**：LaTeX-in / 双 rubric scoresheet-out；树外层 `judge` 用其比分决定节点采纳。

**入口**：

- `PaperScorer.run` / `arun` / `ascore`
- `ingest_latex`
- `StructuredCompleter`
- `aggregate_*` / `build_scoresheet`

**关键 schema**：

- `PaperScoringInput/Output`
- `PaperDocument`
- `GeneralRubricReviewOutput`
- `ExperimentRigorReviewOutput`
- `Scoresheet`：`general_score` / `experiment_score` / `composite_score`
- 三组 Weights：`general_weights` / `experiment_weights` / `composite_weights`

**旋钮（**`paper_scoring:`**）**：

- `timeout` / `temperature` / `rubric_samples`（3 或 5）
- `max_validation_retries`
- context token 预算：`context_window_tokens` / `output_reserve_tokens` / `prompt_overhead_tokens`
- `min_text_chars` / `max_figures` / `figure_max_side`
- `dispersion_range` / `dispersion_mad`
- 各类权重

**支撑**：`ingestion.py`、`llm.py`、`aggregation.py`、`artifacts.py`、`prompts/rubric.md`、`prompts/experiment_rigor.md`。

---



## 8. 模块数据流

```
[可选 LaTeX paper_dir]
  → PaperPreprocess → initial_prompt / ResearchContext
        ↓ (modify_paper seed)
topic → TopicSurvey → ResearchBrief.resource_paths
  → ExperimentDesign.create → ExperimentPlan + design artifacts
    → CodeImplementation → Manifest(ready) + code_commit
      → ExperimentExecution → ExperimentResult
        → Reflection → ReflectionJudgment
          → Reporting → paper/dist/*.pdf (或 tex)
                ↓ (仅树外层)
          PaperScorer → composite_score → adopt / reject / prune

旁路：
  execution 失败 → code repair → 再执行
  reflection 建议 iterate → design.update → 再实现
  新 survey（卡住时）→ pending_research_revision → design.revise_research
```

---



## 9. 实现注意点

1. `pipeline.modules.idea_generation`：yaml 有开关，仓库无该模块实现（遗留键）。
2. `extensions/agents`：中央 registry 未真正接线；agent 在 `modules/*`，扩展只有 rails/tools。
3. **密钥**：`API_KEY` / `OPENAI_API_KEY` 等仅 env；yaml `openjiuwen.base_url/model` 建议保持 `null`。
4. **两套 resume**：Manager HITL 可 pause/resume；`PaperArtifactProvider.resume` 恒为不支持。
5. **reporting 完成判定**：host `_verify_and_build_output`，不信任 agent 自报。
6. **experiment_execution 无 LLM**：确定性跑实验；瞬态失败才重试。
7. **树注册表进程内**：`task_id → orchestrator/run_dir` 重启丢失；外层任务 resume 仍是开放问题。

