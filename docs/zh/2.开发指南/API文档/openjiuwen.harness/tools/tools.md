# tools

DeepAgent 内置工具实现。所有工具通过 Rails 或 `create_deep_agent()` 的 `tools` 参数注册到智能体的 `AbilityManager`。

[浏览器子智能体](../subagents/浏览器子智能体.md)的工具（Playwright MCP 工具、页面探测、批量交互、自定义动作）单独记录在 [`browser_tools`](./浏览器子智能体工具.md) 中。

---

## 内置工具一览

### 文件系统工具

| 工具 | 说明 |
|---|---|
| `ReadFileTool` | 读取文件内容，支持行号偏移和限制 |
| `WriteFileTool` | 写入文件内容，支持创建新文件或覆盖现有文件 |
| `EditFileTool` | 编辑文件，执行精确的字符串替换 |
| `GlobTool` | 文件模式匹配搜索（如 `**/*.py`） |
| `GrepTool` | 基于正则表达式的文件内容搜索 |
| `ListDirTool` | 列出目录内容 |

### Shell 工具

| 工具 | 说明 |
|---|---|
| `BashTool` | 执行 Bash 命令，支持超时控制 |
| `PowerShellTool` | 执行 PowerShell 命令并返回输出 |

### 代码工具

| 工具 | 说明 |
|---|---|
| `CodeTool` | 执行代码片段（Python 等），支持沙箱化执行 |

### 视觉工具

| 工具 | 说明 |
|---|---|
| `ImageOCRTool` | 图像 OCR 文字识别 |
| `VisualQuestionAnsweringTool` | 视觉问答，基于图像回答自然语言问题 |

### 音频工具

| 工具 | 说明 |
|---|---|
| `AudioTranscriptionTool` | 音频转录为文本 |
| `AudioQuestionAnsweringTool` | 音频问答，基于音频内容回答问题 |
| `AudioMetadataTool` | 提取音频元数据（时长、格式、比特率等） |

### Web 工具

| 工具 | 说明 |
|---|---|
| `WebFetchWebpageTool` | 获取网页内容并转换为 Markdown |
| `WebFreeSearchTool` | 免费网页搜索 |
| `WebPaidSearchTool` | 付费网页搜索（更高质量） |

#### 来源选择与检索质量

`free_search` 接受 `provider="auto"`（默认）、`"bing"` 或 `"duckduckgo"`。
指定来源必须已经启用，不会回退到其他来源，也不能跳出 `allowed_domains`。
自动模式按 DuckDuckGo、非验证码失败时的 reader 入口、Bing 顺序尝试，受来源预算约束。
360 来源已移除，旧环境配置或 `enabled_engines` 不能再次启用；显式 `provider="360"` 返回 `invalid_input`，不发网络请求。

先做 URL 合法性、去重、搜索导航和允许域名检查；每批非空候选最多 30 条，一次性复用当前 Agent 的
`get_llm()` 模型评估。独立短上下文只包含可取得的原任务、搜索子问题和有界标题/摘要/来源路径，
不带整个会话历史，不提供工具。不再使用查询专属别名、意图词典或关键词相关性规则。
独立调用者可以通过 `WebFreeSearchTool(model=model)` 传入已有 `Model`，不会隐式创建新客户端。

模型输出必须完整覆盖输入候选 ID，并给出 `relevant`、`uncertain`、`rejected` 三态及理由。
评估成功后，相关线索排在前面，待核验线索最多保留 2 条，并标明 `Relevance: uncertain`；全部待核验时用
`Content status: uncertain`，不能标为 `ok`。摘要缺失和搜索报告的日期本身不是不相关的充分证据。
语义评估不等于事实核查，也不能找回搜索源未返回的内容。
后台 `FREE_SEARCH_ASSESSMENT_TIMEOUT_SECONDS=15` 控制每批超时（整数，限制在 1–60 秒），
同时消耗原检索总时限，评估器本身不重试。评估单次调用通过 SDK 能力映射优先关闭思考，
不能关闭时使用可用的 minimal/low 档位；不修改主 Agent 配置。输出上限随批量大小限制在 4096-8192 tokens，
长度截断明确返回 `assessment_output_truncated`，不采信残留 JSON。缺少模型、超时、输出不合法或服务异常时标记
`Assessment status: unavailable`，与低相关拒绝区分；按来源顺序返回至 `max_results` 条结构有效的未核验线索，
保留警告，不输出推荐抓取排名。应比较全部线索再选来源，不要逐条全部抓取。
不因此增加市场请求。取消继续向上传播。查询、来源原文、URL、来源次数与访问限制处理不变，
已拒绝候选不能重新标成成功结果。
Reader 摘要按自己的结果标题和 URL 绑定，缺失时不能串到下一条。
`Content status: ok` 只说明存在可用候选或正文，不证明事实正确、行情最新。

