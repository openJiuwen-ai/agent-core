# openjiuwen.core.retrieval.utils.config_manager

## class openjiuwen.core.retrieval.utils.config_manager.ConfigManager

Configuration manager for unified configuration management, supports loading and saving from files.


```python
ConfigManager(config_path: Optional[str] = None)
```

Initialize configuration manager.

**Parameters**:

* **config_path**(str, optional): Configuration file path, will be automatically loaded if provided. Default: None.

### load_from_file

```python
load_from_file(path: str) -> None
```

Load configuration from file (supports JSON and YAML formats).

**Parameters**:

* **path**(str): Configuration file path.

### save_to_file

```python
save_to_file(path: str) -> None
```

Save configuration to file (supports JSON and YAML formats).

**Parameters**:

* **path**(str): Configuration file path.

### get_config

```python
get_config(config_type: Type[T]) -> Optional[T]
```

Get configuration of specified type.

**Parameters**:

* **config_type**(Type[T]): Configuration type.

**Returns**:

**Optional[T]**, returns configuration of specified type, or None if it does not exist.

### get_knowledge_base_config

```python
get_knowledge_base_config() -> KnowledgeBaseConfig
```

Get knowledge base configuration.

**Returns**:

**KnowledgeBaseConfig**, returns knowledge base configuration.

### update_config

```python
update_config(config: BaseModel) -> None
```

Update configuration.

**Parameters**:

* **config**(BaseModel): Configuration object.

