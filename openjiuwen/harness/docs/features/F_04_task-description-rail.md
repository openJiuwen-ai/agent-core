# TaskDescriptionRail — 任务描述文件钉入常驻 system-prompt section

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-09-15 |
| 范围 | `openjiuwen/harness/rails/task_description_rail.py`、`openjiuwen/harness/prompts/sections/__init__.py`（`SectionName.TASK_DESCRIPTION`）、`openjiuwen/harness/rails/__init__.py`（导出） |
| 测试基线 | `tests/unit_tests/harness/rails/test_task_description_rail.py`（4 通过） |
| Refs | #<issue> |

## 背景

CI / 评测流水线里，任务通常先写到一个文件（如 `/app/task.md`）再启动 agent。若任务描述只出现在
会话历史里，会被上下文压缩（context compression）压掉；agent 长时间运行后丢失「最初要做什么」。

jiuwenswarm 先落地了一个 `TaskDescriptionRail`（`feat/task-description-reinjection` 分支），但它
放在了 `jiuwenswarm/agents/harness/common/rails/`。review 指出：该 rail 是**纯 harness 行为**，
只 import `openjiuwen.*`，没有任何产品耦合，应归入 agent-core，由 jiuwenswarm 只做配置。

本 feature 把该 rail 上移到 agent-core 的 `harness/rails/`，与 `HeartbeatRail` 等「注入 prompt
section」类 rail 同列。

## 决策

1. **落点 `harness/rails/task_description_rail.py`**：与 `HeartbeatRail`（同为「注入 section」范本）
   同目录，`TaskDescriptionRail(DeepAgentRail)`。
2. **rail priority = 80**：对齐 `HeartbeatRail`，在工具/规划梯队（90–95）之后、resilience/evolution
   梯队（70–60）之前。section 的**出现顺序**由 `PromptSection.priority` 决定（见下），与 rail
   priority 无关。
3. **section priority = 12**：在 `IDENTITY`（10）之后、`SAFETY`（20）之前 —— agent 先知道「我是谁」，
   紧接着看到「要做什么」，再看到规则。
4. **section 名走 `SectionName.TASK_DESCRIPTION`**（新增枚举项），不再硬编码字符串；`content` 用
   `{"cn": ..., "en": ...}` 语言映射（`PromptSection` 契约）。
5. **不引入 agent-core 配置**：rail 构造参数 `task_path: str` 由集成方传入；是否启用、路径从哪个配置
   读，都是集成方（jiuwenswarm）的决策。agent-core 不新增 `DeepAgentConfig` 字段。
6. **`before_invoke` 清理重读 + `before_model_call` 兜底重试**：文件缺失/为空时**不**标记
   `_injected`，下次 model call 重试，覆盖「任务文件异步挂载」的场景。
7. **`uninit` 撤净**：移除注入的 section，并把 `system_prompt_builder` 置空（`rails/AGENTS.md` 清单第 4 条）。

## 拒绝的方案

- **留在 jiuwenswarm**（原实现）：纯 harness 行为落到产品仓，制造与 `HeartbeatRail` 先例一样的
  fork；未来每个想「钉任务描述」的集成方都要重新写一遍。
- **加进 `DeepAgentConfig`**：把 `task_description.enabled/path` 变成框架配置字段。否 —— 这是个
  可选的、文件来源的行为 rail，构造参数已够；配置归属集成方更干净，也避免为单一 rail 扩 config 面。
- **只读一次（`init`）**：无法覆盖任务文件异步挂载（CI 里 task.md 晚于 agent 启动出现）的场景，
  故保留 `before_model_call` 重试。

## 验证

- `tests/unit_tests/harness/rails/test_task_description_rail.py`：
  - `test_injects_section_with_dict_content` —— 注入的 `PromptSection` 是 `{cn, en}` 语言映射；
  - `test_empty_file_is_not_marked_injected` —— 空文件不注入、下次 model call 补注；
  - `test_missing_file_is_not_marked_injected` —— 缺失文件不标记 injected；
  - `test_uninit_removes_section` —— `uninit` 移除 section 并清空 builder 引用。
- jiuwenswarm 侧 hot-reload 集成测试仍留在 `tests/unit_tests/agents/harness/test_task_description_rail.py`
  （改为从 `openjiuwen.harness.rails` import）。

## 已知遗留

- agent-core 侧尚无配置/装配入口：rail 由集成方直接 `TaskDescriptionRail(path)` 构造并
  `add_rail`/`register_rail`，未接入任何默认装配路径。是否把它并入默认 composition，留给
  pluggability 重构统一决定。
