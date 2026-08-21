# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""RepeatToolCallDetector — fix/circuit_breaker_recovery-aligned tool-loop detection.

Algorithms and thresholds match jiuwenswarm_enterprise branch
``fix/circuit_breaker_recovery`` → ``circuit_breaker_rail.py``:

  - generic_repeat:      same tool+args repeat (WARNING≥5)
  - unknown_tool_repeat: failing tool streak (WARNING≥threshold/2, CRITICAL≥threshold)
  - global_breaker:      no-progress streak (CRITICAL≥10)
  - ping_pong:           A↔B alternation (WARNING≥5, CRITICAL≥10 + no_progress)

Default thresholds: warning=5, critical=10, global_breaker=10, unknown_tool=10.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.agent_ras.config import RepeatToolConfig
from openjiuwen.harness.agent_ras.models import (
    Anomaly,
    AnomalyKind,
    Severity,
    Signal,
    SignalKind,
)

_TOOL_ARGUMENTS_MAX_LEN = 400
# Session stash so streak survives AgentRASRail pop/rebuild across HITL ask/resume.
_SESSION_HISTORIES_KEY = "__agent_ras_repeat_tool_histories__"
# Must match openjiuwen.core.single_agent.interrupt.state.INTERRUPTION_KEY
_INTERRUPTION_KEY = "__react_agent_interruption__"


@dataclass
class ToolCallRecord:
    """One completed (or failed) tool call kept in the per-session history."""

    tool_name: str
    args_hash: str
    result_hash: str | None
    timestamp: float
    has_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "args_hash": self.args_hash,
            "result_hash": self.result_hash,
            "timestamp": self.timestamp,
            "has_error": self.has_error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ToolCallRecord | None:
        tool_name = payload.get("tool_name")
        args_hash = payload.get("args_hash")
        if not isinstance(tool_name, str) or not isinstance(args_hash, str):
            return None
        result_hash = payload.get("result_hash")
        if result_hash is not None and not isinstance(result_hash, str):
            result_hash = str(result_hash)
        try:
            timestamp = float(payload.get("timestamp") or 0.0)
        except (TypeError, ValueError):
            timestamp = 0.0
        return cls(
            tool_name=tool_name,
            args_hash=args_hash,
            result_hash=result_hash,
            timestamp=timestamp,
            has_error=bool(payload.get("has_error")),
        )


@dataclass
class _PingPongResult:
    count: int = 0
    paired_tool: str | None = None
    no_progress: bool = False


