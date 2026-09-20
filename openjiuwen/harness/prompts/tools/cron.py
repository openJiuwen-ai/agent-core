# coding: utf-8
"""Bilingual description and input params for the cron tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider

DESCRIPTION: Dict[str, str] = {
    "cn": (
        "定时任务管理：查询/创建/修改/删除/立即执行全部通过本工具 action 完成。"
        "任务由系统统一存储管理，禁止用 shell、文件操作、数据库或任何其他工具增删改定时任务，"
        "这条规则没有例外。\n"
        "action：list 查询全部；add 创建；update 修改（传 jobId，只改传入字段，未传字段保持不变）；"
        "remove 删除；run 立即执行一次。\n"
        "【何时创建】用户明确要求定时/周期执行，或表达中出现频率线索"
        "（'每天''每周''每隔X分钟/小时''每天早上9点''下午3点提醒我'）时直接创建，"
        "无需用户说出'定时任务'四个字；拿不准时，只要请求=任务+周期性时间模式，就创建。"
        "创建的任务立即启用生效，无需用户再手动开启；"
        "add 时不要传 enabled 字段（停用/启用只通过 update 操作已有任务）。\n"
        "【调度】周期任务填 cron_expr（每天9点='0 9 * * *'，每周一9点='0 9 * * 1'）；"
        "一次性任务填 run_at（ISO 8601 本地时间，保留本地时区偏移，不改写成 Z 或 UTC）。"
        "二者填其一。\n"
        "【未指定时间时避开整点】用户只给了频率没给具体时间点"
        "（如'每天生成早报''每周汇总一次'）时，自选一个具体运行时间，"
        "不要落在整点（:00）和半点（:30）：分钟从 01-29、31-59 里随机选"
        "（避开 00/15/30/45 这类常用刻度，分散负载）；小时按任务语义就近合理选择"
        "（早报选早晨、日报选傍晚），用户说过时间要求时严格遵守用户的时间。\n"
        "【先查后改】修改/查看已有任务先用 action=list 看现状；"
        "已存在满足需求的任务优先 update 复用，不要重复 add。\n"
        "【任务内容 description】到点执行的指令，须自足、只描述任务本身"
        "（执行时用户可能不在场，无法追问）："
        "禁止包含时间/频率——用户说'每五分钟提醒我滴眼药水'，description 应为'该滴眼药水了'；"
        "禁止塞入'写个文件''汇报无事可做'之类无意义指令，除非用户明确要求；"
        "用户只给简单提醒（'提醒我喝水'）时充实为 1-3 句可播报文案"
        "（做什么+好处+一句行动建议），用户已给出丰富内容时只做轻度润色。"
        "缺失细节做合理假设并注明，不要反复追问；确实无法推进时简要说明后停止。\n"
        "【推送频道 targets】用户未明确指定时不填，系统默认当前对话渠道；禁止从历史推断。\n"
        "【cron 步长限制】*/X 是'字段值能被 X 整除时触发'，不是'每隔 X 单位'。"
        "均匀间隔的合法 X：分→1/2/3/4/5/6/10/12/15/20/30，时→1/2/3/4/6/8/12，月→1/2/3/4/6；"
        "*/40 分钟实为0分和40分交替、*/5 小时实为0/5/10/15/20时，都不是均匀间隔；"
        "'每隔X天'不可靠（月份天数不一），建议改'每周X'或固定日期；"
        "'每隔X周'也不可靠，建议直接指定星期几。"
        "用户要求的 X 不在合法值内时，必须先告知限制并确认，或建议最近的合法间隔，"
        "未经确认不得创建。\n"
        "【禁止传 wake_offset_seconds】除非用户明确要求'提前X分钟唤醒'，"
        "否则不要传该字段，后端默认 0（到点执行）。\n"
        "处理'2分钟后''明天上午9点'这类相对时间时，直接根据系统提示中的当前日期时间换算，"
        "不要为了简单时间换算先调用 code 或 bash。"
    ),
    "en": (
        "Scheduled-task management: query/create/update/delete/run-now all go through this "
        "tool's action interface. Tasks are stored and managed by the system; NEVER create, "
        "modify, or delete them with shell, file operations, databases, or any other tool. "
        "This rule is absolute.\n"
        "action: list to query all; add to create; update to modify (pass jobId; only "
        "provided fields change, unspecified fields are preserved); remove to delete; "
        "run to trigger once now.\n"
        "[When to create] Create directly when the user explicitly asks for scheduling, or when "
        "the request contains frequency cues ('every day', 'every week', 'every X minutes/hours', "
        "'daily at 9am', 'remind me at 3pm') — even if the words 'scheduled task' never appear. "
        "When in doubt, if the request describes a task plus a recurring time pattern, create it. "
        "Created tasks are enabled immediately; do NOT pass enabled on add "
        "(enable/disable only via update on existing tasks).\n"
        "[Schedule] Recurring tasks: cron_expr (daily 9am = '0 9 * * *', Mondays 9am = "
        "'0 9 * * 1'). One-shot tasks: run_at (ISO 8601 local time; keep the local timezone "
        "offset; do not rewrite into Z or UTC). Provide exactly one of the two.\n"
        "[Off-hour default when time unspecified] When the user gives only a frequency without "
        "a concrete time (e.g. 'daily news digest', 'weekly summary'), pick the run time "
        "yourself and AVOID on-the-hour (:00) and half-past (:30) minutes: choose the minute "
        "randomly from 01-29 / 31-59 (skip common marks like 00/15/30/45 to spread load); "
        "choose the hour to fit the task's meaning (mornings for morning briefs, evenings "
        "for daily digests). Always honor an explicitly stated time.\n"
        "[Look before you change] Before modifying an existing task, query with action=list "
        "to see what is already set up; if a task already satisfies the need, prefer update "
        "over creating a duplicate.\n"
        "[Task content description] The instruction executed at the scheduled time. Keep it "
        "self-contained and about the task itself (the user may have limited availability to "
        "answer questions when it fires): NEVER copy time/frequency wording into it — "
        "'remind me to use eye drops every 5 minutes' -> description 'Time to use your eye drops'. "
        "Do not instruct it to write a file or announce 'nothing to do' unless the user "
        "explicitly asks for that. For bare reminders ('remind me to drink water'), enrich "
        "into 1-3 sentences of ready-to-deliver copy (what to do + why it helps + one tip); "
        "when the user already gave rich content, only lightly polish it. Make reasonable "
        "assumptions for missing details, note them, and proceed instead of repeatedly "
        "asking; if truly blocked, report briefly and stop.\n"
        "[Delivery channel targets] Leave empty unless the user explicitly specifies one; the "
        "system uses the current channel. Never infer from history.\n"
        "[Cron step limits] */X means 'trigger when the field value is divisible by X', NOT "
        "'every X units'. Legal X for uniform intervals: minute->1/2/3/4/5/6/10/12/15/20/30, "
        "hour->1/2/3/4/6/8/12, month->1/2/3/4/6. */40 minutes actually fires at :00 and :40; "
        "*/5 hours at 0/5/10/15/20h — neither is uniform. 'Every X days' is unreliable "
        "(uneven month lengths) — suggest 'weekly on day X' or fixed dates; 'every X weeks' is "
        "also unreliable — name the weekday instead. When the requested X is not legal, you "
        "MUST inform the user and get confirmation, or suggest the nearest legal interval. "
        "Do NOT create without user confirmation.\n"
        "[Do NOT pass wake_offset_seconds] Unless the user explicitly asks to 'wake X minutes "
        "early', omit this field; the backend defaults to 0 (run at the scheduled time).\n"
        "For relative times like 'in 2 minutes' or 'tomorrow at 9am', convert directly from the "
        "current date/time already provided in the system prompt instead of using code or bash."
    ),
}

FIELD_DESCRIPTIONS: Dict[str, Dict[str, str]] = {
    "action": {
        "cn": "要执行的 cron 操作",
        "en": "Cron action to execute",
    },
    "jobId": {
        "cn": "update/remove/run 操作的任务 ID",
        "en": "Job id used by update/remove/run",
    },
    "name": {
        "cn": "任务名称（最长 64 字符）",
        "en": "Job name (max 64 characters)",
    },
    "description": {
        "cn": (
            "任务内容，到点执行时发给助手。禁止包含时间/频率信息（时间已由 cron_expr/run_at 表达）；"
            "简单提醒充实为 1-3 句可播报文案：做什么+好处+一句行动建议。最长 500 字符。"
        ),
        "en": (
            "Task content sent to the assistant at run time. Do NOT include time/frequency "
            "info (the schedule already covers timing); enrich bare reminders into 1-3 "
            "sentences of deliverable copy: what to do + why + one actionable tip. Max 500 chars."
        ),
    },
    "cron_expr": {
        "cn": (
            "周期任务 cron 表达式，5 段标准格式'分 时 日 月 周'"
            "（6/7 段 Quartz 也可，系统自动归一）。每天9点='0 9 * * *'；每周一9点='0 9 * * 1'。"
            "与 run_at 二选一。用户未指定具体时间时，分钟避开 00/15/30/45，"
            "从 01-29、31-59 随机选，不要落在整点/半点。"
        ),
        "en": (
            "Cron expression for recurring tasks, 5-field 'minute hour day month dow' "
            "(6/7-field Quartz also accepted and auto-normalized). Daily 9am='0 9 * * *'; "
            "Mondays 9am='0 9 * * 1'. Mutually exclusive with run_at. When the user does "
            "not specify a concrete time, pick the minute from 01-29 / 31-59 (avoid "
            "00/15/30/45 and on-the-hour/half-past marks)."
        ),
    },
    "run_at": {
        "cn": (
            "一次性任务执行时间，ISO 8601 本地时间（如 '2026-03-20T14:30'）。"
            "直接使用用户当前本地时区偏移（如 +08:00），除非用户明确要求，否则不改写成 Z 或 UTC。"
            "与 cron_expr 二选一。"
        ),
        "en": (
            "One-shot execution time in ISO 8601 local time (e.g. '2026-03-20T14:30'). "
            "Write the user's current local timezone offset directly (e.g. +08:00); unless the "
            "user explicitly asks, do not rewrite into Z or UTC. Mutually exclusive with cron_expr."
        ),
    },
    "timezone": {
        "cn": "IANA 时区，默认 Asia/Shanghai",
        "en": "IANA timezone, defaults to Asia/Shanghai",
    },
    "targets": {
        "cn": "推送频道。用户未明确指定时不填，系统自动使用当前对话频道；禁止从历史记录推断。",
        "en": (
            "Delivery channel. Leave empty unless the user explicitly specifies one; "
            "the system uses the current channel. Never infer from history."
        ),
    },
    "enabled": {
        "cn": "仅 update 使用：启用/停用已有任务。add 时勿传（创建的任务立即启用）。",
        "en": "Update only: enable/disable an existing job. Do not pass on add (created jobs are enabled immediately).",
    },
}


def _desc(key: str, language: str) -> str:
    return FIELD_DESCRIPTIONS[key].get(language, FIELD_DESCRIPTIONS[key]["cn"])


def get_cron_input_params(language: str = "cn") -> Dict[str, Any]:
    """Flat parameter schema for the unified cron tool.

    Kept deliberately small (8 params): routing fields such as mode/model/
    project_id are injected from the session context by the host bridge, and
    one-shot semantics (``delete_after_run``) are derived from whether
    ``run_at`` or ``cron_expr`` is provided.
    """
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "add", "update", "remove", "run"],
                "description": _desc("action", language),
            },
            "jobId": {
                "type": "string",
                "description": _desc("jobId", language),
            },
            "name": {
                "type": "string",
                "description": _desc("name", language),
            },
            "description": {
                "type": "string",
                "description": _desc("description", language),
            },
            "cron_expr": {
                "type": "string",
                "description": _desc("cron_expr", language),
            },
            "run_at": {
                "type": "string",
                "description": _desc("run_at", language),
            },
            "timezone": {
                "type": "string",
                "description": _desc("timezone", language),
                # 不设 default：pydantic 会把 default 填进 update 的扁平
                # patch，静默覆盖用户自选时区（如 America/New_York 被改成
                # Asia/Shanghai）。未传时落 None，由分发层 None 过滤剔除。
            },
            "targets": {
                "type": "string",
                "description": _desc("targets", language),
            },
            "enabled": {
                "type": "boolean",
                "description": _desc("enabled", language),
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
