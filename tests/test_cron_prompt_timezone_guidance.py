import unittest

from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.prompts.tools.cron import (
    DESCRIPTION,
    FIELD_DESCRIPTIONS,
    get_cron_input_params,
)
from openjiuwen.harness.tools import (
    CronToolContext,
    create_cron_tools,
)
from openjiuwen.harness.tools.cron import _dispatch_cron_action


class _DummyCronBackend:
    async def list_jobs(self, *, include_disabled: bool = True):
        return []

    async def get_job(self, job_id: str):
        return None

    async def create_job(self, params, *, context=None):
        return {"params": params, "context": context}

    async def update_job(self, job_id, patch, *, context=None):
        return {"job_id": job_id, "patch": patch, "context": context}

    async def delete_job(self, job_id: str):
        return True

    async def toggle_job(self, job_id: str, enabled: bool):
        return {"job_id": job_id, "enabled": enabled}

    async def preview_job(self, job_id: str, count: int = 5):
        return []

    async def run_now(self, job_id: str):
        return "run-1"

    async def status(self):
        return {"ok": True}

    async def get_runs(self, job_id: str, limit: int = 20):
        return []

    async def wake(self, text: str, *, context=None, mode=None):
        return {"text": text, "context": context, "mode": mode}


class CronPromptTimezoneGuidanceTests(unittest.TestCase):
    def test_cron_tool_description_warns_against_rewriting_to_utc(self):
        description = DESCRIPTION["cn"]

        self.assertIn("run_at", description)
        self.assertIn("不改写成 Z 或 UTC", description)

    def test_cron_tool_description_gives_off_hour_default(self):
        """Unspecified-time tasks must default to a randomized off-hour minute."""
        for lang, on_the_hour_cue, off_hour_cue in (
            ("cn", "不要落在整点", "01-29"),
            ("en", "on-the-hour", "01-29"),
        ):
            description = DESCRIPTION[lang]
            self.assertIn(off_hour_cue, description)
            self.assertIn(on_the_hour_cue, description)
        # The field description repeats the rule for schema-first clients.
        cn_field = FIELD_DESCRIPTIONS["cron_expr"]["cn"]
        self.assertIn("避开 00/15/30/45", cn_field)

    def test_cron_tool_description_workbuddy_patterns(self):
        """Decision defaults and content guardrails borrowed from WorkBuddy."""
        cn = DESCRIPTION["cn"]
        en = DESCRIPTION["en"]

        # When in doubt: task + recurring time pattern -> create.
        self.assertIn("拿不准时，只要请求=任务+周期性时间模式，就创建", cn)
        self.assertIn(
            "if the request describes a task plus a recurring time pattern", en
        )
        # Content must be self-sufficient because the user may be unavailable.
        self.assertIn("执行时用户可能不在场", cn)
        self.assertIn("limited availability", en)
        # No "write a file" / "nothing to do" filler instructions.
        self.assertIn("无事可做", cn)
        self.assertIn("nothing to do", en)
        # Blocked fallback: report briefly and stop.
        self.assertIn("简要说明后停止", cn)
        self.assertIn("report briefly and stop", en)
        # Storage is system-managed; the no-shell rule is absolute.
        self.assertIn("这条规则没有例外", cn)
        self.assertIn("This rule is absolute", en)
        # Unspecified fields are preserved on update.
        self.assertIn("未传字段保持不变", cn)
        self.assertIn("unspecified fields are preserved", en)

    def test_cron_tool_description_restored_compact_constraints(self):
        """Pre-simplification constraints restored in compact form."""
        cn = DESCRIPTION["cn"]
        en = DESCRIPTION["en"]

        # */X legal values per field (compact table instead of 5 paragraphs).
        self.assertIn("分→1/2/3/4/5/6/10/12/15/20/30", cn)
        self.assertIn("时→1/2/3/4/6/8/12", cn)
        self.assertIn("月→1/2/3/4/6", cn)
        self.assertIn("minute->1/2/3/4/5/6/10/12/15/20/30", en)
        self.assertIn("hour->1/2/3/4/6/8/12", en)
        # Uneven-gap examples kept (*/40 minutes, */5 hours).
        self.assertIn("*/40 分钟", cn)
        self.assertIn("*/40 minutes", en)
        # 'Every X days' / 'every X weeks' pitfalls kept.
        self.assertIn("'每隔X天'不可靠", cn)
        self.assertIn("'Every X days' is unreliable", en)
        # wake_offset_seconds guard kept as a one-line rule.
        self.assertIn("禁止传 wake_offset_seconds", cn)
        self.assertIn("Do NOT pass wake_offset_seconds", en)

    def test_cron_tool_description_stays_within_token_budget(self):
        """Restored constraints must not blow the token budget.

        Pre-simplification cn description was ~2900 chars; the compact
        restoration should stay well under half of that.
        """
        self.assertLess(len(DESCRIPTION["cn"]), 1600)
        self.assertLess(len(DESCRIPTION["en"]), 3900)

    def test_cron_prompt_metadata_has_no_openclaw_wording(self):
        all_text = "\n".join(DESCRIPTION.values())
        all_text += "\n" + "\n".join(
            text
            for mapping in FIELD_DESCRIPTIONS.values()
            for text in mapping.values()
        )

        self.assertNotIn("OpenClaw", all_text)
        self.assertNotIn("openclaw", all_text)

    def test_build_tool_card_exposes_timezone_guidance(self):
        card = build_tool_card("cron", "cron_test", "cn")

        self.assertEqual(card.name, "cron")
        self.assertIn("run_at", card.description)
        self.assertIn("不改写成 Z 或 UTC", card.description)

    def test_cron_input_params_are_flat_and_minimal(self):
        schema = get_cron_input_params("cn")

        self.assertEqual(
            set(schema["properties"].keys()),
            {
                "action",
                "jobId",
                "name",
                "description",
                "cron_expr",
                "run_at",
                "timezone",
                "targets",
                "enabled",
            },
        )
        self.assertEqual(
            schema["properties"]["action"]["enum"],
            ["list", "add", "update", "remove", "run"],
        )
        # cn/en schemas stay structurally identical.
        self.assertEqual(
            set(get_cron_input_params("en")["properties"].keys()),
            set(schema["properties"].keys()),
        )

    def test_create_cron_tools_supports_unified_entry_only(self):
        tools = create_cron_tools(
            _DummyCronBackend(),
            context=CronToolContext(channel_id="web", session_id="sess-1"),
            include_legacy_compat=False,
        )

        self.assertEqual([tool.card.name for tool in tools], ["cron"])
        self.assertIn("web_sess-1", tools[0].card.id)

    def test_create_cron_tools_ignores_legacy_compat_flag(self):
        """The legacy flag is accepted but always yields only the unified tool."""
        tools = create_cron_tools(
            _DummyCronBackend(),
            context=CronToolContext(channel_id="web", session_id="sess-1"),
            include_legacy_compat=True,
        )

        self.assertEqual([tool.card.name for tool in tools], ["cron"])


