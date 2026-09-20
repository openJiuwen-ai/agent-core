# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Reusable cron tool factory."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from openjiuwen.core.foundation.tool import LocalFunction, Tool
from openjiuwen.harness.prompts.tools import build_tool_card


@dataclass(frozen=True)
class CronToolContext:
    """Runtime context bound to a cron tool registration."""

    channel_id: str
    session_id: str | None = None
    metadata: dict[str, Any] | None = None
    mode: str | None = None

    @property
    def tool_scope(self) -> str:
        channel = (self.channel_id or "unknown").strip() or "unknown"
        session = (self.session_id or "default").strip() or "default"
        return f"{channel}:{session}"


class CronToolBackend(Protocol):
    """Host-provided cron backend used by the generic tool layer."""

    async def list_jobs(self, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        ...

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        ...

    async def create_job(
        self,
        params: dict[str, Any],
        *,
        context: CronToolContext | None = None,
    ) -> dict[str, Any]:
        ...

    async def update_job(
        self,
        job_id: str,
        patch: dict[str, Any],
        *,
        context: CronToolContext | None = None,
    ) -> dict[str, Any]:
        ...

    async def delete_job(self, job_id: str) -> bool:
        ...

    async def toggle_job(self, job_id: str, enabled: bool) -> dict[str, Any]:
        ...

    async def preview_job(
        self,
        job_id: str,
        count: int = 5,
    ) -> list[dict[str, Any]]:
        ...

    async def run_now(self, job_id: str) -> str:
        ...

    async def status(self) -> dict[str, Any]:
        ...

    async def get_runs(
        self,
        job_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        ...

    async def wake(
        self,
        text: str,
        *,
        context: CronToolContext | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        ...


def _tool_scope(context: CronToolContext | None) -> str:
    scope = context.tool_scope if context is not None else "cron:default"
    return scope.replace(":", "_")


async def _dispatch_cron_action(
    backend: CronToolBackend,
    *,
    action: str,
    job: dict[str, Any] | None = None,
    jobId: str | None = None,
    patch: dict[str, Any] | None = None,
    includeDisabled: bool = False,
    text: str | None = None,
    mode: str | None = None,
    contextMessages: int | None = None,  # noqa: ARG001
    context: CronToolContext | None = None,
    **kwargs: Any,
) -> Any:
    action_name = str(action or "").strip().lower()
    legacy_job_id = kwargs.pop("id", None)
    target_job_id = str(jobId or legacy_job_id or "").strip()
    excluded_keys = {
        "action",
        "job",
        "jobId",
        "patch",
        "includeDisabled",
        "text",
        "mode",
        "contextMessages",
        "gatewayUrl",
        "gatewayToken",
        "timeoutMs",
        "runMode",
    }
    flat_kwargs: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in excluded_keys:
            continue
        flat_kwargs[key] = value
    if action_name == "status":
        return await backend.status()
    if action_name == "list":
        return {"jobs": await backend.list_jobs(include_disabled=bool(includeDisabled))}
    if action_name == "add":
        create_input = dict(job or {})
        if not create_input:
            create_input = flat_kwargs
        return await backend.create_job(create_input, context=context)
    if action_name == "update":
        if not target_job_id:
            raise ValueError("jobId is required")
        patch_input = dict(patch or {})
        if not patch_input:
            # 扁平参数路径：LLM 未传的字段经 pydantic schema 格式化后以 None
            # （及 timezone 默认值）出现在 flat_kwargs 里。直接透传会让后端把
            # "键存在但值为 None" 误判为更新意图，清空 name/description 等字段。
            # 只保留 LLM 显式传值的键。
            patch_input = {
                key: value
                for key, value in flat_kwargs.items()
                if value is not None
            }
        return await backend.update_job(target_job_id, patch_input, context=context)
    if action_name == "remove":
        if not target_job_id:
            raise ValueError("jobId is required")
        return {"deleted": await backend.delete_job(target_job_id)}
    if action_name == "run":
        if not target_job_id:
            raise ValueError("jobId is required")
        return {"run_id": await backend.run_now(target_job_id)}
    if action_name == "runs":
        if not target_job_id:
            raise ValueError("jobId is required")
        return {"runs": await backend.get_runs(target_job_id)}
    if action_name == "wake":
        return await backend.wake(text or "", context=context, mode=mode)
    raise ValueError("unsupported cron action")


def create_cron_tools(
    backend: CronToolBackend,
    *,
    context: CronToolContext | None = None,
    language: str = "cn",
    target_channels: list[str] | None = None,  # noqa: ARG001
    default_target_channel: str | None = None,  # noqa: ARG001
    include_legacy_compat: bool = False,  # noqa: ARG001
    agent_id: str | None = None,
) -> list[Tool]:
    """Create the unified cron tool.

    Exactly one model-facing tool named ``cron`` (action dispatcher) is
    returned. Legacy ``cron_*`` tools were removed; the parameters are kept
    in the signature for backward compatibility with existing callers and
    are ignored.
    """

    scope = _tool_scope(context)
    final_agent_id = agent_id or scope

    async def cron_tool_wrapper(**kwargs: Any) -> Any:
        return await _dispatch_cron_action(backend, context=context, **kwargs)

    return [
        LocalFunction(
            card=build_tool_card("cron", f"cron_{scope}", language, agent_id=final_agent_id),
            func=cron_tool_wrapper,
        )
    ]


__all__ = [
    "CronToolContext",
    "CronToolBackend",
    "create_cron_tools",
]
