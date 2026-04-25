# Logging

全项目统一的日志子系统。提供 **模块级 logger 聚合** + **结构化事件模型** + **可插拔 backend**（stdlib / loguru），对 asyncio 优化（contextvars + 无锁 LogManager）。

## 公开入口（public API）

只有 `__init__.py` 导出的符号是公开的。外部代码一律从这里引用。

| 入口 | 用途 |
|---|---|
| `agent_logger` / `workflow_logger` / `llm_logger` / `tool_logger` / `memory_logger` / `team_logger` / ... | 模块级懒加载 logger。`import` 不触发初始化，首次访问方法时才绑定真实 logger |
| `LogManager.get_logger(log_type)` | 动态按名获取 logger；缺省时用当前 backend 的默认 logger 类构造 |
| `set_session_id(trace_id)` / `get_session_id()` | 基于 `contextvars` 的 trace 上下文，跨 `asyncio.Task` 隔离 |
| `set_member_id(member_id)` / `get_member_id()` | 多 Agent 场景的成员标识，同样用 contextvars |
| `create_log_event(event_type, **fields)` | 根据 `LogEventType` 选择事件 dataclass 构造；未定义字段会被过滤并警告 |
| `sanitize_event_for_logging(event, sensitive_fields=None)` | 事件序列化前清洗 query / messages / result 等敏感载荷 |
| `configure_log(config_path)` / `configure_log_config(dict)` | 替换全局 `log_config` 并调用 `LogManager.reset()`，让懒 logger 重新绑定 |
| `register_event_class(event_type_str, cls)` / `unregister_event_class(event_type_str)` | 运行时注册自定义事件类型；**key 必须是字符串**，禁止撞 `LogEventType` 枚举值 |

**新增一类日志用途时的决策**：

1. 只是多一个命名空间（想要独立 level / 文件） → 在 `__init__.py` 加一个 `LazyLogger` + 在默认配置里加 `loggers.<name>.level`。
2. 需要结构化字段（新 module 类型，如 retrieval / sys_operation） → 在 `events.py` 加新的 `@dataclass(BaseLogEvent)`，注册到 `EVENT_CLASS_MAP`，同步更新 `ModuleType` / `LogEventType`。
3. 需要一个全新的 backend（非 default / loguru） → 看下方《扩展 backend》。

禁止：把运行时开关放到 logger 实例上、在库代码里 `logging.getLogger(__name__)`（绕过了配置体系）、在非测试代码中调用 `LogManager.reset()`。

## 模块地图

```
logging/
├── __init__.py          # 公开 API：LazyLogger 聚合 + 事件/工具 re-export
├── protocol.py          # LoggerProtocol（runtime_checkable） —— backend 必须实现
├── manager.py           # LogManager：按 backend 选实现类并缓存实例（类级单例，无锁）
├── log_config.py        # LogConfig：从 yaml/dict 加载，按 backend 分发 normalize/build
├── log_levels.py        # level 常量 + normalize_log_level + extract_backend
├── config_manager.py    # 向后兼容 shim → log_levels.py 的 re-export
├── events.py            # BaseLogEvent + 子类 + LogEventType + 事件注册表
├── base_impl.py         # StructuredLoggerMixin / format_log_filename —— backend 共享辅助
├── utils.py             # contextvars（trace/member）+ 路径校验 + max_bytes 校验
├── default/             # stdlib logging backend
│   ├── default_impl.py  # DefaultLogger（SafeRotatingFileHandler + ContextFilter）
│   ├── config_provider.py
│   └── constant.py      # DEFAULT_INNER_LOG_CONFIG
└── loguru/              # loguru backend（可选依赖，import 失败则报 RuntimeError）
    ├── loguru_impl.py   # LoguruLogger（基于 extra / patch_record 的结构化）
    ├── config_provider.py
    └── constant.py
```

## 架构铁律

### 1. Card/Config 分层之外：LogConfig 是配置，LogManager 是运行时

`log_config`（全局 `LogConfig` 实例）持有**归一化后的配置快照**，是纯数据；`LogManager` 持有 **backend 类 + 实例缓存**，是运行时。任何配置变更都走 `configure_log*` → `LogManager.reset()`，**不要直接 mutate 实例属性**。

### 2. 异步安全是第一优先级

- 上下文：`set_session_id` / `set_member_id` 用 `contextvars.ContextVar`。禁止用 `threading.local()`——它在 `asyncio.Task` 间会泄漏。
- 锁：`LogManager` **没有 threading lock**（注释里明确说明面向 asyncio + GIL）。如果未来要在纯多线程环境下用，**另开并行实现**，不要回头加锁污染当前路径。
- 文件句柄：backend 内部的 rotation / sink 依赖各自库保证并发安全。`default_impl.SafeRotatingFileHandler` 继承 stdlib `RotatingFileHandler`；`loguru_impl` 用 `enqueue=True` 走进程内队列。

