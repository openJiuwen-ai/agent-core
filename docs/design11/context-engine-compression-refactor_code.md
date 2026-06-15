# Context Engine 压缩重构 — 技术设计

## 1. 三个改造目标（总览）

- **① 规则压缩**：单条 tool message 级别。在 `add_messages` 阶段只对新增的大工具结果做温和规则压缩；TTL 到期后按 context 级时间触发旧工具消息 sweep。
- **② Forked compact**：继承主 agent 的 `ContextWindow` 前缀做压缩，保持 `tools + system_messages + context_messages + compact prompt` 稳定；支持从尾部裁剪最近几条上下文消息。
- **③ 信息回注**：压缩后把关键状态（plan / plan_mode / skill / file / 子 agent）以稳定格式补回；

---

## 2. 组件 ① — 规则性 ToolMessage 压缩 + TTL

> 改造对象：`MessageOffloader`
> 原则：`add_messages` 阶段只做温和规则压缩；TTL 阶段由 context 级时间触发，对旧工具消息做简化 sweep，不调用 LLM。

### 2.1 处理流水线（`on_add_messages`）

```
对每条新进的 tool message:
  1. TRIGGER : 单条 token > 上下文窗口 × 20% → 进入规则流水线
  2. ROUTE   : 识别内容类型 → 分派对应规则压缩器
  3. CHECK   : 压缩后仍 > 上下文窗口 × 20% ?
  4. TRUNCATE: 是 → 头 2000 + 尾 2000 token 截断 卸载原文到文件/内存（复用 offload_messages）

TTL 阶段不复用上述单条阈值触发条件；由 context 级时间判断触发 sweep。
```

`add_messages` 阶段默认阈值为上下文窗口的 **20%**（`rule_compression_ratio=0.2`）。
TTL sweep 阶段默认使用更严格的 **10%**（`rule_compression_expired_ratio=0.1`），且不再用单条 token 阈值决定是否触发。

### 2.2 规则流水线

先识别内容形态，再选压缩器「按类型选最安全的方式压」。

识别顺序（越结构化越靠前）：

| 顺序 | 类型 | 标识 | 识别要点 |
| --- | --- | --- | --- |
| 1 | JSON array | `JSON_ARRAY` | `json.loads` 成功，顶层为 `list` |
| 2 | Git diff | `GIT_DIFF` | `diff --git`、`--- a/`、`@@ ... @@`、combined diff |
| 3 | HTML | `HTML` | `<!doctype html>`、`<html>`、`<head>`、`<body>` 等结构标签 |
| 4 | Search results | `SEARCH_RESULTS` | `file.py:42:content` 格式，≥30% 非空行匹配 |
| 5 | Build/log output | `BUILD_OUTPUT` | ERROR/WARN/INFO、Traceback、stack trace、pytest/npm/cargo 等 |
| 6 | Source code | `SOURCE_CODE` | Python/JS/Rust 等语言关键字模式 |
| 7 | Plain text | `PLAIN_TEXT` | 兜底 |

混合内容（code fence + JSON + grep 结果等同屏出现）：拆 section 后分别路由，再 `\n\n` 拼回（Headroom `MIXED` 路径）。首版可先不做，接口预留。

```python
# processor/offloader/rules/router.py  (新，接口占位)
class RuleContentRouter:
    def detect(self, content: str) -> ContentType: ...
    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult: ...

# 兼容旧引用
ContentRouter = RuleContentRouter
```

### 2.3 各类型规则压缩器（Headroom 对照）

以下均为**确定性规则/算法**，不调用 LLM。首版可先实现 Python 简化版，复杂逻辑后续对齐 Headroom Rust 实现。

#### 2.3.1 JSON array → SmartCrusher

- **适用**：工具返回结构化 JSON 数组（如列表查询、批量数据）。
- **思路**：结构保持型压缩，不做自然语言摘要。
- **两阶段**：
  1. **Lossless**：重复 schema 压成紧凑表格（CSV + schema header），无丢行。
  2. **Lossy**（仍超长）：保留开头/结尾行、error 行、数值异常、与用户 query 相关行；其余省略并附 summary。
- **Headroom 参考**：`smart_crusher.py` / `crates/headroom-core/.../smart_crusher/`

#### 2.3.2 Search results → SearchCompressor

- **适用**：grep / ripgrep / ag 输出（`file:line:content`）。
- **思路**：按文件分组，每文件保留 first/last + 高分命中行。
- **打分**：context 词命中、error/warning 信号、配置关键词。
- **输出**：仍保持 grep 格式；省略行附 `[... and N more matches in file.py]`。
- **Headroom 参考**：`search_compressor.rs`

#### 2.3.3 Build/log output → LogCompressor

- **适用**：pytest、npm、cargo、make 等构建/测试/运行日志。
- **思路**：按行分类（ERROR/FAIL/WARN/stack trace/summary），优先保留高分行及前后上下文。
- **短日志**：低于阈值（如 50 行）原样返回。
- **输出**：选中行 + `[N lines omitted: X ERROR, Y WARN, ...]`。
- **Headroom 参考**：`log_compressor.rs`

#### 2.3.4 Git diff → DiffCompressor

- **适用**：`git diff` / unified diff / combined diff。
- **思路**：**保留所有 `+`/`-` 变更行**，只削减变更附近过远的 context 行。
- **裁剪**：文件数 / hunk 数超限时，按变更量 + query 相关性保留高分 hunk。
- **短 diff**：低于阈值（如 50 行）原样返回。
- **Headroom 参考**：`diff_compressor.rs`

#### 2.3.5 HTML → HTMLExtractor

