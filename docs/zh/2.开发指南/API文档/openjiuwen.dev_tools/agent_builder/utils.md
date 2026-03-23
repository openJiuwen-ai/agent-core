# openjiuwen.dev_tools.agent_builder.utils

`openjiuwen.dev_tools.agent_builder.utils` 是 **枚举、进度与通用工具子模块**，负责：

- 定义 `AgentType`、`BuildState`、`ProgressStage`、`ProgressStatus` 等枚举；
- `ProgressReporter`、`ProgressManager` 与全局单例 `progress_manager`；
- 文本与 JSON 工具函数：`extract_json_from_text`、`format_dialog_history`、`safe_json_loads`、`validate_session_id`、`merge_dict_lists`、`deep_merge_dict`、`load_json_file`。

**包导出**：见源码 `utils/__init__.py` 中 `__all__`。

---

## class openjiuwen.dev_tools.agent_builder.utils.enums.AgentType

字符串枚举：

* **LLM_AGENT**：`"llm_agent"`。
* **WORKFLOW**：`"workflow"`。

---

## class openjiuwen.dev_tools.agent_builder.utils.enums.BuildState

* **INITIAL**、**PROCESSING**、**COMPLETED**：构建状态。

---

## class openjiuwen.dev_tools.agent_builder.utils.enums.ProgressStage

构建阶段（含 LLM Agent 与工作流专用阶段），如 `INITIALIZING`、`CLARIFYING`、`GENERATING_CONFIG`、`DETECTING_INTENTION`、`GENERATING_WORKFLOW_DESIGN` 等。

---

## class openjiuwen.dev_tools.agent_builder.utils.enums.ProgressStatus

* **PENDING**、**RUNNING**、**SUCCESS**、**FAILED**、**WARNING**。

---

## AgentTypeLiteral

`Literal["llm_agent", "workflow"]`，用于类型标注。

---

## class openjiuwen.dev_tools.agent_builder.utils.progress.ProgressStep

数据类：单步进度（`stage`、`status`、`message`、`details`、`timestamp`、`duration`、`error`）。

### to_dict() -> Dict[str, Any]

序列化为可 JSON 化的字典。

---

## class openjiuwen.dev_tools.agent_builder.utils.progress.BuildProgress

数据类：会话级总进度（`session_id`、`agent_type`、`current_stage`、`steps`、`overall_progress` 等）。

### to_dict() -> Dict[str, Any]

序列化整体进度。

---

## class openjiuwen.dev_tools.agent_builder.utils.progress.ProgressReporter

构建进度上报器，支持回调通知。

**参数**：

* **session_id**(str)：会话 ID。
* **agent_type**(str)：`'llm_agent'` 或 `'workflow'`。

### add_callback / remove_callback

注册或移除 `Callable[[BuildProgress], None]`。

### start_stage / update_stage / complete_stage / fail_stage / warn_stage

推进阶段、更新文案或标记失败/警告。

### get_progress() -> BuildProgress

返回当前 `BuildProgress` 实例。

### complete(message: str = "Build completed") -> None

标记整体完成（进度 100%）。

---

## class openjiuwen.dev_tools.agent_builder.utils.progress.ProgressManager

管理多会话 [ProgressReporter](#class-openjiuwendevtoolsagent_builderutilsprogressprogressreporter)。

### create_reporter(session_id: str, agent_type: str) -> ProgressReporter

创建或返回已存在的 Reporter。

### get_reporter / remove_reporter / get_progress

按会话查询或删除。

---

## progress_manager

**ProgressManager** 全局单例，供执行器与外部查询 [AgentBuilder.get_progress](../agent_builder.md) 使用。

---

## func openjiuwen.dev_tools.agent_builder.utils.utils.extract_json_from_text

从文本中提取首个 Markdown JSON 代码块内容，若无则返回原文。

---

## func openjiuwen.dev_tools.agent_builder.utils.utils.format_dialog_history

将 `{"role","content"}` 列表格式化为多行字符串。

---

## func openjiuwen.dev_tools.agent_builder.utils.utils.safe_json_loads

安全解析 JSON，失败返回 `default`。

---

## func openjiuwen.dev_tools.agent_builder.utils.utils.validate_session_id

校验会话 ID 是否仅含字母、数字、下划线与连字符。

---

## func openjiuwen.dev_tools.agent_builder.utils.utils.merge_dict_lists

按唯一键合并字典列表并去重。

---

## func openjiuwen.dev_tools.agent_builder.utils.utils.deep_merge_dict

深度合并字典（不原地修改传入的 `base` 语义以源码为准）。

---

## func openjiuwen.dev_tools.agent_builder.utils.utils.load_json_file

读取 JSON 文件为字典；文件不存在或解析失败时抛错（见源码 `ValidationError`）。
