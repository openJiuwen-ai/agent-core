"""Bounded worker-side batches. Executes only caller-selected actions, without inference."""

from __future__ import annotations

import time
from dataclasses import asdict

if __package__:
    from .batch_rules import BatchRules
    from .errors import InputNotStarted
    from .schema import Action
else:
    from batch_rules import BatchRules  # pylint: disable=import-error
    from errors import InputNotStarted  # pylint: disable=import-error
    from schema import Action  # pylint: disable=import-error


# Keep sequential native guards and cleanup visible in one operation.
# pylint: disable-next=too-many-branches,too-many-locals,too-many-statements
def run_batch(desktop, actions, observation, *, max_steps, max_observations, ineffective_clicks=0, wait_for=None):
    """Keep all fresh native checks inside one RPC; transfer only the final image."""
    settings = desktop.settings
    if not settings.window_navigation or not settings.optimized_batches:
        raise ValueError("native batch acceleration is unavailable")
    if not 1 <= len(actions) <= settings.max_batch_actions or len(actions) > max_steps:
        raise ValueError("batch exceeds native action budget")
    # Reserve one final full observation if a metadata-only step stops the batch.
    if max_observations < len(actions) + 1:
        raise ValueError("batch exceeds native observation budget")
    for index, action in enumerate(actions):
        if action.kind in {"launch", "done", "fail"} or (
            index and action.kind in {"focus", "move", "drag", "hold_mouse", "scroll"}
        ):
            raise ValueError("unsupported native batch action")
    current = desktop.last
    if (
        current is None
        or observation.get("generation") != current.generation
        or (observation.get("context") or {}).get("observation_id") != (current.context or {}).get("observation_id")
    ):
        raise RuntimeError("stale desktop observation")
    if wait_for is not None:
        # This private RPC retains the public tool's exact bounded signal contract.
        if (
            set(wait_for) - {"signal", "label", "timeout_seconds"}
            or wait_for.get("signal") not in {"screen_change", "control_enabled"}
            or not 0 <= wait_for.get("timeout_seconds", 2) <= 2
            or (wait_for["signal"] == "control_enabled") != bool(wait_for.get("label"))
        ):
            raise ValueError("invalid readiness condition")
    bindings = BatchRules(current).bind_batch(actions)
    events = []
    completed = steps = observations = 0
    status, reason = "action_completed", ""
    signal_result = None

    def emit(event, **values):
        events.append({"event": event, "at": time.monotonic(), **values})

    def phase(fn):
        desktop.phase_deadline = time.monotonic() + settings.budgets.action_seconds
        try:
            result = fn()
            if time.monotonic() > desktop.phase_deadline:
                raise TimeoutError("native batch phase exceeded action deadline")
            return result
        finally:
            desktop.phase_deadline = None

    def observe(mode):
        nonlocal current, observations
        if observations >= max_observations:
            raise RuntimeError("native observation budget exhausted")
        phase(lambda: desktop.observe(image_mode=mode))
        current = desktop.last
        observations += 1
        emit("observation", **{k: v for k, v in asdict(current).items() if k != "png_b64"})

    def act(action):
        nonlocal steps
        if steps >= max_steps:
            raise RuntimeError("native action budget exhausted")
        emit("action", step=steps, action=action.model_dump(exclude_defaults=True))
        phase(lambda: desktop.act(action, asdict(current)))
        emit("action_completed", step=steps, kind=action.kind)
        steps += 1

    def mode_for(index):
        if index == len(actions) - 1:
            return "full"
        # Exact pixel equality is still used for every click's stall check.
        pixel_needed = actions[index].kind == "click" or actions[index + 1].kind == "click"
        pixel_needed |= bool(wait_for and wait_for["signal"] == "screen_change" and index == len(actions) - 2)
        if pixel_needed or current.accessibility_status != "available":
            return "pixels"
        return "metadata"

    try:
        for index, action in enumerate(actions):
            if index in bindings:
                matches = [e for e in current.elements if BatchRules.control_key(e, current) == bindings[index]]
                if len(matches) != 1:
                    reason = "bound accessibility control changed; inspect before continuing"
                    break
                if action.element_id:
                    action = action.model_copy(update={"element_id": matches[0]["id"]})
            before = current
            mode = mode_for(index)
            act(action)
            completed += 1
            observe(mode)
            unchanged = bool(before.image_digest and before.image_digest == current.image_digest) and all(
                getattr(before, k) == getattr(current, k) for k in ("target", "bounds", "width", "height")
            )
            ineffective_clicks = ineffective_clicks + 1 if action.kind == "click" and unchanged else 0
            if ineffective_clicks >= settings.stall_click_limit:
                status, reason = "stalled", "repeated ineffective clicks with unchanged observations"
                break
            if index + 1 < len(actions) and (
                BatchRules.scene_signature(before) != BatchRules.scene_signature(current)
                or (before.width, before.height) != (current.width, current.height)
                and any(a.x is not None for a in actions[index + 1 :])
            ):
                reason = "window target, geometry or window set changed; inspect before continuing"
                break
        if wait_for and completed == len(actions) and status == "action_completed":
            started = time.monotonic()
            polls, wait_reason = 0, ""

            def met():
                if wait_for["signal"] == "screen_change":
                    return bool(before.image_digest and current.image_digest != before.image_digest)
                return any(
                    e.get("label") == wait_for["label"] and e.get("enabled") and e.get("actionable")
                    for e in current.elements
                )

            while not met():
                remaining = wait_for.get("timeout_seconds", 2) - (time.monotonic() - started)
                if remaining <= 0 or polls >= 8:
                    wait_reason = "signal timeout"
                    break
                if steps >= max_steps or observations >= max_observations - 1:
                    wait_reason = "remaining native action or observation budget exhausted"
                    break
                act(Action(kind="wait", seconds=min(0.1, remaining)))
                polls += 1
                observe("metadata" if wait_for["signal"] == "control_enabled" else "pixels")
            signal_result = dict(
                signal=wait_for["signal"],
                met=met(),
                polls=polls,
                elapsed=time.monotonic() - started,
                reason=wait_reason,
            )
        if not current.png_b64:
            observe("full")
        if signal_result is not None:
            # The final fresh observation can invalidate a previously enabled control.
            previously_met = signal_result["met"]
            signal_result["met"] = met()
            if previously_met and not signal_result["met"]:
                signal_result["reason"] = "signal changed before the final observation"
            emit("wait_signal", **signal_result)
    except Exception as exc:
        status, reason = "failed", f"{type(exc).__name__}: {exc}"
        monitor = getattr(desktop, "monitor", None)
        if monitor is not None and monitor.contaminated.is_set():
            status, reason = "contaminated", f"CONTAMINATED: {monitor.reason}; {reason}"
        try:
            desktop.release_inputs()
            if isinstance(exc, InputNotStarted) and status != "contaminated":
                # The initial guard proved that this action never started.
                # Retain grants, stop the batch, and let the model inspect again.
                observe("full")
                status = "action_completed"
        except Exception as release_error:
            reason += f"; input release failed: {release_error}"
        current = desktop.last
    finally:
        desktop.phase_deadline = None
    return dict(
        status=status,
        reason=reason,
        completed=completed,
        steps=steps,
        observations=observations,
        ineffective_clicks=ineffective_clicks,
        events=events,
        observation=asdict(current) if current is not None else None,
        wait_for=signal_result,
    )
