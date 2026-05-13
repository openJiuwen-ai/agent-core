---
name: verify_ext
description: Runtime extension 验证规范 — 验证 harness package 中 tools、rails、skills 是否能真实热加载并可运行
immutable: true
tools:
  - read_file
  - bash_tool
  - glob_tool
  - grep_tool
---

# Verify Extension Skill

你是 auto-harness 扩展验证阶段的规范。目标不是证明文件能 import，而是证明生成的 runtime extension 在真实 harness 加载路径里可注册、可观测、可调用。

## 验证分层

### L1 结构校验

必须检查：
- `harness_config.yaml` 存在且 `schema_version: harness_config.v0.1`
- rail/tool 条目必须是 `type: package`
- 每个 rail/tool 条目必须包含 `module` 和 `class`
- `module` 必须以 `openjiuwen.extensions.harness.<extension_name>` 开头
- 测试代码导入和实例化 rail/tool 时，必须以 `harness_config.yaml` 实际声明的 `module` 和 `class` 为唯一来源；不要手写或猜测 module path
- `module` 必须能映射到扩展根目录内真实存在的 `.py` 文件
- `__init__.py` 不得包含 re-export
- Tool class 必须无参构造，且自己创建 `ToolCard`
- `ToolCard.id` 和 `ToolCard.name` 必须显式设置，推荐一致
- skill 目录必须包含合法 `SKILL.md`

失败归因：
- `manifest_invalid`
- `entry_point_not_allowed`
- `module_import_failed`
- `class_init_failed`
- `skill_manifest_invalid`

### L2 临时热加载

必须创建临时 `DeepAgent`，调用真实加载路径：

```python
loaded = await agent.load_harness_config(config_path)
```

断言：
- 每个 rail 返回 `rail:<ClassName>`
- 每个 tool 返回 `tool:<ClassName>`
- Tool 的 `ToolCard.name` 出现在 `agent.ability_manager.list()`
- Skill 目录被追加到现有或新建的 `SkillUseRail`

这一层必须覆盖 `DeepAgent.load_harness_config()`，不能只调用 `load_runtime_rails()` / `load_runtime_tools()`。

失败归因：
- `harness_load_failed`
- `rail_not_registered`
- `tool_not_registered`
- `skill_not_loaded`

### L3 运行时验收

运行时验收必须使用结构化 test spec，不接受“模型自然语言说成功”作为通过依据。

推荐 spec：

```json
{
  "name": "runtime_extension_acceptance",
  "components": ["rail", "tool", "skill"],
  "prewarm_queries": [
    "请简单回复：hello"
  ],
  "tool_tests": [
    {
      "tool_name": "example_tool",
      "query": "请调用 example_tool，并只根据工具结果回答。",
      "assertions": {
        "must_call_tool": "example_tool",
        "tool_result_success": true,
        "required_fields": ["source"]
      }
    }
  ],
  "rail_tests": [
    {
      "query": "请完成一个会触发 rail 生命周期的简短任务。",
      "assertions": {
        "observable_state_file": ".state/<session_id>.json",
        "json_contains": ["updated_at"],
        "must_observe_side_effect": true
      }
    }
  ],
  "skill_tests": [
    {
      "query": "根据已加载的扩展技能，说明这个扩展适合何时使用。",
      "assertions": {
        "answer_contains_any": ["统计", "工具", "流程", "策略"]
      }
    }
  ]
}
```

Tool 验收必须使用 agent-core/harness 自身可观测接口：
- 检查 `agent.ability_manager.list()` 中是否存在目标 `ToolCard.name`
- 检查 `Runner.resource_mgr.get_tool(tool_id)` 能取回工具实例
- 优先通过 agent-core 工具执行链验证工具，例如构造 `ToolCall` 并让 `ability_manager` 在 session 下执行
- 可以直接调用 `Runner.resource_mgr.get_tool(tool_id).invoke(..., session=session)` 验证工具核心输出，但这不能替代工具注册检查
- 如果必须验证“模型会调用工具”，使用 harness 内可观测对象：测试 Tool 写入状态文件、测试 Rail 观察 `before_tool_call` / `after_tool_call`，或注册测试用 tracking rail 记录 `ToolCallInputs`
- `ToolOutput.success` 必须为 true，或返回明确结构化降级结果
- 输出必须包含 spec 中声明的字段

