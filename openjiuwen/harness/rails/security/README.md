# BaseSecurityRail

agent-core 安全护栏基类，为自定义安全检查提供标准化框架。

## 概述

`BaseSecurityRail` 是实现安全护栏的基类，通过拦截代理操作执行安全检查：

- **SecurityAllow** - 允许操作继续执行
- **SecurityReject** - 阻止操作并返回错误信息
- **SecurityInterrupt** - 暂停等待人工审批 (HITL)
- **SecurityAlert** - 发送警报但允许执行继续

## 核心概念

### Detection Type 与 Auto-confirm

#### 概念

**Detection Type** 是检测到的敏感信息类型标识，用于区分不同类型的检测结果。

Auto-confirm key 格式约定：
```
{rail_name}:{detection_type}:{event}
```

- `rail_name`：Rail 标识名（推荐类名去掉 `Rail` 后缀转小写）
- `detection_type`：检测类型标识（由 rail 决定）
- `event`：`before` 或 `after`（对应 BEFORE/AFTER 事件）

示例：
- `apikeyguardinterrupt:api_key_openai:before`
- `sensitivedatasanitize:pii_email:after`

#### Detection Type 生成

**Regex 检测：**
Pattern 与 type 关联定义：
```python
DETECTION_RULES = [
    {"pattern": r"sk-[a-zA-Z0-9_-]{20,}", "type": "api_key_openai"},
    {"pattern": r"AKIA[0-9A-Z]{16}", "type": "api_key_aws"},
]
```

**模型检测：**
从模型输出提取 type：
```python
detection_type = model_result.get("type", "unknown")
```

#### Auto-confirm 流程

1. 用户选择 "Always Allow"
2. `_store_auto_confirm` 存储到 session state
3. 后续同类型检测自动放行
4. Session 结束后失效

#### 命名建议

推荐格式：`{category}_{subcategory}`

- `api_key_openai`、`api_key_aws`、`api_key_generic`
- `pii_email`、`pii_phone`、`pii_name`
- `secret_generic`

避免：
- 过细：包含具体内容（如 `api_key_sk_xxx123`）
- 过宽：无法区分类型（如 `secret`）

#### Rail 前缀隔离

不同 rail 的同类型 detection_type 互不冲突：

```
Rail A: apikeyguardinterrupt:api_key:before
Rail B: customsecretguard:api_key:before
```

两条 key 独立存储，各自 auto-confirm。

### SecurityDecision 类型

| 类型 | 行为 | 适用场景 |
|------|------|----------|
| `SecurityAllow` | 继续执行 | 安全检查通过 |
| `SecurityReject` | 阻止执行 | 检测到敏感数据、违规操作 |
| `SecurityInterrupt` | 暂停等待审批 | 需要人工确认的危险操作 |
| `SecurityAlert` | 发送警报后继续执行 | 需要通知但不阻止的场景 |

### 各事件下的 Reject 行为差异

| 事件 | Reject 行为 | 代理状态 |
|------|------------|----------|
| `BEFORE_TOOL_CALL` | `_skip_tool` | **继续运行**，尝试其他方案 |
| `AFTER_TOOL_CALL` | `_skip_tool` | **继续运行**，尝试其他方案 |
| `BEFORE_MODEL_CALL` | `force_finish` | **终止**，返回错误 |
| `AFTER_MODEL_CALL` | `force_finish` | **终止**，返回错误 |

**设计原因**：
- `TOOL_CALL` 事件：工具被跳过，代理收到 "blocked for security reason" 消息，可尝试替代方案
- `MODEL_CALL` 事件：LLM 内容撤回，代理终止

## 支持的事件

| 事件 | 触发时机 | 支持 Interrupt? |
|------|----------|-----------------|
| `BEFORE_INVOKE` | 代理 invoke 开始前 | ✓ |
| `BEFORE_MODEL_CALL` | LLM 调用前 | ✗ (自动转为 Reject) |
| `AFTER_MODEL_CALL` | LLM 响应后 | ✗ (自动转为 Reject) |
| `BEFORE_TOOL_CALL` | 工具执行前 | ✓ |
| `AFTER_TOOL_CALL` | 工具执行后 | ✓ |
| `ON_MODEL_EXCEPTION` | LLM 调用失败时 | ✗ |
| `ON_TOOL_EXCEPTION` | 工具执行失败时 | ✓ |