排障可设置 `FREE_SEARCH_DEBUG=true`，并用 `FREE_SEARCH_DEBUG_DIR` 指定目录（默认是工作目录下 `.tmp/free_search_debug`）。
同一 run ID 的 JSON 保存响应状态/最终 URL、有界响应摘录、过滤前的全部已解析候选，以及逐条保留/拒绝/降权原因。
评估状态、耗时毫秒数和脱敏后的错误类型单独记录，不与相关性结论混淆。
数量上限与相关性拒绝分开记录。不记录响应头和代理凭据，但查询与公开页面摘录本身也可能敏感，应私下保存，不要提交。
搜索响应正文上限为 2 MB，截断报告 `truncated`，不报 `empty`。
验证码页面即使返回 HTTP 200 也报告 `blocked`，不能反复重试或绕过验证。

抓取即使返回 HTTP 200，也会区分验证码/访问限制、动态空壳和空正文。
验证码检测同时检查 HTML 提取后文本，以及 reader 剥离元数据后的正文。
通过 MIME 或文件头识别的 PDF/二进制响应返回 `unsupported_content`，不会将原始字节当作提取成功的正文，
也不会自动转 reader；需要使用合适的文档工具提取 PDF。
提取保留 HTTP(S) 链接、表格行列分隔和 `[missing]` 空单元格；不能用相邻字段补全缺失值。
只有未被拦截的公开 JavaScript 空壳页可回退 reader；受限域名抓取不走 reader。
单响应头字段上限为 64 KiB；代理和 reader 回退共用剩余请求超时。不能绕过认证、限流或验证码。

需在模型多轮、并发工具之间共享检索上限时，在 Agent 注册 Rail：

```python
from openjiuwen.harness.rails.web_research_rail import WebResearchBudgetRail

await agent.register_rail(WebResearchBudgetRail())
```

每次 invocation 默认 `WEB_RESEARCH_SEARCHES_PER_ENGINE=2`、`WEB_RESEARCH_MAX_ENGINES=2`、
`WEB_RESEARCH_MAX_FETCHES=3`、`WEB_RESEARCH_TIMEOUT_SECONDS=90`，从第一次检索开始计时。
DuckDuckGo 和其 reader 合计为一个来源；预算耗尽返回 `Content status: budget_exhausted`，不再联网。
未挂载 Rail 时，多次直接调用工具不共享计数。
这些是检索上限，不是权限边界、整个会话配额或浏览器/付费/自定义工具的预算。
证据充分即可停止，不强制读取两页，不自动切换付费搜索。

### 待办工具

| 工具 | 说明 |
|---|---|
| `TodoTool` | 统一待办事项工具（创建、列出、修改） |
| `TodoCreateTool` | 创建待办事项 |
| `TodoListTool` | 列出待办事项 |
| `TodoModifyTool` | 修改待办事项状态 |

### 技能工具

| 工具 | 说明 |
|---|---|
| `ListSkillTool` | 列出可用技能 | 
| `SkillTool` | 获取与技能相关的技能文件 |

### 工具管理

| 工具 | 说明 |
|---|---|
| `ToolSearchTool` | 搜索延迟工具并返回完整参数 schema |

### 人机交互工具

| 工具 | 说明 |
|---|---|
| `AskUserTool` | 向用户提问以收集信息、澄清歧义或做出决策（HITL 场景） |

### 定时任务工具

| 工具 | 说明 |
|---|---|
| `CronToolContext` | 定时任务上下文和工具集 |

---

## 辅助类

### class ToolOutput

```python
class ToolOutput: ...
```

工具输出基类，定义工具返回结果的标准格式（定义于 `openjiuwen.core.foundation.tool`，此处再导出）。模型看到的文本由工具的 `render_for_llm` 生成：默认成功时取 `data["content"]`、失败时取 `error`，各工具可覆写以定制。

---

## 工厂函数

| 函数 | 说明 |
|---|---|
| `create_vision_tools(...)` | 批量创建视觉工具（ImageOCRTool + VisualQuestionAnsweringTool） |
| `create_audio_tools(...)` | 批量创建音频工具（AudioTranscriptionTool + AudioQuestionAnsweringTool + AudioMetadataTool） |
| `create_todos_tool(...)` | 创建待办事项工具 |
| `create_cron_tools(...)` | 创建定时任务工具 |