### 文件产物验收

文件/产物生成类 Tool 必须通过 artifact-level acceptance gate。不能只验证“工具返回成功”，必须验证真实产物存在且格式有效。

测试必须做到：
- 调用 Tool 时传入 pytest `tmp_path` 下的 `output_path`，避免写到未知工作目录。
- 断言 Tool 返回结构化字段：`success=true`、`path` 或 `absolute_path`、`exists=true`、`format`、`size_bytes > 0`。
- 断言返回路径对应的文件真实存在，后缀与 `format` 匹配，文件大小大于 0。
- 如果 Tool 返回 `success=true` 但文件不存在、格式错误或只是中间结构，必须失败。

格式最低断言：
- PPTX：使用 `zipfile.ZipFile` 打开，断言包含 `[Content_Types].xml`、`ppt/presentation.xml`，且至少存在一个 `ppt/slides/slide*.xml`。
- DOCX：使用 `zipfile.ZipFile` 打开，断言包含 `[Content_Types].xml` 和 `word/document.xml`。
- PDF：读取文件头，断言以 `%PDF` 开始。
- JSON：使用 `json.load` 重新解析，并断言设计声明的关键字段存在。
- 图片：用文件头或可用标准/项目依赖识别格式，不能只看扩展名。

禁止通过：
- JSON、Markdown、纯文本或“待下游转换”的中间结构冒充 PPTX/DOCX/PDF。
- Tool 没有写文件但返回成功。
- Tool 写到了未声明位置，导致用户拿不到产物。

Rail 验收必须检查可观测副作用：
- 文件状态，例如 `<extension_root>/.state/<session_id>.json`
- prompt section 注入
- tool gating / rewritten tool args
- agent-core session stream 中的 `OutputSchema`，仅当扩展主动写入 session stream 时可用
- steering message

如果 Rail 与 Tool 配合，必须验证“Rail 写状态 + Tool 读状态”：
- prewarm query 先触发 Rail 生命周期
- test query 明确要求调用 Tool
- Tool 结果必须读到 Rail 写入的 session 状态

Skill 验收必须检查：
- `SKILL.md` frontmatter 合法
- skill 内容能被加载进上下文
- 测试 query 能触发 skill 的关键行为或关键术语

失败归因：
- `tool_not_called`
- `tool_result_failed`
- `tool_result_schema_missing`
- `artifact_not_created`
- `artifact_format_invalid`
- `artifact_placeholder_output`
- `rail_hook_not_observed`
- `rail_tool_state_not_shared`
- `skill_not_used`

## 自修复循环

verify_ext 失败后必须把以下内容交给 implement_ext agent：
- 失败归因码
- 最小复现 query
- 期望事件或状态
- 实际事件或状态
- 相关日志摘要

最多修复 3 轮。只有 L1、L2、L3 全部通过，才允许进入 activate。

## 状态交互规则

当 Tool 需要读取 Rail 采集的数据时，必须使用文件系统状态作为事实来源：
- Rail 从 `ctx.session.get_session_id()` 获取 session_id
- Tool 从 `kwargs["session"].get_session_id()` 获取 session_id
- 状态文件按 session_id 隔离，例如 `<extension_root>/.state/<session_id>.json`
- 写入使用锁和原子替换
- Tool 不得依赖 `kwargs["ctx"]`、`kwargs["agent"]` 或扫描 agent rails
- 模块级全局变量只能作为缓存，不能作为事实来源

参考：
- `openjiuwen/harness/tools/todo.py`
- `openjiuwen/harness/rails/task_planning_rail.py`
