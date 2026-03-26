# coding: utf-8
"""Bilingual description and input params for the cron tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import ToolMetadataProvider

DESCRIPTION: Dict[str, str] = {
    "cn": (
        "使用 action 接口：status、list、add、update、"
        "remove、run、runs、wake，并兼容结构化 schedule/payload/delivery 字段。"
        "处理“2分钟后”“明天上午9点”“下周一”这类时间时，优先根据系统提示中已提供的当前"
        "日期与时间直接换算并调用 cron，不要为了简单的时间换算先调用 code 或 bash。"
        "创建一次性提醒时，schedule.at 默认直接使用用户当前本地时区偏移来写，例如 +08:00；"
        "除非用户明确要求，否则不要改写成 Z 或 UTC。"
        "给当前聊天创建提醒时，优先使用 payload.kind=systemEvent 和 sessionTarget=current。"
        "向用户确认创建结果时，优先按 schedule.at 里的原始时区/偏移表述，不要自行改写成 UTC。"
    ),
    "en": (
        "Use the cron action interface. Supports status, "
        "list, add, update, remove, run, runs, and wake using structured schedule/payload/"
        "delivery fields. For requests like 'in 2 minutes', 'tomorrow at 9am', or 'next "
        "Monday', prefer converting the time directly from the current date/time already "
        "provided in the system prompt and call cron directly instead of using code or bash "
        "for simple time math. When creating one-shot reminders, write schedule.at using the "
        "user's current local timezone offset directly, for example +08:00; unless the user "
        "explicitly asks for it, do not rewrite it into Z or UTC. For reminders targeting the current "
        "chat, prefer "
        "payload.kind=systemEvent with sessionTarget=current. When confirming a created "
        "reminder to the user, prefer the original timezone/offset from schedule.at instead "
        "of rewriting it into UTC."
    ),
}

FIELD_DESCRIPTIONS: Dict[str, Dict[str, str]] = {
    "action": {
        "cn": "要执行的 cron 操作",
        "en": "Cron action to execute",
    },
    "job": {
        "cn": "用于 add 的任务对象；支持结构化字段和兼容层字段",
        "en": "Job object for add; supports structured fields and compatibility fields",
    },
    "jobId": {
        "cn": "用于 update/remove/run/runs 的任务 ID",
        "en": "Job id used by update/remove/run/runs",
    },
    "patch": {
        "cn": "用于 update 的补丁对象",
        "en": "Patch object used by update",
    },
    "includeDisabled": {
        "cn": "list 时是否包含已禁用任务",
        "en": "Whether list should include disabled jobs",
    },
    "text": {
        "cn": "wake 动作要发送的提示文本",
        "en": "Wake text to inject for action=wake",
    },
    "mode": {
        "cn": "wake 的触发模式",
        "en": "Wake delivery mode",
    },
    "contextMessages": {
        "cn": "保留给上下文提示的兼容字段",
        "en": "Reserved compatibility field for context hints",
    },
    "name": {
        "cn": "任务名称",
        "en": "Job name",
    },
    "enabled": {
        "cn": "任务是否启用",
        "en": "Whether the job is enabled",
    },
    "schedule": {
        "cn": "结构化调度定义，支持 at/every/cron",
        "en": "Structured schedule definition supporting at/every/cron",
    },
    "schedule.kind": {
        "cn": "调度类型：at、every 或 cron",
        "en": "Schedule type: at, every, or cron",
    },
    "schedule.at": {
        "cn": "一次性执行时间，ISO 8601",
        "en": "One-shot execution time in ISO 8601",
    },
    "schedule.everyMs": {
        "cn": "循环间隔，毫秒",
        "en": "Recurring interval in milliseconds",
    },
    "schedule.anchorMs": {
        "cn": "every 调度的起始锚点毫秒时间戳",
        "en": "Anchor timestamp in milliseconds for every schedules",
    },
    "schedule.expr": {
        "cn": "cron 表达式",
        "en": "Cron expression",
    },
    "schedule.tz": {
        "cn": "cron 调度使用的时区",
        "en": "Timezone used by cron schedules",
    },
    "schedule.staggerMs": {
        "cn": "cron 调度的可选抖动毫秒数",
        "en": "Optional cron jitter in milliseconds",
    },
    "payload": {
        "cn": "结构化任务负载，支持 systemEvent 或 agentTurn",
        "en": "Structured job payload supporting systemEvent or agentTurn",
    },
    "payload.kind": {
        "cn": "负载类型：systemEvent 或 agentTurn",
        "en": "Payload type: systemEvent or agentTurn",
    },
    "payload.text": {
        "cn": "systemEvent 提醒文本",
        "en": "Reminder text for systemEvent payloads",
    },
    "payload.message": {
        "cn": "agentTurn 发送给代理的消息",
        "en": "Message sent to the agent for agentTurn payloads",
    },
    "payload.model": {
        "cn": "agentTurn 可选模型覆盖",
        "en": "Optional model override for agentTurn",
    },
    "payload.thinking": {
        "cn": "agentTurn 的思考预算或模式",
        "en": "Thinking mode or budget for agentTurn",
    },
    "payload.timeoutSeconds": {
        "cn": "agentTurn 超时时间（秒）",
        "en": "Timeout in seconds for agentTurn",
    },
    "payload.allowUnsafeExternalContent": {
        "cn": "是否允许不安全的外部内容",
        "en": "Whether unsafe external content is allowed",
    },
    "payload.lightContext": {
        "cn": "是否使用轻量上下文执行",
        "en": "Whether to run with lighter context",
    },
    "payload.deliver": {
        "cn": "agentTurn 自带的投递策略字段",
        "en": "Embedded delivery strategy field for agentTurn",
    },
    "payload.channel": {
        "cn": "agentTurn 的默认投递频道",
        "en": "Default delivery channel for agentTurn",
    },
    "payload.to": {
        "cn": "agentTurn 的目标收件人",
        "en": "Target recipient for agentTurn",
    },
    "payload.bestEffortDeliver": {
        "cn": "是否最佳努力投递",
        "en": "Whether delivery should be best effort",
    },
    "payload.fallbacks": {
        "cn": "agentTurn 的回退投递列表",
        "en": "Fallback delivery list for agentTurn",
    },
    "delivery": {
        "cn": "提醒结果的投递方式",
        "en": "How reminder output should be delivered",
    },
    "delivery.mode": {
        "cn": "投递模式：none、announce 或 webhook",
        "en": "Delivery mode: none, announce, or webhook",
    },
    "delivery.channel": {
        "cn": "announce 模式使用的频道",
        "en": "Channel used by announce mode",
    },
    "delivery.to": {
        "cn": "目标收件人或会话标识",
        "en": "Target recipient or session identifier",
    },
    "delivery.accountId": {
        "cn": "投递账号标识",
        "en": "Account identifier for delivery",
    },
    "delivery.bestEffort": {
        "cn": "announce/webhook 是否最佳努力投递",
        "en": "Whether announce/webhook delivery is best effort",
    },
    "delivery.failureDestination": {
        "cn": "失败时的兜底投递目标",
        "en": "Fallback destination when delivery fails",
    },
    "sessionTarget": {
        "cn": "会话目标：main、isolated、current 或 session:<id>",
        "en": "Session target: main, isolated, current, or session:<id>",
    },
    "wakeMode": {
        "cn": "唤醒模式：now 或 next-heartbeat",
        "en": "Wake mode: now or next-heartbeat",
    },
    "deleteAfterRun": {
        "cn": "执行后是否自动删除该任务",
        "en": "Whether the job should be deleted after it runs",
    },
    "cron_expr": {
        "cn": "兼容层 cron 表达式",
        "en": "Compatibility cron expression",
    },
    "timezone": {
        "cn": "兼容层时区字段",
        "en": "Compatibility timezone field",
    },
    "wake_offset_seconds": {
        "cn": "兼容层提前唤醒秒数",
        "en": "Compatibility wake offset in seconds",
    },
    "description": {
        "cn": "兼容层描述字段",
        "en": "Compatibility description field",
    },
    "targets": {
        "cn": "兼容层目标频道字段",
        "en": "Compatibility target channel field",
    },
}


def _desc(key: str, language: str) -> str:
    return FIELD_DESCRIPTIONS[key].get(language, FIELD_DESCRIPTIONS[key]["cn"])


def get_cron_job_input_params(language: str = "cn") -> Dict[str, Any]:
    return {
        "type": "object",
        "required": [],
        "additionalProperties": True,
        "properties": {
            "name": {
                "type": "string",
                "description": _desc("name", language),
            },
            "enabled": {
                "type": "boolean",
                "description": _desc("enabled", language),
            },
            "schedule": {
                "type": "object",
                "description": _desc("schedule", language),
                "required": [],
                "additionalProperties": True,
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["at", "every", "cron"],
                        "description": _desc("schedule.kind", language),
                    },
                    "at": {
                        "type": "string",
                        "description": _desc("schedule.at", language),
                    },
                    "everyMs": {
                        "type": "integer",
                        "description": _desc("schedule.everyMs", language),
                    },
                    "anchorMs": {
                        "type": "integer",
                        "description": _desc("schedule.anchorMs", language),
                    },
                    "expr": {
                        "type": "string",
                        "description": _desc("schedule.expr", language),
                    },
                    "tz": {
                        "type": "string",
                        "description": _desc("schedule.tz", language),
                    },
                    "staggerMs": {
                        "type": "integer",
                        "description": _desc("schedule.staggerMs", language),
                    },
                },
            },
            "payload": {
                "type": "object",
                "description": _desc("payload", language),
                "required": [],
                "additionalProperties": True,
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["systemEvent", "agentTurn"],
                        "description": _desc("payload.kind", language),
                    },
                    "text": {
                        "type": "string",
                        "description": _desc("payload.text", language),
                    },
                    "message": {
                        "type": "string",
                        "description": _desc("payload.message", language),
                    },
                    "model": {
                        "type": "string",
                        "description": _desc("payload.model", language),
                    },
                    "thinking": {
                        "type": "string",
                        "description": _desc("payload.thinking", language),
                    },
                    "timeoutSeconds": {
                        "type": "integer",
                        "description": _desc("payload.timeoutSeconds", language),
                    },
                    "allowUnsafeExternalContent": {
                        "type": "boolean",
                        "description": _desc("payload.allowUnsafeExternalContent", language),
                    },
                    "lightContext": {
                        "type": "boolean",
                        "description": _desc("payload.lightContext", language),
                    },
                    "deliver": {
                        "type": "string",
                        "description": _desc("payload.deliver", language),
                    },
                    "channel": {
                        "type": "string",
                        "description": _desc("payload.channel", language),
                    },
                    "to": {
                        "type": "string",
                        "description": _desc("payload.to", language),
                    },
                    "bestEffortDeliver": {
                        "type": "boolean",
                        "description": _desc("payload.bestEffortDeliver", language),
                    },
                    "fallbacks": {
                        "type": "array",
                        "description": _desc("payload.fallbacks", language),
                        "items": {
                            "type": "string",
                            "description": _desc("payload.fallbacks", language),
                        },
                    },
                },
            },
            "delivery": {
                "type": "object",
                "description": _desc("delivery", language),
                "required": [],
                "additionalProperties": True,
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["none", "announce", "webhook"],
                        "description": _desc("delivery.mode", language),
                    },
                    "channel": {
                        "type": "string",
                        "description": _desc("delivery.channel", language),
                    },
                    "to": {
                        "type": "string",
                        "description": _desc("delivery.to", language),
                    },
                    "accountId": {
                        "type": "string",
                        "description": _desc("delivery.accountId", language),
                    },
                    "bestEffort": {
                        "description": _desc("delivery.bestEffort", language),
                    },
                    "failureDestination": {
                        "description": _desc("delivery.failureDestination", language),
                    },
                },
            },
            "sessionTarget": {
                "type": "string",
                "description": _desc("sessionTarget", language),
            },
            "wakeMode": {
                "type": "string",
                "enum": ["now", "next-heartbeat"],
                "description": _desc("wakeMode", language),
            },
            "deleteAfterRun": {
                "type": "boolean",
                "description": _desc("deleteAfterRun", language),
            },
            "cron_expr": {
                "type": "string",
                "description": _desc("cron_expr", language),
            },
            "timezone": {
                "type": "string",
                "description": _desc("timezone", language),
            },
            "wake_offset_seconds": {
                "type": "integer",
                "description": _desc("wake_offset_seconds", language),
            },
            "description": {
                "type": "string",
                "description": _desc("description", language),
            },
            "targets": {
                "type": "string",
                "description": _desc("targets", language),
            },
        },
    }


def get_cron_input_params(language: str = "cn") -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "list", "add", "update", "remove", "run", "runs", "wake"],
                "description": _desc("action", language),
            },
            "job": {
                **get_cron_job_input_params(language),
                "description": _desc("job", language),
            },
            "jobId": {
                "type": "string",
                "description": _desc("jobId", language),
            },
            "patch": {
                **get_cron_job_input_params(language),
                "description": _desc("patch", language),
            },
            "includeDisabled": {
                "type": "boolean",
                "description": _desc("includeDisabled", language),
            },
            "text": {
                "type": "string",
                "description": _desc("text", language),
            },
            "mode": {
                "type": "string",
                "enum": ["now", "next-heartbeat"],
                "description": _desc("mode", language),
            },
            "contextMessages": {
                "type": "integer",
                "description": _desc("contextMessages", language),
            },
        },
        "required": ["action"],
        "additionalProperties": True,
    }


class CronMetadataProvider(ToolMetadataProvider):
    """Metadata provider for the unified cron tool."""

    def get_name(self) -> str:
        return "cron"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_cron_input_params(language)