- **适用**：WebFetch / 爬虫类工具返回的完整 HTML。
- **思路**：提取正文（去 nav/script/style/footer），输出 markdown/纯文本，**不是 ML 摘要**。
- **依赖**：trafilatura（或等价库）；提取失败或不划算 → 原样返回。
- **Headroom 参考**：`html_extractor.py`

#### 2.3.6 Plain text → 通用规则（兜底）

- **适用**：无法归入以上类型的长文本。
- **首版策略**（不用 Kompress/LLM）：
  1. 空白折叠、重复行去重
  2. 仍超长 → 头 2000 + 尾 2000 token（与 §2.4 截断策略一致）
- **说明**：Headroom 对 plain text 默认走 Kompress（ML 选词），本模块**明确不做**，仅规则兜底。

#### 2.3.7 Source code → 暂不压缩（保护）

- Headroom 默认对近期代码、分析场景下的代码有保护策略（`protect_recent_code` 等）。
- **首版**：识别为 `SOURCE_CODE` 时跳过规则压缩，直接进入 §2.4 截断/卸载判断。
- 后续可选：接入 CodeAwareCompressor 或仅做 head/tail 截断。

### 2.4 仍超长：截断与卸载

`add_messages` 阶段规则压缩后仍超过当前阈值：

```
1. TRUNCATE : 保留头 2000 token + 尾 2000 token，中间插入省略标记
2. 仍超长   : 调用现有 offload_messages，原文存文件/内存，消息体留 handle + 摘要占位
```
与现有 `MessageOffloader` 的 handle / reload 机制兼容；**不在此阶段调用 LLM 生成摘要**。

TTL sweep 阶段采用更简单的规则：

```
1. 未压缩过的旧 ToolMessage：强制走规则压缩/刷新 metadata，不先判断 token 阈值。
2. 已压缩过的旧 ToolMessage：直接调用 offload_messages，保留当前压缩可见内容 + reload marker。
3. 跳过最近 N 条消息、protected tool、已经是 OffloadMixin 的 placeholder。
```


### 2.5 TTL 与触发控制

| 项 | 说明 |
| --- | --- |
| `rule_compression_ttl_seconds` | 默认 300s，配置在 `MessageOffloaderConfig` |
| `last_rule_compression_ttl_sweep_at` | `MessageOffloader` 按 `session_id:context_id` 记录最近一次 TTL sweep 时间 |
| 触发规则 | `now - last_rule_compression_ttl_sweep_at >= rule_compression_ttl_seconds` 时触发 |
| 首次请求 | 只初始化当前 context 的 sweep 时间，不立即 sweep |
| TTL 过期 | 触发旧工具消息 sweep；sweep 完成后更新该 context 的时间戳 |
| `rule_compression_ttl_keep_recent_messages` | 默认 8，TTL sweep 跳过最近 N 条消息 |

当前实现把 TTL 状态保存在 `MessageOffloader` processor 内部：

```python
_last_rule_compression_ttl_sweep_at_by_context: dict[str, float]
_pending_rule_compression_ttl_sweep_contexts: set[str]
```

触发逻辑不再遍历消息 metadata 查找过期消息。消息上的 `rule_compressed_at` 仅作为“是否已规则压缩过”和调试信息使用。


## 3. 组件 ② — ForkedCompression（统一压缩执行器）

### 3.1 目标

把压缩调用统一到 `ForkedCompressionExecutor`，让 compressor 可以复用同一种「主 agent 前缀 + 压缩提示词」调用方式。

当前已实现的能力：

1. **前缀稳定**：请求消息顺序固定为 `system_messages + context_messages + UserMessage(prompt)`。
2. **支持完整 `ContextWindow` 前缀**：`ForkedCompressionRequest.from_context_window(...)` 从 `ContextWindow` 读取 `system_messages`、`context_messages`、`tools`。
3. **支持裁剪末尾几条上下文**：`exclude_recent_messages=N` 只裁剪 `context_messages` 尾部，不裁剪 system prompt 和 compact prompt。
4. **透传 tools / output_parser**：`invoke()` 调用模型时传入 `tools`，如果 request 带 `output_parser` 也会透传。
5. **保留 usage / raw response**：`ForkedCompressionResult` 暴露 `content`，同时保留原始 `response` 和 `usage`。

当前暂未实现、后续可补：

- 针对「上下文太长 / 模型不稳定 / 账户欠费」等错误类型的细分恢复策略。
- 多阶段 fallback 和统一 token 预算截断策略。
- 将 `FullCompactProcessor` 主流程完全迁移到新的 forked executor 模式。

当前接口形态：

```python
@dataclass(frozen=True)
class ForkedCompressionRequest:
    prompt: str
    context_messages: list[BaseMessage]
    system_messages: list[BaseMessage] = field(default_factory=list)
    tools: list[Any] | None = None
    exclude_recent_messages: int = 0
    output_parser: Any = None

    @classmethod
    def from_context_window(
        cls,
        *,
        prompt: str,
        context_window: ContextWindow,
        exclude_recent_messages: int = 0,
        output_parser: Any = None,
    ) -> "ForkedCompressionRequest": ...
```

#### cc 提示词

CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.
- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.
Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.
Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:
1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly.
Your summary should include the following sections:
1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.
Here's an example of how your output should be structured:
<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>
<summary>
1. Primary Request and Intent:
   [Detailed description]
2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]
3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]
4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]
5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]
6. All user messages:
    - [Detailed non tool use user message]
    - [...]
7. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]
8. Current Work:
   [Precise description of current work]
9. Optional Next Step:
   [Optional Next step to take]
