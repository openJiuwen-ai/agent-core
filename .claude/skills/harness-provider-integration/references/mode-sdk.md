# SDK 模式：厂商 Python SDK 驱动其运行时

接入优先级最高：进程、帧格式、请求配对与类型都由厂商维护，provider 代码最少。没有 SDK、SDK 缺关键能力
或宿主不能接受这个依赖时，依次退到原生协议、一次性模式、ACP（见 SKILL.md 第 2 节）。

适用：厂商提供 Python SDK（多数在内部拉起自己的 CLI 子进程），能拿到结构化流、中断、
会话恢复等 API。仓库先例：`claudecode/`（claude-agent-sdk）、`codex/`（openai-codex）、
`dsh/`（deepseek-harness，同步 SDK + 回调）。

## 模板做了什么

- `load_sdk()` 在 `_open_session` 内 lazy import；缺 SDK 抛 `HarnessError`，**在启动任何东西之前**。
- 所有 SDK 调用集中在 `_sdk_connect / _sdk_new_session / _sdk_resume_session / _sdk_run /
  _sdk_cancel / _sdk_close`，按一个**假定的异步 SDK 形状**写成：

  ```python
  client = sdk.Client(cwd=..., env=..., model=..., api_base=..., api_key=...)
  await client.start()
  session = await client.new_session(system_prompt=..., system_prompt_mode=..., mcp_servers=[...])
  async for record in session.run(text): ...      # 每个 record 是参考形状的 dict
  await session.cancel()
  await client.close()                             # 必须让正在跑的 run() 结束
  ```

- `_execute_turn` 用 `turn_idle_timeout_s` 包 `anext()`，超时先取消再以 `network_timeout` 失败。
- 启动异常经 `classify_exception` 包成 `ProviderStartupError`。

## 厂商化：重写 `_sdk_*`，保持其余不动

1. 读 SDK 源码确定真实 API，逐个重写 `_sdk_*`。
2. `consume()` 吃的是 `to_json_object(record)`；SDK 产出 pydantic / dataclass 对象时这一步已转成
   dict，映射按 dict 写即可。
3. 重写测试里的假 SDK，让它实现**真实** API 的形状、产出**真实**记录的形状。
4. 关闭 `SCAFFOLD(sdk-calls)`、`SCAFFOLD(config)`、`SCAFFOLD(mcp-config)`、`SCAFFOLD(mapping)`、
   `SCAFFOLD(turn-end)`、`SCAFFOLD(failure-*)`、`SCAFFOLD(mcp-naming)`、`SCAFFOLD(capabilities)`、
   `SCAFFOLD(fixtures)`。

## 同步 SDK + 回调（DSH 形态）

SDK 方法阻塞、通知在 SDK 工作线程回调时，不要每次回调起一个 `asyncio.to_thread`，也不要
fire-and-forget。用 DSH 的线程桥：**保序且把有界缓冲区的背压传回 SDK 线程**。

```python
loop = asyncio.get_running_loop()

def on_notification(notification: object) -> None:
    future = asyncio.run_coroutine_threadsafe(self._handle(turn, accumulator, notification), loop)
    future.result()          # 阻塞 SDK 线程直到事件入缓冲区：顺序与背压都靠这一行

run_result = await asyncio.to_thread(session.run, text, on_notification=on_notification)
```

关闭时 `await asyncio.to_thread(sdk_harness.close)`；`stop()` 期间唯一的持续消费者必须读到 EOF，
否则 BLOCK 背压会让关闭卡住（DSH 不变量 4）。

## 服务端请求（审批 / 提问 / 动态工具）

SDK 把 CLI 发来的请求交给一个回调（Codex：底层 client 的 `_approval_handler(method, params)`，
运行在 SDK reader 线程）。模式：

```python
def _approval_handler(self, method: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
    if method == USER_INPUT_METHOD:
        return self._handle_user_input_request(params or {})
    future = asyncio.run_coroutine_threadsafe(self._route_approval(method, params or {}), self._loop)
    return future.result(timeout=_APPROVAL_WAIT_TIMEOUT_S)
```

- 没有生成类型的请求照样能处理：按 method 名与原始 params 解析（Codex `item/tool/requestUserInput`）。
- 等人类回答不设总超时，按片轮询并在事件循环关闭时放弃（Codex `_USER_INPUT_WAIT_SLICE_S`）。
- 宿主没声明对应能力时：不装回调或安全拒绝，绝不默认批准危险操作，也不让请求悬挂。

## 先例里值得照抄的做法

- **steer 竞态**：外部 STARTED 已发而 SDK 的 turn handle 还没返回时到达的 steer 先排队，handle
  就绪后立即投递（Codex `_pending_steers`）。
- **Turn 边界以送达回执为准**：一次 Turn 内被厂商拆成多个周期时，等所有提交的消息都被应答
  （Claude `lifecycle.py`）。
- **认证 fallback 先征得宿主同意**：`_confirm_provider_extension("auth_fallback", ...)`，被拒就回到
  原端点并以原 `auth_required` 失败。
- **模型切换跨重连保持**：`_apply_model_selection` 同时改 active 与 primary 模型。
- **SDK 自带 CLI 可能过旧**：配置项允许指定 CLI 路径（`cli_path` / `codex_bin`），e2e 用 PATH 上的。
