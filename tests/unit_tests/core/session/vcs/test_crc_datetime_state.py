# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""A datetime in session state must be appendable and CRC-valid."""

from datetime import datetime, timezone

from openjiuwen.core.session.vcs.backend import decode_log_entry, encode_log_entry
from openjiuwen.core.session.vcs.models import LogEntry, StateDelta


def test_datetime_state_entry_round_trips_with_valid_crc() -> None:
    entry = LogEntry(
        event_id=1,
        state=StateDelta(set={"last_seen": datetime(2026, 1, 1, tzinfo=timezone.utc)}),
    )

    decoded = decode_log_entry(encode_log_entry(entry))

    assert decoded is not None
    assert decoded.event_id == 1
    assert decoded.state.set["last_seen"] == "2026-01-01T00:00:00Z"