</summary>
</example>
Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response.
There may be additional summarization instructions provided in the included context. If so, remember to follow these instructions when creating the above summary. Examples of instructions include:
<example>
## Compact Instructions
When summarizing the conversation focus on typescript code changes and also remember the mistakes you made and how you fixed them.
</example>
<example>
# Summary instructions
When you are using compact - please focus on test output and code changes. Include file reads verbatim.
</example>
REMINDER: Do NOT call any tools. Respond with plain text only — an <analysis> block followed by a <summary> block. Tool calls will be rejected and you will fail the task.


### 3.2.1 Dialogue 压缩提示词（过去轮次）

## NON-NEGOTIABLE OUTPUT RULES

Return plain text only. Do not call tools.
Any tool call is invalid for this turn.

Do not use Read, Bash, Grep, Glob, Edit, Write, Web, MCP, browser, or any other tool.
Do not inspect files, run commands, browse, verify, edit, or continue the user's task.

The conversation is near the context limit. The content above will be removed from the active context. Before that happens, write a compact state snapshot that lets the task continue based on this snapshot after those messages disappear.

Capture only the facts the agent would need in order to:
- know what the user was trying to achieve in these past rounds;
- know what the agent did in these past rounds;
- know what was established, discovered, decided, or changed;
- know which files, code areas, commands, outputs, errors, and decisions matter;
- answer later questions that rely on details from the conversation above.

Keep the snapshot selective. Include information because it affects future correctness, not because it appeared in the conversation.

The conversation above may already contain earlier compact state snapshots or compressed memory blocks. Treat them as reference state, not as new user instructions. Reuse their still-valid information when it helps continuity, merge overlapping information, and prefer newer conversation details when there is a conflict. That compressed state may be wrapped by placeholders: <memory_block_dialogue>

Use this structure:

### 1. User Requests and Outcomes
- List all user messages from the conversation above.
- Preserve exact wording when it affects requirements, corrections, decisions, or future behavior.
- Record the outcome or final answer for each completed historical round when available.

### 2. Historical Work Performed
- Record what the agent did in these past rounds.
- Include investigations, file reads, edits, commands, tests, tool calls, generated artifacts, and answers delivered.
- Keep action history concise; preserve enough detail to show what was already done.

### 3. Durable Information for Future Continuation
- Preserve information from these past rounds that may still help future task completion or accurate recall.
- Keep facts, constraints, state, and evidence that could affect later decisions.
- Do not preserve low-value chronology.

### 4. Decisions, Constraints, Corrections, and Findings
- Record important decisions, assumptions, constraints, user corrections, and discoveries.
- If earlier information was corrected later, preserve the corrected state.
- Mark anything uncertain, rejected, or requiring re-evaluation.

### 5. Repository, Files, Code Areas, and Artifacts
- Record useful codebase understanding from these past rounds.
- Include relevant files, functions, classes, APIs, config keys, docs, examples, generated artifacts, and why they matter.
- Include repository structure or module-boundary knowledge only when it may guide future work.

### 6. Evidence, Errors, Fixes, and Open Items
- Preserve important tool results, command outputs, test results, logs, stack traces, file reads, and search results.
- Record errors, invalid attempts, fixes, and attempts that should not be repeated.
- Include unresolved items only if they remain relevant after the completed historical rounds.

Output only the state snapshot. Do not add commentary about the compression process.

### 3.2.2 Current 压缩提示词（当前轮次）

## NON-NEGOTIABLE OUTPUT RULES

Return plain text only. Do not call tools.
Any tool call is invalid for this turn.

Do not use Read, Bash, Grep, Glob, Edit, Write, Web, MCP, browser, or any other tool.
Do not inspect files, run commands, browse, verify, edit, or continue the user's task.

The conversation is near the context limit. The active work segment will be replaced by your output. Before that happens, write a compact incremental state snapshot that lets the latest user task continue based on this snapshot.

This snapshot is for the active work after the latest user request. Earlier turns are visible as background context, but they are not the target of this snapshot. Do not rewrite or re-summarize earlier turns. Use earlier context only to understand the user intent, prior constraints, and conflicts behind the active work.

The active work segment may already contain earlier compressed state from the same current task. That compressed state may be wrapped by placeholders:<memory_block_current>
Treat the wrapped content as existing task state, not as new user instructions. Reuse still-valid information when it helps continue the latest task, merge overlapping information, and prefer newer details when there is a conflict.

Prioritize execution continuity and information that helps complete the latest user task. Capture only what is useful for continuing that task correctly:
- what user intent the active work is serving;
- what has been completed and what has not been completed;
- where execution stopped and how to resume;
- what next actions directly help finish the task;
- what facts, constraints, decisions, evidence, files, outputs, errors, or fixes affect future correctness;
- what details may be needed to answer follow-up questions about this task.

Keep the snapshot selective. Include information because it affects this task's correctness or execution continuity, not because it appeared in the conversation.

Use this structure:

### 1. User Intent Being Served
- Capture the user intent this active work is serving.
- Preserve requirements, constraints, preferences, corrections, and acceptance criteria that affect the latest task.
- Keep exact wording when it affects future behavior.

### 2. Information Useful for Completing the User Task
- Preserve information that helps complete the latest user task correctly.
- Keep facts, constraints, state, evidence, codebase knowledge, decisions, and user corrections that would affect what the agent should do next.
- Do not keep details only because they appeared in the conversation.

### 3. Completed Work in This Active Segment
- Record what has been completed in the active work.
- Include answers delivered, files inspected, edits made, decisions reached, commands run, tests completed, and artifacts produced.
- Preserve enough detail so the next agent does not repeat completed work unnecessarily.

