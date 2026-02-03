# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Comprehensive Async Callback Framework

Main framework class for production-ready async callback management.
"""

import asyncio
import inspect
import json
import logging
import time
from collections import (
    defaultdict,
    deque,
)
from datetime import datetime
from functools import wraps
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Set,
)

from openjiuwen.core.runner.callback.chain import CallbackChain
from openjiuwen.core.runner.callback.enums import (
    ChainAction,
    FilterAction,
    HookType,
)
from openjiuwen.core.runner.callback.filters import (
    CircuitBreakerFilter,
    EventFilter,
)
from openjiuwen.core.runner.callback.models import (
    CallbackInfo,
    CallbackMetrics,
    ChainContext,
    ChainResult,
    FilterResult,
)


class AsyncCallbackFramework:
    """Production-ready async callback framework.

    A comprehensive event-driven framework with support for:
    - Priority-based callback execution
    - Filtering and validation
    - Callback chains with rollback
    - Performance metrics
    - Lifecycle hooks
    - Circuit breakers
    - Rate limiting

    All operations are async-only and designed for asyncio environments.

    Attributes:
        enable_metrics: Whether to collect performance metrics
        enable_logging: Whether to enable logging
        logger: Logger instance
    """

    def __init__(
            self,
            enable_metrics: bool = True,
            enable_logging: bool = True
    ):
        """Initialize the callback framework.

        Args:
            enable_metrics: Enable performance metric collection
            enable_logging: Enable logging output
        """
        # Core storage
        self._callbacks: Dict[str, List[CallbackInfo]] = defaultdict(list)
        self._chains: Dict[str, CallbackChain] = {}

        # Filters
        self._filters: Dict[str, List[EventFilter]] = defaultdict(list)
        self._global_filters: List[EventFilter] = []
        self._callback_filters: Dict[Callable, List[EventFilter]] = {}

        # Hooks
        self._hooks: Dict[str, Dict[HookType, List[Callable]]] = defaultdict(
            lambda: defaultdict(list)
        )

        # Metrics
        self.enable_metrics = enable_metrics
        self._metrics: Dict[str, CallbackMetrics] = defaultdict(CallbackMetrics)

        # Logging
        self.enable_logging = enable_logging
        self.logger = logging.getLogger(__name__)
        if enable_logging:
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )

        # Circuit breakers
        self._circuit_breakers: Dict[str, CircuitBreakerFilter] = {}

        # Event history
        self._event_history: deque = deque(maxlen=1000)
        self._enable_history = False

        # Async locks
        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @property
    def callbacks(self):
        return self._callbacks

    @property
    def chains(self):
        return self._chains

    @property
    def circuit_breakers(self):
        return self._circuit_breakers

    @property
    def callback_filters(self):
        return self._callback_filters

    # ========== Registration ==========

    def on(
            self,
            event: str,
            *,
            priority: int = 0,
            once: bool = False,
            namespace: str = "default",
            tags: Optional[Set[str]] = None,
            filters: Optional[List[EventFilter]] = None,
            rollback_handler: Optional[Callable] = None,
            error_handler: Optional[Callable] = None,
            max_retries: int = 0,
            retry_delay: float = 0.0,
            timeout: Optional[float] = None
    ):
        """Decorator to register an async callback.

        Example:
            >>> framework = AsyncCallbackFramework()
            >>> @framework.on("user_login", priority=10)
            >>> async def handle_login(username: str):
            >>>     print(f"User logged in: {username}")

        Args:
            event: Event name to listen for
            priority: Execution priority (higher first)
            once: Execute only once then disable
            namespace: Namespace for grouping
            tags: Set of tags for filtering
            filters: List of filters to apply
            rollback_handler: Function to call on rollback
            error_handler: Function to call on error
            max_retries: Maximum retry attempts
            retry_delay: Delay between retries in seconds
            timeout: Execution timeout in seconds

        Returns:
            Decorator function
        """

        def decorator(func: Callable[..., Awaitable[Any]]) -> Callable:
            # 装饰器中使用同步注册方法
            self.register_sync(
                event, func,
                priority=priority,
                once=once,
                namespace=namespace,
                tags=tags,
                filters=filters,
                rollback_handler=rollback_handler,
                error_handler=error_handler,
                max_retries=max_retries,
                retry_delay=retry_delay,
                timeout=timeout
            )

            @wraps(func)
            async def wrapper(*args, **kwargs):
                return await func(*args, **kwargs)

            return wrapper

        return decorator

    def register_sync(
            self,
            event: str,
            callback: Callable[..., Awaitable[Any]],
            *,
            priority: int = 0,
            once: bool = False,
            namespace: str = "default",
            tags: Optional[Set[str]] = None,
            filters: Optional[List[EventFilter]] = None,
            rollback_handler: Optional[Callable] = None,
            error_handler: Optional[Callable] = None,
            max_retries: int = 0,
            retry_delay: float = 0.0,
            timeout: Optional[float] = None
    ) -> None:
        """Synchronous registration method for decorator use.

        This is an internal method used by the decorator to register callbacks
        synchronously during module loading.
        """
        callback_info = CallbackInfo(
            callback=callback,
            priority=priority,
            once=once,
            namespace=namespace,
            tags=tags or set(),
            max_retries=max_retries,
            retry_delay=retry_delay,
            timeout=timeout
        )

        self._callbacks[event].append(callback_info)
        self._callbacks[event].sort(key=lambda x: x.priority, reverse=True)

        if filters:
            self._callback_filters[callback] = filters

        # Add to chain if rollback/error handlers provided
        if rollback_handler or error_handler:
            if event not in self._chains:
                self._chains[event] = CallbackChain(event)
            self._chains[event].add(
                callback_info,
                rollback_handler,
                error_handler
            )

        if self.enable_logging:
            self.logger.info(f"Registered callback: {event} -> {callback.__name__}")

    def trigger_on_call(
            self,
            event: str,
            pass_result: bool = False,
            pass_args: bool = True
    ):
        """Decorator to automatically trigger event when function is called.

        Triggers the event before executing the decorated function.

        Example:
            >>> @framework.trigger_on_call("processing_started")
            >>> async def process_data(data):
            >>>     return {"processed": data}

        Args:
            event: Event name to trigger
            pass_result: Whether to pass function result to callbacks
            pass_args: Whether to pass function arguments to callbacks

        Returns:
            Decorator function
        """

        def decorator(func: Callable[..., Awaitable[Any]]) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # Trigger event before function execution
                if pass_args:
                    await self.trigger(event, *args, **kwargs)
                else:
                    await self.trigger(event)

                # Execute the original function
                result = await func(*args, **kwargs)

                # Optionally trigger with result
                if pass_result:
                    await self.trigger(event, result=result)

                return result

            return wrapper

        return decorator

    def emits(
            self,
            event: str,
            result_key: str = "result",
            include_args: bool = False
    ):
        """Decorator to trigger event with function result after execution.

        Triggers the event after the function completes, passing the result.

        Example:
            >>> @framework.emits("data_processed")
            >>> async def process_data(data):
            >>>     return {"processed": data}
            >>>
            >>> # Callbacks receive: result={"processed": data}

        Args:
            event: Event name to trigger
            result_key: Keyword argument name for the result
            include_args: Whether to include original args in event

        Returns:
            Decorator function
        """

        def decorator(func: Callable[..., Awaitable[Any]]) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # Execute the original function
                result = await func(*args, **kwargs)

                # Trigger event with result
                event_kwargs = {result_key: result}
                if include_args:
                    event_kwargs.update(kwargs)
                    await self.trigger(event, *args, **event_kwargs)
                else:
                    await self.trigger(event, **event_kwargs)

                return result

            return wrapper

        return decorator

    def emits_stream(
            self,
            event: str,
            item_key: str = "item"
    ) -> Callable:
        """Decorator to emit events for each item yielded by async generator.

        When decorating an async generator function, this will automatically
        trigger an event for each item that is yielded. The original items
        are still yielded to the caller.

        Example:
            >>> @framework.on("chunk_ready")
            >>> async def save_chunk(item: dict):
            >>>     print(f"Saving: {item}")
            >>>
            >>> @framework.emits_stream("chunk_ready")
            >>> async def process_file(filepath: str):
            >>>     with open(filepath, 'r') as f:
            >>>         for line in f:
            >>>             await asyncio.sleep(0.01)
            >>>             yield {"line": line.strip()}
            >>>
            >>> async for chunk in process_file("data.txt"):
            >>>     pass  # chunk_ready event triggered for each chunk

        Args:
            event: Event name to trigger for each yielded item
            item_key: Keyword argument name for the yielded item (default: "item")

        Returns:
            Decorated async generator function
        """

        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # Call the original async generator function
                async_gen = func(*args, **kwargs)

                # Check if it's an async generator
                if not inspect.isasyncgen(async_gen):
                    raise TypeError(
                        f"emits_stream can only decorate async generator functions, "
                        f"got {type(async_gen)}"
                    )

                try:
                    # Iterate through the async generator
                    async for item in async_gen:
                        # Trigger event with the item
                        await self.trigger(event, **{item_key: item})

                        # Yield the item to caller
                        yield item

                except Exception as e:
                    if self.enable_logging:
                        self.logger.error(
                            f"Error in emits_stream for event '{event}': {e}"
                        )
                    raise

            return wrapper

        return decorator

    def emit_around(
            self,
            before_event: str,
            after_event: str,
            pass_args: bool = True,
            pass_result: bool = True,
            on_error_event: Optional[str] = None
    ):
        """Decorator to trigger events before and after function execution.

        Triggers before_event before execution and after_event after completion.
        Optionally triggers on_error_event if an exception occurs.

        Example:
            >>> @framework.emit_around("process_start", "process_end",
            >>>                        on_error_event="process_error")
            >>> async def process_data(data):
            >>>     return {"processed": data}

        Args:
            before_event: Event to trigger before execution
            after_event: Event to trigger after successful execution
            pass_args: Whether to pass function arguments to events
            pass_result: Whether to pass result to after_event
            on_error_event: Optional event to trigger on error

        Returns:
            Decorator function
        """

        def decorator(func: Callable[..., Awaitable[Any]]) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # Trigger before event
                if pass_args:
                    await self.trigger(before_event, *args, **kwargs)
                else:
                    await self.trigger(before_event)

                try:
                    # Execute the original function
                    result = await func(*args, **kwargs)

                    # Trigger after event with result
                    if pass_result:
                        after_kwargs = kwargs.copy()
                        after_kwargs['result'] = result
                        if pass_args:
                            await self.trigger(after_event, *args, **after_kwargs)
                        else:
                            await self.trigger(after_event, result=result)
                    else:
                        if pass_args:
                            await self.trigger(after_event, *args, **kwargs)
                        else:
                            await self.trigger(after_event)

                    return result

                except Exception as e:
                    # Trigger error event if specified
                    if on_error_event:
                        error_kwargs = kwargs.copy()
                        error_kwargs['error'] = e
                        if pass_args:
                            await self.trigger(on_error_event, *args, **error_kwargs)
                        else:
                            await self.trigger(on_error_event, error=e)

                    # Re-raise the exception
                    raise

            return wrapper

        return decorator

    async def register(
            self,
            event: str,
            callback: Callable[..., Awaitable[Any]],
            *,
            priority: int = 0,
            once: bool = False,
            namespace: str = "default",
            tags: Optional[Set[str]] = None,
            filters: Optional[List[EventFilter]] = None,
            rollback_handler: Optional[Callable] = None,
            error_handler: Optional[Callable] = None,
            max_retries: int = 0,
            retry_delay: float = 0.0,
            timeout: Optional[float] = None
    ) -> None:
        """Register a callback for an event.

        Args:
            event: Event name to listen for
            callback: Async callback function
            priority: Execution priority (higher first)
            once: Execute only once then disable
            namespace: Namespace for grouping
            tags: Set of tags for filtering
            filters: List of filters to apply
            rollback_handler: Function to call on rollback
            error_handler: Function to call on error
            max_retries: Maximum retry attempts
            retry_delay: Delay between retries in seconds
            timeout: Execution timeout in seconds
        """
        async with self._locks["registration"]:
            callback_info = CallbackInfo(
                callback=callback,
                priority=priority,
                once=once,
                namespace=namespace,
                tags=tags or set(),
                max_retries=max_retries,
                retry_delay=retry_delay,
                timeout=timeout
            )

            self._callbacks[event].append(callback_info)
            self._callbacks[event].sort(key=lambda x: x.priority, reverse=True)

            if filters:
                self._callback_filters[callback] = filters

            # Add to chain if rollback/error handlers provided
            if rollback_handler or error_handler:
                if event not in self._chains:
                    self._chains[event] = CallbackChain(event)
                self._chains[event].add(
                    callback_info,
                    rollback_handler,
                    error_handler
                )

            if self.enable_logging:
                self.logger.info(f"Registered callback: {event} -> {callback.__name__}")

    async def unregister(self, event: str, callback: Callable) -> None:
        """Unregister a callback from an event.

        Args:
            event: Event name
            callback: Callback to remove
        """
        async with self._locks["registration"]:
            self._callbacks[event] = [
                ci for ci in self._callbacks[event] if ci.callback != callback
            ]
            self._callback_filters.pop(callback, None)

            if event in self._chains:
                self._chains[event].remove(callback)

            if self.enable_logging:
                self.logger.info(f"Unregistered callback: {event} -> {callback.__name__}")

    async def unregister_namespace(self, namespace: str) -> None:
        """Unregister all callbacks in a namespace.

        Args:
            namespace: Namespace to clear
        """
        async with self._locks["registration"]:
            for event in list(self._callbacks.keys()):
                self._callbacks[event] = [
                    ci for ci in self._callbacks[event]
                    if ci.namespace != namespace
                ]

    async def unregister_by_tags(self, tags: Set[str]) -> None:
        """Unregister callbacks matching any of the given tags.

        Args:
            tags: Set of tags to match
        """
        async with self._locks["registration"]:
            for event in list(self._callbacks.keys()):
                self._callbacks[event] = [
                    ci for ci in self._callbacks[event]
                    if not ci.tags.intersection(tags)
                ]

    # ========== Triggering ==========

    async def trigger(self, event: str, *args, **kwargs) -> List[Any]:
        """Trigger an event and execute all registered callbacks.

        Executes callbacks in priority order, applying filters and collecting
        metrics. Handles errors gracefully and executes lifecycle hooks.

        Example:
            >>> await framework.trigger("user_login", username="alice")

        Args:
            event: Event name to trigger
            *args: Positional arguments for callbacks
            **kwargs: Keyword arguments for callbacks

        Returns:
            List of results from all executed callbacks
        """
        results = []

        # Record history
        if self._enable_history:
            self._event_history.append({
                "event": event,
                "args": args,
                "kwargs": kwargs,
                "timestamp": time.time()
            })

        # Execute BEFORE hooks
        await self._execute_hooks(event, HookType.BEFORE, *args, **kwargs)

        callbacks = self._callbacks.get(event, [])

        for callback_info in callbacks:
            if not callback_info.enabled:
                continue

            callback = callback_info.callback

            try:
                # Apply filters
                filter_result = await self._apply_filters(
                    event, callback, args, kwargs
                )

                if filter_result.action == FilterAction.STOP:
                    if self.enable_logging:
                        self.logger.info(f"Filter stopped event processing: {event}")
                    break
                elif filter_result.action == FilterAction.SKIP:
                    if self.enable_logging:
                        self.logger.debug(
                            f"Filter skipped callback {callback.__name__}: "
                            f"{filter_result.reason}"
                        )
                    continue

                final_args = filter_result.modified_args or args
                final_kwargs = filter_result.modified_kwargs or kwargs

                # Execute callback with metrics
                start_time = time.time()

                # Call callback (might return coroutine or async generator)
                callback_result = callback(*final_args, **final_kwargs)

                # Check if it's an async generator
                if inspect.isasyncgen(callback_result):
                    # Don't await async generators, return them directly
                    result = callback_result
                else:
                    # It's a coroutine, await it
                    result = await callback_result

                execution_time = time.time() - start_time

                # Update metrics
                if self.enable_metrics:
                    key = f"{event}:{callback.__name__}"
                    self._metrics[key].update(execution_time, is_error=False)

                # Record circuit breaker success
                cb_key = f"{event}:{callback.__name__}"
                if cb_key in self._circuit_breakers:
                    await self._circuit_breakers[cb_key].record_success(
                        event, callback
                    )

                results.append(result)

                # Handle once-only callbacks
                if callback_info.once:
                    callback_info.enabled = False

            except Exception as e:
                execution_time = time.time() - start_time

                # Update metrics
                if self.enable_metrics:
                    key = f"{event}:{callback.__name__}"
                    self._metrics[key].update(execution_time, is_error=True)

                # Record circuit breaker failure
                cb_key = f"{event}:{callback.__name__}"
                if cb_key in self._circuit_breakers:
                    await self._circuit_breakers[cb_key].record_failure(
                        event, callback
                    )

                # Execute ERROR hooks
                await self._execute_hooks(event, HookType.ERROR, e, *args, **kwargs)

                if self.enable_logging:
                    self.logger.error(
                        f"Callback execution failed: {callback.__name__} - {e}",
                        exc_info=True
                    )

        # Execute AFTER hooks
        await self._execute_hooks(event, HookType.AFTER, results, *args, **kwargs)

        return results

    async def trigger_delayed(
            self,
            event: str,
            delay: float,
            *args,
            **kwargs
    ) -> List[Any]:
        """Trigger an event after a delay.

        Args:
            event: Event name
            delay: Delay in seconds
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            List of callback results
        """
        await asyncio.sleep(delay)
        return await self.trigger(event, *args, **kwargs)

    async def trigger_chain(self, event: str, *args, **kwargs) -> ChainResult:
        """Trigger callbacks as a chain with data flow.

        Executes callbacks sequentially, passing each result to the next.
        Supports rollback on failure.

        Example:
            >>> result = await framework.trigger_chain(
            >>>     "order_process",
            >>>     order_id="123"
            >>> )
            >>> if result.action == ChainAction.ROLLBACK:
            >>>     print("Order processing failed and was rolled back")

        Args:
            event: Event name
            *args: Initial positional arguments
            **kwargs: Initial keyword arguments

        Returns:
            ChainResult with execution outcome
        """
        if event not in self._chains:
            chain = CallbackChain(event)
            for callback_info in self._callbacks.get(event, []):
                chain.add(callback_info)
        else:
            chain = self._chains[event]

        context = ChainContext(
            event=event,
            initial_args=args,
            initial_kwargs=kwargs
        )

        return await chain.execute(context)

    async def trigger_parallel(self, event: str, *args, **kwargs) -> List[Any]:
        """Trigger callbacks in parallel (concurrent execution).

        All callbacks execute concurrently using asyncio.gather.
        Significantly faster than sequential trigger when callbacks
        involve I/O operations.

        Example:
            >>> # All three callbacks run concurrently
            >>> results = await framework.trigger_parallel(
            >>>     "fetch_data",
            >>>     url="https://api.example.com"
            >>> )

        Args:
            event: Event name to trigger
            *args: Positional arguments for callbacks
            **kwargs: Keyword arguments for callbacks

        Returns:
            List of results from all callbacks (excluding failed ones)
        """
        callbacks = self._callbacks.get(event, [])
        if not callbacks:
            return []

        tasks = []

        for callback_info in callbacks:
            if not callback_info.enabled:
                continue

            callback = callback_info.callback

            # Create task for each callback
            async def execute_callback(cb_info=callback_info, cb=callback):
                try:
                    # Apply filters
                    filter_result = await self._apply_filters(
                        event, cb, args, kwargs
                    )

                    if filter_result.action == FilterAction.STOP:
                        if self.enable_logging:
                            self.logger.info(f"Filter stopped processing for {event}")
                        return None
                    elif filter_result.action == FilterAction.SKIP:
                        if self.enable_logging:
                            self.logger.debug(
                                f"Filter skipped {cb.__name__}: "
                                f"{filter_result.reason}"
                            )
                        return None

                    final_args = filter_result.modified_args or args
                    final_kwargs = filter_result.modified_kwargs or kwargs

                    # Execute with timeout if specified
                    if cb_info.timeout:
                        result = await asyncio.wait_for(
                            cb(*final_args, **final_kwargs),
                            timeout=cb_info.timeout
                        )
                    else:
                        result = await cb(*final_args, **final_kwargs)

                    # Handle once-only callbacks
                    if cb_info.once:
                        cb_info.enabled = False

                    return result

                except Exception as e:
                    if self.enable_logging:
                        self.logger.error(
                            f"Callback {cb.__name__} failed in parallel execution: {e}"
                        )
                    return None

            tasks.append(execute_callback())

        # Execute all tasks concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out None and exceptions
        clean_results = []
        for result in results:
            if result is not None and not isinstance(result, Exception):
                clean_results.append(result)
            elif isinstance(result, Exception):
                if self.enable_logging:
                    self.logger.error(f"Parallel execution exception: {result}")

        return clean_results

    async def trigger_until(
            self,
            event: str,
            condition: Callable[[Any], bool],
            *args,
            **kwargs
    ) -> Optional[Any]:
        """Trigger callbacks until a condition is satisfied.

        Executes callbacks in priority order, stopping when one returns
        a result that satisfies the condition function.

        Example:
            >>> # Stop at first result > 10
            >>> result = await framework.trigger_until(
            >>>     "compute",
            >>>     lambda x: x > 10,
            >>>     value=5
            >>> )

        Args:
            event: Event name to trigger
            condition: Function that takes result and returns bool
            *args: Positional arguments for callbacks
            **kwargs: Keyword arguments for callbacks

        Returns:
            First result that satisfies condition, or None
        """
        callbacks = self._callbacks.get(event, [])
        if not callbacks:
            return None

        for callback_info in callbacks:
            if not callback_info.enabled:
                continue

            callback = callback_info.callback

            try:
                # Apply filters
                filter_result = await self._apply_filters(
                    event, callback, args, kwargs
                )

                if filter_result.action == FilterAction.STOP:
                    break
                elif filter_result.action == FilterAction.SKIP:
                    continue

                final_args = filter_result.modified_args or args
                final_kwargs = filter_result.modified_kwargs or kwargs

                # Execute callback
                result = await callback(*final_args, **final_kwargs)

                # Check condition
                if condition(result):
                    if self.enable_logging:
                        self.logger.info(
                            f"Condition satisfied by {callback.__name__}: {result}"
                        )

                    # Handle once-only callbacks
                    if callback_info.once:
                        callback_info.enabled = False

                    return result

                # Handle once-only callbacks even if condition not met
                if callback_info.once:
                    callback_info.enabled = False

            except Exception as e:
                if self.enable_logging:
                    self.logger.error(
                        f"Callback {callback.__name__} failed in trigger_until: {e}"
                    )

        return None

    async def trigger_with_timeout(
            self,
            event: str,
            timeout: float,
            *args,
            **kwargs
    ) -> List[Any]:
        """Trigger event with timeout control.

        Executes all callbacks but will abort if total execution
        time exceeds the timeout.

        Example:
            >>> results = await framework.trigger_with_timeout(
            >>>     "slow_task",
            >>>     timeout=5.0,  # 5 seconds max
            >>>     data="process_me"
            >>> )

        Args:
            event: Event name to trigger
            timeout: Maximum execution time in seconds
            *args: Positional arguments for callbacks
            **kwargs: Keyword arguments for callbacks

        Returns:
            List of callback results (may be incomplete if timeout)
        """
        try:
            return await asyncio.wait_for(
                self.trigger(event, *args, **kwargs),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            if self.enable_logging:
                self.logger.warning(
                    f"Event '{event}' execution timeout after {timeout}s"
                )
            return []

    async def trigger_stream(
            self,
            event: str,
            input_stream: AsyncIterator[Any],
            *args,
            **kwargs
    ) -> AsyncIterator[Any]:
        """Trigger event for each item in an async input stream.

        Processes each item from the input stream through registered callbacks,
        yielding results as they are produced. Supports callbacks that return
        regular values or async generators.

        Example:
            >>> async def data_source():
            >>>     for i in range(5):
            >>>         await asyncio.sleep(0.1)
            >>>         yield {"value": i}
            >>>
            >>> async for result in framework.trigger_stream(
            >>>     "process_item",
            >>>     data_source()
            >>> ):
            >>>     print(result)

        Args:
            event: Event name to trigger
            input_stream: Async iterator providing input items
            *args: Additional positional arguments for callbacks
            **kwargs: Additional keyword arguments for callbacks

        Yields:
            Results from callback executions for each stream item
        """
        try:
            async for item in input_stream:
                # Trigger event for this item
                results = await self.trigger(event, item, *args, **kwargs)

                # Yield each result
                for result in results:
                    # Check if result is an async generator
                    if inspect.isasyncgen(result):
                        # Yield all items from the generator
                        async for sub_item in result:
                            yield sub_item
                    else:
                        yield result

        except Exception as e:
            if self.enable_logging:
                self.logger.error(f"Stream processing error for event '{event}': {e}")
            raise

    async def trigger_generator(
            self,
            event: str,
            *args,
            **kwargs
    ) -> AsyncIterator[Any]:
        """Trigger event and aggregate async generator outputs from callbacks.

        Executes all callbacks for an event and yields items from any callbacks
        that return async generators. Regular return values are also yielded.

        Example:
            >>> @framework.on("data_stream")
            >>> async def streaming_callback():
            >>>     for i in range(10):
            >>>         await asyncio.sleep(0.05)
            >>>         yield {"data": i}
            >>>
            >>> async for item in framework.trigger_generator("data_stream"):
            >>>     print(item)

        Args:
            event: Event name to trigger
            *args: Positional arguments for callbacks
            **kwargs: Keyword arguments for callbacks

        Yields:
            All items from callbacks, including async generator outputs
        """
        # Execute BEFORE hooks
        await self._execute_hooks(event, HookType.BEFORE, *args, **kwargs)

        callbacks = self._callbacks.get(event, [])

        try:
            for callback_info in callbacks:
                if not callback_info.enabled:
                    continue

                callback = callback_info.callback

                try:
                    # Apply filters
                    filter_result = await self._apply_filters(
                        event, callback, args, kwargs
                    )

                    if filter_result.action == FilterAction.STOP:
                        if self.enable_logging:
                            self.logger.info(f"Filter stopped event processing: {event}")
                        break
                    elif filter_result.action == FilterAction.SKIP:
                        if self.enable_logging:
                            self.logger.debug(
                                f"Filter skipped callback {callback.__name__}: "
                                f"{filter_result.reason}"
                            )
                        continue

                    final_args = filter_result.modified_args or args
                    final_kwargs = filter_result.modified_kwargs or kwargs

                    # Execute callback
                    start_time = time.time()
                    result = callback(*final_args, **final_kwargs)

                    # Check if result is an async generator function (not yet called)
                    # or an async generator object
                    if inspect.isasyncgen(result):
                        # It's an async generator, iterate and yield items
                        async for item in result:
                            yield item
                    elif inspect.iscoroutine(result):
                        # It's a coroutine, await it
                        awaited_result = await result
                        # Check if awaited result is async gen
                        if inspect.isasyncgen(awaited_result):
                            async for item in awaited_result:
                                yield item
                        else:
                            yield awaited_result
                    else:
                        # Regular result
                        yield result

                    execution_time = time.time() - start_time

                    # Update metrics
                    if self.enable_metrics:
                        key = f"{event}:{callback.__name__}"
                        self._metrics[key].update(execution_time, is_error=False)

                    # Handle once-only callbacks
                    if callback_info.once:
                        callback_info.enabled = False

                except Exception as e:
                    execution_time = time.time() - start_time

                    # Update metrics
                    if self.enable_metrics:
                        key = f"{event}:{callback.__name__}"
                        self._metrics[key].update(execution_time, is_error=True)

                    if self.enable_logging:
                        self.logger.error(
                            f"Callback {callback.__name__} failed in generator mode: {e}"
                        )

                    # Execute ERROR hooks
                    await self._execute_hooks(event, HookType.ERROR, e, *args, **kwargs)

            # Execute AFTER hooks
            await self._execute_hooks(event, HookType.AFTER, *args, **kwargs)

        finally:
            # Execute CLEANUP hooks
            await self._execute_hooks(event, HookType.CLEANUP, *args, **kwargs)

    # ========== Filters ==========

    def add_filter(self, event: str, filter_obj: EventFilter) -> None:
        """Add a filter to a specific event.

        Args:
            event: Event name
            filter_obj: Filter instance
        """
        self._filters[event].append(filter_obj)

    def add_global_filter(self, filter_obj: EventFilter) -> None:
        """Add a filter that applies to all events.

        Args:
            filter_obj: Filter instance
        """
        self._global_filters.append(filter_obj)

    def add_circuit_breaker(
            self,
            event: str,
            callback: Callable,
            failure_threshold: int = 5,
            timeout: float = 60.0
    ) -> None:
        """Add circuit breaker protection to a callback.

        Args:
            event: Event name
            callback: Callback to protect
            failure_threshold: Failures before opening circuit
            timeout: Seconds before reset attempt
        """
        key = f"{event}:{callback.__name__}"
        breaker = CircuitBreakerFilter(failure_threshold, timeout)
        self._circuit_breakers[key] = breaker
        self.add_filter(event, breaker)

    async def _apply_filters(
            self,
            event: str,
            callback: Callable,
            args: tuple,
            kwargs: dict
    ) -> FilterResult:
        """Apply all relevant filters to a callback.

        Filters are applied in order: global -> event -> callback-specific.

        Args:
            event: Event name
            callback: Callback being executed
            args: Positional arguments
            kwargs: Keyword arguments

        Returns:
            FilterResult with final action and possibly modified arguments
        """
        current_args = args
        current_kwargs = kwargs

        # Collect all applicable filters
        all_filters = []
        all_filters.extend(self._global_filters)
        all_filters.extend(self._filters.get(event, []))
        all_filters.extend(self._callback_filters.get(callback, []))

        # Apply filters sequentially
        for filter_obj in all_filters:
            result = await filter_obj.filter(
                event, callback, *current_args, **current_kwargs
            )

            if result.action in [FilterAction.STOP, FilterAction.SKIP]:
                return result
            elif result.action == FilterAction.MODIFY:
                if result.modified_args is not None:
                    current_args = result.modified_args
                if result.modified_kwargs is not None:
                    current_kwargs = result.modified_kwargs

        return FilterResult(
            FilterAction.CONTINUE,
            modified_args=current_args,
            modified_kwargs=current_kwargs
        )

    # ========== Hooks ==========

    def add_hook(
            self,
            event: str,
            hook_type: HookType,
            hook: Callable
    ) -> None:
        """Add a lifecycle hook to an event.

        Args:
            event: Event name
            hook_type: Type of hook (BEFORE, AFTER, ERROR, CLEANUP)
            hook: Async function to execute
        """
        self._hooks[event][hook_type].append(hook)

    async def _execute_hooks(
            self,
            event: str,
            hook_type: HookType,
            *args,
            **kwargs
    ) -> None:
        """Execute all hooks of a specific type for an event.

        Args:
            event: Event name
            hook_type: Type of hooks to execute
            *args: Arguments to pass to hooks
            **kwargs: Keyword arguments to pass to hooks
        """
        for hook in self._hooks[event][hook_type]:
            try:
                if asyncio.iscoroutinefunction(hook):
                    await hook(*args, **kwargs)
                else:
                    hook(*args, **kwargs)
            except Exception as e:
                self.logger.error(f"Hook execution failed: {hook.__name__} - {e}")

    # ========== Metrics ==========

    def get_metrics(
            self,
            event: Optional[str] = None,
            callback: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Get performance metrics for callbacks.

        Args:
            event: Optional event name filter
            callback: Optional callback name filter

        Returns:
            Dictionary mapping callback keys to metric dictionaries
        """
        result = {}

        for key, metrics in self._metrics.items():
            event_name, callback_name = key.split(":", 1)

            if event and event != event_name:
                continue
            if callback and callback != callback_name:
                continue

            result[key] = metrics.to_dict()

        return result

    def reset_metrics(self) -> None:
        """Reset all performance metrics."""
        self._metrics.clear()

    def get_slow_callbacks(self, threshold: float = 1.0) -> List[Dict[str, Any]]:
        """Get callbacks with average execution time above threshold.

        Args:
            threshold: Minimum average time in seconds

        Returns:
            List of slow callback information, sorted by avg_time
        """
        slow_callbacks = []

        for key, metrics in self._metrics.items():
            if metrics.avg_time > threshold:
                slow_callbacks.append({
                    "callback": key,
                    "avg_time": metrics.avg_time,
                    "max_time": metrics.max_time,
                    "call_count": metrics.call_count
                })

        return sorted(slow_callbacks, key=lambda x: x["avg_time"], reverse=True)

    # ========== History & Replay ==========

    def enable_event_history(self, enabled: bool = True) -> None:
        """Enable or disable event history recording.

        Args:
            enabled: Whether to record event history
        """
        self._enable_history = enabled

    def get_event_history(
            self,
            event: Optional[str] = None,
            since: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Get recorded event history.

        Args:
            event: Optional event name filter
            since: Optional datetime filter

        Returns:
            List of event records
        """
        history = list(self._event_history)

        if event:
            history = [h for h in history if h["event"] == event]

        if since:
            since_timestamp = since.timestamp()
            history = [h for h in history if h["timestamp"] >= since_timestamp]

        return history

    async def replay_events(self, since: Optional[datetime] = None) -> None:
        """Replay recorded events.

        Args:
            since: Optional datetime to replay from
        """
        history = self.get_event_history(since=since)

        for record in history:
            await self.trigger(
                record["event"],
                *record["args"],
                **record["kwargs"]
            )

    # ========== Persistence ==========

    def save_state(self, filepath: str) -> None:
        """Save framework state to file.

        Note: Does not save actual callback functions, only metadata.

        Args:
            filepath: Path to save state file
        """
        state = {
            "callbacks": {},
            "metrics": {k: v.to_dict() for k, v in self._metrics.items()},
            "history": list(self._event_history)
        }

        # Serialize callback metadata
        for event, callback_list in self._callbacks.items():
            state["callbacks"][event] = [
                {
                    "name": ci.callback.__name__,
                    "priority": ci.priority,
                    "namespace": ci.namespace,
                    "tags": list(ci.tags),
                    "enabled": ci.enabled
                }
                for ci in callback_list
            ]

        with open(filepath, 'w') as f:
            json.dump(state, f, indent=2)

    # ========== Queries ==========

    def list_events(self, namespace: Optional[str] = None) -> List[str]:
        """List all registered events.

        Args:
            namespace: Optional namespace filter

        Returns:
            List of event names
        """
        events = list(self._callbacks.keys())

        if namespace:
            filtered_events = []
            for event in events:
                if any(ci.namespace == namespace for ci in self._callbacks[event]):
                    filtered_events.append(event)
            return filtered_events

        return events

    def list_callbacks(self, event: str) -> List[Dict[str, Any]]:
        """List all callbacks registered for an event.

        Args:
            event: Event name

        Returns:
            List of callback information dictionaries
        """
        callbacks = []

        for ci in self._callbacks.get(event, []):
            info = {
                "name": ci.callback.__name__,
                "priority": ci.priority,
                "enabled": ci.enabled,
                "namespace": ci.namespace,
                "tags": list(ci.tags),
                "once": ci.once,
                "max_retries": ci.max_retries,
                "timeout": ci.timeout
            }
            callbacks.append(info)

        return callbacks

    def get_statistics(self) -> Dict[str, Any]:
        """Get overall framework statistics.

        Returns:
            Dictionary with various statistics
        """
        total_callbacks = sum(len(cbs) for cbs in self._callbacks.values())
        total_events = len(self._callbacks)

        namespaces = set()
        for callback_list in self._callbacks.values():
            for ci in callback_list:
                namespaces.add(ci.namespace)

        return {
            "total_events": total_events,
            "total_callbacks": total_callbacks,
            "namespaces": list(namespaces),
            "total_filters": len(self._global_filters) + sum(
                len(filters) for filters in self._filters.values()
            ),
            "total_chains": len(self._chains),
            "history_size": len(self._event_history),
            "metrics_collected": len(self._metrics)
        }
