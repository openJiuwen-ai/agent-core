"""修复验证：与 README 一致，可直接运行（应在 <1s 返回）。"""
import asyncio
import logging
import time


async def hung():
    try:
        await asyncio.sleep(3600)
    finally:
        await asyncio.sleep(3600)


async def _wait_for_no_await(coro, timeout):
    task = asyncio.ensure_future(coro)
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        return task.result()
    task.cancel()
    raise asyncio.TimeoutError()


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

t0 = time.time()
try:
    asyncio.run(_wait_for_no_await(hung(), timeout=0.2))
except asyncio.TimeoutError:
    logger.info("✓ 超时 %.2fs 返回（旧 asyncio.wait_for 会挂起）", time.time() - t0)