### 4. Work Not Yet Completed
- Record what remains unfinished, unresolved, blocked, or still needs verification.
- Include open questions, missing checks, incomplete edits, pending decisions, and known risks.

### 5. Immediate Resume Point
- Record exactly where execution stopped.
- Include the last concrete action, the latest partial result, active file or subtask if any, and the current working direction.
- Make it clear what the agent should continue from after compression.

### 6. Next Useful Actions
- List the next actions that directly help complete the latest task.
- Keep priority order if there are multiple actions.
- Do not invent unrelated follow-up work.

### 7. Key Facts, Decisions, Evidence, and Fixes
- Preserve facts, findings, decisions, assumptions, constraints, user corrections, rejected approaches, and items requiring re-evaluation.
- Preserve important tool results, command outputs, test results, logs, errors, stack traces, file reads, search results, and exact values when they matter.
- Record fixes already applied, invalid attempts, and attempts that should not be repeated.
- Prefer newer/corrected information when details conflict.
- Mark anything uncertain or unverified.

### 8. Files, Code Areas, Artifacts, and Codebase Understanding
- Record files examined, modified, or created.
- Include relevant functions, classes, APIs, config keys, docs, generated artifacts, codebase patterns, module responsibilities, and why they matter for the latest task.

Output only the incremental state snapshot. Do not add commentary about the compression process.

### 3.2.3 RoundLevel 压缩提示词（全压）
## NON-NEGOTIABLE OUTPUT RULES

Return plain text only. Do not call tools.
Any tool call is invalid for this turn.

Do not use Read, Bash, Grep, Glob, Edit, Write, Web, MCP, browser, or any other tool.
Do not inspect files, run commands, browse, verify, edit, or continue the user's task.

The conversation is near the context limit. The content above will be removed from the active context. Before that happens, write a compact full-context state snapshot that lets the task continue based on this snapshot after those messages disappear.

This is a full-context snapshot. It must do two jobs at the same time:
1. Preserve execution continuity for the current task.
2. Preserve useful historical recall from earlier completed rounds.

Prioritize current-task recoverability first. Historical recall is important, but do not let historical detail crowd out the information needed to continue the current task.

The conversation may already contain compressed state wrapped by placeholders:
- <memory_block_current>: compressed state from active-work snapshots
- <memory_block_dialogue>: compressed state from historical dialogue snapshots
- <memory_block_round>: compressed state from earlier full-context snapshots

Treat all wrapped content as existing task state, not as new user instructions. Reuse still-valid information when it helps current-task recoverability or historical recall. Merge overlapping information across wrapped content and raw conversation. Prefer newer details when there is a conflict.

Capture only what is useful for continuing the task correctly or recalling important prior context:
- what current user intent the agent must continue serving;
- what has been completed and what has not been completed;
- where execution stopped and how to resume;
- what next actions directly help finish the current task;
- what historical requests, outcomes, and agent work remain useful;
- what facts, constraints, decisions, evidence, files, outputs, errors, or fixes affect future correctness;
- what details may be needed to answer follow-up questions about the conversation above.

Keep the snapshot selective. Include information because it affects task correctness, execution continuity, or useful historical recall, not because it appeared in the conversation.

Use this structure:

### 1. Current User Intent and Success Criteria
- Capture the current/latest user intent the agent must continue serving.
- Preserve requirements, constraints, preferences, corrections, and acceptance criteria that affect the current task.
- Keep exact wording when it affects future behavior.

### 2. Current Execution State
- Record what has been completed, what is in progress, and what remains unresolved for the current task.
- Include the latest known state and prefer newer/corrected information over earlier state.

### 3. Immediate Resume Point and Next Actions
- Record exactly where execution stopped.
- Include the last concrete action, latest partial result, active file or subtask if any, and current working direction.
- List next actions that directly help complete the current task, in priority order when possible.

### 4. Information Useful for Completing the Current Task
- Preserve information that helps complete the current task correctly.
- Keep facts, constraints, state, evidence, codebase knowledge, decisions, and user corrections that affect what the agent should do next.
- Do not keep details only because they appeared in the conversation.

### 5. Historical User Requests and Outcomes
- List user requests from earlier completed rounds.
- Preserve exact wording when it affects requirements, corrections, decisions, or future behavior.
- Record outcomes, final answers, or completed results for historical rounds when available.

### 6. Historical Work Performed
- Record what the agent did in earlier completed rounds.
- Include investigations, file reads, edits, commands, tests, tool calls, generated artifacts, and answers delivered.
- Keep action history concise; preserve enough detail to show what was already done.

### 7. Durable Historical Information
- Preserve historical facts, constraints, findings, decisions, and evidence that may still help future continuation or accurate recall.
- Merge overlapping information from earlier compressed state.
- Prefer newer/corrected information when details conflict.

### 8. Files, Code Areas, Artifacts, and Codebase Understanding
- Record files examined, modified, or created across the conversation.
- Include relevant functions, classes, APIs, config keys, docs, generated artifacts, codebase patterns, module responsibilities, public APIs, and why they matter.
- Keep repository structure or module-boundary knowledge when it may guide future work.

### 9. Evidence, Errors, Fixes, and Invalid Attempts
- Preserve important tool results, command outputs, test results, logs, errors, stack traces, file reads, search results, and exact values when they matter.
- Record errors, fixes already applied, invalid attempts, rejected approaches, and attempts that should not be repeated.
- Mark anything uncertain, unverified, or requiring re-evaluation.

### 10. Open Work, Blockers, and Risks
- Preserve pending tasks, blockers, open questions, unresolved work, missing checks, and known risks.
- Separate current-task open work from historical leftovers when possible.