class CronDispatchJobIdParamTests(unittest.IsolatedAsyncioTestCase):
    """The tool schema now declares ``jobId`` (matching the dispatcher's named
    param), so remove/update/run receive the job id directly. Historical note:
    the schema previously said ``job_id`` (snake_case), which landed in
    **kwargs and was silently dropped, making remove/update/run raise
    "jobId is required" until the model self-corrected on retry."""

    async def test_remove_accepts_camel_case_job_id(self):
        deleted: list[str] = []

        class _Backend(_DummyCronBackend):
            async def delete_job(self, job_id: str):
                deleted.append(job_id)
                return True

        result = await _dispatch_cron_action(
            _Backend(), action="remove", jobId="abc-123"
        )

        self.assertEqual(result, {"deleted": True})
        self.assertEqual(deleted, ["abc-123"])

    async def test_remove_accepts_legacy_id(self):
        deleted: list[str] = []

        class _Backend(_DummyCronBackend):
            async def delete_job(self, job_id: str):
                deleted.append(job_id)
                return True

        result = await _dispatch_cron_action(
            _Backend(), action="remove", id="abc-123"
        )

        self.assertEqual(result, {"deleted": True})
        self.assertEqual(deleted, ["abc-123"])

    async def test_remove_without_any_job_id_still_raises(self):
        with self.assertRaisesRegex(ValueError, "jobId is required"):
            await _dispatch_cron_action(_DummyCronBackend(), action="remove")

    async def test_update_accepts_job_id_without_leaking_into_patch(self):
        calls: list[tuple[str, dict]] = []

        class _Backend(_DummyCronBackend):
            async def update_job(self, job_id, patch, *, context=None):
                calls.append((job_id, dict(patch)))
                return {"job_id": job_id, "patch": patch}

        await _dispatch_cron_action(
            _Backend(), action="update", jobId="j1", enabled=False
        )

        self.assertEqual(calls, [("j1", {"enabled": False})])

    async def test_run_accepts_job_id(self):
        ran: list[str] = []

        class _Backend(_DummyCronBackend):
            async def run_now(self, job_id: str):
                ran.append(job_id)
                return "run-1"

        result = await _dispatch_cron_action(
            _Backend(), action="run", jobId="j1"
        )

        self.assertEqual(result, {"run_id": "run-1"})
        self.assertEqual(ran, ["j1"])

    def test_schema_declares_job_id_param_as_jobId(self):
        """Schema-compliant param must hit the dispatcher's named argument.

        Guards the original bug: schema used to declare ``job_id`` while the
        dispatcher only accepted ``jobId``, so schema-compliant calls broke.
        """
        schema = get_cron_input_params("cn")
        self.assertIn("jobId", schema["properties"])
        self.assertNotIn("job_id", schema["properties"])


if __name__ == "__main__":
    unittest.main()
