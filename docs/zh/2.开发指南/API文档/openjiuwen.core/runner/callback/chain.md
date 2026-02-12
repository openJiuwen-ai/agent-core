# openjiuwen.core.runner.callback.chain

带回滚能力的回调链执行逻辑。

## class CallbackChain

```python
class CallbackChain(name: str = "")
```

按顺序执行一组回调，支持错误处理与回滚。用于需要“事务式”执行的场景。

**参数**：**name**(str, 可选)：链的名称。默认值：""。

### add

```python
def add(
    self,
    callback_info: CallbackInfo,
    rollback_handler: Optional[Callable] = None,
    error_handler: Optional[Callable] = None
) -> None
```

向链中追加一个回调。

**参数**：**callback_info**(CallbackInfo)：回调信息。**rollback_handler**(Optional[Callable], 可选)：回滚时调用的函数。默认值：None。**error_handler**(Optional[Callable], 可选)：出错时调用的函数。默认值：None。

### remove

```python
def remove(self, callback: Callable) -> None
```

从链中移除指定回调。

**参数**：**callback**(Callable)：要移除的回调函数。

### execute

```python
async def execute(self, context: ChainContext) -> ChainResult
```

按优先级顺序执行链中的回调，上一个回调的返回值作为下一个回调的输入；支持重试、错误处理与失败回滚。

**参数**：**context**(ChainContext)：链执行上下文（事件名、初始 args/kwargs、results、metadata 等）。

**返回**：**ChainResult**，包含最终动作（CONTINUE/BREAK/ROLLBACK 等）、结果、上下文及可能的异常。