class ToolResultErrorDetector:
    """Infer tool-call failure from structured result fields (fix branch)."""

    _ERROR_STATUSES = frozenset({"error", "failed", "failure"})
    _EXIT_KEYS = ("exit_code", "exitCode", "returncode", "return_code")
    _REPR_SUCCESS_FALSE = re.compile(r"^success\s*=\s*False\b", re.IGNORECASE)
    _REPR_SUCCESS_TRUE = re.compile(r"^success\s*=\s*True\b", re.IGNORECASE)

    @staticmethod
    def has_error(value: Any) -> bool:
        payload = ToolResultErrorDetector._normalize(value)
        if payload is None:
            return False
        return ToolResultErrorDetector._dict_has_error(payload)

    @staticmethod
    def has_explicit_success(value: Any) -> bool:
        payload = ToolResultErrorDetector._normalize(value)
        if payload is None or "success" not in payload:
            return False
        return ToolResultErrorDetector._boolish_true(payload.get("success"))

    @staticmethod
    def signal_has_error(signal: Signal) -> bool:
        if signal.error is not None:
            return True
        if signal.tool_msg_content is not None:
            return ToolResultErrorDetector.has_error(signal.tool_msg_content)
        return False

    @staticmethod
    def infer_record_has_error(tool_result: Any, signal: Signal) -> bool:
        if ToolResultErrorDetector.has_error(tool_result):
            return True
        if ToolResultErrorDetector.has_explicit_success(tool_result):
            return False
        return ToolResultErrorDetector.signal_has_error(signal)

    @staticmethod
    def _normalize(value: Any) -> dict | None:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump()
                if isinstance(dumped, dict):
                    return dumped
            except Exception:
                logger.debug(
                    "ToolResultErrorDetector model_dump failed",
                    exc_info=True,
                )
        if hasattr(value, "success"):
            return {
                "success": getattr(value, "success", None),
                "data": getattr(value, "data", None),
                "error": getattr(value, "error", None),
            }
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.startswith("{"):
                try:
                    parsed = json.loads(text)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    return parsed
            if ToolResultErrorDetector._REPR_SUCCESS_FALSE.match(text):
                return {"success": False}
            if ToolResultErrorDetector._REPR_SUCCESS_TRUE.match(text):
                return {"success": True}
        return None

    @staticmethod
    def _dict_has_error(payload: dict, *, check_data: bool = True) -> bool:
        if "success" in payload:
            if ToolResultErrorDetector._boolish_false(payload.get("success")):
                return True
            if ToolResultErrorDetector._boolish_true(payload.get("success")):
                return False
        if (
            ToolResultErrorDetector._boolish_true(payload.get("is_error"))
            or ToolResultErrorDetector._boolish_true(payload.get("isError"))
        ):
            return True
        status = payload.get("status")
        if isinstance(status, str) and status.strip().lower() in ToolResultErrorDetector._ERROR_STATUSES:
            return True
        result_type = payload.get("result_type")
        if isinstance(result_type, str) and result_type.strip().lower() == "error":
            return True
        err = payload.get("error")
        if err is not None:
            if isinstance(err, str):
                if err.strip() and err.strip().lower() != "none":
                    return True
            elif err:
                return True
        for key in ToolResultErrorDetector._EXIT_KEYS:
            code = payload.get(key)
            if isinstance(code, int) and code != 0:
                return True
        if check_data:
            data = payload.get("data")
            if isinstance(data, dict) and ToolResultErrorDetector._dict_has_error(data, check_data=False):
                return True
        return False

    @staticmethod
    def _boolish_false(value: Any) -> bool:
        if value is False:
            return True
        return isinstance(value, str) and value.strip().lower() in {"false", "0", "no"}

    @staticmethod
    def _boolish_true(value: Any) -> bool:
        if value is True:
            return True
        return isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}


def format_tool_arguments(params: Any, max_len: int = _TOOL_ARGUMENTS_MAX_LEN) -> str:
    text = json.dumps(params or {}, sort_keys=True, default=str)
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


