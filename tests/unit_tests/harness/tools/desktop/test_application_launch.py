"""Approved launch through real tool/session/navigation paths, with fake OS boundaries."""

import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from test_direct import Desktop, Lease
from test_window_navigation import NativeRig, row

from openjiuwen.core.session.agent import Session
from openjiuwen.harness.tools.desktop import worker
from openjiuwen.harness.tools.desktop.direct import DirectDesktopSession
from openjiuwen.harness.tools.desktop.direct_protocol import decode_call, input_schema
from openjiuwen.harness.tools.desktop.direct_tool import DirectComputerUseTool
from openjiuwen.harness.tools.desktop.macos_navigation import MacOSNavigation
from openjiuwen.harness.tools.desktop.schema import Action, DesktopSettings, Target
from openjiuwen.harness.tools.desktop.session_contract import DesktopObservationContext


def settings(**updates):
    return DesktopSettings.model_validate(
        {
            "enabled": True,
            "execution_mode": "direct",
            "window_navigation": True,
            "targets": [{"name": "editor", "bundle_id": "test.editor", "allow_launch": True}],
            **updates,
        }
    )


class LaunchRig(NativeRig):
    """No actual frameworks, apps, input, screenshots or inference are used."""

    def __init__(self, *, running=False):
        super().__init__()
        self.running = running
        self.registered = True
        self.rows = [row(1)] if running else []
        self.front = 10 if running else 0
        self.requests = []
        self.launch_error = None
        self.return_wrong_bundle = False
        self.callback_pending = self.window_pending = False
        self.on_tick = lambda: None
        self.tick_count = 0
        self.d.settings = settings()
        self.d.targets = {t.name: t for t in self.d.settings.targets}
        self.apps[10].bundleIdentifier = lambda: "wrong" if self.return_wrong_bundle else "test.editor"
        self.apps[10].terminate = Mock(side_effect=AssertionError("must not terminate GUI app"))
        self.apps[10].forceTerminate = Mock(side_effect=AssertionError("must not kill GUI app"))
        self.d.appkit.NSRunningApplication = NS(
            runningApplicationsWithBundleIdentifier_=lambda _: (
                [self.apps[10]] if self.running and self.registered else []
            )
        )
        self.d.application = worker.NativeDesktop.application.__get__(self.d)
        self.config = NS(
            **{
                name: Mock()
                for name in (
                    "setActivates_",
                    "setCreatesNewApplicationInstance_",
                    "setAllowsRunningApplicationSubstitution_",
                    "setAddsToRecentItems_",
                    "setArguments_",
                )
            }
        )
        self.workspace = NS(
            frontmostApplication=lambda: self.apps.get(self.front),
            URLForApplicationWithBundleIdentifier_=Mock(return_value="approved-url"),
            openApplicationAtURL_configuration_completionHandler_=self.open,
        )
        self.d.appkit.NSWorkspace = NS(sharedWorkspace=lambda: self.workspace)
        self.d.appkit.NSBundle = NS(bundleWithURL_=Mock(return_value=NS(bundleIdentifier=lambda: "test.editor")))
        self.d.appkit.NSWorkspaceOpenConfiguration = NS(configuration=lambda: self.config)
        self.d.appkit.NSRunLoop = NS(currentRunLoop=lambda: NS(runUntilDate_=lambda _: self.tick()))
        self.d.appkit.NSDate = NS(dateWithTimeIntervalSinceNow_=lambda value: value)
        self.d.navigation = MacOSNavigation(self.d)
        self.d.navigation.capture_window = self.capture

    def open(self, url, config, callback):
        self.requests.append((url, config))
        self.callback = callback
        if self.launch_error:
            callback(None, self.launch_error)
            return
        self.running, self.front = True, 10
        if not self.window_pending:
            self.rows = [row(1)]
        if not self.callback_pending:
            callback(self.apps[10], None)

    def tick(self):
        if not self.requests:
            return  # Launch callbacks cannot run before a launch request registered them.
        self.tick_count += 1
        self.on_tick()


@pytest.fixture
def launch_rig():
    worker.STOPPING.clear()
    yield LaunchRig()
    worker.STOPPING.clear()


