# security

openJiuwen 的安全模块提供护栏（Guardrail）安全检测与拦截框架。

## guardrail（护栏框架）

护栏框架用于监控 Agent 执行流程中的关键事件，并在事件触发时执行安全检测，以防范提示词注入、敏感数据泄露等安全风险。

### 核心类

| CLASS                                                                                                                 | DESCRIPTION          |
|-----------------------------------------------------------------------------------------------------------------------|----------------------|
| [GuardrailBackend](./guardrail/backends.md#class-openjiuwencoresecurityguardrailguardrailbackend)             | 检测后端抽象接口类，用于实现自定义安全检测逻辑。           |
| [BaseGuardrail](./guardrail/guardrail.md#class-openjiuwencoresecurityguardrailbaseguardrail)                 | 护栏抽象基类，用于创建自定义护栏。              |
| [PromptInjectionGuardrail](./guardrail/builtin.md#class-openjiuwencoresecurityguardrailpromptinjectionguardrail) | Prompt 注入检测护栏，监控 LLM 输入和工具输出事件。 |


### 快速开始

1. **实现检测后端**：继承 `GuardrailBackend` 实现自定义检测逻辑
2. **配置护栏**：使用内置护栏类（如 `PromptInjectionGuardrail`）并设置检测后端
3. **注册到框架**：将护栏注册到回调框架以自动触发检测

详细用法请参考各类的完整文档。
