# Context Engine 压缩重构 — 技术设计

## 1. 三个改造目标（总览）

- ### **① 规则压缩**：单条 tool message 级别。在 `add_messages` 阶段只对新增的大工具结果做温和规则压缩；构建 `ContextWindow` 时若推定 KV Cache TTL 已到期，且完整 `ModelContext` 占用达到 50%，再触发旧工具消息 sweep。

  ![alt text](image.png)

### openclaw
1、进入上下文的截断
  ┌────────────────┬──────────────┐
  │ context window │     硬顶     │
  ├────────────────┼──────────────┤
  │ < 100K tokens  │ 16,000 chars │
  ├────────────────┼──────────────┤
  │ ≥ 100K tokens  │ 32,000 chars │
  ├────────────────┼──────────────┤
  │ ≥ 200K tokens  │ 64,000 chars │
  └────────────────┴──────────────┘
  非常长的 10MB 文件内容的开头大约 64K chars...
     ...
     文件内容（换行处截断）
     [... 9950000 more characters truncated; rerun with narrower args if needed]
2、ttl失效后的处理
  Phase 1 — Soft Trim（ratio ≥ 0.3）

  对每条可裁 tool result：
  长度 ≤ 4000 chars  → 不动
  长度 > 4000 chars  → 保留头 1500 + 尾 1500
                         中间换成 "⚠️ [... middle content omitted — showing head and tail ...]"

  改完后重新算 ratio。如果还高，继续 Phase 2。

  Phase 2 — Hard Clear（ratio ≥ 0.5）

  对已 soft trim 过的 tool result，从最旧开始逐条替换成：
  "[Old tool result content cleared]"
  直到 ratio 降到 0.5 以下或所有可裁的都清了。（压缩的阈值）

### claude code
1、进入上下文的截断
  DEFAULT_MAX_RESULT_SIZE_CHARS = 50,000 字符

  <persisted-output>
  Output too large (10.00 MB). Full output saved to: .claude/sessions/<sessionId>/tool-results/<tool_use_id>.txt

  Preview (first 2000 bytes):
  <实际内容的前2000字节，在换行边界截断>
  ...
  </persisted-output>

2、ttl失效后的处理
  Phase 1

  特定工具的老旧消息替换成'[Old tool result content cleared]'

  效果（假设有 20 条工具结果）：

  t1  t2  t3  t4  t5  |  t6  t7  t8  t9  t10  t11  t12  t13  t14  t15  t16  t17  t18  t19  t20
  ←── 清除为占位符 ──→  |  ←────────── 保留最新的 5 条 ──────────────────────────────────────→

  Phase 2

  每一轮保持50k的预算，如果超过，在一轮内从大的消息开始卸载 知道满足预算位置。

  <persisted-output>
  Output too large (10.00 MB). Full output saved to: /home/user/project/.claude/sessions/a1b2c3/tool-results/abc123.txt

  Preview (first 2000 bytes):
  具体命令输出内容的前2000字节，
  会尽量在换行处截断
  ...
  </persisted-output>

### hermes
1、没有截断，只对特效信息进行处理 比如图片 将超过 4MB 或 8000px 的图片用 Pillow 实时压缩到目标大小。
2、全靠llm压缩，压缩前会对工具信息继续处理
  compress(messages)
    │
    ├── Phase 1: _prune_old_tool_results(messages)    ← 第一层：修改消息本身
    │    ├── 去重相同内容的 tool_result，保留最新的，旧的换为
    │    │   "[Duplicate tool output — same content as a more recent call]"
    │    ├── 替换超旧 tool_result 为一行摘要
    │    │   "[terminal] ran `npm test` -> exit 0, 47 lines output"
    │    ├── 多模态内容（base64 截图）→ 去掉图片数据，保留文本
    │    └── 裁剪 assistant 消息里的超大 tool_call 参数
    │
    ├── Phase 2: 确定压缩窗口（protect head + tail）
    │
    ├── Phase 3: _serialize_for_summary(turns_to_summarize) ← 第二层：序列化时截断
    │    │ 对每一条要总结的消息：
    │    │   content > 6000 字符 → 截成 head 4000 + tail 1500
    │    │   tool_call 参数 > 1500 字符 → 截成 head 1200
    │    │   ⚠️ 注意：这只是给 LLM 的输入截断了，原始消息不动
    │    │
    │    └── _generate_summary(turns) → 调辅助模型
    │
    └── Phase 4: 组装压缩后消息列表
         head + summary(LLM或fallback) + tail

