# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""KVC policy owned by single-agent model and child-agent lifecycles."""

from dataclasses import dataclass
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.kv_cache import (
    KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV,
    KVCacheAffinityConfig,
    resolve_session_lineage,
)


@dataclass(frozen=True, slots=True)
class KVCacheRuntime:
    enable_release: bool
    enable_affinity: bool
    supports_release: bool
    supports_affinity: bool


class KVCacheModelCallHook:
    """Stateful warning and ContextWindow management for ReAct model calls."""

    def __init__(self) -> None:
        self._release_warning_logged = False
        self._affinity_warning_logged = False

    def reset_warnings(self) -> None:
        self._release_warning_logged = False
        self._affinity_warning_logged = False

    def resolve_runtime(
            self,
            llm: Any,
            config: KVCacheAffinityConfig | None,
    ) -> KVCacheRuntime:
        config = config or KVCacheAffinityConfig()
        enable_affinity = config.enable_kv_cache_affinity
        enable_release = config.enable_kv_cache_release

        supports_release = False
        if enable_release:
            supports = getattr(llm, "supports_kv_cache_release", None)
            supports_release = bool(supports()) if callable(supports) else False

        supports_affinity = False
        if enable_affinity:
            supports = getattr(llm, "supports_kv_cache_affinity", None)
            supports_affinity = bool(supports()) if callable(supports) else False

        runtime = KVCacheRuntime(
            enable_release=enable_release,
            enable_affinity=enable_affinity,
            supports_release=supports_release,
            supports_affinity=supports_affinity,
        )
        self._warn_unsupported(runtime)
        return runtime

    @staticmethod
    def resolve_lineage(
            runtime: KVCacheRuntime,
            session: Any,
            fallback_session_id: str,
    ) -> tuple[str | None, str | None]:
        if runtime.enable_release and runtime.supports_release:
            session_id = (
                session.get_session_id()
                if session is not None
                else fallback_session_id
            )
            return session_id, None
        if runtime.enable_affinity and runtime.supports_affinity:
            session_id, parent_session_id = resolve_session_lineage(session)
            if not session_id:
                return fallback_session_id, fallback_session_id
            return session_id, parent_session_id
        return None, None

    async def handle_context_window_change(
            self,
            *,
            runtime: KVCacheRuntime,
            llm: Any,
            context: Any,
            context_window: Any,
            session_id: str | None,
            parent_session_id: str | None,
            model_name: str,
    ) -> None:
        if runtime.enable_release and runtime.supports_release:
            await self._release_changed_window(
                llm=llm,
                context=context,
                context_window=context_window,
                session_id=session_id,
                model_name=model_name,
            )
        elif runtime.enable_affinity and runtime.supports_affinity:
            await self._evict_changed_window(
                llm=llm,
                context=context,
                context_window=context_window,
                session_id=session_id,
                parent_session_id=parent_session_id,
                model_name=model_name,
            )

    @staticmethod
    def build_invoke_kwargs(
            *,
            runtime: KVCacheRuntime,
            llm: Any,
            session: Any,
            session_id: str | None,
            parent_session_id: str | None,
    ) -> dict:
        extra_kwargs: dict = {}
        build_release = getattr(llm, "build_kv_cache_invoke_kwargs", None)
        if runtime.enable_release and runtime.supports_release and callable(build_release):
            extra_kwargs.update(build_release(
                session=session,
                enable_kv_cache_release=True,
            ))
        build_affinity = getattr(llm, "build_kv_cache_affinity_invoke_kwargs", None)
        if runtime.enable_affinity and runtime.supports_affinity and callable(build_affinity):
            extra_kwargs.update(build_affinity(
                session=session,
                session_id=session_id,
                parent_session_id=parent_session_id,
                enable_kv_cache_affinity=True,
            ))
        return extra_kwargs

    async def _evict_changed_window(
            self,
            *,
            llm: Any,
            context: Any,
            context_window: Any,
            session_id: str | None,
            parent_session_id: str | None,
            model_name: str,
    ) -> None:
        if not session_id:
            logger.warning("Skip Ascend KV cache window diff eviction because session_id is empty.")
            return
        change = context.detect_context_window_change(context_window)
        if change is None or not change.has_change:
            return

        target = "messages" if change.msg_start is not None else "tools"
        evict_kwargs = {
            "session_id": session_id,
            "parent_session_id": parent_session_id or session_id,
            "target": target,
            "messages": change.old_messages,
            "tools": change.old_tools,
            "model": model_name,
        }
        if change.msg_start is not None:
            evict_kwargs["msg_start"] = change.msg_start
            evict_kwargs["msg_end"] = change.msg_end
            if change.tools_start is not None:
                evict_kwargs["include_tools"] = True
                evict_kwargs["tools_start"] = change.tools_start
                evict_kwargs["tools_end"] = change.tools_end
        else:
            evict_kwargs["tools_start"] = change.tools_start
            evict_kwargs["tools_end"] = change.tools_end

        try:
            evicted = await llm.evict_kvc(**evict_kwargs)
        except Exception as exc:
            logger.warning(
                "Ascend KV cache window diff eviction failed; continue normal inference. "
                "session_id=%s parent_session_id=%s target=%s "
                "msg_range=[%s,%s] tools_range=[%s,%s] error=%s",
                session_id,
                parent_session_id or session_id,
                target,
                change.msg_start,
                change.msg_end,
                change.tools_start,
                change.tools_end,
                exc,
            )
            return
        if not evicted:
            logger.warning(
                "Ascend KV cache window diff eviction returned false; continue normal inference. "
                "session_id=%s parent_session_id=%s target=%s "
                "msg_range=[%s,%s] tools_range=[%s,%s]",
                session_id,
                parent_session_id or session_id,
                target,
                change.msg_start,
                change.msg_end,
                change.tools_start,
                change.tools_end,
            )

    @staticmethod
    async def _release_changed_window(
            *,
            llm: Any,
            context: Any,
            context_window: Any,
            session_id: str | None,
            model_name: str,
    ) -> None:
        if not session_id:
            logger.warning("Skip KV cache release because session_id is empty.")
            return
        change = context.detect_context_window_change(context_window)
        if change is None or not change.has_change:
            return

        release_kwargs = {
            "session_id": session_id,
            "messages": change.old_messages,
            "messages_released_index": (
                change.msg_start
                if change.msg_start is not None
                else len(change.old_messages)
            ),
            "model": model_name,
        }
        if change.tools_start is not None:
            release_kwargs["tools"] = change.old_tools
            release_kwargs["tools_released_index"] = change.tools_start
        if change.msg_start is not None:
            logger.info(f"  [RELEASE REASON] Message modified at index {change.msg_start}")
        if change.tools_start is not None:
            logger.info(f"  [RELEASE REASON] Tool modified at index {change.tools_start}")

        try:
            released = await llm.release(**release_kwargs)
        except Exception as exc:
            logger.warning(
                "KV cache release failed; continue normal inference. "
                "session_id=%s msg_start=%s tools_start=%s error=%s",
                session_id,
                change.msg_start,
                change.tools_start,
                exc,
            )
            return
        if not released:
            logger.warning(
                "KV cache release returned false; continue normal inference. "
                "session_id=%s msg_start=%s tools_start=%s",
                session_id,
                change.msg_start,
                change.tools_start,
            )

    def _warn_unsupported(self, runtime: KVCacheRuntime) -> None:
        if (
                runtime.enable_release
                and not runtime.supports_release
                and not self._release_warning_logged
        ):
            logger.warning(
                "KVCacheAffinityConfig.enable_kv_cache_release is True, "
                "but the current LLM does not support KV cache release; "
                "KV cache release will not take effect."
            )
            self._release_warning_logged = True
        if (
                runtime.enable_affinity
                and not runtime.supports_affinity
                and not self._affinity_warning_logged
        ):
            logger.warning(
                "KVCacheAffinityConfig.enable_kv_cache_affinity is True, "
                "but the current LLM does not support Ascend KV cache affinity; "
                "agent_hint will not take effect."
            )
            self._affinity_warning_logged = True


def build_child_session_kwargs(agent: Any, parent_session: Any) -> dict:
    """Return no session mutation at all when the child agent has affinity disabled."""
    config_fn = getattr(agent, "config", None)
    config = config_fn() if callable(config_fn) else getattr(agent, "_config", None)
    kv_config = getattr(config, "kv_cache_affinity_config", None)
    if getattr(kv_config, "enable_kv_cache_affinity", False) is not True:
        return {}
    child_envs = dict(parent_session.get_envs() or {})
    child_envs[KV_CACHE_AFFINITY_PARENT_SESSION_ID_ENV] = parent_session.get_session_id()
    return {"envs": child_envs}