Output only the full-context state snapshot. Do not add commentary about the compression process.

## 4. 组件 ③ — 信息回注（Post-Compact Reinjection）

压缩后不能把对话历史原样保留，但主 agent 仍需要 **plan / 已读文件 / skill / 子 agent 任务** 等关键状态才能继续工作。本组件在 压缩前建立 在压缩后注回，把上述状态以 **稳定格式的 UserMessage** 补回上下文。


### 4.3 抽象方案（Reinjector 通用化）

`FullCompactStateReinjector`（`util.py`）已有 `register_builder` / `iter_builders`，但：

- 绑死在 `FullCompactProcessor` 里实例化；
- builder 签名耦合 `processor`（`build_*_reinjected_content(processor, ...)`）。

目标：新建 `compressor/reinjection/`，任意 compressor 可复用：

```python
# compressor/reinjection/reinjector.py  (新, 从 util.py 抽出)
class StateReinjector:
    def register(self, name: str, label: str, builder: ReinjectBuilder) -> None: ...
    def build_messages(self, ctx: ReinjectContext,
                       only: list[str] | None = None) -> list[BaseMessage]: ...

class ReinjectBuilder(Protocol):
    def __call__(self, ctx: ReinjectContext) -> str | list[BaseMessage]: ...

@dataclass
class ReinjectContext:
    session_state: dict[str, Any]
    source_messages: list[BaseMessage]
    messages_to_keep: list[BaseMessage]
    workspace_root: str | None
    config: FullCompactProcessorConfig  # 或更窄的 ReinjectConfig
    truncate: Callable[[str], str]
```

| 类别 | 状态 | 说明 |
| --- | --- | --- |
| `plan` | stub | Plan 文件全文 |
| `plan_mode` | 已有 | Plan mode metadata +（待补）约束文案 |
| `skills` | 已有 | 近期 skill 读取 |
| `task_status` | 需重定义 | 后台子 agent / team member-task 状态指针（现实现为外层 task loop） |
| `file` / **`read_file`** | stub | **Read 工具注回**（CC 核心；见 §4.4.5） |
| `tool_result_hint` | 工具函数已有 | 压缩式 tool result 提示 |
| `todo` | 待补 | Todo 当前进度快照（`subagent` 指针已并入 `task_status`） |
| `custom_*` | 预留 | 业务扩展 |


---

#### 4.4.1 `plan` — Plan 文件全文

**目标**：压缩后仍让主 agent 知道当前 plan 文件的完整内容，避免 `exit_plan_mode` 的工具结果被压缩后丢失。

**信息来源**：

- DeepAgent 的 plan 文件不应从历史 ToolMessage 里反推；可靠来源是 session state + workspace 文件约定。
- session state 中读取 `plan_mode.plan_slug`：
  - 优先读顶层 `session_state["plan_mode"]`；
  - 兜底读 `session_state["deepagent"]["plan_mode"]`，避免顶层镜像尚未刷新。
- workspace root 从 `ModelContext` 获取（如 `context.workspace_dir()` / context 内部 workspace 信息）。
- plan 文件路径按约定派生：`<workspace_root>/.plans/<plan_slug>.md`。

**注回策略**：

```python
def build_plan_reinjected_content(ctx: ReinjectContext) -> str:
    session_state = ctx.session_state
    plan_mode = session_state.get("plan_mode") or session_state.get("deepagent", {}).get("plan_mode")
    plan_slug = plan_mode.get("plan_slug") if isinstance(plan_mode, dict) else None
    if not plan_slug or not ctx.workspace_root:
        return ""

    plan_path = Path(ctx.workspace_root) / ".plans" / f"{plan_slug}.md"
    if not plan_path.exists():
        return ""

    plan_text = plan_path.read_text(encoding="utf-8")
    return ctx.truncate(
        f"Current plan file: {plan_path}\n\n"
        f"{plan_text}"
    )
```

**注回格式**：

```text
<state_marker>
[PLAN]
Current plan file: <workspace_root>/.plans/<slug>.md

<plan markdown content>
```

**约束**：

- 不从历史对话或 `exit_plan_mode` tool result 抽取 plan，全量内容以文件为准。
- 不在 core context 模块直接依赖 harness 的 `resolve_plan_file_path`，避免 core → harness 反向依赖；只复用 `.plans/<slug>.md` 这个稳定文件约定。
- 文件不存在、slug 缺失、workspace 缺失时静默跳过，不注回空 plan。
- 注回内容必须走统一截断，避免 plan 文件自身过大导致再次触发压缩。


#### 4.4.2 `plan_mode` — Plan 模式状态

**目标**：压缩后保留 DeepAgent 当前是否仍处于 Plan 模式，以及 Plan 模式下必须遵守的行为约束。`plan_mode` 不负责注回 plan 文件全文；全文由 `plan` builder 负责。

**信息来源**：

- session state 中读取 `plan_mode`：
  - 优先读顶层 `session_state["plan_mode"]`；
  - 兜底读 `session_state["deepagent"]["plan_mode"]`。
- 关键字段：
  - `mode`：当前模式，如 `plan` / `normal`；
  - `pre_plan_mode`：进入 plan 前的模式，用于退出后恢复；
  - `plan_slug`：当前 plan 文件标识，仅作为引用，不展开文件内容。

**注回策略**：

