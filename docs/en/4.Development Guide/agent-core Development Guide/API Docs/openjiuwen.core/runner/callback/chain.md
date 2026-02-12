# openjiuwen.core.runner.callback.chain

Callback chain execution with rollback support.

## class CallbackChain

```python
class CallbackChain(name: str = "")
```

Executes a group of callbacks in order with error handling and rollback; for transactional-style flows.

**Parameters**: **name** (str, optional): Name of the chain. Default: "".

### add

```python
def add(
    self,
    callback_info: CallbackInfo,
    rollback_handler: Optional[Callable] = None,
    error_handler: Optional[Callable] = None
) -> None
```

Add a callback to the chain.

**Parameters**: **callback_info** (CallbackInfo): Callback info. **rollback_handler** (Optional[Callable], optional): Function to call on rollback. Default: None. **error_handler** (Optional[Callable], optional): Function to call on error. Default: None.

### remove

```python
def remove(self, callback: Callable) -> None
```

Remove a callback from the chain.

**Parameters**: **callback** (Callable): Callback to remove.

### execute

```python
async def execute(self, context: ChainContext) -> ChainResult
```

Execute the chain in priority order; each callback receives the previous result as input. Supports retry, error handling, and rollback on failure.

**Parameters**: **context** (ChainContext): Chain execution context (event name, initial args/kwargs, results, metadata, etc.).

**Returns**: **ChainResult**, with final action (CONTINUE/BREAK/ROLLBACK, etc.), result, context, and optional exception.