@pytest.mark.parametrize(
    "target",
    [
        {"name": "x", "pid": 10, "allow_launch": True},
        {"name": "x", "pid": 10, "bundle_id": "test.editor", "allow_launch": True},
        {"name": "x", "bundle_id": "test.editor", "window_title": "one", "allow_launch": True},
    ],
)
def test_launch_requires_explicit_bundle_wide_identity(target):
    with pytest.raises(ValueError, match="allow_launch"):
        Target.model_validate(target)


@pytest.mark.parametrize("updates", [{"window_navigation": False}, {"execution_mode": "delegated"}])
def test_launch_scope_cannot_silently_expand_legacy_mode(updates):
    with pytest.raises(ValueError, match="direct"):
        settings(**updates)


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"target": "editor", "path": [[1, 2], [3, 4]]},
        {"target": "editor", "window_id": "w"},
        {"target": "editor", "url": "https://example.com"},
        {"target": "editor", "args": ["--script"]},
        {"target": "editor", "text": "do a task"},
    ],
)
def test_launch_call_rejects_missing_target_and_extra_input(fields):
    with pytest.raises(ValueError):
        decode_call({"operation": "launch", "observation_id": "fresh", **fields})


def test_launch_flat_and_legacy_protocol_require_observation():
    assert "launch" in input_schema()["properties"]["operation"]["enum"]
    flat = decode_call({"operation": "launch", "target": "editor", "observation_id": "fresh"})
    nested = decode_call(
        {"operation": "act", "action": {"kind": "launch", "target": "editor"}, "observation_id": "fresh"}
    )
    assert flat == nested == ("act", Action(kind="launch", target="editor"))
    with pytest.raises(ValueError, match="observation_id"):
        decode_call({"operation": "launch", "target": "editor"})


def test_empty_observation_does_not_launch_or_capture_foreign_windows(launch_rig):
    rig = launch_rig
    rig.rows = [row(99, pid=99)]
    observed = rig.observe()
    context = DesktopObservationContext.model_validate_json(json.dumps(observed.context))
    assert context.windows == () and rig.d.navigation.scope.application_ids == ()
    rig.d.navigation.scope.validate_observation(context)
    assert not rig.requests and not rig.captures and not rig.posts


def test_launch_permission_never_adopts_a_preexisting_application():
    with pytest.raises(PermissionError, match="explicitly share"):
        LaunchRig(running=True)


def test_launch_with_multiple_initial_windows_requires_explicit_sharing(launch_rig):
    rig = launch_rig
    original = rig.open

    def open_multiple(*args):
        original(*args)
        rig.rows.append(row(2, bounds=(200, 30, 150, 100)))

    rig.workspace.openApplicationAtURL_configuration_completionHandler_ = open_multiple
    with pytest.raises(PermissionError, match="multiple windows"):
        rig.act(Action(kind="launch", target="editor"), rig.observe())
    assert not rig.posts and not rig.captures


def test_closed_app_launch_pins_process_and_returns_fresh_window(launch_rig):
    rig = launch_rig
    before = rig.observe()
    rig.act(Action(kind="launch", target="editor"), before)
    assert rig.d.last is None  # launch consumed the previous native snapshot
    after = rig.observe()
    assert len(after.context["windows"]) == 1
    assert after.context["windows"][0]["application_name"] == "editor"
    assert before.context["observation_id"] != after.context["observation_id"]
    assert rig.d.navigation.instances["editor"] == (10, 1.0)
    assert rig.d.monitor_pids == frozenset({10})
    assert len(rig.requests) == 1 and rig.requests[0][0] == "approved-url"
    rig.config.setCreatesNewApplicationInstance_.assert_called_once_with(True)
    rig.config.setActivates_.assert_called_once_with(True)
    assert not rig.posts


def test_running_app_reuses_bound_instance_and_window_handle():
    rig = LaunchRig()
    rig.act(Action(kind="launch", target="editor"), rig.observe())
    before = rig.observe()
    application_id = before.context["windows"][0]["application_id"]
    rig.act(Action(kind="launch", target="editor"), before)
    after = rig.observe()
    assert after.context["windows"][0]["application_id"] == application_id
    assert after.context["windows"][0]["window_id"] == before.context["windows"][0]["window_id"]
    assert rig.running and len(rig.d.navigation.instances) == 1
    rig.apps[10].terminate.assert_not_called()
    rig.apps[10].forceTerminate.assert_not_called()


