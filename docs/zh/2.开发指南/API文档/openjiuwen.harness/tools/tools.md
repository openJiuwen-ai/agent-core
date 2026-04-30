# tools

DeepAgent 内置工具实现。所有工具通过 Rails 或 `create_deep_agent()` 的 `tools` 参数注册到智能体的 `AbilityManager`。

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
| `SearchToolsTool` | 搜索可用工具（渐进式工具暴露） |
| `LoadToolsTool` | 加载指定工具（渐进式工具暴露） |

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

工具输出基类，定义工具返回结果的标准格式。

---

## 工厂函数

| 函数 | 说明 |
|---|---|
| `create_vision_tools(...)` | 批量创建视觉工具（ImageOCRTool + VisualQuestionAnsweringTool） |
| `create_audio_tools(...)` | 批量创建音频工具（AudioTranscriptionTool + AudioQuestionAnsweringTool + AudioMetadataTool） |
| `create_todos_tool(...)` | 创建待办事项工具 |
| `create_cron_tools(...)` | 创建定时任务工具 |