### 3. Backend 可插拔，但入口唯一

`LogConfig._normalize_loaded_config` 和 `LogConfig.get_logger_config` 按 `backend` 字段路由到 `_BACKEND_LOADERS` / `_BACKEND_LOGGER_BUILDERS`。`LogManager._get_logger_class_for_backend` 按 backend 名选 logger 类。

**扩展 backend 的标准步骤**：

1. 新建 `logging/<backend_name>/`，实现 `<Backend>Logger(LoggerProtocol)`。
2. 在该目录 `config_provider.py` 提供 `load_<backend>_backend_config` + `build_<backend>_logger_config` + `normalize_<backend>_logging_config`。
3. 在 `log_config.py` 的 `_BACKEND_LOADERS` / `_BACKEND_LOGGER_BUILDERS` 和 `log_levels.py` 的 `normalize_logging_config` 分支里注册。
4. 在 `manager.py._get_logger_class_for_backend` 中加入新分支。

禁止：在库代码里强 import 可选 backend（例如 loguru）。`LogManager` 延迟到真正使用时才 import，import 失败抛明确的 `RuntimeError`。

### 4. LazyLogger 的语义不可动摇

模块级 logger（`agent_logger` 等）是 `LazyLogger(lambda: LogManager.get_logger("agent"))`。两条约束：

- **import 零副作用**：`LazyLogger.__init__` 只登记 getter，**不调 `LogManager.initialize()`**。这是整个启动路径的性能约束。
- **配置变更后必须重绑**：`LogManager.reset()` 会回调 `reset_lazy_loggers()`，清掉所有 `LazyLogger._logger` 缓存。新增持久 logger 缓存路径时，必须同样接入 reset 回调（见 `reset_common_logger_cache` 的模式）。

### 5. 结构化事件 = "dataclass + 白名单字段"

`create_log_event` 用 `_get_event_field_names`（带缓存）过滤 `**kwargs`，未定义字段不报错但 `common_logger.warning` 记录。意味着：

- **加字段先加到 dataclass**。在 backend 或调用方 `extra={...}` 里硬塞字段会被静默丢弃。
- `EVENT_CLASS_MAP` 以 `LogEventType` 为 key；`_CUSTOM_EVENT_CLASS_MAP` 以 `str` 为 key；查找顺序是 **动态（str）→ 静态（enum）→ `BaseLogEvent`**。新增枚举值记得在 `EVENT_CLASS_MAP` 里指到对应子类，否则会退化成 `BaseLogEvent`。
- `BaseLogEvent.to_dict` 会自动把 `Enum` / `datetime` / `Exception` 序列化；**不要**在 backend 侧重复做这层转换。

### 6. 敏感字段清洗走统一清单

`sanitize_event_for_logging` 的默认名单覆盖 `messages / response_content / input_content / query / arguments / result / message_content / tool_calls / input_data / output_data / retrieved_memories`。新增事件字段若含原文载荷，**必须**要么命名沿用清单里的 key，要么显式传 `sensitive_fields=[...]` 扩展清单——不要在 backend 私下再写一份。

### 7. 路径校验是安全边界

日志相关的所有路径（`LogConfig._load_config` / `DefaultLogger` 创建文件）都走 `normalize_and_validate_log_path` → `is_sensitive_path`。新增 backend 的 file sink 配置同样走这条。**禁止**在 backend 里裸用 `open(path)` / `os.makedirs(path)`。

## 与项目其它模块的边界

- **不要**在库代码里用 `print()` 或 `logging.getLogger(__name__)`。入口只有本模块的 `*_logger`。
- 事件的 `trace_id` 字段由 `base_impl.StructuredLoggerMixin._build_structured_event_dict` 自动从 `get_session_id()` 注入，调用方不需要手动传；想覆盖时才显式传 `trace_id=...`。
- `LogConfig.get_backend()` 是 backend 名的单一来源。外部模块想知道"当前是哪个 backend"必须查它，不要自己读 env / yaml。

## 测试

- 路径镜像：`openjiuwen/core/common/logging/events.py` → `tests/unit_tests/core/common/logging/test_events.py`。
- 测试隔离：每个涉及 LogManager 状态的用例在 setup/teardown 里调 `LogManager.reset()`，否则会串污到下一个用例。
- `pytest` 纯函数风格；测试里打印用 `test_logger`，断言用 `caplog` 或直接捕获 `LogManager.get_logger("common")` 的输出。
- backend 兼容测试：新增 backend 必须同时跑 `tests/unit_tests/core/common/logging/` 下的 protocol 通用断言（`LoggerProtocol` 的 `runtime_checkable` 能抓到结构性缺失）。
