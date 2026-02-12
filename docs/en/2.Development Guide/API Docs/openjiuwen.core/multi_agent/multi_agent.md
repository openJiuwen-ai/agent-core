# openjiuwen.core.multi_agent

## class openjiuwen.core.multi_agent.Session

```python
class openjiuwen.core.multi_agent.Session(session_id: str = None, envs: dict[str, Any] = None)
```

The core runtime session for `AgentGroup` execution. This class provides session management for scenarios involving `AgentGroup`.

**Parameters**:

- **session_id**(str, optional): The unique identifier of the session. Default: `None`，If not provided, a UUID will be automatically generated.
- **envs**(dict[str, Any], optional): Environment variables used during the execution of the `AgentGroup`，Default: `None`。

**Example**:

```python
>>> from openjiuwen.core.multi_agent import Session
>>> 
>>> session = Session(session_id="123")
>>> 
```

### get_session_id

```python
get_session_id(self) -> str
```

Returns the unique session identifier for the current `AgentGroup` execution.

**Returns**:

**str**: The unique session identifier of the current `AgentGroup` execution.。

**Example**:

```python
>>> from openjiuwen.core.multi_agent import Session
>>> 
>>> session = Session(session_id="123")
>>> 
>>> print(f"session id is: {session.get_session_id()}")
session id is: 123
```

### get_env

```python
get_env(self, key) -> Optional[Any]
```

Retrieves the value of an environment variable configured for the current `AgentGroup` execution.

**Parameters**:

- **key** (str): The key of the environment variable.

**Returns**:

**Optional[Any]**，The value of the environment variable configured for the current `AgentGroup` execution.