### mimo
  1、进入上下文的截断
  阈值：
  MAX_LINES = 2000 行
  MAX_BYTES = 50 * 1024 = 50KB（≈ 12.5K tokens）

  截断策略（head+tail）：

  检查结尾 2048 chars 是否有 error/exception/failed 等错误模式

  有错误 → 70% budget 给 head，30% 给 tail
            head + "\n\n... N lines omitted — showing head and tail ...\n\n" + tail
            完整内容保存到文件

  无错误 → 退化为 head-only
            保留开头 + "...N lines/bytes truncated..."

  截断后发给模型的 tool result 样子：

  { role: "toolResult",
    toolName: "read",
    content: "文件开头（~35KB）...

  ... 50000 lines truncated...

  The tool call succeeded but the output was truncated.
  Full output saved to: /path/to/truncation/dir/tool_xxx
  Use Grep to search the full content or Read with offset/limit to view specific sections." }
2、TTL 过期后，对最近 3 轮之前的消息做：
   只清理两样：media 文件 + reasoning 块，不动 tool result 本身。

### opencode
1、进入上下文前的处理
 阈值：
  MAX_LINES = 2_000 行
  MAX_BYTES = 50 * 1024 = 50KB

  截断策略（head+tail）：

  preview() → headBytes ≈ 25KB + tailBytes ≈ 25KB
              head + (如果有 tail 则 + "\n\n" + tail)

  boundedPreview() → head + marker + tail

  截断后的内容：
  工具结果开头（~25KB）...

  ... output truncated; full content saved to /path/to/tool_output/tool_xxx ...

  工具结果结尾（~25KB）

  完整内容写到 {data_dir}/tool-output/tool_xxx，保留 7 天。

  没有额外的 image/strip 处理。 图片（file 类型的 content）直接保留不动，只对 text 内容截断。
2、没有ttl相关的实现
3、压缩

  这个项目没有 TTL 裁剪。 用的是一个叫 Context Epoch 的机制：

  Overflow 检测（compaction.ts:230-241）

  const compactIfNeeded = Effect.fn(function* (input) {
    // 估算当前请求（system + messages + tools）的 token 数
    // 如果超过 context - max(output, buffer)，触发 compaction
    if (estimate({ system, messages, tools }) <= context - Math.max(output, config.buffer))
      return false  // 没超，跳过
    return compactAfterOverflow(input)
  })

  Overflow 后触发 compaction（compaction.ts:177-229）

  1. select() — 从后往前扫，保留 config.compaction.keep.tokens（默认 8K tokens）的最近对话
  2. LLM 摘要 — 用模型把旧对话生成摘要 summary，格式固定（Goal / Progress / Key Decisions / Next Steps / Relevant
  Files）
  3. 摘要存为 compaction message — 写入 DB 作为 type: "compaction" 的消息

  Compaction 后的投影

  projector 和 toLLMMessages 在构建模型上下文时：
  - 跳过 type: "compaction" 之前的旧消息
  - 用 compaction summary 替换被压缩的内容
  - 保留最近的 ~8K tokens 对话原文

  tool output 在 compaction 时的处理

  compaction.ts:81-82 中序列化旧消息时：

  const truncate = (value: string) =>
    value.length > 2000 ? `${value.slice(0, 2000)}\n[truncated]` : value

  旧的 tool result 被截成 2000 chars 送去做摘要。摘要本身不含完整 tool output。


- **② Forked compact**：继承主 agent 的 `ContextWindow` 前缀做压缩，保持 `tools + system_messages + context_messages + compact prompt` 稳定；支持从尾部裁剪最近几条上下文消息。
- **③ 信息回注**：压缩后把关键状态（plan / plan_mode / skill / file / 子agent）以稳定格式补回；

---

## 2. 组件 ① — 规则性 ToolMessage 压缩 + TTL

> 改造对象：`MessageOffloader`
> 原则：`add_messages` 阶段只做温和规则压缩；TTL 阶段在 `get_context_window` 边界由 context 级时间触发，对旧工具消息做简化 sweep，不调用 LLM。
1 压缩流程里面的  单条 和 ttl失效的时候 大家都有吗
### 2.1 处理流水线（`on_add_messages`）

```
对每条新进的 tool message:
  1. CAPACITY: 上下文字符容量 = context_window_tokens × 3
  2. TRIGGER : 单条字符数 > 字符容量 × 20% → 进入规则流水线
  3. ROUTE   : 识别内容类型 → 分派对应规则压缩器
  4. TRUNCATE: 规则结果仍超预算 → 按固定字符预算保留头尾

TTL 阶段不复用上述单条触发条件。它在 `get_context_window` 时先判断 context
闲置时间，再判断完整 `ModelContext` 字符占用是否达到字符容量的 50%。
```

20%、50%、每 token 约 3 字符以及 TTL 单条 10% 目标预算均为内部固定规则，
不暴露为 `MessageOffloaderConfig` 参数。

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
# processor/offloader/rules/router.py
class RuleContentRouter:
    def detect(self, content: str) -> ContentType: ...
    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult: ...

# 兼容旧引用
ContentRouter = RuleContentRouter
```

规则模块按职责拆分：

```text
rules/
  types.py                       # ContentType / RuleContext / RuleCompressionResult
  common.py                      # token 计数、收益率和公共信号
  router.py                      # 只负责内容检测和压缩器分派
  json_array_compressor.py       # JSON array
  search_results_compressor.py   # grep / ripgrep search results
  log_compressor.py              # build / test / runtime log
  diff_compressor.py             # git diff
  html_compressor.py             # HTML 正文提取
  plain_text_compressor.py       # plain text 规则压缩
  source_code_compressor.py      # source code 保护性 passthrough
```

各压缩器统一实现 `compress(content, ctx) -> RuleCompressionResult`，不反向依赖
`RuleContentRouter`。新增或替换某一类规则时，不需要继续扩大 Router。

### 2.3 各类型规则压缩器（Headroom 对照）

以下均为**确定性规则/算法**，不调用 LLM。首版可先实现 Python 简化版，复杂逻辑后续对齐 Headroom Rust 实现。

#### 2.3.1 JSON array → SmartCrusher （shuli）

- **适用**：工具返回结构化 JSON 数组（如列表查询、批量数据）。
- **思路**：结构保持型压缩，不做自然语言摘要。
- **Token 计算**：通过 `RuleContext.count_tokens` 使用当前 `ModelContext.token_counter()`；TokenCounter 缺失或失败时才回退到字符估算。
- **最低收益**：候选结果至少节省 30% token（`rule_compression_min_savings_ratio=0.3`）才替换原文。
- **Lossless 分流**：
  1. 计算对象数组的平均 schema 密度：`所有行字段数总和 / (行数 × union 字段数)`。（1）
  2. schema 密度达到 80%（`rule_json_csv_min_density=0.8`）：转成 CSV。
  3. schema 密度低于 80%：转成紧凑 JSONL，避免稀疏字段产生大量空 CSV 单元格。（2）
  4. dict/list 类型的 CSV 单元格使用紧凑 JSON 序列化，避免 Python repr。（3）
- **Lossy**（lossless 候选仍不满足目标）：保留开头/结尾行和 error/warning 等高信号行，其余用 `_omitted` 标记。
- **数组分类**：
  1. 对象数组：稳定 schema 走 CSV，稀疏 schema 走 JSONL；仍超预算时保留首尾和 error/warning 行。
  2. 字符串数组：优先紧凑 JSON；仍超预算时保留首尾、错误字符串和最长字符串。
  3. 数字数组：优先紧凑 JSON；仍超预算时保留首尾、最小值、最大值以及最大相邻变化点。
  4. 混合数组：优先紧凑 JSON；仍超预算时保留首尾、每种 JSON 类型的代表项和错误项。
- **替换条件**：候选 token 不超过本轮目标上限，且达到最低收益率；否则 JSON 压缩器返回原文。
- **可恢复性**：`RuleCompressionResult.lossy=True` 会写入 `rule_compression_lossy` metadata。任何发生丢行的 JSON 结果都会强制调用 `offload_messages` 保存完整原文，并在可见内容后附 reload marker，不受普通 `large_message_threshold` 限制。
- **实现边界**：参考 Headroom 的行为目标和测试维度，但采用本项目独立的确定性实现；不复用其 planner、adaptive-K、CCR、strategy/bias 或代码组织。
- **Headroom 参考**：`smart_crusher.py` / `crates/headroom-core/.../smart_crusher/`

#### 2.3.2 Search results → SearchCompressor (梳理)

- **适用**：grep / ripgrep / ag 输出（`file:line:content`）。
- **解析**：支持 `file:line:content` 和 `file-line-content`；按“分隔符 + 数字行号 + 分隔符”定位边界，兼容 Windows 盘符和文件名中的 `-`。
- **分组**：按文件路径分组，保留原始出现顺序。
- **打分**：
  1. error / warning / exception / failed 等高信号行优先。
  2. 从完整 `ModelContext` 最近一条 `UserMessage` 提取长度大于 2 的关键词；内容命中关键词时加分。
  3. 同分时按原始行号和出现顺序保持确定性。
- **自适应总预算**：
  1. 以最多 5 条作为基础保留量。
  2. 计算标准化内容的去重比例；重复度高时接近基础量，内容越多样越接近全局硬上限。
  3. query 命中和 error/warning 等高信号项会提高最低保留量，避免重要结果被重复度规则压掉。
  4. 最终仍受每文件最多 5 条、全局最多 30 条、最多 15 个文件三个硬上限约束。
- **选择**：每文件固定保留首条、末条，再按得分补足到自适应预算。
- **文件优先级**：按文件内匹配总分排序，高相关文件优先进入全局预算。
- **输出**：恢复原始行号顺序，保持 grep 格式；省略行附 `[... and N more matches in file.py]`。
- **未解析行**：标题、汇总或命令说明等无法解析为 grep match 的非空行原样保留，不静默丢弃。
- **收益门槛**：压缩后至少节省 `rule_compression_min_savings_ratio`（默认 30%）token，否则保留原文。
- **可恢复性**：只要删除了文件或匹配行，就标记 `lossy=True` 并强制 offload 完整原文。
- **统计信息**：通过 `rule_compression_details` metadata 记录原始/保留/省略匹配数、涉及/保留文件数、未解析行数和本次自适应预算。
- **实现边界**：不引入 Headroom adaptive-K、bias、CCR 或 signals trait，使用本项目独立的多样性预算算法和确定性评分。
- **Headroom 参考**：`search_compressor.rs`

#### 2.3.3 Build/log output → LogCompressor （梳理）

- **适用**：pytest、npm、cargo、make 等构建/测试/运行日志。
- **格式识别**：检测 pytest / npm / cargo / jest / make，无法识别时按 generic 日志处理。
- **分类**：逐行识别 ERROR / FAIL / WARN / INFO / DEBUG / TRACE、测试摘要和 Python / JS / Java / Rust / Go stack trace。
- **选择**：错误/失败保留首尾和高分项，warning 做保守去重，stack trace 与摘要单独保留，并在选中行前后保留可配置上下文。
- **堆栈保护**：Python chained exception 中的空行和 `During handling...` 不会提前结束 trace。
- **预算**：默认最多 10 条 error/fail、5 条 warning、3 组 stack trace、每组 20 行，总计最多 100 行。
- **短日志**：低于 `rule_log_min_lines=50` 原样返回。
- **输出**：选中行 + `[N lines omitted: X ERROR, Y FAIL, Z WARN, ...]`。
- **收益与恢复**：使用 Context TokenCounter 和统一最低收益率；发生省略时标记 `lossy=True` 并保存可恢复原文。
- **元数据**：记录格式、总/保留/省略行数、各级别数量、trace 数量、warning 去重数量和保留行号。
- **Headroom 参考**：`log_compressor.rs`

#### 2.3.4 Git diff → DiffCompressor （梳理）

- **适用**：`git diff` / unified diff / combined diff。
- **解析**：按 preamble / file / hunk 结构解析，识别 `diff --git`、`diff --cc`、`diff --combined` 与 `@@` / `@@@`。
- **思路**：**保留选中 hunk 内所有 `+`/`-` 变更行**，仅削减距离变更过远的 context 行；默认每侧保留 2 行。
- **裁剪**：文件数超限时按变更量、错误信号和 query 相关性选择；单文件 hunk 超限时保留首尾 hunk，并按相同信号选择中间 hunk，最终按原始顺序输出。
- **特殊信息**：保留 diff 前置说明、rename / similarity / mode / binary 等文件元数据，以及 `\ No newline at end of file`。
- **短 diff**：默认低于 50 行原样返回，可通过 `rule_diff_min_lines` 调整。
- **收益门槛**：通过 Context 的 TokenCounter 计算候选收益，低于 `rule_compression_min_savings_ratio` 时不替换。
- **配置**：`rule_diff_max_context_lines=2`、`rule_diff_max_hunks_per_file=10`、`rule_diff_max_files=20`。
- **元数据**：记录文件、hunk、context 的原始 / 保留 / 省略数量；发生任何省略时标记 `lossy=True`，由 MessageOffloader 保存可恢复原文。
- **Headroom 参考**：`diff_compressor.rs`

#### 2.3.5 HTML → HTMLExtractor

- **适用**：WebFetch / 爬虫类工具返回的完整 HTML。
- **解析器**：优先使用 `trafilatura` 抽取正文，并通过 `HTMLExtractorConfig` 配置输出格式、链接/图片/表格/评论/格式保留、`favor_precision` 和 `favor_recall`。
- **噪声清理**：由 `trafilatura.extract()` 负责解析 HTML、识别正文区域并去除 script / style / nav / footer / aside / 广告等 boilerplate。
- **正文选择**：由 `trafilatura` 执行正文识别；规则模块负责 HTML 检测、配置封装、调用抽取器、结果统计和压缩收益判断。
- **输出**：默认输出 Markdown，保留链接和表格，默认排除图片和评论；**不是 ML 摘要**。
- **失败保护**：`trafilatura.extract()` 返回 `None` 时按空字符串统计；正文为空、低于 `rule_html_min_content_chars=100`，或 TokenCounter 计算收益低于 `rule_compression_min_savings_ratio` 时原样返回。缺少 `trafilatura` 时保留 BeautifulSoup 规则 fallback。
- **元数据**：通过 `trafilatura.extract_metadata()` 提取 title / author / date / sitename / description / categories / tags，并记录原始长度、抽取后长度、字符压缩比例和降幅；成功替换后标记 `lossy=True`，由 MessageOffloader 保存可恢复原始 HTML。
- **Headroom 参考**：`html_extractor.py`

#### 2.3.6 Plain text → 通用规则（兜底）

- **适用**：无法归入以上类型的长文本。
- **首版策略**（不用 Kompress/LLM）：
  1. 空白折叠、重复行去重
  2. 仍超长 → 头 2000 + 尾 2000 token（与 §2.4 截断策略一致）
- **说明**：Headroom 对 plain text 默认走 Kompress（ML 选词），本模块**明确不做**，仅规则兜底。

#### 2.3.7 Source code → Tree-sitter 结构骨架压缩 （梳理、重点看那些代码保留原样）

- **依赖**：本地 `tree-sitter-language-pack`，不调用模型或外部服务。
- **语言**：Python、JavaScript、TypeScript、Go、Rust、Java、C、C++。
- **策略**：保留 imports、类型、类、装饰器、函数/方法签名及全部顶层结构；函数体超过 `rule_source_max_body_lines=5` 时，用语言合法的 omission body 替换。
- **Python 输出**：函数体替换为注释和 `pass`；C-family 输出保留 `{}` 并写入 `// [function body omitted; ...]`。
- **query 保护**：函数文本命中最近用户 query term 时保留完整函数体，不参与骨架压缩。
- **语法保护**：原始 AST 包含 ERROR/MISSING 节点时原样返回；生成候选后重新解析，语法无效时也原样返回。
- **UTF-8**：按 Tree-sitter byte offset 对 UTF-8 bytes 执行替换，中文注释/字符串不会造成位置偏移。
- **阈值**：默认源码低于 `rule_source_min_lines=100` 不压缩；TokenCounter 收益不足时不替换。
- **恢复**：成功压缩标记 `lossy=True`，原始源码由 MessageOffloader 保存并可 reload。
- **元数据**：记录语言、函数体总数/压缩数/query 保护数和候选语法状态。

### 2.4 仍超长：截断与卸载

`add_messages` 阶段规则压缩后仍超过当前字符预算：

```
1. RULE     : 先执行对应类型的温和规则压缩，不做强制头尾截断
2. 若规则压缩已进入预算：保留在上下文，不立即卸载
3. 若规则压缩后仍超过预算：调用 `offload_messages` 保存原文，Context 中保留压缩结果或固定长度预览与 reload marker
```
与现有 `MessageOffloader` 的 handle / reload 机制兼容；**不在此阶段调用 LLM 生成摘要**。

TTL sweep 阶段采用更简单的规则：

```
1. 遍历完整 ModelContext，而不是仅遍历本次裁剪后的 ContextWindow。
2. 跳过 protected tool、已经是 OffloadMixin 的 placeholder，以及带 `rule_compressed_at` 的已规则压缩消息；不跳过最近消息。
3. 其余 ToolMessage 强制进入规则压缩，不先判断单条字符阈值。
4. 内容没有变化：保持原样。
5. 未压缩消息的结果不超过 TTL 单条目标预算：写回并继续保留；仍超过预算：当次直接 offload。
6. 修改写回 ModelContext，并同步替换本次 ContextWindow 中对应消息。
```


### 2.5 TTL 与触发控制

| 项 | 说明 |
| --- | --- |
| `ttl_seconds` | 默认 300s，配置在 `MessageOffloaderConfig`；0 表示关闭 TTL |
| `last_context_window_access_at` | 保存在 `SessionModelContext`，表示最近一次构建 ContextWindow 的时间，并随 Context 状态保存/恢复 |
| 上下文字符容量 | 固定为 `context_window_tokens × 3` |
| TTL 占用门槛 | 固定为完整 ModelContext 字符占用达到字符容量的 50% |
| 触发规则 | `now - last_context_window_access_at >= ttl_seconds` 且完整 ModelContext 字符占用达到 50% |
| 首次请求 | 只初始化 `last_context_window_access_at`，不立即 sweep |
| 每次取窗口 | 无论是否触发 sweep，都更新 `last_context_window_access_at` |
| TTL 语义 | 表示根据闲置时间推定模型侧 KV/Prompt Cache 已失效，不代表能够直接观测供应商缓存状态 |
| TTL 过期但不足 50% | 不处理，避免小上下文做无意义遍历和压缩 |
| TTL 过期且达到 50% | 遍历完整 ModelContext，压缩符合条件的旧 ToolMessage 并写回 |

`MessageOffloaderConfig` 只保留 `ttl_seconds` 和 `protected_tool_names`。消息数阈值、
累计 token 阈值、单条大消息阈值、最近消息保留数、角色列表和截断尺寸均已删除。

当前实现把 TTL 状态保存在具体 `SessionModelContext` 内部：

```python
_last_context_window_access_at: float | None
```

该字段通过 `SessionModelContext.save_state()` / `load_state()` 持久化。TTL 不增加 `ttl_processed` 等消息级过期标记；每次满足 context 级触发条件时重新遍历，但确定性规则应保持幂等，已是 `OffloadMixin` 的消息会被跳过。


## 3. 组件 ② — ForkedCompression（统一压缩执行器）

### 3.1 目标

把压缩调用统一到 `ForkedCompressionExecutor`，让 compressor 可以复用同一种「主 agent 前缀 + 压缩提示词」调用方式。后续sessionMemory和agent压缩都可以在这里扩展

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
```python
class ForkedCompressionExecutor:
    """Shared model invocation wrapper for compaction calls using main-agent prefix context."""

    def __init__(self, model: Any) -> None:
        self._model = model
        self._last_response: Any = None

    @property
    def last_response(self) -> Any:
        return self._last_response

    async def invoke(self, request: ForkedCompressionRequest) -> ForkedCompressionResult:
        messages = self.build_messages(request)
        kwargs: dict[str, Any] = {"messages": messages, "tools": request.tools}
        if request.output_parser is not None:
            kwargs["output_parser"] = request.output_parser
        response = await self._model.invoke(**kwargs)
        self._last_response = response
        return ForkedCompressionResult(
            response=response,
            usage=getattr(response, "usage_metadata", None) or getattr(response, "usage", None),
        )

    @staticmethod
    def build_messages(request: ForkedCompressionRequest) -> list[BaseMessage]:
        context_messages = list(request.context_messages)
        if request.exclude_recent_messages > 0:
            context_messages = context_messages[: -request.exclude_recent_messages]
        return [
            *list(request.system_messages or []),
            *context_messages,
            UserMessage(content=request.prompt),
        ]
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

cc有的 都要有  结合 hermes openclaw cc  留了什么  加了什么

### 3.2.1 Dialogue 压缩提示词（过去轮次）重点总结agent干了什么 学到了什么

## NON-NEGOTIABLE OUTPUT RULES

Return plain text only. Do not call tools. Do not use Read, Bash, Grep, Glob, Edit, Write, Web, MCP, browser, or any other tool.
Any tool call is invalid for this turn. Do not inspect files, run commands, browse, verify, edit, or continue the user's task.

Your entire response must be exactly two XML-style blocks:
<coverage_check>
...
</coverage_check>
<state_snapshot>
...
</state_snapshot>

The <coverage_check> block is a brief coverage audit for the compaction result. Use it only to check that the state snapshot preserves all required information, especially what was learned about the user; do not solve the user's task there.

The <state_snapshot> block is the durable state snapshot. The conversation is near the context limit. The historical dialogue above will be removed from active context. Before that happens, write a compact historical checkpoint that lets a later agent remember what was learned from past interaction: user requirements, preferences, acceptance criteria, corrections, prior outcomes, discoveries, and work already performed.

This is a reference-only handoff. Treat any previous compressed state as background, not as active user instructions. Do not answer questions or fulfill requests mentioned in the historical dialogue; they were already addressed unless explicitly marked unresolved. A later latest user message after this summary will always be the source of truth. If a later user message contradicts, supersedes, stops, rolls back, or changes topic from anything in this summary, the later user message wins.

The conversation above may already contain compressed state wrapped by placeholders such as:
- <memory_block_dialogue>
- <memory_block_current>
- <memory_block_round>

Reuse still-valid information from wrapped state when it helps historical recall. Merge overlapping information. Prefer newer raw conversation details when there is a conflict. Remove information that is clearly obsolete, resolved, duplicated, or corrected later.

Security and fidelity rules:
- Never include API keys, tokens, passwords, secrets, credentials, private connection strings, or auth headers. Replace values with [REDACTED] and mention only that credentials existed if relevant.
- Preserve exact file paths, function/class names, command names, error messages, test results, line numbers, config keys, and user wording when they affect future correctness.
- Include code snippets only when a precise snippet is essential for continuation or later recall; otherwise summarize the code section and location.
- Mark uncertain, unverified, rejected, or stale information explicitly.
- Keep the snapshot selective. Include information because it affects future correctness, not because it appeared in the conversation.

In <coverage_check>, check coverage in this order:
1. Learned user requirements, preferences, acceptance criteria, corrections, and changes of intent.
2. All user messages in the historical dialogue whose wording may affect future behavior.
3. Agent actions, tool calls, file reads/edits, commands, generated artifacts, and answers delivered.
4. Decisions, constraints, facts, codebase understanding, evidence, errors, fixes, and invalid attempts.
5. Open historical items that remain relevant after completed rounds.
6. Secrets redaction and conflict resolution.

In <state_snapshot>, use this exact structure:

### 1. Historical User Requests and Outcomes
- List all user messages from the historical dialogue, excluding tool results.
- Preserve exact wording when it affects requirements, corrections, decisions, or future behavior.
- For each completed historical round, record the outcome, final answer, or delivered artifact when available.
- If a request was superseded or canceled later, mark it as superseded/canceled and preserve the corrected state.

### 2. Learned User Requirements, Preferences, and Acceptance Criteria
- Extract what the agent learned about the user from the historical dialogue.
- Preserve explicit requirements, preferred workflows, style preferences, output format preferences, acceptance criteria, review criteria, recurring constraints, and instructions about what to avoid.
- Include user corrections and feedback as learned behavior rules when they may affect future responses.
- Keep exact wording when the wording itself constrains behavior.
- Prefer newer/corrected preferences when they conflict with older ones.

### 3. Historical Work Performed
- Record what the agent did in these past rounds.
- Include investigations, file reads, edits, commands, tests, tool calls, generated artifacts, and answers delivered.
- Keep action history concise; preserve enough detail to show what was already done and avoid duplicate work.

### 4. Key Technical Concepts and Codebase Understanding
- List important technologies, frameworks, APIs, architectural concepts, module boundaries, and repo patterns discovered.
- Include public API/export constraints, config conventions, and test/build entry points when they may guide future work.
- Omit generic knowledge that can be re-derived easily.

### 5. Files, Code Areas, and Artifacts
- Record relevant files examined, modified, or created and why each matters.
- Include functions, classes, methods, config keys, docs, examples, generated assets, or output files that may matter later.
- Note whether each item was read-only, edited, created, deleted, generated, or only discussed.

### 6. Decisions, Constraints, Corrections, and Findings
- Record important decisions and the rationale behind them.
- Preserve user preferences, constraints, acceptance criteria, corrections, and rejected approaches.
- If earlier information was corrected later, keep only the corrected state unless the correction itself matters.

### 7. Evidence, Errors, Fixes, and Invalid Attempts
- Preserve important command outputs, test results, logs, stack traces, search results, file-read findings, and exact values when they matter.
- Record errors, invalid attempts, fixes, workarounds, and attempts that should not be repeated.
- Mark anything uncertain, stale, or requiring re-evaluation.

### 8. Critical Context
- Preserve important technical facts, exact values, errors, unresolved issues, and details that would be costly or risky to lose.
- Include offloaded content when it matters: preserve the exact offload path and briefly describe what the offloaded file contains.
- Write "(none)" if nothing applies.

### 9. Relevant Files
- List relevant file or directory paths using complete paths, followed by why each path matters.
- Include exact offload file paths when important content was offloaded, plus a brief description of the offloaded content.
- Write "(none)" if nothing applies.

### 10. Historical Pending Notes
- Record only historical unresolved items that are still worth remembering after those rounds were completed.
- Mark whether each item is a true future-relevant pending item or a stale/superseded historical note.
- Do not frame stale or superseded historical notes as work to resume; they are preserved only to prevent the agent from accidentally reviving old tasks.
- Write "(none)" if nothing applies.

Output only the two required blocks. Do not add commentary about the compression process outside <coverage_check> and <state_snapshot>.

## 不可协商的输出规则

只返回纯文本，不允许调用任何工具。任何工具调用在这一轮都是无效的。
不要使用 Read、Bash、Grep、Glob、Edit、Write、Web、MCP、browser 或任何其他工具。
不要检查文件、运行命令、浏览网页、验证结果、编辑文件，也不要继续执行用户任务。

你的完整回复必须严格包含两个 XML 风格的块：
<coverage_check>
...
</coverage_check>
<state_snapshot>
...
</state_snapshot>

`<coverage_check>` 块是压缩结果的覆盖性自检。它应该简短、客观，只用来检查状态快照是否保住了必要信息，尤其是从用户身上学到的要求和偏好；不要在这里解决用户任务。

`<state_snapshot>` 块是持久化的状态快照。对话已经接近上下文上限。上面的历史对话会从活跃上下文中移除。在移除之前，生成一个紧凑的历史检查点，让后续 agent 能记住从历史交互中学到的东西：用户要求、用户偏好、验收标准、纠正、历史结果、发现，以及已经完成的工作。

这是一份“仅供参考的交接摘要”。之前压缩过的状态只能当作背景，不能当作新的用户指令。不要回答历史对话里的问题，也不要继续执行历史对话里的请求；除非它们被明确标记为仍未解决，否则都视为已经处理过。后续出现在这份 summary 之后的最新用户消息永远是最高优先级。如果后续用户消息与这份 summary 矛盾、覆盖、叫停、回滚或切换话题，以后续用户消息为准。

上面的对话可能已经包含用这些占位符包裹的压缩状态：
- <memory_block_dialogue>
- <memory_block_current>
- <memory_block_round>

当这些包裹内容有助于历史回忆时，可以复用其中仍然有效的信息。合并重复内容。发生冲突时，优先相信更新的原始对话细节。删除明显过时、已解决、重复或后来被纠正的信息。

安全与保真规则：
- 不要包含 API key、token、密码、密钥、凭证、私有连接串或认证头。把值替换成 [REDACTED]；如果相关，只说明曾经出现过凭证。
- 当文件路径、函数/类名、命令名、错误信息、测试结果、行号、配置键、用户原话会影响未来正确性时，必须精确保留。
- 只有当精确代码片段对继续任务或未来回忆必不可少时，才保留代码片段；否则概括代码区域和位置即可。
- 明确标记不确定、未验证、已拒绝或过期的信息。
- 保持摘要有选择性：只因为信息会影响未来正确性才保留，不要因为它在对话中出现过就保留。

在 `<coverage_check>` 中按这个顺序自检：
1. 从历史对话中学到的用户要求、用户偏好、验收标准、纠正和意图变化。
2. 历史对话里原话可能影响未来行为的用户消息。
3. agent 的动作、工具调用、文件读取/编辑、命令、生成物和已给出的回答。
4. 决策、约束、事实、代码库理解、证据、错误、修复和无效尝试。
5. 完成历史轮次后仍然相关的开放项。
6. 敏感信息脱敏与冲突处理。

在 `<state_snapshot>` 中使用这个固定结构：

### 1. 历史用户请求与结果
- 列出历史对话里的所有用户消息，不包括工具结果。
- 当原话会影响需求、纠正、决策或未来行为时，保留用户原话。
- 对每个已完成的历史轮次，记录结果、最终回答或交付物。
- 如果某个请求后来被覆盖或取消，标记为已覆盖/已取消，并保留纠正后的状态。

### 2. 学到的用户要求、偏好与验收标准
- 提取 agent 从历史对话中学到的用户信息。
- 保留明确要求、偏好工作流、输出风格偏好、格式偏好、验收标准、评审标准、反复出现的约束，以及用户要求避免的做法。
- 如果用户纠正或反馈会影响未来行为，把它记录成已学到的行为规则。
- 当原话本身约束行为时，保留原话。
- 新旧偏好冲突时，优先采用更新/纠正后的偏好。

### 3. 历史已完成工作
- 记录 agent 在过去轮次中做了什么。
- 包括调查、文件读取、编辑、命令、测试、工具调用、生成物和已交付回答。
- 动作历史要简洁，但要足够说明哪些工作已经做过，避免后续重复。

### 4. 关键技术概念与代码库理解
- 列出发现的重要技术、框架、API、架构概念、模块边界和仓库模式。
- 如果公共 API/export 约束、配置约定、测试/构建入口会指导未来工作，也要保留。
- 省略容易重新推导的通用知识。

### 5. 文件、代码区域与产物
- 记录相关文件被查看、修改或创建的情况，并说明为什么重要。
- 包括未来可能重要的函数、类、方法、配置键、文档、示例、生成资源或输出文件。
- 说明每一项是只读、已编辑、已创建、已删除、已生成，还是仅被讨论过。

### 6. 决策、约束、纠正与发现
- 记录重要决策及其理由。
- 保留用户偏好、约束、验收标准、纠正和被拒绝的方案。
- 如果早期信息后来被纠正，只保留纠正后的状态；除非“发生过纠正”这件事本身很重要。

### 7. 证据、错误、修复与无效尝试
- 当命令输出、测试结果、日志、堆栈、搜索结果、文件读取发现和精确值很重要时，保留它们。
- 记录错误、无效尝试、修复、绕过方案，以及不应重复的尝试。
- 标记不确定、过期或需要重新评估的内容。

### 8. 关键上下文
- 记录重要技术事实、精确值、错误、未解问题，以及一旦丢失会带来成本或风险的细节。
- 如果遇到重要内容被卸载，也要保留准确的卸载路径，并简要说明卸载文件里大概是什么内容。
- 如果没有，写“（无）”。

### 9. 相关文件
- 列出相关文件或目录的完整路径，并说明其重要性。
- 如果重要内容被卸载，列出准确的卸载文件路径，并简要说明卸载内容。
- 如果没有，写“（无）”。

### 10. 仍需记住的历史未决事项
- 只记录历史轮次结束后仍值得记住的未解决事项。
- 标明每一项属于“未来仍可能相关的待办”，还是“已过期/已覆盖的历史记录”。
- 不要把已过期或已覆盖的历史记录写成需要继续执行的任务；保留它们只是为了避免 agent 误恢复旧任务。
- 如果没有，写“（无）”。

只输出这两个必需块。不要在 `<coverage_check>` 和 `<state_snapshot>` 外添加关于压缩过程的说明。

### 3.2.2 Current 压缩提示词（当前轮次）重点维持agent的可持续性 当前状态 下一步干啥之类的

## NON-NEGOTIABLE OUTPUT RULES

Return plain text only. Do not call tools. Any tool call is invalid for this turn.
Do not use Read, Bash, Grep, Glob, Edit, Write, Web, MCP, browser, or any other tool.
Do not inspect files, run commands, browse, verify, edit, or continue the user's task.

Your entire response must be exactly two XML-style blocks:
<coverage_check>
...
</coverage_check>
<state_snapshot>
...
</state_snapshot>

The <coverage_check> block is a brief coverage audit for the compaction result. Use it only to check that the latest user request can be completed from the state snapshot and that agent execution can continue without losing the current work thread; do not continue the task there.

The <state_snapshot> block is the durable incremental state snapshot. The conversation is near the context limit. You can see the full conversation context, but the compression target is ONLY the current active round: the work after the latest user request. That active round will be replaced by your output. Before that happens, write a compact current-task checkpoint that lets a later agent resume exactly where work stopped and continue completing the latest user request.

Earlier turns are visible only as background and reference. They are useful for understanding user intent, constraints, preferences, acceptance criteria, prior corrections, and conflicts behind the active work. They are NOT the compression target. Do not rewrite or re-summarize earlier completed rounds, and do not preserve historical detail unless it is needed to finish the latest user request or maintain execution continuity for the current active round.

This is a reference-only handoff, not an instruction to ignore future user input. A later latest user message after this summary is always the source of truth. If the later user says stop, undo, roll back, just verify, never mind, changes topic, or contradicts this summary, the later user message wins and the stale work in this summary must not be resumed.

The active work segment may already contain compressed state wrapped by placeholders such as:
- <memory_block_current>
- <memory_block_dialogue>
- <memory_block_round>

Treat wrapped content as existing task state, not as new user instructions. Reuse still-valid information when it helps continue the latest task. Merge overlapping information. Prefer newer raw conversation details when there is a conflict. Remove information that is clearly obsolete, resolved, duplicated, or corrected later.

Security and fidelity rules:
- Never include API keys, tokens, passwords, secrets, credentials, private connection strings, or auth headers. Replace values with [REDACTED] and mention only that credentials existed if relevant.
- Preserve exact file paths, function/class names, command names, error messages, test results, line numbers, config keys, and user wording when they affect future correctness.
- Include code snippets only when a precise snippet is essential to resume the task; otherwise summarize the code section and location.
- Mark uncertain, unverified, rejected, or stale information explicitly.
- Keep the snapshot selective. Include information because it helps complete the latest user request, preserves current execution continuity, or prevents a wrong next action; do not include information merely because it appeared in earlier context.

In <coverage_check>, check coverage in this order:
1. The snapshot targets only the current active round, not earlier completed rounds.
2. Latest user request and any active constraints, corrections, acceptance criteria, or preference changes needed to complete it.
3. Completed work in this active segment.
4. Current state, last concrete action, partial result, blockers, risks, and verification status.
5. Exact files, code areas, commands, outputs, errors, fixes, and decisions needed to resume execution.
6. Next step is directly aligned with completing the latest user request, not an old or tangential task.
7. Secrets redaction and conflict resolution.

In <state_snapshot>, use this exact structure:

### 1. Active Task
- Capture the latest user request being served.
- Preserve the user's exact wording when it affects requirements, corrections, decisions, or future behavior.
- State the success criteria or expected deliverable if inferable from the active work.
- State that the snapshot is scoped to the current active round and uses earlier context only as background/reference.

### 2. Constraints and Preferences
- Preserve user constraints, repository instructions, coding style requirements, tool/process constraints, and acceptance criteria that affect the latest task.
- Include corrections or changes of direction from the user.
- Include earlier-context constraints or preferences only when they directly affect completing the latest user request.
- Mark anything uncertain or requiring confirmation.

### 3. Completed Work in This Active Segment
- Record what has been completed since the latest user request.
- Include answers delivered, files inspected, edits made, decisions reached, commands run, tests completed, tool calls, and artifacts produced.
- Preserve enough detail so the next agent does not repeat completed work unnecessarily.

### 4. Current Work and Active State
- Describe precisely what was being worked on immediately before compaction.
- Include the active file, function, class, subtask, branch, plan item, process, or generated artifact if any.
- Include the latest known state and prefer newer/corrected information over earlier state.

### 5. Immediate Resume Point
- Record exactly where execution stopped.
- Include the last concrete action, latest partial result, active file or subtask, and current working direction.
- If the last action failed or timed out, include the exact failure and what had been learned before it failed.

### 6. Pending Tasks and Next Useful Step
- List pending tasks explicitly asked for by the user and not yet fulfilled.
- List the next step that directly continues the latest task. Include it only if it is directly supported by the latest user request and current work.
- Do not invent unrelated follow-up work or revive old completed tasks.

### 7. Key Facts, Decisions, Evidence, and Fixes
- Preserve facts, findings, decisions, assumptions, constraints, user corrections, rejected approaches, and items requiring re-evaluation.
- Preserve important tool results, command outputs, test results, logs, errors, stack traces, file reads, search results, and exact values when they matter.
- Record fixes already applied, invalid attempts, and attempts that should not be repeated.
- Prefer facts that are necessary for finishing the latest user request or preserving current execution continuity.

### 8. Files, Code Areas, Artifacts, and Codebase Understanding
- Record files examined, modified, created, deleted, generated, or only discussed.
- Include relevant functions, classes, APIs, config keys, docs, generated artifacts, codebase patterns, module responsibilities, public APIs, and why they matter for the latest task.

### 9. Blockers, Risks, and Verification
- Record blockers, open questions, missing checks, incomplete edits, pending decisions, and known risks.
- State what has been verified and what has not been verified.
- Include exact commands/results for completed verification when relevant.

### 10. Critical Context
- Preserve important technical facts, exact values, errors, unresolved issues, and details needed to continue the latest user request correctly.
- Include offloaded content when it matters: preserve the exact offload path and briefly describe what the offloaded file contains.
- Write "(none)" if nothing applies.

### 11. Relevant Files
- List relevant file or directory paths using complete paths, followed by why each path matters for the latest user request.
- Include exact offload file paths when important content was offloaded, plus a brief description of the offloaded content.
- Write "(none)" if nothing applies.

Output only the two required blocks. Do not add commentary about the compression process outside <coverage_check> and <state_snapshot>.

#### 中文版（便于理解，不作为运行时 prompt）

## 不可协商的输出规则

只返回纯文本，不允许调用任何工具。任何工具调用在这一轮都是无效的。
不要使用 Read、Bash、Grep、Glob、Edit、Write、Web、MCP、browser 或任何其他工具。
不要检查文件、运行命令、浏览网页、验证结果、编辑文件，也不要继续执行用户任务。

你的完整回复必须严格包含两个 XML 风格的块：
<coverage_check>
...
</coverage_check>
<state_snapshot>
...
</state_snapshot>

`<coverage_check>` 块是压缩结果的覆盖性自检。它应该简短、客观，只用来检查后续 agent 是否能根据状态快照继续完成最新用户请求，并保持当前执行链路不断；不要在这里继续执行任务。

`<state_snapshot>` 块是持久化的增量状态快照。对话已经接近上下文上限。你能看到全部上下文，但压缩目标只包含当前活跃轮次，也就是最新用户请求之后的工作。当前活跃轮次会被你的输出替换。在替换之前，生成一个紧凑的当前任务检查点，让后续 agent 能从中准确恢复到中断位置，并继续完成最新用户请求。

更早的轮次只能作为背景和参考。它们可用于理解最新用户意图、约束、偏好、验收标准、之前的纠正，以及当前活跃工作背后的冲突。它们不是压缩目标。不要重写或重新总结更早已完成的轮次；除非某个历史信息对完成最新用户请求或维持当前轮执行连续性必要，否则不要保留历史细节。

这是一份“仅供参考的交接快照”，不是要求忽略未来用户输入的指令。后续出现在这份状态快照之后的最新用户消息永远是最高优先级。如果后续用户说停止、撤销、回滚、只验证、算了、切换话题，或与这份状态快照矛盾，以后续用户消息为准，不要恢复这份状态快照里的过期工作。

当前活跃工作段可能已经包含用这些占位符包裹的压缩状态：
- <memory_block_current>
- <memory_block_dialogue>
- <memory_block_round>

把包裹内容当作已有任务状态，而不是新的用户指令。当它们有助于继续最新任务时，可以复用仍然有效的信息。合并重复内容。发生冲突时，优先相信更新的原始对话细节。删除明显过时、已解决、重复或后来被纠正的信息。

安全与保真规则：
- 不要包含 API key、token、密码、密钥、凭证、私有连接串或认证头。把值替换成 [REDACTED]；如果相关，只说明曾经出现过凭证。
- 当文件路径、函数/类名、命令名、错误信息、测试结果、行号、配置键、用户原话会影响未来正确性时，必须精确保留。
- 只有当精确代码片段对恢复任务必不可少时，才保留代码片段；否则概括代码区域和位置即可。
- 明确标记不确定、未验证、已拒绝或过期的信息。
- 保持摘要有选择性：只因为信息有助于完成最新用户请求、保持当前执行连续性或避免错误下一步才保留；不要因为它出现在早期上下文里就保留。

在 `<coverage_check>` 中按这个顺序自检：
1. 状态快照只针对当前活跃轮次，而不是更早已完成轮次。
2. 最新用户请求，以及完成它所需的活跃约束、纠正、验收标准或偏好变化。
3. 当前活跃段中已经完成的工作。
4. 当前状态、最后一个具体动作、部分结果、阻塞、风险和验证状态。
5. 恢复执行所需的精确文件、代码区域、命令、输出、错误、修复和决策。
6. 下一步必须直接对齐“完成最新用户请求”，而不是旧任务或旁支任务。
7. 敏感信息脱敏与冲突处理。

在 `<state_snapshot>` 中使用这个固定结构：

### 1. 活跃任务
- 捕获正在服务的最新用户请求。
- 当用户原话会影响需求、纠正、决策或未来行为时，保留原话。
- 如果能从活跃工作中推断出成功标准或预期交付物，也要写明。
- 写明这份快照只覆盖当前活跃轮次，早期上下文只作为背景/参考。

### 2. 约束与偏好
- 保留会影响最新任务的用户约束、仓库指令、代码风格要求、工具/流程限制和验收标准。
- 包含用户纠正或方向变化。
- 只有当早期上下文中的约束或偏好直接影响完成最新用户请求时，才保留它。
- 标记任何不确定或需要确认的内容。

### 3. 当前活跃段已完成工作
- 记录最新用户请求之后已经完成的工作。
- 包括已交付回答、已检查文件、已做编辑、已达成决策、已运行命令、已完成测试、工具调用和产物。
- 保留足够细节，让后续 agent 不必重复已完成工作。

### 4. 当前工作与活跃状态
- 精确描述压缩前正在做什么。
- 包括活跃文件、函数、类、子任务、分支、计划项、进程或生成物。
- 写明最新已知状态，并优先采用更新/纠正后的信息。

### 5. 立即恢复点
- 记录执行精确停在哪里。
- 包括最后一个具体动作、最新部分结果、活跃文件或子任务，以及当前工作方向。
- 如果最后一个动作失败或超时，写明精确失败信息和失败前已经学到的内容。

### 6. 待办任务与下一步
- 列出用户明确要求但尚未完成的待办任务。
- 列出直接延续最新任务的下一步。只有当它被最新用户请求和当前工作直接支持时才写。
- 不要创造无关后续工作，也不要恢复旧的已完成任务。

### 7. 关键事实、决策、证据与修复
- 保留事实、发现、决策、假设、约束、用户纠正、被拒绝方案和需要重新评估的事项。
- 当工具结果、命令输出、测试结果、日志、错误、堆栈、文件读取、搜索结果和精确值很重要时，保留它们。
- 记录已经应用的修复、无效尝试和不应重复的尝试。
- 优先保留完成最新用户请求或维持当前执行连续性所必需的事实。

### 8. 文件、代码区域、产物与代码库理解
- 记录已检查、修改、创建、删除、生成或仅讨论过的文件。
- 包括与最新任务相关的函数、类、API、配置键、文档、生成物、代码库模式、模块职责、公共 API，以及为什么重要。

### 9. 阻塞、风险与验证
- 记录阻塞、开放问题、缺失检查、不完整编辑、待决决策和已知风险。
- 说明哪些已经验证，哪些尚未验证。
- 当已完成验证很重要时，包含精确命令和结果。

### 10. 关键上下文
- 记录重要技术事实、精确值、错误、未解问题，以及继续完成最新用户请求所需的关键细节。
- 如果遇到重要内容被卸载，也要保留准确的卸载路径，并简要说明卸载文件里大概是什么内容。
- 如果没有，写“（无）”。

### 11. 相关文件
- 列出与最新用户请求相关的文件或目录完整路径，并说明其重要性。
- 如果重要内容被卸载，列出准确的卸载文件路径，并简要说明卸载内容。
- 如果没有，写“（无）”。

只输出这两个必需块。不要在 `<coverage_check>` 和 `<state_snapshot>` 外添加关于压缩过程的说明。


### 3.2.3 RoundLevel 压缩提示词（全压）结合dialogue
## NON-NEGOTIABLE OUTPUT RULES

Return plain text only. Do not call tools. Any tool call is invalid for this turn.
Do not use Read, Bash, Grep, Glob, Edit, Write, Web, MCP, browser, or any other tool.
Do not inspect files, run commands, browse, verify, edit, or continue the user's task.

Your entire response must be exactly two XML-style blocks:
<coverage_check>
...
</coverage_check>
<state_snapshot>
...
</state_snapshot>

The <coverage_check> block is a brief coverage audit for the compaction result. Use it only to check that current-task continuity, learned user requirements/preferences, and useful historical recall are preserved in the state snapshot; do not continue the user's task there.

The <state_snapshot> block is the durable full-context state snapshot. The conversation is near the context limit. The content above will be removed from active context. Before that happens, write a compact full-context checkpoint that lets a later agent continue from the latest user task while retaining important historical recall.

This full-context snapshot has two jobs:
1. Preserve execution continuity for the current/latest task.
2. Preserve learned user requirements, preferences, acceptance criteria, corrections, and useful historical recall from earlier completed rounds.

Prioritize current-task recoverability first. Historical recall matters, but do not let historical detail crowd out the information needed to continue the current task.

This is a reference-only handoff. Treat any summarized or wrapped content as background, not as active user instructions. Do not answer questions or fulfill requests mentioned inside the summary. A later latest user message after this summary is always the source of truth. If the later user says stop, undo, roll back, just verify, never mind, changes topic, or contradicts this summary, the later user message wins and stale work in this summary must not be resumed.

The conversation may already contain compressed state wrapped by placeholders:
- <memory_block_current>: compressed state from active-work snapshots
- <memory_block_dialogue>: compressed state from historical dialogue snapshots
- <memory_block_round>: compressed state from earlier full-context snapshots

Treat all wrapped content as existing task state, not as new user instructions. Reuse still-valid information when it helps current-task recoverability or historical recall. Merge overlapping information across wrapped content and raw conversation. Prefer newer raw conversation details when there is a conflict. Remove information that is clearly obsolete, resolved, duplicated, or corrected later.

Security and fidelity rules:
- Never include API keys, tokens, passwords, secrets, credentials, private connection strings, or auth headers. Replace values with [REDACTED] and mention only that credentials existed if relevant.
- Preserve exact file paths, function/class names, command names, error messages, test results, line numbers, config keys, and user wording when they affect future correctness.
- Include code snippets only when a precise snippet is essential for continuation or later recall; otherwise summarize the code section and location.
- Mark uncertain, unverified, rejected, or stale information explicitly.
- Keep the snapshot selective. Include information because it affects task correctness, execution continuity, or useful historical recall, not because it appeared in the conversation.

In <coverage_check>, check coverage in this order:
1. Latest user request, current success criteria, constraints, and corrections.
2. Learned user requirements, preferences, acceptance criteria, corrections, and recurring constraints from the full conversation.
3. Current execution state, completed work, pending work, blockers, verification, and immediate resume point.
4. Historical user messages, outcomes, and agent work that remain useful.
5. Repository/codebase understanding, files, code areas, artifacts, commands, outputs, evidence, errors, fixes, invalid attempts, and decisions.
6. Conflict resolution, stale-item removal, and secrets redaction.
7. Next step is directly aligned with the current/latest user request.

In <state_snapshot>, use this exact structure:

### 1. Active Task and Success Criteria
- Capture the current/latest user intent the agent must continue serving.
- Preserve requirements, constraints, preferences, corrections, and acceptance criteria that affect the current task.
- Keep exact wording when it affects future behavior.

### 2. Current Execution State
- Record what has been completed, what is in progress, and what remains unresolved for the current task.
- Include the latest known state and prefer newer/corrected information over earlier state.
- Note active plan items, active mode/state, running processes, background tasks, or session state only if visible in the conversation and relevant.

### 3. Immediate Resume Point and Next Useful Step
- Record exactly where execution stopped.
- Include the last concrete action, latest partial result, active file or subtask, and current working direction.
- List the next step that directly helps complete the current task. Include it only if it is directly supported by the latest user request and current work.
- Do not invent unrelated follow-up work or revive old completed tasks.

### 4. Current Task Facts, Decisions, Evidence, and Fixes
- Preserve facts, constraints, state, codebase knowledge, user corrections, decisions, assumptions, rejected approaches, and items requiring re-evaluation that affect the current task.
- Preserve important tool results, command outputs, test results, logs, errors, stack traces, file reads, search results, and exact values when they matter.
- Record fixes already applied, invalid attempts, and attempts that should not be repeated.

### 5. Current Files, Code Areas, and Artifacts
- Record files examined, modified, created, deleted, generated, or only discussed for the current task.
- Include relevant functions, classes, APIs, config keys, docs, generated artifacts, codebase patterns, module responsibilities, public APIs, and why they matter.

### 6. Repository and Codebase Understanding
- Preserve useful understanding of the repository gathered across the conversation.
- Include architecture, subsystem responsibilities, module boundaries, public APIs, exported surfaces, config conventions, test/build/lint entry points, coding patterns, and ownership boundaries when they may guide future work.
- Record important relationships between files, classes, functions, commands, docs, examples, and generated artifacts.
- Prefer concise, durable understanding over raw file listings.
- Mark assumptions, uncertain mappings, and knowledge that should be re-verified.

### 7. Historical User Requests and Outcomes
- List user messages from earlier completed rounds, excluding tool results.
- Preserve exact wording when it affects requirements, corrections, decisions, or future behavior.
- Record outcomes, final answers, completed results, or delivered artifacts for historical rounds when available.
- Mark superseded, canceled, or resolved historical requests clearly.

### 8. Learned User Requirements, Preferences, and Acceptance Criteria
- Extract durable lessons about the user from the full conversation.
- Preserve explicit requirements, preferred workflows, style preferences, output format preferences, acceptance criteria, review criteria, recurring constraints, and instructions about what to avoid.
- Include corrections and feedback as behavior rules when they may affect future responses.
- Keep exact wording when the wording itself constrains behavior.
- Prefer newer/corrected preferences when they conflict with older ones.
- Separate current-task requirements from broader user preferences when possible.

### 9. Historical Work Performed
- Record what the agent did in earlier completed rounds.
- Include investigations, file reads, edits, commands, tests, tool calls, generated artifacts, and answers delivered.
- Keep action history concise; preserve enough detail to show what was already done.

### 10. Durable Historical Information
- Preserve historical facts, constraints, findings, decisions, evidence, codebase understanding, and user preferences that may still help future continuation or accurate recall.
- Merge overlapping information from earlier compressed state.
- Prefer newer/corrected information when details conflict.

### 11. Cross-Cutting Files, Code Areas, and Artifacts
- Record files examined, modified, created, deleted, generated, or only discussed across the whole conversation when they remain relevant.
- Include relevant functions, classes, APIs, config keys, docs, examples, generated artifacts, module boundaries, public APIs, and why they matter.
- Keep repository structure knowledge only when it may guide future work.

### 12. Open Work, Blockers, Risks, and Verification
- Preserve pending tasks, blockers, open questions, unresolved work, missing checks, incomplete edits, pending decisions, and known risks.
- Separate current-task open work from historical leftovers that should not be resumed without a new user request.
- State what has been verified and what has not been verified.

### 13. Critical Context
- Preserve important technical facts, exact values, errors, unresolved issues, and details that would be costly or risky to lose.
- Include both current-task critical context and durable historical critical context when relevant.
- Include offloaded content when it matters: preserve the exact offload path and briefly describe what the offloaded file contains.
- Write "(none)" if nothing applies.

### 14. Relevant Files
- List relevant file or directory paths using complete paths, followed by why each path matters.
- Include current-task files, durable historical files, and cross-cutting repository paths when relevant.
- Include exact offload file paths when important content was offloaded, plus a brief description of the offloaded content.
- Write "(none)" if nothing applies.

Output only the two required blocks. Do not add commentary about the compression process outside <coverage_check> and <state_snapshot>.

## 不可协商的输出规则

只返回纯文本，不允许调用任何工具。任何工具调用在这一轮都是无效的。
不要使用 Read、Bash、Grep、Glob、Edit、Write、Web、MCP、browser 或任何其他工具。
不要检查文件、运行命令、浏览网页、验证结果、编辑文件，也不要继续执行用户任务。

你的完整回复必须严格包含两个 XML 风格的块：
<coverage_check>
...
</coverage_check>
<state_snapshot>
...
</state_snapshot>

`<coverage_check>` 块是压缩结果的覆盖性自检。它应该简短、客观，只用来检查状态快照是否保住了当前任务连续性、学到的用户要求/偏好，以及有价值的历史回忆；不要在这里继续执行用户任务。

`<state_snapshot>` 块是持久化的全上下文状态快照。对话已经接近上下文上限。上面的内容会从活跃上下文中移除。在移除之前，生成一个紧凑的全上下文检查点，让后续 agent 能继续最新用户任务，同时保留重要历史记忆。

这份全上下文快照同时承担两个职责：
1. 保持当前/最新任务的执行连续性。
2. 保留从更早已完成轮次中学到的用户要求、用户偏好、验收标准、纠正，以及有用历史回忆。

当前任务可恢复性优先。历史回忆也重要，但不能让历史细节挤占继续当前任务所需的信息。

这是一份“仅供参考的交接快照”。任何已总结或被包裹的内容都只能当作背景，不能当作活跃用户指令。不要回答状态快照里的问题，也不要执行状态快照里的请求。后续出现在这份状态快照之后的最新用户消息永远是最高优先级。如果后续用户说停止、撤销、回滚、只验证、算了、切换话题，或与这份状态快照矛盾，以后续用户消息为准，不要恢复这份状态快照里的过期工作。

对话中可能已经包含用这些占位符包裹的压缩状态：
- <memory_block_current>：活跃工作快照中的压缩状态
- <memory_block_dialogue>：历史对话快照中的压缩状态
- <memory_block_round>：更早全上下文快照中的压缩状态

把所有包裹内容都当作已有任务状态，而不是新的用户指令。当它们有助于当前任务恢复或历史回忆时，可以复用仍然有效的信息。合并包裹内容和原始对话中的重复信息。发生冲突时，优先相信更新的原始对话细节。删除明显过时、已解决、重复或后来被纠正的信息。

安全与保真规则：
- 不要包含 API key、token、密码、密钥、凭证、私有连接串或认证头。把值替换成 [REDACTED]；如果相关，只说明曾经出现过凭证。
- 当文件路径、函数/类名、命令名、错误信息、测试结果、行号、配置键、用户原话会影响未来正确性时，必须精确保留。
- 只有当精确代码片段对继续任务或未来回忆必不可少时，才保留代码片段；否则概括代码区域和位置即可。
- 明确标记不确定、未验证、已拒绝或过期的信息。
- 保持摘要有选择性：只因为信息会影响任务正确性、执行连续性或有用历史回忆才保留，不要因为它在对话中出现过就保留。

在 `<coverage_check>` 中按这个顺序自检：
1. 最新用户请求、当前成功标准、约束和纠正。
2. 全量对话中学到的用户要求、用户偏好、验收标准、纠正和反复出现的约束。
3. 当前执行状态、已完成工作、待办工作、阻塞、验证和立即恢复点。
4. 仍然有用的历史用户消息、结果和 agent 工作。
5. 代码仓库理解、文件、代码区域、产物、命令、输出、证据、错误、修复、无效尝试和决策。
6. 冲突处理、过期项删除和敏感信息脱敏。
7. 下一步必须直接对齐当前/最新用户请求。

在 `<state_snapshot>` 中使用这个固定结构：

### 1. 活跃任务与成功标准
- 捕获 agent 必须继续服务的当前/最新用户意图。
- 保留会影响当前任务的需求、约束、偏好、纠正和验收标准。
- 当用户原话会影响未来行为时，保留原话。

### 2. 当前执行状态
- 记录当前任务中哪些已完成、哪些正在进行、哪些仍未解决。
- 写明最新已知状态，并优先采用更新/纠正后的信息。
- 只有当对话中可见且相关时，才记录活跃计划项、活跃模式/状态、运行中进程、后台任务或会话状态。

### 3. 立即恢复点与下一步
- 记录执行精确停在哪里。
- 包括最后一个具体动作、最新部分结果、活跃文件或子任务，以及当前工作方向。
- 列出直接帮助完成当前任务的下一步。只有当它被最新用户请求和当前工作直接支持时才写。
- 不要创造无关后续工作，也不要恢复旧的已完成任务。

### 4. 当前任务事实、决策、证据与修复
- 保留会影响当前任务的事实、约束、状态、代码库知识、用户纠正、决策、假设、被拒绝方案和需要重新评估的事项。
- 当工具结果、命令输出、测试结果、日志、错误、堆栈、文件读取、搜索结果和精确值很重要时，保留它们。
- 记录已经应用的修复、无效尝试和不应重复的尝试。

### 5. 当前文件、代码区域与产物
- 记录当前任务中已检查、修改、创建、删除、生成或仅讨论过的文件。
- 包括相关函数、类、API、配置键、文档、生成物、代码库模式、模块职责、公共 API，以及为什么重要。

### 6. 代码仓库理解
- 保留整个对话中获得的、有助于后续工作的代码仓库理解。
- 包括架构、子系统职责、模块边界、公共 API、导出面、配置约定、测试/构建/lint 入口、代码模式和职责边界。
- 记录文件、类、函数、命令、文档、示例和生成物之间的重要关系。
- 优先保留简洁、持久的理解，而不是原始文件列表。
- 标记假设、不确定映射，以及后续需要重新验证的理解。

### 7. 历史用户请求与结果
- 列出更早已完成轮次中的用户消息，不包括工具结果。
- 当原话会影响需求、纠正、决策或未来行为时，保留用户原话。
- 记录历史轮次的结果、最终回答、完成结果或交付物。
- 清楚标记已覆盖、已取消或已解决的历史请求。

### 8. 学到的用户要求、偏好与验收标准
- 从全量对话中提取关于用户的持久学习。
- 保留明确要求、偏好工作流、输出风格偏好、格式偏好、验收标准、评审标准、反复出现的约束，以及用户要求避免的做法。
- 如果用户纠正或反馈会影响未来行为，把它记录成行为规则。
- 当原话本身约束行为时，保留原话。
- 新旧偏好冲突时，优先采用更新/纠正后的偏好。
- 尽量区分当前任务要求和更广义的用户偏好。

### 9. 历史已完成工作
- 记录 agent 在更早已完成轮次中做了什么。
- 包括调查、文件读取、编辑、命令、测试、工具调用、生成物和已交付回答。
- 动作历史要简洁，但要足够说明哪些工作已经做过。

### 10. 持久历史信息
- 保留未来继续工作或准确回忆时仍可能有用的历史事实、约束、发现、决策、证据、代码库理解和用户偏好。
- 合并早期压缩状态中的重复信息。
- 发生冲突时，优先采用更新/纠正后的信息。

### 11. 跨轮次文件、代码区域与产物
- 记录整个对话中仍然相关的文件：已检查、修改、创建、删除、生成或仅讨论过的都可以包括。
- 包括相关函数、类、API、配置键、文档、示例、生成物、模块边界、公共 API，以及为什么重要。
- 只有当仓库结构知识会指导未来工作时，才保留它。

### 12. 开放工作、阻塞、风险与验证
- 保留待办任务、阻塞、开放问题、未解决工作、缺失检查、不完整编辑、待决决策和已知风险。
- 区分当前任务开放工作，以及不应在没有新用户请求时恢复的历史遗留项。
- 说明哪些已经验证，哪些尚未验证。

### 13. 关键上下文
- 记录重要技术事实、精确值、错误、未解问题，以及一旦丢失会带来成本或风险的细节。
- 当前任务关键上下文和持久历史关键上下文都可以保留。
- 如果遇到重要内容被卸载，也要保留准确的卸载路径，并简要说明卸载文件里大概是什么内容。
- 如果没有，写“（无）”。

### 14. 相关文件
- 列出相关文件或目录的完整路径，并说明其重要性。
- 当前任务文件、持久历史文件和跨模块仓库路径都可以保留。
- 如果重要内容被卸载，列出准确的卸载文件路径，并简要说明卸载内容。
- 如果没有，写“（无）”。

只输出这两个必需块。不要在 `<coverage_check>` 和 `<state_snapshot>` 外添加关于压缩过程的说明。

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
<state_marker>（有没有必要在系统提示词里面说明）
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


#### 4.4.4 后台子 Agent / Team 协作状态 （那些东西是team不能被压缩掉的，认领任务）

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
