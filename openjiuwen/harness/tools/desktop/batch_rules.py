"""Shared task-independent batch bindings and scene checks; no host/runtime imports."""

if __package__:
    from .accessibility import CLICK_ROLES
    from .schema import Action, Observation
else:
    from accessibility import CLICK_ROLES  # pylint: disable=import-error
    from schema import Action, Observation  # pylint: disable=import-error


class BatchRules:
    """Bind actions to observed controls and detect scene changes."""

    def __init__(self, observation):
        self.observation = observation

    @staticmethod
    def scene_signature(observation: Observation | None) -> tuple | None:
        """Changes requiring a new visual decision, independent of task/content."""
        if observation is None:
            return None
        context = observation.context or {}
        windows = tuple(
            sorted(
                (
                    str(w.get("window_id")),
                    tuple((w.get("desktop_rect") or {}).get(k) for k in ("x", "y", "width", "height")),
                )
                for w in context.get("windows", [])
            )
        )

        controls = tuple(
            sorted(
                # Hit-test availability can change without layout changing. Each
                # pending click still matches the complete actionable control key.
                (key[0], key[2], key[3])
                for e in observation.elements
                if e.get("role") in CLICK_ROLES or e.get("editable")
                for key in [BatchRules.control_key(e, observation)]
            )
        )
        return (
            observation.target,
            observation.window_id,
            observation.bounds,
            windows,
            context.get("active_window_id"),
            controls,
        )

    @staticmethod
    def control_key(element: dict, observation: Observation) -> tuple:
        """Ignore changing IDs, typed values and focus; retain control identity and layout."""
        bounds = element.get("bounds") or ()
        if len(bounds) == 4:
            left, top, width, height = observation.bounds
            bounds = (
                left + bounds[0] * width / observation.width,
                top + bounds[1] * height / observation.height,
                bounds[2] * width / observation.width,
                bounds[3] * height / observation.height,
            )
        return (
            element.get("role", ""),
            element.get("label", ""),
            tuple(round(v, 6) for v in bounds),
            bool(element.get("enabled")),
            bool(element.get("actionable")),
        )

    def bind_batch(self, actions: list[Action]) -> dict[int, tuple]:
        """Validate all later click bindings before any input; no inferred controls."""
        bindings = {}
        for index, action in enumerate(actions):
            if index == 0 or action.kind != "click":
                continue
            elements = self.observation.elements if self.observation else []
            candidates = []
            for element in elements:
                bounds = element.get("bounds") or []
                hit = (
                    len(bounds) == 4
                    and action.x is not None
                    and action.y is not None
                    and bounds[0] <= action.x < bounds[0] + bounds[2]
                    and bounds[1] <= action.y < bounds[1] + bounds[3]
                )
                if (
                    element.get("enabled")
                    and element.get("actionable")
                    and (element.get("id") == action.element_id if action.element_id else hit)
                ):
                    candidates.append(element)
            if len(candidates) != 1:
                raise ValueError("later batch clicks require a unique initially visible actionable AX control")
            key = self.control_key(candidates[0], self.observation)
            if sum(self.control_key(e, self.observation) == key for e in elements) != 1:
                raise ValueError("ambiguous later batch click control")
            bindings[index] = key
        return bindings