**注意**：MODEL 事件不支持 Interrupt，会自动转为 Reject。

## 类结构

### 核心属性

```python
class BaseSecurityRail(AgentRail):
    priority: int = 90          # 优先级，数值越大越先执行
    supported_events: Set[AgentCallbackEvent] = set()  # 监听的事件
```

### 核心方法

```python
# 必须实现
async def run_security_check(self, security_ctx: SecurityCheckContext) -> SecurityDecision

# 可选覆盖
async def apply_security_decision(self, security_ctx, decision) -> None

# 决策创建方法
def allow(self, new_args: Optional[str] = None) -> SecurityAllow
def reject(self, message: str, result=None, tool_message=None) -> SecurityReject
def interrupt(self, request: InterruptRequest, subject_id: str) -> SecurityInterrupt
def alert(self, message: str, level: SecurityAlertLevel, ...) -> SecurityAlert
```

### SecurityCheckContext

```python
@dataclass
class SecurityCheckContext:
    callback_ctx: AgentCallbackContext  # 回调上下文
    event: AgentCallbackEvent           # 当前事件
    user_input: Any | None              # Interrupt 恢复时的用户输入
    auto_confirm_config: dict | None    # 自动确认配置
    subject_id: str                     # 主体标识 (tool_call_id 等)
```

## 内置辅助方法

### 消息处理

| 方法 | 功能 | 返回值 |
|------|------|--------|
| `_pop_last_user_message(ctx, with_history=False)` | 弹出最后一条 user 消息 | List[popped_msg] |
| `_pop_last_assistant_message(ctx, with_history=False)` | 弹出最后一条 assistant 消息 | List[popped_msg] |
| `_pop_last_tool_message(ctx, with_history=False)` | 弹出最后一条 tool 消息（工具结果） | List[popped_msg] |
| `_replace_messages(ctx, messages, with_history)` | 用修改后的完整消息列表替换当前消息 | None |

`with_history=False` 仅处理当前轮次消息（不含历史）；`True` 处理完整历史。

### Interrupt 流程

| 方法 | 功能 | 返回值 |
|------|------|--------|
| `_handle_interrupt_resume(security_ctx, auto_confirm_key)` | 处理 Interrupt 恢复流程 | SecurityAllow/SecurityReject/None |
| `_is_auto_confirmed(config, key)` | 检查是否已自动确认 | Boolean |
| `_store_auto_confirm(ctx, key)` | 存储自动确认状态 | None |

### 其他

| 方法 | 功能 |
|------|------|
| `_skip_tool(ctx, tool_call, tool_result, tool_message)` | 跳过工具执行，返回指定结果 |
| `_raise_tool_interrupt(tool_name, tool_call, request)` | 触发工具中断异常 |

## 使用示例

### 最简示例

```python
from openjiuwen.harness.rails.security.base_security_rail import (
    BaseSecurityRail,
    SecurityCheckContext,
    SecurityAllow,
    SecurityReject,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent

class SimpleRejectRail(BaseSecurityRail):
    """简单的安全护栏：阻止包含 'secret' 的工具结果"""
    
    priority = 85
    supported_events = {AgentCallbackEvent.AFTER_TOOL_CALL}
    
    async def run_security_check(self, security_ctx: SecurityCheckContext):
        ctx = security_ctx.callback_ctx
        tool_result = getattr(ctx.inputs, "tool_result", None)
        
        if tool_result and "secret" in str(tool_result):
            return self.reject(message="敏感数据已被阻止")
        
        return self.allow()
```

### 完整示例：带 Interrupt 和自定义 apply

