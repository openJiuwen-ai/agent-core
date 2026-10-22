"""Task-independent direct tool schema and strict call decoding; no execution."""

from .direct_wait import ReadinessWait
from .schema import ACTION_FIELDS, Action
from .shortcuts import SHORTCUT_GUIDANCE

OPERATIONS = ["observe", *ACTION_FIELDS, "batch", "done", "close"]
CALL_GUIDANCE = (
    "Use one flat computer_use object. operation is observe, launch, focus, move, click, drag, hold_mouse, "
    "type, shortcut, hold_key, scroll, wait, "
    "batch, done or close. For batch, supply actions as flat native action objects; "
    "focus must be first; later clicks require a stable visible accessibility control. "
    "For native input, include the exact latest observation_id. Put the operation's fields beside operation: "
    "type needs text; click needs element_id OR x and y, with optional button/click_count; "
    "move needs x/y; drag needs path pixel pairs and optional button/duration; hold_mouse needs x/y; "
    "launch needs an approved target; focus needs target OR window_id OR element_id; shortcut/hold_key need keys; "
    "holds use duration=0.05..2 seconds and always release before returning; "
    "mouse actions accept optional modifiers; "
    "scroll uses direction/amount and optional x/y; wait uses seconds. Do not add kind or nest action in this format. "
    "observe, done and close accept only operation. Correct the rejected fields before retrying; "
    "if you cannot form a valid call, close and report the limitation."
)


def input_schema(targets=(), max_batch_actions=8):
    """Expose the simpler flat protocol while reusing the executor's field bounds."""
    fields = Action.model_json_schema()["properties"]
    properties = {
        "operation": {
            "type": "string",
            "enum": [op for op in OPERATIONS if op != "batch" or max_batch_actions > 1],
            "description": "Required. Select the action itself, e.g. type or click. No kind/action wrapper.",
        },
        "observation_id": {
            "type": "string",
            "description": "Required for every native action: exact token from the latest observation. "
            "Omit for observe, done and close.",
        },
        **{name: value for name, value in fields.items() if name != "kind"},
        "release_after": {
            "type": "boolean",
            "default": False,
            "description": "For native actions only: release control after this action and screenshot.",
        },
    }
    properties["element_id"]["description"] = (
        "For click or focus: copy a current control ID; omit x/y. Click requires actionable=true. "
        "Focus requires editable=true and focusable=true in the already foreground shared window; "
        "omit target/window_id."
    )
    properties["button"]["description"] = "For click/drag/hold_mouse: left (default), right or middle."
    properties["click_count"]["description"] = "For click: 1 for single, 2 for double, 3 for triple-click."
    properties["modifiers"]["description"] = (
        "For move/click/drag/scroll/hold_mouse: distinct cmd, ctrl, alt, shift keys held only during this action. "
        'Use e.g. ["shift"] for selection; all are released before returning.'
    )
    properties["path"]["description"] = (
        "For drag: 2-64 [x,y] pairs in the latest screenshot, from start through end. "
        "All points must stay on permitted surfaces and pass native input-recipient checks; "
        "do not supply x/y or element_id."
    )
    properties["duration"]["description"] = "For drag/hold_mouse/hold_key: duration in seconds, 0.05-2."
    for axis in ("x", "y"):
        properties[axis]["description"] = (
            "Screenshot pixels for move/click/hold_mouse, or scroll at this pane. "
            "For scroll omit both x/y to use the window center."
        )
    properties["text"]["description"] = (
        "Required for operation=type: exact text for the focused field, including line breaks. "
        "Do not include element_id; use a separate focus or click first if focus needs to change."
    )
    properties["input_mode"]["description"] = (
        "For type only. editable (default) requires text-field focus. keyboard sends printable Unicode "
        "to a shared keyboard-driven window with AX and no observed editable controls; "
        "focus must remain unchanged. No tabs/newlines. Never bypass an editor focus failure."
    )
    properties["target"]["description"] = (
        "For launch or focus: exact configured target name, not an app name; omit when using window_id/element_id. "
        "Launch opens an allow_launch target and waits for a window; focus selects a running app. "
        "Include the latest observation_id."
    )
    if targets:
        properties["target"]["enum"] = list(targets)
    properties["window_id"]["description"] = (
        "For focus in window_navigation mode: copy a window_id from the latest observation.context.windows. "
        "Use instead of target/element_id to select a particular window. Never supply a native numeric window ID."
    )
    properties["keys"]["description"] = "Required for shortcut/hold_key. " + SHORTCUT_GUIDANCE
    properties["reason"]["description"] = "Optional explanation for a native action; never task instructions."
    if max_batch_actions > 1:
        properties["wait_for"] = {
            **ReadinessWait.model_json_schema(),
            "description": "For batch only: optional observable signal after the final action. "
            "screen_change detects changed pixels, not page readiness. control_enabled "
            "requires an exact AX label. Local polling uses remaining native budgets.",
        }
        action_properties = {
            k: v for k, v in properties.items() if k not in {"observation_id", "release_after", "wait_for"}
        }
        action_properties["operation"] = {"type": "string", "enum": [k for k in ACTION_FIELDS if k != "launch"]}
        properties["actions"] = {
            "type": "array",
            "minItems": 1,
            "maxItems": max_batch_actions,
            "items": {
                "type": "object",
                "properties": action_properties,
                "required": ["operation"],
                "additionalProperties": False,
            },
            "description": "For batch only: predictable serial actions, e.g. click then type, or shortcut then type "
            "then wait. Focus must be first; later clicks need a stable visible AX control. Each action is checked and "
            "validated locally; a fresh final image returns. Stop and inspect partial progress before continuing. "
            "Do not batch decisions that require reading a changed screen. No launch/done/close inside a batch.",
        }
    return {"type": "object", "properties": properties, "required": ["operation"], "additionalProperties": False}


