# S_17 Personal Context

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/personal_context/`（17 文件） |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 harness 的 PersonalContext 子系统：设备授权、上下文图、获取服务、
运行时激活。`personal_context/` 17 文件是嵌入式的个人上下文核心（从
`openjiuwen/core` 相关能力独立而来），DeepAgent 经 rail / prompt 消费。

具体覆盖：

- `personal_context/personal_context.py`：`PersonalContext` 门面（
  `set_configuration` / `authorize_provider` / `get_authorization_status` /
  `get_graph` / `search_graph` / `get_graph_page` / `activate_runtime` /
  `start_fetch_service` / `run_fetch` / `stop_fetch_service` / `snapshot` /
  `deactivate_runtime`）。
- `personal_context/fetch/`：`base.py` / `browser_bookmarks.py` / `feishu.py` /
  `github.py` / `local_files.py` / `toutiao_reader.py` / `zhihu_reader.py` /
  `__init__.py`（provider 实现）。
- `personal_context/config.py`：`PersonalContextConfig` / `PersonalContextFetchServiceConfig`。
- `personal_context/context_pipeline.py` / `context_graph.py` / `agent_support.py`：
  pipeline / 图 / agent 支持。
- `personal_context/models.py`：`PersonalContextStatus` / `RawChangeItem` /
  `FetchBatch` 等。
- `personal_context/source_metadata.py` / `status_codes.py`。
- `personal_context/__init__.py`：`__all__ = ["PersonalContext"]`。

不在本规约范围内：
- 授权协议细节（device code / OAuth）—— 实现。
- 图数据结构（core 侧 `ContextEngine`）—— core 规约。
- rail / prompt 消费（`S_04` / `S_06`）。

## 不变量

1. **公开面唯一**:`personal_context/__init__.py` 只导出 `PersonalContext`；一切
   配置 / 状态 / 图访问都经这个门面。
2. **配置唯一入口**:`set_configuration(config: PersonalContextConfig)` 幂等设置；
   `PersonalContextConfig` 字段：`enabled` / `fetching_enabled` / `strategy_profile`
   （`"rules" | "balanced" | "agent"`）/ `model_client` / `model_request` /
   `fetch_services: tuple[PersonalContextFetchServiceConfig, ...]`。
3. **获取服务声明**:`PersonalContextFetchServiceConfig`：`service_id` / `provider`
   （枚举）/ `enabled` / `interval_seconds`（默认 10800，`gt=0, le=31_536_000`）/
   `max_items_per_run`（默认 None，`ge=1, le=10_000`）/ `source` / `credentials`
   （`repr=False` 防泄露）。新增 provider = 扩展 `_create_fetch_provider`。
4. **授权状态可查**:`get_authorization_status(provider) -> dict`；
   `authorize_provider(provider)` 发起 device-code 授权并
   `_finish_authorization(device_code, timeout_seconds)` 轮询；授权失败走
   `_cancel_authorization`（`clear_error`）；`_required_authorization_scopes`
   是 scope 需求唯一声明点。
5. **运行时要先激活**:`activate_runtime()`（`_activate_runtime_impl`）启动 pipeline；
   失败 `_cancel_runtime_after_activation_failure` 清理；`deactivate_runtime(timeout_seconds=30)`
   停止；`snapshot() -> PersonalContextStatus` 报告全量状态。
6. **取数服务受时钟驱动**:`start_fetch_service(service_id)` /
   `stop_fetch_service(service_id, timeout_seconds=30)`；`_run_fetch_service` 按
   `interval_seconds` 循环；`run_fetch(...)` 手动跑一次。结果经 `RawChangeItem` /
   `FetchBatch` 进 pipeline。
7. **安全面**:`_assert_no_symlink_chain(path)` 拒绝符号链接链（context 根必须是真实目录）；
   `_redact_text(value, limit=512)` 清洗日志/错误文本；`_source_fingerprint(config)` 源指纹；
   凭证 `repr=False`。
8. **状态模型**:`PersonalContextStatus`（`pydantic`）：`configured` / `enabled` /
   `fetching_enabled` / `state` / `pipeline_running` / `pipeline_queue_size` /
   `fetch_service_states` / `fetch_service_errors` / `context_root`（`min_length=1`）/
   `context_ready` / `last_error`。

## 接口契约

```python
class PersonalContext:
    def __init__(self, *, home: str | Path) -> None
    async def set_configuration(self, config: PersonalContextConfig) -> None
    async def get_authorization_status(self, provider: str) -> dict[str, object]
    async def authorize_provider(self, provider: str) -> dict[str, object]
    async def get_graph(self) -> dict[str, object]
    async def search_graph(self, query: str) -> dict[str, object]
    async def get_graph_page(self, node_id: str) -> dict[str, object]
    async def activate_runtime(self) -> None
    async def start_fetch_service(self, service_id: str) -> None
    async def run_fetch(self, ...) -> None
    async def stop_fetch_service(self, service_id: str, *, timeout_seconds: float = 30.0) -> None
    async def snapshot(self) -> PersonalContextStatus
    async def deactivate_runtime(self, *, timeout_seconds: float = 30.0) -> None

class PersonalContextConfig(BaseModel):
    enabled: bool
    fetching_enabled: bool
    strategy_profile: Literal["rules", "balanced", "agent"]
    model_client: ModelClientConfig | None = Field(default=None, repr=False)
    model_request: ModelRequestConfig | None = None
    fetch_services: tuple[PersonalContextFetchServiceConfig, ...]
```

错误 / 返回语义：

- 授权失败 / 设备码超时 → `_error(status: StatusCode, ...)` 建 `BaseError`（`status_codes.py`
  定义码）。
- 未配置即操作 → `_state_error`；fetch 失败 → `_fetch_error`；文件问题 → `_file_error`。
- context 根含 symlink 链 → `_assert_no_symlink_chain` 拒绝（抛）。

## 数据结构

### PersonalContextStatus 生命周期

| 字段 | 写入方 | 语义 |
|---|---|---|
| `configured` / `enabled` | `set_configuration` | 配置态 |
| `state` / `pipeline_running` / `pipeline_queue_size` | `activate_runtime` | 运行态 |
| `fetch_service_states` / `fetch_service_errors` | fetch 服务循环 | 每服务状态与错误 |
| `context_root` / `context_ready` | 激活后 | 图根与就绪标志 |
| `last_error` | 各失败路径 | 最近错误（可 None） |

### 获取服务循环

`start_fetch_service → _run_fetch_service（interval 时钟）→ provider._collect →
RawChangeItem/FetchBatch → pipeline → context graph → search_graph/get_graph_page`

## 与其它 spec 的关系

- 上下文图消费 `core/context_engine` 能力 —— core 规约（本 spec 只锚定门面契约）。
- PersonalContext rail / prompt 消费 —— `S_04` / `S_06`（`personal_context.py` rail 位于
  `rails/personal_context.py`）。
- 状态 / 错误码与 `StatusC 语义` 同族（`StatusCode` 体系归 core exception 规约）。
- 与 `agent_teams` / `dev_tools` 的个人上下文实现是不同宿主面，各自独立。