def test_app_started_externally_after_first_observation_is_not_adopted(launch_rig):
    rig = launch_rig
    rig.observe()
    rig.running = True
    rig.rows = [row(1)]
    fresh = rig.observe()
    assert fresh.context["windows"] == []  # not bound merely by observing
    with pytest.raises(PermissionError, match="explicitly share"):
        rig.act(Action(kind="launch", target="editor"), fresh)
    assert not rig.observe().context["windows"] and not rig.captures


@pytest.mark.parametrize("failure", ["missing", "wrong_bundle", "native_error", "wrong_return"])
def test_launch_errors_do_not_authorize_input(launch_rig, failure):
    rig = launch_rig
    if failure == "missing":
        rig.workspace.URLForApplicationWithBundleIdentifier_.return_value = None
    elif failure == "wrong_bundle":
        rig.d.appkit.NSBundle.bundleWithURL_.return_value = NS(bundleIdentifier=lambda: "wrong")
    elif failure == "native_error":
        rig.launch_error = "OS rejected request"
    else:
        rig.return_wrong_bundle = True
    with pytest.raises((FileNotFoundError, PermissionError, RuntimeError)):
        rig.act(Action(kind="launch", target="editor"), rig.observe())
    assert rig.d.last is None and not rig.posts
    assert not rig.d.navigation.instances
    if failure in {"missing", "wrong_bundle"}:
        assert not rig.requests


@pytest.mark.parametrize("stage", ["callback", "window"])
def test_asynchronous_startup_waits_for_callback_and_visible_window(launch_rig, stage):
    rig = launch_rig
    rig.callback_pending = stage == "callback"
    rig.window_pending = stage == "window"

    def ready():
        if rig.callback_pending:
            rig.callback(rig.apps[10], None)
        rig.rows = [row(1)]

    rig.on_tick = ready
    rig.act(Action(kind="launch", target="editor"), rig.observe())
    assert rig.tick_count == 1 and rig.observe().context["windows"]


@pytest.mark.parametrize("failure", ["timeout", "off_display", "locked", "stop", "external", "disconnect"])
def test_launch_wait_is_bounded_and_checks_session_guards(launch_rig, failure):
    rig = launch_rig
    rig.window_pending = True
    rig.d.settings = settings(budgets={"action_seconds": 0.26})
    if failure == "off_display":
        rig.on_tick = lambda: setattr(rig, "rows", [row(1, bounds=(900, 30, 150, 100))])
    elif failure == "locked":
        rig.on_tick = lambda: setattr(rig, "locked", True)
    elif failure == "stop":
        rig.on_tick = worker.STOPPING.set
    elif failure == "external":
        rig.d.monitor = NS(check=Mock(), record=Mock())
        rig.on_tick = lambda: setattr(rig.d.monitor.check, "side_effect", RuntimeError("CONTAMINATED"))
    elif failure == "disconnect":
        rig.on_tick = lambda: setattr(rig, "displays", [])
    with pytest.raises((RuntimeError, PermissionError, TimeoutError)):
        rig.act(Action(kind="launch", target="editor"), rig.observe())
    assert not rig.posts and rig.d.last is None


def test_timed_out_callback_cannot_bind_or_post_late_input(launch_rig):
    rig = launch_rig
    rig.callback_pending = True
    rig.d.settings = settings(budgets={"action_seconds": 0.26})
    with pytest.raises(TimeoutError):
        rig.act(Action(kind="launch", target="editor"), rig.observe())
    rig.callback(rig.apps[10], None)
    assert not rig.d.navigation.instances and not rig.posts


def test_launch_waits_for_cocoa_application_registration(launch_rig):
    rig = launch_rig
    rig.registered = False
    rig.on_tick = lambda: setattr(rig, "registered", True)
    rig.act(Action(kind="launch", target="editor"), rig.observe())
    assert rig.tick_count == 1 and rig.observe().context["windows"]


