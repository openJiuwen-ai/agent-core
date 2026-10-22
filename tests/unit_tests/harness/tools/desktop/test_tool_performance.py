"""Session integration, result projection and rollback without host modification."""

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from openjiuwen.harness.tools.desktop import worker
from openjiuwen.harness.tools.desktop.direct import DirectDesktopSession
from openjiuwen.harness.tools.desktop.direct_tool import DirectComputerUseTool
from openjiuwen.harness.tools.desktop.native_batch import run_batch
from openjiuwen.harness.tools.desktop.performance import performance_settings
from openjiuwen.harness.tools.desktop.process import stop_process
from openjiuwen.harness.tools.desktop.schema import Action, DesktopSettings, Observation
from tests.unit_tests.harness.tools.desktop.performance_fixture import PerformanceRig
from tests.unit_tests.harness.tools.desktop.test_direct import Lease
from tests.unit_tests.harness.tools.desktop.test_input_shutdown import HEADLESS_WORKER


class Driver:
    def __init__(self):
        self.rig = PerformanceRig()
        self.closed = False
        self.block = False
        self.entered = asyncio.Event()

    async def start(self):
        pass

    async def observe(self):
        return self.rig.observe()

    async def execute_batch(self, actions, observation, **limits):
        self.entered.set()
        if self.block:
            await asyncio.Event().wait()
        return run_batch(self.rig.d, actions, asdict(observation), **limits)

    async def close(self):
        self.closed = True
        self.rig.d.release_inputs()


@pytest.mark.asyncio
async def test_worker_batch_receipts_final_image_and_release(tmp_path):
    driver, lease = Driver(), Lease()
    settings = driver.rig.d.settings.model_copy(update={"evidence_dir": str(tmp_path)})
    state = DirectDesktopSession(settings, driver_factory=lambda _: driver, lease_factory=lambda: lease)
    await state.execute("observe")
    result = await state.execute_batch(
        [Action(kind="type", text="Draft"), Action(kind="type", text="\nReview")], release_after=True
    )
    assert result["batch"]["completed"] == 2 and result["closed"]
    assert driver.closed and lease.released and driver.rig.released
    assert len(list(state.evidence.glob("*.png"))) == 2
    observations = [e for e in state.trace if e["event"] == "observation"]
    assert observations[-1]["saved_image"] == "003.png"
    assert (state.evidence / observations[-1]["saved_image"]).is_file()
    assert all(e["observation_id"] for e in observations)
    assert [e["step"] for e in state.trace if e["event"] == "action_completed"] == [0, 1]


@pytest.mark.asyncio
async def test_worker_batch_cancellation_reaps_driver_and_releases_lease():
    driver, lease = Driver(), Lease()
    state = DirectDesktopSession(driver.rig.d.settings, driver_factory=lambda _: driver, lease_factory=lambda: lease)
    await state.execute("observe")
    driver.block = True
    task = asyncio.create_task(state.execute_batch([Action(kind="type", text="never entered")]))
    await driver.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state.closed and driver.closed and lease.released
    assert not driver.rig.actions


@pytest.mark.asyncio
async def test_failed_native_batch_reports_partial_progress_and_never_replays():
    driver, lease = Driver(), Lease()
    state = DirectDesktopSession(driver.rig.d.settings, driver_factory=lambda _: driver, lease_factory=lambda: lease)
    await state.execute("observe")

    def fail_second(_):
        if len(driver.rig.actions) == 2:
            raise RuntimeError("simulated partial input")

    driver.rig.after_action = fail_second
    result = await state.execute_batch([Action(kind="type", text=x) for x in ["a", "b", "c"]])
    assert state.closed and result["status"] == "failed" and lease.released
    assert result["batch"]["completed"] == 1
    assert len(driver.rig.actions) == 2 and "simulated partial input" in result["reason"]


def test_compact_render_preserves_content_controls_geometry_and_original_receipt():
    from openjiuwen.core.foundation.tool.schema import ToolOutput

    settings = DesktopSettings()
    tool = DirectComputerUseTool(settings)
    observation = Observation(
        "pixels",
        100,
        90,
        (0, 0, 100, 90),
        "editor",
        12,
        3,
        elements=[{"id": "fresh", "role": "AXTextArea", "value": "Keep all this text"}],
        performance={"total_seconds": 0.25},
        image_digest="private-diagnostic",
    )
    raw = dict(
        status="action_completed",
        observation_id="latest",
        closed=False,
        run_id="internal",
        observation={k: v for k, v in asdict(observation).items() if k != "png_b64"},
    )
    before = json.dumps(raw)
    rendered = json.loads(tool.render_for_llm(ToolOutput(success=True, data=raw)))
    assert json.dumps(raw) == before
    assert rendered["observation"]["elements"] == raw["observation"]["elements"]
    assert rendered["observation"]["bounds"] == [0, 0, 100, 90]
    assert rendered["observation_id"] == "latest"
    assert "performance" not in rendered["observation"] and "image_digest" not in rendered["observation"]


def test_legacy_profile_only_changes_tool_performance_fields(monkeypatch):
    original = DesktopSettings(active_window_screenshots=True, observation_settle_seconds=0.25)
    monkeypatch.setenv("DESKTOP_TOOL_PERFORMANCE", "legacy")
    changed = performance_settings(original)
    differences = {k for k, v in original.model_dump().items() if v != changed.model_dump()[k]}
    assert differences == {
        "optimized_batches",
        "capture_backend",
        "compact_tool_results",
        "active_window_screenshots",
        "observation_settle_seconds",
    }
    assert original.optimized_batches is True
    monkeypatch.setenv("DESKTOP_TOOL_PERFORMANCE", "unknown")
    with pytest.raises(ValueError):
        performance_settings(original)


@pytest.mark.asyncio
async def test_worker_phase_deadline_releases_held_input_without_host_signal(tmp_path):
    script = tmp_path / "deadline_worker.py"
    script.write_text(
        HEADLESS_WORKER.replace(
            "            self.pressed = True\n",
            "            self.pressed = True\n            self.phase_deadline = time.monotonic() + 0.05\n",
        )
    )
    events = tmp_path / "events.jsonl"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        str(Path(worker.__file__).parent),
        str(events),
        "blocked-held-key",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        process.stdin.write(b'{"method":"start","settings":{}}\n')
        await process.stdin.drain()
        assert json.loads(await asyncio.wait_for(process.stdout.readline(), 5))["ok"]
        request = dict(method="act", action=dict(kind="hold_key", keys=["ctrl", "right"], duration=2), observation={})
        process.stdin.write((json.dumps(request) + "\n").encode())
        await process.stdin.drain()
        async with asyncio.timeout(2):
            while True:
                rows = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
                if any(row.get("kind") == "key-up" and row.get("key") == 124 for row in rows):
                    break
                await asyncio.sleep(0.005)
        assert any(row["kind"] == "blocked-focus" for row in rows)
        assert [row["kind"] for row in rows if row.get("key") == 124] == ["key-down", "key-up"]
        assert [row["kind"] for row in rows if row.get("key") == 59] == ["key-down", "key-up"]
    finally:
        process.stdin.close()
        await stop_process(process)