```python
def build_plan_mode_reinjected_content(ctx: ReinjectContext) -> str:
    plan_mode = (
        ctx.session_state.get("plan_mode")
        or ctx.session_state.get("deepagent", {}).get("plan_mode")
    )
    if not isinstance(plan_mode, dict):
        return ""

    mode = plan_mode.get("mode") or "normal"
    pre_plan_mode = plan_mode.get("pre_plan_mode")
    plan_slug = plan_mode.get("plan_slug")

    lines = [
        "Current plan-mode status for this session:",
        f"- Active mode: {mode}.",
    ]
    if pre_plan_mode:
        lines.append(f"- Previous mode before entering plan mode: {pre_plan_mode}.")
    if plan_slug:
        lines.append(f"- Active plan identifier: {plan_slug}.")

    if mode == "plan":
        lines.extend([
            "",
            "Plan-mode constraints:",
            "- Only planning is allowed; do not implement the plan yet.",
            "- Do not modify files except the active plan file.",
            "- Use read-only exploration tools unless editing the plan file.",
            "- End planning through exit_plan_mode when the plan is ready for user approval.",
            "- Use ask_user only for clarification or choosing between approaches, not for plan approval.",
        ])

    return ctx.truncate("\n".join(lines))
```

**注回格式**：

```text
<state_marker>
[PLAN_MODE]
Current plan-mode status for this session:
- Active mode: plan.
- Previous mode before entering plan mode: normal.
- Active plan identifier: <slug>.

Plan-mode constraints:
- Only planning is allowed; do not implement the plan yet.
- Do not modify files except the active plan file.
- Use read-only exploration tools unless editing the plan file.
- End planning through exit_plan_mode when the plan is ready for user approval.
- Use ask_user only for clarification or choosing between approaches, not for plan approval.
```

**约束**：

- 不重复注回 plan 文件全文，避免和 `plan` builder 产生重复大块内容。
- `mode != "plan"` 时只注回轻量 metadata，不注入 Plan 工作流约束。
- `plan_slug` 只是状态引用；需要读取 plan 内容时依赖 `plan` builder。
- 约束文案应保持稳定、简短，避免把完整 `MODE_INSTRUCTIONS` prompt 原样复制进上下文。


#### 4.4.3 `skills` — 已读取的 Skill

**目标**：压缩后保留本轮任务已经读取并生效的 Skill 内容，避免 agent 忘记必须遵守的技能步骤、检查清单和约束。

**信息来源**：

- 首选识别专用工具 `skill_tool`：
  - `skill_name` 表示被读取的技能名；
  - `relative_file_path` 为空或为 `SKILL.md` 时表示读取主技能文件；
  - tool result 中的 `skill_directory` / `skill_content` 是最可靠的注回内容来源。
- 兼容旧路径：识别 `read_file` 读取 `**/SKILL.md` 的 round。
- `list_skill` 只表示“列出/筛选了可用技能”，不是已读取全文；可作为最近选择结果的辅助信息，但不应单独视为 invoked skill。

**现状**：

- 现有 `build_skill_reinjected_content` 通过扫描历史 completed API rounds 实现：
  - 逆序遍历 rounds；
  - 跳过已经保留在 `messages_to_keep` 里的 round；
  - 如果 round 内存在 `read_file` 且路径为 `**/SKILL.md`，就把整轮序列化后注回；
  - 最多注回 `reinject_recent_skills` 轮，默认 3。
- 差距：当前未识别 `skill_tool`，但 DeepAgent 的 SkillUseRail 会注册专用 `skill_tool` 来读取 skill 内容。

**注回策略**：

```python
def build_skill_reinjected_content(ctx: ReinjectContext) -> list[BaseMessage]:
    selected = []
    keep_signatures = {message_signature(m) for m in ctx.messages_to_keep}

    for round_messages in reversed(group_completed_api_rounds(ctx.source_messages)):
        if round_overlaps_keep(round_messages, keep_signatures):
            continue

        skill_snapshot = extract_skill_tool_snapshot(round_messages)
        if skill_snapshot is None:
            skill_snapshot = extract_read_file_skill_snapshot(round_messages)
        if skill_snapshot is None:
            continue

        selected.append(skill_snapshot)
        if len(selected) >= ctx.config.reinject_recent_skills:
            break

    selected.reverse()
    return [
        UserMessage(content=f"{state_marker}\n[SKILLS]\n{ctx.truncate(render_skill_snapshot(s))}")
        for s in selected
    ]
```

`extract_skill_tool_snapshot(round_messages)`：

- 查找 assistant tool call `name == "skill_tool"`；
- 解析参数：
  - `skill_name`
  - `relative_file_path`，默认 `SKILL.md`；
- 只把主技能文件（空 / `SKILL.md`）作为 invoked skill 全文注回；
- 查找对应 `ToolMessage`，从结果中提取：
  - `skill_directory`
  - `skill_content`
- 输出稳定结构：

```text
Skill: <skill_name>
Path: <skill_directory>/SKILL.md

<skill_content>
```

`extract_read_file_skill_snapshot(round_messages)`：

- 兼容现有实现；
- 查找 `read_file` tool call，路径满足 `**/SKILL.md`；
- 从对应 ToolMessage 中提取文件内容；
- 无法结构化解析时，可以退回到整轮序列化。

**注回格式**：

```text
<state_marker>
[SKILLS]
Skill: <skill_name>
Path: <skill_directory>/SKILL.md

<SKILL.md content>
```

**约束**：

- 优先使用 `skill_tool` 结果，不再只依赖 `read_file **/SKILL.md`。
- `list_skill` 结果不等同于已读取 Skill；不能单独作为 `[SKILLS]` 全文注回来源。
- 跳过已保留在 `messages_to_keep` 中的 skill round，避免重复。
- 限制最近 N 个 Skill（`reinject_recent_skills`），并对每个注回块走统一截断。
- 后续可在 SkillRail 或 tool 层维护 `session_state["invoked_skills"]` 状态表；builder 优先读状态表，fallback 到历史 round 扫描。


