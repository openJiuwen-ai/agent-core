# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Reusable cron tool factory."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard
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


def _make_tool(*, tool_id: str, name: str, description: str, input_params: dict[str, Any], func: Any) -> Tool:
    return LocalFunction(
        card=ToolCard(
            id=tool_id,
            name=name,
            description=description,
            input_params=input_params,
        ),
        func=func,
    )


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
                tool_id=f"cron_list_jobs_{scope}",
                name="cron_list_jobs",
                description="Legacy compatibility tool. List all cron jobs.",
                input_params={"type": "object", "properties": {}, "required": []},
                func=list_jobs_wrapper,
            ),
            _make_tool(
                tool_id=f"cron_get_job_{scope}",
                name="cron_get_job",
                description="Legacy compatibility tool. Get a single cron job by id.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "The job id to look up",
                        }
                    },
                    "required": ["job_id"],
                },
                func=get_job_wrapper,
            ),
            _make_tool(
                tool_id=f"cron_create_job_{scope}",
                name="cron_create_job",
                description=(
                    "Legacy compatibility tool. Create a cron job using flat fields "
                    "(name, cron_expr, timezone, targets, description, wake_offset_seconds)."
                    "\n\n[CRITICAL: Cron Expression Limits] Standard cron's */X means 'trigger when the field value "
                    "is divisible by X', NOT 'every X units'. Uniform intervals only work when the cycle unit "
                    "is divisible by X. Field limits:"
                    "\n- Minute(0-59): */X only works for X dividing 60: 1/2/3/4/5/6/10/12/15/20/30."
                    "  Example: */40 triggers at minute 0 and 40 each hour (alternating 40min-20min gaps), "
                    "NOT every 40 minutes."
                    "  When user requests 'every 40 minutes', MUST inform user of this limitation first "
                    "and let user confirm whether to accept uneven intervals, or suggest intervals that divide 60."
                    "  Do NOT create without user confirmation."
                    "\n- Hour(0-23): */X only works for X dividing 24: 1/2/3/4/6/8/12."
                    "  Example: */5 triggers at hours 0/5/10/15/20 (alternating 5h-4h gaps), NOT every 5 hours."
                    "\n- Day(1-31): */X is unreliable due to varying month lengths."
                    "\n- Month(1-12): */X only works for X dividing 12: 1/2/3/4/6."
                    "\n- Weekday(0-6): */X only works for X dividing 7: 1/7."
                    "\nWhen handling 'every X minutes/hours' requests, always check if X divides the cycle unit. "
                    "If not, MUST inform user and let user confirm before creating."
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "任务名称"},
                        "cron_expr": {
                            "type": "string",
                            "description": (
                                "Cron表达式。*/X仅当周期能被X整除时间隔均匀。"
                                "分钟X整除60；小时X整除24；日不可靠。详见工具描述。"
                            ),
                        },
                        "timezone": {
                            "type": "string",
                            "description": "时区，如 Asia/Shanghai",
                            "default": "Asia/Shanghai",
                        },
                        "targets": target_schema,
                        "enabled": {
                            "type": "boolean",
                            "description": "是否启用",
                            "default": True,
                        },
                        "description": {
                            "type": "string",
                            "description": (
                                "具体任务内容，到点执行时发给助手。"
                                "不要包含时间/频率信息（如'每隔40分钟'、'每天9点'）"
                            ),
                        },
                        "wake_offset_seconds": {
                            "type": "integer",
                            "description": "提前多少秒执行，默认 300",
                            "default": 300,
                        },
                    },
                    "required": ["name", "cron_expr", "timezone", "description"],
                },
                func=create_job_wrapper,
            ),
            _make_tool(
                tool_id=f"cron_update_job_{scope}",
                name="cron_update_job",
                description="Legacy compatibility tool. Update an existing cron job with a flat patch dict.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job id to update"},
                        "patch": {
                            "type": "object",
                            "description": "Fields to update",
                            "additionalProperties": True,
                        },
                    },
                    "required": ["job_id", "patch"],
                },
                func=update_job_wrapper,
            ),
            _make_tool(
                tool_id=f"cron_delete_job_{scope}",
                name="cron_delete_job",
                description="Legacy compatibility tool. Delete a cron job by id.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job id to delete"},
                    },
                    "required": ["job_id"],
                },
                func=delete_job_wrapper,
            ),
            _make_tool(
                tool_id=f"cron_toggle_job_{scope}",
                name="cron_toggle_job",
                description="Legacy compatibility tool. Enable or disable a cron job.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job id"},
                        "enabled": {
                            "type": "boolean",
                            "description": "Whether to enable the job",
                        },
                    },
                    "required": ["job_id", "enabled"],
                },
                func=toggle_job_wrapper,
            ),
            _make_tool(
                tool_id=f"cron_preview_job_{scope}",
                name="cron_preview_job",
                description="Legacy compatibility tool. Preview next N scheduled run times for a job.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job id"},
                        "count": {
                            "type": "integer",
                            "description": "Number of runs to preview (1-50, default 5)",
                            "default": 5,
                        },
                    },
                    "required": ["job_id"],
                },
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