def decode_call(inputs, max_batch_actions=8):
    """Return an executor operation/action, never infer intent or fill freshness tokens."""
    if not isinstance(inputs, dict):
        raise ValueError("computer_use requires a JSON object")
    operation = inputs.get("operation")
    if not isinstance(operation, str) or operation not in [*OPERATIONS, "act"]:
        raise ValueError("missing or invalid operation; select " + ", ".join(OPERATIONS))
    if operation in {"observe", "done", "close"}:
        if set(inputs) != {"operation"}:
            raise ValueError(f"operation={operation} accepts only operation; remove all action fields")
        return operation, None
    if not isinstance(inputs.get("release_after", False), bool):
        raise ValueError("release_after must be boolean")
    if operation == "batch":
        if set(inputs) - {"operation", "observation_id", "actions", "release_after", "wait_for"}:
            raise ValueError("batch accepts only operation, observation_id, actions and release_after")
        if "wait_for" in inputs:
            ReadinessWait.model_validate(inputs["wait_for"])
        values = inputs.get("actions")
        if max_batch_actions <= 1 or not isinstance(values, list) or not 1 <= len(values) <= max_batch_actions:
            raise ValueError(f"batch requires 1..{max_batch_actions} actions and batching enabled")
        actions = []
        for index, value in enumerate(values):
            if not isinstance(value, dict) or value.get("operation") not in ACTION_FIELDS:
                raise ValueError("batch entries must be flat native actions")
            if set(value) & {"observation_id", "release_after"} or value["operation"] == "launch":
                raise ValueError("batch entries cannot launch, release or supply observation tokens")
            _, action = decode_call({**value, "observation_id": inputs.get("observation_id")})
            if index and action.kind in {"focus", "move", "drag", "hold_mouse", "scroll"}:
                raise ValueError("focus and pointer gestures other than click must be first in a batch")
            actions.append(action)
        return "batch", actions
    if operation == "act":
        # Existing SDK clients retain the exact documented nested envelope.
        extra = set(inputs) - {"operation", "observation_id", "action", "release_after"}
        if extra:
            raise ValueError(
                "legacy operation=act requires nested action and observation_id; "
                "alternatively use the flat action as operation, without kind/action wrappers"
            )
        action = Action.model_validate(inputs.get("action"))
        if action.kind not in ACTION_FIELDS:
            raise ValueError("terminal decisions are not native actions; use operation=done or close")
    else:
        extra = set(inputs) - ({"operation", "observation_id", "release_after", "reason"} | ACTION_FIELDS[operation])
        if extra:
            raise ValueError(
                f"unsupported fields for operation={operation}: {', '.join(sorted(extra))}; "
                f"action fields are {', '.join(sorted(ACTION_FIELDS[operation]))}"
            )
        if operation == "type" and "text" not in inputs:
            raise ValueError("operation=type requires an explicit text field")
        action = Action.model_validate(
            {
                "kind": operation,
                **{key: value for key, value in inputs.items() if key in ACTION_FIELDS[operation] or key == "reason"},
            }
        )
    if not isinstance(inputs.get("observation_id"), str) or not inputs["observation_id"]:
        raise ValueError("native actions require the exact latest observation_id; observe again if unavailable")
    return "act", action
