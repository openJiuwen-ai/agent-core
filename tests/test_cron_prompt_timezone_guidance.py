import unittest

from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.prompts.tools.cron import (
    DESCRIPTION,
    FIELD_DESCRIPTIONS,
)
from openjiuwen.harness.tools import (
    CronToolContext,
    create_cron_tools,
)


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

        self.assertIn("schedule.at", description)
        self.assertIn("不要改写成 Z 或 UTC", description)
        self.assertIn("sessionTarget=current", description)
        self.assertNotIn("OpenClaw", description)
        self.assertNotIn("openclaw", description)

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
        self.assertIn("schedule.at", card.description)
        self.assertIn("sessionTarget=current", card.description)

    def test_create_cron_tools_supports_unified_entry_only(self):
        tools = create_cron_tools(
            _DummyCronBackend(),
            context=CronToolContext(channel_id="web", session_id="sess-1"),
            include_legacy_compat=False,
        )

        self.assertEqual([tool.card.name for tool in tools], ["cron"])
        self.assertIn("web_sess-1", tools[0].card.id)

    def test_create_cron_tools_can_keep_legacy_compat_entries(self):
        tools = create_cron_tools(
            _DummyCronBackend(),
            context=CronToolContext(channel_id="web", session_id="sess-1"),
            include_legacy_compat=True,
        )

        names = [tool.card.name for tool in tools]
        self.assertIn("cron", names)
        self.assertIn("cron_list_jobs", names)
        self.assertIn("cron_create_job", names)
        self.assertIn("cron_preview_job", names)


if __name__ == "__main__":
    unittest.main()