def test_returned_launch_instance_must_match_registered_process(launch_rig):
    rig = launch_rig
    rig.registered = False
    rig.on_tick = lambda: (setattr(rig, "registered", True), rig.launches.update({10: 2.0}))
    with pytest.raises(RuntimeError, match="instance changed"):
        rig.act(Action(kind="launch", target="editor"), rig.observe())
    assert not rig.d.navigation.instances and not rig.posts


def test_restarted_bound_app_cannot_be_relaunched():
    rig = LaunchRig()
    rig.act(Action(kind="launch", target="editor"), rig.observe())
    rig.requests.clear()
    before = rig.observe()
    rig.launches[10] = 9.0
    with pytest.raises(RuntimeError, match="instance changed"):
        rig.act(Action(kind="launch", target="editor"), before)
    assert not rig.requests


def test_unapproved_target_rejected_in_native_executor(launch_rig):
    rig = launch_rig
    with pytest.raises(PermissionError, match="allowlist"):
        rig.act(Action(kind="launch", target="other"), rig.observe())
    assert not rig.requests


def test_unapproved_launch_of_running_target_rejected_in_worker():
    rig = LaunchRig()
    rig.act(Action(kind="launch", target="editor"), rig.observe())
    rig.requests.clear()
    rig.d.targets["editor"] = Target(name="editor", bundle_id="test.editor")
    with pytest.raises(PermissionError, match="allowlist"):
        rig.act(Action(kind="launch", target="editor"), rig.observe())
    assert not rig.requests


def test_empty_scope_cannot_authorize_windows_from_another_session(launch_rig):
    rig = launch_rig
    empty_scope = rig.d.navigation.scope
    rig.act(Action(kind="launch", target="editor"), rig.observe())
    context = DesktopObservationContext.model_validate_json(json.dumps(rig.observe().context))
    with pytest.raises(PermissionError, match="outside the session scope"):
        empty_scope.validate_observation(context)


def test_worker_initialization_with_all_apps_closed_does_not_launch(monkeypatch, launch_rig, tmp_path):
    import sys

    rig = launch_rig
    for name, module in {
        "AppKit": rig.d.appkit,
        "ApplicationServices": rig.d.ax,
        "Quartz": rig.d.q,
        "CoreFoundation": NS(CFEqual=lambda x, y: x == y),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(worker, "capability", lambda _: {"available": True, "display_ids": [7]})
    monitor = NS(start=Mock(), check=Mock())
    monkeypatch.setattr(worker, "InputMonitor", lambda *args, **kwargs: monitor)
    desktop = worker.NativeDesktop(settings(stop_on_external_input=True, evidence_dir=str(tmp_path)))
    assert desktop.monitor_pids == frozenset() and desktop.monitor is monitor
    assert desktop.navigation.instances == {} and not rig.requests
    monitor.start.assert_called_once_with()


@pytest.mark.asyncio
async def test_tool_rejects_launch_without_host_opt_in_before_consuming_token():
    from test_direct import setup

    tool, state, driver, _ = setup()
    owner = Session("owner")
    try:
        await tool.invoke({"operation": "observe"}, session=owner)
        token = state.token
        result = await tool.invoke({"operation": "launch", "target": "notes", "observation_id": token}, session=owner)
        assert not result.success and "not approved" in result.error
        assert not driver.actions and state.token == token and not state.closed
    finally:
        await tool.close()


def test_stale_native_observation_cannot_launch(launch_rig):
    rig = launch_rig
    old = rig.observe()
    rig.observe()
    with pytest.raises(RuntimeError, match="stale"):
        rig.act(Action(kind="launch", target="editor"), old)
    assert not rig.requests


@pytest.mark.asyncio
async def test_real_tool_session_launch_roundtrip(launch_rig):
    rig = launch_rig

    class Driver:
        closed = False

        async def start(self):
            pass

        async def observe(self):
            return rig.observe()

        async def act(self, action, observation):
            rig.act(action, observation)

        async def close(self):
            self.closed = True

    driver, lease = Driver(), Lease()
    state = DirectDesktopSession(rig.d.settings, driver_factory=lambda _: driver, lease_factory=lambda: lease)
    tool = DirectComputerUseTool(rig.d.settings, session_factory=lambda: state)
    owner = Session("owner")
    try:
        observed = await tool.invoke({"operation": "observe"}, session=owner)
        assert observed.success and observed.data["available_targets"][0]["allow_launch"]
        token = state.token
        for target, fresh, session in [
            ("other", token, owner),
            ("editor", "stale", owner),
            ("editor", token, Session("foreign")),
        ]:
            result = await tool.invoke(
                {"operation": "launch", "target": target, "observation_id": fresh}, session=session
            )
            assert not result.success and not rig.requests and state.token == token
        result = await tool.invoke(
            {"operation": "launch", "target": "editor", "observation_id": token, "release_after": True}, session=owner
        )
        assert result.success and state.steps == 1 and result.data["model_calls"] == 0
        assert result.data["observation"]["context"]["windows"] and result.data["closed"]
        assert driver.closed and lease.released and rig.running
    finally:
        await tool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["cancel", "timeout"])
