# F_81 AgentTemplate 快照加载

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-08-19 |
| 范围 | `DeepAgentSpec`、`DeepAgent`、`NativeHarness` |
| 测试基线 | `test_agent_template_prepare.py` 与 `test_run_once.py`：8 passed |

## 背景

Team 成员可能在另一个进程或冷恢复路径中重新构建。只在平台装配时读取 AgentTemplate manifest 无法保证新建成员获得相同 persona、Skill、tool、rail 和 MCP；直接把 Pydantic 模型或运行时对象放进 TeamSpec 又会破坏 JSON 序列化边界。

## 决策

- 在 `DeepAgentSpec` 中保存 `AgentTemplateSpec.model_dump(mode="json")` 的普通字典快照。
- 提供 `DeepAgent.load_agent_template_spec()`，复用现有 extension resolver/binder 和失败回滚。
- `NativeHarness._prepare()` 反序列化并应用快照，同一实例通过 `_prepared` 保证只执行一次。
- 基础 rails 先初始化，模板随后热挂载；这是模板 Skill 复用宿主 `SkillUseRail` 的必要条件。模板加载仍位于任何模型调用之前。

## 拒绝的方案

- **NativeHarness 重新读取 manifest**：把平台包布局耦合进 agent-core，并使恢复结果受磁盘即时变化影响。
- **在 DeepAgentSpec 中保存 AgentTemplateSpec 实例**：增加 schema 循环依赖，也弱化 plain-data 序列化约束。
- **在基础 rails 初始化前挂模板 Skill**：extension binder 找不到已注册并初始化的 `SkillUseRail`，无法可靠 reload。
- **为 Team 单独实现模板挂载器**：会复制现有 extension binder 的资源去重、加载和回滚逻辑。

## 验证

- 快照经过 `DeepAgentSpec.model_dump_json()` / `model_validate_json()` 保持一致。
- `_prepare()` 顺序为初始化基础 rails、加载快照，重复调用不重复加载。
- 没有快照时不调用模板加载接口，现有 `run_once` 测试保持通过。

## 已知遗留

文件型资源仍以绝对路径保存在快照中。跨物理节点运行时，平台必须预先分发资源并保证相同路径可访问。
