# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Any, Awaitable, Callable, Dict, Iterator, List, Optional, Set, Tuple
import functools

from pydantic import BaseModel

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import context_engine_logger, LogEventType
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.session.agent import Session
from openjiuwen.core.context_engine.base import ContextWindow, ModelContext
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig
from openjiuwen.core.context_engine.context.context import SessionModelContext
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.token.base import TokenCounter
from openjiuwen.core.context_engine.token.tokenizer_manager import TokenizerArtifactManager
from openjiuwen.core.context_engine.token.tokenizer_registry import TokenizerRegistry
from openjiuwen.core.context_engine.token.tokenizer_selector import TokenizerSelector
from openjiuwen.core.context_engine.token.string_length_counter import StringLengthCounter
from openjiuwen.core.context_engine.processor.base import ContextProcessor
from openjiuwen.core.runner.callback import trigger, lazy_callback_framework as _fw
from openjiuwen.core.runner.callback.events import ContextEvents


class ContextEngine:
    """
    Manages the lifecycle and processing of conversational context.

    ContextEngine acts as the central entry-point for:
    1. Registering and configuring message processors.
    2. Creating isolated ModelContext instances tied to a session.
    3. Applying processor chains to enforce window limits, compression, etc.

    Parameters
    ----------
    config : ContextEngineConfig, optional
        Global engine settings (message/token limits, processor defaults).
        If omitted, a default configuration is used.
    """

    _PROCESSOR_MAP: Dict[str, type[ContextProcessor]] = dict()

    # Provider-specific error codes are not consistent enough to identify a
    # context overflow on their own. Keep the detection message-based and
    # inspect both wrapped exception fields and the exception cause chain.
    _CONTEXT_OVERFLOW_PHRASES = (
        "context length",
        "context window",
        "maximum context",
        "context limit",
        "prompt is too long",
        "prompt too long",
        "input is too long",
        "input too long",
    )
    _CONTEXT_OVERFLOW_FIELDS = (
        "message",
        "body",
        "details",
        "error",
        "errors",
        "response",
        "code",
        "type",
        "param",
        "status_code",
        "status",
        "text",
        "content",
        "cause",
        "__cause__",
        "__context__",
    )

    def __init__(self,
                 config: ContextEngineConfig = None,
                 workspace=None,
                 sys_operation=None,
                 ):
        self._config = config or ContextEngineConfig()
        self._workspace = workspace
        self._sys_operation = sys_operation
        self._context_pool: Dict[str, ModelContext] = dict()
        self._window_mutators: List[
            Callable[[ModelContext, ContextWindow], Awaitable[ContextWindow]]
        ] = []

    def register_window_mutator(
            self,
            mutator: Callable[[ModelContext, ContextWindow], Awaitable[ContextWindow]],
    ) -> None:
        """Register an instance-level final-window mutator."""
        self._window_mutators.append(mutator)

    def clear_window_mutators(self) -> None:
        """Clear instance-level final-window mutators."""
        self._window_mutators.clear()

    @staticmethod
    def _select_token_counter(config: ContextEngineConfig) -> TokenCounter:
        """Select a local-only counter for a context-engine configuration."""
        has_model_tokenizer_target = bool(
            config.model_name
            or config.model_provider
            or config.tokenizer_spec
            or config.tokenizer_registry
        )
        try:
            return TokenizerSelector(
                provider=config.model_provider or "",
                model=config.model_name or "",
                spec=config.tokenizer_spec,
                registry=TokenizerRegistry(config.tokenizer_registry),
                # Context creation and model rebinding are deliberately
                # read-only.  The application warm-up service owns downloads.
                manager=TokenizerArtifactManager(
                    cache_dir=config.tokenizer_cache_dir,
                    enable_download=False,
                    offline=True,
                ),
                # This switch is only meaningful for a model-less historical
                # context. Configured model contexts use native-or-string
                # resolution and never initialize a remote/default tiktoken
                # counter here.
                allow_tiktoken_fallback=(
                    config.enable_tiktoken_counter
                    and not has_model_tokenizer_target
                ),
            ).select()
        except Exception as exc:  # noqa: BLE001 - context must fail open
            context_engine_logger.warning(
                "local tokenizer selection failed; using string-length fallback: %s",
                exc,
            )
            return StringLengthCounter(
                model=config.model_name or "",
                fallback_reason="local_tokenizer_selection_failed",
            )

    def update_model_context(
        self,
        *,
        model_name: Optional[str] = None,
        context_window_tokens: Optional[int] = None,
    ) -> None:
        """Update selected-model metadata for this engine and cached contexts.

        ``ContextEngineConfig.context_window_tokens`` is deliberately not
        changed here: it is the global override and remains the highest
        priority source. Only the selected model name and model-level window
        are refreshed, including on contexts already cached for a session.
        """
        self._config = self._config.model_copy(
            update={
                "model_name": model_name or None,
                "model_context_window_tokens_override": (
                    context_window_tokens
                    if isinstance(context_window_tokens, int) and context_window_tokens > 0
                    else None
                ),
            }
        )
        for context in self._context_pool.values():
            update_context = getattr(context, "update_model_context", None)
            if callable(update_context):
                update_context(
                    model_name=model_name,
                    context_window_tokens=context_window_tokens,
                )

    @_fw.emit_after(ContextEvents.CONTEXT_RETRIEVED, result_key="context")
    async def create_context(
            self,
            context_id: str = "default_context_id",
            session: Session = None,
            *,
            processors: List[Tuple[str, BaseModel]] = None,
            history_messages: List[BaseMessage] = None,
            token_counter: TokenCounter = None,
    ) -> ModelContext:
        """
        Create or retrieve a ModelContext for the given session & context ID.

        Token counting: an explicitly provided ``token_counter`` is always used.
        Otherwise a configured local tokenizer is used when available; an
        unavailable local tokenizer falls back to ``StringLengthCounter``
        without downloading anything. The application-level warm-up service is
        responsible for remote downloads before context creation.

        Message seeding:
        - if `history_messages` is provided, it is used as-is;
        - else if `mem_scope_id` is given, the engine attempts to restore
          previous messages from long-term memory under that scope;
        - otherwise an empty message list is adopted.

        Args:
            context_id: Unique identifier for this context within the session.
            session: Session object supplying session_id; if None, a default
                     session ID is used.
            history_messages: Initial message list.
            token_counter: Strategy for counting tokens. If omitted, the
                           selector only reads local tokenizer artifacts and
                           falls back to character-length counting for a
                           configured model.

        Returns:
            ModelContext: The newly created or cached context instance.
        """
        context_id = self._process_context_id(context_id)
        session_id = session.get_session_id() if session else "default_session_id"
        full_context_id = f"{session_id}_{context_id}"
        if full_context_id in self._context_pool:
            context = self._context_pool.get(full_context_id)
            context.set_session_ref(session)
            self._load_state_from_session(context, session, history_messages)
            return context

        processor_instances = [
            self._create_processor(processor_type, processor_config)
            for processor_type, processor_config in (processors or [])
        ]

        if token_counter is None:
            token_counter = self._select_token_counter(self._config)

        if self._config.enable_openrouter_model_context_window_tokens:
            # Scheduled, not awaited: this is the first-turn critical path and the
            # fetch is a ~600KB cross-region download. This context falls back to
            # the built-in window table; later contexts pick up the fetched values.
            ContextUtils.prefetch_openrouter_model_context_window_tokens(
                self._config.openrouter_request_timeout,
            )

        context = SessionModelContext(
            context_id,
            session_id,
            self._config,
            history_messages=history_messages or [],
            processors=processor_instances,
            token_counter=token_counter,
            session_ref=session,
            workspace=self._workspace,
            sys_operation=self._sys_operation,
            window_mutators=self._window_mutators,
        )
        self._load_state_from_session(context, session, history_messages)
        self._context_pool[full_context_id] = context
        return context

    def rebind_context_model(
        self,
        config: ContextEngineConfig,
        *,
        session_id: str | None = None,
        context_id: str | None = None,
    ) -> int:
        """Apply a new model binding to cached contexts without losing history.

        ``config`` becomes the configuration for contexts created after this
        call.  Existing contexts can be narrowed by ``session_id`` and/or
        ``context_id``; only contexts that implement the built-in rebinding
        contract are updated.  The return value is the number of contexts
        successfully rebound.
        """
        if not isinstance(config, ContextEngineConfig):
            raise TypeError("config must be a ContextEngineConfig")

        self._config = config
        normalized_context_id = (
            self._process_context_id(context_id) if context_id is not None else None
        )
        token_counter = self._select_token_counter(config)
        rebound = 0
        for context in self._context_pool.values():
            if session_id is not None and context.session_id() != session_id:
                continue
            if normalized_context_id is not None and context.context_id() != normalized_context_id:
                continue
            rebind = getattr(context, "rebind_model", None)
            if not callable(rebind):
                continue
            try:
                if rebind(config, token_counter=token_counter):
                    rebound += 1
            except Exception:  # noqa: BLE001 - one custom context must not block switching
                context_engine_logger.warning(
                    "failed to rebind context model, session_id=%s context_id=%s",
                    context.session_id(),
                    context.context_id(),
                    exc_info=True,
                )
        return rebound

    def get_context(
            self,
            context_id: str = "default_context_id",
            session_id: str = "default_session_id"
    ) -> Optional[ModelContext]:
        """
        Retrieve an existing ModelContext from the pool.

        Args:
            context_id: Context identifier within the session.
            session_id: Session identifier.

        Returns:
            ModelContext instance if found, otherwise None.
        """
        context_id = self._process_context_id(context_id)
        full_context_id = f"{session_id}_{context_id}"
        return self._context_pool.get(full_context_id, None)

    async def compress_context(
            self,
            context_id: str = "default_context_id",
            session: Session = None,
            *,
            session_id: str = None,
            processor_types: List[str] = None,
            **kwargs,
    ) -> str | dict[str, Any]:
        """
        Actively run registered compression processors for an existing context.

        Args:
            context_id: Target context identifier.
            session: Optional session object used to resolve session_id.
            session_id: Optional explicit session identifier. If both `session`
                        and `session_id` are provided, `session` takes precedence.
            processor_types: Optional compression processor allowlist.
            **kwargs: Extra arguments forwarded to the processor hook.

        Returns:
            Compression result code:
            - ``"busy"``: passive compression is already in progress.
            - ``"compressed"``: active compression ran and changed context.
            - ``"noop"``: active compression ran but nothing changed, or no
              compression processor is registered.

        A successful compression is saved to the resolved session and committed
        before this method returns. Contexts without a session remain in-memory only.
        """
        resolved_session_id = session.get_session_id() if session else (session_id or "default_session_id")
        context = self.get_context(context_id=context_id, session_id=resolved_session_id)
        if context is None:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg=f"cannot find context '{context_id}' in session '{resolved_session_id}'"
            )
        if not hasattr(context, "compress_context"):
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg=f"context '{context_id}' does not support active compression"
            )
        result = await context.compress_context(
            processor_types=processor_types,
            sys_operation=self._sys_operation,
            **kwargs,
        )
        result_code = result.get("result") if isinstance(result, dict) else result
        if result_code == "compressed":
            get_session_ref = getattr(context, "get_session_ref", None)
            effective_session = session or (get_session_ref() if callable(get_session_ref) else None)
            if effective_session is not None:
                await self.save_contexts(effective_session, context_ids=[context_id])
                await effective_session.commit()
        return result

    async def recover_from_model_exception(
            self,
            *,
            context_id: str = "default_context_id",
            session: Session = None,
            context: ModelContext = None,
            exception: Exception = None,
            streaming: bool = False,
            stream_chunks_emitted: int = 0,
            **kwargs,
    ) -> bool:
        """Recover from a model context-window rejection by compressing once.

        ReAct agents call this hook after model-call rail retries are
        exhausted. A retry is requested when the provider error clearly
        describes an input/context limit and ``compress_context`` returns
        ``"compressed"``. A missing or ineffective processor preserves the
        original model exception.
        """
        del context

        # A streaming response may already have been sent to the caller when
        # the provider reports an overflow. Retrying after compression would
        # emit the already-sent prefix again, so only recover before the first
        # stream chunk is visible.
        if streaming and stream_chunks_emitted > 0:
            context_engine_logger.warning(
                "skip model context recovery after streaming output was emitted, "
                "stream_chunks_emitted=%s",
                stream_chunks_emitted,
            )
            return False

        if not self.is_context_overflow_error(exception):
            return False

        compression_kwargs = dict(kwargs)
        compression_kwargs.setdefault(
            "compression_trigger", "model_context_overflow"
        )
        result = await self.compress_context(
            context_id=context_id,
            session=session,
            **compression_kwargs,
        )
        result_code = result.get("result") if isinstance(result, dict) else result
        if result_code != "compressed":
            context_engine_logger.info(
                "model context recovery did not change context, result=%s",
                result_code,
            )
            return False

        context_engine_logger.info("model context recovery compressed context")
        return True

    @classmethod
    def is_context_overflow_error(cls, exception: Exception) -> bool:
        """Return whether an exception describes an input context overflow.

        Model clients commonly wrap the provider response several times. The
        matcher therefore checks structured error fields, response text, and
        the Python cause/context chain. It intentionally avoids treating a
        generic quota, authentication, or network error as recoverable.
        """
        if exception is None:
            return False

        for value in cls._iter_exception_texts(exception):
            message = value.casefold().replace("_", " ").replace("-", " ")
            if any(phrase in message for phrase in cls._CONTEXT_OVERFLOW_PHRASES):
                return True
            if "token limit" in message and not any(
                term in message for term in ("output", "completion", "response")
            ):
                return True
            if "too long" in message and any(
                term in message for term in ("context", "prompt", "input")
            ):
                return True
            if ("exceed" in message or "over limit" in message) and any(
                term in message for term in ("context", "prompt", "input")
            ):
                return True
        return False

    @classmethod
    def _iter_exception_texts(
            cls,
            value: Any,
            *,
            depth: int = 0,
            seen: Set[int] = None,
    ) -> Iterator[str]:
        """Yield text from common provider error wrappers without recursion loops."""
        if value is None or depth > 6:
            return
        if seen is None:
            seen = set()

        if isinstance(value, str):
            yield value
            return
        if isinstance(value, (bytes, bytearray)):
            yield bytes(value).decode("utf-8", errors="replace")
            return

        value_id = id(value)
        if value_id in seen:
            return
        seen.add(value_id)

        if isinstance(value, BaseException):
            try:
                value_text = str(value)
            except Exception:
                value_text = ""
            if value_text:
                yield value_text
            fields = cls._CONTEXT_OVERFLOW_FIELDS
        elif isinstance(value, dict):
            fields = ()
            for key, nested_value in value.items():
                yield from cls._iter_exception_texts(
                    key, depth=depth + 1, seen=seen
                )
                yield from cls._iter_exception_texts(
                    nested_value, depth=depth + 1, seen=seen
                )
        elif isinstance(value, (list, tuple, set, frozenset)):
            fields = ()
            for nested_value in value:
                yield from cls._iter_exception_texts(
                    nested_value, depth=depth + 1, seen=seen
                )
        else:
            fields = cls._CONTEXT_OVERFLOW_FIELDS

        for field in fields:
            nested_value = getattr(value, field, None)
            if nested_value is None or nested_value is value or callable(nested_value):
                continue
            yield from cls._iter_exception_texts(
                nested_value, depth=depth + 1, seen=seen
            )

    async def clear_context(
            self,
            context_id: str = None,
            session_id: str = None
    ):
        """
        Remove contexts from the internal pool.

        Behavior depends on the arguments provided:
        1. Neither argument supplied  -> delete the all context.
        2. Only `session_id` supplied -> delete every context belonging to that session.
        3. Both arguments supplied    -> delete the single context identified..

        Parameters
        ----------
        context_id : str, optional
            Logical context identifier.  When provided, `session_id` must also
            be supplied and only the exact matching context is removed.
        session_id : str, optional
            Session identifier used to scope the deletion.  If omitted, the
            operation targets all contexts.

        Warnings
        --------
        Logs a warning when the requested session or context cannot be found.
        """
        if session_id is None:
            cleared_count = len(self._context_pool)
            self._context_pool.clear()
            await trigger(ContextEvents.CONTEXT_CLEARED,
                       context_id=context_id, session_id=session_id,
                       cleared_count=cleared_count)
            return

        if context_id is None:
            delete_context_list = [
                context.context_id() for _, context in self._context_pool.items()
                if context.session_id() == session_id
            ]

            if not delete_context_list:
                context_engine_logger.warning(
                    "Delete context failed, session does not exist",
                    event_type=LogEventType.CONTEXT_CLEAR,
                    metadata={"session_id": session_id}
                )
                return

            for context_id in delete_context_list:
                full_context_id = f"{session_id}_{context_id}"
                del self._context_pool[full_context_id]
            await trigger(ContextEvents.CONTEXT_CLEARED,
                       context_id=context_id, session_id=session_id,
                       cleared_count=len(delete_context_list))
            return

        context_id = self._process_context_id(context_id)
        full_context_id = f"{session_id}_{context_id}"
        if full_context_id not in self._context_pool:
            context_engine_logger.warning(
                "Delete context failed, context does not exist",
                event_type=LogEventType.CONTEXT_CLEAR,
                metadata={"session_id": session_id}
            )
            return

        del self._context_pool[full_context_id]
        await trigger(ContextEvents.CONTEXT_CLEARED,
                   context_id=context_id, session_id=session_id)

    @_fw.emit_after(ContextEvents.CONTEXT_OFFLOADED, result_key="result")
    async def save_contexts(self,
                            session: Session,
                            context_ids: List[str] = None
                            ):
        """
        Batch-persist multiple contexts and their runtime states.

        Each context's messages, sliding-window position, token count and statistics
        are saved locally.

        Args:
            context_ids: List of target context identifiers to save.
            session: Session object;
        """
        if not session:
            context_engine_logger.warning(
                "Save context failed, session cannot be None",
                event_type=LogEventType.CONTEXT_SAVE,
            )
            return
        session_id = session.get_session_id()
        states = dict()
        if context_ids is None:
            context_ids = [
                context.context_id() for context_id, context in self._context_pool.items()
                if context.session_id() == session_id
            ]

        for context_id in context_ids:
            context_id = self._process_context_id(context_id)
            full_context_id = f"{session_id}_{context_id}"
            context = self._context_pool.get(full_context_id)
            if context is None or not hasattr(context, "save_state"):
                continue
            context_state = context.save_state()
            states[context_id] = context_state
        self._save_state_to_session(session, states)
        return states

    @classmethod
    def register_processor(cls, processor_class=None):
        """
        Class-method decorator for plugging a new ContextProcessor into the engine.

        Usage
        -----
        @register_processor(MyProcessorConfig)
        class MyProcessor(ContextProcessor):
            ...

        The decorator performs two book-keeping actions:
        1. Maps `processor_class.processor_type()` -> `processor_class`
           so the engine can instantiate the processor at runtime.
        2. Maps `processor_class.processor_type()` -> `config`
           so the engine can validate/convert the user-supplied config dict.

        Parameters
        ----------
        config : subclass of ContextProcessorConfig
            Configuration schema that belongs to the processor being decorated.
        processor_class : subclass of ContextProcessor, optional
            When used as a **parameter-less** decorator this argument is None;
            the inner function receives the real class object.

        Returns
        -------
        callable
            A decorator that accepts the processor class and returns it unchanged
            after registration (allowing normal class-definition syntax).
        """
        @functools.wraps(processor_class)
        def register_processor_class(processor_class: type[ContextProcessor]):
            cls._PROCESSOR_MAP[processor_class.processor_type()] = processor_class
            return processor_class
        return register_processor_class

    def _create_processor(self, processor_type: str, config: BaseModel):
        processor_class = self._PROCESSOR_MAP.get(processor_type)
        if not processor_class:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg=f"cannot find processor type '{processor_type}'"
            )

        try:
            processor = processor_class(config)
        except Exception as e:
            raise build_error(
                StatusCode.CONTEXT_EXECUTION_ERROR,
                error_msg=f"init processor type '{processor_type}' failed",
                cause=e
            ) from e

        return processor

    @staticmethod
    def _load_state_from_session(
            context: ModelContext,
            session: Session,
            history_messages: List[BaseMessage] = None
    ):
        if not session:
            return
        states = None
        if hasattr(session, "get_state"):
            states = session.get_state("context")
        elif hasattr(session, "_inner"):
            states = getattr(session, "_inner").get_state("context") if session else None

        if states is None:
            return

        if not hasattr(context, "load_state"):
            return

        if history_messages is not None:
            context_id = context.context_id()
            states[context_id]['messages'] = history_messages

        context.load_state(states)

    @staticmethod
    def _save_state_to_session(
            session,
            states: dict
    ):
        if not session:
            return
        if hasattr(session, "update_state"):
            session.update_state({"context": None})
            session.update_state({"context": states})
        elif hasattr(session, "_inner"):
            getattr(session, "_inner").update_state({"context": None})
            getattr(session, "_inner").update_state({"context": states})

    @staticmethod
    def _process_context_id(context_id: str) -> str:
        return context_id.replace(".", "_")