```python
from openjiuwen.harness.rails.base import DeepAgentRail  # RailManager 验证必需
from openjiuwen.harness.rails.security.base_security_rail import (
    BaseSecurityRail,
    SecurityCheckContext,
    SecurityAllow,
    SecurityReject,
    SecurityInterrupt,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.foundation.llm import ToolMessage

class ApiKeyGuardRail(BaseSecurityRail):
    """检测 API Key 并要求人工审批"""
    
    priority = 90
    supported_events = {AgentCallbackEvent.AFTER_TOOL_CALL}
    
    API_KEY_PATTERN = r"sk-[a-zA-Z0-9]{20,}"
    
    async def run_security_check(self, security_ctx: SecurityCheckContext):
        ctx = security_ctx.callback_ctx
        tool_result = getattr(ctx.inputs, "tool_result", None)
        tool_call_id = ctx.inputs.tool_call.id if ctx.inputs.tool_call else ""
        
        # 处理 Interrupt 恢复
        auto_confirm_key = f"apikey_guard:{tool_call_id}"
        resume_decision = self._handle_interrupt_resume(security_ctx, auto_confirm_key)
        if resume_decision is not None:
            return resume_decision
        
        # 检测敏感数据
        content = self._extract_content(tool_result)
        import re
        if re.search(self.API_KEY_PATTERN, content):
            return self.interrupt(
                InterruptRequest(
                    message="检测到 API Key，是否允许继续?",
                    payload_schema={
                        "type": "object",
                        "properties": {
                            "approved": {"type": "boolean"},
                            "auto_confirm": {"type": "boolean"},
                        },
                    },
                    auto_confirm_key=auto_confirm_key,
                ),
                subject_id=tool_call_id,
            )
        
        return self.allow()
    
    async def apply_security_decision(self, security_ctx, decision):
        if isinstance(decision, SecurityAllow):
            return
        
        if isinstance(decision, SecurityReject):
            ctx = security_ctx.callback_ctx
            inputs = ctx.inputs
            tool_call_id = inputs.tool_call.id if inputs.tool_call else ""
            inputs.tool_result = decision.message
            inputs.tool_msg = ToolMessage(
                content=decision.message,
                tool_call_id=tool_call_id,
            )
            return
        
        if isinstance(decision, SecurityInterrupt):
            ctx = security_ctx.callback_ctx
            self._raise_tool_interrupt(
                tool_name=ctx.inputs.tool_name,
                tool_call=ctx.inputs.tool_call,
                request=decision.request,
            )
        
        await super().apply_security_decision(security_ctx, decision)
    
    def _extract_content(self, result) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return result.get("content", "") or result.get("output", "") or ""
        return str(result) if result else ""

__all__ = ["ApiKeyGuardRail"]
```

### 使用 SecurityAlert 示例

```python
from openjiuwen.harness.rails.security.base_security_rail import (
    BaseSecurityRail,
    SecurityCheckContext,
    SecurityAllow,
    SecurityAlert,
    SecurityAlertLevel,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent

class LoggingAlertRail(BaseSecurityRail):
    """检测敏感操作但只发送警报，不阻止执行"""
    
    supported_events = {AgentCallbackEvent.BEFORE_TOOL_CALL}
    
    async def run_security_check(self, security_ctx: SecurityCheckContext):
        ctx = security_ctx.callback_ctx
        tool_name = getattr(ctx.inputs, "tool_name", "")
        
        if tool_name == "bash":
            tool_args = getattr(ctx.inputs, "tool_args", {})
            command = tool_args.get("command", "")
            if "rm -rf" in command:
                return self.alert(
                    message="检测到危险命令: rm -rf",
                    level=SecurityAlertLevel.WARNING,
                    display_mode="popup",
                )
        
        return self.allow()
```

## 优先级与执行顺序

| 优先级 | 典型用途 |
|--------|----------|
| 100+ | 最高优先级，最终检查 |
| 85-95 | 安全护栏 |
| 50-70 | 处理/转换护栏 |
| 10-30 | 日志/监控护栏 |

**规则**：优先级数值越大，越先执行。同优先级按注册顺序。

## 测试自定义护栏

```bash
# 单元测试
PYTHONPATH=. uv run pytest tests/unit_tests/harness/rails/test_base_security_rail.py -v

# 集成测试
PYTHONPATH=. uv run pytest tests/system_tests/rail/test_base_security_rail_integration.py -v
```

## 相关文档

- `examples/security_rail_demo/` - 完整示例
- `openjiuwen/harness/rails/security/base_security_rail.py` - 源码实现
- `docs/zh/2.开发指南/高阶用法/安全护栏Guardrail.md` - 详细文档