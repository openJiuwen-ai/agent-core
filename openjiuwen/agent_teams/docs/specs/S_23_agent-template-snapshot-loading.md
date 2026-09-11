# S_23 AgentTemplate 快照加载

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/schema/deep_agent_spec.py`、`openjiuwen/harness/deep_agent.py`、`openjiuwen/agent_teams/harness/native_harness.py` |
| 最近一次修订日期 | 2026-08-19 |
| 关联 feature | `F_81_agent-template-snapshot-loading.md` |

## 范围 / 边界

本规约定义 Team 成员如何携带已解析的 `AgentTemplateSpec` 快照，以及 `NativeHarness` 如何在成员启动前重新挂载该快照。磁盘包发现、manifest 解析和 Team 固定成员名单由平台层负责，不属于 agent-core。

## 不变量

1. `DeepAgentSpec.agent_template_spec` 只保存 JSON 可序列化的普通映射，默认 `None`。
2. 快照由平台在 TeamSpec 组装时生成；`NativeHarness` 不读取磁盘 manifest。
3. `NativeHarness._prepare()` 先初始化基础 rails，再调用 `load_agent_template_spec()`。模板 Skill 必须绑定到已存在的 `SkillUseRail`，但整个 `_prepare()` 完成前不得发生模型调用。
4. 同一 `NativeHarness` 只应用快照一次；新的 `NativeHarness` 从其持有的 `DeepAgentSpec` 再应用一次。
5. 模板加载失败时，沿用 extension binder 的批次回滚语义，成员不得进入可运行状态。
6. `agent_template_spec=None` 时行为与此前完全一致。

## 接口契约

```python
class DeepAgentSpec(BaseModel):
    agent_template_spec: dict[str, Any] | None = None
```

```python
async def DeepAgent.load_agent_template_spec(
    self,
    spec: AgentTemplateSpec,
    *,
    context: BuildContext | None = None,
) -> LoadRecord: ...
```

`load_agent_template_spec()` 是 `load_agent_template(path)` 的内存版本：调用方必须提供已经绝对化的文件路径；方法复用 `resolve_agent_template_parts()` 与 `apply_extension_hot()`，不改变宿主 AgentCard 和模型。

## 数据流

```text
DeepAgentSpec.agent_template_spec (plain dict)
  -> NativeHarness._prepare()
  -> AgentTemplateSpec.model_validate()
  -> DeepAgent.load_agent_template_spec()
  -> resolve_agent_template_parts()
  -> apply_extension_hot()
```

## 与其它 spec 的关系

- Spec 可序列化和 Spec → Runtime 单向流遵循 `S_01_public-api-and-spec-flow.md`。
- NativeHarness 的交互准备和 round 生命周期遵循 `S_18_harness-interaction-contract.md`。