async def test_launch_uses_existing_cancellation_deadline_and_cleanup(stop):
    config = settings(budgets={"action_seconds": 0.05})
    driver, lease = Desktop(), Lease()
    driver.block_action = True
    state = DirectDesktopSession(config, driver_factory=lambda _: driver, lease_factory=lambda: lease)
    tool = DirectComputerUseTool(config, session_factory=lambda: state)
    owner = Session("owner")
    try:
        await tool.invoke({"operation": "observe"}, session=owner)
        task = asyncio.create_task(
            tool.invoke({"operation": "launch", "target": "editor", "observation_id": state.token}, session=owner)
        )
        await asyncio.wait_for(driver.entered.wait(), 1)
        if stop == "cancel":
            await tool.cancel_session("owner")
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert not (await task).success
        assert state.closed and driver.closed and lease.released and state.token is None
    finally:
        await tool.close()


@pytest.mark.parametrize("arguments", [["x"] * 17, ["x" * 4097], ["a\x00b"]])
def test_host_startup_arguments_are_bounded(arguments):
    with pytest.raises(ValueError):
        Target(name="editor", bundle_id="test.editor", allow_launch=True, launch_arguments=arguments)


def test_host_startup_arguments_require_launch_permission():
    with pytest.raises(ValueError, match="launch_arguments"):
        Target(name="editor", bundle_id="test.editor", launch_arguments=["--profile"])


@pytest.mark.parametrize("running", [False, True])
def test_host_arguments_apply_only_to_a_cold_approved_launch(running):
    rig = LaunchRig()
    if running:
        rig.act(Action(kind="launch", target="editor"), rig.observe())
    rig.d.settings = settings(
        targets=[
            {
                "name": "editor",
                "bundle_id": "test.editor",
                "allow_launch": True,
                "launch_arguments": ["--fixed-profile", "/host/prepared/input"],
            }
        ]
    )
    rig.d.targets = {t.name: t for t in rig.d.settings.targets}
    rig.act(Action(kind="launch", target="editor"), rig.observe())
    if running:
        rig.config.setArguments_.assert_not_called()
    else:
        rig.config.setArguments_.assert_called_once_with(["--fixed-profile", "/host/prepared/input"])


def test_ax_enabled_launch_waits_for_editable_native_window_focus(launch_rig):
    rig = launch_rig
    before = rig.observe()
    rig.d.accessibility = NS(clear=Mock())
    rig.hit_error, rig.hit_pid = 0, 10
    rig.focused[10] = None
    rig.on_tick = lambda: rig.focused.update({10: 1})
    rig.act(Action(kind="launch", target="editor"), before)
    assert rig.tick_count >= 1
    assert rig.d.navigation.focused_rect == (10, 30, 150, 100)
    assert not rig.posts


def test_ax_focus_readiness_uses_same_launch_deadline(launch_rig):
    rig = launch_rig
    before = rig.observe()
    rig.d.settings = settings(budgets={"action_seconds": 0.26})
    rig.d.accessibility = NS(clear=Mock())
    rig.hit_error, rig.hit_pid = 0, 10
    rig.focused[10] = None
    with pytest.raises(TimeoutError):
        rig.act(Action(kind="launch", target="editor"), before)
    assert not rig.posts
