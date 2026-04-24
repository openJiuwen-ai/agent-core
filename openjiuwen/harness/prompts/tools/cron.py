# coding: utf-8
"""Bilingual description and input params for the cron tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider

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
        "\n\n【重要：cron 表达式限制】标准 cron 的 */X 语义是'当字段值能被 X 整除时触发'，"
        "而非'每隔 X 单位触发'。只有当周期单位能被 X 整除时，间隔才是均匀的。"
        "以下是各字段的限制："
        "\n- 分钟(0-59)：*/X 仅支持 X 整除60的值：1/2/3/4/5/6/10/12/15/20/30。"
        "  例如 */40 实际在每小时第0分和第40分触发（间隔40分→20分交替），并非每40分钟。"
        "  用户要求'每隔40分钟'时，必须先告知此限制并让用户确认是否接受不均匀间隔，"
        "或建议改用整除60的间隔（如20分钟或30分钟）。未经用户确认不得直接创建。"
        "\n- 小时(0-23)：*/X 仅支持 X 整除24的值：1/2/3/4/6/8/12。"
        "  例如 */5 实际在每天0/5/10/15/20时触发（间隔5h→4h→5h→4h交替），并非每5小时。"
        "  用户要求'每隔5小时'时，必须告知限制并让用户确认，或建议改用整除24的间隔。"
        "\n- 日(1-31)：*/X 不可靠，因为不同月份天数不同（28/29/30/31）。"
        "  例如 */15 在2月只触发1、15日（共2次），在31天月份触发1、16、31日（共3次）。"
        "  用户要求'每隔X天'时，建议改用'每周X'或指定固定日期（如每月1号、15号）。"
        "\n- 月(1-12)：*/X 仅支持 X 整除12的值：1/2/3/4/6。"
        "  例如 */5 实际在1/5/10月触发，并非每5个月均匀触发。"
        "\n- 周(0-6)：*/X 仅支持 X 整除7的值：1/7。"
        "  例如 */2 实际在周一/三/五触发，并非'每隔2周'。"
        "  用户要求'每隔2周'时，应直接指定具体星期几或建议简化为'每周一'。"
        "\n处理'每隔X分钟/小时/天'需求时，务必检查 X 是否整除对应周期单位；"
        "若不整除，必须告知用户限制，让用户确认后再创建，或建议替代方案。"
    ),
    "en": (
        "Use the cron action interface. Supports status, list, add, update, remove, run, runs, "
        "and wake using structured schedule/payload/delivery fields. "
        "For requests like 'in 2 minutes', 'tomorrow at 9am', or 'next Monday', "
        "prefer converting the time directly from the current date/time already provided in the system prompt "
        "and call cron directly instead of using code or bash for simple time math. "
        "When creating one-shot reminders, write schedule.at using the user's current local timezone offset "
        "directly, for example +08:00; unless the user explicitly asks for it, do not rewrite it into Z or UTC. "
        "For reminders targeting the current chat, prefer payload.kind=systemEvent with sessionTarget=current. "
        "When confirming a created reminder to the user, prefer the original timezone/offset from schedule.at "
        "instead of rewriting it into UTC."
        "\n\n[CRITICAL: Cron Expression Limits] Standard cron's */X means 'trigger when the field value "
        "is divisible by X', NOT 'every X units'. Uniform intervals only work when the cycle unit "
        "is divisible by X. Field limits:"
        "\n- Minute(0-59): */X only works for X dividing 60: 1/2/3/4/5/6/10/12/15/20/30."
        "  Example: */40 triggers at minute 0 and 40 each hour (alternating 40min-20min gaps), "
        "NOT every 40 minutes."
        "  When user requests 'every 40 minutes', MUST inform user of this limitation first "
        "and let user confirm whether to accept uneven intervals, or suggest intervals that divide 60 "
        "(e.g. 20 or 30 minutes). Do NOT create without user confirmation."
        "\n- Hour(0-23): */X only works for X dividing 24: 1/2/3/4/6/8/12."
        "  Example: */5 triggers at hours 0/5/10/15/20 (alternating 5h-4h gaps), NOT every 5 hours."
        "  When user requests 'every 5 hours', MUST inform and let user confirm, or suggest 4 or 6 hours."
        "\n- Day(1-31): */X is unreliable due to varying month lengths (28/29/30/31 days)."
        "  Example: */15 triggers on day 1,15 in Feb (2 times), but 1,16,31 in 31-day months (3 times)."
        "  When user requests 'every X days', suggest using 'every week on day X' or fixed dates."
        "\n- Month(1-12): */X only works for X dividing 12: 1/2/3/4/6."
        "  Example: */5 triggers in Jan/May/Oct, NOT uniformly every 5 months."
        "\n- Weekday(0-6): */X only works for X dividing 7: 1/7."
        "  Example: */2 triggers on Mon/Wed/Fri, NOT 'every 2 weeks'."
        "  When user requests 'every 2 weeks', suggest simplifying to a specific weekday."
        "\nWhen handling 'every X minutes/hours/days' requests, always check if X divides the cycle unit. "
        "If not, MUST inform user of the limitation, let user confirm before creating, or suggest alternatives."
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
        "cn": (
            "cron表达式(分时日月周)。*/X表示当字段值能被X整除时触发，仅当周期能被X整除时间隔均匀。"
            "分钟仅支持X整除60(1/2/3/4/5/6/10/12/15/20/30)；"
            "小时仅支持X整除24(1/2/3/4/6/8/12)；日不可靠；月仅支持X整除12(1/2/3/4/6)；周仅支持X整除7(1/7)。"
            "详见工具描述。"
        ),
        "en": (
            "Cron expression (min hour dom month dow). */X triggers when field divisible by X; "
            "uniform intervals only when cycle divisible by X. "
            "Minute: X must divide 60; Hour: X must divide 24; Day unreliable; "
            "Month: X must divide 12; Weekday: X must divide 7. See tool description."
        ),
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
        "cn": "systemEvent提醒文本。不要包含时间/频率信息（如'每隔40分钟'、'每天9点'）",
        "en": "Reminder text for systemEvent. Do NOT include time/frequency info",
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
        "cn": (
            "兼容层cron表达式。*/X仅当周期能被X整除时间隔均匀。"
            "分钟仅支持X整除60(1/2/3/4/5/6/10/12/15/20/30)；"
            "小时仅支持X整除24(1/2/3/4/6/8/12)；日不可靠；月仅支持X整除12(1/2/3/4/6)；周仅支持X整除7(1/7)。"
            "详见工具描述。"
        ),
        "en": (
            "Compatibility cron expression. */X only yields uniform intervals when cycle divisible by X. "
            "Minute: X must divide 60; Hour: X must divide 24; Day unreliable; "
            "Month: X must divide 12; Weekday: X must divide 7. See tool description."
        ),
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
        "cn": "具体任务内容，到点执行时发给助手。不要包含时间/频率信息（如'每隔40分钟'、'每天9点'）",
        "en": "Task content sent to assistant at scheduled time. Do NOT include time/frequency info",
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
