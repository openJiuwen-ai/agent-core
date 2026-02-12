# openjiuwen.core.multi_agent

## class openjiuwen.core.multi_agent.Session

```python
class openjiuwen.core.multi_agent.Session(session_id: str = None, envs: dict[str, Any] = None)
```

`AgentGroup`执行的核心运行时会话，实现了`AgentGroup`场景下的会话管理。

**参数**：

- **session_id**(str, 可选)：会话唯一标识。默认值：`None`，未提供时自动生成UUID。
- **envs**(dict[str, Any], 可选)：`AgentGroup`执行过程中使用的环境变量，默认值：`None`。

**样例**：

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

获取本次`AgentGroup`执行的唯一会话标识。

**返回**：

**str**：当前`AgentGroup`执行的唯一会话标识。

**样例**：

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

获取本次AgentGroup执行配置的环境变量的值。

**参数**：

- **key** (str)：环境变量的键。

**返回**：

**Optional[Any]**，为本次AgentGroup执行配置的环境变量的值。