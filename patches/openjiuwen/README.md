# a11 · LLM 流式响应挂起修复（openjiuwen agent-core）

> 目标仓：`https://gitcode.com/openJiuwen/agent-core.git`（develop @ b28b4f3）
> 本目录为修复的 patch + 说明，供 agent-core 仓可用时应用（当前 GitCode 网络不可达）。
> 实测证据链见 docs/03.5-framework-mechanisms.md §M3。

## 根因

`openjiuwen/core/foundation/llm/model.py:197`：

```python
chunk = await asyncio.wait_for(stream_iterator.__anext__(), timeout=next_timeout)
```

`asyncio.wait_for` 超时后 `task.cancel()` **并 `await task`**。而 openai 的
`AsyncStream.__stream__` 的 `finally: await response.aclose()`（openai/_streaming.py:218-220）
在取消后仍执行——若 httpcore 的 aclose 卡在僵死 TCP 连接上（DeepSeek 流僵死时），
**任务永不终止，wait_for 永不返回，TimeoutError 永不抛**。实测：LLM 调用挂起
16 分钟无任何日志，agentserver 0.3% CPU 空转。

## 修复

新增 `_wait_for_no_await`：超时后 `task.cancel()` 但**不 await 该任务**
（让它在后台慢慢收尾，事件循环不受阻塞——已用隔离测试验证）。

```python
async def _wait_for_no_await(coro, timeout):
    """wait 但超时后不 await 被取消的任务（替代 asyncio.wait_for）。

    关键：不能用 asyncio.wait_for——它超时后 await 被卡任务，finally
    的 sleep 吞取消 → 永不返回。改用 asyncio.wait({task}, timeout)，
    超时后手动 cancel 且不 await，立即抛超时；任务后台收尾，循环不阻塞。
    """
    task = asyncio.ensure_future(coro)
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        return task.result()
    task.cancel()  # 不 await——让任务后台收尾，不阻塞本协程
    raise asyncio.TimeoutError()
```

`model.py:197` 的调用改为 `_wait_for_no_await(...)`。

## 验证

隔离测试（已验证）：
1. **复现**：`hung_anext`（finally 卡死）经 `asyncio.wait_for(timeout=0.2)` →
   挂起 120s+ 不返回（生产环境的挂起现象）。
2. **修复**：同一 coro 经 `_wait_for_no_await` → 0.2s 返回（超时立即抛），
   事件循环后续操作正常、asyncio.run 正常退出。

## 应用方式（agent-core 仓可用时）

```bash
git clone https://gitcode.com/openJiuwen/agent-core.git
cd agent-core
git checkout -b fix/llm-stream-timeout-hang b28b4f3
git apply /path/to/0001-fix-llm-stream-timeout.patch
# 补测试 + 提交 + 提 MR
```

## 注意

- 本修复解决"挂起被超时兜住"（止损），不解决"DeepSeek 流僵死"本身（外部）。
- 配套应用层：watchdog 停滞检测（scripts/watchdog.py `check_stalled_probes`）
  作为双保险，5min 无输出即熔断。
- 传输层 httpx 0.28 移除 total timeout 是次要因素（`Timeout(1800)` 按单次
  read 计时），本修复的应用层超时（first_chunk/idle）已能兜住。
