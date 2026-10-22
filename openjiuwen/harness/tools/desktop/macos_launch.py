"""Explicit approved-bundle launch; no shell, app-specific steps or model calls."""

# Worker collaborators intentionally share private input guards.
# pylint: disable=protected-access

from __future__ import annotations

import threading
import time

if __package__:
    from .macos_session import require_interactive_session
else:
    from macos_session import require_interactive_session  # pylint: disable=import-error


# Keep sequential native guards and cleanup visible in one operation.
# pylint: disable-next=too-many-locals,too-many-statements
def launch_application(navigation, target: str) -> None:
    """Open one host-approved bundle and await an in-scope window within the action budget.

    NSWorkspace owns the GUI application lifetime. Stop reaps our worker, never
    application documents; an already submitted OS request may finish after Stop.
    """
    desktop = navigation.desktop
    spec = desktop.targets.get(target)
    if spec is None or not spec.allow_launch or not spec.bundle_id or spec.pid is not None or spec.window_title:
        raise PermissionError("application launch is outside the host allowlist")
    if not desktop.settings.window_navigation or desktop.settings.execution_mode != "direct":
        raise PermissionError("launch requires direct shared-window navigation")
    # Leave a small margin for the worker to return an explicit error before RPC expiry.
    deadline = time.monotonic() + max(0.01, desktop.settings.budgets.action_seconds - 0.25)

    def check():
        desktop._inputs().check_stop()
        require_interactive_session(desktop.q)
        if getattr(desktop, "monitor", None):
            desktop.monitor.check()
        if time.monotonic() >= deadline:
            raise TimeoutError("application launch did not produce a window on the selected display in time")

    def tick():
        check()
        desktop.appkit.NSRunLoop.currentRunLoop().runUntilDate_(
            desktop.appkit.NSDate.dateWithTimeIntervalSinceNow_(0.025)
        )
        check()

    check()
    # Launch permission never grants access to a pre-existing app's documents.
    existing = desktop.application(target, allow_missing=True)
    if existing is not None:
        if target not in navigation.instances:
            raise PermissionError("application is already running; explicitly share its window before use")
        navigation.bind(target, existing)
        navigation.focus(target=target)
        return
    if target in navigation.instances:
        raise RuntimeError("approved application instance changed; start a new session")
    workspace = desktop.appkit.NSWorkspace.sharedWorkspace()
    url = workspace.URLForApplicationWithBundleIdentifier_(spec.bundle_id)
    if url is None:
        raise FileNotFoundError("approved application is not installed or registered")
    bundle = desktop.appkit.NSBundle.bundleWithURL_(url)
    if bundle is None or bundle.bundleIdentifier() != spec.bundle_id:
        raise PermissionError("resolved application does not match the approved bundle ID")
    config = desktop.appkit.NSWorkspaceOpenConfiguration.configuration()
    config.setActivates_(True)
    config.setCreatesNewApplicationInstance_(True)
    config.setAllowsRunningApplicationSubstitution_(False)
    config.setAddsToRecentItems_(False)
    if existing is None and spec.launch_arguments:
        config.setArguments_(list(spec.launch_arguments))
    ready = threading.Event()
    result = []

    def completed(app, error):
        # Apple invokes this on a concurrent queue. Native binding happens below
        # on the worker thread, after observing the event's memory barrier.
        result.extend((app, error))
        ready.set()

    check()
    navigation.applications()  # Do not reopen an instance that died during resolution.
    workspace.openApplicationAtURL_configuration_completionHandler_(url, config, completed)
    while not ready.is_set():
        tick()
    check()
    app, error = result[0], result[1]
    if error is not None or app is None:
        raise RuntimeError("native application launch failed" + (f": {error}" if error is not None else ""))
    if app.bundleIdentifier() != spec.bundle_id:
        raise PermissionError("launched application does not match the approved bundle ID")
    identity = navigation.identity(app)
    # Cocoa's running-app catalog may lag the asynchronous completion callback.
    # Wait for this exact instance to become discoverable, without accepting a
    # replacement or treating a launch callback as permission to skip identity checks.
    while True:
        check()
        resolved = desktop.application(target, allow_missing=True)
        if resolved is not None:
            if navigation.identity(resolved) != identity:
                raise RuntimeError("launched application instance changed before window discovery")
            navigation.sharing.pending_launches.add(target)
            navigation.bind(target, resolved)
            break
        if app.isTerminated():
            raise RuntimeError("launched application exited before window discovery")
        tick()
    desktop.target = target
    # OS launch success alone is not a usable-window success. Refresh continuously
    # checks process instances and the selected display, without posting any input.
    while True:
        check()
        navigation.refresh()
        pid = navigation.instances[target][0]
        windows = [
            handle
            for handle in navigation.catalog.windows
            if navigation.catalog.resolve(handle).pid == pid and navigation.catalog.resolve(handle).layer == 0
        ]
        focus_ready = desktop.accessibility is None or any(
            tuple(navigation.catalog.resolve(handle).rect.model_dump().values()) == navigation.focused_rect
            for handle in windows
        )
        if windows and navigation.front_pid == pid and focus_ready:
            return
        tick()
