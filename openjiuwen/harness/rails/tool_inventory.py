# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Inventory of ToolCard.name values affected by :class:`ToolCallResilienceRail`.

This module is a *reference data* module: it documents which tools are subject
to the rail's retry decision logic. It is intentionally NOT imported by
``harness/rails/__init__.py`` (no import side effects, not part of the public
rail surface) — import it directly where the data is needed.
"""

from __future__ import annotations

from typing import Set

#: Non-idempotent / side-effecting tool names — the layering risk that the rail
#: docstring calls out as a "future plan to address" (non-idempotent writes,
#: sub-agents). The CURRENT rail version WILL retry these tools if they raise a
#: transport/timeout-class exception, which may repeat the side effect.
NON_IDEMPOTENT_TOOL_NAMES: Set[str] = {
    # filesystem / code / shell
    "write_file", "edit_file", "bash", "powershell", "code",
    # cron mutating ops
    "cron_create_job", "cron_update_job", "cron_delete_job", "cron_toggle_job",
    # memory writes (harness + agent_teams)
    "write_memory", "edit_memory", "coding_memory_write", "coding_memory_edit",
    # sub-agent spawn
    "task_tool", "sessions_spawn",
    # worktree
    "enter_worktree", "exit_worktree",
    # browser side-effecting ops
    "browser_run_code_unsafe", "browser_type",
    "browser_network_state_set", "browser_route", "browser_unroute",
    "browser_cookie_clear", "browser_cookie_delete", "browser_cookie_set",
    "browser_localstorage_clear", "browser_localstorage_delete", "browser_localstorage_set",
    "browser_sessionstorage_clear", "browser_sessionstorage_delete", "browser_sessionstorage_set",
    "browser_set_storage_state",
    # sys_operation execute_*
    "execute_code", "execute_code_stream",
    "execute_cmd", "execute_cmd_stream", "execute_cmd_background",
    # mobile GUI actions
    "tap_coordinate", "double_tap_coordinate", "long_press_coordinate",
    "drag_coordinate", "type_text",
    "scroll", "press_back", "press_home", "press_enter",
}
