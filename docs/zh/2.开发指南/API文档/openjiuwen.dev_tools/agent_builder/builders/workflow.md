# openjiuwen.dev_tools.agent_builder.builders.workflow

`openjiuwen.dev_tools.agent_builder.builders.workflow` 是 **工作流 Agent 构建子模块**，负责：

- `WorkflowBuilder`：意图检测 → 工作流设计 → DL 生成与反思 → Mermaid 与环检测 → 转工作流 DSL；
- `IntentionDetector`（workflow 包）：初始流程描述检测、优化意图检测；
- `DLGenerator`、`Reflector`、`CycleChecker`：DL 生成、格式校验与 Mermaid 环检测。

**子文档（详细 API）**：

- [WorkflowDesigner](workflow_designer.md)：SE 工作流设计（基础 / 分支 / 反思）。
- [DLTransformer](workflow/dl_transformer.md)：DL ↔ Mermaid ↔ 平台 DSL。

**包导出**：`WorkflowBuilder`、`IntentionDetector`、`WorkflowDesigner`、`DLGenerator`、`Reflector`、`DLTransformer`、`CycleChecker`。

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.builder.WorkflowBuilder

```python
class openjiuwen.dev_tools.agent_builder.builders.workflow.builder.WorkflowBuilder(
    llm: Model,
    history_manager: HistoryManager,
)
```

继承 [BaseAgentBuilder](../builders.md)。状态与资源与 LLM 子模块共用基类逻辑；`INITIAL` 时若用户未提供足够流程描述则返回提示文案并进入 `PROCESSING`；否则完成设计、生成 DL、生成 Mermaid 并返回。`PROCESSING` 中根据意图与 `_dl` 是否为空分支，支持继续设计、优化 DL 或转为最终 DSL。

**参数**：

* **llm**([Model](../../../../openjiuwen.core/foundation/llm/llm.md))：大模型实例。
* **history_manager**([HistoryManager](../executor.md))：会话历史。

### property workflow_name / workflow_name_en / workflow_desc / dl / mermaid_code

当前工作流名称、描述、DL 字符串与 Mermaid 代码（内部阶段结果，可能为 `None`）。

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.intention_detector.IntentionDetector

工作流专用意图检测：与 `builders.llm_agent` 中的 `IntentionDetector` 不同，用于「是否已描述流程」与「是否需要优化 Mermaid」。

**参数**：

* **llm**([Model](../../../../openjiuwen.core/foundation/llm/llm.md))：大模型实例。

### classmethod format_dialog_history(dialog_history: List[Dict[str, Any]]) -> str

将对话历史格式化为 `User:` / `Assistant:` 文本。

### staticmethod extract_intent(inputs: str) -> Dict[str, Any]

从模型输出中提取 JSON（基于 [extract_json_from_text](../utils.md)）。

### detect_initial_instruction(messages: List[Dict[str, Any]]) -> bool

是否已提供可执行的流程描述（`provide_process`）。

**异常**：

* **ApplicationError**：`WORKFLOW_INTENTION_DETECT_ERROR`。

### detect_refine_intent(messages: List[Dict[str, Any]], flowchart_code: str) -> bool

是否需要优化当前 Mermaid（`need_refined`）。

**异常**：

* **ApplicationError**：`WORKFLOW_INTENTION_DETECT_ERROR`。

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_generator.DLGenerator

根据工作流设计文档与节点 schema 生成 JSON 数组形式的 DL 字符串，或基于已有 DL/Mermaid 做 refine。

**参数**：

* **llm**([Model](../../../../openjiuwen.core/foundation/llm/llm.md))：大模型实例。

### generate(query: str, resource: Dict[str, List[Dict[str, Any]]]) -> str

生成 DL。

### refine(query: str, resource: Dict[str, List[Dict[str, Any]]], exist_dl: str, exist_mermaid: str) -> str

在已有 DL 与 Mermaid 上按用户指令修改。

### staticmethod load_schema_and_examples() -> tuple[str, str, str]

加载组件说明、schema 与示例（来自 `dl_assets`）。

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_reflector.Reflector

校验 DL JSON 数组：节点类型、参数引用、分支与占位符等；错误累积在 `errors` 列表。

### check_format(generated_dl: str) -> None

解析 DL 并逐项检查；若 JSON 非法则提前返回并在 `errors` 中记录。

### reset() -> None

清空校验状态。

### staticmethod extract_placeholder_content(input_str: str) -> Tuple[bool, List[str]]

检测 `${...}` 占位符。

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.cycle_checker.CycleChecker

**参数**：

* **llm**([Model](../../../../openjiuwen.core/foundation/llm/llm.md))：用于环检测提示的大模型。

### check_mermaid_cycle(mermaid_code: str) -> str

调用 LLM 判断 Mermaid 是否含不合理环路。

### staticmethod parse_cycle_result_json(inputs: str) -> Tuple[bool, str]

解析 JSON 得到 `(need_refined, loop_desc)`。

### check_and_parse(mermaid_code: str) -> Tuple[bool, str]

组合 `check_mermaid_cycle` 与 `parse_cycle_result_json`。

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.WorkflowDesigner

详见 [workflow_designer.md](workflow_designer.md)。

---

## class openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer.dl_transformer.DLTransformer

详见 [workflow/dl_transformer.md](workflow/dl_transformer.md)。
