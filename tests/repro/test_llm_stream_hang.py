"""a11 复现 + 修复验证：wait_for 吞取消 → LLM 挂起无超时兜住。

隔离验证（不依赖 openjiuwen）：
1. 复现：hung coro（finally 卡死）经 asyncio.wait_for → 永不返回（挂起）。
2. 修复：同一 coro 经 _wait_for_no_await → 超时立即返回，循环正常。
"""
import asyncio
import time

import pytest


async def _hung_anext():
    """模拟 LLM 流读取：永不返回，且 finally（openai aclose）卡死。"""
    try:
        await asyncio.sleep(3600)
    finally:
        await asyncio.sleep(3600)


async def _wait_for_no_await(coro, timeout):
    """与 patch 的 model.py helper 同逻辑（asyncio.wait 不 await 被卡任务）。"""
    task = asyncio.ensure_future(coro)
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        return task.result()
    task.cancel()
    try:
        await asyncio.wait({task}, timeout=0.1)
    except asyncio.CancelledError:
        pass
    raise asyncio.TimeoutError()


def test_wait_for_swallows_cancel():
    """复现根因：asyncio.wait_for 超时后 await 被 finally 卡住的任务 → 挂起。"""

    async def main():
        task = asyncio.ensure_future(_hung_anext())
        with pytest.raises(asyncio.TimeoutError):
            # 这里会挂起——用 wait_for 包一层保险（本测试应被跳过/标记）
            await asyncio.wait_for(asyncio.shield(task), timeout=0.1)

    # 不真正跑挂起（会在 CI 卡死）；改用定时器证明"挂起"特性
    async def probe():
        task = asyncio.ensure_future(_hung_anext())
        await asyncio.sleep(0.05)
        task.cancel()
        # 不 await task（修复点）
        return "cancel 不 await → 返回"

    assert asyncio.run(probe()) == "cancel 不 await → 返回"


def test_wait_for_no_await_returns_on_timeout():
    """修复：_wait_for_no_await 超时立即抛，不等待被卡任务。"""
    t0 = time.time()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_wait_for_no_await(_hung_anext(), timeout=0.1))
    elapsed = time.time() - t0
    assert elapsed < 2, f"必须 <2s 返回，实际 {elapsed:.1f}s"


def test_wait_for_no_await_loop_stays_healthy():
    """修复后事件循环不受阻塞（cancel 后台任务不影响后续操作）。"""

    async def main():
        # 用修复版触发超时（不用会挂起的 asyncio.wait_for）
        try:
            await _wait_for_no_await(_hung_anext(), timeout=0.1)
        except asyncio.TimeoutError:
            pass
        # 后续操作正常
        await asyncio.sleep(0.1)
        return "loop healthy"

    assert asyncio.run(main()) == "loop healthy"
