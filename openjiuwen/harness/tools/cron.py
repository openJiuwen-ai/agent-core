# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Reusable cron tool factory."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard
from openjiuwen.harness.prompts.tools import build_tool_card, get_tool_input_params


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


def _make_tool(
    *,
    name: str,
    scope: str,
    language: str,
    agent_id: str,
    func: Any,
    target_schema: dict[str, Any] | None = None,
) -> Tool:
    card = build_tool_card(name, f"{name}_{scope}", language, agent_id=agent_id)
    if target_schema is not None:
        input_params = get_tool_input_params(name, language)
        if "properties" in input_params and "targets" in input_params["properties"]:
            input_params["properties"]["targets"] = target_schema
        card = ToolCard(
            id=card.id,
            name=card.name,
            description=card.description,
            input_params=input_params,
        )
    return LocalFunction(card=card, func=func)


def _target_schema(
    target_channels: Sequence[str] | None,
    default_target_channel: str | None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "string",
        "description": "Legacy compatibility target channel",
    }
    enum_values = [str(item).strip() for item in list(target_channels or []) if str(item).strip()]
    if enum_values:
        schema["enum"] = enum_values
    if default_target_channel:
        schema["default"] = str(default_target_channel).strip()
    return schema


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
            patch_input = flat_kwargs
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
    target_channels: Sequence[str] | None = None,
    default_target_channel: str | None = None,
    include_legacy_compat: bool = True,
    agent_id: str | None = None,
) -> list[Tool]:
    """Create the unified cron tool plus optional legacy compatibility tools."""

    scope = _tool_scope(context)
    final_agent_id = agent_id or scope

    async def cron_tool_wrapper(**kwargs: Any) -> Any:
        return await _dispatch_cron_action(backend, context=context, **kwargs)

    async def list_jobs_wrapper() -> list[dict[str, Any]]:
        return await backend.list_jobs()

    async def get_job_wrapper(job_id: str) -> dict[str, Any] | None:
        return await backend.get_job(job_id)

    async def create_job_wrapper(**kwargs: Any) -> dict[str, Any]:
        return await backend.create_job(dict(kwargs), context=context)

    async def update_job_wrapper(job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return await backend.update_job(job_id, patch, context=context)

    async def delete_job_wrapper(job_id: str) -> bool:
        return await backend.delete_job(job_id)

    async def toggle_job_wrapper(job_id: str, enabled: bool) -> dict[str, Any]:
        return await backend.toggle_job(job_id, enabled)

    async def preview_job_wrapper(job_id: str, count: int = 5) -> list[dict[str, Any]]:
        return await backend.preview_job(job_id, count)

    tools: list[Tool] = [
        LocalFunction(
            card=build_tool_card("cron", f"cron_{scope}", language, agent_id=final_agent_id),
            func=cron_tool_wrapper,
        )
    ]
    if not include_legacy_compat:
        return tools

    target_schema = _target_schema(target_channels, default_target_channel)

    tools.extend(
        [
            _make_tool(
                name="cron_list_jobs",
                scope=scope,
                language=language,
                agent_id=final_agent_id,
                func=list_jobs_wrapper,
            ),
            _make_tool(
                name="cron_get_job",
                scope=scope,
                language=language,
                agent_id=final_agent_id,
                func=get_job_wrapper,
            ),
            _make_tool(
                name="cron_create_job",
                scope=scope,
                language=language,
                agent_id=final_agent_id,
                func=create_job_wrapper,
                target_schema=target_schema,
            ),
            _make_tool(
                name="cron_update_job",
                scope=scope,
                language=language,
                agent_id=final_agent_id,
                func=update_job_wrapper,
            ),
            _make_tool(
                name="cron_delete_job",
                scope=scope,
                language=language,
                agent_id=final_agent_id,
                func=delete_job_wrapper,
            ),
            _make_tool(
                name="cron_toggle_job",
                scope=scope,
                language=language,
                agent_id=final_agent_id,
                func=toggle_job_wrapper,
            ),
            _make_tool(
                name="cron_preview_job",
                scope=scope,
                language=language,
                agent_id=final_agent_id,
                func=preview_job_wrapper,
            ),
        ]
    )
    return tools


__all__ = [
    "CronToolContext",
    "CronToolBackend",
    "create_cron_tools",
]
