# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
import inspect
import threading
from typing import Any, Callable, Dict, NamedTuple, Optional, List, Union

from openjiuwen.core.common.logging import graph_logger, LogEventType
from openjiuwen.core.graph.pregel.base import Message, PregelNode, GraphInterrupt
from openjiuwen.core.graph.pregel.config import PregelConfig, InnerPregelConfig, create_inner_config
from openjiuwen.core.graph.pregel.constants import TASK_STATUS_INTERRUPT, TASK_STATUS_ERROR, PARENT_NS, NS
from openjiuwen.core.runner.callback import get_callback_framework
from openjiuwen.core.runner.callback.events import WorkflowEvents


_SIGNATURE_CACHE_LIMIT = 1024
_signature_cache: dict[int, tuple[Callable[..., Any], inspect.Signature]] = {}
_signature_cache_lock = threading.Lock()

# 反射特征缓存：签名成员（config/state）与协程性对同一 callable 恒定。键取
# "反射解析单元"而非实例本身——可调用实例（Vertex 壳、组件对象）每次执行
# 新建，按实例 id 键控会漂移导致缓存永不命中；inspect.signature(instance) /
# iscoroutinefunction(instance) 实际解析的是 type(instance).__call__，属类级
# 常量，故实例落到其类、绑定方法落到 __func__，跨编译/执行全命中。
_TRAITS_CACHE_LIMIT = 1024
_traits_cache: dict[int, tuple[Any, CallableTraits]] = {}
_traits_cache_lock = threading.Lock()


class CallableTraits(NamedTuple):
    """Pre-resolved reflection traits of a node callable."""

    has_config: bool
    has_state: bool
    is_coroutine: bool


def _reflection_key(func: Callable[..., Any]) -> Callable[..., Any]:
    """Map a callable onto the stable object its reflection actually resolves to."""
    if isinstance(getattr(func, "__signature__", None), inspect.Signature):
        return func
    if inspect.isclass(func):
        # A class callable resolves through its own __init__/__call__ — the
        # class object itself is the stable key (its metaclass is shared).
        return func
    if inspect.ismethod(func):
        return func.__func__
    if not inspect.isfunction(func) and not inspect.isbuiltin(func) and not inspect.isroutine(func):
        return type(func)
    return func


def _get_callable_signature(func: Callable[..., Any]) -> inspect.Signature:
    """Return a callable's signature, avoiding repeated reflection."""
    explicit_signature = getattr(func, "__signature__", None)
    if isinstance(explicit_signature, inspect.Signature):
        return explicit_signature

    cache_key = id(func)
    cached = _signature_cache.get(cache_key)
    if cached is not None and cached[0] is func:
        return cached[1]

    signature = inspect.signature(func)
    with _signature_cache_lock:
        cached = _signature_cache.get(cache_key)
        if cached is not None and cached[0] is func:
            return cached[1]

        if len(_signature_cache) >= _SIGNATURE_CACHE_LIMIT:
            _signature_cache.pop(next(iter(_signature_cache)))
        _signature_cache[cache_key] = (func, signature)
    return signature


def _callable_traits(func: Callable[..., Any]) -> CallableTraits:
    """Resolve (has_config, has_state, is_coroutine) once per reflection key.

    Callers only test signature *membership*, never the Signature object
    itself, so traits — not signatures — are what the execution path needs.
    """
    key = _reflection_key(func)
    cache_key = id(key)
    cached = _traits_cache.get(cache_key)
    if cached is not None and cached[0] is key:
        return cached[1]

    sig = _get_callable_signature(func)
    traits = CallableTraits(
        has_config="config" in sig.parameters,
        has_state="state" in sig.parameters,
        is_coroutine=inspect.iscoroutinefunction(func) or inspect.iscoroutinefunction(getattr(func, "__call__", None)),
    )
    with _traits_cache_lock:
        cached = _traits_cache.get(cache_key)
        if cached is not None and cached[0] is key:
            return cached[1]

        if len(_traits_cache) >= _TRAITS_CACHE_LIMIT:
            _traits_cache.pop(next(iter(_traits_cache)))
        _traits_cache[cache_key] = (key, traits)
    return traits


