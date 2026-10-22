"""Bounded native sessions driven entirely by the host model's tool calls. No inference."""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .batch_rules import BatchRules
from .direct_wait import ReadinessWait, wait_for_signal
from .driver import MacOSProcessDriver
from .errors import InputNotStarted
from .ownership import DesktopLease
from .performance import performance_settings
from .process import finish_cleanup
from .schema import Action, Observation


class DirectDesktopSession(BatchRules):
    """Retain one worker/lease; close on input failure, cancellation or deadline."""

    def __init__(self, settings, *, driver_factory=None, lease_factory=DesktopLease):
        settings = performance_settings(settings)
        self.settings = settings
        self.driver_factory = driver_factory or (lambda fd: MacOSProcessDriver(settings, inherited_lease_fd=fd))
        self.lease = lease_factory()
        self.driver = None
        self.run_id = uuid.uuid4().hex
        self.started = None
        self.steps = self.observations = 0
        self.ineffective_clicks = 0
        self.token = None
        super().__init__(None)
        self.closed = False
        self.status, self.reason = "new", ""
        self.active_task = self.watchdog = self.cleanup_task = None
        self.expiring = False
        self.evidence = None
        self.trace = []

    def emit(self, event, **values):
        """Append a receipt and optional local evidence record."""
        record = {
            "event": event,
            "run_id": self.run_id,
            "elapsed": time.monotonic() - self.started if self.started else 0,
            **values,
        }
        self.trace.append(record)
        if self.evidence:
            with (self.evidence / "trace.jsonl").open("a") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def start(self):
        """Acquire the lease and start one bounded worker."""
        self.started = time.monotonic()
        self.lease.acquire()
        if self.settings.evidence_dir:
            self.evidence = Path(self.settings.evidence_dir) / self.run_id
            self.evidence.mkdir(parents=True, exist_ok=False)
        self.driver = self.driver_factory(self.lease.fd)
        self.watchdog = asyncio.create_task(self._expire())
        await asyncio.wait_for(self.driver.start(), self.settings.budgets.action_seconds)
        self.emit("started", execution_mode="direct", model_calls=0)

    async def _expire(self):
        await asyncio.sleep(max(0, self.settings.budgets.wall_seconds - (time.monotonic() - self.started)))
        self.expiring = True
        if self.active_task is not None and not self.active_task.done():
            self.active_task.cancel()
            await asyncio.gather(self.active_task, return_exceptions=True)
        await self.close("expired", "absolute desktop lifetime expired")

    async def capture(self):
        """Capture a fresh image and advance the observation token."""
        if self.observations >= self.settings.budgets.max_steps + 1:
            raise RuntimeError("observation limit reached")
        self.observation = await asyncio.wait_for(self.driver.observe(), self.settings.budgets.action_seconds)
        self.observations += 1
        self.token = (self.observation.context or {}).get("observation_id") or uuid.uuid4().hex
        metadata = {key: value for key, value in asdict(self.observation).items() if key != "png_b64"}
        self.emit("observation", observation_id=self.token, **metadata)
        if self.evidence:
            (self.evidence / f"{self.observations:03d}.png").write_bytes(base64.b64decode(self.observation.png_b64))

    async def execute(self, operation, *, action=None, release_after=False):
        """Caller validates ownership/token before entry; one native action consumes one observation."""
        if self.closed:
            raise RuntimeError("desktop session is closed; explicitly observe to start a new session")
        self.active_task = asyncio.current_task()
        try:
            if self.started is None:
                await self.start()
            async with asyncio.timeout(max(0, self.settings.budgets.wall_seconds - (time.monotonic() - self.started))):
                if operation == "act":
                    if self.steps >= self.settings.budgets.max_steps:
                        raise RuntimeError("native action limit reached")
                    observation = self.observation
                    self.token = None  # consumed before input; a failed/partial action is never replayed
                    self.emit("action", step=self.steps, action=action.model_dump(exclude_defaults=True))
                    try:
                        await asyncio.wait_for(
                            self.driver.act(action, observation), self.settings.budgets.action_seconds
                        )
                    except InputNotStarted as exc:
                        await self.capture()
                        self.status, self.reason = "observed", str(exc)
                        self.emit("input_not_started", step=self.steps, reason=self.reason)
                        result = self.result(include_image=True)
                        result["action_applied"] = False
                        if release_after:
                            await self.close("closed", "released after unapplied action")
                            result.update(status="closed", closed=True, observation_id=None)
                        return result
                    self.emit("action_completed", step=self.steps, kind=action.kind)
                    self.steps += 1
                await self.capture()
                if operation == "act":
                    unchanged = all(
                        getattr(observation, name) == getattr(self.observation, name)
                        for name in ("target", "bounds", "width", "height", "png_b64")
                    )
                    self.ineffective_clicks = self.ineffective_clicks + 1 if action.kind == "click" and unchanged else 0
                    if self.ineffective_clicks >= self.settings.stall_click_limit:
                        await self.close("stalled", "repeated ineffective clicks with unchanged observations")
                        return self.result(include_image=True)
                self.status = "observed" if operation == "observe" else "action_completed"
                self.reason = ""
                result = self.result(include_image=True)
                if release_after:
                    await self.close("closed", "released after requested action")
                    result.update(status="closed", closed=True, observation_id=None)
                return result
        except asyncio.CancelledError:
            await self.close("expired" if self.expiring else "cancelled", "desktop input cancelled")
            raise
        except Exception as exc:
            expired = (
                isinstance(exc, TimeoutError) and time.monotonic() - self.started >= self.settings.budgets.wall_seconds
            )
            status = "expired" if expired else "contaminated" if "CONTAMINATED" in str(exc) else "failed"
            await self.close(status, f"{type(exc).__name__}: {exc}")
            return self.result()
        finally:
            self.active_task = None

    # Keep sequential native guards and cleanup visible in one operation.
    # pylint: disable-next=too-many-locals
    async def execute_batch(
        self, actions: list[Action], *, release_after: bool = False, wait_for: ReadinessWait | None = None
    ) -> dict:
        """Serial primitives with fresh native checks and one host-visible final image."""
        bindings = self.bind_batch(actions)
        if (
            self.settings.optimized_batches
            and self.settings.window_navigation
            and callable(getattr(self.driver, "execute_batch", None))
            and self.settings.budgets.max_steps + 1 - self.observations >= len(actions) + 1
        ):
            return await self._execute_worker_batch(actions, release_after=release_after, wait_for=wait_for)
        initial_steps = self.steps
        self.emit("batch_started", requested=len(actions))
        stop_reason = ""
        result = None
        for index, action in enumerate(actions):
            if index in bindings:
                matches = [
                    e for e in self.observation.elements if self.control_key(e, self.observation) == bindings[index]
                ]
                if len(matches) != 1:
                    stop_reason = "bound accessibility control changed; inspect before continuing"
                    break
                if action.element_id:
                    action = action.model_copy(update={"element_id": matches[0]["id"]})
            last_before = self.observation
            previous_scene = self.scene_signature(self.observation)
            result = await self.execute("act", action=action)
            if result.get("action_applied") is False:
                stop_reason = result["reason"]
                break
            if self.closed:
                stop_reason = self.reason
                break
            raster_changed = (last_before.width, last_before.height) != (
                self.observation.width,
                self.observation.height,
            )
            remaining_coordinates = any(a.x is not None for a in actions[index + 1 :])
            if index + 1 < len(actions) and (
                self.scene_signature(self.observation) != previous_scene or raster_changed and remaining_coordinates
            ):
                stop_reason = "window target, geometry or window set changed; inspect before continuing"
                break
        completed = self.steps - initial_steps
        if wait_for is not None and completed == len(actions) and not self.closed:
            result, signal = await wait_for_signal(self, wait_for, before=last_before)
            result["wait_for"] = signal
        result["batch"] = {
            "requested": len(actions),
            "completed": completed,
            "remaining": len(actions) - completed,
            "stop_reason": stop_reason,
        }
        self.emit("batch_completed", **result["batch"])
        if release_after and not self.closed:
            await self.close("closed", "released after requested batch")
            result.update(status="closed", closed=True, observation_id=None)
        return result

    async def _execute_worker_batch(self, actions, *, release_after=False, wait_for=None):
        """Keep the normal host tool call boundary; only native RPC work is grouped."""
        if self.closed:
            raise RuntimeError("desktop session is closed")
        self.active_task = asyncio.current_task()
        initial_steps = self.steps
        self.emit("batch_started", requested=len(actions), executor="native_worker")
        observation, self.token = self.observation, None
        try:
            remaining = max(0, self.settings.budgets.wall_seconds - (time.monotonic() - self.started))
            receipt = await asyncio.wait_for(
                self.driver.execute_batch(
                    actions,
                    observation,
                    max_steps=self.settings.budgets.max_steps - self.steps,
                    max_observations=self.settings.budgets.max_steps + 1 - self.observations,
                    ineffective_clicks=self.ineffective_clicks,
                    wait_for=wait_for.model_dump() if wait_for else None,
                ),
                min(remaining, (2 * len(actions) + 18) * self.settings.budgets.action_seconds),
            )
            final_observation = receipt["observation"]
            for event in receipt["events"]:
                values = {k: v for k, v in event.items() if k not in {"event", "at"}}
                if event["event"] == "observation":
                    values["observation_id"] = (values.get("context") or {}).get("observation_id")
                    if (
                        self.evidence
                        and final_observation
                        and final_observation.get("png_b64")
                        and values.get("generation") == final_observation["generation"]
                    ):
                        values["saved_image"] = f"{self.observations + receipt['observations']:03d}.png"
                if "step" in values:
                    values["step"] += initial_steps
                self.emit(event["event"], elapsed=event["at"] - self.started, **values)
            self.steps += receipt["steps"]
            self.observations += receipt["observations"]
            self.ineffective_clicks = receipt["ineffective_clicks"]
            self.status, self.reason = receipt["status"], receipt["reason"]
            data = final_observation
            self.observation = Observation(**{**data, "bounds": tuple(data["bounds"])}) if data else None
            if self.observation is not None and self.status == "action_completed":
                self.token = (self.observation.context or {}).get("observation_id")
            if self.evidence and self.observation and self.observation.png_b64:
                (self.evidence / f"{self.observations:03d}.png").write_bytes(base64.b64decode(self.observation.png_b64))
            if self.status != "action_completed":
                await self.close(self.status, self.reason)
            result = self.result(include_image=bool(self.observation and self.observation.png_b64))
            result["batch"] = dict(
                requested=len(actions),
                completed=receipt["completed"],
                remaining=len(actions) - receipt["completed"],
                stop_reason=self.reason,
            )
            if receipt["wait_for"] is not None:
                result["wait_for"] = receipt["wait_for"]
            self.emit("batch_completed", **result["batch"])
            if release_after and not self.closed:
                await self.close("closed", "released after requested batch")
                result.update(status="closed", closed=True, observation_id=None)
            return result
        except asyncio.CancelledError:
            await self.close("expired" if self.expiring else "cancelled", "desktop input cancelled")
            raise
        except Exception as exc:
            await self.close("expired" if isinstance(exc, TimeoutError) else "failed", f"{type(exc).__name__}: {exc}")
            return self.result()
        finally:
            self.active_task = None

    async def close(self, status="closed", reason="desktop session released"):
        """Fence input immediately and retain ownership until owned cleanup finishes."""
        if self.cleanup_task is None:
            self.closed, self.token = True, None
            self.status, self.reason = status, reason
            if self.watchdog is not None and not self.expiring:
                self.watchdog.cancel()

            async def cleanup():
                try:
                    if self.driver is not None:
                        await self.driver.close()
                finally:
                    self.lease.release()
                    self.emit("stopped", status=self.status, reason=self.reason, model_calls=0)
                    if self.evidence:
                        (self.evidence / "result.json").write_text(json.dumps(self.result(), indent=2))

            self.cleanup_task = asyncio.create_task(cleanup())

        async def wait_for_cleanup():
            await asyncio.shield(self.cleanup_task)

        await finish_cleanup(wait_for_cleanup())

    def result(self, *, include_image=False):
        """Return session status with an optional current observation."""
        result = {
            "status": self.status,
            "reason": self.reason,
            "closed": self.closed,
            "verified": False,
            "execution_mode": "direct",
            "model_calls": 0,
            "calls": 0,
            "reserved_tokens": 0,
            "run_id": self.run_id,
            "steps": self.steps,
            "observation_id": self.token,
        }
        if include_image and self.observation:
            result.update(
                observation={key: value for key, value in asdict(self.observation).items() if key != "png_b64"}
            )
            result["multimodal"] = [
                {
                    "type": "image",
                    "source": "computer_use",
                    "source_path": f"desktop:{self.run_id}:{self.observations}",
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64," + self.observation.png_b64,
                }
            ]
        return result