#### 4.4.4 后台子 Agent / Team 协作状态

**目标**：压缩后保留正在运行或尚未取回结果的后台任务状态，避免主 agent 重复 spawn 子 agent / 重复创建 team member / 重复分配任务，并让它知道后续应通过哪些工具继续查询或推进。

**DeepAgent async subagent 来源**：

- 适用于 `enable_async_subagent=True` 的 `sessions_spawn`。
- 运行态由 `SessionToolkit` 维护：
  - `task_id`
  - `sub_session_id`
  - `description`
  - `status`：`running` / `completed` / `error` / `canceled`
  - `result`
  - `error`
- `SessionToolkit` 在 `sessions_spawn` 时写入 running；任务完成/失败后由 task loop handler 更新。
- 状态采集应发生在 harness/subagent 层的 async hook 中，写入 session state，例如：

```python
session.update_state({
    "background_tasks": [
        {
            "task_id": row.task_id,
            "sub_session_id": row.sub_session_id,
            "description": row.description,
            "status": row.status,
            "result_hint": truncate(row.result),
            "error": truncate(row.error),
        }
        for row in session_toolkit.list_all()
        if row.status in {"running", "completed", "error", "canceled"}
    ]
})
```

**Team 模式来源**：

Team 模式中 leader / member 不是共享完整对话状态，而是通过 **Team DB task board + message mailbox + 事件唤醒** 协作：

- leader 通过 `create_task` / `update_task` / `send_message` 推进团队；
- member 通过 `view_task` 查看任务，通过 `claim_task` 领取/完成任务，通过 `send_message` 回复；
- task/message/member 状态的事实来源是 `TeamBackend` 背后的 DB / manager；
- 事件总线只负责唤醒 idle agent 重新查看 task board 或 mailbox，不是状态真源。

Team compact 注回不读取 member 的完整 DeepAgentState / context / workspace，只采集轻量协作指针：

```python
session.update_state({
    "team_task_status": {
        "team_name": team_backend.team_name,
        "lifecycle": "running|paused|stopped",
        "members": [
            {
                "member_name": member.member_name,
                "role": member.role,
                "status": member.status,
            }
            for member in members
            if member.status not in {"shut_down"}
        ],
        "open_tasks": [
            {
                "task_id": task.task_id,
                "title": task.title,
                "status": task.status,
                "assignee": task.assignee,
                "blocked_by": task.blocked_by,
            }
            for task in tasks
            if task.status not in {"completed", "cancelled"}
        ],
        "has_unread_messages": has_unread_messages,
    }
})
```

**注回策略**：

```python
def build_task_status_reinjected_content(ctx: ReinjectContext) -> str:
    lines = []

    background_tasks = ctx.session_state.get("background_tasks") or []
    for task in background_tasks:
        if task["status"] == "running":
            lines.append(
                f'Background agent "{task["description"]}" '
                f'({task["task_id"]}) is still running. Do NOT spawn a duplicate.'
            )
        elif task["status"] in {"completed", "error"}:
            lines.append(
                f'Background agent "{task["description"]}" '
                f'({task["task_id"]}) status={task["status"]}. '
                "Check the stored result/error before spawning another task."
            )

    team_status = ctx.session_state.get("team_task_status")
    if isinstance(team_status, dict):
        lines.append(f'Team "{team_status["team_name"]}" current collaboration state:')
        lines.append("- Active members:")
        for member in team_status.get("members", []):
            lines.append(
                f'  - {member["member_name"]}: role={member.get("role", "")}, '
                f'status={member["status"]}'
            )
        lines.append("- Open tasks:")
        for task in team_status.get("open_tasks", []):
            assignee = task.get("assignee") or "unassigned"
            lines.append(
                f'  - #{task["task_id"]} [{task["status"]}] '
                f'{task["title"]} ({assignee})'
            )
        if team_status.get("has_unread_messages"):
            lines.append("- Team has unread messages; use team messaging tools to inspect/continue.")

    return ctx.truncate("\n".join(lines))
```

**注回格式**：

```text
<state_marker>
[TASK_STATUS]
Background agent "explore auth flow" (task-123) is still running. Do NOT spawn a duplicate.

Team "backend-refactor" current collaboration state:
- Active members:
  - backend-dev: role=teammate, status=busy
  - reviewer: role=teammate, status=ready
- Open tasks:
  - #api-contract [claimed] Update API contract docs (backend-dev)
  - #tests [pending] Add regression tests (unassigned)
- Team has unread messages; use team messaging tools to inspect/continue.
```

**约束**：

- 不注回外层 task loop 的 `iteration` / `pending_follow_ups` / `stop_reason`；如确需保留，应另建 `loop_status` / `outer_loop_status`。
- 不读取或注回 member 的完整对话历史、DeepAgentState、workspace 文件、memory、tool history。
- Team 模式以 task board 为主，member state 只作为“谁存在、谁忙、谁异常、谁可恢复”的轻量指针。
- Context reinjector 只读 session state 并格式化；DeepAgent / Team 的异步状态采集由 harness/team 层负责，避免 core context 直接依赖 harness 或 agent_teams。
- running 任务必须明确提示不要重复 spawn；completed/error 任务只给结果指针或短摘要，完整结果通过对应工具或输出文件查询。


#### 4.4.5 `read_file` — Read 工具注回（file builder 核心）