class TaskExecutorPool:
    def __init__(self, config: PregelConfig):
        self.config = config
        self.succeed_messages: List[Message] = []
        from openjiuwen.core.graph.store.base import PendingNode

        self.failed: Dict[str, PendingNode] = {}
        self.running_tasks: Dict[asyncio.Task, PregelNode] = {}

    def submit(self, node: PregelNode, version: int) -> None:
        """Submit node's execution task"""
        async_task = asyncio.create_task(NodeTask(node, self.config, version).run())
        self.running_tasks[async_task] = node

    def _commit_failure(self, node: PregelNode, exc: Exception):
        """Record failed node and exception"""
        name = node.name
        if name not in self.failed:
            status = TASK_STATUS_INTERRUPT if isinstance(exc, GraphInterrupt) else TASK_STATUS_ERROR
            from openjiuwen.core.graph.store.base import PendingNode

            self.failed[name] = PendingNode(node_name=name, status=status, exception=[exc])

    async def wait_all(self) -> None:
        """Wait and process all tasks with FIRST_EXCEPTION semantics"""
        if not self.running_tasks:
            return

        tasks = list(self.running_tasks.keys())

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

        first_err_exc: Optional[Exception] = None
        interrupt_exc: Optional[GraphInterrupt] = None

        for t in done:
            node = self.running_tasks.pop(t)

            exc = t.exception()

            if exc:
                self._commit_failure(node, exc)
                if first_err_exc is None:
                    first_err_exc = exc
            else:
                res = t.result()
                if isinstance(res, GraphInterrupt):
                    self._commit_failure(node, res)
                    if interrupt_exc is None:
                        interrupt_exc = res
                else:
                    msgs: List[Message] = res
                    self.succeed_messages.extend(msgs)
        tasks_to_cancel = list(pending)
        for t in tasks_to_cancel:
            t.cancel()
        results = await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                graph_logger.warning(
                    "Task cancelled with exception",
                    event_type=LogEventType.GRAPH_VERTEX_CALL_ERROR,
                    metadata={"error": str(result)},
                )
        for t in tasks_to_cancel:
            if t in self.running_tasks:
                node = self.running_tasks.pop(t)
                self._commit_failure(node, asyncio.CancelledError())

        # Priority: normal exception > interrupt exception
        if first_err_exc:
            raise first_err_exc
        elif interrupt_exc:
            raise interrupt_exc

    def clear(self):
        self.succeed_messages.clear()
        self.failed.clear()
        self.running_tasks.clear()

    async def cancel_all(self):
        to_cancel = []
        for t in list(self.running_tasks.keys()):
            if not t.done() and not t.cancelled():
                t.cancel()
                to_cancel.append(t)
        results = await asyncio.gather(*to_cancel, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                graph_logger.warning(
                    "running task with exception",
                    event_type=LogEventType.GRAPH_VERTEX_CALL_ERROR,
                    metadata={"error": str(result)},
                )
        for t in to_cancel:
            if t in self.running_tasks:
                node = self.running_tasks.pop(t)
                self._commit_failure(node, asyncio.CancelledError())
        self.running_tasks.clear()


class NodeTask:
    def __init__(self, node: PregelNode, config: PregelConfig, version: int):
        self.node = node
        self.config = config
        self.messages: List[Message] = []
        self.version = version

    async def run(self) -> Union[List[Message], GraphInterrupt]:
        """
        Execute the node.
        Returns:
            List[Message]: If success.
            GraphInterrupt: If interrupted (caught internally).
        Raises:
            Exception: If any other error occurs (propagates to cancel others).
        """
        try:
            func = self.node.func
            target_func = func.__call__ if hasattr(func, "__call__") else func
            # Reflection traits (signature members / coroutine-ness) are class-
            # level constants: pre-resolved once per callable, no per-run cost.
            traits = _callable_traits(func)
            kwargs = {}
            if traits.has_config:
                inner_config: InnerPregelConfig = create_inner_config(self.config)
                current_parent_ns = inner_config.get(PARENT_NS)
                current_node_name = self.node.name
                if current_parent_ns:
                    new_full_ns = f"{current_parent_ns}:{current_node_name}:{self.version}"
                    inner_config[NS] = new_full_ns
                    inner_config[PARENT_NS] = new_full_ns
                kwargs["config"] = inner_config
            if traits.has_state:
                kwargs["state"] = None

            if traits.is_coroutine:
                await target_func(**kwargs)
            else:
                target_func(**kwargs)

            # Route messages
            self.messages = []
            for r in self.node.routers:
                msgs = await r.dispatch(source_node=self.node.name)
                self.messages.extend(msgs)

            if self.messages:
                # Event name is fixed here: resolve the subscriber check once
                # per task instead of once per traversed edge.
                fw = get_callback_framework()
                if fw.has_subscribers(WorkflowEvents.EDGE_TRAVERSED):
                    for msg in self.messages:
                        await fw.trigger(
                            WorkflowEvents.EDGE_TRAVERSED,
                            source_node=msg.sender,
                            target_node=msg.target,
                            graph_id=self.config.get(NS),
                        )

            return self.messages

        except GraphInterrupt as e:
            # cast exception to return value
            return e