class RepeatToolCallDetector:
    """Detect repeated / looping tool calls (fix/circuit_breaker_recovery).

    History is scoped to a **user turn**: HITL ask/resume keeps streak via a
    session stash (monitor instances are rebuilt each physical invoke). A new
    user ``chat.send`` (no pending tool interrupt) clears the stash.
    """

    def __init__(self, config: RepeatToolConfig | None = None) -> None:
        self._config = config or RepeatToolConfig()
        self._histories: dict[str, deque[ToolCallRecord]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_emissions: dict[str, tuple[str, str, str]] = {}
        self._bound_session: Any | None = None
        self._ability_manager: Any | None = None
        self._schema_key_cache: dict[str, tuple[str, ...] | None] = {}

    @property
    def name(self) -> str:
        return "repeat_tool_call"

    def set_ability_manager(self, ability_manager: Any | None) -> None:
        """Bind AbilityManager so schema keys resolve by LLM short name."""
        if ability_manager is self._ability_manager:
            return
        self._ability_manager = ability_manager
        self._schema_key_cache.clear()

    def reset(
        self,
        session: Any | None = None,
        *,
        resume_continuation: bool = False,
    ) -> None:
        """Clear or restore per-turn history.

        When ``session`` carries a pending ``ToolInterruptionState`` (ask end or
        resume start) or ``resume_continuation`` is set, keep/hydrate the
        session stash. Otherwise treat as a new user turn and clear stash.
        """
        if session is not None:
            self._bound_session = session
        preserve = not self._is_user_turn_boundary(
            session,
            resume_continuation=resume_continuation,
        )
        logger.debug(
            "RepeatToolCallDetector reset preserve=%s resume_continuation=%s "
            "pending_interrupt=%s",
            preserve,
            resume_continuation,
            self._pending_tool_interruption(session),
        )
        self._histories.clear()
        self._locks.clear()
        self._last_emissions.clear()
        self._schema_key_cache.clear()
        if session is None:
            return
        if preserve:
            self._hydrate_histories(session)
            return
        RepeatToolCallDetector._clear_session_stash(session)

    @staticmethod
    def _pending_tool_interruption(session: Any | None) -> bool:
        if session is None:
            return False
        getter = getattr(session, "get_state", None)
        if not callable(getter):
            return False
        try:
            state = getter(_INTERRUPTION_KEY)
        except Exception:
            logger.debug(
                "RepeatToolCallDetector interruption lookup failed",
                exc_info=True,
            )
            return False
        return state is not None

    @classmethod
    def _is_user_turn_boundary(
        cls,
        session: Any | None,
        *,
        resume_continuation: bool = False,
    ) -> bool:
        """True when streak must restart (real user turn, not HITL/harness resume)."""
        if resume_continuation:
            return False
        if cls._pending_tool_interruption(session):
            return False
        return True

    def _serialize_histories(self) -> dict[str, list[dict[str, Any]]]:
        payload: dict[str, list[dict[str, Any]]] = {}
        for sid, history in self._histories.items():
            payload[sid] = [record.to_dict() for record in history]
        return payload

    def _hydrate_histories(self, session: Any) -> None:
        getter = getattr(session, "get_state", None)
        if not callable(getter):
            return
        try:
            raw = getter(_SESSION_HISTORIES_KEY)
        except Exception:
            logger.debug(
                "RepeatToolCallDetector hydrate read failed",
                exc_info=True,
            )
            return
        if not isinstance(raw, dict):
            return
        maxlen = self._config.history_size
        for sid, records in raw.items():
            if not isinstance(sid, str) or not isinstance(records, list):
                continue
            restored: deque[ToolCallRecord] = deque(maxlen=maxlen)
            for item in records:
                if not isinstance(item, dict):
                    continue
                record = ToolCallRecord.from_dict(item)
                if record is not None:
                    restored.append(record)
            if restored:
                self._histories[sid] = restored

    def _checkpoint(self, session: Any | None = None) -> None:
        target = session if session is not None else self._bound_session
        if target is None:
            return
        updater = getattr(target, "update_state", None)
        if not callable(updater):
            return
        try:
            updater({_SESSION_HISTORIES_KEY: self._serialize_histories()})
        except Exception:
            logger.debug(
                "RepeatToolCallDetector checkpoint failed",
                exc_info=True,
            )

    @staticmethod
    def _clear_session_stash(session: Any) -> None:
        updater = getattr(session, "update_state", None)
        if not callable(updater):
            return
        try:
            updater({_SESSION_HISTORIES_KEY: None})
        except Exception:
            logger.debug(
                "RepeatToolCallDetector clear stash failed",
                exc_info=True,
            )

    def _history(self, sid: str) -> deque[ToolCallRecord]:
        h = self._histories.get(sid)
        if h is None:
            h = deque(maxlen=self._config.history_size)
            self._histories[sid] = h
        return h

    def _lock(self, sid: str) -> asyncio.Lock:
        lock = self._locks.get(sid)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[sid] = lock
        return lock

    @staticmethod
    def _resolve_sid(signal: Signal) -> str:
        if signal.member_name:
            return signal.member_name
        return "default"

    async def observe(self, signal: Signal) -> Anomaly | None:
        if signal.kind not in (SignalKind.AFTER_TOOL_CALL, SignalKind.TOOL_EXCEPTION):
            return None
        if not signal.tool_name:
            return None
        if signal.interrupt_kind is not None:
            logger.debug(
                "RepeatToolCallDetector drop pseudo signal tool=%s "
                "interrupt_kind=%s",
                signal.tool_name,
                signal.interrupt_kind.value,
            )
            return None

        sid = self._resolve_sid(signal)
        tool_args = signal.tool_args or {}
        args_hash = self._hash_args(signal.tool_name, tool_args)
        result_hash = self._hash_outcome(signal.tool_result)
        has_error = ToolResultErrorDetector.infer_record_has_error(
            signal.tool_result,
            signal,
        )

        async with self._lock(sid):
            history = self._history(sid)
            history.append(
                ToolCallRecord(
                    tool_name=signal.tool_name,
                    args_hash=args_hash,
                    result_hash=result_hash,
                    timestamp=time.time(),
                    has_error=has_error,
                )
            )
            self._checkpoint()
            history_list = list(history)
            recent_same = self._count_recent_same(
                history_list,
                signal.tool_name,
                args_hash,
            )
            no_progress = self._get_no_progress_streak(
                history_list,
                signal.tool_name,
                args_hash,
            )
            anomaly = self._detect(
                history_list,
                signal.member_name,
                signal.tool_name,
                args_hash,
                tool_args,
            )
            if anomaly is None:
                self._last_emissions.pop(sid, None)
                return None
            signature = (
                anomaly.kind.value,
                anomaly.severity.value,
                str(anomaly.evidence.get("detector_kind") or ""),
            )
            if self._last_emissions.get(sid) == signature:
                return None
            self._last_emissions[sid] = signature
            return anomaly

    def _filtered_args(self, tool_name: str, params: dict) -> dict[str, Any]:
        """Args actually used for equivalence hashing (schema whitelist)."""
        keys = self._schema_keys(tool_name)
        src = params or {}
        if keys is None:
            return dict(src)
        return {k: src[k] for k in keys if k in src}

    def _resolve_tool_card(self, tool_name: str) -> tuple[Any | None, str]:
        """Resolve tool card: AbilityManager short name, else exact resource id.

        ``AbilityManager.get`` is keyed by LLM short name (e.g. ``read_file``).
        ``resource_mgr.get_tool`` matches ``resource_id`` exactly (often
        ``read_file_{owner_id}``), so short-name lookups fail without AM.
        """
        if not tool_name:
            return None, "none"

        am = self._ability_manager
        if am is not None:
            getter = getattr(am, "get", None)
            if callable(getter):
                try:
                    ability = getter(tool_name)
                except Exception:
                    logger.debug(
                        "RepeatToolCallDetector ability_manager.get failed "
                        "tool=%s",
                        tool_name,
                        exc_info=True,
                    )
                    ability = None
                if ability is not None:
                    if hasattr(ability, "input_params"):
                        return ability, "ability_manager"
                    card = getattr(ability, "card", None)
                    if card is not None:
                        return card, "ability_manager"

        try:
            from openjiuwen.core.runner import Runner

            tool = Runner.resource_mgr.get_tool(tool_id=tool_name)
            if tool is not None:
                return getattr(tool, "card", None), "resource_mgr"
            return None, "none"
        except Exception:
            logger.debug(
                "RepeatToolCallDetector tool card resolve failed tool=%s",
                tool_name,
                exc_info=True,
            )
            return None, "none"

    def _schema_keys(self, tool_name: str) -> tuple[str, ...] | None:
        """Return registered tool parameter property keys, or None if unknown.

        Used to whitelist args for equivalence hashing so display-only fields
        injected for the LLM (e.g. ``call_goal``) do not break repeat streaks.
        """
        if not tool_name:
            return None
        if tool_name in self._schema_key_cache:
            return self._schema_key_cache[tool_name]
        try:
            card, _source = self._resolve_tool_card(tool_name)
            params = getattr(card, "input_params", None) if card is not None else None
            keys = self._keys_from_parameters(params)
        except Exception:
            logger.debug(
                "RepeatToolCallDetector schema lookup failed tool=%s",
                tool_name,
                exc_info=True,
            )
            keys = None
        self._schema_key_cache[tool_name] = keys
        return keys

    @staticmethod
    def _keys_from_parameters(parameters: Any) -> tuple[str, ...] | None:
        """Extract JSON-schema ``properties`` keys from tool ``input_params``."""
        if parameters is None:
            return None
        if isinstance(parameters, dict):
            props = parameters.get("properties")
            if isinstance(props, dict):
                return tuple(props)
            return None
        model_json_schema = getattr(parameters, "model_json_schema", None)
        if callable(model_json_schema):
            try:
                schema = model_json_schema()
            except Exception:
                return None
            if isinstance(schema, dict):
                props = schema.get("properties")
                if isinstance(props, dict):
                    return tuple(props)
        return None

    def _hash_args(self, tool_name: str, params: dict) -> str:
        """Hash tool args; when schema is known, only registered property keys."""
        src = self._filtered_args(tool_name, params)
        canonical = json.dumps(src, sort_keys=True, default=str)
        return hashlib.sha256(f"{tool_name}:{canonical}".encode()).hexdigest()

    @staticmethod
    def _normalize_result(result: Any) -> dict:
        if isinstance(result, dict):
            return {
                "content": str(result.get("content", "")).strip(),
                "output": str(result.get("output", "")).strip(),
                "error": str(result.get("error", "")).strip(),
                "status": str(result.get("status", "")),
            }
        return {"raw": str(result)}

    @staticmethod
    def _hash_outcome(result: Any) -> str | None:
        if result is None:
            return None
        normalized = RepeatToolCallDetector._normalize_result(result)
        return hashlib.sha256(
            json.dumps(normalized, sort_keys=True, default=str).encode()
        ).hexdigest()

    def _detect(
        self,
        history: list[ToolCallRecord],
        member_name: str,
        tool_name: str,
        args_hash: str,
        tool_args: dict[str, Any],
    ) -> Anomaly | None:
        cfg = self._config
        tool_arguments = format_tool_arguments(tool_args)

        no_progress = self._get_no_progress_streak(history, tool_name, args_hash)
        if no_progress >= cfg.global_breaker_threshold:
            return self._build(
                AnomalyKind.TOOL_CALL_LOOP,
                Severity.CRITICAL,
                member_name,
                tool_name,
                {
                    "detector_kind": "global_circuit_breaker",
                    "msg_key": "global_circuit_breaker",
                    "count": no_progress,
                    "tool_arguments": tool_arguments,
                },
            )

        unknown_streak = self._get_unknown_tool_streak(history, tool_name)
        unknown_critical = self._unknown_tool_critical_threshold(cfg)
        unknown_warning = self._unknown_tool_warning_threshold(cfg)
        if unknown_streak >= unknown_critical:
            return self._build(
                AnomalyKind.REPEAT_TOOL_CALL,
                Severity.CRITICAL,
                member_name,
                tool_name,
                {
                    "detector_kind": "unknown_tool_repeat",
                    "msg_key": "unknown_tool_repeat",
                    "count": unknown_streak,
                    "tool_arguments": tool_arguments,
                },
            )
        if unknown_streak >= unknown_warning:
            return self._build(
                AnomalyKind.REPEAT_TOOL_CALL,
                Severity.LOW,
                member_name,
                tool_name,
                {
                    "detector_kind": "unknown_tool_repeat",
                    "msg_key": "unknown_tool_repeat_warning",
                    "count": unknown_streak,
                    "tool_arguments": tool_arguments,
                },
            )

        ping_pong = self._get_ping_pong_streak(history, args_hash)
        if ping_pong.count >= cfg.critical_threshold and ping_pong.no_progress:
            return self._build(
                AnomalyKind.TOOL_CALL_LOOP,
                Severity.CRITICAL,
                member_name,
                tool_name,
                {
                    "detector_kind": "ping_pong",
                    "msg_key": "ping_pong_critical",
                    "count": ping_pong.count,
                    "paired_tool": ping_pong.paired_tool,
                    "tool_arguments": tool_arguments,
                },
            )
        if ping_pong.count >= cfg.warning_threshold:
            return self._build(
                AnomalyKind.TOOL_CALL_LOOP,
                Severity.LOW,
                member_name,
                tool_name,
                {
                    "detector_kind": "ping_pong",
                    "msg_key": "ping_pong_warning",
                    "count": ping_pong.count,
                    "paired_tool": ping_pong.paired_tool,
                    "tool_arguments": tool_arguments,
                },
            )

        recent = self._count_recent_same(history, tool_name, args_hash)
        if recent >= cfg.warning_threshold:
            return self._build(
                AnomalyKind.REPEAT_TOOL_CALL,
                Severity.LOW,
                member_name,
                tool_name,
                {
                    "detector_kind": "generic_repeat",
                    "msg_key": "generic_repeat",
                    "count": recent,
                    "tool_arguments": tool_arguments,
                },
            )

        return None

    @staticmethod
    def _build(
        kind: AnomalyKind,
        severity: Severity,
        member_name: str,
        tool_name: str,
        evidence: dict[str, Any],
    ) -> Anomaly:
        ev = dict(evidence or {})
        ev.setdefault("tool_name", tool_name)
        return Anomaly(
            detector="repeat_tool_call",
            kind=kind,
            severity=severity,
            member_name=member_name,
            summary=f"{kind.value} on {tool_name}",
            evidence=ev,
        )

    @staticmethod
    def _unknown_tool_warning_threshold(cfg: RepeatToolConfig) -> int:
        return max(1, cfg.unknown_tool_threshold // 2)

    @staticmethod
    def _unknown_tool_critical_threshold(cfg: RepeatToolConfig) -> int:
        if cfg.unknown_tool_threshold == cfg.warning_threshold:
            return cfg.unknown_tool_threshold + 1
        return cfg.unknown_tool_threshold

    @staticmethod
    def _get_no_progress_streak(
        history: list[ToolCallRecord],
        tool_name: str,
        args_hash: str,
    ) -> int:
        """Count no-progress repeats within the history window (non-contiguous).

        Records from other tools / other args and records without a result
        hash are skipped, matching the legacy ``circuit_breaker_rail`` on
        develop / fix/circuit_breaker_recovery. Only a different result hash
        on the same call (i.e. real progress) stops the streak.
        """
        streak = 0
        latest_hash: str | None = None
        for record in reversed(history):
            if record.tool_name != tool_name or record.args_hash != args_hash:
                continue
            if record.result_hash is None:
                continue
            if latest_hash is None:
                latest_hash = record.result_hash
                streak = 1
            elif record.result_hash == latest_hash:
                streak += 1
            else:
                break
        return streak

    @staticmethod
    def _side_outputs_stable(records: list[ToolCallRecord]) -> bool:
        hashes = [r.result_hash for r in records if r.result_hash is not None]
        if len(hashes) != len(records) or not hashes:
            return False
        return all(h == hashes[0] for h in hashes)

    @staticmethod
    def _collect_alternating_streak(
        history: list[ToolCallRecord],
    ) -> tuple[list[ToolCallRecord], str | None]:
        if len(history) < 2:
            return [], None
        last_hash = history[-1].args_hash
        other_hash: str | None = None
        other_name: str | None = None
        for record in reversed(history[:-1]):
            if record.args_hash != last_hash:
                other_hash = record.args_hash
                other_name = record.tool_name
                break
        if other_hash is None:
            return [], None

        streak_rev = [history[-1]]
        expect = other_hash
        for record in reversed(history[:-1]):
            if record.args_hash != expect:
                break
            streak_rev.append(record)
            expect = last_hash if expect == other_hash else other_hash
        streak_rev.reverse()
        return streak_rev, other_name

    def _get_ping_pong_streak(
        self,
        history: list[ToolCallRecord],
        _current_args_hash: str,
    ) -> _PingPongResult:
        streak, paired_tool = self._collect_alternating_streak(history)
        if len(streak) < 2:
            return _PingPongResult()

        count = len(streak) // 2
        hash_last = streak[-1].args_hash
        hash_other = next(r.args_hash for r in streak if r.args_hash != hash_last)

        side_last = [r for r in streak if r.args_hash == hash_last]
        side_other = [r for r in streak if r.args_hash == hash_other]

        no_progress = (
            len(side_last) >= 2
            and len(side_other) >= 2
            and self._side_outputs_stable(side_last)
            and self._side_outputs_stable(side_other)
        )

        return _PingPongResult(
            count=count,
            paired_tool=paired_tool,
            no_progress=no_progress,
        )

    @staticmethod
    def _count_recent_same(
        history: list[ToolCallRecord],
        tool_name: str,
        args_hash: str,
    ) -> int:
        """Count (tool_name, args_hash) occurrences across the history window.

        Matches the legacy circuit_breaker_rail on develop / fix/circuit_breaker_recovery:
        matches do NOT have to be contiguous — intervening records (other tools, other
        args, or permission-interrupt noise) do not break the count.
        """
        return sum(
            1 for r in history
            if r.tool_name == tool_name and r.args_hash == args_hash
        )

    @staticmethod
    def _get_unknown_tool_streak(
        history: list[ToolCallRecord],
        tool_name: str,
    ) -> int:
        streak = 0
        for record in reversed(history):
            if record.tool_name != tool_name:
                break
            if not record.has_error:
                break
            streak += 1
        return streak