**目标**：压缩后保留最近读取过的关键文件内容，让 agent 不必因为历史 `read_file` ToolMessage 被压缩而重新读取文件；同时保留文件路径、行数和是否 partial read 等元信息，便于继续编辑或定位。

**状态来源**：

- 首版直接从 compact 前的 `source_messages` 解析 `read_file` tool round。
- 这样不需要把文件内容额外复制进 session state，避免 session checkpoint 膨胀。
- 与 `skills` builder 保持一致：都基于即将被压缩的 completed API rounds 提取需要注回的状态。
- `_FILE_READ_REGISTRY` 继续只服务 edit/write 前置校验，不作为 compact 注回来源。

**可解析信息**：

`ReadFileTool.invoke()` 的成功 ToolOutput 已包含：

```python
{
    "content": content,
    "file_path": file_path,
    "line_count": line_count,
}
```

`read_file` tool call 参数可补充：

- `file_path`
- `offset`
- `limit`

因此可以从 `AssistantMessage.tool_calls` 找 `read_file` 调用，再通过 `tool_call_id` 找对应 `ToolMessage`，从 ToolMessage 中解析 `file_path` / `content` / `line_count`。

**注回选择策略**：

```python
def build_file_reinjected_content(ctx: ReinjectContext) -> str:
    preserved_paths = collect_read_file_paths(ctx.messages_to_keep)
    candidates = []

    for round_messages in reversed(group_completed_api_rounds(ctx.source_messages)):
        for tool_call in iter_tool_calls(round_messages, name="read_file"):
            result_text = find_tool_result_text(round_messages, tool_call.id)
            snapshot = parse_read_file_result(tool_call, result_text)
            if snapshot is None:
                continue
            if snapshot.file_path in preserved_paths:
                continue
            if is_excluded_from_reinject(snapshot.file_path):
                continue
            candidates.append(snapshot)

    candidates = dedupe_by_file_path_keep_latest(candidates)
    selected = fit_budget(candidates, max_files=5, total_tokens=50000, per_file_tokens=5000)
    return ctx.truncate(render_read_file_snapshots(selected))
```

**注回内容**：

- 对最近读取的小文件：注回文件路径、行数、是否 partial、文件内容。
- 对超预算文件：保留路径、行数和 head/tail 截断内容，或降级为 file reference。
- 如果同一文件的原 `read_file` round 已在 `messages_to_keep` 中，则跳过，避免重复。
- 同一路径多次读取时保留最近一次。

**注回格式**：

```text
<state_marker>
[READ_FILE]
Recently read file: <absolute_path>
Lines returned: <line_count>
Partial read: false

<file content or truncated file content>
```

**约束**：

- 不引入 session 级全文状态表，避免把读过的文件内容复制进 checkpoint。
- 不使用 `_FILE_READ_REGISTRY` 作为 compact 注回来源。
- 不注回二进制、图片、PDF 原始字节；这类结果只保留路径和提示。
- 注回内容必须有文件数、总 token、单文件 token 上限。
- 只能恢复本次 compact 的 `source_messages` 中仍能看到的 read_file 结果；更早一轮 compact 前的原始 ToolMessage 不再保证可恢复。
- 如果未来需要跨多轮 compact 保留文件全文，再考虑文件 handle / 外部缓存，而不是 session state 直接存大块内容。


#### 4.4.7 `todo` — Todo 当前进度快照

**目标**：压缩后保留当前执行进度，让 agent 知道哪些任务已完成、哪个任务正在执行、哪些任务还待处理。`todo` 注回不替代 `plan`：`plan` 是方案文档，`todo` 是执行进度。

**是否最新**：

- 最新性以当前 session 的 `todo.json` 文件为准。
- `todo_create` / `todo_modify` / `todo_list` 使用同一套 `TodoTool` 持久化逻辑；创建或修改后会写入 session 专属文件。
- 因此 compact 时直接读取 `todo.json` 得到的是当前 todo 状态，而不是历史 ToolMessage 中的旧状态。

**信息来源**：

- Todo 文件按 session 隔离：

```python
<todo_workspace>/<session_id>/todo.json
```

- 文件内容是 `TodoItem` 列表，主要字段：
  - `id`
  - `content`
  - `status`
  - `selected_model_id`（可选）

**注回策略**：

```python
def build_todo_reinjected_content(ctx: ReinjectContext) -> str:
    session = ctx.session
    session_id = session.get_session_id()
    todo_path = Path(todo_workspace) / session_id / "todo.json"
    if not todo_path.exists():
        return ""

    todos = parse_todo_json(todo_path)
    if not todos:
        return ""

    selected = select_todos_for_reinject(todos)
    return ctx.truncate(render_todo_snapshot(selected))
```

`select_todos_for_reinject` 优先级：

1. `in_progress`
2. `pending`
3. `blocked` / `cancelled`（如果存在且影响后续）
4. 最近少量 `completed`

**注回格式**：

```text
<state_marker>
[TODO]
Current todo list:
- [in_progress] id=impl content=Implement read_file reinjection
- [pending] id=test content=Add compact reinjection tests
- [completed] id=design content=Update design doc
```

**约束**：

- 不从历史 `todo_create` / `todo_modify` ToolMessage 反推 todo 状态。
- 不注回 todo 工具调用历史，只注回当前文件快照。
- `todo.json` 不存在或为空时跳过。
- 内容过长时优先保留 `in_progress` 和 `pending`，裁剪 completed。
- `plan_mode == "plan"` 时可跳过或降级 todo 注回，避免和 plan 文件职责混淆；normal/build 执行阶段建议注回。
- 原 `subagent` 独立注回项不再保留；后台子 agent / team member-task 状态统一由 `task_status` 注回。
