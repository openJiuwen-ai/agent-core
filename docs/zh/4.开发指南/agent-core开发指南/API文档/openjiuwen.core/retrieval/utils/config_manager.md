# openjiuwen.core.retrieval.utils.config_manager

## class openjiuwen.core.retrieval.utils.config_manager.ConfigManager

统一配置管理器，支持从文件加载和保存配置。


```python
ConfigManager(config_path: Optional[str] = None)
```

初始化配置管理器。

**参数**：

* **config_path**(str, 可选)：配置文件路径，如果提供则自动加载。默认值：None。

### load_from_file

```python
load_from_file(path: str) -> None
```

从文件加载配置（支持 JSON 和 YAML 格式）。

**参数**：

* **path**(str)：配置文件路径。

**异常**：

* **FileNotFoundError**：配置文件不存在时抛出。
* **ImportError**：YAML 文件需要 PyYAML 但未安装时抛出。
* **ValueError**：不支持的文件格式时抛出。

### save_to_file

```python
save_to_file(path: str) -> None
```

保存配置到文件（支持 JSON 和 YAML 格式）。

**参数**：

* **path**(str)：配置文件路径。

**异常**：

* **ValueError**：没有配置可保存时抛出。
* **ImportError**：YAML 文件需要 PyYAML 但未安装时抛出。
* **ValueError**：不支持的文件格式时抛出。

### get_config

```python
get_config(config_type: Type[T]) -> Optional[T]
```

获取指定类型的配置。

**参数**：

* **config_type**(Type[T])：配置类型。

**返回**：

**Optional[T]**，返回指定类型的配置，如果不存在则返回 None。

### get_knowledge_base_config

```python
get_knowledge_base_config() -> KnowledgeBaseConfig
```

获取知识库配置。

**返回**：

**KnowledgeBaseConfig**，返回知识库配置。

**异常**：

* **ValueError**：知识库配置未加载时抛出。

### update_config

```python
update_config(config: BaseModel) -> None
```

更新配置。

**参数**：

* **config**(BaseModel)：配置对象。
