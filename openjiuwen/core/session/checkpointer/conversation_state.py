# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Project executor checkpoints without their explicitly bound conversations."""
from openjiuwen.core.session.state.base import GLOBAL_STATE_KEY


def executor_checkpoint_state(session, state: dict) -> dict:
    get_bindings = getattr(session, "conversation_bindings", None)
    bindings = get_bindings() if get_bindings is not None else {}
    global_state = state.get(GLOBAL_STATE_KEY)
    contexts = global_state.get("context") if isinstance(global_state, dict) else None
    if not bindings or not isinstance(contexts, dict):
        return state
    contexts = {key: value for key, value in contexts.items() if key not in bindings}
    global_state = {**global_state, "context": contexts}
    return {**state, GLOBAL_STATE_KEY: global_state}
